import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { errorMessage } from '../../api/client';
import { resolvePartNumber } from '../../api/part-numbers';
import {
  deleteWorkOrderDemand,
  getWorkOrder,
  updateWorkOrder,
} from '../../api/work-orders';
import type { WorkOrderDetail } from '../../api/work-orders';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { TypeChip } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { PageNote } from '../../components/PageNote';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { formatIsoDate } from '../dates';
import { normalizePartNumber } from '../scan-station/barcode';
import type { RequestType } from '../view-models';
import { AddPartDialog } from './AddPartDialog';
import type { AddPartResult } from './AddPartDialog';
import {
  RELEASED_REMOVE_EXPLANATION,
  applyWorkOrderDueDateChange,
  buildLineEdits,
  collectMissingDemandInfo,
  createDraftLine,
  draftFromDemand,
  draftToNewLine,
  isPositiveInteger,
  lineRemoveRule,
  processScan,
  validateDemandLines,
  workOrderStatusLabel,
} from './demand-lines';
import type {
  DemandLineDraft,
  LineError,
  LineField,
  MissingDemandInfo,
} from './demand-lines';
import { ReleaseDialog } from './ReleaseDialog';

/** One saved demand row offered for release (GUI_DESIGN §11.4) — the
 * COMMITTED server values, never the local draft. */
export interface ReleaseRequestContext {
  demandId: number;
  partNumber: string;
  requestedQuantity: number;
}

const RELEASE_WHILE_DIRTY_EXPLANATION =
  'Save or discard demand changes before releasing.';

const REMOVE_WHILE_DIRTY_EXPLANATION =
  'Save or discard demand changes before removing a saved line.';

/**
 * Work Order Details as a modal dialog over the Work Order list
 * (GUI_DESIGN §11.2): the list stays mounted and visible behind it and
 * the URL never changes. The dialog loads the real Work Order (header
 * + demand lines) from the API. An OPEN Work Order is editable: demand
 * lines can be added (manual-first ＋ Add Part, scanning secondary)
 * and edited as a local draft applied by `Save demand` — ONE PATCH,
 * one all-or-nothing backend transaction; a failed save keeps the
 * whole draft. Removing a saved line is its own explicit, confirmed
 * server action; the backend enforces the canonical rules
 * (PROJECT_PROFILE §13, §8.2 — released demand and the last line
 * answer 409 removing nothing). Every close request (Cancel, Escape,
 * backdrop) on a dirty draft asks for explicit discard confirmation
 * first. The Add Part and confirmation dialogs render as siblings of
 * this dialog so only the topmost dialog handles Escape, backdrop,
 * and focus.
 */
export function WorkOrderDetailPanel({
  workOrderId,
  writeBlocked,
  onClose,
  onChanged,
  onDirtyChange,
  showNotice,
}: {
  workOrderId: number;
  writeBlocked: boolean;
  onClose: () => void;
  /** Server state changed (save/removal/release committed) — reload
   * the list. */
  onChanged: () => void;
  onDirtyChange: (dirty: boolean) => void;
  showNotice: (message: string) => void;
}) {
  const headingId = useId();
  // The dialog owns its detail load so a committed save can adopt the
  // PATCH response as the fresh server state (never a simulated local
  // success).
  const [detail, setDetail] = useState<WorkOrderDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadGeneration, setLoadGeneration] = useState(0);
  const retryLoad = useCallback(() => {
    setLoadError(null);
    setLoadGeneration((value) => value + 1);
  }, []);

  const [due, setDue] = useState('');
  const [lines, setLines] = useState<DemandLineDraft[]>([]);
  const [dirty, setDirty] = useState(false);
  const [addPartOpen, setAddPartOpen] = useState(false);
  const [lineErrors, setLineErrors] = useState<LineError[]>([]);
  const [confirmRemove, setConfirmRemove] = useState<DemandLineDraft | null>(
    null,
  );
  const [confirmMissing, setConfirmMissing] =
    useState<MissingDemandInfo | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  // The §11.4 release flow of one saved demand row — carried by this
  // dialog so a committed release refreshes the SERVER-derived
  // released state (never a session-local flag).
  const [releaseDialog, setReleaseDialog] =
    useState<ReleaseRequestContext | null>(null);
  // The audited external-number entry of an internal Work Order
  // (PROJECT_PROFILE §7): blank = no change; a non-blank entry travels
  // VERBATIM with Save demand.
  const [numberDraft, setNumberDraft] = useState('');

  const scanRef = useRef<HTMLInputElement>(null);
  const fieldRefs = useRef(new Map<string, HTMLInputElement>());
  const [focusField, setFocusField] = useState<{
    id: number;
    field: LineField;
  } | null>(null);

  /** Adopt fresh server state and rebuild the editable draft. The
   * Released/read-only state of every line comes from the server's
   * release evidence inside the response. */
  const adoptDetail = useCallback((fresh: WorkOrderDetail) => {
    setDetail(fresh);
    setDue(fresh.dueDate ?? '');
    setLines(
      fresh.demands.map((demand) => draftFromDemand(demand, fresh.dueDate)),
    );
    setNumberDraft('');
    setLineErrors([]);
    setDirty(false);
  }, []);

  useEffect(() => {
    let cancelled = false;
    getWorkOrder(workOrderId).then(
      (fresh) => {
        if (!cancelled) adoptDetail(fresh);
      },
      (error: unknown) => {
        if (!cancelled) setLoadError(errorMessage(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [workOrderId, loadGeneration, adoptDetail]);

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    if (focusField) {
      const el = fieldRefs.current.get(`${focusField.id}:${focusField.field}`);
      el?.focus();
      el?.select();
      setFocusField(null);
    }
  }, [focusField, lines]);

  if (loadError !== null) {
    return (
      <ModalDialog labelledBy={headingId} onClose={onClose}>
        <h2 id={headingId} className="nwo-title">
          Work Order Details
        </h2>
        <ErrorState
          message="The Work Order could not be loaded."
          detail={loadError}
          onRetry={retryLoad}
        />
        <div className="row">
          <button className="bigbtn ghost" onClick={onClose}>
            Cancel (Esc)
          </button>
        </div>
      </ModalDialog>
    );
  }
  if (detail === null) {
    return (
      <ModalDialog labelledBy={headingId} onClose={onClose}>
        <h2 id={headingId} className="nwo-title">
          Work Order Details
        </h2>
        <LoadingState label="Loading Work Order" />
        <div className="row">
          <button className="bigbtn ghost" onClick={onClose}>
            Cancel (Esc)
          </button>
        </div>
      </ModalDialog>
    );
  }

  const editable = detail.status === 'OPEN';
  const woDisplay = detail.workOrderNumber ?? '—';
  const internal = detail.workOrderNumber === null;
  // An internal Work Order may receive its real external number later
  // through the audited edit (PROJECT_PROFILE §7) — release evidence
  // never blocks that, so the entry stays available on a RELEASED
  // Work Order too (only demand-line editing is OPEN-only).
  const numberEditVisible =
    internal && (detail.status === 'OPEN' || detail.status === 'RELEASED');
  const numberEntered = numberEditVisible && numberDraft.trim() !== '';

  // Released/read-only comes from the server release evidence loaded
  // with each line (`draftFromDemand`) — no session-local remapping.
  const display = lines;

  const errorFor = (id: number, field: LineField) =>
    lineErrors.find((e) => e.lineId === id && e.field === field)?.message;

  function clearLineError(id: number, field: LineField) {
    setLineErrors((current) =>
      current.filter((e) => !(e.lineId === id && e.field === field)),
    );
  }

  function updateLine(id: number, patch: Partial<DemandLineDraft>) {
    setLines((current) =>
      current.map((l) => (l.id === id ? { ...l, ...patch } : l)),
    );
    setDirty(true);
  }

  function handleDueChange(value: string) {
    setDue(value);
    setLines((current) => applyWorkOrderDueDateChange(current, value));
    setDirty(true);
  }

  function addScannedLine(pn: string, barcode: string, isNewPn: boolean) {
    const line = createDraftLine({
      pn,
      isNewPn,
      barcodeNote: isNewPn
        ? `new PN — barcode ${barcode}`
        : `existing PN · barcode ${barcode}`,
      due,
    });
    setLines((current) => [...current, line]);
    setDirty(true);
    showNotice(
      `✓ ${pn} added as an unsaved draft line — Request Type NEW · due date from the WO due date.`,
    );
    setFocusField({ id: line.id, field: 'qty' });
  }

  function handleScan(value: string) {
    const result = processScan(value, display);
    if (!result) return;
    if (result.kind === 'invalid') {
      showNotice(
        `✕ Unknown barcode “${result.barcode}” — nothing added. Only PN barcodes (PF:PN:…) add demand lines.`,
      );
      scanRef.current?.focus();
      return;
    }
    if (result.kind === 'duplicate') {
      if (result.released) {
        showNotice(
          `⚠ ${result.pn} is already on this Work Order and its production quantity is released — the line is read-only here.`,
        );
        scanRef.current?.focus();
        return;
      }
      showNotice(
        `⚠ ${result.pn} is already on this Work Order — edit its quantity instead of adding a duplicate line.`,
      );
      setFocusField({ id: result.lineId, field: 'qty' });
      return;
    }
    void resolvePartNumber(result.pn).then(
      (master) => addScannedLine(result.pn, result.barcode, master === null),
      (error: unknown) => showNotice(`✕ ${errorMessage(error)}`),
    );
  }

  function handleAddPartComplete(result: AddPartResult) {
    setAddPartOpen(false);
    const line = createDraftLine(result);
    setLines((current) => [...current, line]);
    setDirty(true);
    showNotice(
      `✓ ${result.pn} added as an unsaved draft line — apply it with Save demand.`,
    );
  }

  function handleAddPartDuplicate(pn: string) {
    setAddPartOpen(false);
    const duplicate = display.find((l) => l.pn === pn);
    showNotice(
      `⚠ ${pn} is already on this Work Order — edit the existing line instead of adding a duplicate.`,
    );
    if (duplicate && !duplicate.released) {
      setFocusField({ id: duplicate.id, field: 'qty' });
    }
  }

  function requestRemove(line: DemandLineDraft) {
    const rule = lineRemoveRule(line);
    if (rule === 'blocked') return;
    if (rule === 'draft') {
      // An unsaved draft line is local state only — removing it stays
      // available while the draft is dirty (it IS the draft).
      setLines((current) => current.filter((l) => l.id !== line.id));
      setLineErrors((current) => current.filter((e) => e.lineId !== line.id));
      setDirty(true);
      showNotice('✕ Draft line removed — it had never been saved.');
      return;
    }
    // Removing a SAVED line is a committed server action — like
    // Release, it never runs with unsaved edits in flight (the button
    // is disabled; this guard covers any other path).
    if (dirty) return;
    setConfirmRemove(line);
  }

  async function removeSavedLine(line: DemandLineDraft) {
    if (line.demandId === null) return;
    setConfirmRemove(null);
    setBusy(true);
    setServerError(null);
    try {
      await deleteWorkOrderDemand(workOrderId, line.demandId);
      const fresh = await getWorkOrder(workOrderId);
      // Re-apply the still-unsaved draft edits of the OTHER lines on
      // top of the fresh state? No — removal commits alone; the other
      // lines keep their local draft values below.
      setDetail(fresh);
      setLines((current) => current.filter((l) => l.id !== line.id));
      setLineErrors((current) => current.filter((e) => e.lineId !== line.id));
      setBusy(false);
      onChanged();
      showNotice(`✕ ${line.pn} removed from ${woDisplay}.`);
    } catch (error) {
      setBusy(false);
      setServerError(errorMessage(error));
      showNotice(`✕ ${errorMessage(error)}`);
    }
  }

  async function saveDetail() {
    if (!detail) return;
    setBusy(true);
    setServerError(null);
    try {
      const fresh = await updateWorkOrder(workOrderId, {
        // The audited external-number edit: only an entered value
        // travels, and it travels VERBATIM (never trimmed/reformatted
        // — trimming only detected that something was entered).
        ...(numberEntered ? { workOrderNumber: numberDraft } : {}),
        ...((due || null) !== detail.dueDate ? { dueDate: due || null } : {}),
        lineEdits: buildLineEdits(display, detail.demands),
        newLines: display
          .filter((line) => line.demandId === null)
          .map(draftToNewLine),
      });
      // Refresh from the committed server state — never a simulated
      // local success.
      adoptDetail(fresh);
      setBusy(false);
      onChanged();
      showNotice(
        `💾 WO ${fresh.workOrderNumber ?? '—'} demand updated — business demand only.`,
      );
    } catch (error) {
      setBusy(false);
      setServerError(errorMessage(error));
      showNotice(`✕ ${errorMessage(error)} The entered draft was kept.`);
    }
  }

  function handleSave() {
    if (!detail || busy) return;
    const errors = validateDemandLines(display);
    setLineErrors(errors);
    if (errors.length) {
      showNotice(
        '✕ The Work Order has invalid demand lines — fix them to save. Entered values are preserved; incomplete rows are never dropped silently.',
      );
      const first = errors[0];
      const el =
        fieldRefs.current.get(`${first.lineId}:${first.field}`) ??
        fieldRefs.current.get(`${first.lineId}:qty`);
      el?.focus();
      return;
    }
    // Missing due dates are valid — but they are summarized and
    // explicitly confirmed, never silently saved. Released lines are
    // read-only history and are not re-confirmed.
    const missing = collectMissingDemandInfo(
      numberEntered ? numberDraft : (detail.workOrderNumber ?? ''),
      due,
      display.filter((line) => !line.released),
    );
    if (missing) {
      setConfirmMissing(missing);
      return;
    }
    void saveDetail();
  }

  // Every close request (Cancel, Escape, backdrop) funnels through
  // here: a dirty draft is never discarded silently.
  function requestClose() {
    if (dirty) setConfirmDiscard(true);
    else onClose();
  }

  return (
    <>
      <ModalDialog labelledBy={headingId} onClose={requestClose} size="xwide">
        <div className="wo-head">
          <h2 id={headingId} className="nwo-title">
            Work Order Details
          </h2>
          <span className="spacer" />
          {(editable || numberEditVisible) && dirty ? (
            <span className="unsaved">● Unsaved changes</span>
          ) : null}
        </div>
        <div className="big mono">{woDisplay}</div>
        {numberEditVisible ? (
          <div className="wo-numedit">
            <label htmlFor="wo-extnum">
              External WO Number{' '}
              <span className="field-optional">(optional — audited edit)</span>
            </label>
            <input
              id="wo-extnum"
              className="mono"
              placeholder="e.g. 007482"
              value={numberDraft}
              onChange={(e) => {
                setNumberDraft(e.target.value);
                setDirty(true);
              }}
            />
          </div>
        ) : null}
        <p className="wo-sub">
          received <b className="mono">{formatIsoDate(detail.receivedDate)}</b>{' '}
          ·{' '}
          {editable ? (
            <>
              WO due date{' '}
              <input
                type="date"
                className="mono wo-due-input"
                value={due}
                aria-label="WO due date"
                onChange={(e) => handleDueChange(e.target.value)}
              />
              {due === '' ? (
                <span className="duetxt none"> no due date</span>
              ) : null}
            </>
          ) : (
            <>
              WO due date{' '}
              <b className="mono">{formatIsoDate(detail.dueDate)}</b>
            </>
          )}{' '}
          · {display.length} demand line{display.length === 1 ? '' : 's'} ·{' '}
          <span
            className={`wostat ${workOrderStatusLabel(detail.status).toLowerCase()}`}
          >
            {workOrderStatusLabel(detail.status)}
          </span>
          {internal
            ? ' · internal Work Order — no external number yet (displays —)'
            : ''}
        </p>
        {display.length === 0 && !editable ? (
          <EmptyState message="This Work Order has no demand lines." />
        ) : null}
        <div className="wo-card">
          {editable && (
            <div className="woc-head">
              <span className="meta">
                Demand lines — each line's due date defaults to the{' '}
                <b>WO due date</b> and may be edited per line. Edits stay an
                unsaved draft until <b>Save demand</b>.
              </span>
              <span className="spacer" />
              {dirty ? (
                <span className="unsaved">● Unsaved changes</span>
              ) : null}
            </div>
          )}
          <div className="wo-lines">
            <table className="wo-table">
              <thead>
                <tr>
                  <th>PN</th>
                  <th>Request Type</th>
                  <th>Qty</th>
                  <th>Due date</th>
                  <th>Job Numbers</th>
                  <th>Status</th>
                  {editable ? <th></th> : null}
                </tr>
              </thead>
              <tbody>
                {display.map((line) => {
                  const rowEditable = editable && !line.released;
                  const removeRule = lineRemoveRule(line);
                  return (
                    <tr key={line.id}>
                      <td
                        data-label="PN"
                        className={errorFor(line.id, 'pn') ? 'err-cell' : ''}
                      >
                        {line.pn ? (
                          <div className="pn" title={line.pn}>
                            {line.pn}
                          </div>
                        ) : rowEditable ? (
                          <input
                            ref={(el) => {
                              if (el)
                                fieldRefs.current.set(`${line.id}:pn`, el);
                              else fieldRefs.current.delete(`${line.id}:pn`);
                            }}
                            placeholder="type PN — lookup or create"
                            size={16}
                            aria-label="PartNumber lookup or create"
                            aria-invalid={
                              errorFor(line.id, 'pn') ? true : undefined
                            }
                            onBlur={(e) => {
                              // Normalize to the canonical uppercase PN:
                              // surrounding whitespace trims away, and
                              // internal whitespace is invalid — never
                              // silently removed.
                              if (!e.target.value.trim()) return;
                              const pn = normalizePartNumber(e.target.value);
                              if (!pn) {
                                showNotice(
                                  '⚠ A Part Number cannot contain spaces or other whitespace inside the value — correct the entry.',
                                );
                                return;
                              }
                              const duplicate = display.find(
                                (l) =>
                                  l.id !== line.id &&
                                  l.pn !== null &&
                                  l.pn === pn,
                              );
                              if (duplicate) {
                                showNotice(
                                  `⚠ ${pn} is already on this Work Order — edit the existing line instead of adding a duplicate.`,
                                );
                                e.target.value = '';
                                if (!duplicate.released) {
                                  setFocusField({
                                    id: duplicate.id,
                                    field: 'qty',
                                  });
                                }
                                return;
                              }
                              clearLineError(line.id, 'pn');
                              updateLine(line.id, {
                                pn,
                                barcodeNote: `new PN — barcode PF:PN:${pn}`,
                                isNewPn: true,
                              });
                              setFocusField({
                                id: line.id,
                                field: 'qty',
                              });
                            }}
                          />
                        ) : (
                          <div
                            className="pn"
                            style={{
                              color: 'var(--faint)',
                            }}
                          >
                            —
                          </div>
                        )}
                        <div className={`bc ${line.isNewPn ? 'newpn' : ''}`}>
                          {line.barcodeNote}
                        </div>
                        {line.notes ? (
                          <div className="bc">notes: {line.notes}</div>
                        ) : null}
                        {errorFor(line.id, 'pn') ? (
                          <div className="rowerr">
                            {errorFor(line.id, 'pn')}
                          </div>
                        ) : null}
                      </td>
                      <td data-label="Request Type">
                        {rowEditable ? (
                          <select
                            value={line.type}
                            aria-label={`Request Type for ${line.pn ?? 'new line'}`}
                            onChange={(e) =>
                              updateLine(line.id, {
                                type: e.target.value as RequestType,
                              })
                            }
                          >
                            <option>NEW</option>
                            <option>MODIFY</option>
                          </select>
                        ) : (
                          <TypeChip type={line.type} />
                        )}
                      </td>
                      <td
                        data-label="Qty"
                        className={errorFor(line.id, 'qty') ? 'err-cell' : ''}
                      >
                        {rowEditable ? (
                          <>
                            <input
                              ref={(el) => {
                                if (el)
                                  fieldRefs.current.set(`${line.id}:qty`, el);
                                else fieldRefs.current.delete(`${line.id}:qty`);
                              }}
                              className="mono"
                              size={4}
                              inputMode="numeric"
                              placeholder="qty"
                              value={line.qty}
                              aria-label={`Quantity for ${line.pn ?? 'new line'}`}
                              aria-invalid={
                                errorFor(line.id, 'qty') ? true : undefined
                              }
                              onChange={(e) => {
                                clearLineError(line.id, 'qty');
                                updateLine(line.id, {
                                  qty: e.target.value,
                                });
                              }}
                              onKeyDown={(e) => {
                                if (e.key !== 'Enter') return;
                                if (!isPositiveInteger(e.currentTarget.value)) {
                                  showNotice(
                                    '✕ Quantity must be a positive whole number',
                                  );
                                  e.currentTarget.select();
                                  return;
                                }
                                scanRef.current?.focus();
                              }}
                            />
                            {errorFor(line.id, 'qty') ? (
                              <div className="rowerr">
                                {errorFor(line.id, 'qty')}
                              </div>
                            ) : null}
                          </>
                        ) : (
                          <span className="mono">{line.qty}</span>
                        )}
                      </td>
                      <td data-label="Due date">
                        {rowEditable ? (
                          <>
                            <input
                              ref={(el) => {
                                if (el)
                                  fieldRefs.current.set(`${line.id}:due`, el);
                                else fieldRefs.current.delete(`${line.id}:due`);
                              }}
                              type="date"
                              className="mono"
                              value={line.due}
                              aria-label={`Due date for ${line.pn ?? 'new line'}`}
                              onChange={(e) => {
                                updateLine(line.id, {
                                  due: e.target.value,
                                  dueTouched: true,
                                });
                              }}
                            />
                            {line.due === '' ? (
                              <div className="bc">No due date</div>
                            ) : null}
                          </>
                        ) : (
                          <span className="mono">
                            {formatIsoDate(line.due || null)}
                          </span>
                        )}
                      </td>
                      <td data-label="Job Numbers">
                        {rowEditable ? (
                          <input
                            className="mono"
                            size={10}
                            placeholder="job #…"
                            value={line.job}
                            aria-label={`Job Numbers for ${line.pn ?? 'new line'}`}
                            onChange={(e) =>
                              updateLine(line.id, {
                                job: e.target.value,
                              })
                            }
                          />
                        ) : (
                          <span className="mono">{line.job || '—'}</span>
                        )}
                      </td>
                      <td data-label="Status">
                        <span
                          className={`linestat ${
                            line.released
                              ? 'released'
                              : line.saved
                                ? 'saved'
                                : 'draft'
                          }`}
                        >
                          {line.statusLabel}
                        </span>
                      </td>
                      {editable ? (
                        <td data-label="" className="wo-cell-actions">
                          <div className="wo-rowactions">
                            {line.demandId !== null ? (
                              <button
                                className="rel-btn"
                                disabled={
                                  writeBlocked ||
                                  busy ||
                                  line.released ||
                                  // A release must run against the
                                  // COMMITTED demand — never with
                                  // unsaved edits in flight.
                                  dirty
                                }
                                title={
                                  dirty && !line.released
                                    ? RELEASE_WHILE_DIRTY_EXPLANATION
                                    : undefined
                                }
                                onClick={() => {
                                  // The committed server values — the
                                  // local draft equals them here (a
                                  // dirty draft disables this button).
                                  const committed = detail.demands.find(
                                    (demand) => demand.id === line.demandId,
                                  );
                                  if (committed) {
                                    setReleaseDialog({
                                      demandId: committed.id,
                                      partNumber: committed.partNumber,
                                      requestedQuantity:
                                        committed.requestedQuantity,
                                    });
                                  }
                                }}
                              >
                                Release to production…
                              </button>
                            ) : null}
                            <button
                              className="pr-x"
                              disabled={
                                removeRule === 'blocked' ||
                                busy ||
                                (removeRule === 'confirm' &&
                                  // A saved-line removal commits on the
                                  // server — never with unsaved edits
                                  // in flight (same rule as Release).
                                  (writeBlocked || dirty))
                              }
                              title={
                                removeRule === 'blocked'
                                  ? RELEASED_REMOVE_EXPLANATION
                                  : removeRule === 'confirm'
                                    ? dirty
                                      ? REMOVE_WHILE_DIRTY_EXPLANATION
                                      : 'Remove line (asks for confirmation)'
                                    : 'Remove draft line'
                              }
                              aria-label={
                                line.pn
                                  ? `Remove line ${line.pn}`
                                  : 'Remove draft line'
                              }
                              onClick={() => requestRemove(line)}
                            >
                              ✕
                            </button>
                          </div>
                          {removeRule === 'blocked' ? (
                            <div className="bc">
                              {RELEASED_REMOVE_EXPLANATION}
                            </div>
                          ) : null}
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {editable && (
            <div className="wo-addpart">
              <div className="nwo-scanrow">
                <button
                  className="btn primary"
                  disabled={writeBlocked}
                  onClick={() => setAddPartOpen(true)}
                >
                  ＋ Add Part manually
                </button>
                <input
                  ref={scanRef}
                  className="nwo-scan"
                  placeholder="Optional: scan PN barcode (PF:PN:…) — Enter"
                  aria-label="Scan PN barcode"
                  autoComplete="off"
                  disabled={writeBlocked}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleScan(e.currentTarget.value);
                      e.currentTarget.value = '';
                    }
                  }}
                />
              </div>
              <div className="nwo-hint">
                Scanning is optional: a PN barcode adds an <b>unsaved draft</b>{' '}
                line; a PN already on this Work Order focuses its existing line
                instead of adding a duplicate.
              </div>
            </div>
          )}
        </div>
        <PageNote>
          A demand line can be removed only while no production quantity has
          been released for it — a released line stays; later adjustments go
          through the correction workflows. Removal never deletes the Part, its
          production quantity, or its history.
        </PageNote>
        {serverError ? (
          <div className="rowerr" role="alert">
            {serverError}
          </div>
        ) : null}
        <div className="wo-actions nwo-actions">
          <button className="btn ghost" onClick={requestClose}>
            Cancel (Esc)
          </button>
          {editable || numberEditVisible ? (
            <button
              className="btn primary"
              disabled={writeBlocked || busy}
              onClick={handleSave}
            >
              {busy ? 'Saving…' : 'Save demand'}
            </button>
          ) : null}
          <span className="hint">
            {editable ? (
              dirty ? (
                <>
                  Saving stores <b>business demand only</b>.{' '}
                  <b>{RELEASE_WHILE_DIRTY_EXPLANATION}</b>{' '}
                  <b>{REMOVE_WHILE_DIRTY_EXPLANATION}</b>
                </>
              ) : (
                <>
                  Saving stores <b>business demand only</b> — release to
                  production stays a separate explicit step. Invalid rows cannot
                  be saved and are never dropped silently.
                </>
              )
            ) : (
              <>
                This Work Order is <b>{workOrderStatusLabel(detail.status)}</b>{' '}
                — demand lines are read-only. Editing is available only while a
                Work Order is Open.
              </>
            )}
          </span>
        </div>
      </ModalDialog>

      {/* Stacked dialogs render as siblings of the details dialog, so
          each one's Escape / backdrop / focus trap stays its own and a
          child close never closes Work Order Details. */}
      {releaseDialog ? (
        <ReleaseDialog
          workOrderId={workOrderId}
          workOrderNumber={detail.workOrderNumber}
          demand={releaseDialog}
          writeBlocked={writeBlocked}
          onCancel={() => {
            setReleaseDialog(null);
            showNotice('✕ Release cancelled — nothing was created.');
          }}
          onReleased={(result) => {
            setReleaseDialog(null);
            // The Released/read-only state comes back from the server
            // (release evidence in the reloaded demand lines) — never
            // from a session-local flag.
            retryLoad();
            onChanged();
            showNotice(
              `✓ ${result.partNumber} released to production × ${result.quantity} · Quantity Flow #${result.quantityFlowId}.`,
            );
          }}
        />
      ) : null}

      {addPartOpen ? (
        <AddPartDialog
          workOrderDue={due}
          existingPns={display.flatMap((l) => (l.pn ? [l.pn] : []))}
          onCancel={() => setAddPartOpen(false)}
          onDuplicate={handleAddPartDuplicate}
          onComplete={handleAddPartComplete}
        />
      ) : null}

      {confirmMissing ? (
        <ConfirmDialog
          title="Save demand with missing information?"
          confirmLabel="Confirm and save"
          cancelLabel="Cancel — keep editing"
          onConfirm={() => {
            setConfirmMissing(null);
            void saveDetail();
          }}
          onCancel={() => setConfirmMissing(null)}
        >
          These omissions are valid — confirm them explicitly:
          <ul className="missinglist">
            {confirmMissing.noWorkOrderDue ? (
              <li>
                No WO due date — the Work Order remains <b>unscheduled</b> until
                a due date is added.
              </li>
            ) : null}
            {confirmMissing.undatedLineCount ? (
              <li>
                <b>{confirmMissing.undatedLineCount}</b> demand line
                {confirmMissing.undatedLineCount === 1 ? ' has' : 's have'} no
                due date — they receive the lowest date priority and order by
                the Work Order received date.
              </li>
            ) : null}
          </ul>
          Cancel returns to editing with every entered value preserved.
        </ConfirmDialog>
      ) : null}

      {confirmRemove ? (
        <ConfirmDialog
          title={`Remove ${confirmRemove.pn ?? 'line'} from ${woDisplay}?`}
          confirmLabel="Remove line"
          cancelLabel="Cancel — keep the line"
          danger
          onConfirm={() => void removeSavedLine(confirmRemove)}
          onCancel={() => setConfirmRemove(null)}
        >
          Removing this saved Work Order Demand line (
          <span className="mono">{confirmRemove.pn}</span> · qty{' '}
          {confirmRemove.qty || '—'}) is applied immediately. It is blocked once
          any production quantity has been released for the line, and the last
          demand line of a Work Order cannot be removed. Removal never deletes
          the PartNumber master, production quantity, movement history, release
          history, or other Work Order Demand for the same PN.
        </ConfirmDialog>
      ) : null}

      {confirmDiscard ? (
        <ConfirmDialog
          title="Discard unsaved demand changes?"
          confirmLabel="Discard changes"
          cancelLabel="Keep editing"
          danger
          onConfirm={onClose}
          onCancel={() => setConfirmDiscard(false)}
        >
          This Work Order has unsaved demand edits. Closing Work Order Details
          discards them; Keep editing returns with every entered value
          preserved.
        </ConfirmDialog>
      ) : null}
    </>
  );
}
