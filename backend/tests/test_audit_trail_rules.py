"""The pure rules of the PN audit trail (Phase 14 slice 7) — no database.

- BT-1: ``classify_audit_row`` over every committed audit vocabulary, an
  unknown combination falling back to ``CHANGE_RECORDED``;
- BT-2: ``classify_allocation_row``;
- BT-3: ``field_changes`` per event type, in ``TRAIL_FIELDS`` order,
  never an identity or kind-only key;
- BT-4: image digests reduced to booleans — no digest is ever returned;
- BT-8: every key the audit snapshots emit is displayed, an identity or a
  kind-only key of its entity, and the wire ``field`` Literal is exactly
  the displayed fields.
"""

import datetime
import typing
from typing import Any

import pytest

from app.api.tracking import AuditTrailChangeResponse
from app.application import audit_trail
from app.application.audit_trail import (
    IDENTITY_FIELDS,
    KIND_ONLY_FIELDS,
    TRAIL_FIELDS,
    AuditTrailKind,
    TrailChange,
    classify_allocation_row,
    classify_audit_row,
    field_changes,
)
from app.application.errors import InvalidInputError
from app.application.part_numbers import master_snapshot
from app.application.work_orders import demand_snapshot, work_order_snapshot
from app.domain.enums import AuditEntityType, AuditEventType
from app.infrastructure.models import PartNumber, WorkOrder, WorkOrderAllocation, WorkOrderDemand

PN = AuditEntityType.PART_NUMBER
WO = AuditEntityType.WORK_ORDER
WOD = AuditEntityType.WORK_ORDER_DEMAND
ROUTE = AuditEntityType.ASSIGNED_ROUTE
CREATED = AuditEventType.CREATED
UPDATED = AuditEventType.UPDATED
DELETED = AuditEventType.DELETED
ADJUSTED = AuditEventType.ROUTE_ADJUSTED

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_MASTER = {"part_number": "PN-1", "name": "Shaft", "current_revision": None, "erp_id": None}
_DEMAND = {
    "work_order_id": 7,
    "part_number": "PN-1",
    "request_type": "NEW",
    "requested_quantity": 10,
    "due_date": None,
    "job_numbers": [],
    "requester": None,
    "reason": None,
    "notes": None,
}


# ---------------------------------------------------------------------------
# BT-1 / BT-2 — classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entity", "event", "before", "after", "metadata", "kind"),
    [
        (PN, CREATED, None, _MASTER, None, AuditTrailKind.PART_NUMBER_CREATED),
        (PN, UPDATED, _MASTER, {**_MASTER, "name": "Pin"}, None, "PART_NUMBER_UPDATED"),
        (PN, UPDATED, {"image": None}, {"image": _DIGEST_A}, None, "PART_NUMBER_IMAGE_CHANGED"),
        (PN, UPDATED, {"image": _DIGEST_A}, {"image": None}, None, "PART_NUMBER_IMAGE_CHANGED"),
        # Both detail and image keys: a details edit, never an image change.
        (
            PN,
            UPDATED,
            {"name": "A", "image": None},
            {"name": "B", "image": _DIGEST_A},
            None,
            "PART_NUMBER_UPDATED",
        ),
        (PN, DELETED, {**_MASTER, "image": None}, None, None, "PART_NUMBER_DELETED"),
        (WO, CREATED, None, {"status": "OPEN"}, None, "WORK_ORDER_CREATED"),
        (WO, UPDATED, {"due_date": None}, {"due_date": "2030-01-01"}, None, "WORK_ORDER_UPDATED"),
        (
            WO,
            UPDATED,
            {"completed_at": None},
            {"completed_at": "2026-10-07T08:00:00+00:00"},
            {"completion": {"trigger": "WORK_ORDER_SAVE"}},
            "WORK_ORDER_COMPLETED",
        ),
        (WOD, CREATED, None, _DEMAND, None, "DEMAND_CREATED"),
        (WOD, UPDATED, _DEMAND, {**_DEMAND, "notes": "x"}, None, "DEMAND_UPDATED"),
        (
            WOD,
            UPDATED,
            {"priority_rank": None},
            {"priority_rank": 1},
            {"hot_list_change": {"action": "ADD"}},
            "PRIORITY_CHANGED",
        ),
        (ROUTE, ADJUSTED, {"steps": []}, {"steps": []}, {"route_adjustment": {}}, "ROUTE_ADJUSTED"),
        # Combinations no writer produces.
        (WOD, DELETED, _DEMAND, None, None, "CHANGE_RECORDED"),
        (ROUTE, UPDATED, None, None, None, "CHANGE_RECORDED"),
        (PN, ADJUSTED, None, None, None, "CHANGE_RECORDED"),
        (AuditEntityType.AREA, UPDATED, {}, {}, None, "CHANGE_RECORDED"),
        ("Unknown", CREATED, None, {}, None, "CHANGE_RECORDED"),
    ],
)
def test_classify_audit_row(
    entity: str,
    event: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    kind: str,
) -> None:
    """BT-1."""
    assert classify_audit_row(entity, event, before, after, metadata) == kind


def _allocation(**fields: Any) -> WorkOrderAllocation:
    values: dict[str, Any] = {"reverses_allocation_id": None, "exceeds_demand": False}
    values.update(fields)
    return WorkOrderAllocation(**values)


def test_classify_allocation_row() -> None:
    """BT-2: a reversal of any row (a correction included) is a reversal."""
    assert classify_allocation_row(_allocation(reverses_allocation_id=3)) == (
        AuditTrailKind.ALLOCATION_REVERSED
    )
    assert classify_allocation_row(_allocation(exceeds_demand=True)) == (
        AuditTrailKind.ALLOCATED_BEYOND_DEMAND
    )
    assert classify_allocation_row(_allocation()) == AuditTrailKind.ALLOCATED


# ---------------------------------------------------------------------------
# BT-3 / BT-4 — field changes
# ---------------------------------------------------------------------------


def test_created_lists_set_fields_only() -> None:
    """BT-3: a creation skips null and ``[]``; identity keys never appear."""
    changes = field_changes(WOD, CREATED, None, {**_DEMAND, "job_numbers": ["J1"]})
    assert changes == [
        TrailChange("request_type", None, "NEW"),
        TrailChange("requested_quantity", None, 10),
        TrailChange("job_numbers", None, ["J1"]),
    ]
    assert field_changes(WOD, CREATED, None, _DEMAND) == [
        TrailChange("request_type", None, "NEW"),
        TrailChange("requested_quantity", None, 10),
    ]
    assert field_changes(PN, CREATED, None, _MASTER) == [TrailChange("name", None, "Shaft")]


def test_deleted_lists_the_last_values() -> None:
    """BT-3 / BT-4: a hard delete lists its last values; an image becomes ``True``."""
    before = {**_MASTER, "erp_id": "E-1", "image": _DIGEST_A}
    assert field_changes(PN, DELETED, before, None) == [
        TrailChange("name", "Shaft", None),
        TrailChange("erp_id", "E-1", None),
        TrailChange("image", True, None),
    ]
    assert field_changes(PN, DELETED, {**_MASTER, "image": None}, None) == [
        TrailChange("name", "Shaft", None)
    ]


def test_updated_lists_differing_fields_in_display_order() -> None:
    """BT-3: absent = null; identity and kind-only keys never appear."""
    before = {**_DEMAND, "requested_quantity": 4, "notes": "a"}
    after = {**_DEMAND, "requested_quantity": 10, "notes": None, "due_date": "2030-01-01"}
    assert field_changes(WOD, UPDATED, before, after) == [
        TrailChange("requested_quantity", 4, 10),
        TrailChange("due_date", None, "2030-01-01"),
        TrailChange("notes", "a", None),
    ]
    # An identity change (never written) is still never listed.
    assert field_changes(WOD, UPDATED, _DEMAND, {**_DEMAND, "work_order_id": 8}) == []
    assert field_changes(WOD, UPDATED, {"priority_rank": 2}, {"priority_rank": None}) == [
        TrailChange("priority_rank", 2, None)
    ]
    assert field_changes(WO, UPDATED, {"status": "OPEN"}, {}) == [
        TrailChange("status", "OPEN", None)
    ]
    completion = field_changes(
        WO, UPDATED, {"completed_at": None}, {"completed_at": "2026-10-07T08:00:00+00:00"}
    )
    assert completion == []
    assert field_changes(ROUTE, ADJUSTED, {"steps": [1]}, {"steps": [2]}) == []
    assert field_changes(AuditEntityType.AREA, UPDATED, {"name": "a"}, {"name": "b"}) == []


@pytest.mark.parametrize(
    ("before", "after", "shown"),
    [
        (None, _DIGEST_A, (False, True)),
        (_DIGEST_A, _DIGEST_B, (True, True)),
        (_DIGEST_A, None, (True, False)),
    ],
    ids=["added", "replaced", "removed"],
)
def test_image_digests_become_booleans(
    before: str | None, after: str | None, shown: tuple[bool, bool]
) -> None:
    """BT-4: the stored digests are compared, never returned."""
    changes = field_changes(PN, UPDATED, {"image": before}, {"image": after})
    assert changes == [TrailChange("image", *shown)]
    assert _DIGEST_A not in repr(changes) and _DIGEST_B not in repr(changes)
    assert field_changes(PN, UPDATED, {"image": _DIGEST_A}, {"image": _DIGEST_A}) == []


def test_priority_and_completion_payloads() -> None:
    """The Hot list cause of a rank row and the completion trigger."""
    cause = {
        "trigger": "ALLOCATION",
        "reference": {"source": "MANAGEMENT", "device_event_id": "secret"},
        "removed": [{"work_order_demand_id": 5, "reason": "FULLY_ALLOCATED"}],
    }
    metadata = {"hot_list_change": {"action": "AUTO_REMOVE", "cause": cause}}
    removed, closed_gap = {"priority_rank": None}, {"priority_rank": 2}
    assert audit_trail.trail_priority(metadata, 5, {"priority_rank": 1}, removed) == (
        "AUTO_REMOVE",
        "ALLOCATION",
        "FULLY_ALLOCATED",
        False,
    )
    assert audit_trail.trail_priority(metadata, 6, {"priority_rank": 3}, closed_gap) == (
        "AUTO_REMOVE",
        "ALLOCATION",
        None,
        True,
    )
    manual = {"hot_list_change": {"action": "MOVE_UP", "device_event_id": "x", "fingerprint": "y"}}
    up, down = {"priority_rank": 1}, {"priority_rank": 2}
    assert audit_trail.trail_priority(manual, 5, down, up) == ("MOVE_UP", None, None, False)
    assert audit_trail.trail_priority(manual, 6, up, down) == ("MOVE_UP", None, None, True)
    assert audit_trail.completion_trigger({"completion": {"trigger": "DEMAND_LINE_REMOVAL"}}) == (
        "DEMAND_LINE_REMOVAL"
    )
    assert audit_trail.completion_trigger(None) is None


def test_the_cursor_needs_both_parts_or_neither() -> None:
    """TR-2."""
    assert audit_trail.trail_cursor(None, None) is None
    assert audit_trail.trail_cursor("AUDIT", 4) == ("AUDIT", 4)
    for source, row_id in (("AUDIT", None), (None, 4)):
        with pytest.raises(InvalidInputError) as refused:
            audit_trail.trail_cursor(source, row_id)
        assert str(refused.value) == "Give both before_source and before_id, or neither."


# ---------------------------------------------------------------------------
# BT-8 — snapshot completeness
# ---------------------------------------------------------------------------


def _covered(entity: AuditEntityType) -> set[str]:
    return (
        set(TRAIL_FIELDS.get(entity, ()))
        | set(IDENTITY_FIELDS.get(entity, ()))
        | set(KIND_ONLY_FIELDS.get(entity, ()))
    )


def test_every_snapshot_key_is_displayed_or_named() -> None:
    """BT-8: a future snapshot key fails here instead of disappearing."""
    master = PartNumber(part_number="PN-1", name="Shaft", current_revision="B", erp_id="E")
    work_order = WorkOrder(
        work_order_number="WO-1",
        received_date=datetime.date(2026, 10, 1),
        due_date=None,
        status="OPEN",
    )
    demand = WorkOrderDemand(
        work_order_id=1,
        part_number="PN-1",
        request_type="NEW",
        requested_quantity=3,
        due_date=None,
        job_numbers=["J"],
        requester=None,
        reason=None,
        notes=None,
    )
    assert set(master_snapshot(master)) | {"image"} <= _covered(PN)
    assert set(work_order_snapshot(work_order)) <= _covered(WO)
    assert set(demand_snapshot(demand)) | {"priority_rank"} <= _covered(WOD)
    # The displayed, identity and kind-only keys of one entity never overlap.
    for entity in (PN, WO, WOD):
        groups = [TRAIL_FIELDS, IDENTITY_FIELDS, KIND_ONLY_FIELDS]
        keys = [key for group in groups for key in group.get(entity, ())]
        assert len(keys) == len(set(keys)), entity


def test_the_wire_field_literal_is_every_displayed_field() -> None:
    """BT-8: the response's ``field`` Literal names exactly the displayed fields."""
    literal = typing.get_args(AuditTrailChangeResponse.model_fields["field"].annotation)
    displayed = {field for fields in TRAIL_FIELDS.values() for field in fields}
    assert set(literal) == displayed


@pytest.mark.parametrize(
    ("action", "before", "after", "shifted"),
    [
        # The target of the action.
        ("ADD", None, 4, False),
        ("REMOVE", 2, None, False),
        ("AUTO_REMOVE", 2, None, False),
        ("LINE_DELETE", 2, None, False),
        ("MOVE_UP", 3, 2, False),
        ("MOVE_DOWN", 2, 3, False),
        # A line that only closed a gap or was displaced.
        ("ADD", 2, 3, True),
        ("REMOVE", 3, 2, True),
        ("AUTO_REMOVE", 3, 2, True),
        ("LINE_DELETE", 3, 2, True),
        ("MOVE_UP", 2, 3, True),
        ("MOVE_DOWN", 3, 2, True),
        # Whole-reorder actions never name their target on a row.
        ("DRAG", 4, 1, False),
        ("DRAG", 1, 2, False),
        ("UNDO", None, 2, False),
        ("REDO", 2, None, False),
        ("SOMETHING_NEW", 1, 2, False),
        (None, 1, 2, False),
    ],
)
def test_a_line_that_only_shifted(
    action: str | None, before: int | None, after: int | None, shifted: bool
) -> None:
    """BT-13: a rank row of a line the action did not target is marked
    shifted, whatever the action the whole change carries."""
    assert audit_trail.shifted_by_another_entry(action, before, after) is shifted
