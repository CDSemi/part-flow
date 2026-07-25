import { useEffect, useId, useRef, useState } from 'react';

import { ConfirmDialog } from '../../components/ConfirmDialog';
import { TypeChip } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import type { MockPo, RequestType } from '../view-models';
import { todayIso } from './dates';
import {
  applyPoDueDateChange,
  createDraftLine,
  draftsToSavedLines,
  isPositiveInteger,
  linesPreview,
  processScan,
  validateDemandLines,
} from './demand-lines';
import type { DemandLineDraft, LineError, LineField } from './demand-lines';

interface HeaderErrors {
  po?: string;
  received?: string;
  due?: string;
}

/**
 * Scanner-first New PO entry as a modal dialog over the PO list
 * (GUI_DESIGN §11.3): enter the header, scan a PN barcode, type the
 * quantity, Enter returns focus to the scan input. The URL never
 * changes; closing with entered data requires explicit confirmation.
 * Phase 2: saving changes local mock state only.
 */
export function NewPoDialog({
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
  onOpenExisting: (po: string) => void;
  onSave: (po: MockPo) => void;
  onDirtyChange: (dirty: boolean) => void;
  showNotice: (message: string) => void;
}) {
  const headingId = useId();
  const [poNumber, setPoNumber] = useState('');
  const initialReceived = useRef(todayIso());
  const [received, setReceived] = useState(initialReceived.current);
  const [due, setDue] = useState('');
  const [lines, setLines] = useState<DemandLineDraft[]>([]);
  const [headerErrors, setHeaderErrors] = useState<HeaderErrors>({});
  const [lineErrors, setLineErrors] = useState<LineError[]>([]);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [confirmExisting, setConfirmExisting] = useState<string | null>(null);

  const poNumRef = useRef<HTMLInputElement>(null);
  const receivedRef = useRef<HTMLInputElement>(null);
  const dueRef = useRef<HTMLInputElement>(null);
  const scanRef = useRef<HTMLInputElement>(null);
  const fieldRefs = useRef(new Map<string, HTMLInputElement>());
  const [focusField, setFocusField] = useState<{
    id: number;
    field: LineField;
  } | null>(null);

  const dirty =
    poNumber.trim() !== '' ||
    due !== '' ||
    received !== initialReceived.current ||
    lines.length > 0;

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  // Initial focus: the first header field, per the scanner-first flow.
  useEffect(() => {
    poNumRef.current?.focus();
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
        `⚠ ${result.pn} is already on this PO — edit its quantity instead of adding a duplicate line.`,
      );
      setFocusField({ id: result.lineId, field: 'qty' });
      return;
    }
    const line = createDraftLine({
      pn: result.pn,
      barcodeNote: `existing PN · barcode ${result.barcode}`,
      due,
    });
    setLines((current) => [...current, line]);
    showNotice(
      `✓ ${result.pn} added — Request Type NEW · due date from PO due date. Type the quantity.`,
    );
    setFocusField({ id: line.id, field: 'qty' });
  }

  function addManualLine() {
    const line = createDraftLine({ due });
    setLines((current) => [...current, line]);
    setFocusField({ id: line.id, field: 'pn' });
  }

  function handleDueChange(value: string) {
    setDue(value);
    setHeaderErrors((current) => ({ ...current, due: undefined }));
    // The PO due date is the default — update lines still holding it.
    setLines((current) => applyPoDueDateChange(current, value));
  }

  function focusFirstInvalid(headerErr: HeaderErrors, lineErrs: LineError[]) {
    if (headerErr.po) return poNumRef.current?.focus();
    if (headerErr.received) return receivedRef.current?.focus();
    if (headerErr.due) return dueRef.current?.focus();
    const first = lineErrs[0];
    if (!first) return;
    const el =
      fieldRefs.current.get(`${first.lineId}:${first.field}`) ??
      fieldRefs.current.get(`${first.lineId}:qty`);
    el?.focus();
  }

  function handleSave() {
    const number = poNumber.trim().toUpperCase();
    if (number && existing.includes(number)) {
      // An existing PO Number is opened, never duplicated (§11.3). With
      // entered lines, opening discards them — confirm explicitly.
      if (lines.length === 0) onOpenExisting(number);
      else setConfirmExisting(number);
      return;
    }
    const headerErr: HeaderErrors = {};
    if (!number) headerErr.po = 'PO Number is required';
    if (!received) headerErr.received = 'received date is required';
    if (!due)
      headerErr.due =
        'PO due date is required — it is the default for every demand line';
    const lineErrs = validateDemandLines(lines);
    setHeaderErrors(headerErr);
    setLineErrors(lineErrs);
    if (
      headerErr.po ||
      headerErr.received ||
      headerErr.due ||
      lineErrs.length
    ) {
      showNotice(
        '✕ The form has invalid fields — fix them to save. Entered values are preserved; incomplete rows are never dropped silently.',
      );
      focusFirstInvalid(headerErr, lineErrs);
      return;
    }
    if (lines.length === 0) {
      showNotice('✕ Add at least one demand line (scan a PN barcode)');
      scanRef.current?.focus();
      return;
    }
    const savedLines = draftsToSavedLines(lines);
    onSave({
      po: number,
      received,
      due,
      dueClass: '',
      status: 'Open',
      preview: linesPreview(savedLines),
      lines: savedLines,
    });
  }

  function requestClose() {
    if (dirty) setConfirmDiscard(true);
    else onClose();
  }

  return (
    <>
      <ModalDialog labelledBy={headingId} onClose={requestClose} size="xwide">
        <div className="npd">
          <h2 id={headingId} className="npd-title">
            New PO
          </h2>
          <p className="po-sub">
            Enter the PO header, then <b>scan each part's PN barcode</b> and
            type its quantity — or add lines manually for a PN that does not
            exist yet. Every line defaults to Request Type{' '}
            <TypeChip type="NEW" /> and to the <b>PO due date</b>; both can be
            changed per line. An existing PO Number is opened instead of
            duplicated. The PO list stays behind this dialog; nothing here is
            persisted to the backend in Phase 2.
          </p>

          <div className="np-form">
            <label htmlFor="np-num">PO Number</label>
            <div>
              <input
                id="np-num"
                ref={poNumRef}
                className="mono"
                placeholder="PO-____"
                value={poNumber}
                aria-invalid={headerErrors.po ? true : undefined}
                onChange={(e) => {
                  setPoNumber(e.target.value);
                  setHeaderErrors((c) => ({ ...c, po: undefined }));
                }}
              />
              {headerErrors.po ? (
                <div className="rowerr">{headerErrors.po}</div>
              ) : null}
            </div>
            <label htmlFor="np-recv">Received date</label>
            <div>
              <input
                id="np-recv"
                ref={receivedRef}
                type="date"
                className="mono"
                value={received}
                aria-invalid={headerErrors.received ? true : undefined}
                onChange={(e) => {
                  setReceived(e.target.value);
                  setHeaderErrors((c) => ({ ...c, received: undefined }));
                }}
              />
              {headerErrors.received ? (
                <div className="rowerr">{headerErrors.received}</div>
              ) : null}
            </div>
            <label htmlFor="np-due">PO due date</label>
            <div>
              <input
                id="np-due"
                ref={dueRef}
                type="date"
                className="mono"
                value={due}
                aria-invalid={headerErrors.due ? true : undefined}
                onChange={(e) => handleDueChange(e.target.value)}
              />
              <span className="np-fieldhint">
                default due date for every demand line
              </span>
              {headerErrors.due ? (
                <div className="rowerr">{headerErrors.due}</div>
              ) : null}
            </div>
          </div>

          <div className="np-scanrow">
            <input
              ref={scanRef}
              className="np-scan"
              placeholder="Scan PN barcode (PF:PN:…) — Enter"
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
              className="btn ghost"
              disabled={writeBlocked}
              onClick={addManualLine}
            >
              ＋ Add line manually
            </button>
          </div>
          <div className="np-hint">
            Scan → the line is added and its <b>Qty</b> field gets focus → type
            the quantity → Enter returns focus to the scan input, ready for the
            next part. Demo barcodes: <code>PF:PN:1014</code> ·{' '}
            <code>PF:PN:1021</code> · <code>PF:PN:1102</code>. Scanning a PN
            already on this PO focuses its existing line instead of adding a
            duplicate. Unknown barcodes are rejected — nothing is added.
          </div>

          <div className="po-lines npd-lines">
            <table className="po-table">
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
                    <td colSpan={7} className="np-empty">
                      No demand lines yet — scan the first PN barcode above
                    </td>
                  </tr>
                ) : (
                  lines.map((line) => (
                    <tr key={line.id}>
                      <td className={errorFor(line.id, 'pn') ? 'err-cell' : ''}>
                        {line.pn ? (
                          <div className="pn" title={line.pn}>
                            {line.pn}
                          </div>
                        ) : (
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
                              const pn = e.target.value.trim().toUpperCase();
                              if (!pn) return;
                              const duplicate = lines.find(
                                (l) => l.id !== line.id && l.pn === pn,
                              );
                              if (duplicate) {
                                showNotice(
                                  `⚠ ${pn} is already on this PO — edit the existing line instead of adding a duplicate.`,
                                );
                                e.target.value = '';
                                setFocusField({
                                  id: duplicate.id,
                                  field: 'qty',
                                });
                                return;
                              }
                              clearLineError(line.id, 'pn');
                              updateLine(line.id, {
                                pn,
                                barcodeNote:
                                  'new PN — barcode created with PN master: PF:PN:…',
                                isNewPn: true,
                              });
                              setFocusField({ id: line.id, field: 'qty' });
                            }}
                          />
                        )}
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
                          <option>REWORK</option>
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
                      <td
                        className={errorFor(line.id, 'due') ? 'err-cell' : ''}
                      >
                        <input
                          ref={(el) => {
                            if (el) fieldRefs.current.set(`${line.id}:due`, el);
                            else fieldRefs.current.delete(`${line.id}:due`);
                          }}
                          type="date"
                          className="mono"
                          value={line.due}
                          aria-label={`Due date for ${line.pn ?? 'new line'}`}
                          aria-invalid={
                            errorFor(line.id, 'due') ? true : undefined
                          }
                          onChange={(e) => {
                            clearLineError(line.id, 'due');
                            updateLine(line.id, {
                              due: e.target.value,
                              dueTouched: true,
                            });
                          }}
                        />
                        {errorFor(line.id, 'due') ? (
                          <div className="rowerr">
                            {errorFor(line.id, 'due')}
                          </div>
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

          <div className="po-actions npd-actions">
            <button className="btn ghost" onClick={requestClose}>
              Cancel
            </button>
            <button
              className="btn primary"
              disabled={writeBlocked}
              onClick={handleSave}
            >
              Save demand
            </button>
            <span className="hint">
              Saving stores <b>business demand only</b> — no Quantity Flow, no
              Movement, no release. (Development mock — nothing is persisted to
              the backend.)
            </span>
          </div>
        </div>
      </ModalDialog>

      {confirmDiscard ? (
        <ConfirmDialog
          title="Discard this New PO?"
          confirmLabel="Discard entries"
          cancelLabel="Keep editing"
          danger
          onConfirm={onClose}
          onCancel={() => setConfirmDiscard(false)}
        >
          The entered PO header
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
          confirmLabel="Open existing PO"
          cancelLabel="Keep editing"
          onConfirm={() => onOpenExisting(confirmExisting)}
          onCancel={() => setConfirmExisting(null)}
        >
          A PO Number is never duplicated — <b>{confirmExisting}</b> will be
          opened instead. The {lines.length} line
          {lines.length === 1 ? '' : 's'} entered here will be discarded.
        </ConfirmDialog>
      ) : null}
    </>
  );
}
