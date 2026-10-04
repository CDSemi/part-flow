"""Unit tests for the Hot list ordering rules (PROJECT_PROFILE §21 Priority Management)."""

import pytest

from app.domain.hot_list import (
    HotListAction,
    Insert,
    InvalidHotListChangeError,
    Move,
    Remove,
    fingerprint,
    interpret_change,
    require_action,
    target_ranks,
)

A, B, C, D = 11, 22, 33, 44


def _matches(action: HotListAction, expected: list[int], new: list[int]) -> bool:
    try:
        require_action(action, interpret_change(expected, new), expected_length=len(expected))
    except InvalidHotListChangeError:
        return False
    return True


class TestInterpretation:
    def test_an_adjacent_swap_has_both_move_interpretations(self) -> None:
        assert set(interpret_change([A, B], [B, A])) == {Move(A, 0, 1), Move(B, 1, 0)}

    def test_an_adjacent_swap_is_accepted_as_every_move_and_history_action(self) -> None:
        for action in (
            HotListAction.MOVE_UP,
            HotListAction.MOVE_DOWN,
            HotListAction.DRAG,
            HotListAction.UNDO,
            HotListAction.REDO,
        ):
            assert _matches(action, [A, B], [B, A]), action
        assert not _matches(HotListAction.ADD, [A, B], [B, A])
        assert not _matches(HotListAction.REMOVE, [A, B], [B, A])

    def test_a_non_adjacent_move_is_unique(self) -> None:
        assert interpret_change([A, B, C], [B, C, A]) == (Move(A, 0, 2),)
        assert interpret_change([A, B, C, D], [D, A, B, C]) == (Move(D, 3, 0),)

    def test_insert_and_remove(self) -> None:
        assert interpret_change([], [A]) == (Insert(A, 0),)
        assert interpret_change([A, B], [A, C, B]) == (Insert(C, 1),)
        assert interpret_change([A], []) == (Remove(A, 0),)
        assert interpret_change([A, B, C], [A, C]) == (Remove(B, 1),)

    def test_one_entry_list(self) -> None:
        assert _matches(HotListAction.REMOVE, [A], [])
        assert _matches(HotListAction.ADD, [A], [A, B])
        assert _matches(HotListAction.UNDO, [], [A])

    @pytest.mark.parametrize(
        ("expected", "new"),
        [
            pytest.param([A, B], [A, B], id="empty-delta"),
            pytest.param([], [], id="empty-lists"),
            pytest.param([A, A], [A], id="duplicate-expected"),
            pytest.param([A, B], [A, B, A], id="duplicate-new"),
            pytest.param([A, B], [A, B, C, D], id="two-inserts"),
            pytest.param([A, B, C], [A], id="two-removals"),
            pytest.param([A, B, C], [C, B, A], id="two-moves"),
            pytest.param([A, B], [A, C], id="replacement"),
            pytest.param([A, B], [B, A, C], id="move-and-insert"),
        ],
    )
    def test_anything_but_one_single_entry_delta_is_refused(
        self, expected: list[int], new: list[int]
    ) -> None:
        with pytest.raises(InvalidHotListChangeError):
            interpret_change(expected, new)


class TestActions:
    def test_move_up_and_down_are_exactly_one_position(self) -> None:
        assert _matches(HotListAction.MOVE_UP, [A, B, C], [A, C, B])
        assert _matches(HotListAction.MOVE_DOWN, [A, B, C], [B, A, C])
        # A move by two positions is a DRAG, never a MOVE_UP / MOVE_DOWN.
        assert not _matches(HotListAction.MOVE_UP, [A, B, C], [C, A, B])
        assert not _matches(HotListAction.MOVE_DOWN, [A, B, C], [B, C, A])
        assert _matches(HotListAction.DRAG, [A, B, C], [C, A, B])

    def test_add_inserts_only_at_the_bottom(self) -> None:
        assert _matches(HotListAction.ADD, [A, B], [A, B, C])
        assert not _matches(HotListAction.ADD, [A, B], [C, A, B])
        assert not _matches(HotListAction.ADD, [A, B], [A, C, B])
        # A history step may re-insert anywhere.
        assert _matches(HotListAction.UNDO, [A, B], [C, A, B])
        assert _matches(HotListAction.REDO, [A, B], [A, C, B])

    def test_remove_is_never_a_move_or_an_add(self) -> None:
        assert not _matches(HotListAction.DRAG, [A, B], [A])
        assert not _matches(HotListAction.ADD, [A, B], [A])
        assert not _matches(HotListAction.REMOVE, [A], [A, B])


class TestRanksAndFingerprint:
    def test_target_ranks_are_dense_from_one(self) -> None:
        assert target_ranks([C, A, B]) == {C: 1, A: 2, B: 3}
        assert target_ranks([]) == {}

    def test_fingerprint_is_deterministic_and_covers_every_field(self) -> None:
        base = fingerprint(HotListAction.DRAG, [A, B, C], [C, A, B])
        assert base == fingerprint(HotListAction.DRAG, [A, B, C], [C, A, B])
        assert base != fingerprint(HotListAction.UNDO, [A, B, C], [C, A, B])
        assert base != fingerprint(HotListAction.DRAG, [A, C, B], [C, A, B])
        assert base != fingerprint(HotListAction.DRAG, [A, B, C], [A, C, B])
