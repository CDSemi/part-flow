// Presentation logic of the Import Work Orders dialog (GUI_DESIGN
// §11.7): the client-side file pre-check, labels, row lists, change
// lists, report order and when Import may be pressed. Every import rule
// itself is the server's — nothing here decides what a file creates or
// changes, or which permission it needs.

import { IMPORT_MAX_BYTES, importFileKind } from '../../api/work-order-import';
import type {
  ImportChange,
  ImportOutcome,
  ImportRowError,
  WorkOrderImportEntry,
  WorkOrderImportReport,
} from '../../api/work-order-import';
import type { Permission } from '../../api/roles';
import { PERMISSION_LABELS } from '../administration/permissions';

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

/** `Work Order` / `Work Orders`. */
export function workOrderNoun(n: number): string {
  return n === 1 ? 'Work Order' : 'Work Orders';
}

/** `change` / `changes`. */
export function changeNoun(k: number): string {
  return k === 1 ? 'change' : 'changes';
}

/** The Import button over the Work Orders a Check file answer creates
 * and changes: `Import 1 Work Order`, `Change 2 Work Orders…`,
 * `Create 1 Work Order, change 1 Work Order…` (the ellipsis: a typed
 * confirmation follows); plain `Import Work Orders` while neither. */
export function importButtonLabel(create: number, update: number): string {
  if (update > 0 && create > 0) {
    return `Create ${create} ${workOrderNoun(create)}, change ${update} ${workOrderNoun(update)}…`;
  }
  if (update > 0) return `Change ${update} ${workOrderNoun(update)}…`;
  if (create > 0) return `Import ${create} ${workOrderNoun(create)}`;
  return 'Import Work Orders';
}

/** The value typed to confirm changing `m` existing Work Orders. */
export function typedConfirmValue(m: number): string {
  return `CHANGE ${m}`;
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
    case 'WILL_UPDATE':
    case 'UPDATED':
      return 1;
    case 'WILL_CREATE':
    case 'CREATED':
      return 2;
    case 'EXISTS':
      return 3;
    default:
      return assertNever(outcome);
  }
}

/** Report order: refused Work Orders first (they need action), then the
 * ones changed, then the ones created, then the ones already in
 * PartFlow; file order within. */
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

function changeCount(entry: WorkOrderImportEntry): string {
  const k = entry.changes?.length ?? 0;
  return `${k} ${changeNoun(k)}`;
}

/** The result label of one Work Order (the only place the number of
 * changes appears). */
export function outcomeLabel(entry: WorkOrderImportEntry): string {
  switch (entry.outcome) {
    case 'WILL_CREATE':
      return 'Will be created';
    case 'WILL_UPDATE':
      return `Will change — ${changeCount(entry)}`;
    case 'CREATED':
      return 'Created';
    case 'UPDATED':
      return `Changed — ${changeCount(entry)}`;
    case 'EXISTS':
      return entry.existingStatus === 'COMPLETED'
        ? 'Already in PartFlow — not changed by this import'
        : 'Already in PartFlow — nothing to change';
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
    case 'WILL_UPDATE':
    case 'CREATED':
    case 'UPDATED':
      return '✓';
    case 'EXISTS':
      return '•';
    case 'REFUSED':
      return '✕';
    default:
      return assertNever(outcome);
  }
}

/** `Kept, not in this file: {PNs}` for saved lines the file does not
 * list, or null when it lists them all. */
export function keptLinesText(entry: WorkOrderImportEntry): string | null {
  const kept = entry.linesNotInFile ?? [];
  return kept.length > 0 ? `Kept, not in this file: ${kept.join(', ')}` : null;
}

/** The quiet notes under a result label. */
export function outcomeNotes(entry: WorkOrderImportEntry): string[] {
  const notes: string[] = [];
  const newPns = entry.newPartNumbers.length;
  if (
    (entry.outcome === 'WILL_CREATE' || entry.outcome === 'WILL_UPDATE') &&
    newPns > 0
  ) {
    notes.push(
      newPns === 1 ? '1 new Part Number' : `${newPns} new Part Numbers`,
    );
  }
  if (entry.outcome === 'EXISTS') {
    if (entry.existingStatus === 'COMPLETED') {
      if (entry.differsFromFile === true) {
        notes.push(
          'Differs from this file — this Work Order is completed and is never changed.',
        );
      }
    } else {
      const kept = keptLinesText(entry);
      if (kept !== null) notes.push(kept);
    }
  }
  return notes;
}

/** One change of an existing Work Order as one line of text, e.g.
 * `Row 4 · A-100 · Qty 10 → 15 · leaves the Hot list` or
 * `Row 5 · Add B-200 · Qty 3 · Due Jul 24, 2026 · Job 18112`. */
export function changeText(
  change: ImportChange,
  formatDate: (iso: string | null) => string,
): string {
  const parts = [`Row ${change.row}`];
  const jobs = (values: readonly string[]) =>
    values.length > 0 ? values.join(', ') : '—';
  if (change.kind === 'ADD_LINE') {
    parts.push(`Add ${change.partNumber}`);
    if (change.requestedQuantity !== null) {
      parts.push(`Qty ${change.requestedQuantity.after}`);
    }
    if (change.dueDate?.after)
      parts.push(`Due ${formatDate(change.dueDate.after)}`);
    if (change.jobNumbers !== null && change.jobNumbers.after.length > 0) {
      parts.push(`Job ${change.jobNumbers.after.join(', ')}`);
    }
    if (change.newPartNumber) parts.push('new Part Number');
    return parts.join(' · ');
  }
  parts.push(change.partNumber);
  if (change.requestedQuantity !== null) {
    parts.push(
      `Qty ${change.requestedQuantity.before ?? '—'} → ${change.requestedQuantity.after}`,
    );
  }
  if (change.dueDate !== null) {
    parts.push(
      `Due ${formatDate(change.dueDate.before)} → ${formatDate(change.dueDate.after)}`,
    );
  }
  if (change.jobNumbers !== null) {
    parts.push(
      `Job Numbers ${jobs(change.jobNumbers.before)} → ${jobs(change.jobNumbers.after)}`,
    );
  }
  if (change.leavesHotList) parts.push('leaves the Hot list');
  return parts.join(' · ');
}

/** The permissions the file's content needs that this user lacks. */
export function missingPermissions(
  report: WorkOrderImportReport,
  can: (permission: Permission) => boolean,
): Permission[] {
  return report.requiredPermissions.filter((key) => !can(key));
}

/** Why Import stays disabled for one missing permission. */
export function missingPermissionText(key: Permission): string {
  const label = PERMISSION_LABELS[key];
  if (key === 'MANAGE_WORK_ORDERS') {
    return `Creating Work Orders needs the "${label}" permission.`;
  }
  if (key === 'EDIT_WORK_ORDER_DEMAND') {
    return `Changing existing Work Orders needs the "${label}" permission.`;
  }
  return `This import needs the "${label}" permission.`;
}

/** Import may be pressed: a connected, idle Check file answer that
 * creates or changes at least one Work Order, has no blocking rows, and
 * needs no permission the user lacks. */
export function commitAllowed(
  report: WorkOrderImportReport,
  writeBlocked: boolean,
  busy: boolean,
  can: (permission: Permission) => boolean,
): boolean {
  return (
    !writeBlocked &&
    !busy &&
    report.dryRun &&
    !report.commitBlocked &&
    report.summary.willCreate + report.summary.willUpdate > 0 &&
    missingPermissions(report, can).length === 0
  );
}

/** The one-line count summary of a report. */
export function summaryLine(report: WorkOrderImportReport): string {
  const first = report.dryRun
    ? `Will create ${report.summary.willCreate} · Will change ${report.summary.willUpdate}`
    : `Created ${report.summary.created} · Changed ${report.summary.updated}`;
  return `${first} · Already in PartFlow ${report.summary.existing} · Not imported ${report.summary.refused}`;
}

/** An Import answer that left existing Work Orders unchanged because
 * they (or the confirmed changes) changed after the check. */
export function hasStaleUpdates(report: WorkOrderImportReport): boolean {
  return (
    !report.dryRun &&
    report.workOrders.some(
      (entry) =>
        entry.outcome === 'REFUSED' &&
        entry.workOrderId !== null &&
        entry.errors.some((error) => error.row === null),
    )
  );
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

/** The commit-blocked copy over the distinct unassigned rows. */
export function unassignedRowsMessage(report: WorkOrderImportReport): string {
  const n = new Set(report.unassignedRows.map((error) => error.row)).size;
  // The same copy as the server's refusal of the Import (C2).
  return `${n === 1 ? '1 row has' : `${n} rows have`} no usable Work Order Number. Add or fix it, or delete those rows, then check the file again.`;
}

function assertNever(value: never): never {
  throw new Error(`Unexpected import outcome: ${String(value)}`);
}
