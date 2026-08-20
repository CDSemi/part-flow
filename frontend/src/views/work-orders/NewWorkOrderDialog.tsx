import { useEffect, useId, useRef, useState } from 'react';

import { errorMessage } from '../../api/client';
import { createWorkOrder, resolveWorkOrderNumber } from '../../api/work-orders';
import type { WorkOrderDetail, WorkOrderSummary } from '../../api/work-orders';
import { resolvePartNumber } from '../../api/part-numbers';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { TypeChip } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { todayIso } from '../dates';
import type { RequestType } from '../view-models';
import { AddPartDialog } from './AddPartDialog';
import type { AddPartResult } from './AddPartDialog';
import {
  applyWorkOrderDueDateChange,
  collectMissingDemandInfo,
  createDraftLine,
  draftToNewLine,
  isPositiveInteger,
  processScan,
  validateDemandLines,
} from './demand-lines';
import type {
  DemandLineDraft,
  LineError,
  LineField,
  MissingDemandInfo,
} from './demand-lines';
import { PnBarcodeLabelDialog } from './PnBarcodeLabelDialog';

interface HeaderErrors {
  received?: string;
}

/**
 * New Work Order entry as a modal dialog over the Work Orders list
 * (GUI_DESIGN): the header inputs (WO Number, WO due date) are both
 * optional — a blank WO Number saves an internal Work Order with a
 * NULL number that displays as `—` (no temporary number is ever
 * generated; the real external number can be added later through an
 * audited edit). Manual Part addition (multi-step Add Part dialog) is
 * the primary workflow; barcode scanning stays available as a
 * secondary method. The URL never changes; closing with entered data
 * requires explicit confirmation. Save demand is ONE POST — one
 * all-or-nothing backend transaction; a failed save keeps the whole
 * draft and every entered value.
 */
export function NewWorkOrderDialog({
  writeBlocked,
  onClose,
  onOpenExisting,
  onSaved,
  onDirtyChange,
  showNotice,
}: {
  writeBlocked: boolean;
  onClose: () => void;
  /** An entered WO Number already exists — open it, never duplicate. */
  onOpenExisting: (existing: WorkOrderSummary) => void;
  /** The Work Order was committed by the backend. */
  onSaved: (detail: WorkOrderDetail) => void;
  onDirtyChange: (dirty: boolean) => void;
  showNotice: (message: string) => void;
}) {
  const headingId = useId();
  const [workOrderNumber, setWorkOrderNumber] = useState('');
  const initialReceived = useRef(todayIso());
  const [received, setReceived] = useState(initialReceived.current);
  const [due, setDue] = useState('');
  const [lines, setLines] = useState<DemandLineDraft[]>([]);
  const [headerErrors, setHeaderErrors] = useState<HeaderErrors>({});
  const [lineErrors, setLineErrors] = useState<LineError[]>([]);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [confirmExisting, setConfirmExisting] =
    useState<WorkOrderSummary | null>(null);
  const [confirmMissing, setConfirmMissing] =
    useState<MissingDemandInfo | null>(null);
  const [addPartOpen, setAddPartOpen] = useState(false);
  // The printable PN barcode label of one entered line (§10 — the
  // barcode derives from the PN identity itself).
  const [labelPn, setLabelPn] = useState<string | null>(null);
  // One in-flight server interaction at a time (duplicate resolution
  // or the save itself); a failed write keeps the draft and shows the
  // server's message here.
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const workOrderNumRef = useRef<HTMLInputElement>(null);
  const receivedRef = useRef<HTMLInputElement>(null);
  const scanRef = useRef<HTMLInputElement>(null);
  const fieldRefs = useRef(new Map<string, HTMLInputElement>());
  const [focusField, setFocusField] = useState<{
    id: number;
    field: LineField;
  } | null>(null);

  const dirty =
    workOrderNumber.trim() !== '' ||
    due !== '' ||
    received !== initialReceived.current ||
    lines.length > 0;

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  // Initial focus: the first header field.
  useEffect(() => {
    workOrderNumRef.current?.focus();
  }, []);

  useEffect(() => {
    if (focusField) {
      const el = fieldRefs.current.get(`${focusField.id}:${focusField.field}`);
      el?.focus();
      el?.select();
      setFocusField(null);
    }
  }, [focusField, lines]);

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
    showNotice(
      `✓ ${pn} added — Request Type NEW · due date from WO due date. Type the quantity.`,
    );
    setFocusField({ id: line.id, field: 'qty' });
  }

  function handleScan(value: string) {
    const result = processScan(value, lines);
    if (!result) return;
    if (result.kind === 'invalid') {
      showNotice(
        `✕ Unknown barcode “${result.barcode}” — nothing added. Only PN barcodes (PF:PN:…) add demand lines.`,
      );
      scanRef.current?.focus();
      return;
    }
    if (result.kind === 'duplicate') {
      showNotice(
        `⚠ ${result.pn} is already on this Work Order — edit its quantity instead of adding a duplicate line.`,
      );
      setFocusField({ id: result.lineId, field: 'qty' });
      return;
    }
    // Whether the PN already has a master record is a server lookup —
    // a miss means create-on-first-use with the Save transaction.
    void resolvePartNumber(result.pn).then(
      (master) => addScannedLine(result.pn, result.barcode, master === null),
      (error: unknown) => showNotice(`✕ ${errorMessage(error)}`),
    );
  }

  function handleAddPartComplete(result: AddPartResult) {
    setAddPartOpen(false);
    const line = createDraftLine(result);
    setLines((current) => [...current, line]);
    showNotice(
      `✓ ${result.pn} added as an editable draft line — nothing is saved until Save demand.`,
    );
  }

  function handleAddPartDuplicate(pn: string) {
    setAddPartOpen(false);
    const duplicate = lines.find((l) => l.pn === pn);
    showNotice(
      `⚠ ${pn} is already on this Work Order — edit the existing line instead of adding a duplicate.`,
    );
    if (duplicate) setFocusField({ id: duplicate.id, field: 'qty' });
  }

  function handleDueChange(value: string) {
    setDue(value);
    // The WO due date is the default — update lines still holding it.
    setLines((current) => applyWorkOrderDueDateChange(current, value));
  }

  function focusFirstInvalid(headerErr: HeaderErrors, lineErrs: LineError[]) {
    if (headerErr.received) return receivedRef.current?.focus();
    const first = lineErrs[0];
    if (!first) return;
    const el =
      fieldRefs.current.get(`${first.lineId}:${first.field}`) ??
      fieldRefs.current.get(`${first.lineId}:qty`);
    el?.focus();
  }

  // The entered Work Order Number is an opaque string that is NEVER
  // reformatted (PROJECT_PROFILE §7): trimming exists only to DETECT a
  // blank entry — a blank saves NULL, a non-blank value travels (and
  // resolves duplicates) exactly as entered, surrounding whitespace
  // included.
  const numberIsBlank = workOrderNumber.trim() === '';

  async function saveWorkOrder() {
    // A blank number is saved as NULL — displayed as `—` (the
    // placeholder itself is never persisted); multiple Work Orders may
    // have a null number while non-null numbers stay unique. The POST
    // is one all-or-nothing transaction.
    setBusy(true);
    setServerError(null);
    try {
      const detail = await createWorkOrder({
        workOrderNumber: numberIsBlank ? null : workOrderNumber,
        receivedDate: received,
        dueDate: due || null,
        lines: lines.map(draftToNewLine),
      });
      onSaved(detail);
    } catch (error) {
      // A lost duplicate race commits nothing — resolve the ORIGINAL
      // entered value and open the existing Work Order exactly like
      // the pre-check would have.
      if (!numberIsBlank) {
        try {
          const existing = await resolveWorkOrderNumber(workOrderNumber);
          if (existing) {
            setBusy(false);
            setConfirmExisting(existing);
            return;
          }
        } catch {
          // fall through to the original error
        }
      }
      setBusy(false);
      setServerError(errorMessage(error));
      showNotice(`✕ ${errorMessage(error)} The entered draft was kept.`);
    }
  }

  async function handleSave() {
    if (busy) return;
    if (!numberIsBlank) {
      // An entered WO Number that already exists is opened, never
      // duplicated (exact verbatim resolution on the server). With
      // entered lines, opening discards them — confirm explicitly.
      // (Does not apply to blank WO Numbers.)
      setBusy(true);
      setServerError(null);
      let existing: WorkOrderSummary | null;
      try {
        // Verbatim equality — the ORIGINAL entered string, untrimmed.
        existing = await resolveWorkOrderNumber(workOrderNumber);
      } catch (error) {
        setBusy(false);
        setServerError(errorMessage(error));
        showNotice(`✕ ${errorMessage(error)} The entered draft was kept.`);
        return;
      }
      setBusy(false);
      if (existing) {
        if (lines.length === 0) onOpenExisting(existing);
        else setConfirmExisting(existing);
        return;
      }
    }
    const headerErr: HeaderErrors = {};
    if (!received) headerErr.received = 'received date is required';
    const lineErrs = validateDemandLines(lines);
    setHeaderErrors(headerErr);
    setLineErrors(lineErrs);
    if (headerErr.received || lineErrs.length) {
      showNotice(
        '✕ The form has invalid fields — fix them to save. Entered values are preserved; incomplete rows are never dropped silently.',
      );
      focusFirstInvalid(headerErr, lineErrs);
      return;
    }
    if (lines.length === 0) {
      showNotice('✕ Add at least one demand line (＋ Add Part manually)');
      return;
    }
    // Absent WO Number / due dates are NOT validation errors — they are
    // summarized and explicitly confirmed before saving.
    const missing = collectMissingDemandInfo(workOrderNumber, due, lines);
    if (missing) {
      setConfirmMissing(missing);
      return;
    }
    await saveWorkOrder();
  }

  function requestClose() {
    if (dirty) setConfirmDiscard(true);
    else onClose();
  }

  return (
    <>
      <ModalDialog labelledBy={headingId} onClose={requestClose} size="xwide">
        <div className="nwo">
          <h2 id={headingId} className="nwo-title">
            New Work Order
          </h2>
          <p className="wo-sub">
            Enter the Work Order header and add its Parts below. New lines
            default to Request Type <TypeChip type="NEW" /> and the WO due date.
          </p>

          <div className="nwo-form">
            <div className="nwo-field nwo-field-num">
              <label htmlFor="nwo-num">
                WO Number <span className="field-optional">(optional)</span>
              </label>
              <input
                id="nwo-num"
                ref={workOrderNumRef}
                className="mono"
                placeholder="e.g. 007482"
                value={workOrderNumber}
                onChange={(e) => setWorkOrderNumber(e.target.value)}
              />
            </div>
            <div className="nwo-field">
              <label htmlFor="nwo-recv">Received date</label>
              <input
                id="nwo-recv"
                ref={receivedRef}
                type="date"
                className="mono"
                value={received}
                aria-invalid={headerErrors.received ? true : undefined}
                onChange={(e) => {
                  setReceived(e.target.value);
                  setHeaderErrors({});
                }}
              />
              {headerErrors.received ? (
                <div className="rowerr">{headerErrors.received}</div>
              ) : null}
            </div>
            <div className="nwo-field">
              <label htmlFor="nwo-due">
                WO due date <span className="field-optional">(optional)</span>
              </label>
              <input
                id="nwo-due"
                type="date"
                className="mono"
                value={due}
                onChange={(e) => handleDueChange(e.target.value)}
              />
            </div>
          </div>

          <div className="nwo-scanrow">
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
            <button
              className="btn primary"
              disabled={writeBlocked}
              onClick={() => setAddPartOpen(true)}
            >
              ＋ Add Part manually
            </button>
          </div>
          <div className="nwo-lines-title">Demand lines</div>

          <div className="wo-lines nwo-lines">
            <table className="wo-table">
              <thead>
                <tr>
                  <th className="col-pn">PN</th>
                  <th className="col-type">Req. Type</th>
                  <th className="col-qty">Qty</th>
                  <th className="col-due">Due date</th>
                  <th className="col-job">Job Numbers</th>
                  <th className="col-notes">Notes</th>
                  <th className="col-x"></th>
                </tr>
              </thead>
              <tbody>
                {lines.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="nwo-empty">
                      No demand lines yet — add the first Part with ＋ Add Part
                      manually
                    </td>
                  </tr>
                ) : (
                  lines.map((line) => (
                    <tr key={line.id}>
                      <td
                        data-label="PN"
                        className={errorFor(line.id, 'pn') ? 'err-cell' : ''}
                      >
                        {/* Lines always carry a PN here: they come from
                            the Add Part flow or a valid PN barcode. */}
                        <div className="pn" title={line.pn ?? ''}>
                          {line.pn}
                        </div>
                        {line.pn ? (
                          <button
                            className="pn-labellink"
                            onClick={() => setLabelPn(line.pn)}
                          >
                            Barcode label…
                          </button>
                        ) : null}
                        {errorFor(line.id, 'pn') ? (
                          <div className="rowerr">
                            {errorFor(line.id, 'pn')}
                          </div>
                        ) : null}
                      </td>
                      <td data-label="Request Type">
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
                      </td>
                      <td
                        data-label="Qty"
                        className={errorFor(line.id, 'qty') ? 'err-cell' : ''}
                      >
                        <input
                          ref={(el) => {
                            if (el) fieldRefs.current.set(`${line.id}:qty`, el);
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
                            updateLine(line.id, { qty: e.target.value });
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
                      </td>
                      <td data-label="Due date">
                        <input
                          ref={(el) => {
                            if (el) fieldRefs.current.set(`${line.id}:due`, el);
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
                      </td>
                      <td data-label="Job Numbers">
                        <input
                          className="mono"
                          size={10}
                          placeholder="job #…"
                          value={line.job}
                          aria-label={`Job Numbers for ${line.pn ?? 'new line'}`}
                          onChange={(e) =>
                            updateLine(line.id, { job: e.target.value })
                          }
                        />
                      </td>
                      <td data-label="Notes">
                        <input
                          size={10}
                          placeholder="notes…"
                          value={line.notes}
                          aria-label={`Notes for ${line.pn ?? 'new line'}`}
                          onChange={(e) =>
                            updateLine(line.id, { notes: e.target.value })
                          }
                        />
                      </td>
                      <td data-label="" className="wo-cell-actions">
                        <button
                          className="pr-x"
                          title="Remove draft line"
                          aria-label={
                            line.pn
                              ? `Remove line ${line.pn}`
                              : 'Remove draft line'
                          }
                          onClick={() =>
                            setLines((current) =>
                              current.filter((l) => l.id !== line.id),
                            )
                          }
                        >
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {serverError ? (
            <div className="rowerr" role="alert">
              {serverError}
            </div>
          ) : null}
          <div className="row nwo-actions">
            <button className="bigbtn ghost" onClick={requestClose}>
              Cancel (Esc)
            </button>
            <button
              className="bigbtn primary"
              disabled={writeBlocked || busy}
              onClick={() => void handleSave()}
            >
              {busy ? 'Saving…' : 'Save demand'}
            </button>
          </div>
        </div>
      </ModalDialog>

      {addPartOpen ? (
        <AddPartDialog
          workOrderDue={due}
          existingPns={lines.flatMap((l) => (l.pn ? [l.pn] : []))}
          onCancel={() => setAddPartOpen(false)}
          onDuplicate={handleAddPartDuplicate}
          onComplete={handleAddPartComplete}
        />
      ) : null}

      {labelPn !== null ? (
        <PnBarcodeLabelDialog pn={labelPn} onClose={() => setLabelPn(null)} />
      ) : null}

      {confirmMissing ? (
        <ConfirmDialog
          title="Save demand with missing information?"
          confirmLabel="Confirm and save"
          cancelLabel="Cancel — keep editing"
          onConfirm={() => {
            setConfirmMissing(null);
            void saveWorkOrder();
          }}
          onCancel={() => setConfirmMissing(null)}
        >
          These omissions are valid — confirm them explicitly:
          <ul className="missinglist">
            {confirmMissing.noWorkOrderNumber ? (
              <li>
                No external WO Number — the Work Order is saved as an{' '}
                <b>internal Work Order without an external number</b>. It
                displays as <span className="mono">—</span> (never persisted as
                a placeholder) and the real number can be added later through an
                audited edit.
              </li>
            ) : null}
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

      {confirmDiscard ? (
        <ConfirmDialog
          title="Discard this New Work Order?"
          confirmLabel="Discard entries"
          cancelLabel="Keep editing"
          danger
          onConfirm={onClose}
          onCancel={() => setConfirmDiscard(false)}
        >
          The entered Work Order header
          {lines.length > 0 ? (
            <>
              {' '}
              and <b>{lines.length}</b> demand line
              {lines.length === 1 ? '' : 's'}
            </>
          ) : null}{' '}
          have not been saved. Closing the dialog discards them — nothing was
          persisted.
        </ConfirmDialog>
      ) : null}

      {confirmExisting ? (
        <ConfirmDialog
          title={`${confirmExisting.workOrderNumber ?? ''} already exists`}
          confirmLabel="Open existing Work Order"
          cancelLabel="Keep editing"
          onConfirm={() => onOpenExisting(confirmExisting)}
          onCancel={() => setConfirmExisting(null)}
        >
          A WO Number is never duplicated —{' '}
          <b>{confirmExisting.workOrderNumber}</b> will be opened instead. The{' '}
          {lines.length} line
          {lines.length === 1 ? '' : 's'} entered here will be discarded.
        </ConfirmDialog>
      ) : null}
    </>
  );
}
