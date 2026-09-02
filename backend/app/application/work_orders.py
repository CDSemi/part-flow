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
- A demand line that has released production quantity accepts a
  RESTRICTED edit, not none at all (PROJECT_PROFILE §13; GUI_DESIGN
  §11): business demand keeps changing after production started —
  quantities grow, due dates move, Job Numbers arrive — while what
  production already committed can never be contradicted. ``Qty``,
  ``due_date`` and ``job_numbers`` stay editable; the Part Number
  (never in the edit schema at all) and ``request_type`` are locked
  once quantity has been released, and ``requester``/``reason``/
  ``notes`` stay locked with them. ``requested_quantity`` may not fall
  below ``max(released_quantity, allocated_quantity)`` — it may be
  raised freely (the demand then has releasable remainder again and
  the Work Order derives back to ``OPEN``) and lowered down to exactly
  what is committed. Its released and remaining quantities are DERIVED
  from the immutable ``RECEIVED`` history
  (``production_release.released_quantities``) — never stored, so they
  survive any reload and need no migration. The check runs under a
  ``FOR UPDATE`` lock on each edited line, which is what makes it a
  rule rather than a hope: a release and an edit of the same demand
  serialize in either arrival order, and no interleaving can leave
  ``released_quantity > requested_quantity``.
- A canonical PN appears **at most once** among the current demand
  lines of one Work Order. The rule is enforced where the rows are
  written and it is serialized against itself: staging new demand
  lines first takes the parent Work Order's row lock and re-reads that
  Work Order's canonical PN set from the database **after** the lock is
  granted, so two concurrent saves adding the same PN can never both
  pass a pre-lock snapshot. The loser is refused with the ordinary
  duplicate-demand error and writes nothing.
- Demand-line removal follows the canonical rule (PROJECT_PROFILE §13):
  a saved demand may be deleted only while no production quantity has
  ever been released for it — the released-quantity evidence lives in
  the immutable ``RECEIVED`` metadata context
  (``production_release.demand_has_released_quantity``). Removal never
  touches the PartNumber master, QuantityFlows, PartMovements, release
  history, or other demand lines; an unsaved draft is a frontend
  concern and never reaches this layer. Release itself, allocation,
  and Completed Work Orders stay outside this module.
"""

import base64
import datetime
import json
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Final, Literal, NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.application import audit, production_release
from app.application.common import UNSET, UnsetType, commit, flush, optional_text
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.application.part_numbers import PART_NUMBER_CONFLICTS, ensure_part_number
from app.core.config import get_settings
from app.domain.enums import AuditEntityType, AuditEventType, RequestType, WorkOrderStatus
from app.infrastructure.models import WorkOrder, WorkOrderAllocation, WorkOrderDemand

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
    # The server-derived read status (OPEN, or RELEASED when every
    # current demand line has committed release evidence).
    status: str


class WorkOrderDetail(NamedTuple):
    """Work Order Details: header plus demand lines in creation order."""

    work_order: WorkOrder
    demands: list[WorkOrderDemand]
    # Released quantity per demand id, derived from the immutable
    # RECEIVED Movement context — the server-authoritative Released
    # evidence and the basis of each line's remaining quantity. A
    # demand never released is absent (read as 0).
    released_quantities: Mapping[int, int]
    # The server-derived read status (see WorkOrderSummary.status).
    status: str


def _derived_status(
    work_order: WorkOrder,
    demands: Collection[tuple[int, int]],
    released: Mapping[int, int],
) -> str:
    """OPEN while any current demand still has quantity left to release;
    RELEASED once every current demand line is fully released
    (GUI_DESIGN §11.1); COMPLETED once every demand line is fully
    allocated from stocked quantity (Phase 10 — `completed_at` is the
    allocation projection). Derived at read time — the stored column
    stays OPEN, so no migration and no drift are possible. A partially
    released line keeps the Work Order OPEN: its remaining quantity is
    still releasable."""
    if work_order.completed_at is not None:
        return WorkOrderStatus.COMPLETED
    if demands and all(released.get(demand_id, 0) >= requested for demand_id, requested in demands):
        return WorkOrderStatus.RELEASED
    return work_order.status


def _require_active(work_order: WorkOrder, action: str) -> None:
    """A completed Work Order is read-only history (PROJECT_PROFILE §18)."""
    if work_order.completed_at is not None:
        raise ConflictError(
            f"Work Order {work_order.id} is completed: every demand line is fully"
            f" allocated from stock, and it is read-only history. {action} Later"
            " work is a new Work Order Demand; an allocation adjustment reopens it."
        )


def _build_detail(
    session: Session, work_order: WorkOrder, demands: list[WorkOrderDemand]
) -> WorkOrderDetail:
    """Assemble the detail read model with ONE released-evidence query."""
    released = production_release.released_quantities(session, [demand.id for demand in demands])
    return WorkOrderDetail(
        work_order=work_order,
        demands=demands,
        released_quantities=released,
        status=_derived_status(
            work_order,
            [(demand.id, demand.requested_quantity) for demand in demands],
            released,
        ),
    )


#: Most Work Orders one active-list read returns. Nothing leaves the
#: active list before allocation-derived completion (Phase 10), so the
#: list only grows: it is bounded HERE, in the query, never by slicing
#: an already-transferred list in the browser. This is a result bound,
#: not a pagination contract — the client narrows with ``search``, and
#: the Completed Work Orders history (GUI_DESIGN §11.5) brings its own
#: server-side paging when it becomes real. ``number`` is unaffected:
#: an exact resolution must reach every Work Order, bound or not.
LIST_RESULT_LIMIT: Final = 100


def list_work_orders(
    session: Session, *, search: str | None = None, number: str | None = None
) -> list[WorkOrderSummary]:
    """List/search the WO list (GUI_DESIGN §11.1), newest first.

    ``number`` is the exact-resolution lookup the New Work Order dialog
    uses to open an existing entered number instead of duplicating it —
    verbatim equality, because stored numbers are verbatim, and never
    bounded away. ``search`` is a case-insensitive contains-match over
    the Work Order Number with LIKE wildcards escaped (a lookup
    convenience only), evaluated in the database and bounded there at
    :data:`LIST_RESULT_LIMIT` in the unchanged canonical order.
    """
    part_numbers = func.array_agg(
        aggregate_order_by(WorkOrderDemand.part_number, WorkOrderDemand.id)
    )
    demand_ids = func.array_agg(aggregate_order_by(WorkOrderDemand.id, WorkOrderDemand.id))
    requested = func.array_agg(
        aggregate_order_by(WorkOrderDemand.requested_quantity, WorkOrderDemand.id)
    )
    query = (
        select(WorkOrder, func.count(WorkOrderDemand.id), part_numbers, demand_ids, requested)
        .outerjoin(WorkOrderDemand, WorkOrderDemand.work_order_id == WorkOrder.id)
        .group_by(WorkOrder.id)
        .order_by(WorkOrder.received_date.desc(), WorkOrder.id.desc())
    )
    if number is not None:
        # Exact resolution answers "does this number already exist?" —
        # it must see every Work Order, completed history included, so
        # it is never bounded and never filtered.
        query = query.where(WorkOrder.work_order_number == number)
    else:
        # The active list: a completed Work Order left it (Phase 10).
        query = query.where(WorkOrder.completed_at.is_(None))
        if search is not None and search.strip():
            escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(WorkOrder.work_order_number.ilike(f"%{escaped}%", escape="\\"))
        query = query.limit(LIST_RESULT_LIMIT)
    rows = [
        (
            work_order,
            count,
            # array_agg over an empty outer join yields [None].
            [value for value in (values or []) if value is not None],
            [
                (demand_id, quantity)
                for demand_id, quantity in zip(ids or [], quantities or [], strict=True)
                if demand_id is not None
            ],
        )
        for work_order, count, values, ids, quantities in session.execute(query)
    ]
    # ONE released-evidence query for the whole page — never per row.
    released = production_release.released_quantities(
        session, [demand_id for _, _, _, lines in rows for demand_id, _ in lines]
    )
    return [
        WorkOrderSummary(
            work_order=work_order,
            demand_line_count=count,
            part_numbers=values,
            status=_derived_status(work_order, lines, released),
        )
        for work_order, count, values, lines in rows
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
    return _build_detail(session, work_order, demands)


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


def _lock_work_order_and_read_part_numbers(session: Session, work_order_id: int) -> set[str]:
    """Serialize adding demand lines to one Work Order, then re-read its PNs.

    Adding a demand line is the one write whose rule — a canonical PN
    appears at most once among a Work Order's current demand lines —
    spans rows this transaction has not touched yet, so a snapshot taken
    before the lock is not authority. The parent Work Order row lock
    makes concurrent adds to the SAME Work Order queue instead of
    interleaving, and the PN set is read from the database only after
    the lock is granted: a competing save that added the same PN while
    this one waited is visible here and its duplicate is refused with
    the ordinary duplicate-demand error, writing nothing.

    Placement matters twice over. It is taken AFTER the edited-line
    ``FOR UPDATE`` locks, keeping the system-wide demand → Work Order
    order that `delete_work_order_demand` also uses (and that
    `release_to_production` never contradicts). And it is taken BEFORE
    any mutation of this transaction, so no autoflush can emit the
    header UPDATE and take the same row lock earlier, inverting that
    order.
    """
    session.get(WorkOrder, work_order_id, with_for_update=True)
    return set(
        session.scalars(
            select(WorkOrderDemand.part_number).where(
                WorkOrderDemand.work_order_id == work_order_id
            )
        )
    )


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
    server-owned (allocation and Hot ranking are later phases). The
    extra restrictions of a line with released quantity are applied by
    the caller (``_guard_released_line_edit``) before this runs, so no
    locked field is ever written and then rolled back.
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
# The restricted edit of a demand line with released quantity (§13)
# ---------------------------------------------------------------------------

# Locked once production quantity has been released: the demand's own
# identity of the work (Request Type — like the PN, which the edit
# schema never carries) and the intake metadata recorded with it.
# Opening any of these is a separate decision, not a side effect of
# making Qty/Due date/Job Numbers editable.
_RELEASED_LINE_LOCKED_TEXT_FIELDS: Final = (
    ("requester", "Requester"),
    ("reason", "Reason"),
    ("notes", "Notes"),
)


def _locked_field_of_released_line(demand: WorkOrderDemand, edit: Mapping[str, Any]) -> str | None:
    """The locked field this edit would really change, if any.

    A field submitted with its current value changes nothing and is not
    an edit — the same no-op semantics every other field has here — so
    only a real change is reported.
    """
    if (
        "request_type" in edit
        # An explicit null is invalid on any line, released or not, and
        # stays the InvalidInputError of `_apply_line_edit`.
        and edit["request_type"] is not None
        and _validated_request_type(edit["request_type"]) != demand.request_type
    ):
        return "Request Type"
    for field, label in _RELEASED_LINE_LOCKED_TEXT_FIELDS:
        if field in edit and optional_text(edit[field]) != getattr(demand, field):
            return label
    return None


def _guard_released_line_edit(
    demand: WorkOrderDemand, edit: Mapping[str, Any], released_quantity: int
) -> None:
    """Enforce the committed-quantity rules of one demand line; write nothing.

    Runs under the caller's ``FOR UPDATE`` lock on the demand with the
    released quantity recomputed under it (PROJECT_PROFILE §13):

    - a locked field (Request Type, Requester, Reason, Notes) refuses
      the whole save — the PN is not editable on any saved line and
      never reaches here. This part is tied to RELEASE: it is what
      "Request Type is fixed once quantity has been released" means, so
      it applies only when ``released_quantity > 0``;
    - ``requested_quantity`` may not fall below what is already
      committed: ``max(released_quantity, allocated_quantity)``.
      Raising it is always allowed — the demand simply has releasable
      remainder again — and lowering it to exactly the committed
      quantity is valid, leaving nothing left to release. This part is
      tied to COMMITTED QUANTITY, not to release alone, so it also
      binds a line that only carries allocated quantity (Phase 10 —
      ``allocated_quantity`` is the projection the allocation command
      maintains under this same demand row lock, and the caller
      re-reads the row under the lock before judging it).

    Qty, due date and Job Numbers are otherwise the normal audited
    edit; nothing here touches QuantityFlows, PartMovements, or release
    history.
    """
    locked = _locked_field_of_released_line(demand, edit) if released_quantity > 0 else None
    if locked is not None:
        raise ConflictError(
            f"Cannot change {locked} for Part Number '{demand.part_number}':"
            " production quantity has already been released for this demand"
            " line. Qty, Due date and Job Numbers stay editable."
        )
    if "requested_quantity" not in edit:
        return
    quantity = _validated_quantity(edit["requested_quantity"])
    committed = max(released_quantity, demand.allocated_quantity)
    if quantity < committed:
        reason = (
            f"{released_quantity} pcs are already released"
            if released_quantity >= demand.allocated_quantity
            else f"{demand.allocated_quantity} pcs are already allocated"
        )
        raise ConflictError(
            f"Cannot lower Qty to {quantity} pcs for Part Number"
            f" '{demand.part_number}': {reason}. Enter {committed} pcs or more."
        )


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
    return _build_detail(session, work_order, demands)


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

    Every edited saved demand line is locked ``FOR UPDATE`` before this
    save decides which of its fields may be edited and how low its
    quantity may go, so the restricted-edit rule of a released line
    cannot be raced by a release in flight and
    ``released_quantity > requested_quantity`` is unreachable. A
    header-only save takes no demand lock.

    A save that adds demand lines additionally locks the parent Work
    Order row and re-reads that Work Order's canonical PN set under the
    lock, so two concurrent saves adding the same PN serialize and only
    one of them can create the line
    (``_lock_work_order_and_read_part_numbers``). Lock order stays
    demand (ascending id) → Work Order.
    """
    detail = get_work_order(session, work_order_id)
    work_order = detail.work_order
    demands_by_id = {demand.id: demand for demand in detail.demands}

    header_before = _work_order_snapshot(work_order)
    header_changed = False

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

    # Lock every edited saved demand line BEFORE deciding what may be
    # edited on it, then recompute its released quantity under that lock.
    #
    # The decision races a release otherwise: this save could read
    # "nothing released", a concurrent release could lock the same
    # demand, create quantity and commit, and this save could then still
    # lower `requested_quantity` — leaving released > requested, a
    # quantity state the domain has no meaning for. With the lock the
    # two orders are both safe and neither can interleave:
    #
    # - release commits first → the recomputed released quantity is
    #   visible here and the restricted-edit rule applies to that line
    #   against the real released quantity (a lower Qty is refused);
    # - this save commits first → the release waits on this lock, then
    #   re-reads the demand row (its own ``FOR UPDATE`` get refreshes
    #   it) and applies the remaining cap to the NEW requested quantity.
    #
    # Locking in ascending id order makes two concurrent saves queue
    # instead of deadlocking, and matches the demand→Work Order order
    # `delete_work_order_demand` already takes. A header-only save locks
    # nothing: the audited Work Order Number edit stays available after
    # a release (PROJECT_PROFILE §7).
    #
    # `populate_existing` makes the lock a RE-READ: `get_work_order`
    # above already loaded these rows into the identity map, and a lock
    # alone would keep their pre-lock attributes — `allocated_quantity`
    # (Phase 10) is read from the row itself, so a partial allocation
    # committed while this save waited would otherwise be judged on the
    # stale figure and a lowered Qty could leave allocated > requested.
    edited_ids = sorted(edit_id for edit_id in seen_edit_ids if isinstance(edit_id, int))
    for edited_id in edited_ids:
        locked = session.get(
            WorkOrderDemand, edited_id, with_for_update=True, populate_existing=True
        )
        if locked is None or locked.work_order_id != work_order.id:
            raise NotFoundError(
                f"Demand line {edited_id} does not exist on Work Order {work_order.id}."
            )
        demands_by_id[edited_id] = locked
    released = production_release.released_quantities(session, edited_ids)

    # The Work Order row is locked for every save (after the demand
    # locks — the established demand → Work Order order, the same the
    # allocation command takes) and its completion judged on the locked
    # RE-READ: a completed Work Order is read-only history (Phase 10),
    # and an allocation completing it "at the same time" as this save
    # has one serial outcome — the save sees the completion and refuses,
    # or commits first and the allocation judges the saved quantities.
    session.refresh(work_order, with_for_update=True)
    _require_active(work_order, "Nothing was saved.")

    # Adding demand lines re-reads the authoritative PN set under that
    # Work Order lock — never the `detail.demands` snapshot taken before
    # any waiting. Nothing has been mutated yet, so this cannot
    # autoflush an earlier lock.
    taken: set[str] = (
        _lock_work_order_and_read_part_numbers(session, work_order.id) if new_lines else set()
    )

    # Header edits are applied AFTER the demand locks: their UPDATE is
    # emitted at flush, so touching them first would let an autoflush
    # take the Work Order row lock before the demand locks and invert
    # the order `delete_work_order_demand` uses.
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

    audited: list[tuple[WorkOrderDemand, dict[str, Any]]] = []
    for edit in line_edits:
        demand_id = edit.get("id")
        demand = demands_by_id.get(demand_id) if isinstance(demand_id, int) else None
        if demand is None:
            raise NotFoundError(
                f"Demand line {demand_id} does not exist on Work Order {work_order.id}."
            )
        released_quantity = released.get(demand.id, 0)
        if released_quantity > 0 or demand.allocated_quantity > 0:
            # Restricted, not frozen (PROJECT_PROFILE §13): the UI
            # renders the locked fields read-only, the backend refuses
            # them anyway. Released quantity itself is never rewritten
            # from here — production corrections stay in the correction
            # and production workflows (PROJECT_PROFILE §16). A line
            # carrying only allocated quantity reaches the same guard
            # for its quantity floor alone (Phase 10).
            _guard_released_line_edit(demand, edit, released_quantity)
        before = _demand_snapshot(demand)
        if _apply_line_edit(demand, {key: value for key, value in edit.items() if key != "id"}):
            demand.updated_at = func.now()
            audited.append((demand, before))

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
    else:
        # A save that turns out to change nothing still took row locks
        # above. Ending the transaction here releases them immediately
        # instead of holding them until the request-scoped session
        # closes, where they would needlessly block a concurrent
        # release or save of the same line.
        session.rollback()
    return _build_detail(session, work_order, [*detail.demands, *created])


# ---------------------------------------------------------------------------
# Demand-line removal — the canonical rule of PROJECT_PROFILE §13
# ---------------------------------------------------------------------------


def _demand_has_allocation_history(session: Session, demand_id: int) -> bool:
    """Any allocation row — active or reversed — referencing the demand line."""
    return (
        session.scalar(
            select(WorkOrderAllocation.id)
            .where(WorkOrderAllocation.work_order_demand_id == demand_id)
            .limit(1)
        )
        is not None
    )


def delete_work_order_demand(session: Session, work_order_id: int, demand_id: int) -> None:
    """Delete one saved demand line, blocked once quantity has released.

    The backend enforces the rules — never only the UI:

    - once any quantity for this demand has been released to
      production, deletion is refused (later adjustments go through
      correction and production workflows, PROJECT_PROFILE §16 —
      removal is not a correction mechanism);
    - the LAST demand line of a Work Order is never removable: a Work
      Order contains one or more Work Order Demand records
      (PROJECT_PROFILE §8.2), and removal never auto-deletes the Work
      Order.

    Removal deletes exactly the one demand row: the PartNumber master,
    QuantityFlows, PartMovements, release history, the demand's own
    audit history, and other demand lines for the same PN are
    untouched.

    Locking: the demand row lock serializes this check against a
    release in flight for the same demand (`release_to_production`
    locks the demand row for its whole transaction), so the
    released-quantity evidence cannot appear between the check and the
    DELETE; the parent Work Order row lock serializes sibling
    deletions, so two concurrent removals can never both pass the
    last-line check and leave a zero-demand Work Order. No audit row
    is appended: the Slice 1 audit vocabulary records creations and
    edits only (SLICE1_DATA_MODEL §16) and the demand's existing
    CREATED/UPDATED history remains — historical records never
    disappear.
    """
    demand = session.get(WorkOrderDemand, demand_id, with_for_update=True)
    if demand is None or demand.work_order_id != work_order_id:
        raise NotFoundError(
            f"Demand line {demand_id} does not exist on Work Order {work_order_id}."
        )
    work_order = session.get(WorkOrder, work_order_id, with_for_update=True, populate_existing=True)
    if work_order is None:  # pragma: no cover - the demand's FK guarantees the row
        raise NotFoundError(f"Work Order {work_order_id} does not exist.")
    _require_active(work_order, "Nothing was removed.")
    if demand.allocated_quantity > 0:
        raise ConflictError(
            "Cannot remove: stocked quantity has already been allocated to this demand line."
        )
    if _demand_has_allocation_history(session, demand.id):
        # Phase 10: a reversed allocation leaves no active allocation,
        # but its append-only rows still reference the line — that
        # audit history never disappears (the FK would refuse anyway;
        # this names the reason instead of failing at COMMIT).
        raise ConflictError(
            "Cannot remove: stocked quantity has been allocated to this demand line before"
            " and the allocation history stays with it."
        )
    if production_release.demand_has_released_quantity(session, demand.id):
        # GUI_DESIGN §11.2 wording — the UI disables the action, the
        # backend still refuses.
        raise ConflictError("Cannot remove: production quantity has already been released.")
    siblings_remaining = session.scalar(
        select(func.count())
        .select_from(WorkOrderDemand)
        .where(
            WorkOrderDemand.work_order_id == work_order_id,
            WorkOrderDemand.id != demand.id,
        )
    )
    if not siblings_remaining:
        raise ConflictError(
            "Cannot remove the last demand line: a Work Order always contains"
            " at least one Work Order Demand. Add the replacement line first,"
            " or leave the Work Order as it is."
        )
    session.delete(demand)
    commit(session, _WORK_ORDER_CONFLICTS)


# ---------------------------------------------------------------------------
# Completed Work Orders history (Phase 10 — GUI_DESIGN §11.5)
# ---------------------------------------------------------------------------

#: Rows one page of the completed history returns (the page appends
#: the next page through the keyset cursor). Bounded here, in the
#: query, because the history is unbounded by design.
COMPLETED_PAGE_LIMIT: Final = 50

DueOutcomeFilter = Literal["ALL", "ON_TIME", "LATE", "NO_DUE_DATE"]
DueOutcome = Literal["ON_TIME", "LATE", "NO_DUE_DATE"]
CompletedSort = Literal["DONE", "RECEIVED", "DUE", "NUMBER"]
SortDirection = Literal["ASC", "DESC"]
#: The Done range presets of the history page (GUI_DESIGN §11.5), resolved
#: HERE on the site's current date — the browser's clock and time zone
#: never define the window.
DoneRangePreset = Literal["LAST_30_DAYS", "LAST_90_DAYS", "THIS_YEAR", "LAST_YEAR"]


# -- The site calendar: ONE rule for every date-versus-timestamp judgement --


def site_timezone() -> str:
    """The factory's IANA time zone (``SITE_TIMEZONE``) — the calendar in
    which a completion timestamp becomes a done DATE."""
    return get_settings().site_timezone


def _now() -> datetime.datetime:
    """The current instant (a seam the history tests pin)."""
    return datetime.datetime.now(datetime.UTC)


def site_today() -> datetime.date:
    """Today on the site calendar — the anchor of every Done range preset."""
    return _now().astimezone(ZoneInfo(site_timezone())).date()


def done_range_bounds(
    preset: DoneRangePreset,
) -> tuple[datetime.date | None, datetime.date | None]:
    """The inclusive done-DATE bounds (``None`` = open) a preset stands for
    on the site calendar today: the "last N days" presets reach back N
    calendar days with no upper bound, the year presets are calendar
    years of the site's current date."""
    today = site_today()
    if preset == "LAST_30_DAYS":
        return today - datetime.timedelta(days=30), None
    if preset == "LAST_90_DAYS":
        return today - datetime.timedelta(days=90), None
    if preset == "THIS_YEAR":
        return datetime.date(today.year, 1, 1), None
    if preset == "LAST_YEAR":
        return datetime.date(today.year - 1, 1, 1), datetime.date(today.year - 1, 12, 31)
    raise InvalidInputError(
        "done_range must be LAST_30_DAYS, LAST_90_DAYS, THIS_YEAR or LAST_YEAR."
    )


def done_date_of(completed_at: datetime.datetime | None) -> datetime.date | None:
    """The done date of a completed Work Order: ``completed_at`` in the
    site calendar. The Python twin of ``_done_date_sql`` — the two must
    agree, which the history test asserts around a day boundary."""
    if completed_at is None:
        return None
    return completed_at.astimezone(ZoneInfo(site_timezone())).date()


def _done_date_sql() -> ColumnElement[datetime.date]:
    """``date(completed_at AT TIME ZONE site)`` — the same rule in SQL, so
    the due-outcome FILTER judges exactly what the row DISPLAYS."""
    return func.date(func.timezone(site_timezone(), WorkOrder.completed_at))


class DueOutcomeOf(NamedTuple):
    outcome: DueOutcome
    # Calendar days between the due date and the done date (LATE only).
    days_late: int | None


def due_outcome_of(work_order: WorkOrder) -> DueOutcomeOf | None:
    """Done date versus due date of a COMPLETED Work Order (None while active)."""
    done = done_date_of(work_order.completed_at)
    if done is None:
        return None
    if work_order.due_date is None:
        return DueOutcomeOf("NO_DUE_DATE", None)
    if done > work_order.due_date:
        return DueOutcomeOf("LATE", (done - work_order.due_date).days)
    return DueOutcomeOf("ON_TIME", None)


def _site_day_start(day: datetime.date) -> datetime.datetime:
    """Midnight of a site-calendar date as the instant the index compares."""
    return datetime.datetime.combine(day, datetime.time.min, tzinfo=ZoneInfo(site_timezone()))


# -- Keyset paging over the chosen sort ---------------------------------------


class CompletedCursor(NamedTuple):
    """The keyset position: the sort value of the last row (None when
    that value is NULL — an internal number, no due date) and its id.
    Only meaningful for the sort it was issued for: the token carries
    the sort and direction, and a mismatch is refused."""

    sort: CompletedSort
    direction: SortDirection
    value: str | None
    work_order_id: int


def encode_completed_cursor(cursor: CompletedCursor) -> str:
    raw = json.dumps(
        {"s": cursor.sort, "d": cursor.direction, "v": cursor.value, "i": cursor.work_order_id},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_completed_cursor(token: str) -> CompletedCursor:
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        sort, direction, value, work_order_id = data["s"], data["d"], data["v"], data["i"]
        if (
            sort not in ("DONE", "RECEIVED", "DUE", "NUMBER")
            or direction not in ("ASC", "DESC")
            or (value is not None and not isinstance(value, str))
            or not isinstance(work_order_id, int)
            or isinstance(work_order_id, bool)
        ):
            raise ValueError("cursor fields")
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidInputError("The paging cursor is not valid. Reload the history.") from exc
    return CompletedCursor(sort, direction, value, work_order_id)


class CompletedPage(NamedTuple):
    work_orders: list[WorkOrderSummary]
    # Matching rows in the whole history for the same filters — the
    # "Showing n of N" summary line.
    total: int
    # Completed Work Orders in the whole history regardless of the
    # filters: lets the page tell "none ever" from "none in this range"
    # (GUI_DESIGN §11.5) without a second request.
    history_total: int
    # The opaque cursor of the last row — present exactly when a further
    # row exists (the query looks one row past the page).
    next_cursor: str | None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sort_column(sort: CompletedSort) -> InstrumentedAttribute[Any]:
    if sort == "DONE":
        return WorkOrder.completed_at
    if sort == "RECEIVED":
        return WorkOrder.received_date
    if sort == "DUE":
        return WorkOrder.due_date
    return WorkOrder.work_order_number


def _cursor_value(sort: CompletedSort, work_order: WorkOrder) -> str | None:
    if sort == "DONE":
        assert work_order.completed_at is not None  # filtered on IS NOT NULL
        return work_order.completed_at.isoformat()
    if sort == "RECEIVED":
        return work_order.received_date.isoformat()
    if sort == "DUE":
        return work_order.due_date.isoformat() if work_order.due_date is not None else None
    return work_order.work_order_number


def _typed_cursor_value(sort: CompletedSort, value: str) -> Any:
    try:
        if sort == "DONE":
            return datetime.datetime.fromisoformat(value)
        if sort in ("RECEIVED", "DUE"):
            return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInputError("The paging cursor is not valid. Reload the history.") from exc
    return value


def _keyset_after(
    column: InstrumentedAttribute[Any], direction: SortDirection, cursor: CompletedCursor
) -> ColumnElement[bool]:
    """Rows strictly after the cursor in the order ``(column, id)`` with
    NULL values of the column LAST in either direction (``—`` rows sit
    at the end whichever way the column is sorted) and the id
    tie-breaker following the direction."""
    later_id = (
        WorkOrder.id > cursor.work_order_id
        if direction == "ASC"
        else WorkOrder.id < cursor.work_order_id
    )
    if cursor.value is None:
        # Inside the trailing NULL block: only the id decides.
        return and_(column.is_(None), later_id)
    value = _typed_cursor_value(cursor.sort, cursor.value)
    strictly_after = column > value if direction == "ASC" else column < value
    return or_(
        and_(column.is_not(None), strictly_after),
        and_(column == value, later_id),
        column.is_(None),
    )


def list_completed_work_orders(
    session: Session,
    *,
    search: str | None = None,
    done_range: DoneRangePreset | None = None,
    done_from: datetime.date | None = None,
    done_to: datetime.date | None = None,
    due_outcome: DueOutcomeFilter = "ALL",
    sort: CompletedSort = "DONE",
    direction: SortDirection = "DESC",
    cursor: str | None = None,
    limit: int = COMPLETED_PAGE_LIMIT,
) -> CompletedPage:
    """The completed history — search, filter, sort and page server-side.

    ``search`` matches the Work Order Number, any demand line's PN or
    any Job Number (case-insensitive contains); the Done range is either
    a ``done_range`` preset — resolved here on the site's current date
    (``done_range_bounds``) — or explicit ``done_from`` / ``done_to``
    inclusive done DATES in the site calendar, never both; the due
    outcome compares the done date (same calendar) with the Work Order
    due date. The order is the chosen column then the id (NULLs last
    in either direction) — Done descending by default — and the page
    is a keyset continuation over exactly that order, never an offset
    over an unbounded table; the cursor is bound to the sort it was
    issued for and exists only while a further row does.
    """
    if limit <= 0 or limit > 200:
        raise InvalidInputError("The page size must be between 1 and 200.")
    if sort not in ("DONE", "RECEIVED", "DUE", "NUMBER"):
        raise InvalidInputError("sort must be DONE, RECEIVED, DUE or NUMBER.")
    if direction not in ("ASC", "DESC"):
        raise InvalidInputError("direction must be ASC or DESC.")
    if done_range is not None:
        if done_from is not None or done_to is not None:
            raise InvalidInputError(
                "Use either a done_range preset or explicit done_from / done_to dates."
            )
        done_from, done_to = done_range_bounds(done_range)
    filters: list[ColumnElement[bool]] = [WorkOrder.completed_at.is_not(None)]
    if search is not None and search.strip():
        pattern = f"%{_escape_like(search.strip())}%"
        matching_demand = (
            select(WorkOrderDemand.id)
            .where(WorkOrderDemand.work_order_id == WorkOrder.id)
            .where(
                WorkOrderDemand.part_number.ilike(pattern, escape="\\")
                | func.array_to_string(WorkOrderDemand.job_numbers, "\n").ilike(
                    pattern, escape="\\"
                )
            )
            .correlate(WorkOrder)
            .exists()
        )
        filters.append(WorkOrder.work_order_number.ilike(pattern, escape="\\") | matching_demand)
    # The date bounds become instants (site midnight) so the
    # `(completed_at, id)` index still serves the range.
    if done_from is not None:
        filters.append(WorkOrder.completed_at >= _site_day_start(done_from))
    if done_to is not None:
        filters.append(
            WorkOrder.completed_at < _site_day_start(done_to + datetime.timedelta(days=1))
        )
    done_date = _done_date_sql()
    if due_outcome == "ON_TIME":
        filters.append(WorkOrder.due_date.is_not(None) & (done_date <= WorkOrder.due_date))
    elif due_outcome == "LATE":
        filters.append(WorkOrder.due_date.is_not(None) & (done_date > WorkOrder.due_date))
    elif due_outcome == "NO_DUE_DATE":
        filters.append(WorkOrder.due_date.is_(None))
    elif due_outcome != "ALL":
        raise InvalidInputError("due_outcome must be ALL, ON_TIME, LATE or NO_DUE_DATE.")

    total = session.scalar(select(func.count()).select_from(WorkOrder).where(*filters)) or 0
    history_total = (
        session.scalar(
            select(func.count()).select_from(WorkOrder).where(WorkOrder.completed_at.is_not(None))
        )
        or 0
    )

    column = _sort_column(sort)
    ordering = (
        [column.asc().nulls_last(), WorkOrder.id.asc()]
        if direction == "ASC"
        else [column.desc().nulls_last(), WorkOrder.id.desc()]
    )
    part_numbers = func.array_agg(
        aggregate_order_by(WorkOrderDemand.part_number, WorkOrderDemand.id)
    )
    # One row beyond the page is fetched only to learn whether a further
    # page exists: a cursor is issued exactly when it does, so a history
    # that ends on the page boundary never offers an empty `Show more`.
    query = (
        select(WorkOrder, func.count(WorkOrderDemand.id), part_numbers)
        .outerjoin(WorkOrderDemand, WorkOrderDemand.work_order_id == WorkOrder.id)
        .where(*filters)
        .group_by(WorkOrder.id)
        .order_by(*ordering)
        .limit(limit + 1)
    )
    if cursor is not None:
        position = decode_completed_cursor(cursor)
        if position.sort != sort or position.direction != direction:
            raise InvalidInputError(
                "The paging cursor belongs to another sort order. Reload the history."
            )
        query = query.where(_keyset_after(column, direction, position))
    fetched = list(session.execute(query))
    has_more = len(fetched) > limit
    rows = fetched[:limit]
    summaries = [
        WorkOrderSummary(
            work_order=work_order,
            demand_line_count=count,
            part_numbers=[value for value in (values or []) if value is not None],
            status=WorkOrderStatus.COMPLETED,
        )
        for work_order, count, values in rows
    ]
    next_cursor: str | None = None
    if has_more:
        last = rows[-1][0]
        next_cursor = encode_completed_cursor(
            CompletedCursor(sort, direction, _cursor_value(sort, last), last.id)
        )
    return CompletedPage(
        work_orders=summaries,
        total=int(total),
        history_total=int(history_total),
        next_cursor=next_cursor,
    )
