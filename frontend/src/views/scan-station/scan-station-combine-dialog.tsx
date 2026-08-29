import { useCallback, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { areaRefColor, combineQuantities } from '../../api/scan-station';
import type {
  AreaRef,
  CombineResult,
  FlowInArea,
  MachineRef,
  StationContext,
} from '../../api/scan-station';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import {
  ConfirmationSummary,
  EntityChip,
  Guidance,
  StepButtons,
} from './scan-station-presentation';
import { useOneShotWrite } from './scan-station-write';
import {
  enterKeyHandler,
  operationLabel,
  portionLabel,
  portionState,
} from './scan-station-wizard';

/**
 * `Combine quantities` (Phase 8 — GUI_DESIGN §4.7 item 3): the
 * operator-facing workflow of the backend's MERGED command. The
 * station offers it only for a group of quantities the SERVER reported
 * combinable (`ScanResolution.combineGroups` — one identical production
 * context per group); the dialog lets the operator select at least two
 * of those portions, previews the resulting quantity, and records the
 * combine after a dedicated confirmation. Nothing is ever combined
 * automatically, the client judges no compatibility of its own, and
 * the server re-judges the selection under its locks before writing.
 * Whole portions only — a part of a portion is never combined here.
 */

function AreaChip({ area, children }: { area: AreaRef; children: ReactNode }) {
  return (
    <EntityChip>
      <AreaDot colorVar={areaRefColor(area)} />
      {children}
    </EntityChip>
  );
}

export function CombineQuantitiesDialog({
  station,
  partNumber,
  portions,
  machines,
  writeBlocked,
  onBack,
  onCancel,
  onDone,
  onRejected,
  onAbandonUnknown,
}: {
  station: StationContext;
  partNumber: string;
  /** ONE server-reported combinable group, in resolution order. */
  portions: FlowInArea[];
  /** The active Machines of the station's Area (to name `On {Machine}`). */
  machines: MachineRef[];
  writeBlocked: boolean;
  onBack?: () => void;
  onCancel: () => void;
  /** Called ONLY with a server-confirmed result. */
  onDone: (result: CombineResult, selected: FlowInArea[]) => void;
  /** The server refused the write (nothing recorded); see useOneShotWrite. */
  onRejected?: () => void;
  onAbandonUnknown: () => void;
}) {
  const [step, setStep] = useState<'select' | 'confirm'>('select');
  // Every portion of the group starts selected — the operator narrows
  // the selection; at least two portions make a combine.
  const [selectedIds, setSelectedIds] = useState<number[]>(() =>
    portions.map((flow) => flow.quantityFlowId),
  );
  const selected = portions.filter((flow) =>
    selectedIds.includes(flow.quantityFlowId),
  );
  const enough = selected.length >= 2;
  const total = selected.reduce((sum, flow) => sum + flow.quantity, 0);
  // Every portion of a server-reported group shares one context: the
  // first selected portion names it for the preview and the summary.
  const context = selected[0] ?? portions[0];
  const machine =
    machines.find((item) => item.id === context.machineId) ?? null;
  // The portions the confirmed request names — read at completion
  // without re-binding the write hook to every selection change.
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  function toggle(flowId: number) {
    setSelectedIds((ids) =>
      ids.includes(flowId)
        ? ids.filter((id) => id !== flowId)
        : [...ids, flowId],
    );
  }
  function goConfirm() {
    if (enough) setStep('confirm');
  }

  const write = useOneShotWrite<CombineResult>({
    writeBlocked,
    onRejected,
    send: (deviceEventId) =>
      combineQuantities({
        stationId: station.stationId,
        partNumber,
        quantityFlowIds: selected.map((flow) => flow.quantityFlowId),
        deviceEventId,
      }),
    onDone: useCallback(
      (result: CombineResult) => onDone(result, selectedRef.current),
      [onDone],
    ),
  });

  const cancel = write.outcomeUnknown ? onAbandonUnknown : onCancel;
  const stateText = portionState(context, machines);
  const preview = `${total} pcs · ${stateText} · ${operationLabel(context.operation)}`;
  const parts = selected.map((flow) => `${flow.quantity} pcs`).join(' + ');

  return (
    <ModalDialog
      label="Combine quantities"
      onClose={write.busy ? () => undefined : cancel}
      onKeyDown={enterKeyHandler(
        step === 'select' ? goConfirm : () => void write.submit(),
      )}
    >
      <h3>Combine quantities</h3>
      {step === 'select' ? (
        <div>
          <div className="big mono" title={partNumber}>
            {partNumber}
          </div>
          <div className="sub">
            These separate quantities of this Part Number are in{' '}
            {station.area.name} in the same state. Select at least two to
            combine into one quantity. Nothing is combined until you confirm.
          </div>
          <div
            className="ss-choices"
            role="group"
            aria-label="Quantities to combine"
          >
            {portions.map((flow) => {
              const on = selectedIds.includes(flow.quantityFlowId);
              return (
                <button
                  key={flow.quantityFlowId}
                  type="button"
                  className={`choice${on ? ' selected' : ''}`}
                  aria-pressed={on}
                  onClick={() => toggle(flow.quantityFlowId)}
                >
                  <span className="cic" aria-hidden="true">
                    {on ? '✓' : ''}
                  </span>
                  <span>
                    <span className="ct1">{portionLabel(flow, machines)}</span>
                  </span>
                </button>
              );
            })}
          </div>
          {enough ? (
            <Guidance tone="info">
              Result: <b>{preview}</b> ({parts}).
            </Guidance>
          ) : (
            <Guidance tone="action">
              Select at least two quantities to combine.
            </Guidance>
          )}
          <StepButtons
            onBack={onBack}
            onCancel={onCancel}
            primary={{ label: 'Next', onClick: goConfirm, disabled: !enough }}
          />
        </div>
      ) : (
        <div>
          <div className="big mono" title={partNumber}>
            {partNumber}
          </div>
          <div className="sub">
            Review the combine, then confirm. The selected quantities become one
            quantity in {station.area.name}; their history is kept.
          </div>
          <ConfirmationSummary
            rows={[
              ['Action', 'Combine quantities', 'primary'],
              ['PN', <span className="mono">{partNumber}</span>, 'primary'],
              [
                'Area',
                <AreaChip area={station.area}>{station.area.name}</AreaChip>,
                'primary',
              ],
              [
                'Selected quantities',
                <span className="mono">{parts}</span>,
                'primary',
              ],
              [
                'Resulting quantity',
                <span className="mono">
                  {parts} → {total} pcs
                </span>,
                'primary',
              ],
              ['State', stateText, 'primary'],
              [
                'Machine',
                machine ? <EntityChip>{machine.name}</EntityChip> : null,
                'primary',
              ],
              [
                'Operation',
                <EntityChip>{operationLabel(context.operation)}</EntityChip>,
                'primary',
              ],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', 'MERGED', 'secondary'],
            ]}
          />
          {write.outcomeUnknown ? (
            <Guidance tone="warn">
              The server did not answer — this combine may or may not have been
              recorded. Retry the exact same combine to find out: the server
              answers with the recorded result, or records it once. Nothing can
              be changed until then.
            </Guidance>
          ) : null}
          {write.serverError ? (
            <Guidance tone="error">{write.serverError}</Guidance>
          ) : null}
          {writeBlocked && !write.serverError && !write.outcomeUnknown ? (
            <Guidance tone="error">
              Disconnected — the combine cannot be recorded until the connection
              returns.
            </Guidance>
          ) : null}
          <StepButtons
            onBack={
              write.outcomeUnknown || write.rejected
                ? undefined
                : () => {
                    write.clearError();
                    setStep('select');
                  }
            }
            onCancel={cancel}
            cancelLabel={
              write.outcomeUnknown ? 'Leave — check the Area' : 'Cancel (Esc)'
            }
            primary={{
              label: write.outcomeUnknown
                ? 'Retry the same combine'
                : write.serverError
                  ? 'Retry combine'
                  : write.busy
                    ? 'Recording…'
                    : 'Confirm combine',
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
