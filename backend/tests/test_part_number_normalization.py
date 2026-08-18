"""Unit tests for canonical Part Number normalization (PROJECT_PROFILE §7)."""

import pytest

from app.domain.part_number import InvalidPartNumberError, normalize_part_number


class TestCanonicalization:
    def test_uppercase_input_is_already_canonical(self) -> None:
        assert normalize_part_number("ABC-123") == "ABC-123"

    def test_lowercase_and_mixed_case_normalize_to_uppercase(self) -> None:
        assert normalize_part_number("abc-123") == "ABC-123"
        assert normalize_part_number("AbC-123") == "ABC-123"

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert normalize_part_number(" ABC-123 ") == "ABC-123"
        assert normalize_part_number("\tABC-123\n") == "ABC-123"

    def test_real_multi_segment_shapes_stay_opaque(self) -> None:
        # PN segments are never parsed for business meaning; the string
        # is preserved as-is apart from case normalization.
        for canonical in ("214-406", "78-04-0031", "0455-20-0118-03", "2027-60-8114-00"):
            assert normalize_part_number(canonical) == canonical


class TestRejection:
    def test_empty_input_is_rejected(self) -> None:
        with pytest.raises(InvalidPartNumberError):
            normalize_part_number("")

    def test_whitespace_only_input_is_rejected(self) -> None:
        with pytest.raises(InvalidPartNumberError):
            normalize_part_number("   \t\n")

    @pytest.mark.parametrize("raw", ["ABC 123", "ABC\t123", "ABC\n123", "ABC\u00a0123"])
    def test_internal_whitespace_is_rejected_never_stripped(self, raw: str) -> None:
        with pytest.raises(InvalidPartNumberError):
            normalize_part_number(raw)
