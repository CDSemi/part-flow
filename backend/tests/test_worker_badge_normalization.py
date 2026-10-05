"""Unit tests for the canonical Worker badge rule (PROJECT_PROFILE §10; OD-3).

A badge is canonicalized by trimming surrounding whitespace and
converting to UPPERCASE; the empty, over-long (counted on the canonical
form) and ``PF:``-prefixed canonical values are refused.
"""

import pytest

from app.domain.worker_badge import (
    MAX_BADGE_BARCODE_LENGTH,
    InvalidBadgeBarcodeError,
    normalize_badge_barcode,
)


class TestCanonicalization:
    def test_scanner_line_ending_and_padding_are_trimmed(self) -> None:
        assert normalize_badge_barcode(" 100482\r\n") == "100482"
        assert normalize_badge_barcode("\t100482 \t") == "100482"

    def test_internal_spaces_are_kept(self) -> None:
        assert normalize_badge_barcode("ab 12") == "AB 12"

    def test_letters_are_uppercased(self) -> None:
        assert normalize_badge_barcode("abC1") == "ABC1"
        assert normalize_badge_barcode(" abc1 ") == "ABC1"

    def test_the_full_unicode_uppercase_mapping_applies(self) -> None:
        assert normalize_badge_barcode("straße") == "STRASSE"
        assert normalize_badge_barcode("é1") == "É1"

    @pytest.mark.parametrize("raw", [" 100482\r\n", "abC1", "straße", "ab 12", "ǆ9"])
    def test_normalization_is_idempotent(self, raw: str) -> None:
        canonical = normalize_badge_barcode(raw)
        assert normalize_badge_barcode(canonical) == canonical


class TestEmpty:
    @pytest.mark.parametrize("raw", ["", "   ", "\r\n"])
    def test_empty_canonical_value_is_refused(self, raw: str) -> None:
        with pytest.raises(InvalidBadgeBarcodeError, match="must not be empty"):
            normalize_badge_barcode(raw)


class TestLength:
    def test_the_limit_is_128_characters(self) -> None:
        assert MAX_BADGE_BARCODE_LENGTH == 128

    def test_128_characters_are_accepted(self) -> None:
        assert normalize_badge_barcode("a" * 128) == "A" * 128

    def test_surrounding_whitespace_does_not_count(self) -> None:
        assert normalize_badge_barcode("  " + "a" * 128 + "\r\n") == "A" * 128

    def test_129_characters_are_refused(self) -> None:
        with pytest.raises(InvalidBadgeBarcodeError) as refused:
            normalize_badge_barcode("a" * 129)
        assert str(refused.value) == "A badge barcode must be at most 128 characters."

    def test_the_length_is_counted_on_the_canonical_form(self) -> None:
        # 128 raw characters, 129 canonical: "ß" uppercases to "SS".
        with pytest.raises(InvalidBadgeBarcodeError, match="at most 128 characters"):
            normalize_badge_barcode("a" * 127 + "ß")


class TestPartFlowNamespace:
    @pytest.mark.parametrize("raw", ["PF:WORKER:1", "pf:x", " Pf:abc "])
    def test_pf_prefix_is_refused_in_any_case(self, raw: str) -> None:
        with pytest.raises(InvalidBadgeBarcodeError) as refused:
            normalize_badge_barcode(raw)
        assert str(refused.value) == (
            "A badge barcode cannot start with PF: — that prefix belongs to PartFlow"
            " barcodes. Use the barcode printed on the employee badge."
        )

    @pytest.mark.parametrize(("raw", "canonical"), [("PFX1", "PFX1"), ("P F:1", "P F:1")])
    def test_values_that_only_resemble_the_prefix_are_accepted(
        self, raw: str, canonical: str
    ) -> None:
        assert normalize_badge_barcode(raw) == canonical
