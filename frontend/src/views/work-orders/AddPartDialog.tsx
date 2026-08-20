import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { searchPartNumbers } from '../../api/part-numbers';
import { useApiData } from '../../api/use-api-data';
import { ModalDialog } from '../../components/ModalDialog';
import { applyQuantityKey } from '../../components/quantity-input';
import { QuantityKeypad } from '../../components/QuantityKeypad';
import { normalizePartNumber } from '../scan-station/barcode';
import { formatIsoDate } from '../dates';
import type { RequestType } from '../view-models';
import { isPositiveInteger } from './demand-lines';
import { PnBarcodeLabelDialog } from './PnBarcodeLabelDialog';

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
  const [labelOpen, setLabelOpen] = useState(false);

  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (step === 0) searchRef.current?.focus();
  }, [step]);

  // Search runs against the real PartNumber masters (contains-match,
  // case-insensitive on the server). Creating a new PN uses the
  // canonical form (PROJECT_PROFILE §7): surrounding whitespace is
  // trimmed, the entry is canonicalized to uppercase, and a value with
  // internal whitespace is not a valid PN — internal whitespace is
  // never silently removed. The master record itself is created by the
  // Save demand transaction (create-on-first-valid-use).
  const trimmed = query.trim();
  const searchLoader = useCallback(() => searchPartNumbers(query), [query]);
  const searchData = useApiData(searchLoader);
  const matches =
    searchData.state.status === 'ready' ? searchData.state.data : [];
  const canonical = normalizePartNumber(query);
  const exactMatch =
    canonical !== null &&
    matches.some((entry) => entry.partNumber === canonical);

  function choosePn(value: string, asNewPn: boolean, barcodeNote: string) {
    // `value` is a canonical PN; existing line PNs are canonical too.
    const duplicate = existingPns.find((existing) => existing === value);
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

  // Header context for the selected PN: the derived barcode for an
  // existing PN master, a `new Part Number` marker otherwise; the
  // final step adds the entered quantity and due-date choice. (PN
  // master metadata such as a description arrives with Phase 13.)

  function dueSummary(): string {
    if (dueMode === 'none') return 'no due date';
    if (dueMode === 'custom') return `due ${formatIsoDate(customDue)}`;
    return workOrderDue
      ? `due ${formatIsoDate(workOrderDue)} (WO due date)`
      : 'follows the WO due date';
  }

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
    if (
      event.target instanceof HTMLInputElement &&
      event.target.classList.contains('qtydisplay')
    ) {
      // The focused quantity input owns cursor-aware editing (shared
      // QuantityKeypad transitions) and consumes those keys itself.
      return;
    }
    const next = applyQuantityKey(qty, event.key);
    if (next !== null) {
      event.preventDefault();
      setQty(next);
    }
  }

  const dialog = (
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
        {pn ? (
          <div className="ap-pnhead">
            <div className="big mono">{pn}</div>
            <div className="ap-pninfo">
              {isNewPn ? (
                <span className="ap-pnnew">new Part Number</span>
              ) : (
                <span className="mono">{pn ? `PF:PN:${pn}` : ''}</span>
              )}
              {step === 3 ? (
                <span>
                  {' '}
                  · Qty <b className="mono">{qty}</b> · {dueSummary()}
                </span>
              ) : null}{' '}
              {/* The barcode derives from the PN identity itself, so
                  the printable label exists for an existing master and
                  a new canonical PN alike (Phase 4 §10 capability). */}
              <button
                className="pn-labellink"
                onClick={() => setLabelOpen(true)}
              >
                Barcode label…
              </button>
            </div>
          </div>
        ) : null}

        {step === 0 && (
          <>
            <div className="sub">
              Search for an existing Part Number or enter a new one. If it’s
              already on this Work Order, the existing line will be opened
              instead.
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
                  key={entry.partNumber}
                  className="ap-item"
                  onClick={() =>
                    choosePn(
                      entry.partNumber,
                      false,
                      `existing PN · barcode ${entry.barcodeValue}`,
                    )
                  }
                >
                  <span className="mono ap-pn">{entry.partNumber}</span>
                  <span className="ap-name mono">{entry.barcodeValue}</span>
                </button>
              ))}
              {searchData.state.status === 'error' ? (
                <div className="ap-empty" role="alert">
                  {searchData.state.message}
                </div>
              ) : searchData.state.status === 'loading' ? (
                <div className="ap-empty">Searching Part Numbers…</div>
              ) : matches.length === 0 ? (
                <div className="ap-empty">
                  No existing PN matches “{query.trim()}”.
                </div>
              ) : null}
            </div>
            {canonical && !exactMatch ? (
              <button
                className="btn ghost ap-create"
                onClick={() =>
                  choosePn(
                    canonical,
                    true,
                    `new PN — barcode PF:PN:${canonical}`,
                  )
                }
              >
                ＋ Create new PN “{canonical}”
              </button>
            ) : null}
            {trimmed && !canonical ? (
              <div className="ap-empty">
                A Part Number cannot contain spaces or other whitespace inside
                the value, so “{query.trim()}” cannot be created as a new PN.
              </div>
            ) : null}
          </>
        )}

        {step === 1 && (
          <>
            <div className="sub">
              Enter a positive whole-number quantity. Use the keypad or
              keyboard.
            </div>
            <QuantityKeypad value={qty} onChange={setQty} />
          </>
        )}

        {step === 2 && (
          <>
            <div className="sub">
              Choose the Work Order due date, a specific date, or no due date.
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

  return (
    <>
      {dialog}
      {/* Stacked sibling: only the topmost dialog handles Escape,
          backdrop, and focus — closing the label returns here. */}
      {labelOpen && pn ? (
        <PnBarcodeLabelDialog pn={pn} onClose={() => setLabelOpen(false)} />
      ) : null}
    </>
  );
}
