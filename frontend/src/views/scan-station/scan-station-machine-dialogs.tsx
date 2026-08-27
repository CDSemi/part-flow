import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { errorMessage } from '../../api/client';
import {
  areaRefColor,
  recordMachineAction,
  resolveMachineScan,
} from '../../api/scan-station';
import type {
  AreaRef,
  FlowInArea,
  MachineActionKind,
  MachineActionResult,
  MachineRef,
  MachineScanResolution,
  OperationRef,
  StationContext,
} from '../../api/scan-station';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { QuantityKeypad } from '../../components/QuantityKeypad';
import { parseScan } from './barcode';
import {
  ConfirmationSummary,
  EntityChip,
  Guidance,
  StepButtons,
  StepRecap,
} from './scan-station-presentation';
import { useOneShotWrite } from './scan-station-write';
import {
  enterKeyHandler,
  operationLabel,
  quantityKeyHandler,
} from './scan-station-wizard';

/**
 * The Phase 6 one-shot Machine-Area dialogs of the REAL Scan Station
 * (GUI_DESIGN §4.6; PROJECT_PROFILE §12, §15): `Assign to Machine`
 * (Machine-first from a Machine scan with the Machine preselected, or
 * PN-first from a queued row with the PN preselected), and the two
 * distinct Machine-card actions `Complete Area processing` (DONE) and
 * `Return to Area queue` (QUEUE). Every dialog is one temporary wizard:
 * nothing is recorded before the final confirmation, the server
 * confirms every write before it reads as success, and no Machine or
 * PN context survives completion or cancellation.
 */

const MACHINE_STATE_TEXT: Record<MachineRef['operationalState'], string> = {
  RUNNING: 'running',
  IDLE: 'idle',
  MAINTENANCE: 'maintenance',
};

function AreaChip({ area, children }: { area: AreaRef; children: ReactNode }) {
  return (
    <EntityChip>
      <AreaDot colorVar={areaRefColor(area)} />
      {children}
    </EntityChip>
  );
}

/** The recorded-event names of the three in-Area commands. */
const RECORDED_EVENT: Record<MachineActionKind, string> = {
  ASSIGN: 'ASSIGNED_TO_MACHINE',
  QUEUE: 'RELEASED_FROM_MACHINE',
  DONE: 'AREA_COMPLETED',
};

/** Whole-flow-only guidance (SPLIT arrives with a later release). */
function fullQuantityGuidance(
  parsed: number,
  max: number,
  where: string,
  action: string,
): ReactNode {
  if (parsed > max) {
    return (
      <Guidance tone="error">
        Quantity cannot exceed the {max} pcs currently {where}.
      </Guidance>
    );
  }
  if (parsed < max) {
    return (
      <Guidance tone="error">
        Partial {action} is not available in this release: this quantity of{' '}
        {max} pcs is handled as a whole. Enter {max} or cancel — nothing is
        recorded.
      </Guidance>
    );
  }
  return null;
}

/** The shared unknown-outcome / rejection / offline guidance block. */
function WriteGuidance({
  outcomeUnknown,
  serverError,
  writeBlocked,
  what,
}: {
  outcomeUnknown: boolean;
  serverError: string | null;
  writeBlocked: boolean;
  what: string;
}) {
  return (
    <>
      {outcomeUnknown ? (
        <Guidance tone="warn">
          The server did not answer — this {what} may or may not have been
          recorded. Retry the exact same {what} to find out: the server answers
          with the recorded result, or records it once. Nothing can be changed
          until then.
        </Guidance>
      ) : null}
      {serverError ? <Guidance tone="error">{serverError}</Guidance> : null}
      {writeBlocked && !serverError && !outcomeUnknown ? (
        <Guidance tone="error">
          Disconnected — the {what} cannot be recorded until the connection
          returns.
        </Guidance>
      ) : null}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Assign to Machine                                                   */
/* ------------------------------------------------------------------ */

export function AssignToMachineDialog({
  station,
  machines,
  queued,
  preselectedMachineId,
  preselectedFlow,
  writeBlocked,
  onBack,
  onCancel,
  onDone,
  onRejected,
  onAbandonUnknown,
}: {
  station: StationContext;
  /** The active Machines of the station's Area (Machine cards). */
  machines: MachineRef[];
  /** The QUEUED flows of the Area — every one an explicit choice. */
  queued: FlowInArea[];
  /** Machine-first: the scanned Machine. */
  preselectedMachineId?: number;
  /** PN-first: the queued flow the action was taken on. */
  preselectedFlow?: FlowInArea;
  writeBlocked: boolean;
  onBack?: () => void;
  onCancel: () => void;
  /** Called ONLY with a server-confirmed result. */
  onDone: (
    result: MachineActionResult,
    machine: MachineRef,
    flow: FlowInArea,
  ) => void;
  /** The server refused the write (nothing recorded); see useOneShotWrite. */
  onRejected?: () => void;
  onAbandonUnknown: () => void;
}) {
  const [machineList, setMachineList] = useState(machines);
  // PN-first: the flow the action was taken on comes from the fresh PN
  // resolution while the queued list comes from the last inventory read,
  // which may predate the flow entering the queue. The preselected flow
  // is always an explicit choice of this dialog.
  const [queuedList, setQueuedList] = useState(() =>
    preselectedFlow === undefined ||
    queued.some(
      (item) => item.quantityFlowId === preselectedFlow.quantityFlowId,
    )
      ? queued
      : [preselectedFlow, ...queued],
  );
  const [machineId, setMachineId] = useState<number | null>(
    preselectedMachineId ?? null,
  );
  const [flowId, setFlowId] = useState<number | null>(
    preselectedFlow?.quantityFlowId ?? null,
  );
  const [step, setStep] = useState<'select' | 'qty' | 'confirm'>('select');
  const [scanError, setScanError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const scanRef = useRef<HTMLInputElement>(null);
  const machine = machineList.find((item) => item.id === machineId) ?? null;
  const flow =
    queuedList.find((item) => item.quantityFlowId === flowId) ?? null;
  const pairSelected = machine !== null && flow !== null;

  useEffect(() => {
    if (step === 'select') scanRef.current?.focus();
  }, [step]);

  const [qty, setQty] = useState('');
  const max = flow?.quantity ?? 0;
  const parsedQty = parseInt(qty || '0', 10);
  const fullQuantity = flow !== null && parsedQty === max;

  function goQty() {
    if (!pairSelected) return;
    setQty(String(flow.quantity));
    setStep('qty');
  }
  function goConfirm() {
    if (fullQuantity) setStep('confirm');
  }

  const write = useOneShotWrite<MachineActionResult>({
    writeBlocked,
    onRejected,
    send: (deviceEventId) =>
      recordMachineAction('ASSIGN', {
        stationId: station.stationId,
        partNumber: flow!.partNumber,
        quantityFlowId: flow!.quantityFlowId,
        machineId: machine!.id,
        quantity: flow!.quantity,
        deviceEventId,
      }),
    onDone: useCallback(
      (result: MachineActionResult) => onDone(result, machine!, flow!),
      [onDone, machine, flow],
    ),
  });

  /**
   * The dialog's own barcode input: a Machine barcode of the station's
   * Area selects (and re-validates) the Machine, a PN barcode selects
   * the queued flow of that PN. A refused scan is reported in place and
   * never discards the selections already made.
   */
  async function handleSelectScan(raw: string) {
    const parsed = parseScan(raw);
    if (parsed.kind === 'empty') return;
    if (parsed.kind === 'machine') {
      setScanning(true);
      try {
        const resolution: MachineScanResolution = await resolveMachineScan(
          station.stationId,
          { barcode: raw.trim() },
        );
        setScanError(null);
        setMachineList((list) =>
          list.some((item) => item.id === resolution.machine.id)
            ? list.map((item) =>
                item.id === resolution.machine.id ? resolution.machine : item,
              )
            : [...list, resolution.machine],
        );
        setQueuedList(resolution.queued);
        setMachineId(resolution.machine.id);
        if (
          flowId !== null &&
          !resolution.queued.some((item) => item.quantityFlowId === flowId)
        ) {
          // The selected quantity is no longer queued (assigned or
          // moved meanwhile): the choice must be made again.
          setFlowId(null);
          setScanError(
            'The selected Part Number is no longer queued in this Area. Select a queued Part Number again.',
          );
        }
      } catch (error) {
        setScanError(`${errorMessage(error)} Nothing was changed.`);
      } finally {
        setScanning(false);
        scanRef.current?.focus();
      }
      return;
    }
    if (parsed.kind === 'pn') {
      const matches = queuedList.filter(
        (item) => item.partNumber === parsed.pn,
      );
      if (matches.length === 1) {
        setFlowId(matches[0].quantityFlowId);
        setScanError(null);
      } else if (matches.length === 0) {
        setScanError(
          `${parsed.pn} has no queued quantity in ${station.area.name}. Scan a queued Part Number or a Machine of this Area.`,
        );
      } else {
        setScanError(
          `${parsed.pn} is queued as ${matches.length} separate quantities. Select exactly one below — quantities are never combined.`,
        );
      }
      return;
    }
    setScanError(
      'Barcode not valid for this step. Scan a Machine in this Area or a PN currently waiting in the Area queue. Your current selections were not changed.',
    );
  }

  const cancel = write.outcomeUnknown ? onAbandonUnknown : onCancel;
  const keys =
    step === 'qty'
      ? quantityKeyHandler(qty, setQty, goConfirm)
      : step === 'confirm'
        ? enterKeyHandler(() => void write.submit())
        : enterKeyHandler(goQty);

  return (
    <ModalDialog
      label="Assign to Machine"
      onClose={write.busy ? () => undefined : cancel}
      size="wide"
      onKeyDown={keys}
    >
      <h3>Assign to Machine</h3>
      {step === 'select' ? (
        <>
          <div className="sub">
            Select a Machine and a queued Part Number, then review the
            assignment. This assignment applies once and closes after
            confirmation.
          </div>
          <input
            ref={scanRef}
            className="field mono"
            autoComplete="off"
            disabled={scanning}
            placeholder="Scan Machine or queued PN barcode… (ENTER)"
            aria-label="Scan Machine or queued PN barcode"
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return;
              // One Enter has exactly one meaning: a filled input is a
              // selection scan, an empty input advances once the
              // Machine + PN pair is selected.
              e.preventDefault();
              e.stopPropagation();
              const value = e.currentTarget.value;
              if (value) {
                e.currentTarget.value = '';
                void handleSelectScan(value);
                return;
              }
              goQty();
            }}
          />
          {scanError ? <Guidance tone="error">{scanError}</Guidance> : null}
          <div className="ss-dlgrid">
            <span className="lbl" id="ma-machine-lbl">
              Machine
            </span>
            <div
              className="ss-choicerow"
              role="group"
              aria-labelledby="ma-machine-lbl"
            >
              {machineList.map((item) => {
                const unavailable = item.operationalState === 'MAINTENANCE';
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={`pickbtn ${machineId === item.id ? 'sel' : ''}`}
                    aria-pressed={machineId === item.id}
                    disabled={unavailable}
                    title={
                      unavailable
                        ? 'Under maintenance · Unavailable for production'
                        : undefined
                    }
                    onClick={() => setMachineId(item.id)}
                  >
                    {item.name}
                    <span className="s">
                      {MACHINE_STATE_TEXT[item.operationalState]}
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="ss-dlgsep" aria-hidden="true" />
            <span className="lbl" id="ma-pn-lbl">
              PN <span className="field-note">(queued)</span>
            </span>
            {queuedList.length === 0 ? (
              <span className="sub">
                No queued quantity in {station.area.name}.
              </span>
            ) : (
              <div
                className="ss-choicerow"
                role="group"
                aria-labelledby="ma-pn-lbl"
              >
                {queuedList.map((item) => (
                  <button
                    key={item.quantityFlowId}
                    type="button"
                    className={`pickbtn mono ${flowId === item.quantityFlowId ? 'sel' : ''}`}
                    aria-pressed={flowId === item.quantityFlowId}
                    onClick={() => setFlowId(item.quantityFlowId)}
                  >
                    <span className="pickpn" title={item.partNumber}>
                      {item.partNumber}
                    </span>
                    <span className="s">queued {item.quantity}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <StepButtons
            onBack={onBack}
            onCancel={onCancel}
            primary={{ label: 'Next', onClick: goQty, disabled: !pairSelected }}
          />
        </>
      ) : null}
      {step === 'qty' && flow && machine ? (
        <>
          <div className="big mono" title={flow.partNumber}>
            {flow.partNumber}
          </div>
          <StepRecap
            lines={[
              <>
                Assigning to <EntityChip>{machine.name}</EntityChip>. Queued
                quantity: <b>{flow.quantity} pcs</b>.
              </>,
              <>
                Source: <AreaChip area={station.area}>Area queue</AreaChip> →
                Destination: <EntityChip>{machine.name}</EntityChip>
              </>,
            ]}
          />
          <Guidance tone="info">
            The full queued quantity moves to the Machine as a whole.
          </Guidance>
          {fullQuantityGuidance(
            parsedQty,
            flow.quantity,
            'queued for this Part Number',
            'assignment',
          )}
          <QuantityKeypad value={qty} onChange={setQty} max={flow.quantity} />
          <StepButtons
            onBack={() => setStep('select')}
            onCancel={onCancel}
            primary={{
              label: 'Next',
              onClick: goConfirm,
              disabled: !fullQuantity,
            }}
          />
        </>
      ) : null}
      {step === 'confirm' && flow && machine ? (
        <>
          <div className="big mono" title={flow.partNumber}>
            {flow.partNumber}
          </div>
          <div className="sub">Review the assignment, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'Assign to Machine', 'primary'],
              [
                'PN',
                <span className="mono">{flow.partNumber}</span>,
                'primary',
              ],
              [
                'Quantity',
                <span className="mono">{flow.quantity} pcs</span>,
                'primary',
              ],
              [
                'Source',
                <AreaChip area={station.area}>Area queue</AreaChip>,
                'primary',
              ],
              [
                'Destination Machine',
                <EntityChip>{machine.name}</EntityChip>,
                'primary',
              ],
              [
                'Remaining queued after assignment',
                <span className="mono">0 pcs</span>,
              ],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', RECORDED_EVENT.ASSIGN, 'secondary'],
            ]}
          />
          <WriteGuidance
            outcomeUnknown={write.outcomeUnknown}
            serverError={write.serverError}
            writeBlocked={writeBlocked}
            what="assignment"
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
                ? 'Retry the same assignment'
                : write.serverError
                  ? 'Retry assignment'
                  : write.busy
                    ? 'Recording…'
                    : 'Confirm assignment',
              onClick: () => void write.submit(),
              disabled: write.busy || writeBlocked,
              autoFocus: true,
            }}
          />
        </>
      ) : null}
    </ModalDialog>
  );
}

/* ------------------------------------------------------------------ */
/* DONE / QUEUE — the two distinct Machine-card actions, and the       */
/* direct-processing DONE (Phase 7): the same wizard without a Machine */
/* ------------------------------------------------------------------ */

export function MachineActionDialog({
  kind,
  station,
  flow,
  machine,
  operation,
  writeBlocked,
  onBack,
  onCancel,
  onDone,
  onRejected,
  onAbandonUnknown,
}: {
  kind: 'DONE' | 'QUEUE';
  station: StationContext;
  /** The ON_MACHINE flow the row action was taken on — or, with
   * `machine: null`, the PROCESSING flow of an Area without Machines. */
  flow: FlowInArea;
  /** The Machine the quantity is on; null for the direct-processing
   * DONE of an Area without Machines (GUI_DESIGN §4.6 exception) —
   * the wizard then renders no Machine field and the server records an
   * `AREA_COMPLETED` without a Machine. QUEUE always names a Machine. */
  machine: MachineRef | null;
  /** The Operation the quantity is in the Area for (the flow's
   * recorded Operation), named on the direct-processing summary. */
  operation: OperationRef | null;
  writeBlocked: boolean;
  onBack?: () => void;
  onCancel: () => void;
  /** Called ONLY with a server-confirmed result. */
  onDone: (result: MachineActionResult) => void;
  /** The server refused the write (nothing recorded); see useOneShotWrite. */
  onRejected?: () => void;
  onAbandonUnknown: () => void;
}) {
  const areaName = station.area.name;
  const pn = flow.partNumber;
  const max = flow.quantity;
  const [step, setStep] = useState<'qty' | 'confirm'>('qty');
  const [qty, setQty] = useState(String(max));
  const parsedQty = parseInt(qty || '0', 10);
  const fullQuantity = parsedQty === max;
  // Final-confirmation gate (GUI_DESIGN §4.6, post-v18): the summary's
  // primary opens ONE more question — DONE in the information tone,
  // QUEUE in the warning tone — before anything is recorded. Worker
  // badge gates arrive with the Worker-session workflows.
  const [gate, setGate] = useState(false);

  const write = useOneShotWrite<MachineActionResult>({
    writeBlocked,
    onRejected,
    send: (deviceEventId) =>
      recordMachineAction(kind, {
        stationId: station.stationId,
        partNumber: pn,
        quantityFlowId: flow.quantityFlowId,
        machineId: machine?.id ?? null,
        quantity: max,
        deviceEventId,
      }),
    onDone,
  });

  function goConfirm() {
    if (fullQuantity) setStep('confirm');
  }
  function requestConfirm() {
    if (!fullQuantity || write.busy || writeBlocked) return;
    if (write.outcomeUnknown || write.serverError) {
      // The intent was already confirmed through the gate: a retry
      // resends the SAME request without asking the question again.
      void write.submit();
      return;
    }
    setGate(true);
  }

  const isDone = kind === 'DONE';
  const title = isDone
    ? 'Complete Area processing'
    : 'Return unfinished quantity to queue';
  const what = isDone ? 'completion' : 'queue return';
  const cancel = write.outcomeUnknown ? onAbandonUnknown : onCancel;
  // Where the quantity is now: on the Machine, or in the Area's own
  // direct processing (no Machine).
  const position = machine ? machine.name : `${areaName} processing`;
  const gateInfo = isDone ? (
    <>
      Are you sure{' '}
      {machine ? (
        <>
          <b>{machine.name}</b> has finished
        </>
      ) : (
        <>
          <b>{areaName}</b> has finished processing
        </>
      )}{' '}
      <b>{max} pcs</b> of <b className="mono">{pn}</b>? The finished quantity
      moves to the {areaName} finished rack, ready to transfer.
    </>
  ) : (
    <>
      Are you sure you want to return <b>{max} pcs</b> of{' '}
      <b className="mono">{pn}</b> running on <b>{position}</b> back to the{' '}
      {areaName} queue? The quantity stays unfinished.
    </>
  );

  return (
    <>
      <ModalDialog
        label={title}
        onClose={write.busy ? () => undefined : cancel}
        onKeyDown={
          step === 'qty'
            ? quantityKeyHandler(qty, setQty, goConfirm)
            : enterKeyHandler(requestConfirm)
        }
      >
        <h3>{title}</h3>
        {step === 'qty' ? (
          <div>
            <div className="big mono" title={pn}>
              {pn}
            </div>
            <StepRecap
              lines={[
                <>
                  <EntityChip>{position}</EntityChip> →{' '}
                  <EntityChip>
                    {areaName} {isDone ? 'finished rack' : 'queue'}
                  </EntityChip>
                </>,
              ]}
            />
            <Guidance tone="info">
              <b>{max} pcs</b> are{' '}
              {machine ? `on ${machine.name}` : `in processing at ${areaName}`}.
              The full quantity {isDone ? 'completes' : 'returns to the queue'}{' '}
              as a whole.
            </Guidance>
            {fullQuantityGuidance(
              parsedQty,
              max,
              machine ? `on ${machine.name}` : `in processing at ${areaName}`,
              what,
            )}
            <QuantityKeypad value={qty} onChange={setQty} max={max} />
            <StepButtons
              onBack={onBack}
              onCancel={onCancel}
              primary={{
                label: 'Next',
                onClick: goConfirm,
                disabled: !fullQuantity,
              }}
            />
          </div>
        ) : (
          <div>
            <div className="big mono" title={pn}>
              {pn}
            </div>
            <div className="sub">
              {isDone
                ? `Confirm the completed quantity. It will remain on the ${areaName} finished rack until transferred.`
                : `Confirm the return. The quantity goes back to the ${areaName} queue and stays unfinished.`}
            </div>
            <ConfirmationSummary
              rows={[
                ['Action', title, 'primary'],
                ['PN', <span className="mono">{pn}</span>, 'primary'],
                [
                  'Quantity',
                  <span className="mono">{max} pcs</span>,
                  'primary',
                ],
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
                [
                  'Operation',
                  machine === null && operation ? (
                    <EntityChip>{operationLabel(operation)}</EntityChip>
                  ) : null,
                  'primary',
                ],
                [
                  'Result',
                  isDone
                    ? 'Finished — ready to move'
                    : 'Queued — awaiting Machine',
                  'primary',
                  isDone ? 'ok' : 'warn',
                ],
                ['Scan Station', station.stationId, 'secondary'],
                ['Recorded event', RECORDED_EVENT[kind], 'secondary'],
              ]}
            />
            <WriteGuidance
              outcomeUnknown={write.outcomeUnknown}
              serverError={write.serverError}
              writeBlocked={writeBlocked}
              what={what}
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
                  ? `Retry the same ${what}`
                  : write.serverError
                    ? `Retry ${what}`
                    : write.busy
                      ? 'Recording…'
                      : isDone
                        ? 'Confirm completion'
                        : 'Confirm return to queue',
                onClick: requestConfirm,
                disabled: write.busy || writeBlocked,
                autoFocus: true,
              }}
            />
          </div>
        )}
      </ModalDialog>
      {gate ? (
        <ConfirmDialog
          title={
            isDone
              ? 'Confirm finished quantity?'
              : 'Return unfinished quantity?'
          }
          tone={isDone ? 'info' : 'warning'}
          confirmLabel={isDone ? 'Yes — finished' : 'Yes — return to queue'}
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
