"""Pure tests of the Work Order import analysis (Phase 15 slice 1, AN-1…AN-14, BS-1).

``_analyse`` runs on hand-built :class:`ImportSheet` values — no file
format, no database: the header contract, the limits, cell conversion
and validation, and grouping are one rule set for CSV and XLSX.
"""

import ast
import datetime
from pathlib import Path

import pytest

from app.application.errors import InvalidInputError
from app.application.work_order_import import (
    IMPORT_COLUMNS,
    MAX_IMPORT_DATA_ROWS,
    CellError,
    CellValue,
    FormattedNumber,
    ImportFormat,
    ImportRecord,
    ImportSheet,
    RowError,
    _analyse,
    _Analysis,
    _Group,
    column_letter,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_APP_DIR = _BACKEND_DIR / "app"
_HEADER: tuple[CellValue, ...] = IMPORT_COLUMNS


def _sheet(*rows: tuple[int, tuple[CellValue, ...]]) -> ImportSheet:
    return ImportSheet(
        file_format=ImportFormat.CSV,
        records=tuple(ImportRecord(row=row, cells=cells) for row, cells in rows),
        check_token="0" * 64,
        worksheet=None,
    )


def _numbered(*rows: tuple[CellValue, ...]) -> ImportSheet:
    """Header on row 1, then ``rows`` on rows 2, 3, …"""
    return _sheet((1, _HEADER), *((index, cells) for index, cells in enumerate(rows, start=2)))


def _row(
    number: CellValue = "WO-1",
    part_number: CellValue = "PN-1",
    quantity: CellValue = "5",
    job: CellValue = "",
    due: CellValue = "",
) -> tuple[CellValue, ...]:
    return (number, part_number, quantity, job, due)


def _refusal(sheet: ImportSheet) -> str:
    with pytest.raises(InvalidInputError) as caught:
        _analyse(sheet)
    return caught.value.message


def _group(analysis: _Analysis, key: str) -> _Group:
    return next(group for group in analysis.groups if group.key == key)


def _errors(analysis: _Analysis, key: str) -> list[RowError]:
    return _group(analysis, key).errors


def _messages(errors: list[RowError]) -> list[str]:
    return [error.message for error in errors]


# ---------------------------------------------------------------------------
# AN-1 … AN-4 — the header
# ---------------------------------------------------------------------------


def test_header_matches_case_and_whitespace_insensitively() -> None:
    """AN-1."""
    analysis = _analyse(
        _sheet(
            (1, ("  work order NUMBER ", "PART number", "requested quantity")),
            (2, ("WO-1", "pn-1", "3")),
        )
    )
    assert analysis.rows_read == 1
    assert analysis.ignored_columns == ()
    line = _group(analysis, "WO-1").lines[0]
    assert (line.row, line.part_number, line.requested_quantity) == (2, "PN-1", 3)
    assert (line.due_date, line.job_number) == (None, None)


def test_leading_blank_records_before_the_header() -> None:
    """AN-2: the adapters keep only non-blank records, so the header is
    the first kept record whatever its row number."""
    analysis = _analyse(_sheet((4, _HEADER), (5, _row()), (7, _row("WO-2"))))
    assert [line.row for group in analysis.groups for line in group.lines] == [5, 7]
    assert analysis.empty_rows_ignored == 1


def test_header_refusals() -> None:
    """AN-3."""
    missing = _refusal(_sheet((1, ("Work Order Number", "Part Number")), (2, ("WO-1", "PN"))))
    assert missing == (
        "Required column missing: Requested Quantity. The header row must name"
        " Work Order Number, Part Number and Requested Quantity."
    )
    semicolon = _refusal(
        _sheet(
            (1, ("Work Order Number;Part Number;Requested Quantity",)),
            (2, ("WO-1;PN;5",)),
        )
    )
    assert semicolon.startswith(
        "Required column missing: Work Order Number, Part Number, Requested Quantity."
    )
    assert semicolon.endswith(
        " The header looks semicolon-separated — save the file as CSV UTF-8 (Comma delimited)."
    )
    duplicate = _refusal(_sheet((1, (*_HEADER, "part number")), (2, _row())))
    assert duplicate == "Column Part Number appears more than once in the header row."
    bom_only = _refusal(_sheet((1, ("Part Number", "Quantity")), (2, ("PN", "5"))))
    assert bom_only == (
        "This looks like a BOM export (columns Quantity). Keep only the rows PartFlow should"
        " track and only the template columns, then check the file again."
    )
    bom_two = _refusal(_sheet((1, (*_HEADER, "type", "Shelf")), (2, _row())))
    assert "(columns type, Shelf)" in bom_two


def test_unknown_and_unnamed_columns_are_ignored_and_listed() -> None:
    """AN-4."""
    analysis = _analyse(
        _sheet(
            (1, (*_HEADER[:4], "Revision", "")),
            (2, ("WO-1", "PN-1", "5", "", "B", "")),
            (3, ("WO-2", "PN-1", "5", "", "", "note")),
        )
    )
    assert analysis.ignored_columns == ("Revision", "Column F (no header)")
    beyond = _analyse(_sheet((1, _HEADER), (2, (*_row(), "", "", "x"))))
    assert beyond.ignored_columns == ("Column H (no header)",)
    assert [column_letter(index) for index in (0, 25, 26, 63)] == ["A", "Z", "AA", "BL"]


# ---------------------------------------------------------------------------
# AN-5 … AN-8 — cell conversion
# ---------------------------------------------------------------------------


def test_every_error_of_a_row_is_collected() -> None:
    """AN-5."""
    analysis = _analyse(_numbered(_row(part_number="", quantity="x", due="06/10/2026")))
    errors = _errors(analysis, "WO-1")
    assert [(error.row, error.column) for error in errors] == [
        (2, "Part Number"),
        (2, "Requested Quantity"),
        (2, "Due Date"),
    ]
    assert _messages(errors) == [
        "Part Number is required.",
        'Requested Quantity "x" must be a positive whole number no greater than 2,147,483,647.',
        'Due Date "06/10/2026" must be a date written as YYYY-MM-DD.',
    ]


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "1.5", "1,000", "+5", "５", "2147483648", "9" * 5000, True, 0.5, 0, -3],
)
def test_invalid_quantities_are_refused(value: CellValue) -> None:
    """AN-6 (a 5,000-digit string is refused without ``int()``'s ValueError)."""
    errors = _errors(_analyse(_numbered(_row(quantity=value))), "WO-1")
    assert len(errors) == 1
    assert errors[0].column == "Requested Quantity"
    assert errors[0].message.endswith(
        "must be a positive whole number no greater than 2,147,483,647."
    )


def test_blank_and_error_quantities() -> None:
    """AN-6."""
    assert _messages(_errors(_analyse(_numbered(_row(quantity=" "))), "WO-1")) == [
        "Requested Quantity is required."
    ]
    assert _messages(_errors(_analyse(_numbered(_row(quantity=CellError("#N/A")))), "WO-1")) == [
        "Requested Quantity holds the spreadsheet error #N/A. Fix the formula or type the value."
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("007", 7),
        ("00000000007", 7),
        ("2147483647", 2_147_483_647),
        (5, 5),
        (5.0, 5),
        (FormattedNumber(5, "0"), 5),
    ],
)
def test_valid_quantities(value: CellValue, expected: int) -> None:
    """AN-6."""
    line = _group(_analyse(_numbered(_row(quantity=value))), "WO-1").lines[0]
    assert line.requested_quantity == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-06", datetime.date(2026, 10, 6)),
        ("", None),
        (datetime.date(2026, 7, 24), datetime.date(2026, 7, 24)),
        (datetime.datetime(2026, 7, 24), datetime.date(2026, 7, 24)),
    ],
)
def test_valid_due_dates(value: CellValue, expected: datetime.date | None) -> None:
    """AN-7."""
    line = _group(_analyse(_numbered(_row(due=value))), "WO-1").lines[0]
    assert line.due_date == expected


@pytest.mark.parametrize(
    "value",
    [
        "06/10/2026",
        "20261006",
        "2026-1-6",
        "2026-02-30",
        datetime.datetime(2026, 7, 24, 10, 30),
        45000,
        datetime.time(10, 30),
        FormattedNumber(45000, "dd/mm/yy"),
    ],
)
def test_invalid_due_dates(value: CellValue) -> None:
    """AN-7."""
    errors = _errors(_analyse(_numbered(_row(due=value))), "WO-1")
    assert [
        (error.column, error.message.endswith("must be a date written as YYYY-MM-DD."))
        for error in errors
    ] == [("Due Date", True)]


def test_integer_cells_are_read_as_text_in_text_columns() -> None:
    """AN-8: a General/@ integer cell is the digits Excel shows."""
    analysis = _analyse(_numbered(_row(number=7010, part_number=123456, job=42)))
    line = _group(analysis, "7010").lines[0]
    assert (line.part_number, line.job_number) == ("123456", "42")


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (10**15, "a number with decimals or more than 15 digits"),
        (7010.0, "a number with decimals or more than 15 digits"),
        (1.23456789012346e18, "a number with decimals or more than 15 digits"),
        (7010.5, "a number with decimals or more than 15 digits"),
        (FormattedNumber(7010, "000000"), "a number shown through a number format"),
        (datetime.date(2026, 1, 1), "a date"),
        (datetime.datetime(2026, 1, 1, 8), "a date"),
        (True, "a true/false value"),
        (datetime.time(8, 0), "a time"),
        (datetime.timedelta(hours=8), "a time"),
    ],
)
@pytest.mark.parametrize("column", ["Part Number", "Job Number"])
def test_non_text_cells_are_refused_in_text_columns(
    value: CellValue, kind: str, column: str
) -> None:
    """AN-8: never imported as "7010" / "1234567890123460096"."""
    row = _row(part_number=value) if column == "Part Number" else _row(job=value)
    errors = _errors(_analyse(_numbered(row)), "WO-1")
    assert errors == [
        RowError(
            2,
            column,
            f"{column} must be text, not {kind}. Format the column as Text and retype the value.",
        )
    ]


def test_a_non_text_work_order_number_cell_is_unassigned() -> None:
    """AN-8: no group key, so the row blocks the commit."""
    analysis = _analyse(_numbered(_row(number=7010.0), _row("WO-2")))
    assert analysis.commit_blocked
    assert analysis.unassigned_rows == (
        RowError(
            2,
            "Work Order Number",
            "Work Order Number must be text, not a number with decimals or more than 15 digits."
            " Format the column as Text and retype the value.",
        ),
    )
    assert [group.key for group in analysis.groups] == ["WO-2"]


# ---------------------------------------------------------------------------
# AN-9 … AN-14 — grouping and Work Order rules
# ---------------------------------------------------------------------------


def test_padded_and_missing_work_order_numbers() -> None:
    """AN-9."""
    analysis = _analyse(_numbered(_row("WO1"), _row("WO1 ", part_number="PN-2")))
    assert [group.key for group in analysis.groups] == ["WO1"]
    assert _errors(analysis, "WO1") == [
        RowError(
            3,
            "Work Order Number",
            'Work Order Number "WO1 " has spaces before or after it. Remove them — the number'
            " is stored exactly as written.",
        )
    ]
    assert not analysis.commit_blocked

    blank = _analyse(_numbered(_row(""), _row("   ", part_number=""), _row("WO-3")))
    assert blank.commit_blocked
    assert [(error.row, error.message) for error in blank.unassigned_rows] == [
        (2, "Work Order Number is missing."),
        (3, "Work Order Number is missing."),
        (3, "Part Number is required."),
    ]


def test_a_part_number_twice_in_one_work_order() -> None:
    """AN-10: canonical PNs compared."""
    analysis = _analyse(_numbered(_row(part_number="abc-1"), _row(part_number=" ABC-1 ")))
    assert _errors(analysis, "WO-1") == [
        RowError(
            3,
            "Part Number",
            "Part Number ABC-1 is already on row 2 of this Work Order. List each Part Number"
            " once per Work Order.",
        )
    ]


def test_row_counting_and_limits() -> None:
    """AN-11."""
    gaps = _analyse(_sheet((2, _HEADER), (3, _row()), (6, _row("WO-2")), (8, _row("WO-3"))))
    assert (gaps.rows_read, gaps.empty_rows_ignored) == (3, 3)

    full = _numbered(*(_row(f"WO-{index}") for index in range(MAX_IMPORT_DATA_ROWS)))
    assert _analyse(full).rows_read == MAX_IMPORT_DATA_ROWS
    over = _numbered(*(_row(f"WO-{index}") for index in range(MAX_IMPORT_DATA_ROWS + 1)))
    assert _refusal(over) == (
        "The file has more than 2,000 data rows; at most 2,000 can be imported at once."
        " Split it into smaller files."
    )
    assert _refusal(_sheet((1, _HEADER))) == "The file has a header row but no data rows."
    assert _refusal(_sheet()) == (
        "The file has no header row. The first row must name the columns Work Order Number,"
        " Part Number and Requested Quantity (Job Number and Due Date are optional)."
    )


def test_group_order_is_the_first_row() -> None:
    """AN-12."""
    analysis = _analyse(
        _numbered(
            _row("B"),
            _row("A"),
            _row("B", part_number="PN-2"),
            _row("A", part_number="PN-3"),
        )
    )
    assert [(group.key, group.rows) for group in analysis.groups] == [("B", [2, 4]), ("A", [3, 5])]
    assert [line.row for line in _group(analysis, "B").lines] == [2, 4]


def test_format_neutral_text_guards() -> None:
    """AN-13."""
    analysis = _analyse(
        _numbered(
            _row("WO\x00"),
            _row("WO-2", job="J\x00"),
            _row("WO-3", part_number=CellError("#REF!")),
            _row("WO-4", job=" #n/a "),
        )
    )
    assert _messages(_errors(analysis, "WO\x00")) == [
        "Work Order Number must not contain a NUL character."
    ]
    assert _messages(_errors(analysis, "WO-2")) == ["Job Number must not contain a NUL character."]
    assert _messages(_errors(analysis, "WO-3")) == [
        "Part Number holds the spreadsheet error #REF!. Fix the formula or type the value."
    ]
    assert _messages(_errors(analysis, "WO-4")) == [
        "Job Number holds the spreadsheet error #n/a. Fix the formula or type the value."
    ]

    long = "W" * 201
    too_long = _analyse(
        _numbered(_row(long), _row("WO-5", part_number="P" * 201), _row("WO-6", job="J" * 201))
    )
    assert _messages(_errors(too_long, long)) == [
        "Work Order Number is longer than 200 characters."
    ]
    assert _messages(_errors(too_long, "WO-5")) == ["Part Number is longer than 200 characters."]
    assert _messages(_errors(too_long, "WO-6")) == ["Job Number is longer than 200 characters."]
    fits = _analyse(_numbered(_row("W" * 200, part_number="P" * 200, job="J" * 200)))
    assert fits.groups[0].errors == []
    assert fits.groups[0].lines[0].job_number == "J" * 200


def test_quoted_values_are_truncated() -> None:
    """Messages quote the cell as read, at most 60 characters."""
    errors = _errors(_analyse(_numbered(_row(quantity="x" * 80))), "WO-1")
    assert f'"{"x" * 60}…"' in errors[0].message


def test_lines_per_work_order_limit() -> None:
    """AN-14."""
    rows = [_row("BIG", part_number=f"PN-{index}") for index in range(500)]
    assert _analyse(_numbered(*rows)).groups[0].errors == []
    analysis = _analyse(
        _numbered(_row("SMALL"), *rows, _row("BIG", part_number="PN-500"), _row("OTHER"))
    )
    assert _errors(analysis, "BIG") == [
        RowError(
            None,
            None,
            "This Work Order has 501 lines; at most 500 can be imported for one Work Order."
            " Import the first 500 and add the rest in the Work Order.",
        )
    ]
    assert _errors(analysis, "SMALL") == [] and _errors(analysis, "OTHER") == []


# ---------------------------------------------------------------------------
# BS-1 — what the import modules may know
# ---------------------------------------------------------------------------


def _imports(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def _names(tree: ast.Module) -> set[str]:
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


def test_the_import_application_module_knows_no_format_transport_or_permission() -> None:
    """BS-1."""
    tree = ast.parse((_APP_DIR / "application/work_order_import.py").read_text(encoding="utf-8"))
    imported = _imports(tree)
    for forbidden in ("csv", "openpyxl", "fastapi", "app.application.authorization"):
        assert not {
            module
            for module in imported
            if module == forbidden or module.startswith(f"{forbidden}.")
        }, forbidden
    assert not {"Permission", "User", "authorization"} & _names(tree)

    openpyxl_importers = {
        path.relative_to(_BACKEND_DIR).as_posix()
        for path in sorted(_APP_DIR.rglob("*.py"))
        if any(
            module == "openpyxl" or module.startswith("openpyxl.")
            for module in _imports(ast.parse(path.read_text(encoding="utf-8")))
        )
    }
    assert openpyxl_importers == {"app/api/work_order_import_files.py"}
    files_tree = ast.parse(
        (_APP_DIR / "api/work_order_import_files.py").read_text(encoding="utf-8")
    )
    assert "Permission" not in _names(files_tree)
