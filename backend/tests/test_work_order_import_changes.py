"""Pure tests of the Work Order import's change model (Phase 15 slice 2, CH-1…CH-7).

``plan_changes``, the classification of one existing Work Order, the
``update_token`` digest and the state token run on hand-built
:class:`WorkOrderState` values — no database: what a file changes on an
Open or Released Work Order, which edit rules refuse it, and what the
typed confirmation and the stale check bind. CH-8 (one state read feeds
both the change list and the state token) needs the database and lives
in ``test_work_order_import_update_api``.
"""

import dataclasses
import datetime
from collections.abc import Sequence
from typing import Any

import pytest

from app.application import work_orders
from app.application.errors import ConflictError
from app.application.work_order_import import (
    ImportChange,
    ImportChangeKind,
    ImportLine,
    ImportOutcome,
    RowError,
    WorkOrderImportEntry,
    _classify_existing,
    _Group,
    _planned_update,
    plan_changes,
    update_token_of,
)
from app.application.work_orders import DemandState, WorkOrderState, work_order_state_token
from app.domain.enums import RequestType
from app.infrastructure.models import WorkOrderDemand

_JUL_24 = datetime.date(2026, 7, 24)
_AUG_1 = datetime.date(2026, 8, 1)


def _demand(
    demand_id: int,
    part_number: str,
    quantity: int,
    *,
    due: datetime.date | None = None,
    jobs: Sequence[str] = (),
    allocated: int = 0,
    released: int = 0,
    ranked: bool = False,
) -> DemandState:
    return DemandState(
        id=demand_id,
        part_number=part_number,
        requested_quantity=quantity,
        due_date=due,
        job_numbers=tuple(jobs),
        allocated_quantity=allocated,
        released_quantity=released,
        ranked=ranked,
    )


def _state(
    *lines: DemandState, status: str = "OPEN", number: str = "WO-1", completed: bool = False
) -> WorkOrderState:
    return WorkOrderState(
        work_order_id=7,
        work_order_number=number,
        status=status,
        completed=completed,
        lines=tuple(lines),
    )


def _file(*lines: tuple[Any, ...]) -> list[ImportLine]:
    """File lines on rows 2, 3, …: (PN, quantity[, due date, Job Number])."""
    return [
        ImportLine(
            row=row,
            part_number=line[0],
            requested_quantity=line[1],
            due_date=line[2] if len(line) > 2 else None,
            job_number=line[3] if len(line) > 3 else None,
        )
        for row, line in enumerate(lines, start=2)
    ]


def _group(lines: list[ImportLine], key: str = "WO-1") -> _Group:
    return _Group(key=key, rows=[line.row for line in lines], lines=lines)


def _classified(
    state: WorkOrderState, lines: list[ImportLine], known: Sequence[str] = ()
) -> WorkOrderImportEntry:
    entry, _ = _classify_existing(_group(lines, state.work_order_number or ""), state, known, set())
    return entry


def _edit(
    row: int,
    part_number: str,
    demand_id: int,
    *,
    quantity: tuple[int, int] | None = None,
    due: tuple[datetime.date | None, datetime.date | None] | None = None,
    jobs: tuple[tuple[str, ...], tuple[str, ...]] | None = None,
    leaves: bool = False,
) -> ImportChange:
    return ImportChange(
        kind=ImportChangeKind.EDIT_LINE,
        row=row,
        part_number=part_number,
        demand_id=demand_id,
        new_part_number=False,
        quantity=quantity,
        due_date=due,
        job_numbers=jobs,
        leaves_hot_list=leaves,
    )


# ---------------------------------------------------------------------------
# CH-1 … CH-3 — what a file line changes
# ---------------------------------------------------------------------------


def test_same_values_change_nothing_and_a_new_quantity_edits_only_the_quantity() -> None:
    """CH-1."""
    state = _state(_demand(1, "ABC-1", 10))
    assert plan_changes(state, _file(("ABC-1", 10)), set()).changes == ()
    assert plan_changes(state, _file(("ABC-1", 15)), set()).changes == (
        _edit(2, "ABC-1", 1, quantity=(10, 15)),
    )


def test_blank_cells_keep_a_new_date_is_set_and_a_job_number_is_appended() -> None:
    """CH-2."""
    state = _state(_demand(1, "ABC-1", 10, due=_JUL_24, jobs=["J1"]))
    assert plan_changes(state, _file(("ABC-1", 10, None, None)), set()).changes == ()
    assert plan_changes(state, _file(("ABC-1", 10, _JUL_24, "J1")), set()).changes == ()
    assert plan_changes(state, _file(("ABC-1", 10, _AUG_1, None)), set()).changes == (
        _edit(2, "ABC-1", 1, due=(_JUL_24, _AUG_1)),
    )
    assert plan_changes(state, _file(("ABC-1", 10, None, "J2")), set()).changes == (
        _edit(2, "ABC-1", 1, jobs=(("J1",), ("J1", "J2"))),
    )
    undated = _state(_demand(1, "ABC-1", 10))
    assert plan_changes(undated, _file(("ABC-1", 12, _AUG_1, "J9")), set()).changes == (
        _edit(2, "ABC-1", 1, quantity=(10, 12), due=(None, _AUG_1), jobs=((), ("J9",))),
    )


def test_an_unknown_part_number_is_added_to_an_open_work_order_only() -> None:
    """CH-3."""
    state = _state(_demand(1, "ABC-1", 10))
    lines = _file(("ABC-1", 10), ("NEW-1", 4, _JUL_24, "J1"), ("OLD-1", 2))
    plan = plan_changes(state, lines, {"OLD-1"})
    assert plan.errors == ()
    assert plan.changes == (
        ImportChange(
            kind=ImportChangeKind.ADD_LINE,
            row=3,
            part_number="NEW-1",
            demand_id=None,
            new_part_number=True,
            quantity=(None, 4),
            due_date=(None, _JUL_24),
            job_numbers=((), ("J1",)),
            leaves_hot_list=False,
        ),
        ImportChange(
            kind=ImportChangeKind.ADD_LINE,
            row=4,
            part_number="OLD-1",
            demand_id=None,
            new_part_number=False,
            quantity=(None, 2),
            due_date=(None, None),
            job_numbers=((), ()),
            leaves_hot_list=False,
        ),
    )

    released = _state(_demand(1, "ABC-1", 10, released=10), status="RELEASED")
    plan = plan_changes(released, _file(("ABC-1", 12), ("NEW-1", 4)), set())
    assert plan.errors == (
        RowError(
            3,
            "Part Number",
            "Part Number NEW-1 is not on this Work Order, and the Work Order is Released:"
            " lines can be added only while it is Open. Remove this row from the file.",
        ),
    )
    entry = _classified(released, _file(("ABC-1", 12), ("NEW-1", 4)))
    assert (entry.outcome, entry.work_order_id, entry.existing_status) == (
        ImportOutcome.REFUSED,
        7,
        "RELEASED",
    )
    assert entry.lines == () and entry.changes is None and entry.lines_not_in_file is None

    partially = _state(_demand(1, "ABC-1", 10, released=4))
    assert [
        change.kind for change in plan_changes(partially, _file(("NEW-1", 4)), set()).changes
    ] == [ImportChangeKind.ADD_LINE]


# ---------------------------------------------------------------------------
# CH-4 — the quantity floor (one rule source)
# ---------------------------------------------------------------------------


def _floor_error(state: WorkOrderState, quantity: int) -> RowError:
    (error,) = plan_changes(state, _file(("ABC-1", quantity)), set()).errors
    return error


def test_the_quantity_floor_refuses_below_the_committed_quantity() -> None:
    """CH-4."""
    released = _state(_demand(1, "ABC-1", 20, released=10, allocated=4))
    assert _floor_error(released, 5) == RowError(
        2,
        "Requested Quantity",
        "Cannot lower Qty to 5 pcs for Part Number 'ABC-1': 10 pcs are already released."
        " Enter 10 pcs or more.",
    )
    assert plan_changes(released, _file(("ABC-1", 10)), set()).errors == ()

    allocated = _state(_demand(1, "ABC-1", 20, released=10, allocated=12))
    assert _floor_error(allocated, 11).message == (
        "Cannot lower Qty to 11 pcs for Part Number 'ABC-1': 12 pcs are already allocated."
        " Enter 12 pcs or more."
    )

    beyond = _state(_demand(1, "ABC-1", 10, allocated=15, released=15))
    assert _floor_error(beyond, 12).message == (
        "Cannot set Qty to 12 pcs for Part Number 'ABC-1': 15 pcs are already released."
        " Enter 15 pcs or more."
    )
    beyond_allocated = _state(_demand(1, "ABC-1", 10, allocated=15, released=10))
    assert _floor_error(beyond_allocated, 12).message == (
        "Cannot set Qty to 12 pcs for Part Number 'ABC-1': 15 pcs are already allocated."
        " Enter 15 pcs or more."
    )
    for quantity in (15, 16):
        assert plan_changes(beyond_allocated, _file(("ABC-1", quantity)), set()).errors == ()
    # The unchanged quantity is never judged.
    unchanged = plan_changes(beyond_allocated, _file(("ABC-1", 10, _AUG_1, None)), set())
    assert unchanged.errors == ()
    assert unchanged.changes == (_edit(2, "ABC-1", 1, due=(None, _AUG_1)),)


def test_the_restricted_edit_guard_keeps_its_messages() -> None:
    """CH-4: the guard still refuses locked fields and the floor with
    identical messages after the extraction of ``check_quantity_floor``."""
    demand = WorkOrderDemand(
        part_number="ABC-1",
        request_type=RequestType.NEW,
        requested_quantity=10,
        allocated_quantity=15,
        job_numbers=[],
        requester=None,
        reason=None,
        notes=None,
    )
    guard = work_orders._guard_released_line_edit
    with pytest.raises(ConflictError) as locked:
        guard(demand, {"request_type": "MODIFY"}, 4)
    assert locked.value.message == (
        "Cannot change Request Type for Part Number 'ABC-1': production quantity has already"
        " been released for this demand line. Qty, Due date and Job Numbers stay editable."
    )
    with pytest.raises(ConflictError) as set_below:
        guard(demand, {"requested_quantity": 12}, 4)
    assert set_below.value.message == (
        "Cannot set Qty to 12 pcs for Part Number 'ABC-1': 15 pcs are already allocated."
        " Enter 15 pcs or more."
    )
    demand.allocated_quantity = 2
    with pytest.raises(ConflictError) as lowered:
        guard(demand, {"requested_quantity": 3}, 4)
    assert lowered.value.message == (
        "Cannot lower Qty to 3 pcs for Part Number 'ABC-1': 4 pcs are already released."
        " Enter 4 pcs or more."
    )
    guard(demand, {"requested_quantity": 10}, 40)  # unchanged: never judged
    guard(demand, {"requested_quantity": 4}, 4)


# ---------------------------------------------------------------------------
# CH-5 / CH-6 — consequences, kept lines, the D8 count
# ---------------------------------------------------------------------------


def test_hot_list_removal_and_completion_are_flagged() -> None:
    """CH-5."""
    state = _state(
        _demand(1, "ABC-1", 10, allocated=6, released=6, ranked=True),
        _demand(2, "XYZ-1", 5, allocated=5, released=5),
    )
    plan = plan_changes(state, _file(("ABC-1", 6)), set())
    assert plan.changes == (_edit(2, "ABC-1", 1, quantity=(10, 6), leaves=True),)
    assert plan.completes_work_order is True
    with_add = plan_changes(state, _file(("ABC-1", 6), ("NEW-1", 1)), set())
    assert with_add.completes_work_order is False
    still_short = plan_changes(state, _file(("ABC-1", 7)), set())
    assert still_short.changes == (_edit(2, "ABC-1", 1, quantity=(10, 7)),)
    assert still_short.completes_work_order is False
    unranked = _state(_demand(1, "ABC-1", 10, allocated=6, released=6))
    (change,) = plan_changes(unranked, _file(("ABC-1", 6)), set()).changes
    assert change.leaves_hot_list is False
    due_only = plan_changes(state, _file(("ABC-1", 10, _AUG_1, None)), set())
    assert due_only.completes_work_order is False


def test_kept_lines_and_the_lines_written_without_a_due_date() -> None:
    """CH-6."""
    state = _state(_demand(3, "C-1", 1), _demand(5, "A-1", 1), _demand(9, "B-1", 1))
    plan = plan_changes(state, _file(("A-1", 2)), set())
    assert plan.lines_not_in_file == ("C-1", "B-1")

    only_listed = _classified(state, _file(("A-1", 1)))
    assert (only_listed.outcome, only_listed.differs_from_file) == (ImportOutcome.EXISTS, True)
    assert only_listed.lines_not_in_file == ("C-1", "B-1")
    assert (only_listed.changes, only_listed.lines_without_due_date) == (None, 0)
    everything = _classified(state, _file(("C-1", 1), ("A-1", 1), ("B-1", 1)))
    assert (everything.outcome, everything.differs_from_file) == (ImportOutcome.EXISTS, False)
    assert everything.lines_not_in_file == ()

    update = _classified(
        state,
        _file(("A-1", 2), ("NEW-1", 1), ("NEW-2", 1, _JUL_24, None), ("NEW-3", 1)),
        known=["NEW-3"],
    )
    assert update.outcome is ImportOutcome.WILL_UPDATE
    assert update.lines_without_due_date == 2
    assert update.new_part_numbers == ("NEW-1", "NEW-2")
    assert update.differs_from_file is None

    completed = _classified(dataclasses.replace(state, completed=True, status="COMPLETED"), [])
    assert (completed.outcome, completed.lines_without_due_date) == (ImportOutcome.EXISTS, 0)
    assert completed.lines_not_in_file is None


# ---------------------------------------------------------------------------
# CH-7 — what the typed confirmation and the stale check bind
# ---------------------------------------------------------------------------


def _token(state: WorkOrderState, lines: list[ImportLine]) -> str | None:
    return update_token_of([_classified(state, lines)])


def test_the_update_token_digests_what_the_confirmation_shows() -> None:
    """CH-7."""
    base = _state(
        _demand(1, "ABC-1", 10, allocated=2, released=2),
        _demand(2, "XYZ-1", 5, allocated=1, released=1, ranked=True),
        _demand(3, "KEPT-1", 5),
    )
    lines = _file(("ABC-1", 12), ("XYZ-1", 5))
    token = _token(base, lines)
    assert token is not None and len(token) == 64 and token == token.lower()
    assert _token(base, lines) == token

    def with_line(index: int, **fields: Any) -> WorkOrderState:
        changed = list(base.lines)
        changed[index] = dataclasses.replace(changed[index], **fields)
        return dataclasses.replace(base, lines=tuple(changed))

    # Unchanged: activity on an unedited line that changes nothing shown.
    assert _token(with_line(1, allocated_quantity=3, ranked=False), lines) == token
    assert _token(with_line(0, released_quantity=4), lines) == token
    # Changed: values, rows, flags, number, status, kept lines.
    assert _token(base, _file(("ABC-1", 13), ("XYZ-1", 5))) != token
    assert _token(with_line(0, requested_quantity=11), lines) != token
    assert _token(base, _file(("XYZ-1", 5), ("ABC-1", 12))) != token
    assert _token(dataclasses.replace(base, work_order_number="WO-2"), lines) != token
    assert _token(dataclasses.replace(base, status="RELEASED"), lines) != token
    assert _token(base, _file(("ABC-1", 12), ("XYZ-1", 5), ("KEPT-1", 5))) != token
    lowered = _file(("ABC-1", 12), ("XYZ-1", 1))
    assert _token(base, lowered) != _token(with_line(1, ranked=False), lowered)
    completing = _file(("ABC-1", 4), ("B-1", 1))
    filled = _state(_demand(1, "ABC-1", 10, allocated=4), _demand(2, "B-1", 1, allocated=1))
    short = _state(_demand(1, "ABC-1", 10, allocated=4), _demand(2, "B-1", 1))
    assert _classified(filled, completing).completes_work_order is True
    assert _classified(short, completing).completes_work_order is False
    assert _token(filled, completing) != _token(short, completing)
    # Group order.
    first = _classified(base, lines)
    second = dataclasses.replace(_classified(base, _file(("ABC-1", 14))), work_order_id=8)
    assert update_token_of([first, second]) != update_token_of([second, first])
    # Nothing to update.
    assert _token(base, _file(("ABC-1", 10))) is None
    assert update_token_of([]) is None


def test_the_state_token_hashes_what_the_update_relies_on() -> None:
    """CH-7: ``work_order_state_token``."""
    base = _state(
        _demand(1, "ABC-1", 10, due=_JUL_24, jobs=["J1"], allocated=2, released=2, ranked=True),
        _demand(2, "XYZ-1", 5, ranked=False),
    )
    edited = {1}
    token = work_order_state_token(base, edited)
    assert len(token) == 64 and work_order_state_token(base, edited) == token

    def line(index: int, **fields: Any) -> WorkOrderState:
        changed = list(base.lines)
        changed[index] = dataclasses.replace(changed[index], **fields)
        return dataclasses.replace(base, lines=tuple(changed))

    for changed in (
        dataclasses.replace(base, work_order_number="WO-9"),
        dataclasses.replace(base, status="RELEASED"),
        dataclasses.replace(base, lines=base.lines[:1]),
        line(1, id=3),
        line(1, part_number="XYZ-2"),
        line(0, requested_quantity=11),
        line(0, due_date=_AUG_1),
        line(0, job_numbers=("J1", "J2")),
        line(0, allocated_quantity=3),
        line(0, ranked=False),
    ):
        assert work_order_state_token(changed, edited) != token, changed
    for unchanged in (line(1, ranked=True), line(0, released_quantity=9)):
        assert work_order_state_token(unchanged, edited) == token, unchanged


def test_the_planned_update_sends_only_the_changed_fields() -> None:
    """The ``update_work_order`` call of a change list."""
    state = _state(_demand(1, "ABC-1", 10, jobs=["J1"]), _demand(2, "XYZ-1", 5))
    plan = plan_changes(
        state, _file(("ABC-1", 12, None, "J2"), ("XYZ-1", 5, _JUL_24, None), ("N-1", 3)), set()
    )
    update = _planned_update(state, plan.changes)
    assert update.work_order_id == 7
    assert update.line_edits == (
        {"id": 1, "requested_quantity": 12, "job_numbers": ["J1", "J2"]},
        {"id": 2, "due_date": _JUL_24},
    )
    assert update.new_lines == (
        {"part_number": "N-1", "requested_quantity": 3, "due_date": None, "job_numbers": []},
    )
    assert update.state_token == work_order_state_token(state, {1})
