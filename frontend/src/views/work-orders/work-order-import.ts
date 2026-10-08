// Presentation logic of the Import Work Orders dialog (GUI_DESIGN
// §11.7): the client-side file pre-check, labels, row lists, report
// order and when Import may be pressed. Every import rule itself is the
// server's — nothing here decides what a file creates.

import { IMPORT_MAX_BYTES, importFileKind } from '../../api/work-order-import';
import type {
  ImportOutcome,
  ImportRowError,
  WorkOrderImportEntry,
  WorkOrderImportReport,
} from '../../api/work-order-import';

/** Why a chosen file cannot be checked at all (nothing is sent), or
 * null. Same copy as the server's refusals. */
export function fileProblem(file: {
  name: string;
  size: number;
}): string | null {
  if (importFileKind(file.name) === null) return 'Choose a .csv or .xlsx file.';
  if (file.size > IMPORT_MAX_BYTES) {
    return 'The file is larger than 1 MB. Split it into smaller files.';
  }
  if (file.size === 0) return 'The file is empty.';
  return null;
}

/** The Import button: `Import 1 Work Order` / `Import {n} Work Orders`;
 * plain `Import Work Orders` while nothing would be created. */
export function importButtonLabel(n: number): string {
  if (n === 1) return 'Import 1 Work Order';
  if (n > 1) return `Import ${n} Work Orders`;
  return 'Import Work Orders';
}

/** Spreadsheet rows as ranges: `[2, 3, 4, 9]` → `2–4, 9`. */
export function formatRowList(rows: readonly number[]): string {
  const sorted = [...rows].sort((a, b) => a - b);
  const parts: string[] = [];
  let start = 0;
  for (let i = 1; i <= sorted.length; i += 1) {
    if (i < sorted.length && sorted[i] === sorted[i - 1] + 1) continue;
    const first = sorted[start];
    const last = sorted[i - 1];
    parts.push(first === last ? String(first) : `${first}–${last}`);
    start = i;
  }
  return parts.join(', ');
}

function outcomeRank(outcome: ImportOutcome): number {
  switch (outcome) {
    case 'REFUSED':
      return 0;
    case 'WILL_CREATE':
    case 'CREATED':
      return 1;
    case 'EXISTS':
      return 2;
    default:
      return assertNever(outcome);
  }
}

/** Report order: refused Work Orders first (they need action), then the
 * ones created, then the ones already in PartFlow; file order within. */
export function orderEntries(
  entries: readonly WorkOrderImportEntry[],
): WorkOrderImportEntry[] {
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort(
      (a, b) =>
        outcomeRank(a.entry.outcome) - outcomeRank(b.entry.outcome) ||
        a.index - b.index,
    )
    .map(({ entry }) => entry);
}

/** The result label of one Work Order. */
export function outcomeLabel(entry: WorkOrderImportEntry): string {
  switch (entry.outcome) {
    case 'WILL_CREATE':
      return 'Will be created';
    case 'CREATED':
      return 'Created';
    case 'EXISTS':
      return 'Already in PartFlow — not changed by this import';
    case 'REFUSED':
      return 'Not imported — fix the rows listed';
    default:
      return assertNever(entry.outcome);
  }
}

/** The status icon beside the label (never color alone). */
export function outcomeIcon(outcome: ImportOutcome): string {
  switch (outcome) {
    case 'WILL_CREATE':
    case 'CREATED':
      return '✓';
    case 'EXISTS':
      return '•';
    case 'REFUSED':
      return '✕';
    default:
      return assertNever(outcome);
  }
}

/** The quiet notes under a result label. */
export function outcomeNotes(entry: WorkOrderImportEntry): string[] {
  const notes: string[] = [];
  const newPns = entry.newPartNumbers.length;
  if (entry.outcome === 'WILL_CREATE' && newPns > 0) {
    notes.push(
      newPns === 1 ? '1 new Part Number' : `${newPns} new Part Numbers`,
    );
  }
  if (entry.outcome === 'EXISTS' && entry.differsFromFile === true) {
    notes.push(
      entry.existingStatus === 'COMPLETED'
        ? 'Differs from this file — this Work Order is completed and is never changed.'
        : 'Differs from this file — open the Work Order to apply changes.',
    );
  }
  return notes;
}

/** Import may be pressed: a connected, idle Check file answer that
 * creates at least one Work Order and has no blocking rows. */
export function commitAllowed(
  report: WorkOrderImportReport,
  writeBlocked: boolean,
  busy: boolean,
): boolean {
  return (
    !writeBlocked &&
    !busy &&
    report.dryRun &&
    !report.commitBlocked &&
    report.summary.willCreate > 0
  );
}

/** The one-line count summary of a report. */
export function summaryLine(report: WorkOrderImportReport): string {
  const first = report.dryRun
    ? `Will create ${report.summary.willCreate}`
    : `Created ${report.summary.created}`;
  return `${first} · Already in PartFlow ${report.summary.existing} · Not imported ${report.summary.refused}`;
}

/** `{n} rows read`, plus the empty rows inside the data when any. */
export function rowsReadLine(report: WorkOrderImportReport): string {
  const read =
    report.rowsRead === 1 ? '1 row read' : `${report.rowsRead} rows read`;
  const empty = report.emptyRowsIgnored;
  if (empty === 0) return read;
  return `${read} · ${empty === 1 ? '1 empty row' : `${empty} empty rows`} ignored`;
}

/** `Row {r} · {column} — {message}`, row and column omitted when null. */
export function rowErrorText(error: ImportRowError): string {
  const place = [
    error.row === null ? null : `Row ${error.row}`,
    error.column,
  ].filter((part): part is string => part !== null);
  return place.length > 0
    ? `${place.join(' · ')} — ${error.message}`
    : error.message;
}

/** A file size for the chosen-file line: bytes, KB or MB. */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes === 1 ? '1 byte' : `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Undated lines of the Work Orders the Check file answer would create. */
export function undatedLinesToCreate(report: WorkOrderImportReport): number {
  return report.workOrders
    .filter((entry) => entry.outcome === 'WILL_CREATE')
    .reduce((sum, entry) => sum + entry.linesWithoutDueDate, 0);
}

/** The commit-blocked copy over the distinct unassigned rows. */
export function unassignedRowsMessage(report: WorkOrderImportReport): string {
  const n = new Set(report.unassignedRows.map((error) => error.row)).size;
  // The same copy as the server's refusal of the Import (C2).
  return `${n === 1 ? '1 row has' : `${n} rows have`} no usable Work Order Number. Add or fix it, or delete those rows, then check the file again.`;
}

function assertNever(value: never): never {
  throw new Error(`Unexpected import outcome: ${String(value)}`);
}
