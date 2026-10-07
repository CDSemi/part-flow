"""Unit tests for the AssignedRoute adjustment rules (Phase 14 slice 6,
``app.domain.assigned_route``; PROJECT_PROFILE §8.10, §17; OD-P11)."""

import datetime
from typing import NamedTuple

import pytest

from app.domain.assigned_route import (
    StepContent,
    future_part,
    is_unchanged,
    kept_through_sequence,
)


class _Step(NamedTuple):
    id: int
    sequence: int


def _content(area_id: int, duration: datetime.timedelta | None = None) -> StepContent:
    return StepContent(
        area_id=area_id,
        operation_id=area_id * 10,
        expected_duration=duration,
        preferred_machine_id=None,
        instructions=None,
    )


def test_kept_through_is_the_highest_referenced_sequence() -> None:
    assert kept_through_sequence([1]) == 1
    assert kept_through_sequence([3, 1, 2]) == 3
    assert kept_through_sequence(iter([2, 2])) == 2


def test_kept_through_refuses_a_route_nothing_references() -> None:
    with pytest.raises(ValueError):
        kept_through_sequence([])


def test_future_part_is_the_steps_after_the_boundary_in_sequence_order() -> None:
    steps = [_Step(13, 3), _Step(11, 1), _Step(14, 4), _Step(12, 2)]
    assert future_part(steps, lambda step: step.sequence, 2) == [_Step(13, 3), _Step(14, 4)]
    # The boundary itself is past.
    assert future_part(steps, lambda step: step.sequence, 3) == [_Step(14, 4)]
    # After the last step: no future part.
    assert future_part(steps, lambda step: step.sequence, 4) == []
    assert future_part([], lambda step: step, 0) == []


def test_is_unchanged_compares_step_by_step() -> None:
    hour = datetime.timedelta(hours=1)
    current = [_content(1), _content(2, hour)]
    assert is_unchanged(current, [_content(1), _content(2, hour)])
    assert is_unchanged([], [])
    # Reordered.
    assert not is_unchanged(current, [_content(2, hour), _content(1)])
    # Length differs.
    assert not is_unchanged(current, [_content(1)])
    assert not is_unchanged([], [_content(1)])
    # Duration None vs a value.
    assert not is_unchanged(current, [_content(1), _content(2)])
    assert not is_unchanged([_content(1)], [_content(1, hour)])
    # Any field counts.
    assert not is_unchanged([_content(1)], [_content(1)._replace(instructions="Deburr")])
    assert not is_unchanged([_content(1)], [_content(1)._replace(preferred_machine_id=7)])
