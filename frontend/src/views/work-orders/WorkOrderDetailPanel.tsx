import { useEffect, useId, useRef, useState } from 'react';

import { ConfirmDialog } from '../../components/ConfirmDialog';
import { TypeChip } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { PageNote } from '../../components/PageNote';
import { EmptyState } from '../../components/view-states';
import { formatIsoDate } from '../dates';
import { normalizePartNumber } from '../scan-station/barcode';
import type { MockWorkOrder, RequestType } from '../view-models';
import { AddPartDialog } from './AddPartDialog';
import type { AddPartResult } from './AddPartDialog';
import {
  RELEASED_REMOVE_EXPLANATION,
  applyWorkOrderDueDateChange,
  collectMissingDemandInfo,
  createDraftLine,
  draftFromSavedLine,
  draftsToSavedLines,
  isPositiveInteger,
  lineRemoveRule,
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

/**
 * Work Order Details as a modal dialog over the Work Order list
 * (GUI_DESIGN §11.2): the list stays mounted and visible behind it and
 * the URL never changes. An OPEN Work Order is editable: demand lines
 * can be added (manual-first ＋ Add Part, scanning secondary), edited,
 * and — while no production quantity has been released for them —
 * removed. Released lines are read-only and can never be removed here
 * (PROJECT_PROFILE §13). All editing is a local draft applied by
 * `Save demand`; every close request (Cancel, Escape, backdrop) on a
 * dirty draft asks for explicit discard confirmation first. The Add
 * Part and confirmation dialogs render as siblings of this dialog so
 * only the topmost dialog handles Escape, backdrop, and focus.
 */
export function WorkOrderDetailPanel({
  workOrder,
  releasedLines,
  writeBlocked,
  onClose,
  onRelease,
  onSaveDetail,
  onDirtyChange,
  showNotice,
}: {
  workOrder: MockWorkOrder | undefined;
  releasedLines: Set<string>;
  writeBlocked: boolean;
  onClose: () => void;
  onRelease: (pn: string) => void;
  onSaveDetail: (workOrder: MockWorkOrder) => void;
  onDirtyChange: (dirty: boolean) => void;
  showNotice: (message: string) => void;
}) {
  // All hooks run unconditionally; the missing-WO branch renders below.
  const headingId = useId();
  const editable = workOrder ? workOrder.status === 'Open' : false;
  const [lines, setLines] = useState<DemandLineDraft[]>(() =>
    workOrder
      ? workOrder.lines.map((line) => draftFromSavedLine(line, workOrder.due))
      : [],
  );
  const [due, setDue] = useState(workOrder?.due ?? '');
  const [dirty, setDirty] = useState(false);
  const [addPartOpen, setAddPartOpen] = useState(false);
  const [lineErrors, setLineErrors] = useState<LineError[]>([]);
  const [confirmRemove, setConfirmRemove] = useState<DemandLineDraft | null>(
    null,
  );
  const [confirmMissing, setConfirmMissing] =
    useState<MissingDemandInfo | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

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
    if (focusField) {
      const el = fieldRefs.current.get(`${focusField.id}:${focusField.field}`);
      el?.focus();
      el?.select();
      setFocusField(null);
    }
  }, [focusField, lines]);

  if (!workOrder) {
    return (
      <ModalDialog labelledBy={headingId} onClose={onClose}>
        <h2 id={headingId} className="nwo-title">
          Work Order Details
        </h2>
        <EmptyState message="This Work Order could not be found." />
        <div className="row">
          <button className="bigbtn ghost" onClick={onClose}>
            Cancel (Esc)
          </button>
        </div>
      </ModalDialog>
    );
  }

  const woDisplay = workOrder.workOrderNumber ?? '—';

  // A release performed in this session also freezes its line.
  const display = lines.map((line) =>
    !line.released && line.pn && releasedLines.has(`${workOrder.id}:${line.pn}`)
      ? { ...line, released: true, statusLabel: 'Released' }
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
    setLines((current) => applyWorkOrderDueDateChange(current, value));
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
    const line = createDraftLine({
      pn: result.pn,
      isNewPn: result.isNewPn,
      barcodeNote: result.isNewPn
        ? `new PN — barcode ${result.barcode}`
        : `existing PN · barcode ${result.barcode}`,
      due,
    });
    setLines((current) => [...current, line]);
    setDirty(true);
    showNotice(
      `✓ ${result.pn} added as an unsaved draft line — Request Type NEW · due date from the WO due date.`,
    );
    setFocusField({ id: line.id, field: 'qty' });
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
      setLines((current) => current.filter((l) => l.id !== line.id));
      setLineErrors((current) => current.filter((e) => e.lineId !== line.id));
      setDirty(true);
      showNotice('✕ Draft line removed — it had never been saved.');
      return;
    }
    setConfirmRemove(line);
  }

  function saveDetail() {
    if (!workOrder) return; // unreachable: save renders only with a WO present
    const savedLines = draftsToSavedLines(display);
    onSaveDetail({
      ...workOrder,
      due: due || null,
      preview: linesPreview(savedLines),
      lines: savedLines,
    });
    setLines(savedLines.map((line) => draftFromSavedLine(line, due || null)));
    setDirty(false);
  }

  function handleSave() {
    if (!workOrder) return; // unreachable: save renders only with a WO present
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
      workOrder.workOrderNumber ?? '',
      due,
      display.filter((line) => !line.released),
    );
    if (missing) {
      setConfirmMissing(missing);
      return;
    }
    saveDetail();
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
          {editable && dirty ? (
            <span className="unsaved">● Unsaved changes</span>
          ) : null}
        </div>
        <div className="big mono">{woDisplay}</div>
        <p className="wo-sub">
          received <b className="mono">{formatIsoDate(workOrder.received)}</b> ·{' '}
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
              WO due date <b className="mono">{formatIsoDate(workOrder.due)}</b>
            </>
          )}{' '}
          · {display.length} demand line{display.length === 1 ? '' : 's'} ·{' '}
          <span className={`wostat ${workOrder.status.toLowerCase()}`}>
            {workOrder.status}
          </span>
          {workOrder.done ? (
            // Done date (`completed_at`, GUI_DESIGN §11.5) — present
            // exactly on completed Work Orders.
            <>
              {' '}
              · Done <b className="mono">{formatIsoDate(workOrder.done)}</b>
            </>
          ) : null}
          {workOrder.internal
            ? ' · internal Work Order — no external number yet (displays —)'
            : ''}
        </p>
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
        <div className="wo-actions nwo-actions">
          <button className="btn ghost" onClick={requestClose}>
            Cancel (Esc)
          </button>
          {editable ? (
            <button
              className="btn primary"
              disabled={writeBlocked}
              onClick={handleSave}
            >
              Save demand
            </button>
          ) : null}
          <span className="hint">
            {editable ? (
              <>
                Saving stores <b>business demand only</b> — release to
                production stays a separate explicit step. Invalid rows cannot
                be saved and are never dropped silently.
              </>
            ) : (
              <>
                This Work Order is <b>{workOrder.status}</b> — demand lines are
                read-only. Editing is available only while a Work Order is Open.
              </>
            )}
          </span>
        </div>
      </ModalDialog>

      {/* Stacked dialogs render as siblings of the details dialog, so
          each one's Escape / backdrop / focus trap stays its own and a
          child close never closes Work Order Details. */}
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
            saveDetail();
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
              `✕ ${confirmRemove.pn} removed from the draft — apply the removal with Save demand.`,
            );
          }}
          onCancel={() => setConfirmRemove(null)}
        >
          No production quantity has been released for this Work Order Demand
          line (<span className="mono">{confirmRemove.pn}</span> · qty{' '}
          {confirmRemove.qty || '—'}). Removing it never deletes the PartNumber
          master, production quantity, movement history, release history, or
          other Work Order Demand for the same PN. The removal is applied by
          Save demand.
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
