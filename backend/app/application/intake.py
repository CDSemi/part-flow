"""Scan Station `Receive Quantity` — the MODIFY intake command (Phase 10.5).

The Scan Station workflow that INTRODUCES physical quantity of a PN
that has no active Work Order Demand — including a PN seen for the
first time (PROJECT_PROFILE §14 *Modify Intake*, GUI_DESIGN §4.7 item
1). `intake` is the internal workflow name; the operator-facing dialog
is `Receive Quantity` and its only write point is `Confirm receipt`.

One confirmed receipt is ONE database transaction, idempotent per
`device_event_id`, following the established command protocol
(`app.application.machine_processing`): the PartNumber master created
on first valid use, the internal `WorkOrder` (`work_order_number =
NULL`) created or reused, the `WorkOrderDemand`, the `QuantityFlow`,
an independent `AssignedRoute` snapshot for a `PLANNED` receipt only,
the immutable `RECEIVED` Movement and the current-position projection
commit together or not at all.

Rules owned here
----------------

**Active Work Order Demand** (the workflow's entry condition) is
derived, never stored: a demand line is ACTIVE while it still has a
remaining business shortage — `requested_quantity >
allocated_quantity`. Released quantity does NOT decide it (a line
released in full but not yet allocated stays active demand), and
`WorkOrder.completed_at` is the aggregate state of the whole Work
Order, never the state of one line: a fully allocated line is inactive
even while its Work Order stays open because of its other lines. The
`Receive Quantity` workflow exists exactly where no active demand of
the PN remains, so it can never substitute for releasing demand that
Management already holds.

**Internal Work Order reuse** (PROJECT_PROFILE §14 "must never
guess"): the candidates of a `MODIFY` receipt are the Work Orders
WITHOUT an external number that are not completed and already carry a
`MODIFY` demand line for the same PN. Exactly one candidate is reused,
several REQUIRE an explicit selection (`WorkOrderSelectionRequiredError`
— nothing is written and no first match is guessed), none creates a
new internal Work Order. A `NEW` receipt never reuses: reuse is defined
for blank-number MODIFY Work Orders only.

Reuse keeps the SLICE1_DATA_MODEL §5 invariant that one canonical PN
appears at most once among a Work Order's current demand lines: the
reused Work Order's existing line for the PN is RAISED by the received
quantity (the restricted edit of PROJECT_PROFILE §13 — raising a
released line's requested quantity is always valid) instead of gaining
a second line for the same PN, and the receipt releases exactly that
increment. Only the fields that restricted edit permits are touched —
the requested quantity and, when the operator entered one, the due
date; `request_type`, `requester`, `reason` and `notes` of an existing
line are never rewritten by a station receipt (the receipt's own
reason travels on the immutable Movement). A receipt that creates the
internal Work Order writes the operator's reason on the new demand
line as well.

**Scan-driven `RECEIVED`**: the Movement records the Scan Station
identity and the resolved Operation. Station, Area and Operation are
re-validated AUTHORITATIVELY at write time under their row locks — a
station deactivated or rebound, a deactivated Area, a terminal Area (a
terminal Area is where finished quantity ends, never where production
enters) and an inactive or foreign Operation are each refused with
zero writes.

**No silent join of existing quantity**: the workflow exists only
where the PN has NO active quantity at all. If active quantity of the
PN appeared between the resolution and the confirmation, the receipt
is refused — PROJECT_PROFILE §14 requires an explicit confirmation of
whether new quantity joins an existing Quantity Flow or creates a
separate one, and this wizard collects no such confirmation. Nothing
is inferred from PN identity alone.

**`Add more quantity` stays separate**: found physical quantity beside
quantity already active in the station's Area is the Phase 9
`QUANTITY_ADJUSTED · INCREASE` correction (`app.application
.quantity_events`), never this command.

**`received_date` is the SCAN, not the confirmation** (§14: "the
received date defaults to the scan timestamp"). The PN resolution that
opens the wizard issues `scanned_at`; the station carries that one
instant through every step and sends it back with the confirmed
receipt, so a scan before midnight confirmed after midnight still
records the day it was scanned. The server validates the instant
(time-zone aware, never in the future, never older than
:data:`MAX_SCAN_AGE`) and derives the calendar date from it in
`SITE_TIMEZONE` — the one site-calendar rule of Phase 10 — and the
instant is part of the idempotency fingerprint, so a retry of the same
intent replays instead of silently receiving under a new date.

Serialization: a receipt takes the ONE shared PN-level lock
(`part_numbers.acquire_part_number_lock`) that every other command
able to give a PN active quantity or active demand takes as well — the
production release, a Work Order save that adds or raises a demand
line, an allocation reversal, and an Undo that reopens a flow its
command had closed. The no-active-quantity and no-active-demand
preconditions are therefore judged on state no concurrent transaction
can still change before this one commits. Lock order is PN advisory →
Scan Station → demand → WorkOrder → Area → Operation — the advisory
lock always first, then the established row order of the release
(demand → Area → Operation) and the demand save (demand → WorkOrder),
with no cycle.

Deliberately absent: Worker identity and the badge gates (Phase 13),
authorization (Phase 14), and Undo of a receipt — a receipt also
creates or raises business demand, which the Movement-level reversal
of PROJECT_PROFILE §16 does not rewrite, so `app.application.undo`
refuses it explicitly (the same boundary Phase 10 drew for `STOCKED`)
rather than half-reversing a command.
"""

import datetime
import hashlib
import json
from typing import Any, Final, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application import audit, work_orders
from app.application.common import device_event_id_text, flush, optional_text
from app.application.errors import (
    ConflictError,
    IdempotencyConflictError,
    InvalidInputError,
    NotFoundError,
)
from app.application.machine_processing import (
    FINGERPRINT_KEY,
    command_metadata,
    committed_command,
)
from app.application.machines import area_has_machines
from app.application.part_numbers import (
    acquire_part_number_lock,
    canonical_part_number,
    ensure_part_number,
)
from app.application.production_release import CONTEXT_KEY, DEMAND_ID_KEY
from app.application.projections import processing_state_of
from app.application.transfers import require_production_station, resolve_arrival_operation
from app.domain.enums import (
    AuditEntityType,
    AuditEventType,
    MovementType,
    ProcessingState,
    QuantityFlowStatus,
    RequestType,
    RouteMode,
    WorkOrderStatus,
)
from app.infrastructure.models import (
    DEVICE_EVENT_ID_CONSTRAINT,
    Area,
    AssignedRoute,
    AssignedRouteStep,
    PartMovement,
    PartNumber,
    QuantityFlow,
    RouteStep,
    RouteTemplate,
    ScanStation,
    WorkOrder,
    WorkOrderDemand,
)

#: Immutable record of what the receipt decided, read back verbatim on
#: a replay instead of being re-derived from the current configuration.
INTAKE_KEY: Final = "intake"

_COMMAND_KIND: Final = "INTAKE"

#: How long a resolved scan may stay open before its `Receive Quantity`
#: must be prepared again. The received date of a receipt is the DATE
#: of its scan, and the instant comes back from the station, so an
#: unbounded past would let a stale or fabricated context backdate
#: business demand. Twelve hours is wider than any single station
#: interaction — one shift — and still bounds the damage to the current
#: or the previous site day.
MAX_SCAN_AGE: Final = datetime.timedelta(hours=12)


class WorkOrderSelectionRequiredError(ConflictError):
    """Several internal blank-number MODIFY Work Orders are plausible.

    PROJECT_PROFILE §14: Work Order reuse must never guess. The
    response carries the candidates so the station can present the
    explicit selection dialog; nothing is written until one is chosen.
    """

    def __init__(self, message: str, work_orders: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.work_orders = work_orders


# ---------------------------------------------------------------------------
# Read model — the entry condition and the reuse candidates
# ---------------------------------------------------------------------------


def has_active_demand(session: Session, part_number: str) -> bool:
    """True while any demand line of the PN still has a business shortage.

    ACTIVE means `requested_quantity > allocated_quantity` (see the
    module docstring): the demand can still be satisfied, so the PN's
    quantity belongs to a production release from Management, not to a
    station receipt.
    """
    return (
        session.scalar(
            select(WorkOrderDemand.id)
            .where(
                WorkOrderDemand.part_number == part_number,
                WorkOrderDemand.requested_quantity > WorkOrderDemand.allocated_quantity,
            )
            .limit(1)
        )
        is not None
    )


class InternalWorkOrderCandidate(NamedTuple):
    """One reusable internal blank-number MODIFY Work Order.

    Presented to the operator by its business facts — received date,
    the PN's existing demand line and its Job Numbers — never by the
    database key (PROJECT_PROFILE §7: an internal id is never the
    user-facing Work Order identifier; the id travels in the request
    only).
    """

    work_order_id: int
    work_order_demand_id: int
    received_date: datetime.date
    due_date: datetime.date | None
    requested_quantity: int
    job_numbers: list[str]


def internal_modify_work_orders(
    session: Session, part_number: str
) -> list[InternalWorkOrderCandidate]:
    """The blank-number MODIFY Work Orders a receipt of this PN may reuse.

    A candidate is a Work Order without an external number that is not
    completed and already carries a `MODIFY` demand line for the same
    canonical PN — the only linkage that makes one "clearly applicable"
    to this PN. Ordered oldest first for a stable selection list.
    """
    rows = session.execute(
        select(WorkOrder, WorkOrderDemand)
        .join(WorkOrderDemand, WorkOrderDemand.work_order_id == WorkOrder.id)
        .where(
            WorkOrder.work_order_number.is_(None),
            WorkOrder.completed_at.is_(None),
            WorkOrderDemand.part_number == part_number,
            WorkOrderDemand.request_type == RequestType.MODIFY,
        )
        .order_by(WorkOrder.received_date, WorkOrder.id)
    ).all()
    return [
        InternalWorkOrderCandidate(
            work_order_id=work_order.id,
            work_order_demand_id=demand.id,
            received_date=work_order.received_date,
            due_date=demand.due_date,
            requested_quantity=demand.requested_quantity,
            job_numbers=list(demand.job_numbers),
        )
        for work_order, demand in rows
    ]


class IntakeContext(NamedTuple):
    """What the station needs to open (or withhold) `Receive Quantity`."""

    #: The PN still has a business shortage on some demand line.
    has_active_demand: bool
    #: The workflow applies here: the PN has no active demand and no
    #: active quantity, and the station's Area can start production.
    available: bool
    #: The PartNumber master already exists (the Step 1 copy of
    #: GUI_DESIGN §4.7 tells a known PN from a new one).
    part_number_known: bool
    #: The reuse candidates of a MODIFY receipt (§14).
    work_orders: list[InternalWorkOrderCandidate]


def intake_context(
    session: Session, part_number: str, area: Area, *, has_active_quantity: bool
) -> IntakeContext:
    """Judge the `Receive Quantity` entry condition for one resolved scan."""
    active_demand = has_active_demand(session, part_number)
    available = not has_active_quantity and not area.is_terminal and not active_demand
    return IntakeContext(
        has_active_demand=active_demand,
        available=available,
        part_number_known=session.get(PartNumber, part_number) is not None,
        work_orders=internal_modify_work_orders(session, part_number) if available else [],
    )


# ---------------------------------------------------------------------------
# Input normalization — pure shape checks, no database access
# ---------------------------------------------------------------------------


def _validated_quantity(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidInputError("Received quantity must be a positive whole number.")
    return value


def _validated_request_type(value: object) -> RequestType:
    if isinstance(value, RequestType):
        return value
    if isinstance(value, str):
        try:
            return RequestType(value)
        except ValueError:
            pass
    raise InvalidInputError("Request Type must be NEW or MODIFY.")


def _validated_route_mode(value: object) -> RouteMode:
    if isinstance(value, RouteMode):
        return value
    if isinstance(value, str):
        try:
            return RouteMode(value)
        except ValueError:
            pass
    raise InvalidInputError("Route Mode must be FLOATING or PLANNED.")


def _validated_due_date(value: object) -> datetime.date | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime) or not isinstance(value, datetime.date):
        raise InvalidInputError("The due date must be a calendar date.")
    return value


def _validated_scanned_at(value: object) -> datetime.datetime:
    """The instant the PN scan opened this `Receive Quantity` (§14).

    Server-issued by the scan resolution and carried unchanged through
    the wizard, so the received date follows the SCAN and not the
    moment the operator finished confirming. Validated as an actual
    instant — time-zone aware, never in the future, never older than
    :data:`MAX_SCAN_AGE` — because the value travels through the
    client: a naive, future or stale timestamp is refused with zero
    writes instead of backdating business demand.
    """
    if not isinstance(value, datetime.datetime) or value.tzinfo is None:
        raise InvalidInputError(
            "The scan timestamp must be a date and time with a time zone offset."
        )
    now = work_orders.now()
    if value > now:
        raise InvalidInputError("The scan timestamp is in the future.")
    if now - value > MAX_SCAN_AGE:
        raise InvalidInputError(
            "This Receive Quantity was prepared too long ago to record the"
            " quantity under its scan date. Scan the Part Number again."
        )
    return value


def _fingerprint(
    *,
    station_id: str,
    part_number: str,
    quantity: int,
    request_type: RequestType,
    route_mode: RouteMode,
    route_template_id: int | None,
    operation_id: int | None,
    due_date: datetime.date | None,
    reason: str | None,
    work_order_id: int | None,
    scanned_at: datetime.datetime,
) -> str:
    """Deterministic canonical hash of the confirmed receipt intent.

    Covers the whole intent exactly as the station confirmed it — the
    explicitly selected internal Work Order included, and `None` when
    the operator confirmed without a selection because the server
    resolved it unambiguously. The scan instant is part of it because
    it DECIDES the received date: a retry of the lost response carries
    the same instant and replays, while a fresh scan of the same PN and
    quantity is a different intent that needs its own
    `device_event_id`. The instant is normalized to UTC first, so an
    equal moment expressed in another offset hashes the same.
    """
    normalized = {
        "command": _COMMAND_KIND,
        "station_id": station_id,
        "part_number": part_number,
        "quantity": quantity,
        "request_type": str(request_type),
        "route_mode": str(route_mode),
        "route_template_id": route_template_id if route_mode is RouteMode.PLANNED else None,
        "operation_id": operation_id,
        "due_date": due_date.isoformat() if due_date is not None else None,
        "reason": reason,
        "work_order_id": work_order_id,
        "scanned_at": scanned_at.astimezone(datetime.UTC).isoformat(),
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The committed receipt
# ---------------------------------------------------------------------------


class IntakeReceipt(NamedTuple):
    """One committed receipt, read from its immutable Movement."""

    movement_id: int
    quantity_flow_id: int
    part_number: str
    quantity: int
    request_type: RequestType
    route_mode: RouteMode
    assigned_route_id: int | None
    area_id: int
    operation_id: int
    #: QUEUED (Area with Machines) or PROCESSING (Area without) —
    #: judged at command time and recorded immutably for replay.
    processing_state: ProcessingState
    work_order_id: int
    work_order_demand_id: int
    #: True when an existing internal blank-number MODIFY Work Order
    #: took this receipt instead of a new one being created.
    work_order_reused: bool
    reason: str | None
    station_id: str
    device_event_id: str
    occurred_at: datetime.datetime
    created: bool


def _reused_for_other_kind() -> IdempotencyConflictError:
    return IdempotencyConflictError(
        "This device_event_id was already used for a different production"
        " request. Nothing was recorded — a new intent needs a new"
        " device_event_id."
    )


def _receipt_result(
    session: Session, command: list[PartMovement], *, created: bool
) -> IntakeReceipt:
    movement = command[-1]
    recorded = (movement.metadata_ or {}).get(INTAKE_KEY)
    context = (movement.metadata_ or {}).get(CONTEXT_KEY)
    if (
        len(command) != 1
        or movement.movement_type != MovementType.RECEIVED
        or movement.station_id is None
        or not isinstance(recorded, dict)
        or not isinstance(context, dict)
        or DEMAND_ID_KEY not in context
    ):
        raise _reused_for_other_kind()
    demand_id = int(context[DEMAND_ID_KEY])
    demand = session.get(WorkOrderDemand, demand_id)
    if demand is None:  # pragma: no cover - the demand is never deleted after release
        raise _reused_for_other_kind()
    assigned_route_id: int | None = None
    if movement.assigned_route_step_id is not None:
        assigned_route_id = session.scalar(
            select(AssignedRouteStep.assigned_route_id).where(
                AssignedRouteStep.id == movement.assigned_route_step_id
            )
        )
    return IntakeReceipt(
        movement_id=movement.id,
        quantity_flow_id=movement.quantity_flow_id,
        part_number=movement.part_number,
        quantity=movement.quantity,
        request_type=RequestType(recorded["request_type"]),
        route_mode=RouteMode.PLANNED if assigned_route_id is not None else RouteMode.FLOATING,
        assigned_route_id=assigned_route_id,
        area_id=movement.to_area_id,
        operation_id=movement.operation_id,
        processing_state=ProcessingState(recorded["processing_state"]),
        work_order_id=demand.work_order_id,
        work_order_demand_id=demand_id,
        work_order_reused=bool(recorded["work_order_reused"]),
        reason=movement.reason,
        station_id=movement.station_id,
        device_event_id=movement.device_event_id,
        occurred_at=movement.occurred_at,
        created=created,
    )


def _replay_or_conflict(
    session: Session, command: list[PartMovement], fingerprint: str
) -> IntakeReceipt:
    stored = (command[-1].metadata_ or {}).get(FINGERPRINT_KEY)
    if stored != fingerprint:
        raise _reused_for_other_kind()
    return _receipt_result(session, command, created=False)


# ---------------------------------------------------------------------------
# Work Order resolution (PROJECT_PROFILE §14 — never a guess)
# ---------------------------------------------------------------------------


class _ResolvedWorkOrder(NamedTuple):
    work_order: WorkOrder
    #: The existing demand line of the PN this receipt raises, or None
    #: when the receipt creates the line together with its Work Order.
    demand: WorkOrderDemand | None


def _candidate_payload(candidates: list[InternalWorkOrderCandidate]) -> list[dict[str, Any]]:
    return [
        {
            "work_order_id": candidate.work_order_id,
            "work_order_demand_id": candidate.work_order_demand_id,
            "received_date": candidate.received_date.isoformat(),
            "due_date": candidate.due_date.isoformat() if candidate.due_date is not None else None,
            "requested_quantity": candidate.requested_quantity,
            "job_numbers": candidate.job_numbers,
        }
        for candidate in candidates
    ]


def _lock_reused_work_order(
    session: Session, candidate: InternalWorkOrderCandidate, part_number: str
) -> _ResolvedWorkOrder:
    """Lock the reused line and its Work Order, then re-validate under the locks.

    Lock order is demand → WorkOrder, the same order the demand save
    and the demand removal take. Both rows are re-read under their lock
    (`populate_existing`), so a Work Order that received an external
    number, completed, or whose line changed since the candidates were
    listed is refused here with nothing written.
    """
    demand = session.get(
        WorkOrderDemand,
        candidate.work_order_demand_id,
        with_for_update=True,
        populate_existing=True,
    )
    work_order = (
        session.get(
            WorkOrder, candidate.work_order_id, with_for_update=True, populate_existing=True
        )
        if demand is not None
        else None
    )
    stale = ConflictError(
        "The selected internal Work Order changed since the receipt was"
        " prepared and can no longer take this quantity. Scan the Part"
        " Number again. Nothing was recorded."
    )
    if demand is None or work_order is None:
        raise stale
    if (
        demand.work_order_id != work_order.id
        or demand.part_number != part_number
        or demand.request_type != RequestType.MODIFY
        or work_order.work_order_number is not None
        or work_order.completed_at is not None
    ):
        raise stale
    return _ResolvedWorkOrder(work_order=work_order, demand=demand)


def _resolve_work_order(
    session: Session,
    *,
    part_number: str,
    request_type: RequestType,
    work_order_id: int | None,
) -> _ResolvedWorkOrder | None:
    """The internal Work Order this receipt reuses — None to create one.

    A `NEW` receipt never reuses (reuse is defined for blank-number
    MODIFY Work Orders only). A `MODIFY` receipt reuses the single
    clearly applicable candidate, requires an explicit selection when
    several are plausible, and creates a new internal Work Order when
    none exists.
    """
    if request_type is not RequestType.MODIFY:
        if work_order_id is not None:
            raise InvalidInputError(
                "A NEW receipt always creates its own internal Work Order:"
                " reuse is defined for blank-number MODIFY Work Orders only."
            )
        return None
    candidates = internal_modify_work_orders(session, part_number)
    if work_order_id is not None:
        chosen = next(
            (item for item in candidates if item.work_order_id == work_order_id),
            None,
        )
        if chosen is None:
            raise ConflictError(
                "The selected internal Work Order is no longer applicable to"
                f" Part Number '{part_number}'. Scan the Part Number again and"
                " choose from the current list. Nothing was recorded."
            )
        return _lock_reused_work_order(session, chosen, part_number)
    if len(candidates) > 1:
        raise WorkOrderSelectionRequiredError(
            f"Part Number '{part_number}' has {len(candidates)} internal Work"
            " Orders without an external number that could take this receipt."
            " Select the one this quantity belongs to — nothing was recorded.",
            _candidate_payload(candidates),
        )
    if len(candidates) == 1:
        return _lock_reused_work_order(session, candidates[0], part_number)
    return None


# ---------------------------------------------------------------------------
# The receipt command
# ---------------------------------------------------------------------------


def receive_quantity(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    quantity: object,
    request_type: object,
    route_mode: object,
    route_template_id: int | None = None,
    operation_id: int | None = None,
    due_date: object = None,
    reason: object = None,
    work_order_id: int | None = None,
    scanned_at: object,
    device_event_id: object,
) -> IntakeReceipt:
    """Record one confirmed `Receive Quantity` as ONE transaction.

    Order: input shape → fingerprint → idempotency fast path → station
    context → the ONE shared PN-level advisory lock → idempotency
    re-check → the STATION row lock with its
    authoritative active/binding re-check → the no-active-quantity and
    no-active-demand preconditions → the internal Work Order resolution
    under the demand → WorkOrder locks → the locked Area re-read
    (active, non-terminal) → the Operation lock → the writes → COMMIT
    (or the replay of a race winner). Any failure before COMMIT leaves
    zero writes.
    """
    pn = canonical_part_number(part_number)
    received_quantity = _validated_quantity(quantity)
    intake_request_type = _validated_request_type(request_type)
    mode = _validated_route_mode(route_mode)
    if mode is RouteMode.PLANNED:
        if route_template_id is None:
            raise InvalidInputError("A PLANNED receipt requires a Planned Route.")
    elif route_template_id is not None:
        raise InvalidInputError(
            "A FLOATING receipt takes no Planned Route — it has no AssignedRoute;"
            " its route trace is derived from Movement history."
        )
    demand_due_date = _validated_due_date(due_date)
    receipt_reason = optional_text(reason if isinstance(reason, str) or reason is None else None)
    if reason is not None and not isinstance(reason, str):
        raise InvalidInputError("The reason must be text.")
    scan_instant = _validated_scanned_at(scanned_at)
    # §14: the received date defaults to the SCAN — read on the site
    # calendar, the one SITE_TIMEZONE rule, so a wizard that crosses
    # midnight still records the day the PN was scanned.
    received_on = work_orders.site_date_of(scan_instant)
    event_id = device_event_id_text(device_event_id)
    fingerprint = _fingerprint(
        station_id=station_id,
        part_number=pn,
        quantity=received_quantity,
        request_type=intake_request_type,
        route_mode=mode,
        route_template_id=route_template_id,
        operation_id=operation_id,
        due_date=demand_due_date,
        reason=receipt_reason,
        work_order_id=work_order_id,
        scanned_at=scan_instant,
    )

    committed = committed_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint)

    station, area = require_production_station(session, station_id)

    # The ONE shared PN-level lock: every command that can give this PN
    # active quantity or active demand takes it too, so nothing can
    # make the preconditions below false between judging and COMMIT.
    acquire_part_number_lock(session, pn)
    committed = committed_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint)

    # The Scan Station row locked until COMMIT and judged on the locked
    # re-read: a station deactivated or rebound since the wizard opened
    # is refused, so a `RECEIVED` can never carry the identity of a
    # station that no longer belongs to its Area.
    locked_station = session.get(
        ScanStation, station_id, with_for_update=True, populate_existing=True
    )
    if locked_station is None:  # pragma: no cover - it existed on the read above
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    station = locked_station
    if not station.is_active:
        raise ConflictError(
            f"Scan Station '{station_id}' is inactive and accepts no production use."
            " Nothing was recorded."
        )
    if station.area_id != area.id:
        raise ConflictError(
            f"Scan Station '{station_id}' is no longer bound to Area '{area.name}' —"
            " its configuration changed since the receipt was prepared. Reload the"
            " station and confirm the receipt again. Nothing was recorded."
        )

    # PROJECT_PROFILE §14: quantity that would join existing active
    # quantity needs an explicit join-or-separate confirmation this
    # workflow does not collect — it exists only where the PN has none.
    active_flow = session.scalar(
        select(QuantityFlow.id)
        .where(
            QuantityFlow.part_number == pn,
            QuantityFlow.status == QuantityFlowStatus.ACTIVE,
        )
        .limit(1)
    )
    if active_flow is not None:
        raise ConflictError(
            f"Part Number '{pn}' already has active production quantity."
            " Receiving new quantity beside it needs an explicit decision about"
            " the existing quantity — scan the Part Number again. Nothing was"
            " recorded."
        )
    if has_active_demand(session, pn):
        raise ConflictError(
            f"Part Number '{pn}' now has active Work Order Demand. Release that"
            " demand to production from Management → Work Orders instead of"
            " receiving it here. Nothing was recorded."
        )

    reused = _resolve_work_order(
        session,
        part_number=pn,
        request_type=intake_request_type,
        work_order_id=work_order_id,
    )

    # The Area row locked until COMMIT and its flags judged on the
    # locked re-read (the same protocol as a transfer destination):
    # Area deactivation and a receipt have one serial outcome.
    session.refresh(area, with_for_update=True)
    if not area.is_active:
        raise ConflictError(
            f"Area '{area.name}' is inactive and cannot accept received quantity."
            " Nothing was recorded."
        )
    if area.is_terminal:
        raise ConflictError(
            f"Area '{area.name}' is a terminal Area and never starts production."
            " Receive quantity at a production Area instead. Nothing was recorded."
        )
    operation = resolve_arrival_operation(session, area, operation_id)

    template_steps: list[RouteStep] = []
    template: RouteTemplate | None = None
    if mode is RouteMode.PLANNED:
        template = session.get(RouteTemplate, route_template_id)
        if template is None:
            raise InvalidInputError(f"Planned Route {route_template_id} does not exist.")
        if template.archived_at is not None:
            raise ConflictError(
                f"Planned Route '{template.name}' is archived and is never offered"
                " for new route assignments. Nothing was recorded."
            )
        template_steps = list(
            session.scalars(
                select(RouteStep)
                .where(RouteStep.route_template_id == template.id)
                .order_by(RouteStep.sequence)
            )
        )
        if not template_steps:
            raise InvalidInputError(
                f"Planned Route '{template.name}' has no steps to receive against."
            )
        first_step = template_steps[0]
        if first_step.area_id != area.id:
            raise InvalidInputError(
                f"The first step of Planned Route '{template.name}' does not start"
                f" in Area '{area.name}'. Receive against a Route that starts here."
            )
        if first_step.operation_id is not None and first_step.operation_id != operation.id:
            raise InvalidInputError(
                f"The first step of Planned Route '{template.name}' defines a"
                " different Operation than the resolved one."
            )

    # -- Writes — all inside the one open transaction --------------------
    ensure_part_number(session, pn)
    if reused is not None and reused.demand is not None:
        work_order = reused.work_order
        demand = reused.demand
        before = work_orders.demand_snapshot(demand)
        # The restricted edit of PROJECT_PROFILE §13: raising the
        # requested quantity of a released line is always valid, and the
        # due date stays editable. Nothing else of an existing line is
        # ever rewritten by a station receipt.
        demand.requested_quantity += received_quantity
        if demand_due_date is not None:
            demand.due_date = demand_due_date
        # A receipt that raises a line is an edit of that line: it
        # stamps `updated_at` exactly as the Work Order save does, so
        # the line's last-changed time never lies about a station
        # having raised it.
        demand.updated_at = func.now()
        flush(session, {})
        audit.append_audit_event(
            session,
            event_type=AuditEventType.UPDATED,
            entity_type=AuditEntityType.WORK_ORDER_DEMAND,
            entity_id=str(demand.id),
            before_data=before,
            after_data=work_orders.demand_snapshot(demand),
        )
    else:
        work_order = WorkOrder(
            work_order_number=None,
            # §14: the received date defaults to the scan — the site's
            # calendar day OF THE SCAN, never of the confirmation.
            received_date=received_on,
            due_date=None,
            status=WorkOrderStatus.OPEN,
        )
        session.add(work_order)
        flush(session, {})
        demand = WorkOrderDemand(
            work_order_id=work_order.id,
            part_number=pn,
            request_type=intake_request_type,
            requested_quantity=received_quantity,
            due_date=demand_due_date,
            job_numbers=[],
            requester=None,
            reason=receipt_reason,
            notes=None,
        )
        session.add(demand)
        flush(session, {})
        audit.append_audit_event(
            session,
            event_type=AuditEventType.CREATED,
            entity_type=AuditEntityType.WORK_ORDER,
            entity_id=str(work_order.id),
            before_data=None,
            after_data=work_orders.work_order_snapshot(work_order),
        )
        audit.append_audit_event(
            session,
            event_type=AuditEventType.CREATED,
            entity_type=AuditEntityType.WORK_ORDER_DEMAND,
            entity_id=str(demand.id),
            before_data=None,
            after_data=work_orders.demand_snapshot(demand),
        )

    snapshot: AssignedRoute | None = None
    first_snapshot_step: AssignedRouteStep | None = None
    if template is not None:
        snapshot = AssignedRoute(source_route_template_id=template.id)
        session.add(snapshot)
        flush(session, {})
        snapshot_steps = [
            AssignedRouteStep(
                assigned_route_id=snapshot.id,
                sequence=step.sequence,
                area_id=step.area_id,
                operation_id=step.operation_id,
                expected_duration=step.expected_duration,
                instructions=step.instructions,
            )
            for step in template_steps
        ]
        session.add_all(snapshot_steps)
        flush(session, {})
        first_snapshot_step = snapshot_steps[0]

    state = processing_state_of(
        MovementType.RECEIVED,
        direct_processing=not area_has_machines(session, area.id),
    )
    flow = QuantityFlow(
        part_number=pn,
        quantity=received_quantity,
        status=QuantityFlowStatus.ACTIVE,
        route_mode=mode,
        assigned_route_id=snapshot.id if snapshot is not None else None,
        # The projection is set by the INSERT itself: no
        # projection-less row ever exists.
        current_area_id=area.id,
        current_machine_id=None,
    )
    session.add(flow)
    flush(session, {})

    metadata: dict[str, Any] = {
        **command_metadata(_COMMAND_KIND, fingerprint, size=1),
        # The same immutable release context every RECEIVED carries:
        # the demand this quantity was introduced for (the evidence the
        # released-quantity derivation and the read models use).
        CONTEXT_KEY: {DEMAND_ID_KEY: demand.id},
        INTAKE_KEY: {
            "request_type": str(intake_request_type),
            "processing_state": state.value,
            "work_order_reused": reused is not None,
        },
    }
    movement = PartMovement(
        quantity_flow_id=flow.id,
        part_number=pn,
        movement_type=MovementType.RECEIVED,
        quantity=received_quantity,
        from_area_id=None,
        to_area_id=area.id,
        operation_id=operation.id,
        assigned_route_step_id=(
            first_snapshot_step.id if first_snapshot_step is not None else None
        ),
        station_id=station.station_id,
        source_machine_id=None,
        destination_machine_id=None,
        reason=receipt_reason,
        occurred_at=func.now(),
        server_received_at=func.now(),
        device_event_id=event_id,
        command_sequence=1,
        metadata_=metadata,
    )
    session.add(movement)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) == DEVICE_EVENT_ID_CONSTRAINT:
            winner = committed_command(session, event_id)
            if winner:
                return _replay_or_conflict(session, winner, fingerprint)
        raise
    return _receipt_result(session, [movement], created=True)
