// Work Order file import API (Phase 15 slice 1 — PROJECT_PROFILE §13
// File import; GUI_DESIGN §11.7).
//
// A prepared CSV (UTF-8) or Excel (.xlsx) file is sent as the raw
// request body, labelled by its file extension — never by `File.type`,
// which Windows reports as `application/vnd.ms-excel` or '' for a CSV.
// `Check file` is a dry run that writes nothing and answers the
// `check_token` of the exact bytes; `Import` sends the same bytes with
// that token, and the server re-validates them and creates each new
// Work Order in its own transaction. Every rule (columns, row checks,
// grouping, existing numbers) is the server's; this module only maps
// the snake_case report to the camelCase types the dialog renders.
// Converters throw on a malformed answer or an unknown outcome instead
// of rendering a wrong report.
//
// Production-safe: no mock data, no framework imports.

import { apiUpload } from './client';

// ---------------------------------------------------------------------------
// Files
// ---------------------------------------------------------------------------

/** The largest file the server accepts (1 MB). */
export const IMPORT_MAX_BYTES = 1_048_576;

export type ImportFileKind = 'CSV' | 'XLSX';

/** The media type each kind is sent with. */
export const IMPORT_MEDIA_TYPE: Readonly<Record<ImportFileKind, string>> = {
  CSV: 'text/csv',
  XLSX: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
};

/** The header-only templates the server writes from its column list. */
export const IMPORT_TEMPLATE_URLS: Readonly<Record<ImportFileKind, string>> = {
  CSV: '/api/work-orders/import/template.csv',
  XLSX: '/api/work-orders/import/template.xlsx',
};

/** The kind of a file by its extension (case-insensitive), or null. */
export function importFileKind(name: string): ImportFileKind | null {
  const lower = name.toLowerCase();
  if (lower.endsWith('.csv')) return 'CSV';
  if (lower.endsWith('.xlsx')) return 'XLSX';
  return null;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * One Work Order's outcome: `WILL_CREATE` (Check file), `CREATED`
 * (Import), `EXISTS` (the number is already in PartFlow — not changed
 * by this import) or `REFUSED` (a row of it is invalid).
 */
export type ImportOutcome = 'WILL_CREATE' | 'CREATED' | 'EXISTS' | 'REFUSED';

/** The derived status of an existing Work Order (EXISTS only). */
export type ExistingWorkOrderStatus = 'OPEN' | 'RELEASED' | 'COMPLETED';

/** One problem the server found; row/column null for a Work Order-wide
 * problem. `column` is the template column name. */
export interface ImportRowError {
  row: number | null;
  column: string | null;
  message: string;
}

/** One demand line as read from the file (canonical PN). */
export interface ImportLine {
  row: number;
  partNumber: string;
  requestedQuantity: number;
  /** ISO `YYYY-MM-DD`, or null when the row has none. */
  dueDate: string | null;
  jobNumber: string | null;
}

export interface WorkOrderImportEntry {
  /** The Work Order Number the rows were grouped by. */
  workOrderNumber: string;
  /** Every spreadsheet row of the group, ascending. */
  rows: number[];
  outcome: ImportOutcome;
  /** Empty when REFUSED. */
  lines: ImportLine[];
  /** Part Numbers this Work Order creates on first use. */
  newPartNumbers: string[];
  linesWithoutDueDate: number;
  /** EXISTS and CREATED. */
  workOrderId: number | null;
  /** EXISTS only. */
  existingStatus: ExistingWorkOrderStatus | null;
  /** EXISTS only: the file's Part Numbers or quantities differ from the
   * Work Order's (due dates and Job Numbers are not compared). */
  differsFromFile: boolean | null;
  /** REFUSED only. */
  errors: ImportRowError[];
}

interface ImportReportBase {
  fileFormat: ImportFileKind;
  /** Excel: the title of the worksheet read; CSV: null. */
  worksheet: string | null;
  /** Proves on Import that the same bytes were checked. */
  checkToken: string;
  /** Rows without a usable Work Order Number block the import. */
  commitBlocked: boolean;
  rowsRead: number;
  emptyRowsIgnored: number;
  ignoredColumns: string[];
  linesWithoutDueDate: number;
  /** In file order (first row of each Work Order). */
  workOrders: WorkOrderImportEntry[];
  unassignedRows: ImportRowError[];
}

/** The Check file answer — nothing was written. */
export interface ImportPreviewReport extends ImportReportBase {
  dryRun: true;
  summary: { willCreate: number; existing: number; refused: number };
}

/** The Import answer. */
export interface ImportResultReport extends ImportReportBase {
  dryRun: false;
  summary: { created: number; existing: number; refused: number };
}

export type WorkOrderImportReport = ImportPreviewReport | ImportResultReport;

// ---------------------------------------------------------------------------
// Wire mapping (checked)
// ---------------------------------------------------------------------------

type Wire = Record<string, unknown>;

const OUTCOMES: readonly ImportOutcome[] = [
  'WILL_CREATE',
  'CREATED',
  'EXISTS',
  'REFUSED',
];
const EXISTING_STATUSES: readonly ExistingWorkOrderStatus[] = [
  'OPEN',
  'RELEASED',
  'COMPLETED',
];
const FILE_KINDS: readonly ImportFileKind[] = ['CSV', 'XLSX'];

function malformed(): Error {
  return new Error('The server answered a malformed import report.');
}

function record(value: unknown): Wire {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw malformed();
  }
  return value as Wire;
}

function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw malformed();
  return value;
}

function int(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value)) throw malformed();
  return value;
}

function intOrNull(value: unknown): number | null {
  return value === null ? null : int(value);
}

function text(value: unknown): string {
  if (typeof value !== 'string') throw malformed();
  return value;
}

function textOrNull(value: unknown): string | null {
  return value === null ? null : text(value);
}

function flag(value: unknown): boolean {
  if (typeof value !== 'boolean') throw malformed();
  return value;
}

function oneOf<T extends string>(value: unknown, known: readonly T[]): T {
  const found = known.find((item) => item === value);
  if (found === undefined) throw malformed();
  return found;
}

function toRowError(value: unknown): ImportRowError {
  const wire = record(value);
  return {
    row: intOrNull(wire.row),
    column: textOrNull(wire.column),
    message: text(wire.message),
  };
}

function toLine(value: unknown): ImportLine {
  const wire = record(value);
  return {
    row: int(wire.row),
    partNumber: text(wire.part_number),
    requestedQuantity: int(wire.requested_quantity),
    dueDate: textOrNull(wire.due_date),
    jobNumber: textOrNull(wire.job_number),
  };
}

function toEntry(value: unknown): WorkOrderImportEntry {
  const wire = record(value);
  return {
    workOrderNumber: text(wire.work_order_number),
    rows: list(wire.rows).map(int),
    outcome: oneOf(wire.outcome, OUTCOMES),
    lines: list(wire.lines).map(toLine),
    newPartNumbers: list(wire.new_part_numbers).map(text),
    linesWithoutDueDate: int(wire.lines_without_due_date),
    workOrderId: intOrNull(wire.work_order_id),
    existingStatus:
      wire.existing_status === null
        ? null
        : oneOf(wire.existing_status, EXISTING_STATUSES),
    differsFromFile:
      wire.differs_from_file === null ? null : flag(wire.differs_from_file),
    errors: list(wire.errors).map(toRowError),
  };
}

/** Map one import report (either route's) to the application type. */
export function toWorkOrderImportReport(value: unknown): WorkOrderImportReport {
  const wire = record(value);
  const base: ImportReportBase = {
    fileFormat: oneOf(wire.file_format, FILE_KINDS),
    worksheet: textOrNull(wire.worksheet),
    checkToken: text(wire.check_token),
    commitBlocked: flag(wire.commit_blocked),
    rowsRead: int(wire.rows_read),
    emptyRowsIgnored: int(wire.empty_rows_ignored),
    ignoredColumns: list(wire.ignored_columns).map(text),
    linesWithoutDueDate: int(wire.lines_without_due_date),
    workOrders: list(wire.work_orders).map(toEntry),
    unassignedRows: list(wire.unassigned_rows).map(toRowError),
  };
  const summary = record(wire.summary);
  if (flag(wire.dry_run)) {
    return {
      ...base,
      dryRun: true,
      summary: {
        willCreate: int(summary.will_create),
        existing: int(summary.existing),
        refused: int(summary.refused),
      },
    };
  }
  return {
    ...base,
    dryRun: false,
    summary: {
      created: int(summary.created),
      existing: int(summary.existing),
      refused: int(summary.refused),
    },
  };
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

function fileBody(bytes: ArrayBuffer, kind: ImportFileKind): Blob {
  return new Blob([bytes], { type: IMPORT_MEDIA_TYPE[kind] });
}

/** Check file: a dry run over the bytes — nothing is written. */
export async function checkWorkOrderFile(
  bytes: ArrayBuffer,
  kind: ImportFileKind,
): Promise<WorkOrderImportReport> {
  const wire = await apiUpload<unknown>(
    '/api/work-orders/import/preview',
    fileBody(bytes, kind),
    'POST',
  );
  return toWorkOrderImportReport(wire);
}

/** Import the same bytes that were checked (`checkToken` of the check). */
export async function importWorkOrderFile(
  bytes: ArrayBuffer,
  kind: ImportFileKind,
  checkToken: string,
): Promise<WorkOrderImportReport> {
  const wire = await apiUpload<unknown>(
    '/api/work-orders/import',
    fileBody(bytes, kind),
    'POST',
    { 'X-PartFlow-Import-Check': checkToken },
  );
  return toWorkOrderImportReport(wire);
}
