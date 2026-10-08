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
import { ModalDialog } from '../../components/ModalDialog';
import { formatIsoDate } from '../dates';
import {
  commitAllowed,
  fileProblem,
  formatFileSize,
  formatRowList,
  importButtonLabel,
  orderEntries,
  outcomeIcon,
  outcomeLabel,
  outcomeNotes,
  rowErrorText,
  rowsReadLine,
  summaryLine,
  unassignedRowsMessage,
  undatedLinesToCreate,
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

/** A sign-in or permission refusal: nothing ran, the report stays. */
function accessRefusal(error: unknown): boolean {
  return (
    error instanceof ApiError && (error.status === 401 || error.status === 403)
  );
}

/**
 * Import Work Orders from a file (GUI_DESIGN §11.7): choose a CSV or
 * Excel file, `Check file` (a dry run on the server that writes
 * nothing), read the per-Work-Order report, then `Import N Work Orders`
 * with the same bytes — the server re-validates them and creates each
 * new Work Order in its own transaction. Nothing is released to
 * production and nothing is queued: both steps need the server.
 * While the Import is in flight the dialog cannot be closed (its
 * writes cannot be recalled); the host guards navigation through
 * `onBusyChange`. `onClose(wrote)` tells the host whether the list must
 * be reloaded (a Work Order was created, or the outcome is unknown).
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
  const [phase, setPhase] = useState<Phase>('idle');
  const [file, setFile] = useState<File | null>(null);
  const [kept, setKept] = useState<KeptFile | null>(null);
  const [report, setReport] = useState<WorkOrderImportReport | null>(null);
  const [alert, setAlert] = useState<string | null>(null);
  const [wrote, setWrote] = useState(false);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());

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
      setPhase(before);
    }
  }

  async function runImport() {
    if (
      report === null ||
      kept === null ||
      !commitAllowed(report, writeBlocked, busy)
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
      );
      if (!result.dryRun && result.summary.created > 0) setWrote(true);
      showReport(result);
      setPhase('done');
    } catch (error) {
      if (importOutcomeUnknown(error)) {
        // Never retried automatically: checking the file again shows
        // what is already in PartFlow, and nothing is ever duplicated.
        setWrote(true);
        showReport(null);
        setPhase('unknown');
        return;
      }
      setAlert(errorMessage(error));
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
  const canImport =
    report !== null && commitAllowed(report, writeBlocked, busy);
  const disabledReason = writeBlocked
    ? 'Reconnect to check or import the file.'
    : report === null
      ? 'Check the file before importing it.'
      : report.dryRun && !report.commitBlocked && willCreate === 0
        ? 'Nothing new to import.'
        : null;

  return (
    <ModalDialog labelledBy={headingId} onClose={requestClose} size="xwide">
      <div className="wo-import">
        <h2 id={headingId} className="nwo-title">
          Import Work Orders
        </h2>
        <p className="wo-sub">
          Create Work Orders from a CSV or Excel file. Import saves business
          demand only — nothing is released to production.
        </p>
        <p id={helpId} className="nwo-hint wo-import-help">
          Required columns: Work Order Number, Part Number, Requested Quantity.
          Optional: Job Number, Due Date (YYYY-MM-DD). One row per Part Number.
          Format the Work Order Number, Part Number and Job Number columns as
          Text so leading zeros stay. Excel files: the first worksheet is read,
          columns A–BL; formulas are read as the value last saved in Excel;
          every row is imported, hidden or filtered rows too.
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
          <div className="wo-import-alert" role="alert">
            {alert}
          </div>
        ) : null}

        {phase === 'unknown' ? (
          <div className="wo-import-alert" role="alert">
            The import may be partly saved. Check the file again: Work Orders
            already in PartFlow are never duplicated.
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
              onClick={() => void runImport()}
            >
              {importButtonLabel(willCreate)}
            </button>
          )}
        </div>
        {importing ? (
          <p className="wo-import-reason" role="status">
            Importing… Keep this page open.
          </p>
        ) : !finished && disabledReason !== null ? (
          <p className="wo-import-reason">{disabledReason}</p>
        ) : null}
      </div>
    </ModalDialog>
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
  const undated = undatedLinesToCreate(report);
  const anyExisting = entries.some((entry) => entry.outcome === 'EXISTS');
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
      {report.dryRun && report.summary.willCreate > 0 ? (
        <ul className="wo-import-omissions">
          <li>
            Imported Work Orders get no Work Order due date — they stay
            unscheduled.
          </li>
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
      {anyExisting ? (
        <p className="nwo-hint">Due dates and Job Numbers are not compared.</p>
      ) : null}
    </section>
  );
}

/** One Work Order of the report, plus its lines when disclosed. */
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
          {entry.lines.length > 0 ? (
            <button
              className="wo-import-toggle"
              aria-expanded={open}
              aria-controls={open ? linesId : undefined}
              aria-label={`${open ? 'Hide lines' : 'Show lines'} of ${entry.workOrderNumber}`}
              onClick={onToggle}
            >
              {open ? 'Hide lines' : 'Show lines'}
            </button>
          ) : null}
        </td>
      </tr>
      {open ? (
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
