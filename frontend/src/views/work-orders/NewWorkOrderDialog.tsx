import { useEffect, useId, useRef, useState } from 'react';

import { ConfirmDialog } from '../../components/ConfirmDialog';
import { TypeChip } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { todayIso } from '../dates';
import type { MockWorkOrder, RequestType } from '../view-models';
import { AddPartDialog } from './AddPartDialog';
import type { AddPartResult } from './AddPartDialog';
import {
  applyWorkOrderDueDateChange,
  collectMissingDemandInfo,
  createDraftLine,
  draftsToSavedLines,
  isPositiveInteger,
  linesPreview,
  processScan,
  validateDemandLines,
} from './demand-lines';
import type {
  DemandLineDraft,
  LineError,
  LineField,
  MissingDemandInfo,
} from './demand-lines';

let nextInternalWorkOrderId = 1;

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
 * requires explicit confirmation. Phase 2: saving changes local mock
 * state only.
 */
export function NewWorkOrderDialog({
  existing,
  writeBlocked,
  onClose,
  onOpenExisting,
  onSave,
  onDirtyChange,
  showNotice,
}: {
  existing: string[];
  writeBlocked: boolean;
  onClose: () => void;
  onOpenExisting: (workOrderNumber: string) => void;
  onSave: (workOrder: MockWorkOrder) => void;
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
  const [confirmExisting, setConfirmExisting] = useState<string | null>(null);
  const [confirmMissing, setConfirmMissing] =
    useState<MissingDemandInfo | null>(null);
  const [addPartOpen, setAddPartOpen] = useState(false);

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
    const line = createDraftLine({
      pn: result.pn,
      isNewPn: result.isNewPn,
      barcodeNote: result.isNewPn
        ? `new PN — barcode created with PN master: ${result.barcode}`
        : `existing PN · barcode ${result.barcode}`,
      due,
    });
    setLines((current) => [...current, line]);
    showNotice(
      `✓ ${result.pn} added — Request Type NEW · due date from WO due date. Type the quantity.`,
    );
    setFocusField({ id: line.id, field: 'qty' });
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

  function saveWorkOrder() {
    // Entered Work Order Numbers stay opaque strings (never
    // reformatted). A blank number is saved as NULL — displayed as `—`
    // (the placeholder itself is never persisted); multiple Work
    // Orders may have a null number while non-null numbers stay
    // unique.
    const entered = workOrderNumber.trim();
    const savedLines = draftsToSavedLines(lines);
    onSave({
      id: `wo-manual-${nextInternalWorkOrderId++}`,
      workOrderNumber: entered || null,
      received,
      due: due || null,
      dueClass: '',
      status: 'Open',
      internal: entered ? undefined : true,
      preview: linesPreview(savedLines),
      lines: savedLines,
    });
  }

  function handleSave() {
    const number = workOrderNumber.trim();
    if (number && existing.includes(number)) {
      // An entered WO Number that already exists is opened, never
      // duplicated. With entered lines, opening discards them —
      // confirm explicitly. (Does not apply to blank WO Numbers.)
      if (lines.length === 0) onOpenExisting(number);
      else setConfirmExisting(number);
      return;
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
    const missing = collectMissingDemandInfo(number, due, lines);
    if (missing) {
      setConfirmMissing(missing);
      return;
    }
    saveWorkOrder();
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
            The header is optional: leave <b>WO Number</b> blank and the Work
            Order is saved as an{' '}
            <b>internal Work Order without an external number</b> — it displays
            as <span className="mono">—</span> and the real number can be added
            later through an audited edit; leave <b>WO due date</b> blank and
            the Work Order is saved without one — due dates can be added later.
            Add Parts manually with <b>＋ Add Part manually</b>; every line
            defaults to Request Type <TypeChip type="NEW" /> and to the WO due
            date, and both can be changed per line.
          </p>

          <div className="nwo-form">
            <label htmlFor="nwo-num">WO Number (optional)</label>
            <div>
              <input
                id="nwo-num"
                ref={workOrderNumRef}
                className="mono"
                placeholder="e.g. 007482 — optional"
                value={workOrderNumber}
                onChange={(e) => setWorkOrderNumber(e.target.value)}
              />
              <span className="nwo-fieldhint">
                blank = saved without an external number (displays —)
              </span>
            </div>
            <label htmlFor="nwo-recv">Received date</label>
            <div>
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
            <label htmlFor="nwo-due">WO due date (optional)</label>
            <div>
              <input
                id="nwo-due"
                type="date"
                className="mono"
                value={due}
                onChange={(e) => handleDueChange(e.target.value)}
              />
              <span className="nwo-fieldhint">
                default due date for demand lines — blank is valid; it can be
                added later
              </span>
            </div>
          </div>

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
            Manual entry is the normal workflow. Scanning stays available as a
            secondary method: a PN barcode carries the PN itself (
            <code>PF:PN:&lt;part-number&gt;</code>, e.g.{' '}
            <code>PF:PN:78-04-0031</code>); a PN not in the catalog is created
            on first use, non-PN barcodes are rejected, and a PN already on this
            Work Order focuses its existing line instead of adding a duplicate.
          </div>

          <div className="wo-lines nwo-lines">
            <table className="wo-table">
              <thead>
                <tr>
                  <th>PN</th>
                  <th>Request Type</th>
                  <th>Qty</th>
                  <th>Due date</th>
                  <th>Job Numbers</th>
                  <th>Notes</th>
                  <th></th>
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
                      <td className={errorFor(line.id, 'pn') ? 'err-cell' : ''}>
                        {/* Lines always carry a PN here: they come from
                            the Add Part flow or a valid PN barcode. */}
                        <div className="pn" title={line.pn ?? ''}>
                          {line.pn}
                        </div>
                        <div className={`bc ${line.isNewPn ? 'newpn' : ''}`}>
                          {line.barcodeNote}
                        </div>
                        {errorFor(line.id, 'pn') ? (
                          <div className="rowerr">
                            {errorFor(line.id, 'pn')}
                          </div>
                        ) : null}
                      </td>
                      <td>
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
                      <td>
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
                        {line.due === '' ? (
                          <div className="bc">No due date</div>
                        ) : null}
                      </td>
                      <td>
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
                      <td>
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
                      <td>
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

          <div className="wo-actions nwo-actions">
            <button className="btn ghost" onClick={requestClose}>
              Cancel (Esc)
            </button>
            <button
              className="btn primary"
              disabled={writeBlocked}
              onClick={handleSave}
            >
              Save demand
            </button>
            <span className="hint">
              Saving stores <b>business demand only</b> — release to production
              stays a separate explicit step.
            </span>
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

      {confirmMissing ? (
        <ConfirmDialog
          title="Save demand with missing information?"
          confirmLabel="Confirm and save"
          cancelLabel="Cancel — keep editing"
          onConfirm={() => {
            setConfirmMissing(null);
            saveWorkOrder();
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
          title={`${confirmExisting} already exists`}
          confirmLabel="Open existing Work Order"
          cancelLabel="Keep editing"
          onConfirm={() => onOpenExisting(confirmExisting)}
          onCancel={() => setConfirmExisting(null)}
        >
          A WO Number is never duplicated — <b>{confirmExisting}</b> will be
          opened instead. The {lines.length} line
          {lines.length === 1 ? '' : 's'} entered here will be discarded.
        </ConfirmDialog>
      ) : null}
    </>
  );
}
