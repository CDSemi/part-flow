"""File-based import of Work Orders (Phase 15 slices 1 and 2).

A prepared CSV (UTF-8) or Excel (.xlsx) file creates Work Orders with
their demand lines through the one existing write path
(:func:`app.application.work_orders.create_work_order`), and changes
the Open and Released Work Orders it lists through the one existing
edit path (:func:`app.application.work_orders.update_work_order`). The format
adapters (``app.api.work_order_import_files``) turn the bytes into an
:class:`ImportSheet` of row-numbered raw cells; everything about the
CONTENT is decided here, once for both formats (PROJECT_PROFILE §13
Source-System Mapping):

- the header contract — the fixed columns :data:`IMPORT_COLUMNS`
  (matched case-insensitively, surrounding whitespace ignored), a
  template column named twice, the columns of a raw BOM export (the
  file is refused) and every other column (ignored and listed);
- the limits — :data:`MAX_IMPORT_DATA_ROWS` data rows per file and
  :data:`MAX_IMPORT_LINES_PER_WORK_ORDER` lines per Work Order;
- cell conversion and validation, collecting EVERY error of a row and
  reusing the manual intake validators (``canonical_part_number``,
  ``validated_quantity``) — one rule source;
- grouping by the trimmed Work Order Number, which every row needs: a
  number with spaces around it is a row error (it is stored exactly as
  written), and a row without a usable number blocks the whole import;
- classification — ``WILL_CREATE``; for an existing number either the
  change list of an Open or Released Work Order (``WILL_UPDATE``:
  changed quantities never below the released/allocated floor, set due
  dates, added Job Numbers, lines added to an Open Work Order — a
  blank cell and a saved line missing from the file keep the saved
  values, and no line is ever removed), ``EXISTS`` (nothing to change,
  or a completed Work Order, which is never changed), or ``REFUSED``
  (any row error, or a change an edit rule refuses, refuses the Work
  Order whole) — and the commit, ONE transaction per Work Order in
  file order: valid Work Orders commit, refused ones are listed with
  their reasons.

The preview (Check file) reads only: no lock, no write. It digests the
change list it shows into ``update_token``; the commit applies updates
only when the confirmation it receives equals its own recomputed
digest — otherwise every update of the file is refused per Work Order
and the creates still commit (Phase 15 S2 SPEC D6). Each update also
carries the state token of the values it was planned on, compared by
``update_work_order`` under its own locks (U3).

The commit holds no lock of its own; each Work Order's transaction is
exactly the manual create's or Save's, and it ends — commit or
rollback — before the next begins, so two Work Orders' locks are never
held together. The Work Order Number is the idempotency key: importing
the same file again reports every created or changed Work Order
``EXISTS`` and writes nothing, which is also the recovery after a lost
response or a crash mid-import.

This module knows no file format, no transport and no permission: the
adapters own the bytes, the routes own the check token, the
confirmation header and the keys (:attr:`ImportPlan.write_kinds` says
what the content writes).
"""

import dataclasses
import datetime
import hashlib
import json
import logging
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application import work_orders
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.application.part_numbers import canonical_part_number
from app.application.work_orders import WorkOrderState
from app.domain.enums import WorkOrderStatus
from app.infrastructure.models import PartNumber, WorkOrder

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The shared contract (Phase 15 S1 SPEC §3.1, §8)
# ---------------------------------------------------------------------------

COLUMN_WORK_ORDER_NUMBER: Final = "Work Order Number"
COLUMN_PART_NUMBER: Final = "Part Number"
COLUMN_REQUESTED_QUANTITY: Final = "Requested Quantity"
COLUMN_JOB_NUMBER: Final = "Job Number"
COLUMN_DUE_DATE: Final = "Due Date"

#: The template's header row, in template order (OD-15-2).
IMPORT_COLUMNS: Final = (
    COLUMN_WORK_ORDER_NUMBER,
    COLUMN_PART_NUMBER,
    COLUMN_REQUESTED_QUANTITY,
    COLUMN_JOB_NUMBER,
    COLUMN_DUE_DATE,
)
REQUIRED_IMPORT_COLUMNS: Final = (
    COLUMN_WORK_ORDER_NUMBER,
    COLUMN_PART_NUMBER,
    COLUMN_REQUESTED_QUANTITY,
)

#: Data rows one file may hold (OD-15-10).
MAX_IMPORT_DATA_ROWS: Final = 2000
#: Spreadsheet columns read, A–BL (OD-S1-15).
MAX_IMPORT_COLUMNS: Final = 64
#: Characters per Work Order Number, Part Number and Job Number cell (OD-S1-11).
MAX_IMPORT_TEXT_LENGTH: Final = 200
#: Lines one imported Work Order may hold (OD-S1-13): bounds the PN
#: advisory locks one transaction takes.
MAX_IMPORT_LINES_PER_WORK_ORDER: Final = 500

#: Header names of a source BOM export (OD-15-3): the file is refused.
BOM_SIGNAL_COLUMNS: Final = (
    "Quantity",
    "Type",
    "Ref Designator",
    "Attribute",
    "Issued",
    "Shelf",
    "Cost",
    "Open/Closed",
)

#: Spreadsheet error values, refused as text in any column (OD-S1-12).
SPREADSHEET_ERROR_LITERALS: Final = (
    "#NULL!",
    "#DIV/0!",
    "#VALUE!",
    "#REF!",
    "#NAME?",
    "#NUM!",
    "#N/A",
    "#GETTING_DATA",
    "#SPILL!",
    "#CALC!",
    "#FIELD!",
    "#BLOCKED!",
    "#CONNECT!",
    "#BUSY!",
    "#UNKNOWN!",
    "#PYTHON!",
)

#: The audit metadata of an imported Work Order's ``CREATED`` row (OD-15-12),
#: and of every demand row an import update appends (Phase 15 S2 D3).
IMPORT_AUDIT_METADATA: Final[Mapping[str, Any]] = {"intake": {"channel": "FILE_IMPORT"}}


@dataclass(frozen=True)
class FormattedNumber:
    """XLSX number cell whose number format is neither General nor Text (@):
    the text Excel displays may differ from the stored value."""

    value: int | float
    number_format: str


@dataclass(frozen=True)
class CellError:
    """XLSX cell holding a spreadsheet error (data_type 'e'), e.g. '#N/A'."""

    text: str


# CSV cells are always str ('' when empty). XLSX: openpyxl stored values,
# except int/float with a non-General, non-'@' number format →
# FormattedNumber, and error cells → CellError.
CellValue = (
    str
    | int
    | float
    | bool
    | datetime.date
    | datetime.datetime
    | datetime.time
    | datetime.timedelta
    | FormattedNumber
    | CellError
    | None
)


def is_blank_cell(value: CellValue) -> bool:
    """``None``, or text that is empty after ``strip()``."""
    return value is None or (isinstance(value, str) and not value.strip())


class ImportFormat(StrEnum):
    CSV = "CSV"
    XLSX = "XLSX"


@dataclass(frozen=True)
class ImportRecord:
    #: 1-based spreadsheet row.
    row: int
    #: Positional; may be shorter or longer than the header.
    cells: tuple[CellValue, ...]


@dataclass(frozen=True)
class ImportSheet:
    file_format: ImportFormat
    #: NON-BLANK records only, in row order; at most
    #: ``MAX_IMPORT_DATA_ROWS + 2`` (header + limit + 1).
    records: tuple[ImportRecord, ...]
    #: Lowercase hex SHA-256 of the exact uploaded bytes.
    check_token: str
    #: XLSX: title of the worksheet read; CSV: None.
    worksheet: str | None


#: The adapters stop reading once this many non-blank records are kept:
#: enough for the header, the row limit and one row past it (F12).
MAX_IMPORT_RECORDS: Final = MAX_IMPORT_DATA_ROWS + 2

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


class ImportOutcome(StrEnum):
    WILL_CREATE = "WILL_CREATE"
    WILL_UPDATE = "WILL_UPDATE"
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    EXISTS = "EXISTS"
    REFUSED = "REFUSED"


@dataclass(frozen=True)
class RowError:
    row: int | None
    column: str | None
    message: str


@dataclass(frozen=True)
class ImportLine:
    row: int
    #: Canonical PN.
    part_number: str
    requested_quantity: int
    due_date: datetime.date | None
    #: Verbatim; None when the cell is blank.
    job_number: str | None


class ImportChangeKind(StrEnum):
    ADD_LINE = "ADD_LINE"
    EDIT_LINE = "EDIT_LINE"


@dataclass(frozen=True)
class ImportChange:
    """One change an import makes to an existing Work Order (S2 §3.2).

    The value pairs are ``(before, after)``; a field an ``EDIT_LINE``
    leaves unchanged is ``None``.
    """

    kind: ImportChangeKind
    row: int
    #: Canonical PN.
    part_number: str
    #: EDIT_LINE only.
    demand_id: int | None
    #: ADD_LINE whose PN has no master yet.
    new_part_number: bool
    quantity: tuple[int | None, int] | None
    due_date: tuple[datetime.date | None, datetime.date | None] | None
    job_numbers: tuple[tuple[str, ...], tuple[str, ...]] | None
    #: EDIT_LINE: a ranked line lowered to its allocated quantity.
    leaves_hot_list: bool


@dataclass(frozen=True)
class WorkOrderImportEntry:
    #: The trimmed group key.
    work_order_number: str
    #: Every row of the group, ascending.
    rows: tuple[int, ...]
    outcome: ImportOutcome
    #: Empty when REFUSED.
    lines: tuple[ImportLine, ...]
    new_part_numbers: tuple[str, ...]
    #: The lines this import writes without a due date (S2 D8):
    #: WILL_CREATE/CREATED its undated lines, WILL_UPDATE/UPDATED its
    #: undated added lines, EXISTS/REFUSED 0.
    lines_without_due_date: int
    #: EXISTS, CREATED, WILL_UPDATE, UPDATED and REFUSED of an existing
    #: Work Order.
    work_order_id: int | None = None
    #: The derived status (OPEN / RELEASED / COMPLETED): as planned, or
    #: after the save on UPDATED. Not on CREATED.
    existing_status: str | None = None
    #: EXISTS only: completed — whether the file's (PN, quantity) pairs
    #: differ; active — whether saved lines are missing from the file.
    differs_from_file: bool | None = None
    #: REFUSED only (non-empty).
    errors: tuple[RowError, ...] = ()
    #: WILL_UPDATE / UPDATED only (non-empty), in file order.
    changes: tuple[ImportChange, ...] | None = None
    #: WILL_UPDATE / UPDATED only: every line becomes fully allocated.
    completes_work_order: bool | None = None
    #: WILL_UPDATE / UPDATED and EXISTS of an active Work Order: the
    #: saved lines the file does not list (kept), in demand-id order.
    lines_not_in_file: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ImportReport:
    dry_run: bool
    file_format: ImportFormat
    worksheet: str | None
    check_token: str
    commit_blocked: bool
    rows_read: int
    empty_rows_ignored: int
    ignored_columns: tuple[str, ...]
    work_orders: tuple[WorkOrderImportEntry, ...]
    unassigned_rows: tuple[RowError, ...]
    #: Preview: the digest of every change the typed confirmation shows,
    #: or None when nothing is updated. Result: always None.
    update_token: str | None = None

    @property
    def lines_without_due_date(self) -> int:
        """Total of the lines this import writes without a due date (D8)."""
        return sum(entry.lines_without_due_date for entry in self.work_orders)

    def count(self, outcome: ImportOutcome) -> int:
        return sum(1 for entry in self.work_orders if entry.outcome == outcome)


# ---------------------------------------------------------------------------
# Copy (SPEC §4.4)
# ---------------------------------------------------------------------------

NO_HEADER_MESSAGE: Final = (
    "The file has no header row. The first row must name the columns Work Order Number,"
    " Part Number and Requested Quantity (Job Number and Due Date are optional)."
)
NO_DATA_ROWS_MESSAGE: Final = "The file has a header row but no data rows."
TOO_MANY_ROWS_MESSAGE: Final = (
    f"The file has more than {MAX_IMPORT_DATA_ROWS:,} data rows; at most"
    f" {MAX_IMPORT_DATA_ROWS:,} can be imported at once. Split it into smaller files."
)
CONCURRENT_CHANGE_MESSAGE: Final = (
    "This Work Order could not be created because another change happened at the same"
    " time. Check the file again."
)
#: U4: the commit's change list differs from the confirmed one (S2 D6).
UNCONFIRMED_UPDATE_MESSAGE: Final = (
    "Work Orders in this file changed after the changes were confirmed, so this Work Order"
    " was not changed. Check the file again and confirm the new changes."
)


def _released_add_message(part_number: str) -> str:
    """U1: lines are added only while the Work Order is Open (OD-S2-4)."""
    return (
        f"Part Number {part_number} is not on this Work Order, and the Work Order is Released:"
        " lines can be added only while it is Open. Remove this row from the file."
    )


_QUOTE_LIMIT: Final = 60


def _missing_columns_message(missing: Sequence[str], *, semicolon_hint: bool) -> str:
    message = (
        f"Required column missing: {', '.join(missing)}. The header row must name"
        " Work Order Number, Part Number and Requested Quantity."
    )
    if semicolon_hint:
        message += (
            " The header looks semicolon-separated — save the file as CSV UTF-8 (Comma delimited)."
        )
    return message


def _blocked_message(rows: int) -> str:
    subject = "1 row has" if rows == 1 else f"{rows} rows have"
    return (
        f"{subject} no usable Work Order Number. Add or fix it, or delete those rows,"
        " then check the file again."
    )


def _cell_text(value: CellValue) -> str:
    """The cell as read, for a header name or a quoted message."""
    if isinstance(value, CellError):
        return value.text
    if isinstance(value, FormattedNumber):
        return str(value.value)
    return "" if value is None else str(value)


def _quoted(value: CellValue) -> str:
    text = _cell_text(value).replace("\x00", "\\0")
    if len(text) > _QUOTE_LIMIT:
        return text[:_QUOTE_LIMIT] + "…"
    return text


# ---------------------------------------------------------------------------
# Header (file-level refusals)
# ---------------------------------------------------------------------------

_TEMPLATE_BY_KEY: Final = {column.casefold(): column for column in IMPORT_COLUMNS}
_BOM_SIGNAL_KEYS: Final = frozenset(column.casefold() for column in BOM_SIGNAL_COLUMNS)
_ERROR_LITERAL_KEYS: Final = frozenset(literal.casefold() for literal in SPREADSHEET_ERROR_LITERALS)


def column_letter(index: int) -> str:
    """Spreadsheet column letter of a 0-based column index (A, …, Z, AA, …)."""
    letters = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


@dataclass(frozen=True)
class _Header:
    row: int
    #: Template column → 0-based position.
    positions: Mapping[str, int]
    ignored_columns: tuple[str, ...]


def _read_header(header: ImportRecord, data: Sequence[ImportRecord]) -> _Header:
    names = [None if is_blank_cell(cell) else _cell_text(cell).strip() for cell in header.cells]
    bom = [name for name in names if name is not None and name.casefold() in _BOM_SIGNAL_KEYS]
    if bom:
        raise InvalidInputError(
            f"This looks like a BOM export (columns {', '.join(bom)}). Keep only the rows"
            " PartFlow should track and only the template columns, then check the file again."
        )
    positions: dict[str, int] = {}
    for position, name in enumerate(names):
        column = _TEMPLATE_BY_KEY.get(name.casefold()) if name is not None else None
        if column is None:
            continue
        if column in positions:
            raise InvalidInputError(f"Column {column} appears more than once in the header row.")
        positions[column] = position
    missing = [column for column in REQUIRED_IMPORT_COLUMNS if column not in positions]
    if missing:
        written = [name for name in names if name is not None]
        raise InvalidInputError(
            _missing_columns_message(
                missing, semicolon_hint=len(written) == 1 and ";" in written[0]
            )
        )
    width = max([len(names), *(len(record.cells) for record in data)])
    ignored: list[str] = []
    for position in range(width):
        name = names[position] if position < len(names) else None
        if name is not None:
            if name.casefold() not in _TEMPLATE_BY_KEY:
                ignored.append(name)
        elif any(
            position < len(record.cells) and not is_blank_cell(record.cells[position])
            for record in data
        ):
            ignored.append(f"Column {column_letter(position)} (no header)")
    return _Header(row=header.row, positions=positions, ignored_columns=tuple(ignored))


# ---------------------------------------------------------------------------
# Cell conversion (row errors)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Text:
    """A text cell: its value, or the one error that stops further checks."""

    value: str | None
    error: RowError | None = None
    #: R3 / R12: the cell holds no text at all.
    unreadable: bool = False


def _not_text_kind(value: CellValue) -> str | None:
    if isinstance(value, bool):
        return "a true/false value"
    if isinstance(value, FormattedNumber):
        return "a number shown through a number format"
    if isinstance(value, datetime.date):  # datetime included
        return "a date"
    if isinstance(value, datetime.time | datetime.timedelta):
        return "a time"
    if isinstance(value, int):
        return None if abs(value) < 10**15 else "a number with decimals or more than 15 digits"
    if isinstance(value, float):
        return "a number with decimals or more than 15 digits"
    return None


def _spreadsheet_error(column: str, value: CellValue, row: int) -> RowError:
    return RowError(
        row,
        column,
        f"{column} holds the spreadsheet error {_quoted(value)}. Fix the formula or type the"
        " value.",
    )


def _text_cell(value: CellValue, column: str, row: int) -> _Text:
    """One rule for the three text columns (OD-S1-2, OD-S1-11, OD-S1-12)."""
    if is_blank_cell(value):
        return _Text(None)
    if isinstance(value, CellError):
        return _Text(None, _spreadsheet_error(column, value, row), unreadable=True)
    kind = _not_text_kind(value)
    if kind is not None:
        return _Text(
            None,
            RowError(
                row,
                column,
                f"{column} must be text, not {kind}. Format the column as Text and retype the"
                " value.",
            ),
            unreadable=True,
        )
    text = value if isinstance(value, str) else str(value)
    if text.strip().casefold() in _ERROR_LITERAL_KEYS:
        return _Text(None, _spreadsheet_error(column, text.strip(), row), unreadable=True)
    if "\x00" in text:
        return _Text(text, RowError(row, column, f"{column} must not contain a NUL character."))
    if len(text) > MAX_IMPORT_TEXT_LENGTH:
        return _Text(
            text,
            RowError(row, column, f"{column} is longer than {MAX_IMPORT_TEXT_LENGTH} characters."),
        )
    return _Text(text)


_QUANTITY_TEXT: Final = re.compile(r"[0-9]+")
_ISO_DATE_TEXT: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
#: Digits a valid quantity can have (2,147,483,647), checked before
#: ``int()``, which refuses very long digit strings.
_MAX_QUANTITY_DIGITS: Final = 10


def _quantity_cell(value: CellValue, row: int) -> tuple[int | None, RowError | None]:
    column = COLUMN_REQUESTED_QUANTITY
    if is_blank_cell(value):
        return None, RowError(row, column, f"{column} is required.")
    if isinstance(value, CellError):
        return None, _spreadsheet_error(column, value, row)
    invalid = RowError(
        row,
        column,
        f'{column} "{_quoted(value)}" must be a positive whole number no greater than'
        " 2,147,483,647.",
    )
    number = value.value if isinstance(value, FormattedNumber) else value
    quantity: int | None = None
    if isinstance(number, str):
        if _QUANTITY_TEXT.fullmatch(number) and len(number.lstrip("0")) <= _MAX_QUANTITY_DIGITS:
            quantity = int(number)
    elif isinstance(number, bool):
        quantity = None
    elif isinstance(number, int):
        quantity = number
    elif isinstance(number, float) and number.is_integer():
        quantity = int(number)
    if quantity is None:
        return None, invalid
    try:
        return work_orders.validated_quantity(quantity), None
    except InvalidInputError:
        return None, invalid


def _due_date_cell(value: CellValue, row: int) -> tuple[datetime.date | None, RowError | None]:
    column = COLUMN_DUE_DATE
    if is_blank_cell(value):
        return None, None
    if isinstance(value, CellError):
        return None, _spreadsheet_error(column, value, row)
    if isinstance(value, datetime.datetime):
        if value.time() == datetime.time() and value.tzinfo is None:
            return value.date(), None
    elif isinstance(value, datetime.date):
        return value, None
    elif isinstance(value, str) and _ISO_DATE_TEXT.fullmatch(value):
        try:
            return datetime.date.fromisoformat(value), None
        except ValueError:
            pass
    return None, RowError(
        row, column, f'{column} "{_quoted(value)}" must be a date written as YYYY-MM-DD.'
    )


# ---------------------------------------------------------------------------
# Analysis: header, rows, groups
# ---------------------------------------------------------------------------


@dataclass
class _Group:
    key: str
    rows: list[int] = dataclasses.field(default_factory=list)
    lines: list[ImportLine] = dataclasses.field(default_factory=list)
    errors: list[RowError] = dataclasses.field(default_factory=list)
    #: Canonical PN → the first row that carries it (OD-15-7).
    part_number_rows: dict[str, int] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True)
class _Analysis:
    sheet: ImportSheet
    rows_read: int
    empty_rows_ignored: int
    ignored_columns: tuple[str, ...]
    groups: tuple[_Group, ...]
    unassigned_rows: tuple[RowError, ...]

    @property
    def commit_blocked(self) -> bool:
        return bool(self.unassigned_rows)


def _cell(record: ImportRecord, header: _Header, column: str) -> CellValue:
    position = header.positions.get(column)
    if position is None or position >= len(record.cells):
        return None
    return record.cells[position]


def _analyse(sheet: ImportSheet) -> _Analysis:
    """Header, limits, row validation and grouping; reads nothing.

    Raises ``InvalidInputError`` for a file-level refusal (F8–F13).
    """
    if not sheet.records:
        raise InvalidInputError(NO_HEADER_MESSAGE)
    header_record, *data = sheet.records
    header = _read_header(header_record, data)
    if not data:
        raise InvalidInputError(NO_DATA_ROWS_MESSAGE)
    if len(data) > MAX_IMPORT_DATA_ROWS:
        raise InvalidInputError(TOO_MANY_ROWS_MESSAGE)

    groups: dict[str, _Group] = {}
    unassigned: list[RowError] = []
    for record in data:
        row = record.row
        errors: list[RowError] = []
        number = _text_cell(
            _cell(record, header, COLUMN_WORK_ORDER_NUMBER), COLUMN_WORK_ORDER_NUMBER, row
        )
        if number.error is not None:
            errors.append(number.error)
        elif number.value is None:
            errors.append(RowError(row, COLUMN_WORK_ORDER_NUMBER, "Work Order Number is missing."))
        elif number.value != number.value.strip():
            errors.append(
                RowError(
                    row,
                    COLUMN_WORK_ORDER_NUMBER,
                    f'Work Order Number "{_quoted(number.value)}" has spaces before or after it.'
                    " Remove them — the number is stored exactly as written.",
                )
            )

        part_number: str | None = None
        pn_text = _text_cell(_cell(record, header, COLUMN_PART_NUMBER), COLUMN_PART_NUMBER, row)
        if pn_text.error is not None:
            errors.append(pn_text.error)
        elif pn_text.value is None:
            errors.append(RowError(row, COLUMN_PART_NUMBER, "Part Number is required."))
        else:
            try:
                part_number = canonical_part_number(pn_text.value)
            except InvalidInputError as exc:
                errors.append(RowError(row, COLUMN_PART_NUMBER, exc.message))

        quantity, quantity_error = _quantity_cell(
            _cell(record, header, COLUMN_REQUESTED_QUANTITY), row
        )
        if quantity_error is not None:
            errors.append(quantity_error)
        job = _text_cell(_cell(record, header, COLUMN_JOB_NUMBER), COLUMN_JOB_NUMBER, row)
        if job.error is not None:
            errors.append(job.error)
        due_date, due_date_error = _due_date_cell(_cell(record, header, COLUMN_DUE_DATE), row)
        if due_date_error is not None:
            errors.append(due_date_error)

        if number.value is None or number.unreadable:
            # No group key: the row blocks the commit until fixed.
            unassigned.extend(errors)
            continue
        key = number.value.strip()
        group = groups.get(key)
        if group is None:
            group = groups[key] = _Group(key)
        group.rows.append(row)
        if part_number is not None:
            first_row = group.part_number_rows.setdefault(part_number, row)
            if first_row != row:
                errors.append(
                    RowError(
                        row,
                        COLUMN_PART_NUMBER,
                        f"Part Number {part_number} is already on row {first_row} of this"
                        " Work Order. List each Part Number once per Work Order.",
                    )
                )
        if errors:
            group.errors.extend(errors)
        else:
            assert part_number is not None and quantity is not None
            group.lines.append(
                ImportLine(
                    row=row,
                    part_number=part_number,
                    requested_quantity=quantity,
                    due_date=due_date,
                    job_number=job.value,
                )
            )

    for group in groups.values():
        if len(group.rows) > MAX_IMPORT_LINES_PER_WORK_ORDER:
            group.errors.append(
                RowError(
                    None,
                    None,
                    f"This Work Order has {len(group.rows):,} lines; at most"
                    f" {MAX_IMPORT_LINES_PER_WORK_ORDER} can be imported for one Work Order."
                    f" Import the first {MAX_IMPORT_LINES_PER_WORK_ORDER} and add the rest in"
                    " the Work Order.",
                )
            )

    last_row = data[-1].row
    return _Analysis(
        sheet=sheet,
        rows_read=len(data),
        empty_rows_ignored=last_row - header.row - len(data),
        ignored_columns=header.ignored_columns,
        groups=tuple(groups.values()),
        unassigned_rows=tuple(unassigned),
    )


# ---------------------------------------------------------------------------
# The change list of an existing active Work Order (pure; S2 SPEC §3.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupPlan:
    """What a file group changes on one active Work Order."""

    #: In file order; empty when nothing changes.
    changes: tuple[ImportChange, ...]
    #: U1 / U2 row errors; any refuses the Work Order whole.
    errors: tuple[RowError, ...]
    #: Saved lines the file does not list (kept), in demand-id order.
    lines_not_in_file: tuple[str, ...]
    #: Informational: the save's completion judgement on the planned values.
    completes_work_order: bool


def plan_changes(
    state: WorkOrderState, file_lines: Sequence[ImportLine], known_part_numbers: Collection[str]
) -> GroupPlan:
    """The change list of the file's lines against ONE saved state.

    Matched by canonical PN. An existing line: a different quantity is
    an edit judged by the quantity floor (U2); a due date in the file
    that differs is set; a Job Number not yet listed is appended — a
    blank cell keeps the saved value (OD-S2-2, OD-S2-3). A PN the Work
    Order does not have is an added line while it is Open and a row
    error (U1) once it is Released (OD-S2-4). Saved lines missing from
    the file are kept and listed, never a change (OD-S2-1).
    """
    saved = {line.part_number: line for line in state.lines}
    planned_quantity = {line.id: line.requested_quantity for line in state.lines}
    changes: list[ImportChange] = []
    errors: list[RowError] = []
    for line in file_lines:
        demand = saved.get(line.part_number)
        if demand is None:
            if state.status == WorkOrderStatus.RELEASED:
                errors.append(
                    RowError(line.row, COLUMN_PART_NUMBER, _released_add_message(line.part_number))
                )
                continue
            changes.append(
                ImportChange(
                    kind=ImportChangeKind.ADD_LINE,
                    row=line.row,
                    part_number=line.part_number,
                    demand_id=None,
                    new_part_number=line.part_number not in known_part_numbers,
                    quantity=(None, line.requested_quantity),
                    due_date=(None, line.due_date),
                    job_numbers=((), (line.job_number,) if line.job_number is not None else ()),
                    leaves_hot_list=False,
                )
            )
            continue
        quantity: tuple[int | None, int] | None = None
        leaves_hot_list = False
        if line.requested_quantity != demand.requested_quantity:
            try:
                work_orders.check_quantity_floor(
                    demand.part_number,
                    line.requested_quantity,
                    demand.requested_quantity,
                    demand.released_quantity,
                    demand.allocated_quantity,
                )
            except ConflictError as exc:
                errors.append(RowError(line.row, COLUMN_REQUESTED_QUANTITY, exc.message))
                continue
            quantity = (demand.requested_quantity, line.requested_quantity)
            planned_quantity[demand.id] = line.requested_quantity
            leaves_hot_list = demand.ranked and line.requested_quantity <= demand.allocated_quantity
        due_date = (
            (demand.due_date, line.due_date)
            if line.due_date is not None and line.due_date != demand.due_date
            else None
        )
        job_numbers = (
            (demand.job_numbers, (*demand.job_numbers, line.job_number))
            if line.job_number is not None and line.job_number not in demand.job_numbers
            else None
        )
        if quantity is not None or due_date is not None or job_numbers is not None:
            changes.append(
                ImportChange(
                    kind=ImportChangeKind.EDIT_LINE,
                    row=line.row,
                    part_number=demand.part_number,
                    demand_id=demand.id,
                    new_part_number=False,
                    quantity=quantity,
                    due_date=due_date,
                    job_numbers=job_numbers,
                    leaves_hot_list=leaves_hot_list,
                )
            )
    in_file = {line.part_number for line in file_lines}
    completes = (
        any(
            change.kind is ImportChangeKind.EDIT_LINE and change.quantity is not None
            for change in changes
        )
        and all(change.kind is ImportChangeKind.EDIT_LINE for change in changes)
        and all(planned_quantity[line.id] <= line.allocated_quantity for line in state.lines)
    )
    return GroupPlan(
        changes=tuple(changes),
        errors=tuple(errors),
        lines_not_in_file=tuple(
            line.part_number for line in state.lines if line.part_number not in in_file
        ),
        completes_work_order=completes,
    )


def _iso(value: datetime.date | None) -> str | None:
    return value.isoformat() if value is not None else None


def change_payload(change: ImportChange) -> dict[str, Any]:
    """One change as JSON-ready values (the wire shape, S2 SPEC §4.2)."""
    return {
        "kind": change.kind.value,
        "row": change.row,
        "part_number": change.part_number,
        "demand_id": change.demand_id,
        "new_part_number": change.new_part_number,
        "requested_quantity": (
            {"before": change.quantity[0], "after": change.quantity[1]}
            if change.quantity is not None
            else None
        ),
        "due_date": (
            {"before": _iso(change.due_date[0]), "after": _iso(change.due_date[1])}
            if change.due_date is not None
            else None
        ),
        "job_numbers": (
            {"before": list(change.job_numbers[0]), "after": list(change.job_numbers[1])}
            if change.job_numbers is not None
            else None
        ),
        "leaves_hot_list": change.leaves_hot_list,
    }


def update_token_of(entries: Iterable[WorkOrderImportEntry]) -> str | None:
    """The digest of everything the typed confirmation shows (S2 SPEC §3.5).

    Per ``WILL_UPDATE`` entry in order: its Work Order, number and
    status, every change with its values and consequences, the
    completion and the kept lines — not the full saved state, so
    activity that changes nothing shown keeps the confirmation valid.
    ``None`` when nothing is updated.
    """
    payload = [
        {
            "work_order_id": entry.work_order_id,
            "work_order_number": entry.work_order_number,
            "existing_status": entry.existing_status,
            "changes": [change_payload(change) for change in entry.changes or ()],
            "completes_work_order": entry.completes_work_order,
            "lines_not_in_file": list(entry.lines_not_in_file or ()),
        }
        for entry in entries
        if entry.outcome == ImportOutcome.WILL_UPDATE
    ]
    if not payload:
        return None
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class _PlannedUpdate:
    """The one ``update_work_order`` call a ``WILL_UPDATE`` group makes."""

    work_order_id: int
    state_token: str
    line_edits: tuple[Mapping[str, Any], ...]
    new_lines: tuple[Mapping[str, Any], ...]


def _planned_update(state: WorkOrderState, changes: Sequence[ImportChange]) -> _PlannedUpdate:
    """Only the changed fields of each edited line, every added line."""
    line_edits: list[Mapping[str, Any]] = []
    new_lines: list[Mapping[str, Any]] = []
    for change in changes:
        if change.kind is ImportChangeKind.ADD_LINE:
            assert change.quantity is not None
            new_lines.append(
                {
                    "part_number": change.part_number,
                    "requested_quantity": change.quantity[1],
                    "due_date": change.due_date[1] if change.due_date is not None else None,
                    "job_numbers": (
                        list(change.job_numbers[1]) if change.job_numbers is not None else []
                    ),
                }
            )
            continue
        edit: dict[str, Any] = {"id": change.demand_id}
        if change.quantity is not None:
            edit["requested_quantity"] = change.quantity[1]
        if change.due_date is not None:
            edit["due_date"] = change.due_date[1]
        if change.job_numbers is not None:
            # The hashed saved list plus one value: a concurrent Job
            # Number change fails the state token, never overwritten.
            edit["job_numbers"] = list(change.job_numbers[1])
        line_edits.append(edit)
    quantity_edited = {
        change.demand_id
        for change in changes
        if change.demand_id is not None and change.quantity is not None
    }
    return _PlannedUpdate(
        work_order_id=state.work_order_id,
        state_token=work_orders.work_order_state_token(state, quantity_edited),
        line_edits=tuple(line_edits),
        new_lines=tuple(new_lines),
    )


# ---------------------------------------------------------------------------
# Classification (reads only)
# ---------------------------------------------------------------------------


def _refused(group: _Group, errors: Iterable[RowError]) -> WorkOrderImportEntry:
    return WorkOrderImportEntry(
        work_order_number=group.key,
        rows=tuple(group.rows),
        outcome=ImportOutcome.REFUSED,
        lines=(),
        new_part_numbers=(),
        lines_without_due_date=0,
        errors=tuple(errors),
    )


def _refused_existing(
    entry: WorkOrderImportEntry, errors: Iterable[RowError]
) -> WorkOrderImportEntry:
    """An existing Work Order refused: keeps its id and status (S2 D7)."""
    return dataclasses.replace(
        entry,
        outcome=ImportOutcome.REFUSED,
        lines=(),
        new_part_numbers=(),
        lines_without_due_date=0,
        differs_from_file=None,
        errors=tuple(errors),
        changes=None,
        completes_work_order=None,
        lines_not_in_file=None,
    )


def _lines_without_due_date(group: _Group) -> int:
    return sum(1 for line in group.lines if line.due_date is None)


def _differs(group: _Group, stored: Iterable[tuple[str, int]]) -> bool:
    """S1: whether the file's (PN, quantity) pairs differ from the saved ones."""
    return {(line.part_number, line.requested_quantity) for line in group.lines} != set(stored)


def _existing(session: Session, group: _Group) -> WorkOrderImportEntry | None:
    """The S1 EXISTS entry of a number created since the plan (the commit
    fallback of a create — never an update: no confirmation covered it)."""
    hits = work_orders.list_work_orders(session, number=group.key)
    if not hits:
        return None
    summary = hits[0]
    detail = work_orders.get_work_order(session, summary.work_order.id)
    return WorkOrderImportEntry(
        work_order_number=group.key,
        rows=tuple(group.rows),
        outcome=ImportOutcome.EXISTS,
        lines=tuple(group.lines),
        new_part_numbers=(),
        lines_without_due_date=0,
        work_order_id=summary.work_order.id,
        existing_status=summary.status,
        differs_from_file=_differs(
            group, ((demand.part_number, demand.requested_quantity) for demand in detail.demands)
        ),
    )


def _classify_existing(
    group: _Group, state: WorkOrderState, known: Collection[str], claimed: set[str]
) -> tuple[WorkOrderImportEntry, _PlannedUpdate | None]:
    """The entry of a group whose Work Order exists, from ONE saved state.

    The change list and the state token are computed from the same
    ``state`` object, so they certify identical values (CH-8). A PN the
    group adds is claimed as new on the first group using it.
    """

    def entry(outcome: ImportOutcome, **fields: Any) -> WorkOrderImportEntry:
        return WorkOrderImportEntry(
            work_order_number=group.key,
            rows=tuple(group.rows),
            outcome=outcome,
            work_order_id=state.work_order_id,
            existing_status=state.status,
            **fields,
        )

    if state.completed:
        # A completed Work Order is never changed (OD-15-4/5).
        return entry(
            ImportOutcome.EXISTS,
            lines=tuple(group.lines),
            new_part_numbers=(),
            lines_without_due_date=0,
            differs_from_file=_differs(
                group, ((line.part_number, line.requested_quantity) for line in state.lines)
            ),
        ), None
    plan = plan_changes(state, group.lines, known)
    if plan.errors:
        return entry(
            ImportOutcome.REFUSED,
            lines=(),
            new_part_numbers=(),
            lines_without_due_date=0,
            errors=plan.errors,
        ), None
    if not plan.changes:
        return entry(
            ImportOutcome.EXISTS,
            lines=tuple(group.lines),
            new_part_numbers=(),
            lines_without_due_date=0,
            differs_from_file=bool(plan.lines_not_in_file),
            lines_not_in_file=plan.lines_not_in_file,
        ), None
    new: list[str] = []
    for change in plan.changes:
        if change.new_part_number and change.part_number not in claimed:
            claimed.add(change.part_number)
            new.append(change.part_number)
    return entry(
        ImportOutcome.WILL_UPDATE,
        lines=tuple(group.lines),
        new_part_numbers=tuple(new),
        lines_without_due_date=sum(
            1
            for change in plan.changes
            if change.kind is ImportChangeKind.ADD_LINE
            and (change.due_date is None or change.due_date[1] is None)
        ),
        changes=plan.changes,
        completes_work_order=plan.completes_work_order,
        lines_not_in_file=plan.lines_not_in_file,
    ), _planned_update(state, plan.changes)


def _classify(
    session: Session, analysis: _Analysis
) -> tuple[list[WorkOrderImportEntry], dict[str, _PlannedUpdate]]:
    """``REFUSED`` / ``EXISTS`` / ``WILL_UPDATE`` / ``WILL_CREATE`` per
    group, in group order, with the update call of each ``WILL_UPDATE``.

    A refused group reads nothing. The existing numbers are found with
    ONE read over every valid group's number (verbatim equality, all
    history); each of those groups reads its Work Order's state once.
    """
    valid = [group for group in analysis.groups if not group.errors]
    existing_ids: dict[str, int] = (
        {
            number: work_order_id
            for number, work_order_id in session.execute(
                select(WorkOrder.work_order_number, WorkOrder.id).where(
                    WorkOrder.work_order_number.in_([group.key for group in valid])
                )
            )
            if number is not None
        }
        if valid
        else {}
    )
    states = {
        group.key: work_orders.read_work_order_state(session, existing_ids[group.key])
        for group in valid
        if group.key in existing_ids
    }

    # Every PN the import may write as a first use: the lines of new Work
    # Orders and the lines an Open/Released Work Order does not have yet.
    candidates: set[str] = set()
    for group in valid:
        state = states.get(group.key)
        saved = {line.part_number for line in state.lines} if state is not None else set()
        candidates.update(line.part_number for line in group.lines if line.part_number not in saved)
    known = (
        set(
            session.scalars(
                select(PartNumber.part_number).where(PartNumber.part_number.in_(candidates))
            )
        )
        if candidates
        else set()
    )

    claimed: set[str] = set()
    by_key: dict[str, WorkOrderImportEntry] = {}
    updates: dict[str, _PlannedUpdate] = {}
    for group in valid:
        state = states.get(group.key)
        if state is not None:
            entry, update = _classify_existing(group, state, known, claimed)
            by_key[group.key] = entry
            if update is not None:
                updates[group.key] = update
            continue
        new = []
        for line in group.lines:
            if line.part_number not in known and line.part_number not in claimed:
                claimed.add(line.part_number)
                new.append(line.part_number)
        by_key[group.key] = WorkOrderImportEntry(
            work_order_number=group.key,
            rows=tuple(group.rows),
            outcome=ImportOutcome.WILL_CREATE,
            lines=tuple(group.lines),
            new_part_numbers=tuple(new),
            lines_without_due_date=_lines_without_due_date(group),
        )
    entries = [
        _refused(group, group.errors) if group.errors else by_key[group.key]
        for group in analysis.groups
    ]
    return entries, updates


def _report(
    analysis: _Analysis,
    entries: Sequence[WorkOrderImportEntry],
    *,
    dry_run: bool,
    update_token: str | None = None,
) -> ImportReport:
    sheet = analysis.sheet
    return ImportReport(
        dry_run=dry_run,
        file_format=sheet.file_format,
        worksheet=sheet.worksheet,
        check_token=sheet.check_token,
        commit_blocked=analysis.commit_blocked,
        rows_read=analysis.rows_read,
        empty_rows_ignored=analysis.empty_rows_ignored,
        ignored_columns=analysis.ignored_columns,
        work_orders=tuple(entries),
        unassigned_rows=analysis.unassigned_rows,
        update_token=update_token,
    )


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


class ImportWriteKind(StrEnum):
    """What an import's content writes; the routes map each to its key."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"


@dataclass(frozen=True)
class ImportPlan:
    """The read-only classification of one file (S2 D5).

    ``report`` is preview-shaped (``dry_run`` True, ``update_token``
    set when anything is updated); ``write_kinds`` holds ``CREATE`` iff
    a Work Order will be created and ``UPDATE`` iff one will be changed.
    The rest is this module's own, for :func:`import_work_orders`.
    """

    report: ImportReport
    write_kinds: frozenset[ImportWriteKind]
    analysis: _Analysis = dataclasses.field(repr=False)
    updates: Mapping[str, _PlannedUpdate] = dataclasses.field(repr=False)


def plan_work_order_import(session: Session, sheet: ImportSheet, *, commit: bool) -> ImportPlan:
    """Analyse and classify the file; reads only, no lock, no write.

    For the commit (``commit=True``) a row without a usable Work Order
    Number refuses the whole import before any classification read
    (S1 C2).
    """
    analysis = _analyse(sheet)
    if commit and analysis.commit_blocked:
        rows = {error.row for error in analysis.unassigned_rows}
        raise InvalidInputError(_blocked_message(len(rows)))
    try:
        entries, updates = _classify(session, analysis)
    finally:
        session.rollback()
    report = _report(analysis, entries, dry_run=True, update_token=update_token_of(entries))
    write_kinds = {
        kind
        for kind, outcome in (
            (ImportWriteKind.CREATE, ImportOutcome.WILL_CREATE),
            (ImportWriteKind.UPDATE, ImportOutcome.WILL_UPDATE),
        )
        if report.count(outcome)
    }
    return ImportPlan(
        report=report, write_kinds=frozenset(write_kinds), analysis=analysis, updates=updates
    )


def preview_work_order_import(session: Session, sheet: ImportSheet) -> ImportReport:
    """Check file: the dry run. No lock, no write, no flush."""
    return plan_work_order_import(session, sheet, commit=False).report


def _commit_group(
    session: Session, group: _Group, planned: WorkOrderImportEntry, *, actor_user_id: int
) -> WorkOrderImportEntry:
    """Create one Work Order in its own transaction (the manual write path)."""
    try:
        detail = work_orders.create_work_order(
            session,
            work_order_number=group.key,
            lines=[
                {
                    "part_number": line.part_number,
                    "requested_quantity": line.requested_quantity,
                    "due_date": line.due_date,
                    "job_numbers": [line.job_number] if line.job_number is not None else [],
                }
                for line in group.lines
            ],
            actor_user_id=actor_user_id,
            audit_metadata=IMPORT_AUDIT_METADATA,
        )
    except ConflictError:
        # Another writer created the number since the check (the
        # pre-check or the unique index). Nothing of this group stays.
        session.rollback()
        try:
            existing = _existing(session, group)
        finally:
            session.rollback()
        if existing is not None:
            return existing
        return _refused(group, [RowError(None, None, CONCURRENT_CHANGE_MESSAGE)])
    except InvalidInputError as exc:
        # Releases the PN advisory locks this group already took.
        session.rollback()
        return _refused(group, [RowError(None, None, exc.message)])
    return dataclasses.replace(
        planned, outcome=ImportOutcome.CREATED, work_order_id=detail.work_order.id
    )


def _commit_update(
    session: Session, planned: WorkOrderImportEntry, update: _PlannedUpdate, *, actor_user_id: int
) -> WorkOrderImportEntry:
    """Change one Work Order in its own transaction (the manual Save's path).

    Only the confirmed changes are sent, under the state token they
    were planned on: a Work Order changed since (U3), completed since,
    released past the planned floor, or missing a planned line is
    refused with the write path's message and nothing of it is written.
    """
    try:
        detail = work_orders.update_work_order(
            session,
            update.work_order_id,
            line_edits=update.line_edits,
            new_lines=update.new_lines,
            actor_user_id=actor_user_id,
            audit_metadata=IMPORT_AUDIT_METADATA,
            expected_state_token=update.state_token,
        )
    except (ConflictError, InvalidInputError, NotFoundError) as exc:
        # Releases the PN advisory, Hot advisory and row locks this
        # group took while the import continues.
        session.rollback()
        return _refused_existing(planned, [RowError(None, None, exc.message)])
    return dataclasses.replace(
        planned, outcome=ImportOutcome.UPDATED, existing_status=detail.status
    )


def import_work_orders(
    session: Session, plan: ImportPlan, *, actor_user_id: int, confirm_token: str | None
) -> ImportReport:
    """Import: one transaction per ``WILL_CREATE`` / ``WILL_UPDATE`` Work
    Order in file order, on a plan made with ``commit=True``.

    Updates are applied only when ``confirm_token`` equals the plan's own
    ``update_token`` — the change list the user confirmed is the one
    this commit found. Otherwise every update is refused per Work Order
    (U4, nothing written to them) while the creates still commit (S2
    D6). Any unexpected failure propagates: the Work Orders already
    committed stay, and checking and importing the same file again
    completes the rest (the number is the idempotency key).
    """
    planned = plan.report
    confirmed = planned.update_token is None or confirm_token == planned.update_token
    groups = {group.key: group for group in plan.analysis.groups}
    entries: list[WorkOrderImportEntry] = []
    unconfirmed = 0
    for entry in planned.work_orders:
        if entry.outcome == ImportOutcome.WILL_CREATE:
            entries.append(
                _commit_group(
                    session, groups[entry.work_order_number], entry, actor_user_id=actor_user_id
                )
            )
        elif entry.outcome == ImportOutcome.WILL_UPDATE and not confirmed:
            unconfirmed += 1
            entries.append(
                _refused_existing(entry, [RowError(None, None, UNCONFIRMED_UPDATE_MESSAGE)])
            )
        elif entry.outcome == ImportOutcome.WILL_UPDATE:
            entries.append(
                _commit_update(
                    session,
                    entry,
                    plan.updates[entry.work_order_number],
                    actor_user_id=actor_user_id,
                )
            )
        else:
            entries.append(entry)
    report = _report(plan.analysis, entries, dry_run=False)
    logger.info(
        "Work Order import: format=%s rows_read=%d created=%d updated=%d"
        " update_unconfirmed=%d existing=%d refused=%d actor_user_id=%d",
        planned.file_format,
        planned.rows_read,
        report.count(ImportOutcome.CREATED),
        report.count(ImportOutcome.UPDATED),
        unconfirmed,
        report.count(ImportOutcome.EXISTS),
        report.count(ImportOutcome.REFUSED),
        actor_user_id,
    )
    return report


def csv_template_text() -> str:
    """The CSV template's header row (the routes add the UTF-8 BOM)."""
    return ",".join(IMPORT_COLUMNS) + "\r\n"
