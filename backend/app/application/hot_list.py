"""Hot list — Priority Management (Phase 12 — PROJECT_PROFILE §21, GUI_DESIGN §8).

The Application side of the Hot Work Order Demand ranking: the ranked
list read model, the candidate read behind the Add dialog, and the ONE
command that changes the list. ``work_order_demands.priority_rank`` is
the only storage of priority — "Hot" means a rank is set, rank 1 is the
highest priority — and this command is its only writer: the Work Order
intake rejects the field, and allocation, the boards, PN Tracking and
the Area inventory only read it (`allocations.canonical_demand_order`).

Rules owned here:

- **Department scope** (PROJECT_PROFILE §21 "managed within the
  Department"; GUI_DESIGN §8): every read and the command resolve the
  single active Department (`production_board.resolve_department`) —
  none is 404, several is 409, nothing is shown or written (only the
  replay of an already committed change answers regardless, see
  Idempotency). The rank is
  one column per demand and demand carries no Department, so the one
  rank space is the Department's own only while exactly one Department
  is active; the distribution shown per entry is restricted to that
  Department's Areas.
- **Invariant H1**: the ranked demands carry exactly the ranks 1..N.
  PostgreSQL enforces uniqueness and positivity
  (`uq_work_order_demands_priority_rank`,
  `ck_work_order_demands_priority_rank_positive`); this command always
  renumbers to 1..N.
- **One single-entry delta per change** (`app.domain.hot_list`): add
  at the bottom, remove, move up / down by one, drag to any position,
  or an Undo / Redo of any one of them. Every change carries the full
  order the manager confirmed against (``expected_order``) as an
  optimistic precondition: a different current order is refused with
  the current entries and nothing is written.
- **Eligibility to join** (PROJECT_PROFILE §14 active demand, §21
  item 3): the demand exists, is not ranked, its Work Order is not
  completed, and ``requested_quantity > allocated_quantity`` — judged
  on the demand row locked FOR UPDATE and re-read, the lock allocation
  and reversal hold while they change ``allocated_quantity`` and
  ``completed_at``. Each demand of a PN is ranked separately.
- **Inactive entries stay** until a manager removes them: a completed
  Work Order or a fully allocated line keeps its rank (nothing is
  written automatically) and may still be removed or moved. Such a
  write changes ``priority_rank`` of a demand of a completed Work Order
  — an accepted exception: a priority write is not a Work Order edit,
  and the Work Order itself stays read-only history.
- **Locking**: the Hot advisory lock first, then the ranked order read
  without row locks (no other writer exists), then ``FOR UPDATE`` on
  only the changed rows plus the inserted row, ascending id, re-read.
  No Work Order or PN lock is ever taken, and the command holds no row
  while it waits on its advisory lock, so it cannot join a deadlock
  with allocation, reversal, release, intake, a Work Order save or a
  demand-line removal — each of which takes demand rows ascending and
  never the Hot lock. The demand-line removal refuses a ranked demand
  under the same row lock (`work_orders.delete_work_order_demand`).
- **Idempotency** (SLICE1_DATA_MODEL §14): one ``device_event_id`` per
  submission; the fingerprint is the SHA-256 of the canonical
  ``{action, expected_order, new_order}``. The audit rows of the
  change ARE the idempotency record (no separate table and no UNIQUE
  on the id): the lookup runs before the advisory lock and again once
  it is granted, so a concurrent identical retry replays instead of
  applying twice. A replay rebuilds ``changes`` from those audit rows
  alone — the identity snapshot lives in their metadata — so it equals
  the original even after a demand was renumbered or deleted since, and
  it never depends on the Department configuration: when no single
  active Department exists any more, the replay still answers with the
  committed ``changes`` and ``entries`` is None (the list cannot be
  shown).
- **Audit** (PROJECT_PROFILE §28): one ``UPDATED`` ``WorkOrderDemand``
  row per demand whose rank changed, in the same transaction, with
  ``before_data`` / ``after_data`` holding only the rank and the
  ``hot_list_change`` metadata block. ``actor_reference`` stays NULL
  until authentication exists (Phase 14 — role enforcement too).
"""

import datetime
from collections.abc import Collection, Sequence
from typing import Any, Final, NamedTuple

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session

from app.application import audit, production_release
from app.application.allocations import canonical_demand_order
from app.application.common import commit, device_event_id_text, flush
from app.application.errors import (
    ConflictError,
    HotListChangedError,
    IdempotencyConflictError,
    InvalidInputError,
    NotFoundError,
)
from app.application.part_numbers import canonical_part_number
from app.application.production_board import (
    LocationState,
    flow_positions,
    group_locations,
    resolve_department,
)
from app.domain.enums import AuditEntityType, AuditEventType, QuantityFlowStatus, RequestType
from app.domain.hot_list import (
    HotListAction,
    Insert,
    InvalidHotListChangeError,
    fingerprint,
    interpret_change,
    require_action,
    target_ranks,
)
from app.infrastructure.models import (
    HOT_LIST_DEVICE_EVENT_ID,
    PART_NUMBER_BARCODE_PREFIX,
    Area,
    AuditEvent,
    Department,
    QuantityFlow,
    WorkOrder,
    WorkOrderDemand,
)

#: The metadata block of every Hot list audit row; its
#: ``device_event_id`` is indexed (`ix_audit_events_hot_list_device_event_id`).
HOT_LIST_CHANGE_KEY: Final = "hot_list_change"

#: The ONE advisory lock serializing every Hot list change.
_HOT_LIST_LOCK_KEY: Final = "partflow:hot-list"

#: Candidates a search (or the unfiltered list) returns at most; a PN
#: barcode returns every eligible demand of its PN.
CANDIDATE_LIMIT: Final = 50

#: The largest demand id PostgreSQL can bind (``integer`` primary key):
#: a larger one is refused as malformed input before any query.
_MAX_DEMAND_ID: Final = 2_147_483_647

_STALE_MESSAGE: Final = (
    "The Hot list was changed elsewhere. The current list is shown; review it and try"
    " again. Nothing was changed."
)
_MISSING_MESSAGE: Final = "This Work Order Demand no longer exists. Nothing was changed."
_INVALID_CHANGE_MESSAGE: Final = (
    "The requested change is not a single add, remove or move of one Hot entry."
    " Nothing was changed."
)

# A defect guard only: the advisory lock serializes every rank writer,
# so the UNIQUE rank can never be lost to a race at flush or COMMIT.
HOT_CONFLICTS: Final = {"uq_work_order_demands_priority_rank": _STALE_MESSAGE}


class HotLocation(NamedTuple):
    """One grouped ACTIVE position of an entry's PN in the Department.

    Plain values, not ORM rows: a command builds its entries before
    COMMIT, under its locks, and returns them after.
    """

    area_id: int
    area_name: str
    area_color: str | None
    machine_id: int | None
    machine_name: str | None
    # The external Operation's name, as on the Production Board.
    activity: str | None
    # Active states only — the Hot list shows no stocked quantity.
    state: LocationState
    quantity: int


class HotEntry(NamedTuple):
    """One Work Order Demand as the Hot list (or a candidate list) shows it."""

    work_order_demand_id: int
    # None only in a candidate list.
    rank: int | None
    part_number: str
    work_order_id: int
    # None = internal Work Order (rendered `—`).
    work_order_number: str | None
    work_order_received_date: datetime.date
    work_order_completed: bool
    request_type: RequestType
    job_numbers: list[str]
    requested_quantity: int
    # The maintained allocation projection of the demand.
    allocated_quantity: int
    released_quantity: int
    due_date: datetime.date | None
    # PN-level: every demand of one PN shows the same distribution —
    # it is never attributed to one demand.
    part_number_locations: list[HotLocation]

    @property
    def shortage_quantity(self) -> int:
        return max(self.requested_quantity - self.allocated_quantity, 0)

    @property
    def active(self) -> bool:
        """Active demand of an open Work Order (PROJECT_PROFILE §14)."""
        return not self.work_order_completed and self.shortage_quantity > 0


class HotList(NamedTuple):
    department: Department
    entries: list[HotEntry]


class HotListCandidates(NamedTuple):
    # The PN a barcode named; None for a search or the unfiltered list.
    part_number: str | None
    candidates: list[HotEntry]
    # Matching demands already on the Hot list (any state), so the view
    # can explain an empty result.
    already_listed_count: int
    truncated: bool


class HotListChangeLine(NamedTuple):
    """One demand whose rank a change set, moved or cleared."""

    work_order_demand_id: int
    part_number: str
    work_order_number: str | None
    previous_rank: int | None
    new_rank: int | None


class HotListChange(NamedTuple):
    """One applied (or replayed) Hot list change.

    ``changes`` is the original change — rebuilt from the audit rows on
    a replay; ``entries`` is the list as it stood at COMMIT for a fresh
    change and the CURRENT list for a replay — None for a replay while
    no single active Department exists, so the list cannot be shown.
    ``created`` is False for an idempotent replay.
    """

    device_event_id: str
    action: HotListAction
    created: bool
    changes: list[HotListChangeLine]
    entries: list[HotEntry] | None


# ---------------------------------------------------------------------------
# Department scope
# ---------------------------------------------------------------------------


def resolve_hot_list_department(session: Session) -> Department:
    """The single active Department the Hot list is managed within.

    None is 404 and several is 409 (`production_board.resolve_department`);
    the 409 is restated in the Hot list's terms. There is deliberately
    no Department parameter: the rank is one column per demand, so
    addressing one of several Departments would present a list that is
    not that Department's own.
    """
    try:
        return resolve_department(session, None)
    except ConflictError as exc:
        active = session.scalars(
            select(Department)
            .where(Department.is_active.is_(True))
            .order_by(Department.name, Department.id)
        )
        names = ", ".join(f"{department.name} (id {department.id})" for department in active)
        raise ConflictError(
            f"Several active Departments exist ({names}). The Hot list is managed within one"
            " Department, so nothing can be shown or changed until exactly one Department is"
            " active."
        ) from exc


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------


def _part_number_locations(
    session: Session, department: Department, part_numbers: Collection[str]
) -> dict[str, list[HotLocation]]:
    """The PNs' ACTIVE quantity in the Department's Areas, grouped and in
    presentation order exactly as the Production Board groups it."""
    wanted = set(part_numbers)
    if not wanted:
        return {}
    areas = {
        area.id: area
        for area in session.scalars(select(Area).where(Area.department_id == department.id))
    }
    if not areas:
        return {}
    flows = list(
        session.scalars(
            select(QuantityFlow)
            .where(
                QuantityFlow.part_number.in_(wanted),
                QuantityFlow.current_area_id.in_(areas.keys()),
                QuantityFlow.status == QuantityFlowStatus.ACTIVE,
            )
            .order_by(QuantityFlow.part_number, QuantityFlow.id)
        )
    )
    groups = group_locations(flow_positions(session, flows).values(), areas)
    return {
        pn: [
            HotLocation(
                area_id=location.area.id,
                area_name=location.area.name,
                area_color=location.area.color,
                machine_id=location.machine.id if location.machine is not None else None,
                machine_name=location.machine.name if location.machine is not None else None,
                activity=location.activity,
                state=location.state,
                quantity=location.quantity,
            )
            for location in locations
        ]
        for pn, locations in groups.items()
    }


def _entries(
    session: Session,
    department: Department,
    rows: Sequence[tuple[WorkOrderDemand, WorkOrder]],
) -> list[HotEntry]:
    """Entries for demand rows in the given order — one released-quantity
    query and one location derivation for all of them."""
    released = production_release.released_quantities(session, [demand.id for demand, _ in rows])
    locations = _part_number_locations(
        session, department, {demand.part_number for demand, _ in rows}
    )
    return [
        HotEntry(
            work_order_demand_id=demand.id,
            rank=demand.priority_rank,
            part_number=demand.part_number,
            work_order_id=work_order.id,
            work_order_number=work_order.work_order_number,
            work_order_received_date=work_order.received_date,
            work_order_completed=work_order.completed_at is not None,
            request_type=RequestType(demand.request_type),
            job_numbers=list(demand.job_numbers),
            requested_quantity=demand.requested_quantity,
            allocated_quantity=demand.allocated_quantity,
            released_quantity=released.get(demand.id, 0),
            due_date=demand.due_date,
            part_number_locations=list(locations.get(demand.part_number, [])),
        )
        for demand, work_order in rows
    ]


def _ranked_rows(session: Session) -> list[tuple[WorkOrderDemand, WorkOrder]]:
    return list(
        session.execute(
            select(WorkOrderDemand, WorkOrder)
            .join(WorkOrder, WorkOrder.id == WorkOrderDemand.work_order_id)
            .where(WorkOrderDemand.priority_rank.is_not(None))
            .order_by(WorkOrderDemand.priority_rank)
        ).tuples()
    )


def hot_list(session: Session) -> HotList:
    """The Department's Hot list in rank order, inactive entries included."""
    department = resolve_hot_list_department(session)
    return HotList(department, _entries(session, department, _ranked_rows(session)))


def _contains_pattern(term: str) -> str:
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _part_number_from_barcode(barcode: str) -> str:
    """The canonical PN of a scanned ``PF:PN:`` barcode (PROJECT_PROFILE §10).

    Any other value — another ``PF:`` namespace, raw text — is refused
    with the Priority view's own copy; the suffix goes through the one
    domain normalization, whose message is kept.
    """
    scanned = barcode.strip()
    if not scanned.startswith(PART_NUMBER_BARCODE_PREFIX):
        raise InvalidInputError(
            "This is not a Part Number barcode. Scan a PN barcode (PF:PN:…) or search by PN,"
            " Work Order Number or Job Number."
        )
    return canonical_part_number(scanned[len(PART_NUMBER_BARCODE_PREFIX) :])


def hot_list_candidates(
    session: Session, *, search: str | None = None, barcode: str | None = None
) -> HotListCandidates:
    """Eligible demand for the Add dialog, in the canonical demand order.

    Eligible is an unranked, active demand of an open Work Order
    (``requested_quantity > allocated_quantity``, PROJECT_PROFILE §14).
    ``search`` is a case-insensitive contains-match on the PN, the Work
    Order Number or any Job Number with LIKE wildcards escaped; it — and
    the unfiltered list — returns at most :data:`CANDIDATE_LIMIT` rows
    and reports more as ``truncated``. ``barcode`` names one PN and
    returns ALL of its eligible demand. A read: no row lock is taken.
    """
    if search is not None and barcode is not None:
        raise InvalidInputError(
            "Search by text or scan a PN barcode — not both. Nothing was searched."
        )
    # PostgreSQL text cannot hold a NUL character: refuse it before any query.
    if any(value is not None and "\x00" in value for value in (search, barcode)):
        raise InvalidInputError(
            "The search text or barcode contains a NUL character. Nothing was searched."
        )
    department = resolve_hot_list_department(session)
    part_number: str | None = None
    limit: int | None = CANDIDATE_LIMIT
    match: ColumnElement[bool] | None = None
    if barcode is not None:
        part_number = _part_number_from_barcode(barcode)
        match = WorkOrderDemand.part_number == part_number
        limit = None
    elif search is not None and search.strip():
        pattern = _contains_pattern(search)
        job_number = func.unnest(WorkOrderDemand.job_numbers).column_valued("job_number")
        match = or_(
            WorkOrderDemand.part_number.ilike(pattern, escape="\\"),
            WorkOrder.work_order_number.ilike(pattern, escape="\\"),
            select(1).where(job_number.ilike(pattern, escape="\\")).exists(),
        )

    def matching(query: Select[Any]) -> Select[Any]:
        query = query.join(WorkOrder, WorkOrder.id == WorkOrderDemand.work_order_id)
        return query.where(match) if match is not None else query

    eligible = canonical_demand_order(
        matching(select(WorkOrderDemand, WorkOrder)).where(
            WorkOrderDemand.priority_rank.is_(None),
            WorkOrder.completed_at.is_(None),
            WorkOrderDemand.requested_quantity > WorkOrderDemand.allocated_quantity,
        )
    )
    if limit is not None:
        # One row past the limit tells "exactly 50" from "more".
        eligible = eligible.limit(limit + 1)
    rows: list[tuple[WorkOrderDemand, WorkOrder]] = [
        (demand, work_order) for demand, work_order in session.execute(eligible)
    ]
    truncated = limit is not None and len(rows) > limit
    if limit is not None:
        rows = rows[:limit]
    already_listed = session.scalar(
        matching(select(func.count()).select_from(WorkOrderDemand)).where(
            WorkOrderDemand.priority_rank.is_not(None)
        )
    )
    return HotListCandidates(
        part_number=part_number,
        candidates=_entries(session, department, rows),
        already_listed_count=int(already_listed or 0),
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# The command — seams and helpers
# ---------------------------------------------------------------------------


def acquire_hot_list_lock(session: Session) -> None:
    """Serialize this transaction against every other Hot list change.

    ``pg_advisory_xact_lock`` releases with the transaction. It is taken
    before any row lock; a hash collision with a PN-level lock key only
    serializes the two.
    """
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(_HOT_LIST_LOCK_KEY, 0)))
    )


def current_ranked_order(session: Session) -> list[int]:
    """The ranked demand ids in rank order — read under the Hot lock.

    No row lock: the Hot command is the only rank writer and it holds
    the advisory lock, so this reading is the authority until COMMIT.
    """
    return list(
        session.scalars(
            select(WorkOrderDemand.id)
            .where(WorkOrderDemand.priority_rank.is_not(None))
            .order_by(WorkOrderDemand.priority_rank)
        )
    )


def lock_demand_rows(session: Session, demand_ids: Collection[int]) -> dict[int, WorkOrderDemand]:
    """The demand rows locked FOR UPDATE ascending and RE-READ under the lock.

    A missing row — an inserted demand deleted meanwhile — is a 404
    with nothing written.
    """
    locked: dict[int, WorkOrderDemand] = {}
    for demand_id in sorted(set(demand_ids)):
        demand = session.get(
            WorkOrderDemand, demand_id, with_for_update=True, populate_existing=True
        )
        if demand is None:
            raise NotFoundError(_MISSING_MESSAGE)
        locked[demand_id] = demand
    return locked


def _work_order_label(work_order: WorkOrder) -> str:
    return (
        work_order.work_order_number if work_order.work_order_number is not None else "— (internal)"
    )


def _require_eligible(session: Session, demand: WorkOrderDemand) -> None:
    """An inserted demand must be active demand of an open Work Order.

    Judged on the locked, re-read demand row: allocation and reversal
    change ``allocated_quantity`` and ``completed_at`` under that same
    row lock, so the reading cannot go stale before COMMIT.
    """
    work_order = session.get(WorkOrder, demand.work_order_id, populate_existing=True)
    if work_order is None:  # pragma: no cover - the demand's FK guarantees the row
        raise NotFoundError(_MISSING_MESSAGE)
    label = _work_order_label(work_order)
    if work_order.completed_at is not None:
        raise ConflictError(
            f"Work Order {label} is completed, so its demand cannot be added to the Hot list."
            " Nothing was changed."
        )
    if demand.requested_quantity <= demand.allocated_quantity:
        raise ConflictError(
            f"{demand.part_number} on Work Order {label} is fully allocated"
            f" ({demand.allocated_quantity} of {demand.requested_quantity} pcs), so there is"
            " nothing left to expedite. Nothing was changed."
        )


def _validated_action(value: object) -> HotListAction:
    if isinstance(value, HotListAction):
        return value
    if isinstance(value, str):
        try:
            return HotListAction(value)
        except ValueError:
            pass
    raise InvalidInputError("action must be ADD, REMOVE, MOVE_UP, MOVE_DOWN, DRAG, UNDO or REDO.")


def _validated_order(value: object, label: str) -> list[int]:
    # bool is an int subclass — true/false is never a demand id; an id
    # PostgreSQL cannot bind is malformed input, not a missing demand.
    if not isinstance(value, list | tuple) or not all(
        isinstance(item, int) and not isinstance(item, bool) and 0 < item <= _MAX_DEMAND_ID
        for item in value
    ):
        raise InvalidInputError(f"{label} must be a list of Work Order Demand ids.")
    return [int(item) for item in value]


def _change_block(row: AuditEvent) -> dict[str, Any]:
    block = (row.metadata_ or {}).get(HOT_LIST_CHANGE_KEY)
    return block if isinstance(block, dict) else {}


def _committed_change(session: Session, device_event_id: str) -> list[AuditEvent]:
    """The audit rows of an earlier change with this id, in change order."""
    # Literally the indexed expression and its predicate (models.py) —
    # the JSONB subscript form the planner matches.
    indexed_id = HOT_LIST_DEVICE_EVENT_ID
    rows = session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_type == AuditEntityType.WORK_ORDER_DEMAND,
            indexed_id == device_event_id,
        )
    )
    return sorted(rows, key=lambda row: int(_change_block(row).get("sequence", 0)))


def _replay_or_conflict(
    session: Session, rows: list[AuditEvent], change_fingerprint: str
) -> HotListChange:
    """Resolve a duplicate ``device_event_id`` against its audit rows."""
    first = _change_block(rows[0])
    if first.get("fingerprint") != change_fingerprint:
        raise IdempotencyConflictError(
            "This device_event_id was already used for a different Hot list change."
            " Nothing was changed — a new change needs a new device_event_id."
        )
    changes: list[HotListChangeLine] = []
    for row in rows:
        block = _change_block(row)
        changes.append(
            HotListChangeLine(
                work_order_demand_id=int(block["work_order_demand_id"]),
                part_number=str(block["part_number"]),
                work_order_number=block.get("work_order_number"),
                previous_rank=(row.before_data or {}).get("priority_rank"),
                new_rank=(row.after_data or {}).get("priority_rank"),
            )
        )
    return HotListChange(
        device_event_id=str(first["device_event_id"]),
        action=HotListAction(first["action"]),
        created=False,
        changes=changes,
        # The CURRENT list, freshly read — not part of the committed result.
        entries=_replay_entries(session),
    )


def _replay_entries(session: Session) -> list[HotEntry] | None:
    """The current list for a replay, or None while it cannot be shown.

    The committed change never depends on the Department configuration:
    with no or several active Departments the replay still returns the
    original ``changes`` and leaves the list to a fresh read, which
    states the configuration problem.
    """
    try:
        department = resolve_hot_list_department(session)
    except (ConflictError, NotFoundError):
        return None
    return _entries(session, department, _ranked_rows(session))


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def apply_hot_list_change(
    session: Session,
    *,
    device_event_id: object,
    action: object,
    expected_order: object,
    new_order: object,
) -> HotListChange:
    """Apply ONE confirmed single-entry change of the Hot list.

    One transaction, idempotent per ``device_event_id``; every refusal —
    stale precondition, ineligible or missing demand, invalid delta,
    Department ambiguity, mismatched id reuse — writes nothing.
    """
    # -- Pure input shape (no database) ---------------------------------
    event_id = device_event_id_text(device_event_id)
    hot_action = _validated_action(action)
    expected = _validated_order(expected_order, "expected_order")
    new = _validated_order(new_order, "new_order")
    try:
        delta = require_action(
            hot_action, interpret_change(expected, new), expected_length=len(expected)
        )
    except InvalidHotListChangeError as exc:
        raise InvalidInputError(_INVALID_CHANGE_MESSAGE) from exc
    change_fingerprint = fingerprint(hot_action, expected, new)

    # -- Idempotency fast path: a committed retry never waits ------------
    committed = _committed_change(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, change_fingerprint)

    department = resolve_hot_list_department(session)
    acquire_hot_list_lock(session)

    # -- Idempotency RE-CHECK once the lock is granted --------------------
    # A concurrent identical submission may have committed while this
    # one waited: it replays instead of applying (or tripping) twice.
    committed = _committed_change(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, change_fingerprint)

    # -- Optimistic precondition ------------------------------------------
    current = current_ranked_order(session)
    if current != expected:
        raise HotListChangedError(
            _STALE_MESSAGE, _entries(session, department, _ranked_rows(session))
        )

    previous_ranks = target_ranks(current)
    new_ranks = target_ranks(new)
    changed_ids = sorted(
        demand_id
        for demand_id in previous_ranks.keys() | new_ranks.keys()
        if previous_ranks.get(demand_id) != new_ranks.get(demand_id)
    )
    rows = lock_demand_rows(session, changed_ids)
    for demand_id, demand in rows.items():
        if demand.priority_rank != previous_ranks.get(demand_id):
            # Defect guard: the advisory lock makes this unreachable.
            raise HotListChangedError(
                _STALE_MESSAGE, _entries(session, department, _ranked_rows(session))
            )
    if isinstance(delta, Insert):
        _require_eligible(session, rows[delta.id])

    # -- Writes: clear first, then assign, so the UNIQUE rank never sees
    # a transient duplicate ------------------------------------------------
    work_orders = {
        work_order.id: work_order
        for work_order in session.scalars(
            select(WorkOrder).where(
                WorkOrder.id.in_({demand.work_order_id for demand in rows.values()})
            )
        )
    }
    for demand in rows.values():
        demand.priority_rank = None
    flush(session, HOT_CONFLICTS)
    for demand_id, demand in rows.items():
        demand.priority_rank = new_ranks.get(demand_id)
        demand.updated_at = func.now()
    flush(session, HOT_CONFLICTS)

    changes = sorted(
        (
            HotListChangeLine(
                work_order_demand_id=demand_id,
                part_number=demand.part_number,
                work_order_number=work_orders[demand.work_order_id].work_order_number,
                previous_rank=previous_ranks.get(demand_id),
                new_rank=new_ranks.get(demand_id),
            )
            for demand_id, demand in rows.items()
        ),
        key=lambda line: (
            line.new_rank is None,
            line.new_rank or 0,
            line.work_order_demand_id,
        ),
    )
    for sequence, line in enumerate(changes, start=1):
        audit.append_audit_event(
            session,
            event_type=AuditEventType.UPDATED,
            entity_type=AuditEntityType.WORK_ORDER_DEMAND,
            entity_id=str(line.work_order_demand_id),
            before_data={"priority_rank": line.previous_rank},
            after_data={"priority_rank": line.new_rank},
            metadata={
                HOT_LIST_CHANGE_KEY: {
                    "device_event_id": event_id,
                    "action": str(hot_action),
                    "fingerprint": change_fingerprint,
                    "sequence": sequence,
                    # Identity snapshot at command time: a replay
                    # rebuilds the change from these rows alone.
                    "work_order_demand_id": line.work_order_demand_id,
                    "part_number": line.part_number,
                    "work_order_id": rows[line.work_order_demand_id].work_order_id,
                    "work_order_number": line.work_order_number,
                }
            },
        )

    # The response list is read inside the transaction, under the locks.
    entries = _entries(session, department, _ranked_rows(session))
    commit(session, HOT_CONFLICTS)
    return HotListChange(
        device_event_id=event_id,
        action=hot_action,
        created=True,
        changes=changes,
        entries=entries,
    )
