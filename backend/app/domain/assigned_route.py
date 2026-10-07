"""AssignedRoute adjustment rules (Phase 14 slice 6 — PROJECT_PROFILE §8.10, §17).

A PLANNED Quantity Flow follows its own AssignedRoute snapshot. Past
steps are immutable (owner decision OD-P11): a step any PartMovement of
the flow references — reversed Movements included — and every step
before it stay as they are. Only the **future** steps, those after the
highest referenced sequence (the *kept-through sequence*), may be
replaced, as a whole, by an authorized and reasoned adjustment.

This module owns the pure part of that rule and is deliberately
framework-independent: the kept-through boundary, the future part of an
ordered step list, and whether a requested tail changes anything.
Locking, validation of the referenced rows, persistence, idempotency
and the audit row belong to the Application command
(``app.application.route_adjustments``).
"""

import datetime
from collections.abc import Callable, Iterable, Sequence
from typing import NamedTuple


class StepContent(NamedTuple):
    """The comparable content of one route step (no id, no sequence)."""

    area_id: int
    operation_id: int | None
    expected_duration: datetime.timedelta | None
    preferred_machine_id: int | None
    instructions: str | None


def kept_through_sequence(referenced_sequences: Iterable[int]) -> int:
    """The highest referenced step sequence: every step up to it is past.

    A PLANNED flow always references at least one step (its RECEIVED,
    SPLIT or MERGED Movement), so an empty input is a defect.
    """
    sequences = list(referenced_sequences)
    if not sequences:
        raise ValueError("A Planned flow always references at least one step of its route.")
    return max(sequences)


def future_part[T](
    steps: Sequence[T], sequence_of: Callable[[T], int], kept_through: int
) -> list[T]:
    """The steps after ``kept_through``, in sequence order."""
    return sorted(
        (step for step in steps if sequence_of(step) > kept_through),
        key=sequence_of,
    )


def is_unchanged(current: Sequence[StepContent], new: Sequence[StepContent]) -> bool:
    """True when ``new`` is exactly ``current``: same length, equal step by step."""
    return len(current) == len(new) and all(
        old == step for old, step in zip(current, new, strict=True)
    )
