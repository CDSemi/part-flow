"""Work Order intake services (Phase 4 — Work Orders view, demand save).

Application-layer operations behind the Work Orders management view
(GUI_DESIGN §11.1–§11.3): listing/searching the WO list, loading Work
Order Details with its demand lines, creating a Work Order with its
demand draft, and saving edits. Business demand only — no production
release, no QuantityFlow, no PartMovement, and no projection change
ever originates here (PROJECT_PROFILE §13; SLICE1_DATA_MODEL §7).

Rules owned here (PROJECT_PROFILE §7 Work Order, §8.2, §8.3, §13;
SLICE1_DATA_MODEL §5, §16; IMPLEMENTATION_ROADMAP Phase 4):

- The external Work Order Number is nullable: a blank confirmed input
  persists ``NULL`` on an internal Work Order (rendered ``—`` by the
  UI, never persisted as a placeholder; no temporary number is ever
  generated) and may receive the real number later through an audited
  edit. An entered number is kept **verbatim** — never trimmed,
  reformatted, padded, or normalized — and non-null numbers are unique
  across all Work Order data (partial unique index). An existing
  entered number is surfaced as a conflict so the UI opens the
  existing Work Order instead of creating a duplicate.
- ``received_date`` is required and defaults to the current date
  during manual creation; both due dates are nullable — a missing due
  date is valid data, never a validation error, and never blocks
  saving.
- Each demand line keeps its canonical PN **by value** (no FK to the
  optional master); the master record is created on first valid use
  through ``ensure_part_number`` inside the same transaction. A PN may
  appear on one Work Order only once — the UI focuses the existing
  line instead of adding a duplicate, and the server rejects one.
- Request Type is ``NEW``/``MODIFY`` with manual entry defaulting to
  ``NEW``; ``requested_quantity`` is a positive integer; external Job
  Numbers are preserved verbatim as metadata (never a Job aggregate).
- One Save from the UI is ONE transaction: the Work Order header, its
  new and edited demand lines, any first-use PN masters, and every
  ``audit_events`` row commit together or roll back together — no
  partial business draft can persist (SLICE1_DATA_MODEL §16).
- Demand-line removal is deliberately absent (deferred with the
  release workflow that its released-quantity rule depends on), as are
  release, allocation, and Completed Work Orders.
"""

import datetime
from collections.abc import Mapping, Sequence
from typing import Any, Final, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session

from app.application import audit
from app.application.common import UNSET, UnsetType, commit, flush, optional_text
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.application.part_numbers import PART_NUMBER_CONFLICTS, ensure_part_number
from app.domain.enums import AuditEntityType, AuditEventType, RequestType, WorkOrderStatus
from app.infrastructure.models import WorkOrder, WorkOrderDemand

_WORK_ORDER_CONFLICTS: Final = {
    "uq_work_orders_work_order_number": (
        "A Work Order with this Work Order Number already exists."
        " Open the existing Work Order instead of creating a duplicate."
    ),
    **PART_NUMBER_CONFLICTS,
}


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------


def _normalized_work_order_number(value: object) -> str | None:
    """Blank confirmed input persists NULL; an entered number stays verbatim.

    PROJECT_PROFILE §7 Work Order: an entered number is an opaque
    arbitrary string that is never reformatted or normalized — not even
    surrounding whitespace is touched. Only a value that is blank
    (empty or whitespace-only — nothing was entered) becomes NULL.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidInputError("Work Order Number must be text.")
    if not value.strip():
        return None
    return value


def _validated_quantity(value: object) -> int:
    # bool is an int subclass — an explicit true/false is never a
    # quantity.
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidInputError("Requested quantity must be a positive whole number.")
    return value


def _validated_request_type(value: object) -> RequestType:
    """Manual entry defaults to NEW (PROJECT_PROFILE §7 Request Type)."""
    if value is None:
        return RequestType.NEW
    if isinstance(value, RequestType):
        return value
    if isinstance(value, str):
        try:
            return RequestType(value)
        except ValueError:
            pass
    raise InvalidInputError("Request Type must be NEW or MODIFY.")


def _validated_job_numbers(value: object) -> list[str]:
    """External Job Numbers are metadata preserved verbatim (§8.3)."""
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise InvalidInputError("Job Numbers must be a list of text values.")
    return list(value)


# ---------------------------------------------------------------------------
# Audit snapshots — the audited business fields, JSON-ready
# ---------------------------------------------------------------------------


def _iso(value: datetime.date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _work_order_snapshot(work_order: WorkOrder) -> dict[str, Any]:
    return {
        "work_order_number": work_order.work_order_number,
        "received_date": _iso(work_order.received_date),
        "due_date": _iso(work_order.due_date),
        "status": work_order.status,
    }


def _demand_snapshot(demand: WorkOrderDemand) -> dict[str, Any]:
    return {
        "work_order_id": demand.work_order_id,
        "part_number": demand.part_number,
        "request_type": demand.request_type,
        "requested_quantity": demand.requested_quantity,
        "due_date": _iso(demand.due_date),
        "job_numbers": list(demand.job_numbers),
        "requester": demand.requester,
        "reason": demand.reason,
        "notes": demand.notes,
    }


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


class WorkOrderSummary(NamedTuple):
    """One WO-list row: header plus its demand-line aggregate (§11.1)."""

    work_order: WorkOrder
    demand_line_count: int
    part_numbers: list[str]


class WorkOrderDetail(NamedTuple):
    """Work Order Details: header plus demand lines in creation order."""

    work_order: WorkOrder
    demands: list[WorkOrderDemand]


def list_work_orders(
    session: Session, *, search: str | None = None, number: str | None = None
) -> list[WorkOrderSummary]:
    """List/search the WO list (GUI_DESIGN §11.1), newest first.

    ``number`` is the exact-resolution lookup the New Work Order dialog
    uses to open an existing entered number instead of duplicating it —
    verbatim equality, because stored numbers are verbatim. ``search``
    is a case-insensitive contains-match over the Work Order Number
    with LIKE wildcards escaped (a lookup convenience only).
    """
    part_numbers = func.array_agg(
        aggregate_order_by(WorkOrderDemand.part_number, WorkOrderDemand.id)
    )
    query = (
        select(WorkOrder, func.count(WorkOrderDemand.id), part_numbers)
        .outerjoin(WorkOrderDemand, WorkOrderDemand.work_order_id == WorkOrder.id)
        .group_by(WorkOrder.id)
        .order_by(WorkOrder.received_date.desc(), WorkOrder.id.desc())
    )
    if number is not None:
        query = query.where(WorkOrder.work_order_number == number)
    elif search is not None and search.strip():
        escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(WorkOrder.work_order_number.ilike(f"%{escaped}%", escape="\\"))
    return [
        WorkOrderSummary(
            work_order=work_order,
            demand_line_count=count,
            # array_agg over an empty outer join yields [None].
            part_numbers=[value for value in (values or []) if value is not None],
        )
        for work_order, count, values in session.execute(query)
    ]


def get_work_order(session: Session, work_order_id: int) -> WorkOrderDetail:
    work_order = session.get(WorkOrder, work_order_id)
    if work_order is None:
        raise NotFoundError(f"Work Order {work_order_id} does not exist.")
    demands = list(
        session.scalars(
            select(WorkOrderDemand)
            .where(WorkOrderDemand.work_order_id == work_order.id)
            .order_by(WorkOrderDemand.id)
        )
    )
    return WorkOrderDetail(work_order, demands)


def _reject_duplicate_work_order_number(
    session: Session, number: str, exclude_id: int | None = None
) -> None:
    """Friendly pre-check; the partial unique index stays the authority."""
    query = select(WorkOrder.id).where(WorkOrder.work_order_number == number).limit(1)
    if exclude_id is not None:
        query = query.where(WorkOrder.id != exclude_id)
    if session.scalar(query) is not None:
        raise ConflictError(
            f"Work Order '{number}' already exists."
            " Open the existing Work Order instead of creating a duplicate."
        )


# ---------------------------------------------------------------------------
# Demand-line drafts
# ---------------------------------------------------------------------------


def _stage_new_line(
    session: Session,
    work_order: WorkOrder,
    draft: Mapping[str, Any],
    taken_part_numbers: set[str],
    *,
    actor: str | None,
) -> WorkOrderDemand:
    """Validate one new demand line and stage it with its PN master.

    Stages only — the caller owns the transaction. The master is
    created on first valid use through ``ensure_part_number`` (which
    also stages the PN ``CREATED`` audit row); the demand keeps the
    canonical PN by value.
    """
    master, _ = ensure_part_number(session, draft.get("part_number"), actor=actor)
    if master.part_number in taken_part_numbers:
        raise InvalidInputError(
            f"Part Number '{master.part_number}' is already on this Work Order."
            " Edit the existing demand line instead of adding a duplicate."
        )
    taken_part_numbers.add(master.part_number)
    demand = WorkOrderDemand(
        work_order_id=work_order.id,
        part_number=master.part_number,
        request_type=_validated_request_type(draft.get("request_type")),
        requested_quantity=_validated_quantity(draft.get("requested_quantity")),
        due_date=draft.get("due_date"),
        job_numbers=_validated_job_numbers(draft.get("job_numbers")),
        requester=optional_text(draft.get("requester")),
        reason=optional_text(draft.get("reason")),
        notes=optional_text(draft.get("notes")),
    )
    session.add(demand)
    return demand


def _apply_line_edit(demand: WorkOrderDemand, edit: Mapping[str, Any]) -> bool:
    """Apply a partial demand-line edit; returns whether anything changed.

    The PN of a saved line is deliberately not editable — a different
    PN is a new demand line (the Add Part flow), never a rewrite of an
    existing one. ``allocated_quantity`` and ``priority_rank`` stay
    server-owned (allocation and Hot ranking are later phases).
    """
    changed = False
    if "request_type" in edit:
        # The NEW default exists for creating a line only; an explicit
        # null on an edit is not reinterpreted — a saved demand always
        # has a Request Type, so clearing it is rejected with no write.
        if edit["request_type"] is None:
            raise InvalidInputError(
                "Request Type cannot be cleared: a demand line is always NEW or"
                " MODIFY. Omit the field to keep the current value."
            )
        request_type = _validated_request_type(edit["request_type"])
        if request_type != demand.request_type:
            demand.request_type = request_type
            changed = True
    if "requested_quantity" in edit:
        quantity = _validated_quantity(edit["requested_quantity"])
        if quantity != demand.requested_quantity:
            demand.requested_quantity = quantity
            changed = True
    if "due_date" in edit and edit["due_date"] != demand.due_date:
        demand.due_date = edit["due_date"]
        changed = True
    if "job_numbers" in edit:
        job_numbers = _validated_job_numbers(edit["job_numbers"])
        if job_numbers != list(demand.job_numbers):
            demand.job_numbers = job_numbers
            changed = True
    for field in ("requester", "reason", "notes"):
        if field in edit:
            text_value = optional_text(edit[field])
            if text_value != getattr(demand, field):
                setattr(demand, field, text_value)
                changed = True
    return changed


# ---------------------------------------------------------------------------
# Create — New Work Order dialog, Save demand
# ---------------------------------------------------------------------------


def create_work_order(
    session: Session,
    *,
    work_order_number: object = None,
    received_date: datetime.date | None = None,
    due_date: datetime.date | None = None,
    lines: Sequence[Mapping[str, Any]],
    actor: str | None = None,
) -> WorkOrderDetail:
    """Create a Work Order with its demand draft as ONE transaction.

    Saves business demand only: no QuantityFlow, no PartMovement, no
    projection change. The header, every demand line, any first-use PN
    masters, and all audit rows (`CREATED` for the Work Order, each
    line, and each new PN) commit together or not at all.
    """
    number = _normalized_work_order_number(work_order_number)
    if number is not None:
        _reject_duplicate_work_order_number(session, number)
    if not lines:
        # PROJECT_PROFILE §8.2: a Work Order contains one or more
        # Work Order Demand records.
        raise InvalidInputError("A Work Order needs at least one demand line.")

    work_order = WorkOrder(
        work_order_number=number,
        received_date=received_date if received_date is not None else datetime.date.today(),
        due_date=due_date,
        status=WorkOrderStatus.OPEN,
    )
    session.add(work_order)
    flush(session, _WORK_ORDER_CONFLICTS)

    taken: set[str] = set()
    demands = [_stage_new_line(session, work_order, draft, taken, actor=actor) for draft in lines]
    flush(session, _WORK_ORDER_CONFLICTS)

    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.WORK_ORDER,
        entity_id=str(work_order.id),
        before_data=None,
        after_data=_work_order_snapshot(work_order),
        actor_reference=actor,
    )
    for demand in demands:
        audit.append_audit_event(
            session,
            event_type=AuditEventType.CREATED,
            entity_type=AuditEntityType.WORK_ORDER_DEMAND,
            entity_id=str(demand.id),
            before_data=None,
            after_data=_demand_snapshot(demand),
            actor_reference=actor,
        )
    commit(session, _WORK_ORDER_CONFLICTS)
    return WorkOrderDetail(work_order, demands)


# ---------------------------------------------------------------------------
# Update — Work Order Details dialog, Save demand
# ---------------------------------------------------------------------------


def update_work_order(
    session: Session,
    work_order_id: int,
    *,
    work_order_number: object = UNSET,
    due_date: datetime.date | None | UnsetType = UNSET,
    new_lines: Sequence[Mapping[str, Any]] = (),
    line_edits: Sequence[Mapping[str, Any]] = (),
    actor: str | None = None,
) -> WorkOrderDetail:
    """Save the Work Order Details draft as ONE transaction.

    Header edits (the audited Work Order Number edit of PROJECT_PROFILE
    §7 — including adding the real external number to an internal Work
    Order — and the nullable WO due date), edited demand lines, and new
    demand lines commit together with their audit rows or not at all.
    An unchanged field appends no audit row. Saving edits never touches
    production data. Demand-line removal is deferred with the release
    workflow (its released-quantity rule needs release to exist).
    """
    detail = get_work_order(session, work_order_id)
    work_order = detail.work_order
    demands_by_id = {demand.id: demand for demand in detail.demands}

    header_before = _work_order_snapshot(work_order)
    header_changed = False

    if not isinstance(work_order_number, UnsetType):
        number = _normalized_work_order_number(work_order_number)
        if number != work_order.work_order_number:
            if number is not None:
                _reject_duplicate_work_order_number(session, number, exclude_id=work_order.id)
            work_order.work_order_number = number
            header_changed = True
    if not isinstance(due_date, UnsetType) and due_date != work_order.due_date:
        work_order.due_date = due_date
        header_changed = True

    # One demand line may appear at most once per save: intermediate
    # states inside one Save would produce misleading multiple UPDATED
    # audit rows for what the user experienced as one edit.
    seen_edit_ids: set[object] = set()
    for edit in line_edits:
        edit_id = edit.get("id")
        if edit_id in seen_edit_ids:
            raise InvalidInputError(
                f"Demand line {edit_id} appears more than once in this save."
                " Combine the changes into one entry per demand line."
            )
        seen_edit_ids.add(edit_id)

    audited: list[tuple[WorkOrderDemand, dict[str, Any]]] = []
    for edit in line_edits:
        demand_id = edit.get("id")
        demand = demands_by_id.get(demand_id) if isinstance(demand_id, int) else None
        if demand is None:
            raise NotFoundError(
                f"Demand line {demand_id} does not exist on Work Order {work_order.id}."
            )
        before = _demand_snapshot(demand)
        if _apply_line_edit(demand, {key: value for key, value in edit.items() if key != "id"}):
            demand.updated_at = func.now()
            audited.append((demand, before))

    taken = {demand.part_number for demand in detail.demands}
    created = [
        _stage_new_line(session, work_order, draft, taken, actor=actor) for draft in new_lines
    ]
    flush(session, _WORK_ORDER_CONFLICTS)

    if header_changed:
        work_order.updated_at = func.now()
        audit.append_audit_event(
            session,
            event_type=AuditEventType.UPDATED,
            entity_type=AuditEntityType.WORK_ORDER,
            entity_id=str(work_order.id),
            before_data=header_before,
            after_data=_work_order_snapshot(work_order),
            actor_reference=actor,
        )
    for demand, before in audited:
        audit.append_audit_event(
            session,
            event_type=AuditEventType.UPDATED,
            entity_type=AuditEntityType.WORK_ORDER_DEMAND,
            entity_id=str(demand.id),
            before_data=before,
            after_data=_demand_snapshot(demand),
            actor_reference=actor,
        )
    for demand in created:
        audit.append_audit_event(
            session,
            event_type=AuditEventType.CREATED,
            entity_type=AuditEntityType.WORK_ORDER_DEMAND,
            entity_id=str(demand.id),
            before_data=None,
            after_data=_demand_snapshot(demand),
            actor_reference=actor,
        )

    if header_changed or audited or created:
        commit(session, _WORK_ORDER_CONFLICTS)
    return WorkOrderDetail(work_order, [*detail.demands, *created])
