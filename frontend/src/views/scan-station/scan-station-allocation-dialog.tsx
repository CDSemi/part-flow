import { useCallback, useEffect, useRef, useState } from 'react';

import {
  confirmAllocation,
  getAllocationSuggestion,
} from '../../api/allocations';
import type {
  AllocationResult,
  AllocationSuggestion,
  SuggestedAllocationLine,
} from '../../api/allocations';
import { errorMessage } from '../../api/client';
import { newDeviceEventId } from '../../api/production-release';
import { areaRefColor, writeOutcomeUnknown } from '../../api/scan-station';
import type {
  AreaRef,
  StationContext,
  TransferResult,
} from '../../api/scan-station';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import { formatIsoDate } from '../dates';
import { EntityChip, Guidance, StepButtons } from './scan-station-presentation';
import { enterKeyHandler } from './scan-station-wizard';
import { WriteGuidance } from './scan-station-machine-dialogs';

/**
 * The receiving allocation of the Stockroom station (GUI_DESIGN §10;
 * PROJECT_PROFILE §18 Receiving Confirmation) — the step that follows
 * a `STOCKED` write the server confirmed.
 *
 * The dialog shows the server's canonical suggestion for exactly the
 * stocked quantity: every outstanding demand line of the PN in the
 * canonical demand ordering (Hot rank, then earliest due date, undated
 * demand after dated by received date) with its Work Order, requested,
 * previously allocated, remaining shortage and proposed quantity. The
 * operator may adjust the proposal with the +/− steppers (never beyond
 * a line's shortage); Confirm is enabled only while the allocated total
 * equals the quantity being allocated — the stocked quantity, which
 * travels as the explicit `allocation_quantity` (never the PN's whole
 * available stock). The server judges the request under its lock: a
 * refusal (a stale available figure, a shortage that shrank) keeps the
 * dialog open with the reason, refreshes the suggestion from the
 * server, and records nothing; a lost response freezes the intent and
 * retries the exact same request under the same `device_event_id`.
 * Nothing is reported as allocated before the server confirmed it.
 * Leaving without allocating is legitimate: the stocked quantity stays
 * available for allocation from Management.
 */
export function AllocationDialog({
  station,
  stocked,
  sourceArea,
  writeBlocked,
  onDone,
  onLeave,
  onAbandonUnknown,
}: {
  station: StationContext;
  /** The server-confirmed STOCKED command whose quantity is allocated. */
  stocked: TransferResult;
  sourceArea: AreaRef;
  writeBlocked: boolean;
  /** Called ONLY with a server-confirmed allocation, with the Work Order
   * Numbers the suggestion showed (by Work Order id) so the completed
   * Work Orders can be NAMED, not only numbered by id. */
  onDone: (
    result: AllocationResult,
    workOrderNumbers: ReadonlyMap<number, string | null>,
  ) => void;
  /** Leave the quantity in stock, unallocated (nothing recorded). */
  onLeave: () => void;
  /** The operator abandons an allocation whose outcome is UNKNOWN. */
  onAbandonUnknown: () => void;
}) {
  const pn = stocked.partNumber;
  // The quantity being allocated: the just-stocked quantity, fixed for
  // the life of this dialog — never re-read from the suggestion's
  // available figure.
  const allocationQuantity = stocked.quantity;

  const [suggestion, setSuggestion] = useState<AllocationSuggestion | null>(
    null,
  );
  const [loadError, setLoadError] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);
  const refreshSuggestion = useCallback(() => {
    setLoadError(null);
    setGeneration((value) => value + 1);
  }, []);
  // Per demand line: the quantity the operator will allocate to it.
  const [quantities, setQuantities] = useState<Map<number, number>>(new Map());

  useEffect(() => {
    let cancelled = false;
    setSuggestion(null);
    getAllocationSuggestion(pn, allocationQuantity).then(
      (fresh) => {
        if (cancelled) return;
        setSuggestion(fresh);
        // The server's proposal seeds the editable quantities — also
        // after a refresh, since the refused snapshot is stale.
        setQuantities(
          new Map(
            fresh.lines.map((line) => [
              line.workOrderDemandId,
              line.proposedQuantity,
            ]),
          ),
        );
      },
      (error: unknown) => {
        if (!cancelled) setLoadError(errorMessage(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [pn, allocationQuantity, generation]);

  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [outcomeUnknown, setOutcomeUnknown] = useState(false);
  // One idempotency key per confirmed intent. An explicit refusal
  // (nothing recorded) lets the operator adjust the lines: that is a
  // NEW intent, so it takes a new key — a retry after an unknown
  // outcome keeps the frozen one.
  const deviceEventId = useRef(newDeviceEventId());

  const total = [...quantities.values()].reduce((sum, value) => sum + value, 0);
  const totalMatches = total === allocationQuantity;
  const lines = suggestion?.lines ?? [];
  const noDemand = suggestion !== null && lines.length === 0;
  const shortageTotal = lines.reduce(
    (sum, line) => sum + line.remainingShortage,
    0,
  );
  // The stocked quantity exceeds what the outstanding demand can take:
  // the lines can never add up to it — the surplus stays in stock.
  const cannotReachTotal =
    suggestion !== null && shortageTotal < allocationQuantity;
  // Someone allocated meanwhile: the server will refuse this quantity
  // as stale — say so up front (the server stays the authority).
  const staleStock =
    suggestion !== null &&
    suggestion.availableStockedQuantity < allocationQuantity;

  function setQuantity(line: SuggestedAllocationLine, value: number) {
    const clamped = Math.max(0, Math.min(line.remainingShortage, value));
    setQuantities((current) => {
      const next = new Map(current);
      next.set(line.workOrderDemandId, clamped);
      return next;
    });
  }

  async function confirm() {
    if (busy || !totalMatches) return;
    if (writeBlocked) {
      setServerError(
        'Connection lost — the allocation was not sent. Reconnect and confirm again; nothing was recorded.',
      );
      return;
    }
    setBusy(true);
    setServerError(null);
    let result: AllocationResult;
    try {
      result = await confirmAllocation({
        partNumber: pn,
        allocationQuantity,
        lines: [...quantities]
          .filter(([, quantity]) => quantity > 0)
          .map(([workOrderDemandId, quantity]) => ({
            workOrderDemandId,
            quantity,
          })),
        stationId: station.stationId,
        deviceEventId: deviceEventId.current,
      });
    } catch (error) {
      if (writeOutcomeUnknown(error)) {
        // The intent is frozen: the exact same request replays the
        // committed allocation or records it once.
        setOutcomeUnknown(true);
        setServerError(null);
      } else {
        // An explicit refusal — nothing recorded. The suggestion is
        // re-read from the server so the operator adjusts against the
        // current figures, under a fresh idempotency key.
        setServerError(
          `${errorMessage(error)} The suggestion was refreshed from the server — review the lines and confirm again.`,
        );
        deviceEventId.current = newDeviceEventId();
        refreshSuggestion();
      }
      setBusy(false);
      return;
    }
    setBusy(false);
    onDone(
      result,
      new Map(lines.map((line) => [line.workOrderId, line.workOrderNumber])),
    );
  }

  const cancel = outcomeUnknown ? onAbandonUnknown : onLeave;
  const frozen = busy || outcomeUnknown;

  return (
    <ModalDialog
      label="Allocate stocked quantity"
      onClose={busy ? () => undefined : cancel}
      size="wide"
      onKeyDown={enterKeyHandler(() => void confirm())}
    >
      <h3>Allocate stocked quantity</h3>
      <div className="big mono" title={pn}>
        {pn}
      </div>
      <div className="sub">
        <b>{allocationQuantity} pcs</b> stocked at{' '}
        <EntityChip>
          <AreaDot colorVar={areaRefColor(station.area)} />
          {station.area.name}
        </EntityChip>{' '}
        from{' '}
        <EntityChip>
          <AreaDot colorVar={areaRefColor(sourceArea)} />
          {sourceArea.name}
        </EntityChip>{' '}
        — recorded by the server. Allocate exactly this quantity to the
        outstanding Work Order Demand below; the suggestion follows the
        canonical demand ordering and may be adjusted.
      </div>
      {loadError !== null ? (
        <ErrorState
          message="The allocation suggestion could not be loaded."
          detail={loadError}
          onRetry={refreshSuggestion}
        />
      ) : suggestion === null ? (
        <LoadingState label="Loading the allocation suggestion" />
      ) : noDemand ? (
        <Guidance tone="info">
          No outstanding Work Order Demand for this Part Number — the{' '}
          {allocationQuantity} pcs stay in stock (
          {suggestion.availableStockedQuantity} pcs available in total).
        </Guidance>
      ) : (
        <>
          {staleStock ? (
            <Guidance tone="warn">
              Only {suggestion.availableStockedQuantity} pcs of this Part Number
              are still unallocated in stock — quantity was allocated elsewhere
              meanwhile. The server refuses an allocation of{' '}
              {allocationQuantity} pcs; leave the quantity in stock and allocate
              it from Management.
            </Guidance>
          ) : null}
          <table className="ss-alloc" aria-label="Allocation suggestion">
            <thead>
              <tr>
                <th>Work Order</th>
                <th className="num">Requested</th>
                <th className="num">Allocated</th>
                <th className="num">Shortage</th>
                <th className="num">Allocate now</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => {
                const value = quantities.get(line.workOrderDemandId) ?? 0;
                const changed = value !== line.proposedQuantity;
                return (
                  <tr key={line.workOrderDemandId}>
                    <td>
                      <span className="mono">
                        {line.workOrderNumber ?? '—'}
                      </span>
                      {line.priorityRank !== null ? (
                        <span className="ss-alloc-hot">
                          {' '}
                          🔥#{line.priorityRank}
                        </span>
                      ) : null}
                      <div className="sub">
                        due {formatIsoDate(line.dueDate)} · received{' '}
                        {formatIsoDate(line.receivedDate)}
                      </div>
                    </td>
                    <td className="num mono">{line.requestedQuantity}</td>
                    <td className="num mono">
                      {line.previouslyAllocatedQuantity}
                    </td>
                    <td className="num mono">{line.remainingShortage}</td>
                    <td className="num">
                      <div className="ss-alloc-stepper">
                        <button
                          type="button"
                          className="pickbtn"
                          aria-label={`Allocate one less to ${line.workOrderNumber ?? 'internal Work Order'}`}
                          disabled={frozen || value <= 0}
                          onClick={() => setQuantity(line, value - 1)}
                        >
                          −
                        </button>
                        <input
                          className="field mono ss-alloc-qty"
                          inputMode="numeric"
                          aria-label={`Quantity for ${line.workOrderNumber ?? 'internal Work Order'}`}
                          value={value}
                          disabled={frozen}
                          onChange={(event) =>
                            setQuantity(
                              line,
                              Number.parseInt(event.target.value || '0', 10) ||
                                0,
                            )
                          }
                        />
                        <button
                          type="button"
                          className="pickbtn"
                          aria-label={`Allocate one more to ${line.workOrderNumber ?? 'internal Work Order'}`}
                          disabled={frozen || value >= line.remainingShortage}
                          onClick={() => setQuantity(line, value + 1)}
                        >
                          +
                        </button>
                        {changed ? (
                          <span className="ss-alloc-adj">
                            adjusted (suggested {line.proposedQuantity})
                          </span>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="ss-alloc-total" role="status">
            <span className="lbl">Allocated total</span>
            <span className={`cnt mono${totalMatches ? ' ok' : ''}`}>
              {total} / {allocationQuantity} pcs
            </span>
            {!totalMatches ? (
              <span className="sub">
                {total < allocationQuantity
                  ? `${allocationQuantity - total} pcs still to allocate`
                  : `${total - allocationQuantity} pcs too many`}
              </span>
            ) : null}
          </div>
          {cannotReachTotal ? (
            <Guidance tone="warn">
              The outstanding demand takes at most {shortageTotal} pcs, so the
              full {allocationQuantity} pcs cannot be allocated now — the
              surplus stays in stock. Leave the quantity in stock, or allocate
              the rest from Management later.
            </Guidance>
          ) : !totalMatches ? (
            <Guidance tone="action">
              The allocated total must equal the {allocationQuantity} pcs being
              allocated before confirming.
            </Guidance>
          ) : null}
        </>
      )}
      <WriteGuidance
        outcomeUnknown={outcomeUnknown}
        serverError={serverError}
        writeBlocked={writeBlocked}
        what="allocation"
      />
      <StepButtons
        onCancel={cancel}
        cancelLabel={
          outcomeUnknown
            ? 'Leave — check the Area'
            : noDemand
              ? 'Close'
              : 'Leave in stock — allocate later'
        }
        primary={{
          label: outcomeUnknown
            ? 'Retry the same allocation'
            : serverError
              ? 'Retry allocation'
              : busy
                ? 'Recording…'
                : 'Confirm allocation',
          onClick: () => void confirm(),
          disabled:
            busy ||
            writeBlocked ||
            suggestion === null ||
            noDemand ||
            (!outcomeUnknown && !totalMatches),
          autoFocus: true,
        }}
      />
    </ModalDialog>
  );
}
