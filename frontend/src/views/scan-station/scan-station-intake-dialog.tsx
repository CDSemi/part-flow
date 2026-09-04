import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { errorMessage } from '../../api/client';
import {
  areaRefColor,
  receiveQuantity,
  workOrderSelectionRequired,
} from '../../api/scan-station';
import type {
  InternalWorkOrder,
  OperationRef,
  ReceiptResult,
  ScanResolution,
  StationContext,
} from '../../api/scan-station';
import { listRouteTemplates } from '../../api/route-templates';
import type { RouteTemplate } from '../../api/route-templates';
import { AreaDot, RouteModeChip, TypeChip } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { QuantityKeypad } from '../../components/QuantityKeypad';
import { formatIsoDate } from '../dates';
import {
  ConfirmationSummary,
  EntityChip,
  Guidance,
  StepButtons,
  StepRecap,
} from './scan-station-presentation';
import { WriteGuidance } from './scan-station-machine-dialogs';
import { useOneShotWrite } from './scan-station-write';
import {
  enterKeyHandler,
  operationLabel,
  quantityKeyHandler,
} from './scan-station-wizard';

/**
 * `Receive Quantity` — the real Scan Station MODIFY intake (Phase 10.5,
 * GUI_DESIGN §4.7 item 1, PROJECT_PROFILE §14). One temporary wizard in
 * three views inside ONE dialog lifecycle: settings → quantity →
 * confirmation, with `Confirm receipt` as the only write point.
 *
 * The defaults `Request Type = MODIFY` and `Route Mode = FLOATING` are
 * editable, never forced; a Planned Route is selected only for
 * `PLANNED`; the due date is optional and belongs to the Work Order
 * Demand; the received date is the scan itself — the resolution's
 * `scannedAt` travels unchanged to `Confirm receipt`, so a wizard left
 * open across midnight still records the day the PN was scanned, and
 * the server derives that date on the site calendar.
 * When several internal Work Orders without an external number could
 * take the receipt, the settings view REQUIRES an explicit choice
 * between them — the station never guesses a first match, and the
 * server refuses the same way if a further one appeared meanwhile.
 *
 * The write follows the shared one-shot protocol: nothing is recorded
 * before the final confirmation, success reads only after the server
 * confirmed, a rejection keeps the wizard open with the server's
 * reason and nothing recorded, and a lost response freezes the intent
 * behind the SAME `device_event_id`. `intake` stays the internal name
 * — it never renders to an operator.
 *
 * Production-safe: server state only, no mock imports.
 */

function AreaChip({
  area,
  children,
}: {
  area: { color: string | null; id: number };
  children: ReactNode;
}) {
  return (
    <EntityChip>
      <AreaDot colorVar={areaRefColor(area)} />
      {children}
    </EntityChip>
  );
}

/** How one reusable internal Work Order reads to an operator: its
 * business facts, never the database key (PROJECT_PROFILE §7). */
function workOrderLabel(candidate: InternalWorkOrder): string {
  const jobs =
    candidate.jobNumbers.length > 0 ? candidate.jobNumbers.join(', ') : null;
  return [
    `Received ${formatIsoDate(candidate.receivedDate)}`,
    `${candidate.requestedQuantity} pcs requested`,
    `Due ${formatIsoDate(candidate.dueDate)}`,
    jobs ? `Jobs ${jobs}` : null,
  ]
    .filter(Boolean)
    .join(' · ');
}

export function IntakeDialog({
  station,
  resolution,
  hasMachines,
  writeBlocked,
  onBack,
  onCancel,
  onDone,
  onRejected,
  onAbandonUnknown,
}: {
  station: StationContext;
  /** The server resolution this receipt was opened from: the canonical
   * PN, the Area's Operations, whether the PN master is known, and the
   * internal Work Orders a MODIFY receipt may reuse. */
  resolution: ScanResolution;
  /** The Area mode of the freshest server read: received quantity
   * enters the queue (Machines) or direct processing (no Machines). */
  hasMachines: boolean;
  writeBlocked: boolean;
  /** Back to the step this wizard was opened from (manual PN entry). */
  onBack?: () => void;
  onCancel: () => void;
  /** Called ONLY with a server-confirmed result. */
  onDone: (result: ReceiptResult) => void;
  /** The server refused the write (nothing recorded); see useOneShotWrite. */
  onRejected?: () => void;
  onAbandonUnknown: () => void;
}) {
  const partNumber = resolution.partNumber;
  const areaName = station.area.name;
  const operations: OperationRef[] = resolution.operations;
  const [step, setStep] = useState<'settings' | 'qty' | 'confirm'>('settings');
  // Deliberately no MAX and no default quantity (GUI_DESIGN §4.7).
  const [qty, setQty] = useState('');
  const [requestType, setRequestType] = useState<'NEW' | 'MODIFY'>('MODIFY');
  const [routeMode, setRouteMode] = useState<'FLOATING' | 'PLANNED'>(
    'FLOATING',
  );
  const [routeTemplateId, setRouteTemplateId] = useState<number | null>(null);
  const [dueDate, setDueDate] = useState('');
  const [notes, setNotes] = useState('');
  const operationRequired = operations.length > 1;
  const [operationId, setOperationId] = useState<number | null>(
    operationRequired ? null : (operations[0]?.id ?? null),
  );
  const operation = operations.find((item) => item.id === operationId) ?? null;
  // The server's candidate list, refreshed when a refusal reports that
  // a further internal Work Order became plausible meanwhile.
  const [candidates, setCandidates] = useState<InternalWorkOrder[]>(
    resolution.internalWorkOrders,
  );
  const [workOrderId, setWorkOrderId] = useState<number | null>(null);

  // Planned Routes are loaded only when the operator actually chooses
  // PLANNED — a FLOATING receipt needs no route at all.
  const [routes, setRoutes] = useState<RouteTemplate[] | null>(null);
  const [routesError, setRoutesError] = useState<string | null>(null);
  useEffect(() => {
    if (routeMode !== 'PLANNED' || routes !== null) return;
    let cancelled = false;
    void listRouteTemplates().then(
      (list) => {
        if (!cancelled) setRoutes(list);
      },
      (error: unknown) => {
        if (!cancelled) setRoutesError(errorMessage(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [routeMode, routes]);

  // A Planned Route may be received against only where it STARTS: its
  // first step must be this Area (and its Operation when the step
  // fixes one). The server validates the same rule authoritatively.
  const startableRoutes = (routes ?? []).filter((route) => {
    const first = route.steps[0];
    if (!first || first.areaId !== station.area.id) return false;
    return (
      first.operationId === null ||
      operationId === null ||
      first.operationId === operationId
    );
  });
  const route =
    startableRoutes.find((item) => item.id === routeTemplateId) ?? null;
  // The Route Mode chip carries the route information itself, so no
  // separate Planned Route row exists on the confirmation (§4.7).
  const routeDetail =
    routeMode === 'PLANNED' ? (route?.name ?? '—') : 'actual trace';

  // Reuse is a MODIFY rule: a NEW receipt always creates its own
  // internal Work Order (PROJECT_PROFILE §14).
  const reusable = requestType === 'MODIFY' ? candidates : [];
  const selectionRequired = reusable.length > 1;
  const selected =
    reusable.find((item) => item.workOrderId === workOrderId) ??
    (reusable.length === 1 ? reusable[0] : null);
  const workOrderBehavior = selected
    ? `Adds the quantity to the internal Work Order without an external number (WO —) · ${workOrderLabel(selected)}`
    : 'Creates an internal Work Order without an external number (displays —)';

  const parsedQty = parseInt(qty || '0', 10);
  const settingsValid =
    operation !== null &&
    (routeMode === 'FLOATING' || route !== null) &&
    (!selectionRequired || selected !== null);
  const valid = settingsValid && Number.isInteger(parsedQty) && parsedQty >= 1;
  // The quantity confirmed on the quantity view — frozen for the
  // summary and the request.
  const [confirmed, setConfirmed] = useState(0);
  const destination = hasMachines
    ? `${areaName} queue (awaiting Machine)`
    : `${areaName} — direct processing`;

  function goQty() {
    if (!settingsValid) return;
    // The refusal that sent the operator back here is answered by the
    // corrected selection; the notice does not follow them forward.
    setSelectionNotice(null);
    setStep('qty');
  }

  function goConfirm() {
    if (!valid) return;
    setConfirmed(parsedQty);
    setStep('confirm');
  }

  // A refusal that reports further plausible internal Work Orders is
  // not a retryable intent: the operator must choose first, so the
  // wizard returns to the settings view with the server's candidates
  // and the next confirmation carries a NEW `device_event_id`.
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);

  const write = useOneShotWrite<ReceiptResult>({
    writeBlocked,
    onRejected,
    send: async (deviceEventId) => {
      try {
        return await receiveQuantity({
          stationId: station.stationId,
          partNumber,
          quantity: confirmed,
          requestType,
          routeMode,
          routeTemplateId: routeMode === 'PLANNED' ? (route?.id ?? null) : null,
          operationId: operation?.id ?? null,
          dueDate: dueDate || null,
          reason: notes.trim() || null,
          workOrderId: selected?.workOrderId ?? null,
          // The SERVER instant of the scan this wizard was opened
          // from, carried through every step: the received date
          // follows the scan, never this confirmation (§14).
          scannedAt: resolution.scannedAt,
          deviceEventId,
        });
      } catch (error) {
        const options = workOrderSelectionRequired(error);
        if (options !== null) {
          setCandidates(options);
          setWorkOrderId(null);
          setStep('settings');
          setSelectionNotice(errorMessage(error));
        }
        throw error;
      }
    },
    onDone,
  });

  const { resetIntent, rejected } = write;
  useEffect(() => {
    // Waits for the refusal to be RECORDED by the write hook (the
    // rejection resumes a microtask later than the handler above), so
    // the fresh intent is not immediately marked rejected again.
    if (selectionNotice === null || !rejected) return;
    resetIntent();
  }, [selectionNotice, rejected, resetIntent]);

  const cancel = write.outcomeUnknown ? onAbandonUnknown : onCancel;

  return (
    <ModalDialog
      label="Receive Quantity"
      size="wide"
      onClose={write.busy ? () => undefined : cancel}
      onKeyDown={
        step === 'qty'
          ? quantityKeyHandler(qty, setQty, goConfirm)
          : step === 'confirm'
            ? enterKeyHandler(() => void write.submit())
            : enterKeyHandler(goQty)
      }
    >
      <h3>Receive Quantity</h3>
      {step === 'settings' ? (
        <div>
          <div className="big mono" title={partNumber}>
            {partNumber}
          </div>
          <div className="sub">
            {resolution.partNumberKnown
              ? 'This Part Number has no active production demand. Review the details below before receiving quantity.'
              : 'New Part Number. Verify it carefully; it will be registered when you confirm the receipt.'}
          </div>
          <Guidance>
            No changes are recorded until you review and confirm the final step.
          </Guidance>
          <div className="ss-dlgrid">
            <label htmlFor="in-type">Request Type</label>
            <select
              id="in-type"
              value={requestType}
              onChange={(event) => {
                setRequestType(event.target.value as 'NEW' | 'MODIFY');
                setWorkOrderId(null);
              }}
            >
              <option value="MODIFY">MODIFY</option>
              <option value="NEW">NEW</option>
            </select>
            <label htmlFor="in-route">Route Mode</label>
            <select
              id="in-route"
              value={routeMode}
              onChange={(event) => {
                setRouteMode(event.target.value as 'FLOATING' | 'PLANNED');
                setRouteTemplateId(null);
              }}
            >
              <option value="FLOATING">FLOATING</option>
              <option value="PLANNED">PLANNED</option>
            </select>
            {routeMode === 'PLANNED' ? (
              <>
                <label htmlFor="in-planned">Planned Route</label>
                <select
                  id="in-planned"
                  value={
                    routeTemplateId === null ? '' : String(routeTemplateId)
                  }
                  onChange={(event) =>
                    setRouteTemplateId(
                      event.target.value === ''
                        ? null
                        : Number(event.target.value),
                    )
                  }
                >
                  <option value="">Select a Planned Route…</option>
                  {startableRoutes.map((item) => (
                    <option key={item.id} value={String(item.id)}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </>
            ) : null}
            <label htmlFor="in-due">
              Due date <span className="field-optional">(optional)</span>
            </label>
            <input
              id="in-due"
              type="date"
              className="mono"
              value={dueDate}
              onChange={(event) => setDueDate(event.target.value)}
            />
            {operationRequired ? null : (
              <>
                <span className="lbl">Starting Area · Operation</span>
                <span>
                  {operation ? (
                    <EntityChip>{`${areaName} — ${operationLabel(operation)}`}</EntityChip>
                  ) : (
                    '—'
                  )}
                </span>
              </>
            )}
            <label htmlFor="in-notes">Reason / notes</label>
            <input
              id="in-notes"
              className="field"
              autoComplete="off"
              value={notes}
              placeholder="Add a reason or note, if needed"
              onChange={(event) => setNotes(event.target.value)}
            />
          </div>
          {routeMode === 'PLANNED' && routesError ? (
            <Guidance tone="error">{routesError}</Guidance>
          ) : null}
          {routeMode === 'PLANNED' &&
          routes !== null &&
          startableRoutes.length === 0 ? (
            <Guidance tone="warn">
              No Planned Route starts at {areaName}. Receive with FLOATING, or
              choose a Route that starts here.
            </Guidance>
          ) : null}
          {operationRequired ? (
            <>
              <Guidance tone="action">
                {areaName} supports several Operations. Select the Operation the
                received quantity is here for.
              </Guidance>
              <div className="ss-choices" role="group" aria-label="Operation">
                {operations.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`choice${operationId === item.id ? ' selected' : ''}`}
                    aria-pressed={operationId === item.id}
                    onClick={() => setOperationId(item.id)}
                  >
                    <span className="cic" aria-hidden="true">
                      OP
                    </span>
                    <span>
                      <span className="ct1">{operationLabel(item)}</span>
                    </span>
                  </button>
                ))}
              </div>
            </>
          ) : operation === null ? (
            <Guidance tone="error">
              {areaName} has no active Operation configured. Configure one in
              Administration → Operations before receiving quantity.
            </Guidance>
          ) : null}
          {selectionNotice ? (
            <Guidance tone="error">{selectionNotice}</Guidance>
          ) : null}
          {selectionRequired ? (
            <>
              <Guidance tone="action">
                Several internal Work Orders without an external number could
                take this quantity. Select the one it belongs to — nothing is
                chosen for you.
              </Guidance>
              <div className="ss-choices" role="group" aria-label="Work Order">
                {reusable.map((item) => (
                  <button
                    key={item.workOrderId}
                    type="button"
                    className={`choice${selected?.workOrderId === item.workOrderId ? ' selected' : ''}`}
                    aria-pressed={selected?.workOrderId === item.workOrderId}
                    onClick={() => setWorkOrderId(item.workOrderId)}
                  >
                    <span className="cic" aria-hidden="true">
                      WO
                    </span>
                    <span>
                      <span className="ct1">WO —</span>
                      <span className="ct2">{workOrderLabel(item)}</span>
                    </span>
                  </button>
                ))}
              </div>
            </>
          ) : null}
          <StepRecap lines={[<>Work Order: {workOrderBehavior}</>]} />
          <StepButtons
            onBack={onBack}
            onCancel={onCancel}
            primary={{
              label: 'Next',
              onClick: goQty,
              disabled: !settingsValid,
            }}
          />
        </div>
      ) : null}
      {step === 'qty' ? (
        <div>
          <div className="big mono" title={partNumber}>
            {partNumber}
          </div>
          <StepRecap
            lines={[
              <>
                <TypeChip type={requestType} />
                {' · '}
                <RouteModeChip mode={routeMode} detail={routeDetail} />
                {operation ? (
                  <>
                    {' · '}
                    <EntityChip>{operationLabel(operation)}</EntityChip>
                  </>
                ) : null}
              </>,
              <>
                WO <b className="rval">—</b> · Due:{' '}
                <b className="rval">{formatIsoDate(dueDate || null)}</b>
                {notes.trim() ? <> · Notes: {notes.trim()}</> : null}
              </>,
            ]}
          />
          <Guidance>
            Enter the physical quantity received. No default quantity is
            assumed.
          </Guidance>
          {parsedQty < 1 ? (
            <Guidance tone="action">Enter a positive quantity.</Guidance>
          ) : null}
          <QuantityKeypad value={qty} onChange={setQty} />
          <StepButtons
            onBack={() => setStep('settings')}
            onCancel={onCancel}
            primary={{ label: 'Next', onClick: goConfirm, disabled: !valid }}
          />
        </div>
      ) : null}
      {step === 'confirm' ? (
        <div>
          <div className="big mono" title={partNumber}>
            {partNumber}
          </div>
          <div className="sub">Review the receipt, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'Receive Quantity', 'primary'],
              ['PN', <span className="mono">{partNumber}</span>, 'primary'],
              [
                'Quantity',
                <span className="mono">{confirmed} pcs</span>,
                'primary',
              ],
              ['Request Type', <TypeChip type={requestType} />],
              [
                'Route Mode',
                <RouteModeChip mode={routeMode} detail={routeDetail} />,
              ],
              ['Work Order', workOrderBehavior],
              ['Due date', formatIsoDate(dueDate || null)],
              [
                'Starting Area · Operation',
                operation ? (
                  <EntityChip>{`${areaName} — ${operationLabel(operation)}`}</EntityChip>
                ) : null,
                'primary',
              ],
              [
                'Destination',
                <AreaChip area={station.area}>{destination}</AreaChip>,
                'primary',
              ],
              ['Reason / notes', notes.trim() || null],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', 'RECEIVED', 'secondary'],
            ]}
          />
          <WriteGuidance
            outcomeUnknown={write.outcomeUnknown}
            serverError={write.serverError}
            writeBlocked={writeBlocked}
            what="receipt"
          />
          <StepButtons
            onBack={
              write.outcomeUnknown || write.rejected
                ? undefined
                : () => {
                    write.clearError();
                    setStep('qty');
                  }
            }
            onCancel={cancel}
            cancelLabel={
              write.outcomeUnknown ? 'Leave — check the Area' : 'Cancel (Esc)'
            }
            primary={{
              label: write.outcomeUnknown
                ? 'Retry the same receipt'
                : write.serverError
                  ? 'Retry receipt'
                  : write.busy
                    ? 'Recording…'
                    : 'Confirm receipt',
              onClick: () => void write.submit(),
              disabled: write.busy || writeBlocked,
              autoFocus: true,
            }}
          />
        </div>
      ) : null}
    </ModalDialog>
  );
}
