import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import {
  addQuantity,
  areaRefColor,
  scrapQuantity,
  undoProductionCommand,
} from '../../api/scan-station';
import type {
  AreaRef,
  FlowInArea,
  MachineRef,
  OperationRef,
  QuantityAdditionResult,
  ScrapResult,
  StationContext,
  UndoPreview,
  UndoResult,
} from '../../api/scan-station';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { QuantityKeypad } from '../../components/QuantityKeypad';
import { parseScan, SCRAP_BARCODE } from './barcode';
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
 * The Phase 9 correction dialogs of the REAL Scan Station (GUI_DESIGN
 * §4.5, §4.7 item 3, §4.9; PROJECT_PROFILE §8.11, §11, §14, §16):
 * `Add more quantity` (found physical quantity recorded as
 * `QUANTITY_ADJUSTED · INCREASE` on a NEW Quantity Flow with a
 * mandatory reason), `Scrap damaged quantity` (the PF:SCRAP counting
 * workflow — counting changes nothing; one confirmed write records ONE
 * auditable SCRAPPED operation for the total) and the command-level
 * Undo (the structured reversal summary from the server's undo
 * preview, the final warning question, and the confirmed reversal that
 * keeps the original history). Every dialog is one temporary wizard
 * under the shared one-shot write protocol: nothing is recorded before
 * the final confirmation, success reads only after the server
 * confirmed the write, a lost response freezes the intent behind the
 * same `device_event_id`, and no context survives completion or
 * cancellation.
 */

function AreaChip({ area, children }: { area: AreaRef; children: ReactNode }) {
  return (
    <EntityChip>
      <AreaDot colorVar={areaRefColor(area)} />
      {children}
    </EntityChip>
  );
}

/* ------------------------------------------------------------------ */
/* Add more quantity                                                   */
/* ------------------------------------------------------------------ */

export function AddQuantityDialog({
  station,
  partNumber,
  hasMachines,
  operations,
  writeBlocked,
  onBack,
  onCancel,
  onDone,
  onRejected,
  onAbandonUnknown,
}: {
  station: StationContext;
  partNumber: string;
  /** The Area mode of the freshest server read: added quantity enters
   * the queue (Machines) or direct processing (no Machines). */
  hasMachines: boolean;
  /** The active Operations of the station's Area, just resolved. */
  operations: OperationRef[];
  writeBlocked: boolean;
  onBack?: () => void;
  onCancel: () => void;
  /** Called ONLY with a server-confirmed result. */
  onDone: (result: QuantityAdditionResult) => void;
  /** The server refused the write (nothing recorded); see useOneShotWrite. */
  onRejected?: () => void;
  onAbandonUnknown: () => void;
}) {
  const areaName = station.area.name;
  const [step, setStep] = useState<'entry' | 'confirm'>('entry');
  // Deliberately no MAX and no default (GUI_DESIGN §4.7).
  const [qty, setQty] = useState('');
  const [reason, setReason] = useState('');
  const parsedQty = parseInt(qty || '0', 10);
  // Several active Operations always take an explicit choice; a single
  // one resolves itself.
  const operationRequired = operations.length > 1;
  const [operationId, setOperationId] = useState<number | null>(
    operationRequired ? null : (operations[0]?.id ?? null),
  );
  const operation = operations.find((item) => item.id === operationId) ?? null;
  const valid =
    Number.isInteger(parsedQty) &&
    parsedQty >= 1 &&
    reason.trim() !== '' &&
    operation !== null;
  // The quantity confirmed on the entry step — frozen for the summary
  // and the request.
  const [confirmed, setConfirmed] = useState(0);
  const destination = hasMachines
    ? `${areaName} queue (awaiting Machine)`
    : `${areaName} — direct processing`;

  function goConfirm() {
    if (!valid) return;
    setConfirmed(parsedQty);
    setStep('confirm');
  }

  const write = useOneShotWrite<QuantityAdditionResult>({
    writeBlocked,
    onRejected,
    send: (deviceEventId) =>
      addQuantity({
        stationId: station.stationId,
        partNumber,
        quantity: confirmed,
        reason: reason.trim(),
        operationId: operation!.id,
        deviceEventId,
      }),
    onDone,
  });

  const cancel = write.outcomeUnknown ? onAbandonUnknown : onCancel;

  return (
    <ModalDialog
      label="Add more quantity"
      onClose={write.busy ? () => undefined : cancel}
      onKeyDown={
        step === 'entry'
          ? quantityKeyHandler(qty, setQty, goConfirm)
          : enterKeyHandler(() => void write.submit())
      }
    >
      <h3>Add more quantity</h3>
      {step === 'entry' ? (
        <div>
          <div className="big mono" title={partNumber}>
            {partNumber}
          </div>
          <div className="sub">
            Add physical quantity found at this Area that was not transferred
            from another Area. A reason is required.
          </div>
          <StepRecap
            lines={[
              <>
                Adding at <AreaChip area={station.area}>{areaName}</AreaChip> →{' '}
                <AreaChip area={station.area}>{destination}</AreaChip>
              </>,
            ]}
          />
          {operationRequired ? (
            <>
              <Guidance tone="action">
                {areaName} supports several Operations. Select the Operation the
                added quantity is here for.
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
          ) : operation ? (
            <StepRecap
              lines={[
                <>
                  Operation <EntityChip>{operationLabel(operation)}</EntityChip>
                </>,
              ]}
            />
          ) : (
            <Guidance tone="error">
              {areaName} has no active Operation configured. Configure one in
              Administration → Operations before adding quantity.
            </Guidance>
          )}
          <Guidance tone="info">
            Enter the actual quantity found. No default quantity is provided.
          </Guidance>
          {parsedQty < 1 ? (
            <Guidance tone="action">A positive quantity is required.</Guidance>
          ) : null}
          <QuantityKeypad value={qty} onChange={setQty} />
          <label className="ss-reasonlbl" htmlFor="addq-reason">
            Reason <span className="field-required">(required)</span>
          </label>
          <div className="ss-fieldhint">
            This reason will be included in the adjustment history.
          </div>
          <input
            id="addq-reason"
            className="field"
            autoComplete="off"
            value={reason}
            placeholder="e.g. found 2 additional blanks with the lot"
            onChange={(event) => setReason(event.target.value)}
          />
          <StepButtons
            onBack={onBack}
            onCancel={onCancel}
            primary={{ label: 'Next', onClick: goConfirm, disabled: !valid }}
          />
        </div>
      ) : (
        <div>
          <div className="big mono" title={partNumber}>
            {partNumber}
          </div>
          <div className="sub">Review the quantity addition, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'Add physical quantity', 'primary'],
              ['PN', <span className="mono">{partNumber}</span>, 'primary'],
              [
                'Quantity',
                <span className="mono">+{confirmed} pcs</span>,
                'primary',
              ],
              ['Area', <AreaChip area={station.area}>{areaName}</AreaChip>],
              [
                'Destination',
                <AreaChip area={station.area}>{destination}</AreaChip>,
                'primary',
              ],
              [
                'Operation',
                operation ? (
                  <EntityChip>{operationLabel(operation)}</EntityChip>
                ) : null,
              ],
              ['Reason', reason.trim(), 'primary'],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', 'QUANTITY_ADJUSTED · INCREASE', 'secondary'],
            ]}
          />
          <WriteGuidance
            outcomeUnknown={write.outcomeUnknown}
            serverError={write.serverError}
            writeBlocked={writeBlocked}
            what="addition"
          />
          <StepButtons
            onBack={
              write.outcomeUnknown || write.rejected
                ? undefined
                : () => {
                    write.clearError();
                    setStep('entry');
                  }
            }
            onCancel={cancel}
            cancelLabel={
              write.outcomeUnknown ? 'Leave — check the Area' : 'Cancel (Esc)'
            }
            primary={{
              label: write.outcomeUnknown
                ? 'Retry the same addition'
                : write.serverError
                  ? 'Retry addition'
                  : write.busy
                    ? 'Recording…'
                    : 'Confirm addition',
              onClick: () => void write.submit(),
              disabled: write.busy || writeBlocked,
              autoFocus: true,
            }}
          />
        </div>
      )}
    </ModalDialog>
  );
}

/* ------------------------------------------------------------------ */
/* Scrap damaged quantity — the PF:SCRAP counting workflow             */
/* ------------------------------------------------------------------ */

export function ScrapDialog({
  station,
  flow,
  machine,
  writeBlocked,
  onBack,
  onCancel,
  onDone,
  onRejected,
  onAbandonUnknown,
}: {
  station: StationContext;
  /** The ONE Quantity Flow the damaged quantity is scrapped from. */
  flow: FlowInArea;
  /** The Machine the flow is on (ON_MACHINE); null otherwise. */
  machine: MachineRef | null;
  writeBlocked: boolean;
  onBack?: () => void;
  onCancel: () => void;
  /** Called ONLY with a server-confirmed result. */
  onDone: (result: ScrapResult) => void;
  /** The server refused the write (nothing recorded); see useOneShotWrite. */
  onRejected?: () => void;
  onAbandonUnknown: () => void;
}) {
  const areaName = station.area.name;
  const pn = flow.partNumber;
  const available = flow.quantity;
  const [step, setStep] = useState<'count' | 'confirm'>('count');
  // The pending scrap counter — counting changes no production state.
  const [count, setCount] = useState(0);
  const [reason, setReason] = useState('');
  const [scanNote, setScanNote] = useState<string | null>(null);
  const scanRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (step === 'count') scanRef.current?.focus();
  }, [step]);
  const valid = count >= 1 && count <= available && reason.trim() !== '';
  // The total confirmed on the counting step — frozen for the summary
  // and the request.
  const [confirmed, setConfirmed] = useState(0);
  const partial = confirmed >= 1 && confirmed < available;
  // Where the quantity physically is — the remainder of a partial
  // scrap keeps exactly this place and state.
  const position = machine
    ? `on ${machine.name}`
    : flow.processingState === 'PROCESSING'
      ? 'in processing'
      : flow.processingState === 'READY_TO_TRANSFER'
        ? 'finished — ready to move'
        : 'in the Area queue';

  function handleScrapScan(value: string) {
    const parsed = parseScan(value);
    if (parsed.kind === 'empty') return;
    if (parsed.kind !== 'scrap') {
      setScanNote(
        `“${value.trim()}” is not a valid scrap barcode. Scan ${SCRAP_BARCODE} to add one piece.`,
      );
      return;
    }
    // Counting changes no production state — only the pending count.
    setCount((current) => current + 1);
    setScanNote(null);
  }

  function goConfirm() {
    if (!valid) return;
    setConfirmed(count);
    setStep('confirm');
  }

  const write = useOneShotWrite<ScrapResult>({
    writeBlocked,
    onRejected,
    send: (deviceEventId) =>
      scrapQuantity({
        stationId: station.stationId,
        partNumber: pn,
        quantityFlowId: flow.quantityFlowId,
        quantity: confirmed,
        reason: reason.trim(),
        deviceEventId,
      }),
    onDone,
  });

  const cancel = write.outcomeUnknown ? onAbandonUnknown : onCancel;

  const countKeys = (event: React.KeyboardEvent) => {
    const target = event.target;
    if (target instanceof HTMLButtonElement) return;
    if (
      target instanceof HTMLInputElement &&
      target.getAttribute('aria-label') === 'Scrap barcode input'
    ) {
      return; // the scrap counting input owns its Enter handling
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      goConfirm();
    }
  };

  return (
    <ModalDialog
      label="Scrap damaged quantity"
      onClose={write.busy ? () => undefined : cancel}
      size="wide"
      onKeyDown={
        step === 'count'
          ? countKeys
          : enterKeyHandler(() => void write.submit())
      }
    >
      <h3>Scrap damaged quantity</h3>
      {step === 'count' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          {/* Operator wording only (§3.10): the scrap barcode counts
              only inside this workflow, and the single auditable
              operation is created on the final confirmation — the
              canonical event name renders in the confirmation
              summary's Recorded event row, never in this instruction. */}
          <div className="sub">
            Scan <code>{SCRAP_BARCODE}</code> once for each damaged piece, then
            enter one reason for the total. Nothing is recorded until
            confirmation.
          </div>
          <StepRecap
            lines={[
              <>
                Scrapping from{' '}
                <AreaChip area={station.area}>{areaName}</AreaChip> —{' '}
                {flow.quantity} pcs {position}
              </>,
            ]}
          />
          <Guidance tone="info">
            Available: <b>{available} pcs</b> · pending scrap <b>{count}</b> ·
            remaining after scrap <b>{Math.max(0, available - count)} pcs</b>.
          </Guidance>
          {count > available ? (
            <Guidance tone="error">
              Scrap count cannot exceed the {available} pcs of this quantity.
            </Guidance>
          ) : null}
          <div className="ss-scrapcount" role="status">
            <span className="lbl">Pending scrap count</span>
            <span className="cnt mono">{count}</span>
            <button
              type="button"
              className="pickbtn"
              disabled={count === 0}
              onClick={() => setCount((current) => Math.max(0, current - 1))}
            >
              Remove one
            </button>
            <button
              type="button"
              className="pickbtn"
              disabled={count === 0}
              onClick={() => setCount(0)}
            >
              Reset
            </button>
          </div>
          <input
            ref={scanRef}
            className="field mono"
            placeholder={`Scan ${SCRAP_BARCODE} — Enter`}
            aria-label="Scrap barcode input"
            autoComplete="off"
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                event.stopPropagation();
                handleScrapScan(event.currentTarget.value);
                event.currentTarget.value = '';
              }
            }}
          />
          {scanNote ? <Guidance tone="error">{scanNote}</Guidance> : null}
          <label className="ss-reasonlbl" htmlFor="scrap-reason">
            Scrap reason <span className="field-required">(required)</span>
          </label>
          <input
            id="scrap-reason"
            className="field"
            autoComplete="off"
            value={reason}
            placeholder="e.g. tool crash — gouged face"
            onChange={(event) => setReason(event.target.value)}
          />
          <StepButtons
            onBack={onBack}
            onCancel={onCancel}
            primary={{ label: 'Next', onClick: goConfirm, disabled: !valid }}
          />
        </div>
      ) : (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">Review the scrap operation, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'Scrap damaged quantity', 'primary'],
              ['PN', <span className="mono">{pn}</span>, 'primary'],
              [
                'Area',
                <AreaChip area={station.area}>{areaName}</AreaChip>,
                'primary',
              ],
              [
                'Machine',
                machine ? <EntityChip>{machine.name}</EntityChip> : null,
                'primary',
              ],
              ['Available', <span className="mono">{available} pcs</span>],
              [
                'Scrap quantity',
                <span className="mono">{confirmed} pcs</span>,
                'primary',
                'err',
              ],
              [
                'Remaining active quantity',
                <span className="mono">
                  {available - confirmed} pcs
                  {partial ? ` — stays ${position}` : ''}
                </span>,
                partial ? 'primary' : undefined,
              ],
              ['Reason', reason.trim(), 'primary'],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', 'SCRAPPED', 'secondary'],
            ]}
          />
          <WriteGuidance
            outcomeUnknown={write.outcomeUnknown}
            serverError={write.serverError}
            writeBlocked={writeBlocked}
            what="scrap"
          />
          <StepButtons
            onBack={
              write.outcomeUnknown || write.rejected
                ? undefined
                : () => {
                    write.clearError();
                    setStep('count');
                  }
            }
            onCancel={cancel}
            cancelLabel={
              write.outcomeUnknown ? 'Leave — check the Area' : 'Cancel (Esc)'
            }
            primary={{
              label: write.outcomeUnknown
                ? 'Retry the same scrap'
                : write.serverError
                  ? 'Retry scrap'
                  : write.busy
                    ? 'Recording…'
                    : 'Confirm scrap',
              onClick: () => void write.submit(),
              disabled: write.busy || writeBlocked,
              autoFocus: true,
            }}
          />
        </div>
      )}
    </ModalDialog>
  );
}

/* ------------------------------------------------------------------ */
/* Undo — the structured reversal summary and final warning question   */
/* ------------------------------------------------------------------ */

/** Operator wording for a restored holding state. `machineLabel` is
 * the resolved Machine identity — never lost: a Machine outside the
 * station's Area (a cross-Area reversal) falls back to its explicit
 * `Machine #<id>` label instead of a vague placeholder. */
function restoredStateText(
  state: FlowInArea['processingState'] | null,
  machineLabel: string | null,
): string {
  switch (state) {
    case 'QUEUED':
      return 'queued — awaiting Machine';
    case 'PROCESSING':
      return 'in processing';
    case 'ON_MACHINE':
      return machineLabel ? `on ${machineLabel}` : 'on its Machine';
    case 'READY_TO_TRANSFER':
      return 'finished — ready to move';
    default:
      return '';
  }
}

export function UndoDialog({
  station,
  preview,
  machines,
  writeBlocked,
  onCancel,
  onDone,
  onRejected,
  onAbandonUnknown,
}: {
  station: StationContext;
  /** The server's summary of the command the reversal would undo. */
  preview: UndoPreview;
  /** The active Machines of the station's Area (to name Machines). */
  machines: MachineRef[];
  onCancel: () => void;
  writeBlocked: boolean;
  /** Called ONLY with a server-confirmed result. */
  onDone: (result: UndoResult) => void;
  /** The server refused the write (nothing recorded); see useOneShotWrite. */
  onRejected?: () => void;
  onAbandonUnknown: () => void;
}) {
  const pn = preview.partNumber;
  // Final-confirmation gate (GUI_DESIGN §4.5/§4.6, post-v18): the
  // summary's `Confirm reversal` opens the warning-toned final
  // question before anything is reversed. The Worker badge variant
  // arrives with the Worker-session workflows.
  const [gate, setGate] = useState(false);

  const write = useOneShotWrite<UndoResult>({
    writeBlocked,
    onRejected,
    send: (deviceEventId) =>
      undoProductionCommand({
        stationId: station.stationId,
        partNumber: pn,
        reversesDeviceEventId: preview.reversesDeviceEventId,
        deviceEventId,
      }),
    onDone,
  });

  function requestConfirm() {
    if (write.busy || writeBlocked) return;
    if (write.outcomeUnknown || write.serverError) {
      // The intent was already confirmed through the gate: a retry
      // resends the SAME request without asking the question again.
      void write.submit();
      return;
    }
    setGate(true);
  }

  const cancel = write.outcomeUnknown ? onAbandonUnknown : onCancel;
  // Machine identity is never dropped: a Machine of the station's Area
  // resolves to its name; one of another Area (a cross-Area command —
  // e.g. the source Machine of an implicit-completion transfer) keeps
  // an explicit `Machine #<id>` fallback.
  const machineLabelOf = (id: number | null) =>
    id === null
      ? null
      : (machines.find((item) => item.id === id)?.name ?? `Machine #${id}`);
  // The original action as recorded, in command order — audit data.
  const repair = preview.movements.some(
    (item) => item.movementReason === 'REPAIR',
  );
  const actionText =
    preview.movements.map((item) => item.movementType).join(' + ') +
    (repair ? ' · REPAIR intent' : '');
  const firstMovement = preview.movements[0];
  const lastMovement = preview.movements[preview.movements.length - 1];
  const crossArea =
    firstMovement !== undefined &&
    firstMovement.fromArea !== null &&
    firstMovement.fromArea.id !== lastMovement.toArea.id;
  const actionMachineLabel = machineLabelOf(
    preview.movements.find((item) => item.machineId !== null)?.machineId ??
      null,
  );
  // The effect of the reversal per involved flow — the server's plan.
  const effects = preview.restored.map((item) => {
    if (item.status !== 'ACTIVE') {
      return `The ${item.quantity} pcs this action introduced leave active quantity.`;
    }
    const where = item.area ? item.area.name : 'its previous Area';
    const state = restoredStateText(
      item.processingState,
      machineLabelOf(item.machineId),
    );
    return `${item.quantity} pcs return to ${where}${state ? ` — ${state}` : ''}.`;
  });
  const gateInfo = (
    <>
      Are you sure you want to reverse <b>{actionText}</b> —{' '}
      <b>{preview.quantity} pcs</b> of <b className="mono">{pn}</b>?{' '}
      {effects.join(' ')} The original history stays recorded for audit.
    </>
  );

  return (
    <>
      <ModalDialog
        label="Reverse the last Part Number action?"
        onClose={write.busy ? () => undefined : cancel}
        onKeyDown={enterKeyHandler(requestConfirm)}
      >
        <h3>Reverse the last Part Number action?</h3>
        <div className="big mono" title={pn}>
          {pn}
        </div>
        <div className="sub">
          This will reverse the complete last action. The original history will
          remain available for audit.
        </div>
        <ConfirmationSummary
          rows={[
            ['Original action', actionText, 'primary'],
            ['PN', <span className="mono">{pn}</span>, 'primary'],
            [
              'Quantity',
              <span className="mono">{preview.quantity} pcs</span>,
              'primary',
            ],
            crossArea
              ? [
                  'Source → destination',
                  <>
                    <AreaChip area={firstMovement.fromArea!}>
                      {firstMovement.fromArea!.name}
                    </AreaChip>{' '}
                    →{' '}
                    <AreaChip area={lastMovement.toArea}>
                      {lastMovement.toArea.name}
                    </AreaChip>
                  </>,
                  'primary',
                ]
              : [
                  'Area',
                  lastMovement ? (
                    <AreaChip area={lastMovement.toArea}>
                      {lastMovement.toArea.name}
                    </AreaChip>
                  ) : null,
                  'primary',
                ],
            [
              'Machine',
              actionMachineLabel ? (
                <EntityChip>{actionMachineLabel}</EntityChip>
              ) : null,
              'primary',
            ],
            [
              'Recorded',
              <span className="mono">
                {new Date(preview.occurredAt).toLocaleString()}
              </span>,
              'secondary',
            ],
            ['Scan Station', station.stationId, 'secondary'],
            [
              'Result after reversal',
              preview.eligible ? effects.join(' ') : null,
              'primary',
              'warn',
            ],
          ]}
        />
        {preview.eligible ? (
          <>
            <Guidance tone="info">
              The original history stays recorded for audit — the reversal is
              recorded as its own new event.
            </Guidance>
            <WriteGuidance
              outcomeUnknown={write.outcomeUnknown}
              serverError={write.serverError}
              writeBlocked={writeBlocked}
              what="reversal"
            />
            <StepButtons
              onCancel={cancel}
              cancelLabel={
                write.outcomeUnknown ? 'Leave — check the Area' : 'Cancel (Esc)'
              }
              primary={{
                label: write.outcomeUnknown
                  ? 'Retry the same reversal'
                  : write.serverError
                    ? 'Retry reversal'
                    : write.busy
                      ? 'Recording…'
                      : 'Confirm reversal',
                onClick: requestConfirm,
                disabled: write.busy || writeBlocked,
                danger: true,
              }}
            />
          </>
        ) : (
          <>
            <Guidance tone="error">{preview.ineligibleReason}</Guidance>
            <div className="row">
              <button className="bigbtn ghost" onClick={onCancel}>
                Cancel (Esc)
              </button>
            </div>
          </>
        )}
      </ModalDialog>
      {gate ? (
        <ConfirmDialog
          title="Reverse this action?"
          tone="warning"
          confirmLabel="Yes — reverse it"
          cancelLabel="Cancel (Esc)"
          confirmDisabled={writeBlocked}
          onConfirm={() => {
            setGate(false);
            void write.submit();
          }}
          onCancel={() => setGate(false)}
        >
          {gateInfo}
        </ConfirmDialog>
      ) : null}
    </>
  );
}
