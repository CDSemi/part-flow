"""Unit tests for the Hot list ordering rules (PROJECT_PROFILE §21 Priority Management)."""

import itertools

import pytest

from app.domain.hot_list import (
    HotListAction,
    Insert,
    InvalidHotListChangeError,
    Move,
    Remove,
    close_gaps,
    fingerprint,
    interpret_change,
    removal_shift_scope,
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


class TestRemovalOutsideTheCommand:
    """The rank arithmetic of the automatic removal and the confirmed line
    deletion (Phase 12 follow-up, OD1 / OD3)."""

    def test_close_gaps_removes_the_middle_entry(self) -> None:
        assert close_gaps({A: 1, B: 2, C: 3}, [B]) == {B: None, C: 2}

    def test_close_gaps_removes_two_entries(self) -> None:
        assert close_gaps({A: 1, B: 2, C: 3, D: 4}, [A, C]) == {A: None, C: None, B: 1, D: 2}

    def test_close_gaps_removes_the_last_entry_without_a_shift(self) -> None:
        assert close_gaps({A: 1, B: 2, C: 3}, [C]) == {C: None}

    def test_close_gaps_is_relative_on_non_dense_ranks(self) -> None:
        # Nothing above the first removed rank is touched.
        assert close_gaps({A: 1, B: 3, C: 4}, [B]) == {B: None, C: 3}

    def test_close_gaps_refuses_an_unranked_removal(self) -> None:
        with pytest.raises(ValueError):
            close_gaps({A: 1}, [B])

    def test_shift_scope_is_empty_without_a_ranked_candidate(self) -> None:
        assert removal_shift_scope({A: 1, B: 2}, [C]) == frozenset()
        assert removal_shift_scope({}, [A]) == frozenset()

    def test_shift_scope_excludes_candidates_and_higher_ranks(self) -> None:
        ranks = {A: 1, B: 2, C: 3, D: 4}
        assert removal_shift_scope(ranks, [B]) == frozenset({C, D})
        assert removal_shift_scope(ranks, [B, D]) == frozenset({C})

    def test_a_ranked_candidate_that_stays_can_shift(self) -> None:
        # The counterexample of the review: the scope is empty, yet B
        # shifts when only A is removed — B is a command line, held by
        # the caller.
        ranks = {A: 1, B: 2}
        assert removal_shift_scope(ranks, [A, B]) == frozenset()
        assert close_gaps(ranks, [A]) == {A: None, B: 1}

    def test_every_change_is_a_ranked_candidate_or_in_the_shift_scope(self) -> None:
        """Exhaustive on small inputs: up to 5 ranked ids with any strictly
        increasing ranks from 1..6 (dense and non-dense), one unranked id,
        every candidate set and every removed subset of the ranked
        candidates."""
        unranked = 99
        for size in range(6):
            ids = list(range(1, size + 1))
            for values in itertools.combinations(range(1, 7), size):
                ranks = dict(zip(ids, values, strict=True))
                pool = [*ids, unranked]
                for count in range(len(pool) + 1):
                    for candidates in itertools.combinations(pool, count):
                        ranked = [c for c in candidates if c in ranks]
                        scope = removal_shift_scope(ranks, candidates)
                        assert not scope & set(candidates)
                        for removed_count in range(len(ranked) + 1):
                            for removed in itertools.combinations(ranked, removed_count):
                                changed = close_gaps(ranks, removed)
                                assert set(changed) <= set(ranked) | scope
                                after = {**ranks, **changed}
                                kept = [r for r in after.values() if r is not None]
                                assert len(kept) == len(set(kept))
                                assert all(r >= 1 for r in kept)
                                assert {i for i, r in changed.items() if r is None} == set(removed)
