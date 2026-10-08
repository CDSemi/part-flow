import { Fragment, useEffect, useId, useRef, useState } from 'react';

import { ApiError, errorMessage } from '../../api/client';
import {
  IMPORT_TEMPLATE_URLS,
  checkWorkOrderFile,
  importFileKind,
  importWorkOrderFile,
} from '../../api/work-order-import';
import type {
  ImportFileKind,
  WorkOrderImportEntry,
  WorkOrderImportReport,
} from '../../api/work-order-import';
import { useSession } from '../../app/session-context';
import { ModalDialog } from '../../components/ModalDialog';
import { TypedConfirmDialog } from '../../components/TypedConfirmDialog';
import { formatIsoDate } from '../dates';
import { workOrderStatusLabel } from './demand-lines';
import {
  changeText,
  commitAllowed,
  fileProblem,
  formatFileSize,
  formatRowList,
  hasStaleUpdates,
  importButtonLabel,
  keptLinesText,
  missingPermissionText,
  missingPermissions,
  orderEntries,
  outcomeIcon,
  outcomeLabel,
  outcomeNotes,
  rowErrorText,
  rowsReadLine,
  summaryLine,
  typedConfirmValue,
  unassignedRowsMessage,
  workOrderNoun,
} from './work-order-import';

/** Where the dialog is in the choose → Check file → Import flow;
 * `unknown`: the Import answer was lost and part of it may be saved. */
type Phase =
  'idle' | 'chosen' | 'checking' | 'checked' | 'importing' | 'done' | 'unknown';

/** The bytes read once at Check file — Import sends exactly these. */
interface KeptFile {
  bytes: ArrayBuffer;
  kind: ImportFileKind;
}

/** An Import failure whose outcome is unknown: the request may have
 * reached the server (no answer, a timeout, a server error, or an
 * answer that could not be read). */
function importOutcomeUnknown(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.status === 408 || error.status >= 500;
}

/** Which alert takes focus after a failed step (its control went away
 * or was disabled while the step ran). */
type AlertFocus = { target: 'alert' | 'unknown' };

/** A sign-in or permission refusal: nothing ran, the report stays. */
function accessRefusal(error: unknown): boolean {
  return (
    error instanceof ApiError && (error.status === 401 || error.status === 403)
  );
}

/**
 * Import Work Orders from a file (GUI_DESIGN §11.7): choose a CSV or
 * Excel file, `Check file` (a dry run on the server that writes
 * nothing), read the per-Work-Order report, then Import with the same
 * bytes — the server re-validates them and creates each new Work Order,
 * and changes each Open or Released one the file lists, in its own
 * transaction. Changes to existing Work Orders are applied only after a
 * typed confirmation listing every change (`CHANGE {m}`), whose
 * `updateToken` travels with the Import. Nothing is released to
 * production and nothing is queued: both steps need the server.
 * While the Import is in flight the dialog cannot be closed (its
 * writes cannot be recalled); the host guards navigation through
 * `onBusyChange`. `onClose(wrote)` tells the host whether the list must
 * be reloaded (a Work Order was created or changed, or the outcome is
 * unknown).
 */
export function ImportWorkOrdersDialog({
  writeBlocked,
  onBusyChange,
  onClose,
}: {
  writeBlocked: boolean;
  onBusyChange: (busy: boolean) => void;
  onClose: (wrote: boolean) => void;
}) {
  const headingId = useId();
  const helpId = useId();
  const fileInputId = useId();
  const resultHeadingId = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const resultHeadingRef = useRef<HTMLHeadingElement>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const unknownAlertRef = useRef<HTMLDivElement>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [file, setFile] = useState<File | null>(null);
  const [kept, setKept] = useState<KeptFile | null>(null);
  const [report, setReport] = useState<WorkOrderImportReport | null>(null);
  const [alert, setAlert] = useState<string | null>(null);
  const [wrote, setWrote] = useState(false);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [alertFocus, setAlertFocus] = useState<AlertFocus | null>(null);
  // The typed confirmation of the changes to existing Work Orders.
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { can } = useSession();

  const importing = phase === 'importing';
  const busy = importing || phase === 'checking';
  const finished = phase === 'done' || phase === 'unknown';

  useEffect(() => {
    fileInputRef.current?.focus();
  }, []);

  useEffect(() => {
    onBusyChange(importing);
  }, [importing, onBusyChange]);

  // A new report is announced by moving focus to its heading.
  useEffect(() => {
    if (report !== null) resultHeadingRef.current?.focus();
  }, [report]);

  // A failed step moves focus to its alert, so focus never falls out of
  // the dialog with the control that started the step — unless another
  // dialog (the sign-in prompt of a 401) has taken it meanwhile.
  useEffect(() => {
    if (alertFocus === null) return;
    const target = (
      alertFocus.target === 'unknown' ? unknownAlertRef : alertRef
    ).current;
    const active = document.activeElement;
    const elsewhere =
      active !== null &&
      active !== document.body &&
      target?.closest('[role="dialog"]')?.contains(active) !== true;
    if (!elsewhere) target?.focus();
  }, [alertFocus]);

  function showReport(next: WorkOrderImportReport | null) {
    setExpanded(new Set());
    setReport(next);
  }

  function choose(event: React.ChangeEvent<HTMLInputElement>) {
    const picked = event.target.files?.[0] ?? null;
    // Reset so the same file can be picked again after a change on disk.
    event.target.value = '';
    if (picked === null) return;
    setFile(picked);
    setKept(null);
    showReport(null);
    setAlert(fileProblem(picked));
    setPhase('chosen');
  }

  async function check() {
    if (file === null || busy || fileProblem(file) !== null) return;
    const kind = importFileKind(file.name);
    if (kind === null) return;
    const before = phase;
    setAlert(null);
    setPhase('checking');
    let source = kept;
    if (source === null) {
      // Read once: Check file again and Import reuse exactly these bytes.
      try {
        source = { bytes: await file.arrayBuffer(), kind };
      } catch {
        setAlert('The file could not be read. Choose it again.');
        setAlertFocus({ target: 'alert' });
        setPhase(before);
        return;
      }
      setKept(source);
    }
    try {
      showReport(await checkWorkOrderFile(source.bytes, source.kind));
      setPhase('checked');
    } catch (error) {
      setAlert(errorMessage(error));
      setAlertFocus({ target: 'alert' });
      setPhase(before);
    }
  }

  /** Import: straight away when the file changes no existing Work
   * Order, otherwise after the typed confirmation. */
  function requestImport() {
    if (report === null || !commitAllowed(report, writeBlocked, busy, can)) {
      return;
    }
    if (report.dryRun && report.summary.willUpdate > 0) setConfirmOpen(true);
    else void runImport(null);
  }

  async function runImport(updateToken: string | null) {
    if (
      report === null ||
      kept === null ||
      !commitAllowed(report, writeBlocked, busy, can)
    ) {
      return;
    }
    setAlert(null);
    setPhase('importing');
    try {
      const result = await importWorkOrderFile(
        kept.bytes,
        kept.kind,
        report.checkToken,
        updateToken,
      );
      if (
        !result.dryRun &&
        (result.summary.created > 0 || result.summary.updated > 0)
      ) {
        setWrote(true);
      }
      showReport(result);
      setPhase('done');
    } catch (error) {
      if (importOutcomeUnknown(error)) {
        // Never retried automatically: checking the file again shows
        // what is already in PartFlow, and nothing is ever duplicated.
        setWrote(true);
        showReport(null);
        setAlertFocus({ target: 'unknown' });
        setPhase('unknown');
        return;
      }
      setAlert(errorMessage(error));
      setAlertFocus({ target: 'alert' });
      if (accessRefusal(error)) {
        setPhase('checked');
        return;
      }
      // The server refused this file (e.g. not the file that was
      // checked): the old report no longer stands.
      showReport(null);
      setPhase('chosen');
    }
  }

  // Esc, the backdrop and Cancel are ignored while the Import runs.
  function requestClose() {
    if (importing) return;
    onClose(wrote);
  }

  function toggleLines(key: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const chosenProblem = file === null ? null : fileProblem(file);
  const willCreate = report?.dryRun ? report.summary.willCreate : 0;
  const willUpdate = report?.dryRun ? report.summary.willUpdate : 0;
  const canImport =
    report !== null && commitAllowed(report, writeBlocked, busy, can);
  const disabledReasons: string[] = writeBlocked
    ? ['Reconnect to check or import the file.']
    : report === null
      ? ['Check the file before importing it.']
      : !report.dryRun || report.commitBlocked
        ? []
        : willCreate + willUpdate === 0
          ? ['Nothing to create or change.']
          : missingPermissions(report, can).map(missingPermissionText);

  return (
    <>
      <ModalDialog labelledBy={headingId} onClose={requestClose} size="xwide">
        <div className="wo-import">
          <h2 id={headingId} className="nwo-title">
            Import Work Orders
          </h2>
          <p className="wo-sub">
            Create Work Orders from a CSV or Excel file, or change the Open and
            Released Work Orders it lists. Import saves business demand only —
            nothing is released to production.
          </p>
          <p id={helpId} className="nwo-hint wo-import-help">
            Required columns: Work Order Number, Part Number, Requested
            Quantity. Optional: Job Number, Due Date (YYYY-MM-DD). One row per
            Part Number. Format the Work Order Number, Part Number and Job
            Number columns as Text so leading zeros stay. Columns A–BL are read.
            Excel files: the first worksheet is read and must be visible;
            formulas are read as the value last saved in Excel (a formula that
            was never calculated reads as empty); every row is imported, hidden
            or filtered rows too. Limits: 1 MB, 2,000 rows, 500 lines per Work
            Order, 200 characters per text cell. For a Work Order already in
            PartFlow, the file changes quantities, sets due dates, adds Job
            Numbers and adds lines to Open Work Orders; empty cells and lines
            not in the file keep their saved values.
          </p>
          <p className="nwo-hint wo-import-templates">
            Download template:{' '}
            <a href={IMPORT_TEMPLATE_URLS.CSV} download>
              CSV
            </a>{' '}
            ·{' '}
            <a href={IMPORT_TEMPLATE_URLS.XLSX} download>
              Excel
            </a>
          </p>

          <div className="wo-import-file">
            <label htmlFor={fileInputId}>Choose file</label>
            <input
              id={fileInputId}
              ref={fileInputRef}
              type="file"
              accept=".csv,.xlsx"
              aria-describedby={helpId}
              disabled={busy}
              onChange={choose}
            />
            {file !== null ? (
              <span className="wo-import-chosen">
                <span className="mono">{file.name}</span> ·{' '}
                {formatFileSize(file.size)}
              </span>
            ) : null}
            <button
              className={report !== null ? 'btn ghost' : 'btn primary'}
              disabled={
                writeBlocked || file === null || chosenProblem !== null || busy
              }
              onClick={() => void check()}
            >
              {phase === 'checking'
                ? 'Checking…'
                : kept !== null
                  ? 'Check file again'
                  : 'Check file'}
            </button>
          </div>

          {alert !== null ? (
            <div
              ref={alertRef}
              className="wo-import-alert"
              role="alert"
              tabIndex={-1}
            >
              {alert}
            </div>
          ) : null}

          {phase === 'unknown' ? (
            <div
              ref={unknownAlertRef}
              className="wo-import-alert"
              role="alert"
              tabIndex={-1}
            >
              The import may be partly saved. Check the file again: Work Orders
              already in PartFlow are never duplicated, and changes already
              saved are not listed again.
            </div>
          ) : null}

          {report !== null ? (
            <ImportReport
              report={report}
              headingId={resultHeadingId}
              headingRef={resultHeadingRef}
              expanded={expanded}
              onToggleLines={toggleLines}
            />
          ) : null}

          <div className="row wo-import-actions">
            <button
              className="bigbtn ghost"
              disabled={importing}
              onClick={requestClose}
            >
              {finished ? 'Close' : 'Cancel (Esc)'}
            </button>
            {finished ? null : (
              <button
                className="bigbtn primary"
                disabled={!canImport}
                onClick={requestImport}
              >
                {importButtonLabel(willCreate, willUpdate)}
              </button>
            )}
          </div>
          {importing ? (
            <p className="wo-import-reason" role="status">
              Importing… Keep this page open.
            </p>
          ) : finished ? null : (
            disabledReasons.map((reason) => (
              <p key={reason} className="wo-import-reason">
                {reason}
              </p>
            ))
          )}
        </div>
      </ModalDialog>
      {confirmOpen && report !== null && report.dryRun ? (
        <ConfirmChangesDialog
          report={report}
          confirmDisabled={writeBlocked || busy}
          onConfirm={() => {
            setConfirmOpen(false);
            void runImport(report.updateToken);
          }}
          onCancel={() => setConfirmOpen(false)}
        />
      ) : null}
    </>
  );
}

/** The typed confirmation of the changes to existing Work Orders: every
 * change of every Work Order the file changes, then `CHANGE {m}`. */
function ConfirmChangesDialog({
  report,
  confirmDisabled,
  onConfirm,
  onCancel,
}: {
  report: WorkOrderImportReport;
  confirmDisabled: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const updates = report.workOrders.filter(
    (entry) => entry.outcome === 'WILL_UPDATE',
  );
  const m = updates.length;
  const creates = report.dryRun ? report.summary.willCreate : 0;
  return (
    <TypedConfirmDialog
      title={`Change ${m} existing ${workOrderNoun(m)}?`}
      expectedValue={typedConfirmValue(m)}
      valueLabel="Work Orders to change"
      confirmLabel={`Import and change ${m} ${workOrderNoun(m)}`}
      confirmDisabled={confirmDisabled}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <p>
        These Work Orders are already in PartFlow. Import applies every change
        below; each Work Order is saved on its own.
        {creates > 0
          ? ` It also creates ${creates === 1 ? '1 new Work Order' : `${creates} new Work Orders`}.`
          : null}
      </p>
      <div
        className="wo-import-confirm-list"
        tabIndex={0}
        role="region"
        aria-label="Changes to existing Work Orders"
      >
        {updates.map((entry) => (
          <section key={entry.workOrderNumber} className="wo-import-confirm-wo">
            <h4>
              WO <span className="mono">{entry.workOrderNumber}</span> ·{' '}
              {workOrderStatusLabel(entry.existingStatus ?? '')}
            </h4>
            <ChangeList entry={entry} />
          </section>
        ))}
      </div>
    </TypedConfirmDialog>
  );
}

/** The change list of one changed Work Order: each change, then the
 * completion and the kept lines when they apply. */
function ChangeList({
  entry,
  id,
}: {
  entry: WorkOrderImportEntry;
  id?: string;
}) {
  const kept = keptLinesText(entry);
  return (
    <ul id={id} className="wo-import-changes">
      {(entry.changes ?? []).map((change) => (
        <li key={`${change.kind}-${change.row}`} className="wo-import-change">
          {changeText(change, formatIsoDate)}
        </li>
      ))}
      {entry.completesWorkOrder === true ? (
        <li className="wo-import-change-note">
          Completes the Work Order — every line becomes fully allocated.
        </li>
      ) : null}
      {kept !== null ? <li className="wo-import-change-note">{kept}</li> : null}
    </ul>
  );
}

/** The Check file / Import report: counts, what was read, blocking
 * rows and one row per Work Order (refused first). */
function ImportReport({
  report,
  headingId,
  headingRef,
  expanded,
  onToggleLines,
}: {
  report: WorkOrderImportReport;
  headingId: string;
  headingRef: React.RefObject<HTMLHeadingElement>;
  expanded: ReadonlySet<string>;
  onToggleLines: (key: string) => void;
}) {
  const entries = orderEntries(report.workOrders);
  // The server counts exactly the lines this import writes.
  const undated = report.linesWithoutDueDate;
  const unscheduled = report.dryRun && report.summary.willCreate > 0;
  const completedDiffers = entries.some(
    (entry) =>
      entry.outcome === 'EXISTS' &&
      entry.existingStatus === 'COMPLETED' &&
      entry.differsFromFile === true,
  );
  return (
    <section className="wo-import-report" aria-labelledby={headingId}>
      <h3 id={headingId} ref={headingRef} tabIndex={-1}>
        {report.dryRun ? 'Check result' : 'Import result'}
      </h3>
      <p className="wo-import-summary">{summaryLine(report)}</p>
      <ul className="wo-import-facts">
        {report.worksheet !== null ? (
          <li>Worksheet read: {report.worksheet}</li>
        ) : null}
        <li>{rowsReadLine(report)}</li>
        {report.ignoredColumns.length > 0 ? (
          <li>Ignored columns: {report.ignoredColumns.join(', ')}</li>
        ) : null}
      </ul>
      {report.dryRun && (unscheduled || undated > 0) ? (
        <ul className="wo-import-omissions">
          {unscheduled ? (
            <li>
              Imported Work Orders get no Work Order due date — they stay
              unscheduled.
            </li>
          ) : null}
          {undated > 0 ? (
            <li>
              {undated === 1
                ? '1 line has no due date and sorts after dated demand.'
                : `${undated} lines have no due date and sort after dated demand.`}
            </li>
          ) : null}
        </ul>
      ) : null}

      {report.commitBlocked ? (
        <div className="wo-import-unassigned">
          <h4>Rows without a usable Work Order Number</h4>
          <ul>
            {report.unassignedRows.map((error, index) => (
              <li key={index}>
                {error.row === null
                  ? error.message
                  : `Row ${error.row} — ${error.message}`}
              </li>
            ))}
          </ul>
          <p className="wo-import-warn">{unassignedRowsMessage(report)}</p>
        </div>
      ) : null}

      {hasStaleUpdates(report) ? (
        <p className="wo-import-warn wo-import-stale">
          Some Work Orders were not changed because they changed after the
          check. Check the file again to see and confirm the current changes.
        </p>
      ) : null}

      {entries.length > 0 ? (
        <table className="wo-import-table">
          <thead>
            <tr>
              <th>WO Number</th>
              <th>Rows</th>
              <th>Lines</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <ImportEntryRows
                key={entry.workOrderNumber}
                entry={entry}
                open={expanded.has(entry.workOrderNumber)}
                onToggle={() => onToggleLines(entry.workOrderNumber)}
              />
            ))}
          </tbody>
        </table>
      ) : null}
      {completedDiffers ? (
        <p className="nwo-hint">
          For completed Work Orders, due dates and Job Numbers are not compared.
        </p>
      ) : null}
    </section>
  );
}

/** One Work Order of the report, plus its lines — or, for a changed
 * existing Work Order, its changes — when disclosed. */
function ImportEntryRows({
  entry,
  open,
  onToggle,
}: {
  entry: WorkOrderImportEntry;
  open: boolean;
  onToggle: () => void;
}) {
  const linesId = useId();
  const tone = entry.outcome.toLowerCase().replace('_', '-');
  const changed = entry.changes !== null;
  const noun = changed ? 'changes' : 'lines';
  const disclosable = changed || entry.lines.length > 0;
  return (
    <Fragment>
      <tr className={`wo-import-entry ${tone}`}>
        <td data-label="WO Number">
          <span className="mono wo-import-wo">{entry.workOrderNumber}</span>
        </td>
        <td data-label="Rows" className="mono-sm">
          {formatRowList(entry.rows)}
        </td>
        <td data-label="Lines">
          {entry.outcome === 'REFUSED' ? '—' : entry.lines.length}
        </td>
        <td data-label="Result">
          <span className={`wo-import-outcome ${tone}`}>
            <span aria-hidden="true">{outcomeIcon(entry.outcome)}</span>{' '}
            <span>{outcomeLabel(entry)}</span>
          </span>
          {outcomeNotes(entry).map((note) => (
            <div key={note} className="wo-import-note">
              {note}
            </div>
          ))}
          {entry.errors.length > 0 ? (
            <ul className="wo-import-errors">
              {entry.errors.map((error, index) => (
                <li key={index}>{rowErrorText(error)}</li>
              ))}
            </ul>
          ) : null}
          {disclosable ? (
            <button
              className="wo-import-toggle"
              aria-expanded={open}
              aria-controls={open ? linesId : undefined}
              aria-label={`${open ? 'Hide' : 'Show'} ${noun} of ${entry.workOrderNumber}`}
              onClick={onToggle}
            >
              {open ? `Hide ${noun}` : `Show ${noun}`}
            </button>
          ) : null}
        </td>
      </tr>
      {open && changed ? (
        <tr className="wo-import-linesrow">
          <td colSpan={4}>
            <ChangeList entry={entry} id={linesId} />
          </td>
        </tr>
      ) : open ? (
        <tr className="wo-import-linesrow">
          <td colSpan={4} id={linesId}>
            <table className="wo-import-lines">
              <thead>
                <tr>
                  <th>Row</th>
                  <th>PN</th>
                  <th>Qty</th>
                  <th>Due</th>
                  <th>Job</th>
                </tr>
              </thead>
              <tbody>
                {entry.lines.map((line) => (
                  <tr key={line.row}>
                    <td data-label="Row" className="mono-sm">
                      {line.row}
                    </td>
                    <td data-label="PN" className="mono wo-import-pn">
                      {line.partNumber}
                    </td>
                    <td data-label="Qty">{line.requestedQuantity}</td>
                    <td data-label="Due">{formatIsoDate(line.dueDate)}</td>
                    <td data-label="Job" className="mono-sm">
                      {line.jobNumber ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}
