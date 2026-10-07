import './AllocationAdjustmentDialog.css';

import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { ApiError, errorMessage, refusalFlag } from '../api/client';
import {
  allocateBeyondDemand,
  allocateFromStock,
  getAllocationContext,
  reverseAllocation,
} from '../api/management-allocations';
import type {
  AllocationContext,
  AllocationScope,
  AllocationUserRef,
  ContextAllocation,
  ContextLine,
  ManagementAllocationResult,
} from '../api/management-allocations';
import { newDeviceEventId } from '../api/production-release';
import { useApiData } from '../api/use-api-data';
import { userAvatarUrl } from '../api/users';
import { formatIsoDate } from '../views/dates';
import {
  BEYOND_DEMAND_WARNING,
  FULLY_ALLOCATED_NOTE,
  MAY_BE_RECORDED,
  NO_OPEN_DEMAND,
  OUTCOME_UNKNOWN,
  SIGN_IN_AGAIN,
  afterCorrectionLine,
  allocateQtyError,
  allocationTimestamp,
  beyondQtyError,
  beyondRange,
  committedNotice,
  parseQuantity,
  reasonError,
  reversalReopens,
  routineLimit,
  sourceLabel,
  workOrderLabel,
} from './allocation-adjustment';
import { Avatar } from './Avatar';
import { ModalDialog } from './ModalDialog';
import { ErrorState, LoadingState } from './view-states';

/** Where the dialog opens. */
export type AllocationAdjustmentStart =
  | { step: 'overview' }
  | { step: 'allocate'; workOrderDemandId: number }
  | { step: 'reverse'; workOrderDemandId: number };

type Step =
  | { kind: 'overview'; filterDemandId: number | null }
  | { kind: 'allocate'; demandId: number }
  | { kind: 'beyond'; demandId: number }
  | { kind: 'reverse'; demandId: number; allocationId: number };

const TITLES: Record<Step['kind'], string> = {
  overview: 'Adjust WO Allocation',
  allocate: 'Allocate from stock',
  beyond: 'Allocate beyond demand',
  reverse: 'Reverse allocation',
};

function stepKey(step: Step): string {
  return step.kind === 'reverse'
    ? `reverse:${step.allocationId}`
    : step.kind === 'overview'
      ? `overview:${step.filterDemandId ?? ''}`
      : `${step.kind}:${step.demandId}`;
}

/** A `reverse` start opens the Reverse step directly when the line has
 * exactly one active allocation; otherwise the Overview of that line. */
function resolveReverseStart(
  context: AllocationContext,
  demandId: number,
): Step {
  const line = context.lines.find((l) => l.workOrderDemandId === demandId);
  if (line && line.activeAllocations.length === 1) {
    return {
      kind: 'reverse',
      demandId,
      allocationId: line.activeAllocations[0].allocationId,
    };
  }
  return { kind: 'overview', filterDemandId: demandId };
}

/** The signed-in User who recorded a row: avatar and name (CD4). */
function Actor({ actor }: { actor: AllocationUserRef }) {
  return (
    <span className="aad-actor">
      <Avatar name={actor.displayName} size="sm" src={userAvatarUrl(actor)} />
      {actor.displayName}
    </span>
  );
}

/**
 * The shared `Adjust WO Allocation` dialog (GUI_DESIGN §11.6; Phase 14
 * slice 5): an Overview of the demand lines in scope with their active
 * allocations, `Allocate from stock` (never beyond the line's remaining
 * demand), the explicit beyond-demand correction step (warning and
 * mandatory reason) and `Reverse allocation` (mandatory reason).
 *
 * ONE intent keeps ONE `device_event_id` through every resubmit; a
 * changed step, target, quantity, note or reason is a new intent with a
 * new key. The inputs and the step navigation lock while a submission
 * is in flight (an edit then would drop the key the request carries).
 * When the server did not answer, the outcome is unknown: they stay
 * locked, so the next submit can only repeat the same intent (PartFlow
 * records it once); an explicit refusal of that resubmit proves nothing
 * was recorded and unlocks. Closing while the outcome is unknown (or
 * a request is still running) tells the host (`onClose(true)`), which
 * then reloads and says so; that request's late answer never reaches
 * the host. Presentation only:
 * the server judges every write again under its locks.
 */
export function AllocationAdjustmentDialog({
  scope,
  start,
  writeBlocked,
  onClose,
  onCommitted,
}: {
  scope: AllocationScope;
  start: AllocationAdjustmentStart;
  writeBlocked: boolean;
  /** A close request; `outcomeUnknown` when a submission may or may not
   * have been recorded. */
  onClose: (outcomeUnknown: boolean) => void;
  /** The server confirmed a write; the host closes the dialog. */
  onCommitted: (result: ManagementAllocationResult, notice: string) => void;
}) {
  const headingId = useId();
  const scopePn = 'partNumber' in scope ? scope.partNumber : null;
  const scopeDemandId =
    'workOrderDemandId' in scope ? scope.workOrderDemandId : null;
  const load = useCallback(
    () =>
      getAllocationContext(
        scopePn !== null
          ? { partNumber: scopePn }
          : { workOrderDemandId: scopeDemandId ?? 0 },
      ),
    [scopePn, scopeDemandId],
  );
  const contextData = useApiData(load);
  const context =
    contextData.state.status === 'ready' ? contextData.state.data : null;

  // A `reverse` start is resolved once the context is known (null until).
  const [step, setStep] = useState<Step | null>(() =>
    start.step === 'overview'
      ? { kind: 'overview', filterDemandId: null }
      : start.step === 'allocate'
        ? { kind: 'allocate', demandId: start.workOrderDemandId }
        : null,
  );
  if (step === null && context !== null && start.step === 'reverse') {
    setStep(resolveReverseStart(context, start.workOrderDemandId));
  }

  // null = the step's default quantity (fixed at the first submit).
  const [qty, setQty] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [reason, setReason] = useState('');
  const [showErrors, setShowErrors] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [outcomeUnknown, setOutcomeUnknown] = useState(false);
  // The intent's idempotency key, created lazily on the first submit and
  // reused on every resubmit of the SAME intent.
  const deviceEventId = useRef<string | null>(null);
  const submitted = useRef(false);

  const fieldRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const [returnFocusKey, setReturnFocusKey] = useState<string | null>(null);
  const [errorFocus, setErrorFocus] = useState(0);
  // False once this dialog instance unmounted: a late answer must never
  // act on (close) a dialog the host opened since.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // An in-flight request carries the intent's key: no edit until it ends.
  const locked = outcomeUnknown || busy;
  const available = context?.availableStockedQuantity ?? 0;

  // The line / allocation the step works on; a step whose target left
  // the context (a reload after a refusal) falls back to the Overview.
  const line =
    context !== null && step !== null && step.kind !== 'overview'
      ? (context.lines.find((l) => l.workOrderDemandId === step.demandId) ??
        null)
      : null;
  const allocation =
    line !== null && step?.kind === 'reverse'
      ? (line.activeAllocations.find(
          (a) => a.allocationId === step.allocationId,
        ) ?? null)
      : null;
  const shown: Step | null =
    step === null
      ? null
      : step.kind === 'overview' ||
          line === null ||
          (step.kind === 'reverse' && allocation === null)
        ? {
            kind: 'overview',
            filterDemandId:
              step.kind === 'overview' ? step.filterDemandId : null,
          }
        : step;
  const shownKey = shown === null ? null : stepKey(shown);
  const contextReady = context !== null;

  const range = line !== null ? beyondRange(line, available) : null;
  const limit = line !== null ? routineLimit(line, available) : 0;
  const qtyText =
    qty ??
    (shown?.kind === 'allocate'
      ? limit > 0
        ? String(limit)
        : ''
      : shown?.kind === 'beyond' && range !== null
        ? String(range.min)
        : '');

  // Focus: each step's first field; the Overview its opener (Back) or
  // its first action.
  useEffect(() => {
    if (!contextReady || shownKey === null) return;
    if (shownKey.startsWith('overview')) {
      const body = bodyRef.current;
      const opener =
        returnFocusKey !== null
          ? body?.querySelector<HTMLElement>(
              `[data-focus-key="${returnFocusKey}"]:not(:disabled)`,
            )
          : null;
      (
        opener ?? body?.querySelector<HTMLElement>('button:not(:disabled)')
      )?.focus();
      return;
    }
    fieldRef.current?.focus();
  }, [contextReady, shownKey, returnFocusKey]);

  useEffect(() => {
    if (errorFocus > 0) errorRef.current?.focus();
  }, [errorFocus]);

  /** A changed intent is a NEW submission: the next submit gets a fresh
   * key (never refused as reusing another intent's key). */
  function intentChanged() {
    if (locked || !submitted.current) return;
    submitted.current = false;
    deviceEventId.current = null;
    setServerError(null);
  }

  function goTo(next: Step, focusKey: string | null = null) {
    if (locked) return;
    intentChanged();
    setStep(next);
    setQty(null);
    setNote('');
    setReason('');
    setShowErrors(false);
    setServerError(null);
    setReturnFocusKey(focusKey);
  }

  function back() {
    if (shown === null || shown.kind === 'overview') return;
    goTo(
      { kind: 'overview', filterDemandId: overviewFilter() },
      shown.kind === 'reverse'
        ? `reverse:${shown.allocationId}`
        : `allocate:${shown.demandId}`,
    );
  }

  /** The Overview a `reverse` start with several allocations showed. */
  function overviewFilter(): number | null {
    return start.step === 'reverse' && scopePn === null
      ? start.workOrderDemandId
      : null;
  }

  function requestClose() {
    onClose(outcomeUnknown || busy);
  }

  function workOrderNumberOf(workOrderId: number): string | null {
    return (
      context?.lines.find((l) => l.workOrderId === workOrderId)
        ?.workOrderNumber ??
      line?.workOrderNumber ??
      null
    );
  }

  /** Explain a refusal; nothing was recorded unless the outcome is
   * unknown. `afterUnknown`: this submit repeated an intent whose
   * outcome was unknown. */
  function handleFailure(error: unknown, afterUnknown: boolean) {
    const unknown =
      !(error instanceof ApiError) ||
      error.status === 408 ||
      error.status >= 500;
    if (unknown) {
      setOutcomeUnknown(true);
      setServerError(OUTCOME_UNKNOWN);
      setErrorFocus((value) => value + 1);
      return;
    }
    const detail = errorMessage(error);
    setErrorFocus((value) => value + 1);
    if (error.status === 401) {
      // Authorization precedes the key check: the intent and its key
      // stay for the resubmit after signing in again.
      setServerError(`${detail} ${SIGN_IN_AGAIN}`);
      return;
    }
    if (error.status === 403) {
      setServerError(afterUnknown ? `${detail} ${MAY_BE_RECORDED}` : detail);
      return;
    }
    setServerError(detail);
    // Fresh figures in the background — a failed re-read keeps the
    // step and its inputs on screen.
    contextData.revalidate();
    // Another user's request id stays taken: the lock (if any) stays.
    if (refusalFlag(error, 'recorded_by_another_user')) return;
    // An explicit refusal of the identical resubmit proves the key
    // never committed: the intent may change again.
    if (afterUnknown && (error.status === 409 || error.status === 422)) {
      setOutcomeUnknown(false);
    }
  }

  async function send(
    call: (key: string) => Promise<ManagementAllocationResult>,
  ) {
    if (line === null) return;
    const afterUnknown = outcomeUnknown;
    // The quantity travels as shown and stays fixed for a resubmit.
    setQty(qtyText);
    setBusy(true);
    setServerError(null);
    submitted.current = true;
    deviceEventId.current ??= newDeviceEventId();
    const key = deviceEventId.current;
    try {
      const result = await call(key);
      if (!alive.current) return;
      setBusy(false);
      onCommitted(result, committedNotice(result, line, workOrderNumberOf));
    } catch (error) {
      if (!alive.current) return;
      setBusy(false);
      handleFailure(error, afterUnknown);
    }
  }

  function invalid(message: string | null): boolean {
    if (message === null) return false;
    setShowErrors(true);
    fieldRef.current?.focus();
    return true;
  }

  function submitAllocate() {
    if (line === null || context === null || busy || writeBlocked) return;
    if (invalid(allocateQtyError(qtyText, line, available))) return;
    const quantity = parseQuantity(qtyText) ?? 0;
    const noteText = note.trim() === '' ? null : note;
    void send((key) =>
      allocateFromStock({
        partNumber: context.partNumber,
        workOrderDemandId: line.workOrderDemandId,
        quantity,
        note: noteText,
        deviceEventId: key,
      }),
    );
  }

  function submitBeyond() {
    if (line === null || context === null || busy || writeBlocked) return;
    const qtyError = beyondQtyError(qtyText, line, available);
    const reasonMessage = reasonError(reason, 'correction');
    if (qtyError !== null || reasonMessage !== null) {
      setShowErrors(true);
      if (qtyError !== null) fieldRef.current?.focus();
      else document.getElementById(`${headingId}-reason`)?.focus();
      return;
    }
    const quantity = parseQuantity(qtyText) ?? 0;
    void send((key) =>
      allocateBeyondDemand({
        partNumber: context.partNumber,
        workOrderDemandId: line.workOrderDemandId,
        quantity,
        reason,
        deviceEventId: key,
      }),
    );
  }

  function submitReverse() {
    if (allocation === null || busy || writeBlocked) return;
    if (invalid(reasonError(reason, 'reversal'))) return;
    const allocationId = allocation.allocationId;
    void send((key) =>
      reverseAllocation({ allocationId, reason, deviceEventId: key }),
    );
  }

  const title = shown === null ? TITLES.overview : TITLES[shown.kind];

  function renderError() {
    return serverError ? (
      <div className="aad-error" role="alert" tabIndex={-1} ref={errorRef}>
        {serverError}
      </div>
    ) : null;
  }

  function lineFacts(target: ContextLine, pn: string) {
    return (
      <dl className="aad-facts">
        <dt>Work Order</dt>
        <dd className="mono">{workOrderLabel(target.workOrderNumber)}</dd>
        <dt>Part Number</dt>
        <dd className="mono">{pn}</dd>
        <dt>Requested</dt>
        <dd className="mono">{target.requestedQuantity} pcs</dd>
        <dt>Allocated</dt>
        <dd className="mono">{target.allocatedQuantity} pcs</dd>
        <dt>Still needed</dt>
        <dd className="mono">{target.remainingShortage} pcs</dd>
        <dt>Available in stock</dt>
        <dd className="mono">{available} pcs</dd>
      </dl>
    );
  }

  function renderOverview(ctx: AllocationContext, filter: number | null) {
    const lines =
      filter === null
        ? ctx.lines
        : ctx.lines.filter((l) => l.workOrderDemandId === filter);
    return (
      <div ref={bodyRef}>
        <div className="aad-stock">
          <span className="mono">{ctx.partNumber}</span> · stocked{' '}
          {ctx.stockedQuantity} pcs · allocated {ctx.activeAllocatedQuantity} ·
          available {ctx.availableStockedQuantity}
        </div>
        {lines.length === 0 ? (
          <div className="aad-note">{NO_OPEN_DEMAND}</div>
        ) : (
          <ul className="aad-lines">
            {lines.map((l) => (
              <li className="aad-line" key={l.workOrderDemandId}>
                <div className="aad-linehead">
                  <b className="mono">WO {workOrderLabel(l.workOrderNumber)}</b>
                  <span> · due {formatIsoDate(l.dueDate)}</span>
                </div>
                <div className="aad-linefig">
                  {l.allocatedQuantity} of {l.requestedQuantity} pcs allocated ·{' '}
                  {l.remainingShortage > 0
                    ? `${l.remainingShortage} pcs still needed`
                    : 'fully allocated'}
                  {l.beyondDemandQuantity > 0 ? (
                    <>
                      {' · '}
                      <span className="aad-beyond">
                        +{l.beyondDemandQuantity} pcs beyond demand
                      </span>
                    </>
                  ) : null}
                </div>
                <button
                  className="btn ghost aad-open"
                  data-focus-key={`allocate:${l.workOrderDemandId}`}
                  disabled={locked}
                  onClick={() =>
                    goTo({ kind: 'allocate', demandId: l.workOrderDemandId })
                  }
                >
                  Allocate from stock…
                </button>
                {l.activeAllocations.length === 0 ? (
                  <div className="aad-note">No active allocation.</div>
                ) : (
                  <ul className="aad-allocs">
                    {l.activeAllocations.map((a) => (
                      <li key={a.allocationId}>
                        <span className="aad-allocdesc">
                          {a.quantity} pcs · {sourceLabel(a.source)} ·{' '}
                          {allocationTimestamp(a.allocatedAt)}
                          {a.exceedsDemand ? ' · beyond demand' : ''}
                          {a.actorUser ? (
                            <>
                              {' · '}
                              <Actor actor={a.actorUser} />
                            </>
                          ) : null}
                          {a.allocationReason
                            ? ` · reason: ${a.allocationReason}`
                            : ''}
                        </span>
                        <button
                          className="btn ghost aad-open"
                          data-focus-key={`reverse:${a.allocationId}`}
                          aria-label={`Reverse allocation of ${a.quantity} pcs to Work Order ${workOrderLabel(l.workOrderNumber)}`}
                          disabled={locked}
                          onClick={() =>
                            goTo({
                              kind: 'reverse',
                              demandId: l.workOrderDemandId,
                              allocationId: a.allocationId,
                            })
                          }
                        >
                          Reverse…
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
        {renderError()}
        <div className="row">
          <button className="bigbtn ghost" onClick={requestClose}>
            Close (Esc)
          </button>
        </div>
      </div>
    );
  }

  function renderAllocate(ctx: AllocationContext, target: ContextLine) {
    const qtyError =
      qtyText.trim() !== '' || showErrors
        ? allocateQtyError(qtyText, target, available)
        : null;
    const submittable =
      !writeBlocked &&
      !busy &&
      limit > 0 &&
      allocateQtyError(qtyText, target, available) === null;
    return (
      <>
        {lineFacts(target, ctx.partNumber)}
        {limit === 0 ? (
          <div className="aad-note">
            {available <= 0
              ? `No stocked quantity of ${ctx.partNumber} is available to allocate.`
              : range !== null
                ? FULLY_ALLOCATED_NOTE
                : null}
          </div>
        ) : null}
        <div className="aad-form">
          <label htmlFor={`${headingId}-qty`}>Quantity to allocate (pcs)</label>
          <input
            id={`${headingId}-qty`}
            ref={(el) => {
              fieldRef.current = el;
            }}
            className="mono"
            inputMode="numeric"
            value={qtyText}
            readOnly={locked}
            aria-invalid={qtyError !== null ? true : undefined}
            onChange={(e) => {
              if (locked) return;
              setQty(e.target.value);
              intentChanged();
            }}
          />
          {qtyError !== null ? (
            <div className="aad-fielderr">{qtyError}</div>
          ) : null}
          <label htmlFor={`${headingId}-note`}>
            Note <span className="field-optional">(optional)</span>
          </label>
          <input
            id={`${headingId}-note`}
            value={note}
            readOnly={locked}
            onChange={(e) => {
              if (locked) return;
              setNote(e.target.value);
              intentChanged();
            }}
          />
        </div>
        {renderError()}
        <div className="row aad-actions">
          <button
            className="bigbtn primary"
            disabled={!submittable}
            onClick={submitAllocate}
          >
            Allocate
          </button>
          {range !== null ? (
            <button
              className="bigbtn ghost"
              disabled={locked}
              onClick={() =>
                goTo({ kind: 'beyond', demandId: target.workOrderDemandId })
              }
            >
              Allocate beyond demand…
            </button>
          ) : null}
        </div>
        <div className="row">
          <button className="bigbtn ghost" disabled={locked} onClick={back}>
            Back
          </button>
          <button className="bigbtn ghost" onClick={requestClose}>
            Cancel (Esc)
          </button>
        </div>
      </>
    );
  }

  function renderBeyond(ctx: AllocationContext, target: ContextLine) {
    const qtyError =
      qtyText.trim() !== '' || showErrors
        ? beyondQtyError(qtyText, target, available)
        : null;
    const reasonMessage = showErrors ? reasonError(reason, 'correction') : null;
    const quantity = parseQuantity(qtyText);
    return (
      <>
        {lineFacts(target, ctx.partNumber)}
        <div className="aad-warning" role="note">
          <h4>Correction beyond demand</h4>
          <p>{BEYOND_DEMAND_WARNING}</p>
        </div>
        <div className="aad-form">
          <label htmlFor={`${headingId}-qty`}>Quantity to allocate (pcs)</label>
          <input
            id={`${headingId}-qty`}
            ref={(el) => {
              fieldRef.current = el;
            }}
            className="mono"
            inputMode="numeric"
            value={qtyText}
            readOnly={locked}
            aria-invalid={qtyError !== null ? true : undefined}
            onChange={(e) => {
              if (locked) return;
              setQty(e.target.value);
              intentChanged();
            }}
          />
          {qtyError !== null ? (
            <div className="aad-fielderr">{qtyError}</div>
          ) : null}
          {quantity !== null && qtyError === null ? (
            <div className="aad-after" role="status">
              {afterCorrectionLine(target, quantity)}
            </div>
          ) : null}
          <label htmlFor={`${headingId}-reason`}>
            Reason <span className="field-required">(required)</span>
          </label>
          <textarea
            id={`${headingId}-reason`}
            rows={3}
            value={reason}
            readOnly={locked}
            aria-invalid={reasonMessage !== null ? true : undefined}
            onChange={(e) => {
              if (locked) return;
              setReason(e.target.value);
              intentChanged();
            }}
          />
          {reasonMessage !== null ? (
            <div className="aad-fielderr">{reasonMessage}</div>
          ) : null}
        </div>
        {renderError()}
        <div className="row">
          <button
            className="bigbtn aad-warnbtn"
            disabled={writeBlocked || busy}
            onClick={submitBeyond}
          >
            Record correction
          </button>
          <button className="bigbtn ghost" disabled={locked} onClick={back}>
            Back
          </button>
        </div>
      </>
    );
  }

  function renderReverse(target: ContextLine, row: ContextAllocation) {
    const reasonMessage = showErrors ? reasonError(reason, 'reversal') : null;
    return (
      <>
        <p className="aad-facts-line">
          {row.quantity} pcs allocated to Work Order{' '}
          {workOrderLabel(target.workOrderNumber)} on{' '}
          {allocationTimestamp(row.allocatedAt)} ({sourceLabel(row.source)})
          {row.exceedsDemand ? ' · beyond demand' : ''}
          {row.actorUser ? (
            <>
              {' · '}
              <Actor actor={row.actorUser} />
            </>
          ) : null}
        </p>
        <div className="aad-consequence">
          {row.quantity} pcs return to available stock.
          {reversalReopens(target, row) ? (
            <>
              {' '}
              <b>
                Work Order {workOrderLabel(target.workOrderNumber)} becomes Open
                again.
              </b>
            </>
          ) : null}
        </div>
        <div className="aad-form">
          <label htmlFor={`${headingId}-reason`}>
            Reason <span className="field-required">(required)</span>
          </label>
          <textarea
            id={`${headingId}-reason`}
            ref={(el) => {
              fieldRef.current = el;
            }}
            rows={3}
            value={reason}
            readOnly={locked}
            aria-invalid={reasonMessage !== null ? true : undefined}
            onChange={(e) => {
              if (locked) return;
              setReason(e.target.value);
              intentChanged();
            }}
          />
          {reasonMessage !== null ? (
            <div className="aad-fielderr">{reasonMessage}</div>
          ) : null}
        </div>
        {renderError()}
        <div className="row">
          <button
            className="bigbtn danger"
            disabled={writeBlocked || busy}
            onClick={submitReverse}
          >
            Reverse allocation
          </button>
        </div>
        <div className="row">
          <button className="bigbtn ghost" disabled={locked} onClick={back}>
            Back
          </button>
          <button className="bigbtn ghost" onClick={requestClose}>
            Cancel (Esc)
          </button>
        </div>
      </>
    );
  }

  return (
    <ModalDialog
      labelledBy={headingId}
      onClose={requestClose}
      size="wide"
      className="aad"
    >
      <h3 id={headingId}>{title}</h3>
      {contextData.state.status === 'error' ? (
        <>
          <ErrorState
            message="The allocation could not be loaded."
            detail={contextData.state.message}
            onRetry={contextData.reload}
          />
          <div className="row">
            <button className="bigbtn ghost" onClick={requestClose}>
              Close (Esc)
            </button>
          </div>
        </>
      ) : context === null || shown === null ? (
        <>
          <LoadingState label="Loading allocation…" />
          <div className="row">
            <button className="bigbtn ghost" onClick={requestClose}>
              Close (Esc)
            </button>
          </div>
        </>
      ) : shown.kind === 'overview' ? (
        renderOverview(context, shown.filterDemandId)
      ) : line === null ? null : shown.kind === 'allocate' ? (
        renderAllocate(context, line)
      ) : shown.kind === 'beyond' ? (
        renderBeyond(context, line)
      ) : allocation !== null ? (
        renderReverse(line, allocation)
      ) : null}
    </ModalDialog>
  );
}
