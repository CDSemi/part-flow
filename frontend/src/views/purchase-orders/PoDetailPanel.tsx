import { useEffect, useRef, useState } from 'react';

import { ConfirmDialog } from '../../components/ConfirmDialog';
import { TypeChip } from '../../components/indicators';
import { EmptyState } from '../../components/view-states';
import type { MockPo, RequestType } from '../view-models';
import { formatIsoDate } from './dates';
import {
  RELEASED_REMOVE_EXPLANATION,
  applyPoDueDateChange,
  createDraftLine,
  draftFromSavedLine,
  draftsToSavedLines,
  isPositiveInteger,
  lineRemoveRule,
  linesPreview,
  processScan,
  validateDemandLines,
} from './demand-lines';
import type { DemandLineDraft, LineError, LineField } from './demand-lines';

/**
 * Detail of one PO (GUI_DESIGN §11.2). An OPEN PO is editable: demand
 * lines can be added (scanner-first ＋ Add Part), edited, and — while no
 * production quantity has been released for them — removed. Released
 * lines are read-only and can never be removed here (PROJECT_PROFILE
 * §13). All editing is a local draft: Save demand applies it to local
 * mock state only.
 */
export function PoDetailPanel({
  po,
  releasedLines,
  writeBlocked,
  onBack,
  onRelease,
  onSaveDetail,
  onDirtyChange,
  showNotice,
}: {
  po: MockPo | undefined;
  releasedLines: Set<string>;
  writeBlocked: boolean;
  onBack: () => void;
  onRelease: (pn: string) => void;
  onSaveDetail: (po: MockPo) => void;
  onDirtyChange: (dirty: boolean) => void;
  showNotice: (message: string) => void;
}) {
  // All hooks run unconditionally; the missing-PO branch renders below.
  const editable = po ? po.status === 'Open' : false;
  const [lines, setLines] = useState<DemandLineDraft[]>(() =>
    po ? po.lines.map((line) => draftFromSavedLine(line, po.due)) : [],
  );
  const [due, setDue] = useState(po?.due ?? '');
  const [dirty, setDirty] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [lineErrors, setLineErrors] = useState<LineError[]>([]);
  const [confirmRemove, setConfirmRemove] = useState<DemandLineDraft | null>(
    null,
  );

  const scanRef = useRef<HTMLInputElement>(null);
  const fieldRefs = useRef(new Map<string, HTMLInputElement>());
  const [focusField, setFocusField] = useState<{
    id: number;
    field: LineField;
  } | null>(null);

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    if (addOpen) scanRef.current?.focus();
  }, [addOpen]);

  useEffect(() => {
    if (focusField) {
      const el = fieldRefs.current.get(`${focusField.id}:${focusField.field}`);
      el?.focus();
      el?.select();
      setFocusField(null);
    }
  }, [focusField, lines]);

  if (!po) {
    return (
      <div>
        <button className="po-back" onClick={onBack}>
          ‹ All POs
        </button>
        <EmptyState message="This PO is not available in the mock data." />
      </div>
    );
  }

  // A release performed in this session (mock) also freezes its line.
  const display = lines.map((line) =>
    !line.released && line.pn && releasedLines.has(`${po.po}:${line.pn}`)
      ? { ...line, released: true, statusLabel: 'Released (mock)' }
      : line,
  );

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
    setLines((current) => applyPoDueDateChange(current, value));
    setDirty(true);
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
          `⚠ ${result.pn} is already on this PO and its production quantity is released — the line is read-only here.`,
        );
        scanRef.current?.focus();
        return;
      }
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
    setDirty(true);
    showNotice(
      `✓ ${result.pn} added as an unsaved draft line — Request Type NEW · due date from PO due date.`,
    );
    setFocusField({ id: line.id, field: 'qty' });
  }

  function addManualLine() {
    const line = createDraftLine({ due });
    setLines((current) => [...current, line]);
    setDirty(true);
    setFocusField({ id: line.id, field: 'pn' });
  }

  function requestRemove(line: DemandLineDraft) {
    const rule = lineRemoveRule(line);
    if (rule === 'blocked') return;
    if (rule === 'draft') {
      setLines((current) => current.filter((l) => l.id !== line.id));
      setLineErrors((current) => current.filter((e) => e.lineId !== line.id));
      setDirty(true);
      showNotice('✕ Draft line removed — it had never been saved.');
      return;
    }
    setConfirmRemove(line);
  }

  function handleSave() {
    if (!po) return; // unreachable: save renders only with a PO present
    const errors = validateDemandLines(display);
    setLineErrors(errors);
    if (errors.length) {
      showNotice(
        '✕ The PO has invalid demand lines — fix them to save. Entered values are preserved; incomplete rows are never dropped silently.',
      );
      const first = errors[0];
      const el =
        fieldRefs.current.get(`${first.lineId}:${first.field}`) ??
        fieldRefs.current.get(`${first.lineId}:qty`);
      el?.focus();
      return;
    }
    const savedLines = draftsToSavedLines(display);
    onSaveDetail({
      ...po,
      due,
      preview: linesPreview(savedLines),
      lines: savedLines,
    });
    setLines(savedLines.map((line) => draftFromSavedLine(line, due)));
    setDirty(false);
    setAddOpen(false);
  }

  function handleBack() {
    if (
      dirty &&
      !window.confirm(
        'This PO has unsaved changes. Discard them and return to the PO list?',
      )
    ) {
      return;
    }
    onBack();
  }

  return (
    <div>
      <div className="po-head">
        <button className="po-back" onClick={handleBack}>
          ‹ All POs
        </button>
        <h1 className="mono">{po.po}</h1>
        <span className="spacer" />
        {editable && dirty ? (
          <span className="unsaved">● Unsaved changes</span>
        ) : null}
      </div>
      <p className="po-sub">
        received <b className="mono">{formatIsoDate(po.received)}</b> ·{' '}
        {editable ? (
          <>
            PO due date{' '}
            <input
              type="date"
              className="mono po-due-input"
              value={due}
              aria-label="PO due date"
              onChange={(e) => handleDueChange(e.target.value)}
            />
          </>
        ) : (
          <>
            PO due date <b className="mono">{formatIsoDate(po.due)}</b>
          </>
        )}{' '}
        · {display.length} demand line{display.length === 1 ? '' : 's'} ·{' '}
        <span className={`postat ${po.status.toLowerCase()}`}>{po.status}</span>
        {po.internal ? ' · temporary internal PO (auditable, unique)' : ''}
      </p>
      <div className="po-card">
        {editable && (
          <div className="pc-head">
            <span className="meta">
              Demand lines — each line's due date defaults to the{' '}
              <b>PO due date</b> and may be edited per line. Editing is a local
              draft (development mock) until <b>Save demand</b>.
            </span>
            <span className="spacer" />
            {dirty ? <span className="unsaved">● Unsaved changes</span> : null}
          </div>
        )}
        <div className="po-lines">
          <table className="po-table">
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
                    <td className={errorFor(line.id, 'pn') ? 'err-cell' : ''}>
                      {line.pn ? (
                        <div className="pn" title={line.pn}>
                          {line.pn}
                        </div>
                      ) : rowEditable ? (
                        <input
                          ref={(el) => {
                            if (el) fieldRefs.current.set(`${line.id}:pn`, el);
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
                            const duplicate = display.find(
                              (l) => l.id !== line.id && l.pn === pn,
                            );
                            if (duplicate) {
                              showNotice(
                                `⚠ ${pn} is already on this PO — edit the existing line instead of adding a duplicate.`,
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
                              barcodeNote:
                                'new PN — barcode created with PN master: PF:PN:…',
                              isNewPn: true,
                            });
                            setFocusField({ id: line.id, field: 'qty' });
                          }}
                        />
                      ) : (
                        <div className="pn" style={{ color: 'var(--faint)' }}>
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
                        <div className="rowerr">{errorFor(line.id, 'pn')}</div>
                      ) : null}
                    </td>
                    <td>
                      {rowEditable && !line.saved ? (
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
                      ) : (
                        <TypeChip type={line.type} />
                      )}
                    </td>
                    <td className={errorFor(line.id, 'qty') ? 'err-cell' : ''}>
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
                              if (addOpen) scanRef.current?.focus();
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
                    <td className={errorFor(line.id, 'due') ? 'err-cell' : ''}>
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
                        </>
                      ) : (
                        <span className="mono">{formatIsoDate(line.due)}</span>
                      )}
                    </td>
                    <td>
                      {rowEditable ? (
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
                      ) : (
                        <span className="mono">{line.job || '—'}</span>
                      )}
                    </td>
                    <td>
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
                      <td>
                        <div className="po-rowactions">
                          {line.saved || line.released ? (
                            <button
                              className="rel-btn"
                              disabled={
                                writeBlocked ||
                                !line.releasable ||
                                line.released
                              }
                              onClick={() => line.pn && onRelease(line.pn)}
                            >
                              Release to production…
                            </button>
                          ) : null}
                          <button
                            className="pr-x"
                            disabled={removeRule === 'blocked'}
                            title={
                              removeRule === 'blocked'
                                ? RELEASED_REMOVE_EXPLANATION
                                : removeRule === 'confirm'
                                  ? 'Remove line (asks for confirmation)'
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
          <div className="po-addpart">
            {addOpen ? (
              <>
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
                  <button
                    className="btn ghost"
                    onClick={() => setAddOpen(false)}
                  >
                    Done
                  </button>
                </div>
                <div className="np-hint">
                  Scan an existing PN barcode, or add a line manually to look up
                  / create a PN. A new line joins this PO as an{' '}
                  <b>unsaved draft</b> with Request Type NEW and the PO due
                  date. A PN already on this PO focuses its existing line
                  instead of adding a duplicate.
                </div>
              </>
            ) : (
              <button
                className="btn ghost"
                disabled={writeBlocked}
                onClick={() => setAddOpen(true)}
              >
                ＋ Add Part
              </button>
            )}
          </div>
        )}
        {editable && (
          <div className="po-actions">
            <button
              className="btn primary"
              disabled={writeBlocked}
              onClick={handleSave}
            >
              Save demand
            </button>
            <span className="hint">
              Saving stores <b>business demand only</b> — no Quantity Flow, no
              Movement, no release. Invalid rows cannot be saved. (Development
              mock — local state only; nothing is persisted to the backend.)
            </span>
          </div>
        )}
      </div>
      <div className="po-note">
        A demand line can be removed only while no production quantity has been
        released for it: an unsaved draft is removed immediately, a saved
        unreleased line asks for confirmation, and a released line can no longer
        be removed here — corrections go through the correction and production
        workflows (PROJECT_PROFILE §13). Removal never deletes the PartNumber
        master, Quantity Flows, Movements, release history, or other PO Demand
        for the same PN. An <b>inactive PN</b> is flagged in lookup and cannot
        be released without reactivation. Leaving this PO with unsaved changes
        asks for confirmation.
      </div>

      {confirmRemove ? (
        <ConfirmDialog
          title={`Remove ${confirmRemove.pn ?? 'line'} from ${po.po}?`}
          confirmLabel="Remove line"
          cancelLabel="Cancel — keep the line"
          danger
          onConfirm={() => {
            setLines((current) =>
              current.filter((l) => l.id !== confirmRemove.id),
            );
            setLineErrors((current) =>
              current.filter((e) => e.lineId !== confirmRemove.id),
            );
            setDirty(true);
            setConfirmRemove(null);
            showNotice(
              `✕ ${confirmRemove.pn} removed from the draft — Save demand applies it to local mock state only.`,
            );
          }}
          onCancel={() => setConfirmRemove(null)}
        >
          No production quantity has been released for this PO Demand line (
          <span className="mono">{confirmRemove.pn}</span> · qty{' '}
          {confirmRemove.qty || '—'}). Removing it never deletes the PartNumber
          master, Quantity Flows, Movements, release history, or other PO Demand
          for the same PN. Phase 2: the removal affects{' '}
          <b>local mock state only</b> and is applied by Save demand.
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
