"""Work Order file import endpoints (Phase 15 slices 1 and 2; GUI_DESIGN §11.7).

- ``POST /api/work-orders/import/preview`` — Check file: the dry run
  over the raw file body (``text/csv`` or the ``.xlsx`` media type); no
  lock, no write. The report carries ``check_token``, the SHA-256 of
  the exact bytes checked, ``update_token``, the digest of the changes
  to existing Work Orders the user must confirm, and
  ``required_permissions``, the keys its content needs.
- ``POST /api/work-orders/import`` — Import: the same bytes again with
  ``X-PartFlow-Import-Check: <check_token>`` (missing or malformed →
  422, another file → 409, both before parsing) and, when the check
  found changes, ``X-PartFlow-Import-Confirm: <update_token>`` (a
  malformed one → 422 before parsing); re-validated, then one
  transaction per new or changed Work Order
  (``app.application.work_order_import``).
- ``GET /api/work-orders/import/template.csv`` / ``template.xlsx`` — the
  header row of the fixed columns.

Who may: every route opens for Create and edit Work Orders or Edit Work
Order Demand, judged first — before the media type (415), the body size
(413) or the content (422); the CSRF middleware runs before any of
them. The commit then needs the keys its content writes (Phase 14
OD-P10): creating a Work Order needs ``MANAGE_WORK_ORDERS``, changing
an existing one ``EDIT_WORK_ORDER_DEMAND`` (the Work Order Save's
demand key), judged after the read-only plan and before any write. The
handlers are plain ``def`` routes — FastAPI runs the parsing and the
per-Work-Order transactions in its threadpool — and the body arrives
through the bounded async reader of the image uploads. The audit actor
is the signed-in User.
"""

import hashlib
import re
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel

from app.api import uploads, work_order_import_files
from app.api.authorization import RequireAnyPermission, SignedInDep, actor_of
from app.api.dependencies import SessionDep
from app.api.work_order_import_files import FILE_TOO_LARGE_MESSAGE, MAX_IMPORT_BYTES
from app.application import authorization, work_order_import
from app.application.authentication import Principal
from app.application.errors import (
    PERMISSION_DENIED_MESSAGE,
    ConflictError,
    InvalidInputError,
    PermissionDeniedError,
)
from app.application.work_order_import import (
    ImportChangeKind,
    ImportFormat,
    ImportOutcome,
    ImportPlan,
    ImportReport,
    ImportWriteKind,
    RowError,
    WorkOrderImportEntry,
)
from app.domain.enums import Permission, WorkOrderStatus

router = APIRouter(prefix="/api")

#: Either key opens the import (S2 D4); the content decides what Import needs.
IMPORT_KEYS: Final = (Permission.EDIT_WORK_ORDER_DEMAND, Permission.MANAGE_WORK_ORDERS)
#: The key each kind of write needs (Phase 14 OD-P10 / MA-3).
IMPORT_WRITE_KEYS: Final = {
    ImportWriteKind.CREATE: Permission.MANAGE_WORK_ORDERS,
    ImportWriteKind.UPDATE: Permission.EDIT_WORK_ORDER_DEMAND,
}

ImportReaderDep = Annotated[Principal, Depends(RequireAnyPermission(*IMPORT_KEYS))]


def require_import_access(principal: SignedInDep) -> Principal:
    """The commit's fast gate: a holder of neither import key is refused
    before the body is read (a write: the A2 message, not V-1)."""
    if not any(key in principal.permissions for key in IMPORT_KEYS):
        raise PermissionDeniedError(
            PERMISSION_DENIED_MESSAGE,
            required=tuple(sorted(key.value for key in IMPORT_KEYS)),
            any_of=True,
        )
    return principal


ImportWriterDep = Annotated[Principal, Depends(require_import_access)]

CHECK_HEADER: Final = "X-PartFlow-Import-Check"
CONFIRM_HEADER: Final = "X-PartFlow-Import-Confirm"
CHECK_REQUIRED_MESSAGE: Final = "Check the file before importing it."
CHECK_MISMATCH_MESSAGE: Final = (
    "This is not the file that was checked. Check the file again before importing."
)
#: C5: a confirmation header that is not a digest.
CONFIRM_INVALID_MESSAGE: Final = (
    "The import confirmation is not valid. Check the file again and confirm the changes."
)
_CHECK_TOKEN: Final = re.compile(r"[0-9a-f]{64}")

CSV_TEMPLATE_FILENAME: Final = "partflow-work-order-import.csv"
XLSX_TEMPLATE_FILENAME: Final = "partflow-work-order-import.xlsx"


def import_format(
    content_type: Annotated[str | None, Header(alias="Content-Type")] = None,
) -> ImportFormat:
    """The body's format from its media type; 415 before the body is read."""
    return work_order_import_files.import_format_of(content_type)


async def read_import_body(request: Request) -> bytes:
    return await uploads.read_bounded_body(
        request, limit=MAX_IMPORT_BYTES, too_large_message=FILE_TOO_LARGE_MESSAGE
    )


ImportFormatDep = Annotated[ImportFormat, Depends(import_format)]
ImportBodyDep = Annotated[bytes, Depends(read_import_body)]


# ---------------------------------------------------------------------------
# Response models (SPEC §4.2)
# ---------------------------------------------------------------------------


class RowErrorResponse(BaseModel):
    row: int | None
    column: str | None
    message: str


class ImportLineResponse(BaseModel):
    row: int
    part_number: str
    requested_quantity: int
    due_date: str | None
    job_number: str | None


class QuantityChangeResponse(BaseModel):
    before: int | None
    after: int


class DueDateChangeResponse(BaseModel):
    before: str | None
    after: str | None


class JobNumbersChangeResponse(BaseModel):
    before: list[str]
    after: list[str]


class ImportChangeResponse(BaseModel):
    kind: ImportChangeKind
    row: int
    part_number: str
    demand_id: int | None
    new_part_number: bool
    requested_quantity: QuantityChangeResponse | None
    due_date: DueDateChangeResponse | None
    job_numbers: JobNumbersChangeResponse | None
    leaves_hot_list: bool


class WorkOrderImportEntryResponse(BaseModel):
    work_order_number: str
    rows: list[int]
    outcome: ImportOutcome
    lines: list[ImportLineResponse]
    new_part_numbers: list[str]
    lines_without_due_date: int
    work_order_id: int | None
    existing_status: WorkOrderStatus | None
    differs_from_file: bool | None
    errors: list[RowErrorResponse]
    changes: list[ImportChangeResponse] | None
    completes_work_order: bool | None
    lines_not_in_file: list[str] | None


class _ImportReportBase(BaseModel):
    file_format: ImportFormat
    worksheet: str | None
    check_token: str
    commit_blocked: bool
    rows_read: int
    empty_rows_ignored: int
    ignored_columns: list[str]
    lines_without_due_date: int
    work_orders: list[WorkOrderImportEntryResponse]
    unassigned_rows: list[RowErrorResponse]
    update_token: str | None
    required_permissions: list[Permission]


class ImportPreviewSummary(BaseModel):
    will_create: int
    will_update: int
    existing: int
    refused: int


class ImportResultSummary(BaseModel):
    created: int
    updated: int
    existing: int
    refused: int


class ImportPreviewResponse(_ImportReportBase):
    dry_run: Literal[True]
    summary: ImportPreviewSummary


class ImportResultResponse(_ImportReportBase):
    dry_run: Literal[False]
    summary: ImportResultSummary


def _row_error(error: RowError) -> RowErrorResponse:
    return RowErrorResponse(row=error.row, column=error.column, message=error.message)


def _entry(entry: WorkOrderImportEntry) -> WorkOrderImportEntryResponse:
    return WorkOrderImportEntryResponse(
        work_order_number=entry.work_order_number,
        rows=list(entry.rows),
        outcome=entry.outcome,
        lines=[
            ImportLineResponse(
                row=line.row,
                part_number=line.part_number,
                requested_quantity=line.requested_quantity,
                due_date=line.due_date.isoformat() if line.due_date is not None else None,
                job_number=line.job_number,
            )
            for line in entry.lines
        ],
        new_part_numbers=list(entry.new_part_numbers),
        lines_without_due_date=entry.lines_without_due_date,
        work_order_id=entry.work_order_id,
        existing_status=(
            WorkOrderStatus(entry.existing_status) if entry.existing_status is not None else None
        ),
        differs_from_file=entry.differs_from_file,
        errors=[_row_error(error) for error in entry.errors],
        changes=(
            [
                ImportChangeResponse.model_validate(work_order_import.change_payload(change))
                for change in entry.changes
            ]
            if entry.changes is not None
            else None
        ),
        completes_work_order=entry.completes_work_order,
        lines_not_in_file=(
            list(entry.lines_not_in_file) if entry.lines_not_in_file is not None else None
        ),
    )


def _required_permissions(plan: ImportPlan) -> frozenset[Permission]:
    """The keys the file's content writes need (S2 SPEC §4.1)."""
    return frozenset(IMPORT_WRITE_KEYS[kind] for kind in plan.write_kinds)


def _base_fields(report: ImportReport, required: frozenset[Permission]) -> dict[str, object]:
    return {
        "file_format": report.file_format,
        "worksheet": report.worksheet,
        "check_token": report.check_token,
        "commit_blocked": report.commit_blocked,
        "rows_read": report.rows_read,
        "empty_rows_ignored": report.empty_rows_ignored,
        "ignored_columns": list(report.ignored_columns),
        "lines_without_due_date": report.lines_without_due_date,
        "work_orders": [_entry(entry) for entry in report.work_orders],
        "unassigned_rows": [_row_error(error) for error in report.unassigned_rows],
        "update_token": report.update_token,
        "required_permissions": sorted(required),
    }


def _sheet_of(body: bytes, file_format: ImportFormat, token: str) -> work_order_import.ImportSheet:
    return work_order_import_files.read_import_file(body, file_format, check_token=token)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/work-orders/import/preview")
def preview_work_order_import(
    principal: ImportReaderDep,
    file_format: ImportFormatDep,
    body: ImportBodyDep,
    session: SessionDep,
) -> ImportPreviewResponse:
    work_order_import_files.reject_empty(body)
    sheet = _sheet_of(body, file_format, hashlib.sha256(body).hexdigest())
    # The preview never refuses for a missing write key: it reports them.
    plan = work_order_import.plan_work_order_import(session, sheet, commit=False)
    report = plan.report
    summary = ImportPreviewSummary(
        will_create=report.count(ImportOutcome.WILL_CREATE),
        will_update=report.count(ImportOutcome.WILL_UPDATE),
        existing=report.count(ImportOutcome.EXISTS),
        refused=report.count(ImportOutcome.REFUSED),
    )
    return ImportPreviewResponse.model_validate(
        {**_base_fields(report, _required_permissions(plan)), "dry_run": True, "summary": summary}
    )


@router.post("/work-orders/import")
def import_work_orders(
    principal: ImportWriterDep,
    file_format: ImportFormatDep,
    body: ImportBodyDep,
    session: SessionDep,
    check_token: Annotated[str | None, Header(alias=CHECK_HEADER)] = None,
    confirm_token: Annotated[str | None, Header(alias=CONFIRM_HEADER)] = None,
) -> ImportResultResponse:
    work_order_import_files.reject_empty(body)
    # The commit carries the bytes the client checked (OD-15-8): judged
    # before parsing. Not a security boundary — the permission is.
    if check_token is None or _CHECK_TOKEN.fullmatch(check_token) is None:
        raise InvalidInputError(CHECK_REQUIRED_MESSAGE)
    token = hashlib.sha256(body).hexdigest()
    if check_token != token:
        raise ConflictError(CHECK_MISMATCH_MESSAGE)
    # Absent is valid (nothing confirmed); whether it matches is judged
    # per Work Order against the commit's own change list (S2 D6).
    if confirm_token is not None and _CHECK_TOKEN.fullmatch(confirm_token) is None:
        raise InvalidInputError(CONFIRM_INVALID_MESSAGE)
    sheet = _sheet_of(body, file_format, token)
    plan = work_order_import.plan_work_order_import(session, sheet, commit=True)
    required = _required_permissions(plan)
    authorization.require(actor_of(principal), required)
    report = work_order_import.import_work_orders(
        session, plan, actor_user_id=principal.user_id, confirm_token=confirm_token
    )
    summary = ImportResultSummary(
        created=report.count(ImportOutcome.CREATED),
        updated=report.count(ImportOutcome.UPDATED),
        existing=report.count(ImportOutcome.EXISTS),
        refused=report.count(ImportOutcome.REFUSED),
    )
    return ImportResultResponse.model_validate(
        {**_base_fields(report, required), "dry_run": False, "summary": summary}
    )


def _attachment(filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-cache",
    }


@router.get("/work-orders/import/template.csv")
def work_order_import_csv_template(principal: ImportReaderDep) -> Response:
    return Response(
        content=work_order_import.csv_template_text().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers=_attachment(CSV_TEMPLATE_FILENAME),
    )


@router.get("/work-orders/import/template.xlsx")
def work_order_import_xlsx_template(principal: ImportReaderDep) -> Response:
    return Response(
        content=work_order_import_files.write_xlsx_template(work_order_import.IMPORT_COLUMNS),
        media_type=work_order_import_files.XLSX_MEDIA_TYPE,
        headers=_attachment(XLSX_TEMPLATE_FILENAME),
    )
