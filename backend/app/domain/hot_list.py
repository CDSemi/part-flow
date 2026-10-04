"""Hot list ordering rules (Phase 12 — PROJECT_PROFILE §21 Priority Management).

The Hot list is the ranked order of Hot Work Order Demand: rank 1 is
the highest priority and the ranked demands always carry exactly the
ranks 1..N (invariant H1). Every accepted change is ONE single-entry
delta against the full order the manager confirmed against — one
entry added at the bottom, one entry removed, or one entry moved —
and the order the change produces is renumbered densely.

This module owns the pure part of that rule and is deliberately
framework-independent: it classifies a submitted ``expected → new``
pair of demand-id orders into its possible single-entry
interpretations, checks that the named action is one of them, derives
the dense target ranks, and computes the deterministic idempotency
fingerprint (SLICE1_DATA_MODEL §14). Locking, eligibility and
persistence belong to the Application command
(``app.application.hot_list``).

Only an adjacent swap is ambiguous: ``[A, B] → [B, A]`` is both
"B moves up" and "A moves down", so the classifier returns every
interpretation and an action matches when ANY of them fits.
"""

import hashlib
import json
from collections.abc import Sequence
from enum import StrEnum
from typing import NamedTuple


class HotListAction(StrEnum):
    """The manager's intent behind one Hot list change (GUI_DESIGN §8)."""

    ADD = "ADD"
    REMOVE = "REMOVE"
    MOVE_UP = "MOVE_UP"
    MOVE_DOWN = "MOVE_DOWN"
    DRAG = "DRAG"
    UNDO = "UNDO"
    REDO = "REDO"


class InvalidHotListChangeError(ValueError):
    """The submitted orders are not one single-entry change of the list."""


class Insert(NamedTuple):
    """Demand ``id`` joins the list at ``position`` (0-based)."""

    id: int
    position: int


class Remove(NamedTuple):
    """Demand ``id`` leaves the list from ``position`` (0-based)."""

    id: int
    position: int


class Move(NamedTuple):
    """Demand ``id`` moves from ``from_index`` to ``to_index`` (0-based)."""

    id: int
    from_index: int
    to_index: int


Delta = Insert | Remove | Move


def _require_unique(order: Sequence[int], label: str) -> None:
    if len(set(order)) != len(order):
        raise InvalidHotListChangeError(f"The {label} order lists a demand more than once.")


def _without(order: Sequence[int], demand_id: int) -> list[int]:
    return [value for value in order if value != demand_id]


def interpret_change(expected: Sequence[int], new: Sequence[int]) -> tuple[Delta, ...]:
    """Every single-entry interpretation of ``expected → new``.

    - Insert x at i: ``new`` without x equals ``expected``, x not in it;
    - Remove x: ``expected`` without x equals ``new``;
    - Move x from i to j (i ≠ j): both lists hold the same ids and are
      equal once x is taken out of each.

    Raises ``InvalidHotListChangeError`` on a duplicate id, an empty
    delta, or a change that is not exactly one of the above.
    """
    _require_unique(expected, "expected")
    _require_unique(new, "new")
    if list(expected) == list(new):
        raise InvalidHotListChangeError("The new order equals the expected order.")
    interpretations: list[Delta] = []
    expected_ids, new_ids = set(expected), set(new)
    if len(new) == len(expected) + 1:
        added = [value for value in new if value not in expected_ids]
        if len(added) == 1 and _without(new, added[0]) == list(expected):
            interpretations.append(Insert(added[0], list(new).index(added[0])))
    elif len(new) == len(expected) - 1:
        removed = [value for value in expected if value not in new_ids]
        if len(removed) == 1 and _without(expected, removed[0]) == list(new):
            interpretations.append(Remove(removed[0], list(expected).index(removed[0])))
    elif new_ids == expected_ids:
        for from_index, demand_id in enumerate(expected):
            to_index = list(new).index(demand_id)
            if from_index != to_index and _without(expected, demand_id) == _without(new, demand_id):
                interpretations.append(Move(demand_id, from_index, to_index))
    if not interpretations:
        raise InvalidHotListChangeError("The change is not a single add, remove or move.")
    return tuple(interpretations)


def _fits(action: HotListAction, delta: Delta, expected_length: int) -> bool:
    if action is HotListAction.ADD:
        # Add-at-bottom is the only insert a new change makes.
        return isinstance(delta, Insert) and delta.position == expected_length
    if action is HotListAction.REMOVE:
        return isinstance(delta, Remove)
    if action is HotListAction.MOVE_UP:
        return isinstance(delta, Move) and delta.to_index == delta.from_index - 1
    if action is HotListAction.MOVE_DOWN:
        return isinstance(delta, Move) and delta.to_index == delta.from_index + 1
    if action is HotListAction.DRAG:
        return isinstance(delta, Move)
    # UNDO / REDO re-apply any one single-entry delta of the history.
    return True


def require_action(
    action: HotListAction, interpretations: Sequence[Delta], *, expected_length: int
) -> Delta:
    """The interpretation the action names, or ``InvalidHotListChangeError``.

    ADD is an insert at the bottom; REMOVE a removal; MOVE_UP / MOVE_DOWN
    a move by exactly one position; DRAG any move; UNDO / REDO any
    interpretation. ``expected_length`` is the length of the expected
    order — the bottom position an ADD must insert at.
    """
    for delta in interpretations:
        if _fits(action, delta, expected_length):
            return delta
    raise InvalidHotListChangeError(f"The change does not match the action {action}.")


def target_ranks(new_order: Sequence[int]) -> dict[int, int]:
    """The dense ranks 1..N the new order assigns (H1)."""
    return {demand_id: index + 1 for index, demand_id in enumerate(new_order)}


def fingerprint(action: HotListAction, expected: Sequence[int], new: Sequence[int]) -> str:
    """Deterministic canonical hash of the normalized change (SLICE1 §14)."""
    normalized = {
        "action": str(action),
        "expected_order": list(expected),
        "new_order": list(new),
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
