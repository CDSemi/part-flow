import { useEffect, useId, useRef, useState } from 'react';

import { ModalDialog } from '../../components/ModalDialog';
import { applyQuantityKey } from '../../components/quantity-input';
import { QuantityKeypad } from '../../components/QuantityKeypad';
import { MOCK_PN_CATALOG } from '../../mocks/work-orders';
import { formatIsoDate } from '../dates';
import type { RequestType } from '../view-models';
import { isPositiveInteger } from './demand-lines';

export interface AddPartResult {
  pn: string;
  isNewPn: boolean;
  barcodeNote: string;
  qty: string;
  /** ISO `YYYY-MM-DD`, or '' when the line has no due date. */
  due: string;
  dueTouched: boolean;
  type: RequestType;
  job: string;
  notes: string;
}

type DueMode = 'inherit' | 'custom' | 'none';

const STEP_TITLES = [
  'Part Number',
  'Quantity',
  'Due date',
  'Optional metadata',
];

/**
 * One accessible multi-step Add Part dialog (no stacked nested modals):
 * ① search/select an existing PN or explicitly create a new one,
 * ② quantity with the same keypad + physical-keyboard interaction as
 * the Scan Station quantity dialog, ③ due date (defaults to the WO due
 * date; `No due date` is an explicit, valid choice), ④ optional
 * metadata. Back never loses entered values. Completing the flow only
 * creates an editable draft row — nothing is saved until `Save demand`.
 */
export function AddPartDialog({
  workOrderDue,
  existingPns,
  onCancel,
  onDuplicate,
  onComplete,
}: {
  /** WO due date (ISO) or '' when the Work Order has no due date. */
  workOrderDue: string;
  existingPns: readonly string[];
  onCancel: () => void;
  /** The PN is already on the Work Order — the owner focuses its line. */
  onDuplicate: (pn: string) => void;
  onComplete: (result: AddPartResult) => void;
}) {
  const headingId = useId();
  const [step, setStep] = useState(0);
  const [query, setQuery] = useState('');
  const [pn, setPn] = useState<string | null>(null);
  const [isNewPn, setIsNewPn] = useState(false);
  const [barcodeNote, setBarcodeNote] = useState('');
  const [qty, setQty] = useState('');
  const [dueMode, setDueMode] = useState<DueMode>('inherit');
  const [customDue, setCustomDue] = useState('');
  const [type, setType] = useState<RequestType>('NEW');
  const [job, setJob] = useState('');
  const [notes, setNotes] = useState('');

  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (step === 0) searchRef.current?.focus();
  }, [step]);

  // Search and identity are case-insensitive; the entered casing is
  // preserved when a new PN is created (never silently re-cased).
  const trimmed = query.trim();
  const upper = trimmed.toUpperCase();
  const matches = MOCK_PN_CATALOG.filter(
    (entry) =>
      !upper ||
      `${entry.pn} ${entry.name} ${entry.barcode}`
        .toUpperCase()
        .includes(upper),
  );
  const exactMatch = MOCK_PN_CATALOG.some(
    (entry) => entry.pn.toUpperCase() === upper,
  );

  function choosePn(value: string, asNewPn: boolean, barcodeNote: string) {
    const duplicate = existingPns.find(
      (existing) => existing.toUpperCase() === value.toUpperCase(),
    );
    if (duplicate) {
      onDuplicate(duplicate);
      return;
    }
    setPn(value);
    setIsNewPn(asNewPn);
    setBarcodeNote(barcodeNote);
    setStep(1);
  }

  const qtyValid = isPositiveInteger(qty);
  const dueValid = dueMode !== 'custom' || customDue !== '';

  function resolvedDue(): { due: string; dueTouched: boolean } {
    if (dueMode === 'none') return { due: '', dueTouched: true };
    if (dueMode === 'custom') return { due: customDue, dueTouched: true };
    // Inherit: the line follows the WO due date — including a WO due
    // date that is only set later (the line stays untouched).
    return { due: workOrderDue, dueTouched: false };
  }

  function finish() {
    if (!pn || !qtyValid || !dueValid) return;
    onComplete({
      pn,
      isNewPn,
      barcodeNote,
      qty,
      ...resolvedDue(),
      type,
      job,
      notes,
    });
  }

  function handleQuantityKeys(event: React.KeyboardEvent) {
    if (step !== 1) return;
    if (event.target instanceof HTMLButtonElement) {
      // Focusable buttons (Back / Cancel) keep native activation;
      // keypad buttons are non-focusable and never receive keys.
      return;
    }
    if (event.key === 'Enter') {
      if (qtyValid) {
        event.preventDefault();
        setStep(2);
      }
      return;
    }
    const next = applyQuantityKey(qty, event.key);
    if (next !== null) {
      event.preventDefault();
      setQty(next);
    }
  }

  return (
    <ModalDialog
      labelledBy={headingId}
      onClose={onCancel}
      size="wide"
      onKeyDown={handleQuantityKeys}
    >
      <div>
        <h3 id={headingId}>
          Add Part — step {step + 1} of 4: {STEP_TITLES[step]}
        </h3>
        {pn ? <div className="big mono">{pn}</div> : null}

        {step === 0 && (
          <>
            <div className="sub">
              Search and select an existing PartNumber, or explicitly create a
              new one. A PN already on this Work Order focuses its existing line
              instead of adding a duplicate.
            </div>
            <input
              ref={searchRef}
              className="field mono"
              placeholder="Search PN or description…"
              aria-label="Search PartNumber"
              autoComplete="off"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <div className="ap-list">
              {matches.map((entry) => (
                <button
                  key={entry.pn}
                  className="ap-item"
                  onClick={() =>
                    choosePn(
                      entry.pn,
                      false,
                      `existing PN · barcode ${entry.barcode}`,
                    )
                  }
                >
                  <span className="mono ap-pn">{entry.pn}</span>
                  <span className="ap-name">{entry.name}</span>
                </button>
              ))}
              {matches.length === 0 ? (
                <div className="ap-empty">
                  No existing PN matches “{query.trim()}”.
                </div>
              ) : null}
            </div>
            {trimmed && !exactMatch ? (
              <button
                className="btn ghost ap-create"
                onClick={() =>
                  choosePn(
                    trimmed,
                    true,
                    `new PN — barcode created with PN master: PF:PN:${trimmed}`,
                  )
                }
              >
                ＋ Create new PN “{trimmed}”
              </button>
            ) : null}
          </>
        )}

        {step === 1 && (
          <>
            <div className="sub">
              Positive whole number. Use the keypad or a physical keyboard —
              digits, Backspace, Delete/Clear, Enter to continue, Escape to
              cancel.
            </div>
            <QuantityKeypad value={qty} onChange={setQty} />
          </>
        )}

        {step === 2 && (
          <>
            <div className="sub">
              A blank due date is valid — the demand simply has no due date yet
              and sorts after all dated demands.
            </div>
            <div className="ap-due" role="radiogroup" aria-label="Due date">
              <label className="ap-duopt">
                <input
                  type="radio"
                  name="ap-due"
                  checked={dueMode === 'inherit'}
                  onChange={() => setDueMode('inherit')}
                />
                <span>
                  {workOrderDue
                    ? `WO due date default — ${formatIsoDate(workOrderDue)}`
                    : 'No date yet — follow the WO due date if one is set later'}
                </span>
              </label>
              <label className="ap-duopt">
                <input
                  type="radio"
                  name="ap-due"
                  checked={dueMode === 'custom'}
                  onChange={() => setDueMode('custom')}
                />
                <span>Specific due date</span>
                <input
                  type="date"
                  className="mono"
                  aria-label="Specific due date"
                  value={customDue}
                  onChange={(e) => {
                    setCustomDue(e.target.value);
                    setDueMode('custom');
                  }}
                />
              </label>
              <label className="ap-duopt">
                <input
                  type="radio"
                  name="ap-due"
                  checked={dueMode === 'none'}
                  onChange={() => setDueMode('none')}
                />
                <span>
                  <b>No due date</b> — leave unspecified (explicit choice; a
                  later WO due-date change will not overwrite it)
                </span>
              </label>
            </div>
          </>
        )}

        {step === 3 && (
          <div className="ap-meta">
            <label htmlFor="ap-type">Request Type</label>
            <select
              id="ap-type"
              value={type}
              onChange={(e) => setType(e.target.value as RequestType)}
            >
              <option>NEW</option>
              <option>MODIFY</option>
            </select>
            <label htmlFor="ap-job">Job Numbers</label>
            <input
              id="ap-job"
              className="mono"
              placeholder="job #… (optional)"
              value={job}
              onChange={(e) => setJob(e.target.value)}
            />
            <label htmlFor="ap-notes">Notes</label>
            <input
              id="ap-notes"
              placeholder="notes… (optional)"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        )}

        <div className="row">
          <button className="bigbtn ghost" onClick={onCancel}>
            Cancel (Esc)
          </button>
          {step > 0 ? (
            <button
              className="bigbtn ghost"
              onClick={() => setStep((s) => s - 1)}
            >
              ‹ Back
            </button>
          ) : null}
          {step === 1 ? (
            <button
              className="bigbtn primary"
              disabled={!qtyValid}
              onClick={() => setStep(2)}
            >
              Next ›
            </button>
          ) : null}
          {step === 2 ? (
            <button
              className="bigbtn primary"
              disabled={!dueValid}
              onClick={() => setStep(3)}
            >
              Next ›
            </button>
          ) : null}
          {step === 3 ? (
            <button className="bigbtn primary" onClick={finish}>
              Add Part
            </button>
          ) : null}
        </div>
      </div>
    </ModalDialog>
  );
}
