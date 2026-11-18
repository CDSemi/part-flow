"""Permanent deletion of test Work Orders and Part Numbers (interim operator command).

``python -m app.cli delete-permanently`` (OPERATIONS_RUNBOOK §10) deletes
Work Orders and Part Numbers that the owner entered only for testing, as
if they had never been entered, and keeps exactly one new
``audit_events`` row (``DELETED``, metadata key ``permanent_deletion``)
per deleted Work Order and per deleted Part Number. It is an interim
tool until the in-app delete of the history archival and purge design
exists. It runs as the database owner role only: it disables the
append-only guard triggers of the tables it deletes from inside its own
transaction, which the application database role can never do, and it
changes no grant, trigger definition or function.

What is deleted
---------------

- A Work Order: its row, its demand lines, every allocation row of those
  lines, every Quantity Flow released for those lines (a ``RECEIVED``
  Movement names the line in ``metadata.context.work_order_demand_id``)
  together with every flow linked to them through lineage (split or
  combine), every Movement and lineage edge of those flows, the Assigned
  Route snapshots of those flows with their steps, and the audit rows
  that concern only these records.
- A Part Number: its master row (details and image), every flow of the
  PN with the same closure, and every demand line and allocation of the
  PN — so every Work Order with a line of the PN must be deleted in the
  same command. An internal Work Order (no Work Order Number, created by
  a Scan Station receipt) whose every demand line is of a Part Number
  being deleted is deleted with it; it cannot be named otherwise.

What makes it refuse (nothing is deleted; each link is named)
--------------------------------------------------------------

An unknown Work Order Number or Part Number; a demand line of a Part
Number being deleted on a Work Order that stays; a demand line on the
Hot list (deleting it would leave a gap in the ranks of lines that
stay); an allocation command or allocation reversal that also concerns a
demand line that stays; a lineage link between a flow to delete and a
flow that stays (released for a Work Order that stays, or quantity added
without a Work Order of a Part Number that stays); a Movement command or
reversal that also concerns a flow that stays; an Assigned Route
snapshot whose steps a staying Movement names; a Movement that stays and
names a deleted demand line or flow in its metadata; stocked
quantity of the flows to delete that the allocations of the Work Orders
that stay still need (``projections.stocked_quantity_of`` and
``allocations.active_allocated_quantity_of`` before and after); and a
flow whose history a Movement-history archival run recorded
(``history_archived_flows``: archived history is never deleted here).

Audit rows
----------

An audit row is deleted only when its entity is a deleted record (a
Work Order, a demand line — current, or removed earlier from a deleted
Work Order —, a Part Number master or an Assigned Route), every Work
Order, demand line, flow and Assigned Route its data names is deleted
too, and every other audit row of the same command (the same
``device_event_id``, or the same Hot list removal) is deleted as well.
Every other audit row that names deleted data is kept unchanged and
listed as ``kept_audit_rows`` ("kept: also concerns other records"). No
read model derives state of remaining data from these rows: the done
date replay reads only ``completion`` rows of existing Work Orders, the
Hot list command replays its own command only, and the audit trail is
scoped by existing demand lines.

Execution
---------

A preview runs in one ``REPEATABLE READ, READ ONLY`` transaction and
never writes. A deletion runs in ONE ``READ COMMITTED`` transaction: the
lock timeout, the owner check, ``LOCK TABLE … IN EXCLUSIVE MODE`` of the
touched tables in one fixed order (readers continue, writers wait), the
plan again under the locks (a refusal rolls back), ``ALTER TABLE …
DISABLE TRIGGER`` of exactly the guard triggers of the tables it deletes
from, the deletes in dependency order (each must delete exactly the
planned rows), ``ENABLE TRIGGER`` of the same triggers, the new audit
rows, a check that every guard trigger is enabled, and COMMIT. No other
session can ever see a disabled guard (catalog changes are
transactional, and the table locks are held until COMMIT);
``session_replication_role`` is never used. Any failure before COMMIT
rolls everything back; a failure during COMMIT is ``outcome_unknown``.
"""

import contextlib
import datetime
import json
import uuid
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import psycopg.errors
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application import allocations, audit, projections
from app.application.migration import RunError, is_connection_failure
from app.application.part_numbers import canonical_part_number, master_snapshot
from app.application.work_orders import work_order_snapshot
from app.domain.enums import AuditEntityType, AuditEventType, MovementType
from app.infrastructure.database_privileges import GUARD_TRIGGERS
from app.infrastructure.models import PartNumber, WorkOrder

REPORT_VERSION: Final = 1
COMMAND: Final = "delete-permanently"
#: Metadata key of the one audit row per deleted Work Order and Part Number.
AUDIT_KEY: Final = "permanent_deletion"

#: The tables the command deletes from, in delete (dependency) order.
DELETE_ORDER: Final = (
    "audit_events",
    "part_movements",
    "quantity_flow_lineage",
    "quantity_flows",
    "assigned_route_steps",
    "assigned_routes",
    "work_order_allocations",
    "work_order_demands",
    "work_orders",
    "part_numbers",
)
#: The same tables in the one fixed lock order (alphabetical).
LOCK_ORDER: Final = tuple(sorted(DELETE_ORDER))

DeletionResult = Literal["preview", "deleted", "refused", "failed", "outcome_unknown"]

RUN_MESSAGES: Final = {
    "database_unavailable": "PartFlow could not reach its database. Nothing was deleted.",
    "lock_not_available": (
        "delete-permanently waited too long for a table lock (PartFlow is busy writing)."
        " Nothing was deleted. Run it again at a quiet moment."
    ),
    "internal_error": (
        "delete-permanently failed unexpectedly (internal error). Nothing was deleted."
    ),
    "outcome_unknown": (
        "The database connection failed while committing: the deletion may or may not have"
        " been stored. Run the preview again to see which records still exist."
    ),
}


# ---------------------------------------------------------------------------
# Request, plan and report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeletionRequest:
    """What the operator asked for; values already validated by the CLI."""

    work_order_numbers: tuple[str, ...]
    #: Canonical Part Numbers (``part_numbers.canonical_part_number``).
    part_numbers: tuple[str, ...]
    operator: str
    reason: str
    confirm: bool = False
    #: The backup taken before the deletion (``pf`` records it), if any.
    backup_name: str | None = None


@dataclass(frozen=True)
class Refusal:
    code: str
    message: str


@dataclass(frozen=True)
class _WorkOrderEntry:
    id: int
    number: str | None
    named: bool

    @property
    def label(self) -> str:
        return _work_order_label(self.id, self.number)


@dataclass
class Plan:
    """Everything one run would delete, what it keeps, and why it refuses."""

    work_orders: list[dict[str, object]] = field(default_factory=list)
    part_numbers: list[dict[str, object]] = field(default_factory=list)
    ids: dict[str, list[Any]] = field(default_factory=lambda: {t: [] for t in DELETE_ORDER})
    kept_audit_rows: list[dict[str, object]] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {table: len(self.ids[table]) for table in DELETE_ORDER}


@dataclass
class DeletionReport:
    started_at: datetime.datetime
    request: DeletionRequest
    result: DeletionResult = "failed"
    finished_at: datetime.datetime | None = None
    plan: Plan | None = None
    audit_rows: list[dict[str, object]] = field(default_factory=list)
    guard_triggers: list[str] = field(default_factory=list)
    error: RunError | None = None

    @property
    def exit_code(self) -> int:
        if self.result in ("preview", "deleted"):
            return 0
        return 1 if self.result == "refused" else 2


class _RefusedError(Exception):
    def __init__(self, refusals: list[Refusal]) -> None:
        super().__init__(refusals[0].code)
        self.refusals = refusals


def requested_part_number(value: object) -> str:
    """A requested Part Number in canonical form, by PartFlow's one normalization
    (``InvalidInputError`` for a value that can never be a PN)."""
    return canonical_part_number(value)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _iso(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _work_order_label(work_order_id: int, number: str | None) -> str:
    return (
        f"Work Order {number!r}" if number is not None else f"internal Work Order {work_order_id}"
    )


# ---------------------------------------------------------------------------
# Small SQL helpers
# ---------------------------------------------------------------------------


def _rows(session: Session, sql: str, **params: object) -> list[Any]:
    return list(session.execute(text(sql), params))


def _texts(values: Iterable[object]) -> list[str]:
    return sorted({str(value) for value in values})


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def _resolve_work_orders(
    session: Session, request: DeletionRequest, refusals: list[Refusal]
) -> dict[int, _WorkOrderEntry]:
    numbers = list(dict.fromkeys(request.work_order_numbers))
    found: dict[str, int] = {
        str(row.work_order_number): int(row.id)
        for row in _rows(
            session,
            "SELECT id, work_order_number FROM work_orders WHERE work_order_number = ANY(:numbers)",
            numbers=numbers,
        )
    }
    entries: dict[int, _WorkOrderEntry] = {}
    for number in numbers:
        if number not in found:
            refusals.append(
                Refusal(
                    "unknown_work_order",
                    f"Work Order {number!r} does not exist (the number is matched exactly, as"
                    " PartFlow stores it).",
                )
            )
            continue
        entries[found[number]] = _WorkOrderEntry(found[number], number, named=True)
    return entries


def _resolve_part_numbers(
    session: Session, request: DeletionRequest, refusals: list[Refusal]
) -> tuple[list[str], set[str]]:
    """The requested PNs that exist (master or any data), and those with a master row."""
    wanted = list(dict.fromkeys(request.part_numbers))
    if not wanted:
        return [], set()
    masters = {
        str(row.part_number)
        for row in _rows(
            session,
            "SELECT part_number FROM part_numbers WHERE part_number = ANY(:pns)",
            pns=wanted,
        )
    }
    used = {
        str(row.part_number)
        for row in _rows(
            session,
            "SELECT part_number FROM work_order_demands WHERE part_number = ANY(:pns)"
            " UNION SELECT part_number FROM quantity_flows WHERE part_number = ANY(:pns)"
            " UNION SELECT part_number FROM work_order_allocations WHERE part_number = ANY(:pns)",
            pns=wanted,
        )
    }
    existing: list[str] = []
    for part_number in wanted:
        if part_number in masters or part_number in used:
            existing.append(part_number)
        else:
            refusals.append(
                Refusal(
                    "unknown_part_number",
                    f"Part Number {part_number!r} has no record and no data in PartFlow.",
                )
            )
    return existing, masters


def _add_part_number_work_orders(
    session: Session,
    part_numbers: Sequence[str],
    entries: dict[int, _WorkOrderEntry],
    refusals: list[Refusal],
) -> None:
    """Every Work Order with a line of a deleted PN: deleted too, or a refusal.

    An internal Work Order whose every line is of a deleted PN is added;
    any other Work Order must have been named.
    """
    if not part_numbers:
        return
    lines = _rows(
        session,
        "SELECT d.id, d.part_number, w.id AS work_order_id, w.work_order_number"
        " FROM work_order_demands d JOIN work_orders w ON w.id = d.work_order_id"
        " WHERE d.part_number = ANY(:pns) ORDER BY w.id, d.id",
        pns=list(part_numbers),
    )
    candidates = {int(row.work_order_id) for row in lines} - set(entries)
    if not candidates:
        return
    all_lines: dict[int, set[str]] = defaultdict(set)
    for row in _rows(
        session,
        "SELECT work_order_id, part_number FROM work_order_demands WHERE work_order_id = ANY(:ids)",
        ids=sorted(candidates),
    ):
        all_lines[int(row.work_order_id)].add(str(row.part_number))
    deleted = set(part_numbers)
    for row in lines:
        work_order_id = int(row.work_order_id)
        if work_order_id not in candidates:
            continue
        number = row.work_order_number
        if number is None and all_lines[work_order_id] <= deleted:
            entries.setdefault(work_order_id, _WorkOrderEntry(work_order_id, None, named=False))
            continue
        label = _work_order_label(work_order_id, number)
        refusals.append(
            Refusal(
                "part_number_used_by_work_order",
                f"Part Number {row.part_number!r} is used by demand line {int(row.id)} of"
                f" {label}, which is not being deleted. Delete that Work Order in the same"
                " command, or keep the Part Number.",
            )
        )


def _lineage_closure(session: Session, seeds: set[int]) -> tuple[set[int], list[Any]]:
    """Every flow connected to ``seeds`` through any lineage edge (void ones too:
    the foreign keys hold both ends), and those edges."""
    flows = set(seeds)
    frontier = set(seeds)
    edges: dict[int, Any] = {}
    while frontier:
        found = _rows(
            session,
            "SELECT id, parent_flow_id, child_flow_id FROM quantity_flow_lineage"
            " WHERE parent_flow_id = ANY(:ids) OR child_flow_id = ANY(:ids)",
            ids=sorted(frontier),
        )
        frontier = set()
        for edge in found:
            edges[int(edge.id)] = edge
            for flow_id in (int(edge.parent_flow_id), int(edge.child_flow_id)):
                if flow_id not in flows:
                    flows.add(flow_id)
                    frontier.add(flow_id)
    return flows, sorted(edges.values(), key=lambda edge: int(edge.id))


def _demand_owners(session: Session, demand_ids: Collection[int]) -> dict[int, str]:
    """A label per demand line: the line and its Work Order."""
    if not demand_ids:
        return {}
    return {
        int(row.id): f"demand line {int(row.id)} of {_work_order_label(row.wid, row.number)}"
        for row in _rows(
            session,
            "SELECT d.id, w.id AS wid, w.work_order_number AS number FROM work_order_demands d"
            " JOIN work_orders w ON w.id = d.work_order_id WHERE d.id = ANY(:ids)",
            ids=sorted(demand_ids),
        )
    }


def _flow_labels(session: Session, flow_ids: Collection[int]) -> dict[int, str]:
    """A label per flow: its PN and what introduced its quantity."""
    if not flow_ids:
        return {}
    received: dict[int, int] = {}
    for row in _rows(
        session,
        "SELECT quantity_flow_id, (metadata['context'] ->> 'work_order_demand_id')::integer"
        " AS demand_id FROM part_movements WHERE movement_type = :received"
        " AND quantity_flow_id = ANY(:ids)",
        received=MovementType.RECEIVED,
        ids=sorted(flow_ids),
    ):
        if row.demand_id is not None:
            received[int(row.quantity_flow_id)] = int(row.demand_id)
    owners = _demand_owners(session, set(received.values()))
    labels: dict[int, str] = {}
    for row in _rows(
        session,
        "SELECT id, part_number FROM quantity_flows WHERE id = ANY(:ids)",
        ids=sorted(flow_ids),
    ):
        flow_id = int(row.id)
        label = f"Quantity Flow {flow_id} (Part Number {row.part_number!r}"
        if flow_id in received:
            label += f", released for {owners.get(received[flow_id], 'a demand line')}"
        labels[flow_id] = label + ")"
    return labels


def _check_archived_history(session: Session, flow_ids: set[int], refusals: list[Refusal]) -> None:
    """A flow whose history an archival run recorded keeps that record (and so its row)."""
    for row in _rows(
        session,
        "SELECT flow_id, min(run_id) AS run_id FROM history_archived_flows"
        " WHERE flow_id = ANY(:ids) GROUP BY flow_id ORDER BY flow_id",
        ids=sorted(flow_ids),
    ):
        refusals.append(
            Refusal(
                "archived_history",
                f"The history of Quantity Flow {int(row.flow_id)} was archived by archival run"
                f" {int(row.run_id)}; this command never deletes archived history.",
            )
        )


def _check_flow_components(
    session: Session,
    flow_ids: set[int],
    seeds: set[int],
    demand_ids: set[int],
    part_numbers: set[str],
    refusals: list[Refusal],
) -> None:
    """Every flow of the closure must belong to the data being deleted."""
    extra = flow_ids - seeds
    if not extra:
        return
    flows = {
        int(row.id): row
        for row in _rows(
            session,
            "SELECT f.id, f.part_number,"
            " (SELECT (m.metadata['context'] ->> 'work_order_demand_id')::integer"
            "  FROM part_movements m WHERE m.quantity_flow_id = f.id"
            "  AND m.movement_type = :received ORDER BY m.id LIMIT 1) AS demand_id,"
            " EXISTS (SELECT 1 FROM quantity_flow_lineage l WHERE l.child_flow_id = f.id)"
            "  AS is_child"
            " FROM quantity_flows f WHERE f.id = ANY(:ids)",
            received=MovementType.RECEIVED,
            ids=sorted(extra),
        )
    }
    foreign: list[int] = []
    for flow_id in sorted(extra):
        row = flows[flow_id]
        if str(row.part_number) in part_numbers:
            continue
        if row.demand_id is not None:
            if int(row.demand_id) not in demand_ids:
                foreign.append(flow_id)
        elif not row.is_child:
            foreign.append(flow_id)
    if not foreign:
        return
    labels = _flow_labels(session, foreign)
    for flow_id in foreign:
        label = labels[flow_id]
        if flows[flow_id].demand_id is None:
            label = label[:-1] + ", quantity added without a Work Order)"
        refusals.append(
            Refusal(
                "lineage_link",
                f"{label} stays, but a split or combine links it to the flows to delete.",
            )
        )


def _check_movement_links(
    session: Session,
    flow_ids: set[int],
    movements: Sequence[Any],
    refusals: list[Refusal],
) -> None:
    """Commands and reversals must not span a flow that stays."""
    movement_ids = [int(row.id) for row in movements]
    events = _texts(row.device_event_id for row in movements)
    staying_flows: dict[int, set[str]] = defaultdict(set)
    for row in _rows(
        session,
        "SELECT DISTINCT device_event_id, quantity_flow_id FROM part_movements"
        " WHERE device_event_id = ANY(:events) AND quantity_flow_id <> ALL(:flows)",
        events=events,
        flows=sorted(flow_ids),
    ):
        staying_flows[int(row.quantity_flow_id)].add(str(row.device_event_id))
    reversing = _rows(
        session,
        "SELECT id, quantity_flow_id, reverses_movement_id FROM part_movements"
        " WHERE reverses_movement_id = ANY(:ids) AND quantity_flow_id <> ALL(:flows)",
        ids=movement_ids,
        flows=sorted(flow_ids),
    )
    deleted = set(movement_ids)
    reversed_outside = [
        row
        for row in movements
        if row.reverses_movement_id is not None and int(row.reverses_movement_id) not in deleted
    ]
    outside_targets = {
        int(row.id): int(row.quantity_flow_id)
        for row in _rows(
            session,
            "SELECT id, quantity_flow_id FROM part_movements WHERE id = ANY(:ids)",
            ids=sorted({int(row.reverses_movement_id) for row in reversed_outside}),
        )
    }
    labels = _flow_labels(
        session,
        set(staying_flows)
        | {int(row.quantity_flow_id) for row in reversing}
        | set(outside_targets.values()),
    )
    for flow_id, commands in sorted(staying_flows.items()):
        refusals.append(
            Refusal(
                "movement_command_shared",
                f"{labels[flow_id]} stays, but the same command"
                f" ({', '.join(sorted(commands))}) also moved the flows to delete.",
            )
        )
    for row in reversing:
        refusals.append(
            Refusal(
                "movement_reversal_link",
                f"Movement {int(row.id)} of {labels[int(row.quantity_flow_id)]} stays, but it"
                f" undoes Movement {int(row.reverses_movement_id)} of the flows to delete.",
            )
        )
    for row in reversed_outside:
        target = int(row.reverses_movement_id)
        refusals.append(
            Refusal(
                "movement_reversal_link",
                f"Movement {int(row.id)} of the flows to delete undoes Movement {target} of"
                f" {labels[outside_targets[target]]}, which stays.",
            )
        )


def _check_movement_metadata(
    session: Session,
    flow_ids: set[int],
    demand_ids: set[int],
    part_numbers: Collection[str],
    refusals: list[Refusal],
) -> None:
    """A Movement that stays must not name a deleted demand line or flow."""
    if not part_numbers:
        return
    rows = _rows(
        session,
        "SELECT m.id, m.quantity_flow_id FROM part_movements m"
        " WHERE m.part_number = ANY(:pns) AND m.quantity_flow_id <> ALL(:flows) AND ("
        " EXISTS (SELECT 1 FROM jsonb_path_query(m.metadata,"
        "  'lax $.**.work_order_demand_id') v WHERE v #>> '{}' = ANY(:demands))"
        " OR EXISTS (SELECT 1 FROM jsonb_path_query(m.metadata,"
        "  'lax $.**.quantity_flow_id') v WHERE v #>> '{}' = ANY(:flow_texts))"
        " OR EXISTS (SELECT 1 FROM jsonb_path_query(m.metadata,"
        "  'lax $.**.quantity_flow_ids[*]') v WHERE v #>> '{}' = ANY(:flow_texts)))"
        " ORDER BY m.id",
        pns=sorted(part_numbers),
        flows=sorted(flow_ids),
        demands=_texts(demand_ids),
        flow_texts=_texts(flow_ids),
    )
    labels = _flow_labels(session, {int(row.quantity_flow_id) for row in rows})
    for row in rows:
        refusals.append(
            Refusal(
                "movement_references_deleted_data",
                f"Movement {int(row.id)} of {labels[int(row.quantity_flow_id)]} stays, but it"
                " names a demand line or flow to delete.",
            )
        )


def _check_routes(
    session: Session,
    flow_ids: set[int],
    route_ids: Sequence[int],
    step_ids: Sequence[int],
    refusals: list[Refusal],
) -> None:
    """A route snapshot to delete must not be used by a flow or Movement that stays."""
    flows = _rows(
        session,
        "SELECT id, assigned_route_id FROM quantity_flows"
        " WHERE assigned_route_id = ANY(:routes) AND id <> ALL(:flows)",
        routes=list(route_ids),
        flows=sorted(flow_ids),
    )
    movements = _rows(
        session,
        "SELECT m.id, m.quantity_flow_id, s.assigned_route_id FROM part_movements m"
        " JOIN assigned_route_steps s ON s.id = m.assigned_route_step_id"
        " WHERE m.assigned_route_step_id = ANY(:steps) AND m.quantity_flow_id <> ALL(:flows)",
        steps=list(step_ids),
        flows=sorted(flow_ids),
    )
    labels = _flow_labels(
        session, {int(row.id) for row in flows} | {int(row.quantity_flow_id) for row in movements}
    )
    for row in flows:
        refusals.append(
            Refusal(
                "route_snapshot_shared",
                f"{labels[int(row.id)]} stays, but it follows Assigned Route"
                f" {int(row.assigned_route_id)} of the flows to delete.",
            )
        )
    for row in movements:
        refusals.append(
            Refusal(
                "route_snapshot_shared",
                f"Movement {int(row.id)} of {labels[int(row.quantity_flow_id)]} stays, but it"
                f" names a step of Assigned Route {int(row.assigned_route_id)} of the flows to"
                " delete.",
            )
        )


def _check_allocations(
    session: Session,
    demand_ids: set[int],
    rows: Sequence[Any],
    refusals: list[Refusal],
) -> None:
    """Allocation commands and reversals must not span a demand line that stays."""
    events = _texts(row.device_event_id for row in rows)
    shared = _rows(
        session,
        "SELECT DISTINCT device_event_id, work_order_demand_id FROM work_order_allocations"
        " WHERE device_event_id = ANY(:events) AND work_order_demand_id <> ALL(:demands)",
        events=events,
        demands=sorted(demand_ids),
    )
    allocation_ids = [int(row.id) for row in rows]
    deleted = set(allocation_ids)
    reversing = _rows(
        session,
        "SELECT id, work_order_demand_id FROM work_order_allocations"
        " WHERE reverses_allocation_id = ANY(:ids) AND work_order_demand_id <> ALL(:demands)",
        ids=allocation_ids,
        demands=sorted(demand_ids),
    )
    outside = [
        row
        for row in rows
        if row.reverses_allocation_id is not None and int(row.reverses_allocation_id) not in deleted
    ]
    owners = _demand_owners(
        session,
        {int(row.work_order_demand_id) for row in shared}
        | {int(row.work_order_demand_id) for row in reversing},
    )
    for row in shared:
        refusals.append(
            Refusal(
                "allocation_command_shared",
                f"Allocation command {row.device_event_id} also allocated to"
                f" {owners[int(row.work_order_demand_id)]}, which stays.",
            )
        )
    for row in reversing:
        refusals.append(
            Refusal(
                "allocation_reversal_link",
                f"Allocation row {int(row.id)} of {owners[int(row.work_order_demand_id)]}"
                " stays, but it reverses an allocation to delete.",
            )
        )
    for row in outside:
        refusals.append(
            Refusal(
                "allocation_reversal_link",
                f"Allocation row {int(row.id)} to delete reverses allocation row"
                f" {int(row.reverses_allocation_id)}, which stays.",
            )
        )


def _check_stock(
    session: Session,
    movements: Sequence[Any],
    allocation_rows: Sequence[Any],
    demand_part_numbers: Mapping[int, str],
    deleted_part_numbers: set[str],
    refusals: list[Refusal],
) -> None:
    """The stocked quantity left after the deletion must still cover the
    allocations of the Work Orders that stay (PROJECT_PROFILE §8.12)."""
    stocked_removed: dict[str, int] = defaultdict(int)
    stocked_ids = [int(row.id) for row in movements if row.movement_type == MovementType.STOCKED]
    reversed_ids = {
        int(row.reverses_movement_id)
        for row in _rows(
            session,
            "SELECT reverses_movement_id FROM part_movements"
            " WHERE reverses_movement_id = ANY(:ids)",
            ids=stocked_ids,
        )
    }
    for row in movements:
        if row.movement_type == MovementType.STOCKED and int(row.id) not in reversed_ids:
            stocked_removed[str(row.part_number)] += int(row.quantity)
    by_part_number: dict[str, list[int]] = defaultdict(list)
    for demand_id, part_number in demand_part_numbers.items():
        by_part_number[part_number].append(demand_id)
    for part_number in sorted(set(stocked_removed) | {str(r.part_number) for r in allocation_rows}):
        if part_number in deleted_part_numbers or stocked_removed.get(part_number, 0) == 0:
            continue
        removed_allocation = sum(
            allocations.active_allocations_by_demand(
                session, by_part_number.get(part_number, [])
            ).values()
        )
        stocked_after = (
            projections.stocked_quantity_of(session, part_number) - stocked_removed[part_number]
        )
        allocated_after = (
            allocations.active_allocated_quantity_of(session, part_number) - removed_allocation
        )
        if allocated_after <= stocked_after:
            continue
        holders = [
            _work_order_label(int(row.id), row.work_order_number)
            for row in _rows(
                session,
                "SELECT DISTINCT w.id, w.work_order_number FROM work_order_allocations a"
                " JOIN work_order_demands d ON d.id = a.work_order_demand_id"
                " JOIN work_orders w ON w.id = d.work_order_id"
                " WHERE a.part_number = :pn AND a.reverses_allocation_id IS NULL"
                " AND NOT EXISTS (SELECT 1 FROM work_order_allocations r"
                "  WHERE r.reverses_allocation_id = a.id)"
                " AND a.work_order_demand_id <> ALL(:demands) ORDER BY w.id",
                pn=part_number,
                demands=sorted(demand_part_numbers),
            )
        ]
        refusals.append(
            Refusal(
                "stock_needed",
                f"Part Number {part_number!r}: the flows to delete hold"
                f" {stocked_removed[part_number]} stocked, and the Work Orders that stay"
                f" ({', '.join(holders) or 'none'}) have {allocated_after} allocated, but only"
                f" {stocked_after} would remain stocked. Reverse those allocations first, or"
                " delete those Work Orders in the same command.",
            )
        )


# -- audit rows ---------------------------------------------------------------

#: JSON keys that name a record, by kind.
_REFERENCE_KEYS: Final[Mapping[str, str]] = {
    "work_order_id": "work_order",
    "work_order_demand_id": "demand",
    "quantity_flow_id": "flow",
    "quantity_flow_ids": "flow",
    "assigned_route_id": "route",
    "part_number": "part_number",
}
_ENTITY_KINDS: Final[Mapping[str, str]] = {
    AuditEntityType.WORK_ORDER.value: "work_order",
    AuditEntityType.WORK_ORDER_DEMAND.value: "demand",
    AuditEntityType.PART_NUMBER.value: "part_number",
    AuditEntityType.ASSIGNED_ROUTE.value: "route",
}
#: Kinds whose reference to a record that stays keeps the row. A row that
#: names a staying Part Number by value (a demand line's PN) is not kept
#: for it: the PN is a value there, not a record.
_BLOCKING_KINDS: Final = frozenset({"work_order", "demand", "flow", "route"})


def _walk(value: object, found: dict[str, set[str]], events: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            kind = _REFERENCE_KEYS.get(str(key))
            if kind is not None:
                items = item if isinstance(item, list) else [item]
                for entry in items:
                    if isinstance(entry, (int, str)) and not isinstance(entry, bool):
                        found[kind].add(str(entry))
            if key == "device_event_id" and isinstance(item, str):
                events.add(item)
            _walk(item, found, events)
    elif isinstance(value, list):
        for item in value:
            _walk(item, found, events)


def _group_keys(row: Any, events: set[str]) -> set[str]:
    keys = {f"event:{event}" for event in events}
    block = (row.meta or {}).get("hot_list_change")
    if isinstance(block, dict) and "cause" in block:
        cause = json.dumps(block["cause"], sort_keys=True)
        keys.add(f"cause:{_iso(row.occurred_at)}:{cause}")
    return keys


_AUDIT_COLUMNS: Final = (
    "a.id, a.event_type, a.entity_type, a.entity_id, a.occurred_at, a.before_data,"
    " a.after_data, a.metadata AS meta"
)


def _reference_term(key: str, parameter: str) -> str:
    path = f"lax $.**.{key}[*]" if key.endswith("_ids") else f"lax $.**.{key}"
    return (
        "EXISTS (SELECT 1 FROM jsonb_path_query(jsonb_build_array(a.before_data, a.after_data,"
        f" a.metadata), '{path}') v WHERE v #>> '{{}}' = ANY(:{parameter}))"
    )


def _plan_audit_rows(session: Session, plan: Plan, deleted: Mapping[str, set[str]]) -> None:
    """Which audit rows go (only these records) and which stay (also others)."""
    parameters = {kind: sorted(values) for kind, values in deleted.items()}
    entity_terms = [
        f"(a.entity_type = '{entity}' AND a.entity_id = ANY(:{kind}))"
        for entity, kind in _ENTITY_KINDS.items()
    ]
    reference_terms = [_reference_term(key, kind) for key, kind in _REFERENCE_KEYS.items()]
    candidates = {
        int(row.id): row
        for row in _rows(
            session,
            f"SELECT {_AUDIT_COLUMNS} FROM audit_events a WHERE "
            + " OR ".join(entity_terms + reference_terms),
            **parameters,
        )
    }
    references: dict[int, dict[str, set[str]]] = {}
    groups: dict[int, set[str]] = {}

    def analyse(row: Any) -> None:
        found: dict[str, set[str]] = defaultdict(set)
        events: set[str] = set()
        for document in (row.before_data, row.after_data, row.meta):
            _walk(document, found, events)
        references[int(row.id)] = found
        groups[int(row.id)] = _group_keys(row, events)

    for row in candidates.values():
        analyse(row)
    events = sorted(
        {key[6:] for keys in groups.values() for key in keys if key.startswith("event:")}
    )
    stamps = sorted(
        {
            row.occurred_at
            for row in candidates.values()
            if any(key.startswith("cause:") for key in groups[int(row.id)])
        }
    )
    siblings: dict[int, Any] = {}
    if events or stamps:
        for row in _rows(
            session,
            f"SELECT {_AUDIT_COLUMNS} FROM audit_events a WHERE EXISTS (SELECT 1 FROM"
            " jsonb_path_query(a.metadata, 'lax $.**.device_event_id') v"
            " WHERE v #>> '{}' = ANY(:events))"
            " OR (a.entity_type = :demand_type AND a.occurred_at = ANY(:stamps))",
            events=events,
            stamps=stamps,
            demand_type=AuditEntityType.WORK_ORDER_DEMAND.value,
        ):
            if int(row.id) not in candidates:
                siblings[int(row.id)] = row
                analyse(row)

    def own_rule(row: Any) -> bool:
        kind = _ENTITY_KINDS.get(str(row.entity_type))
        if kind is None or str(row.entity_id) not in deleted[kind]:
            return False
        found = references[int(row.id)]
        return all(found[k] <= deleted[k] for k in _BLOCKING_KINDS if k in found)

    rows = {**candidates, **siblings}
    deletable = {row_id for row_id, row in rows.items() if own_rule(row)}
    members: dict[str, set[int]] = defaultdict(set)
    for row_id, keys in groups.items():
        for key in keys:
            members[key].add(row_id)
    changed = True
    while changed:
        changed = False
        for group in members.values():
            if group & deletable and not group <= deletable:
                deletable -= group
                changed = True
    plan.ids["audit_events"] = sorted(deletable)
    for row_id in sorted(set(candidates) - deletable):
        row = candidates[row_id]
        plan.kept_audit_rows.append(
            {
                "id": row_id,
                "entity_type": str(row.entity_type),
                "entity_id": str(row.entity_id),
                "event_type": str(row.event_type),
                "occurred_at": _iso(row.occurred_at),
                "kept": "also concerns other records",
            }
        )


def build_plan(session: Session, request: DeletionRequest) -> Plan:
    """Everything the request deletes, keeps and refuses (reads only)."""
    plan = Plan()
    refusals = plan.refusals
    entries = _resolve_work_orders(session, request, refusals)
    part_numbers, masters = _resolve_part_numbers(session, request, refusals)
    _add_part_number_work_orders(session, part_numbers, entries, refusals)
    deleted_pns = set(part_numbers)

    work_order_ids = sorted(entries)
    lines = _rows(
        session,
        "SELECT id, work_order_id, part_number, priority_rank FROM work_order_demands"
        " WHERE work_order_id = ANY(:ids) ORDER BY work_order_id, id",
        ids=work_order_ids,
    )
    demand_ids = {int(row.id) for row in lines}
    demand_pns = {int(row.id): str(row.part_number) for row in lines}
    for row in lines:
        if row.priority_rank is not None:
            entry = entries[int(row.work_order_id)]
            refusals.append(
                Refusal(
                    "on_hot_list",
                    f"Demand line {int(row.id)} (Part Number {row.part_number!r}) of"
                    f" {entry.label} is on the Hot list (rank {int(row.priority_rank)}). Take"
                    " it off the Hot list in PartFlow first.",
                )
            )
    line_pns: dict[int, list[str]] = defaultdict(list)
    for row in lines:
        line_pns[int(row.work_order_id)].append(str(row.part_number))
    plan.work_orders = [
        {
            "id": entry.id,
            "work_order_number": entry.number,
            "internal": entry.number is None,
            "named": entry.named,
            "part_numbers": sorted(set(line_pns[entry.id])),
            "demand_lines": len(line_pns[entry.id]),
        }
        for entry in (entries[work_order_id] for work_order_id in work_order_ids)
    ]
    plan.part_numbers = [
        {"part_number": part_number, "master": part_number in masters}
        for part_number in part_numbers
    ]

    allocation_rows = _rows(
        session,
        "SELECT id, device_event_id, reverses_allocation_id, part_number"
        " FROM work_order_allocations WHERE work_order_demand_id = ANY(:ids) ORDER BY id",
        ids=sorted(demand_ids),
    )
    _check_allocations(session, demand_ids, allocation_rows, refusals)

    seeds = {
        int(row.id)
        for row in _rows(
            session,
            "SELECT id FROM quantity_flows WHERE part_number = ANY(:pns)",
            pns=sorted(deleted_pns),
        )
    } | {
        # A flow released for a deleted line: by its RECEIVED Movement, or —
        # once its history was purged — by its archive record (refused below).
        int(row.quantity_flow_id)
        for row in _rows(
            session,
            "SELECT quantity_flow_id FROM part_movements WHERE movement_type = :received"
            " AND (metadata['context'] ->> 'work_order_demand_id')::integer = ANY(:ids)"
            " UNION SELECT flow_id FROM history_archived_flows WHERE purged_at IS NOT NULL"
            " AND received_demand_id = ANY(:ids)",
            received=MovementType.RECEIVED,
            ids=sorted(demand_ids),
        )
    }
    flow_ids, edges = _lineage_closure(session, seeds)
    _check_flow_components(session, flow_ids, seeds, demand_ids, deleted_pns, refusals)
    _check_archived_history(session, flow_ids, refusals)
    movements = _rows(
        session,
        "SELECT id, quantity_flow_id, part_number, movement_type, quantity, device_event_id,"
        " reverses_movement_id FROM part_movements WHERE quantity_flow_id = ANY(:ids)"
        " ORDER BY id",
        ids=sorted(flow_ids),
    )
    _check_movement_links(session, flow_ids, movements, refusals)
    route_ids = sorted(
        {
            int(row.assigned_route_id)
            for row in _rows(
                session,
                "SELECT assigned_route_id FROM quantity_flows WHERE id = ANY(:ids)"
                " AND assigned_route_id IS NOT NULL",
                ids=sorted(flow_ids),
            )
        }
    )
    step_ids = [
        int(row.id)
        for row in _rows(
            session,
            "SELECT id FROM assigned_route_steps WHERE assigned_route_id = ANY(:ids) ORDER BY id",
            ids=route_ids,
        )
    ]
    _check_routes(session, flow_ids, route_ids, step_ids, refusals)
    touched_pns = (
        {str(row.part_number) for row in movements}
        | set(demand_pns.values())
        | {str(row.part_number) for row in allocation_rows}
    )
    _check_movement_metadata(session, flow_ids, demand_ids, touched_pns, refusals)
    _check_stock(session, movements, allocation_rows, demand_pns, deleted_pns, refusals)

    plan.ids["work_orders"] = work_order_ids
    plan.ids["work_order_demands"] = sorted(demand_ids)
    plan.ids["work_order_allocations"] = [int(row.id) for row in allocation_rows]
    plan.ids["quantity_flows"] = sorted(flow_ids)
    plan.ids["quantity_flow_lineage"] = [int(edge.id) for edge in edges]
    plan.ids["part_movements"] = [int(row.id) for row in movements]
    plan.ids["assigned_routes"] = route_ids
    plan.ids["assigned_route_steps"] = step_ids
    plan.ids["part_numbers"] = sorted(deleted_pns & masters)

    removed_lines = {
        str(row.entity_id)
        for row in _rows(
            session,
            "SELECT entity_id FROM audit_events WHERE entity_type = :demand_type"
            " AND event_type = :created AND after_data ->> 'work_order_id' = ANY(:ids)",
            demand_type=AuditEntityType.WORK_ORDER_DEMAND.value,
            created=AuditEventType.CREATED.value,
            ids=_texts(work_order_ids),
        )
    }
    deleted: dict[str, set[str]] = {
        "work_order": set(_texts(work_order_ids)),
        "demand": set(_texts(demand_ids)) | removed_lines,
        "flow": set(_texts(flow_ids)),
        "route": set(_texts(route_ids)),
        "part_number": set(deleted_pns),
    }
    _plan_audit_rows(session, plan, deleted)
    return plan


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def delete_guards(tables: Iterable[str]) -> list[tuple[str, str]]:
    """(table, trigger) of every guard trigger that refuses DELETE on ``tables``."""
    wanted = set(tables)
    return sorted(
        (guard.table, name)
        for name, guard in GUARD_TRIGGERS.items()
        if guard.table in wanted and " DELETE " in f" {guard.definition.split(' ON ')[0]} "
    )


def _refuse_unless_owner(session: Session) -> None:
    row = session.execute(
        text(
            "SELECT current_user AS who, (SELECT rolsuper FROM pg_roles"
            " WHERE rolname = current_user) AS superuser,"
            " (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            "  WHERE n.nspname = 'public' AND c.relname = ANY(:tables)"
            "  AND NOT pg_has_role(current_user, c.relowner, 'USAGE')) AS foreign_tables"
        ),
        {"tables": list(LOCK_ORDER)},
    ).one()
    if not row.superuser and int(row.foreign_tables) > 0:
        raise _RefusedError(
            [
                Refusal(
                    "owner_role_required",
                    f"The database role {row.who} does not own the PartFlow tables. Run the"
                    " command as the database owner role (pf delete-permanently does).",
                )
            ]
        )


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _delete_rows(session: Session, table: str, ids: Sequence[Any]) -> None:
    if not ids:
        return
    column = "part_number" if table == "part_numbers" else "id"
    result = session.execute(
        text(f"DELETE FROM {_identifier(table)} WHERE {column} = ANY(:ids)"), {"ids": list(ids)}
    )
    deleted = int(getattr(result, "rowcount", -1))
    if deleted != len(ids):
        raise RuntimeError(
            f"delete-permanently deleted {deleted} rows of {table}, {len(ids)} planned."
        )


def _audit_document(
    request: DeletionRequest, plan: Plan, run_id: str
) -> dict[str, dict[str, object]]:
    return {
        AUDIT_KEY: {
            "source": "cli",
            "run_id": run_id,
            "operator": request.operator,
            "reason": request.reason,
            "backup": request.backup_name,
            "work_orders": [
                entry["work_order_number"] or f"internal:{entry['id']}"
                for entry in plan.work_orders
            ],
            "part_numbers": [entry["part_number"] for entry in plan.part_numbers],
            "deleted": plan.counts(),
        }
    }


def _snapshots(
    session: Session, plan: Plan
) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any] | None]]:
    """The audited fields of every record to delete, read before the deletes."""
    work_orders = {
        work_order_id: work_order_snapshot(session.get_one(WorkOrder, work_order_id))
        for work_order_id in plan.ids["work_orders"]
    }
    part_numbers: dict[str, dict[str, Any] | None] = {}
    for entry in plan.part_numbers:
        part_number = str(entry["part_number"])
        master = session.get(PartNumber, part_number)
        part_numbers[part_number] = dict(master_snapshot(master)) if master is not None else None
    return work_orders, part_numbers


def _execute(
    session: Session, request: DeletionRequest, plan: Plan, report: DeletionReport
) -> None:
    run_id = uuid.uuid4().hex
    work_order_before, part_number_before = _snapshots(session, plan)
    tables = [table for table in DELETE_ORDER if plan.ids[table]]
    guards = delete_guards(tables)
    for table, trigger in guards:
        session.execute(
            text(f"ALTER TABLE {_identifier(table)} DISABLE TRIGGER {_identifier(trigger)}")
        )
    report.guard_triggers = [trigger for _, trigger in guards]
    for table in DELETE_ORDER:
        _delete_rows(session, table, plan.ids[table])
    for table, trigger in guards:
        session.execute(
            text(f"ALTER TABLE {_identifier(table)} ENABLE TRIGGER {_identifier(trigger)}")
        )
    metadata = _audit_document(request, plan, run_id)
    for work_order_id, before in work_order_before.items():
        audit.append_audit_event(
            session,
            event_type=AuditEventType.DELETED,
            entity_type=AuditEntityType.WORK_ORDER,
            entity_id=str(work_order_id),
            before_data=before,
            after_data=None,
            metadata=metadata,
        )
    for part_number, master in part_number_before.items():
        audit.append_audit_event(
            session,
            event_type=AuditEventType.DELETED,
            entity_type=AuditEntityType.PART_NUMBER,
            entity_id=part_number,
            before_data=master,
            after_data=None,
            metadata=metadata,
        )
    session.flush()
    disabled = session.execute(
        text(
            "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
            " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
            " AND t.tgname = ANY(:names) AND t.tgenabled <> 'O'"
        ),
        {"names": sorted(GUARD_TRIGGERS)},
    ).all()
    if disabled:
        raise RuntimeError(f"Guard triggers are not enabled: {sorted(r.tgname for r in disabled)}.")
    report.audit_rows = [
        {"id": int(row.id), "entity_type": str(row.entity_type), "entity_id": str(row.entity_id)}
        for row in session.execute(
            text(
                "SELECT id, entity_type, entity_id FROM audit_events"
                " WHERE metadata -> :key ->> 'run_id' = :run ORDER BY id"
            ),
            {"key": AUDIT_KEY, "run": run_id},
        )
    ]


def _failure_code(exc: BaseException) -> str:
    if isinstance(exc, DBAPIError) and isinstance(
        exc.orig, (psycopg.errors.LockNotAvailable, psycopg.errors.DeadlockDetected)
    ):
        return "lock_not_available"
    if isinstance(exc, SQLAlchemyError) and is_connection_failure(exc):
        return "database_unavailable"
    return "internal_error"


def deletion_failure(
    report: DeletionReport, code: str, message: str, exception: BaseException | None = None
) -> DeletionReport:
    """Finish ``report`` as failed: nothing was deleted."""
    report.result = "failed"
    report.error = RunError(code, message, exception)
    report.finished_at = _now()
    return report


def run_deletion(
    engine: Engine, request: DeletionRequest, *, lock_timeout_seconds: int
) -> DeletionReport:
    """One preview or deletion; always a complete report."""
    report = DeletionReport(started_at=_now(), request=request)
    with Session(engine) as session:
        try:
            if request.confirm:
                session.execute(
                    text("SELECT set_config('lock_timeout', :timeout, true)"),
                    {"timeout": f"{lock_timeout_seconds}s"},
                )
                _refuse_unless_owner(session)
                session.execute(text(f"LOCK TABLE {', '.join(LOCK_ORDER)} IN EXCLUSIVE MODE"))
            else:
                session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                _refuse_unless_owner(session)
            report.plan = build_plan(session, request)
            if report.plan.refusals:
                raise _RefusedError(report.plan.refusals)
            if request.confirm:
                _execute(session, request, report.plan, report)
        except _RefusedError as refusal:
            session.rollback()
            if report.plan is None:
                report.plan = Plan(refusals=refusal.refusals)
            report.result = "refused"
            report.finished_at = _now()
            return report
        except Exception as exc:
            # Every failure before COMMIT rolls the whole run back.
            _rollback(session)
            report.audit_rows = []
            report.guard_triggers = []
            code = _failure_code(exc)
            return deletion_failure(report, code, RUN_MESSAGES[code], exc)
        if not request.confirm:
            session.rollback()
            report.result = "preview"
            report.finished_at = _now()
            return report
        try:
            session.commit()
        except Exception as exc:
            # COMMIT itself failed: the outcome is unknown, never "nothing deleted".
            report.result = "outcome_unknown"
            report.error = RunError("outcome_unknown", RUN_MESSAGES["outcome_unknown"], exc)
            report.finished_at = _now()
            return report
    report.result = "deleted"
    report.finished_at = _now()
    return report


def _rollback(session: Session) -> None:
    # On a lost connection the rollback fails; the server discards the transaction itself.
    with contextlib.suppress(SQLAlchemyError):
        session.rollback()


# ---------------------------------------------------------------------------
# The JSON document and the summary line
# ---------------------------------------------------------------------------


def deletion_document(report: DeletionReport) -> dict[str, object]:
    """The JSON document of one run (keys in the contract order)."""
    plan = report.plan
    finished = report.finished_at or report.started_at
    request = report.request
    return {
        "report_version": REPORT_VERSION,
        "command": COMMAND,
        "result": report.result,
        "exit_code": report.exit_code,
        "started_at": _iso(report.started_at),
        "finished_at": _iso(report.finished_at),
        "duration_ms": int((finished - report.started_at).total_seconds() * 1000),
        "mode": "delete" if request.confirm else "preview",
        "operator": request.operator,
        "reason": request.reason,
        "backup": request.backup_name,
        "requested": {
            "work_orders": list(dict.fromkeys(request.work_order_numbers)),
            "part_numbers": list(dict.fromkeys(request.part_numbers)),
        },
        "work_orders": [] if plan is None else plan.work_orders,
        "part_numbers": [] if plan is None else plan.part_numbers,
        "rows": None if plan is None or plan.refusals else plan.counts(),
        "kept_audit_rows": [] if plan is None or plan.refusals else plan.kept_audit_rows,
        "refusals": (
            []
            if plan is None
            else [{"code": item.code, "message": item.message} for item in plan.refusals]
        ),
        "audit_rows": list(report.audit_rows),
        "guard_triggers": list(report.guard_triggers),
        "error": (
            None
            if report.error is None
            else {"code": report.error.code, "message": report.error.message}
        ),
    }


def _records(plan: Plan) -> str:
    work_orders = len(plan.work_orders)
    part_numbers = len(plan.part_numbers)
    return (
        f"{work_orders} Work Order{'s' if work_orders != 1 else ''} and"
        f" {part_numbers} Part Number{'s' if part_numbers != 1 else ''}"
    )


def deletion_summary(report: DeletionReport) -> str:
    plan = report.plan
    if report.result in ("preview", "deleted") and plan is not None:
        rows = ", ".join(f"{table} {count}" for table, count in plan.counts().items() if count)
        kept = len(plan.kept_audit_rows)
        if report.result == "preview":
            return (
                f"delete-permanently: preview — would delete {_records(plan)} ({rows});"
                f" {kept} audit rows kept because they also concern other records. Nothing was"
                " changed; run again with --confirm to delete."
            )
        return (
            f"delete-permanently: deleted {_records(plan)} ({rows}); one audit row each;"
            f" {kept} audit rows kept."
        )
    if report.result == "refused" and plan is not None:
        reasons = " ".join(item.message for item in plan.refusals)
        return f"delete-permanently: refused — nothing was deleted. {reasons}"
    message = report.error.message if report.error is not None else report.result
    return f"delete-permanently: {message}"
