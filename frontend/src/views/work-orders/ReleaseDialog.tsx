import { useMemo, useRef, useState } from 'react';

import { errorMessage } from '../../api/client';
import { areaColor, listAreas, listOperations } from '../../api/environment';
import {
  activeQuantityConfirmation,
  newDeviceEventId,
  releaseToProduction,
} from '../../api/production-release';
import type {
  ActiveQuantityEntry,
  ProductionReleaseResult,
} from '../../api/production-release';
import { listRouteTemplates } from '../../api/route-templates';
import { useApiData } from '../../api/use-api-data';
import { ModalDialog } from '../../components/ModalDialog';
import { ErrorState, LoadingState } from '../../components/view-states';
import { isPositiveInteger } from './demand-lines';
import type { ReleaseRequestContext } from './WorkOrderDetailPanel';

/**
 * Release to production — the explicit confirmation flow of GUI_DESIGN
 * §11.4, carried by one saved demand row. One dialog confirms, in
 * order: the release quantity; the Route Mode (`FLOATING` default — no
 * Route required; `PLANNED` with an existing active Planned Route,
 * snapshot noted); the starting Area and Operation; the existing
 * active distribution of the PN when there is one (explicit
 * confirmation — never auto-created or auto-merged quantity); and a
 * release summary before commit. On Confirm the committed result is
 * reported: Quantity Flow id, Route, starting Area, quantity, and the
 * appended `RECEIVED` Movement.
 *
 * ONE submission keeps ONE `device_event_id` through every retry —
 * including the resubmission after the active-quantity confirmation —
 * so a transport retry can only replay the original committed result,
 * never create a second flow. A fresh dialog is a fresh submission,
 * and so is a CHANGED intent: editing a field that the server's
 * request fingerprint covers after an attempt starts a new key
 * (SLICE1 §14 — "a new release intent gets a new `device_event_id`"),
 * so a corrected retry is never rejected as an idempotency conflict.
 *
 * A demand may be released in several parts, so the dialog offers the
 * REMAINING quantity by default and never lets more than that travel;
 * the backend enforces the same cap.
 */
export function ReleaseDialog({
  workOrderId,
  workOrderNumber,
  demand,
  writeBlocked,
  onCancel,
  onReleased,
}: {
  workOrderId: number;
  workOrderNumber: string | null;
  demand: ReleaseRequestContext;
  writeBlocked: boolean;
  onCancel: () => void;
  /** The commit was reported — the result dialog was acknowledged. */
  onReleased: (result: ProductionReleaseResult) => void;
}) {
  const areasData = useApiData(listAreas);
  const operationsData = useApiData(listOperations);
  const templatesData = useApiData(listRouteTemplates);

  const [qty, setQty] = useState(String(demand.remainingQuantity || ''));
  const [routeMode, setRouteMode] = useState<'FLOATING' | 'PLANNED'>(
    'FLOATING',
  );
  const [templateId, setTemplateId] = useState<number | null>(null);
  const [areaId, setAreaId] = useState<number | null>(null);
  const [operationId, setOperationId] = useState<number | null>(null);
  // The existing active distribution, once the backend required its
  // confirmation (409 + payload). Nothing was created by that answer.
  const [distribution, setDistribution] = useState<
    ActiveQuantityEntry[] | null
  >(null);
  const [confirmActive, setConfirmActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [result, setResult] = useState<ProductionReleaseResult | null>(null);
  // The submission's idempotency key, created lazily on first submit
  // and reused on every transport retry of the SAME intent.
  const deviceEventId = useRef<string | null>(null);
  const submitted = useRef(false);

  function releaseKey(): string {
    deviceEventId.current ??= newDeviceEventId();
    return deviceEventId.current;
  }

  /**
   * A changed release intent is a NEW submission (SLICE1 §14): drop
   * the key so the next submit gets a fresh one. Without this, a
   * corrected retry after a failed attempt would travel under the old
   * key and be refused as an idempotency conflict. The
   * active-quantity confirmation is deliberately NOT an intent change
   * — it is the same submission, continued.
   */
  function intentChanged() {
    if (!submitted.current) return;
    submitted.current = false;
    deviceEventId.current = null;
    setServerError(null);
  }

  const loading =
    areasData.state.status === 'loading' ||
    operationsData.state.status === 'loading' ||
    templatesData.state.status === 'loading';
  const loadError =
    areasData.state.status === 'error'
      ? areasData.state
      : operationsData.state.status === 'error'
        ? operationsData.state
        : templatesData.state.status === 'error'
          ? templatesData.state
          : null;

  // Memoized so the not-ready fallback keeps ONE stable [] identity —
  // otherwise every render would rebuild it and defeat the dependent
  // useMemo hooks below (react-hooks/exhaustive-deps).
  const areas = useMemo(
    () => (areasData.state.status === 'ready' ? areasData.state.data : []),
    [areasData.state],
  );
  const operations = useMemo(
    () =>
      operationsData.state.status === 'ready' ? operationsData.state.data : [],
    [operationsData.state],
  );
  const templates = useMemo(
    () =>
      templatesData.state.status === 'ready' ? templatesData.state.data : [],
    [templatesData.state],
  );

  // Production starts in an active non-terminal Area (a terminal Area
  // holds completed quantity); Operations are the Area's active ones.
  const startAreas = useMemo(
    () => areas.filter((area) => area.isActive && !area.isTerminal),
    [areas],
  );
  const template =
    routeMode === 'PLANNED'
      ? (templates.find((entry) => entry.id === templateId) ?? null)
      : null;
  const firstStep = template?.steps[0] ?? null;

  // PLANNED fixes the starting Area to the Route's first step; the
  // Operation is fixed too when the step defines one (SLICE1 §10 — a
  // mismatch is a validation failure, never a silent adjustment).
  const effectiveAreaId = firstStep ? firstStep.areaId : areaId;
  const effectiveArea = areas.find((area) => area.id === effectiveAreaId);
  const areaOperations = useMemo(
    () =>
      operations.filter(
        (operation) =>
          operation.isActive && operation.areaId === effectiveAreaId,
      ),
    [operations, effectiveAreaId],
  );
  const effectiveOperationId = firstStep?.operationId ?? operationId;
  const effectiveOperation = operations.find(
    (operation) => operation.id === effectiveOperationId,
  );

  const parsedQty = isPositiveInteger(qty) ? Number.parseInt(qty, 10) : null;
  // The demand's remaining quantity is the cap — the server refuses
  // anything beyond it, so the dialog never submits it either.
  const overRemaining =
    parsedQty !== null && parsedQty > demand.remainingQuantity;
  const areaName = effectiveArea?.name ?? '—';
  const operationLabel = effectiveOperation
    ? (effectiveOperation.name ?? effectiveOperation.code)
    : '—';

  const submittable =
    !writeBlocked &&
    !busy &&
    parsedQty !== null &&
    !overRemaining &&
    effectiveAreaId !== null &&
    effectiveOperationId !== null &&
    areaOperations.some((op) => op.id === effectiveOperationId) &&
    (routeMode === 'FLOATING' ||
      (template !== null && template.steps.length > 0)) &&
    (distribution === null || confirmActive);

  async function submit() {
    if (
      parsedQty === null ||
      effectiveAreaId === null ||
      effectiveOperationId === null
    ) {
      return;
    }
    setBusy(true);
    setServerError(null);
    submitted.current = true;
    try {
      const committed = await releaseToProduction(
        workOrderId,
        demand.demandId,
        {
          partNumber: demand.partNumber,
          quantity: parsedQty,
          routeMode,
          routeTemplateId: routeMode === 'PLANNED' ? templateId : null,
          startingAreaId: effectiveAreaId,
          operationId: effectiveOperationId,
          confirmActiveQuantity: distribution !== null && confirmActive,
          deviceEventId: releaseKey(),
        },
      );
      setBusy(false);
      setResult(committed);
    } catch (error) {
      setBusy(false);
      const entries = activeQuantityConfirmation(error);
      if (entries) {
        // Nothing was created — the same submission continues with the
        // distribution shown and an explicit confirmation required.
        setDistribution(entries);
        setConfirmActive(false);
        return;
      }
      setServerError(errorMessage(error));
    }
  }

  if (result) {
    // §11.4 (6): the committed result — every value from the immutable
    // release record.
    return (
      <ModalDialog
        label="Release committed"
        onClose={() => onReleased(result)}
        size="wide"
      >
        <h3>Release committed</h3>
        <div className="big mono">{result.partNumber}</div>
        <div className="relsum" role="status">
          {result.created ? (
            <>
              A new, separate Quantity Flow was created — existing quantity was
              not touched.
            </>
          ) : (
            <>
              This submission was already committed — the original result is
              shown; nothing new was created.
            </>
          )}
        </div>
        <dl className="relresult">
          <dt>Quantity Flow</dt>
          <dd className="mono">#{result.quantityFlowId}</dd>
          <dt>Quantity</dt>
          <dd className="mono">× {result.quantity} pcs</dd>
          <dt>Route</dt>
          <dd>
            {result.routeMode === 'PLANNED' ? (
              <>
                PLANNED — route snapshot{' '}
                <span className="mono">#{result.assignedRouteId}</span>
              </>
            ) : (
              'FLOATING — actual route derives from Movement history'
            )}
          </dd>
          <dt>Starting Area</dt>
          <dd>
            {areas.find((area) => area.id === result.startingAreaId)?.name ??
              `Area #${result.startingAreaId}`}{' '}
            · {operationLabel}
          </dd>
          <dt>Movement</dt>
          <dd className="mono">RECEIVED · movement #{result.movementId}</dd>
        </dl>
        <div className="row">
          <button className="bigbtn primary" onClick={() => onReleased(result)}>
            Done
          </button>
        </div>
      </ModalDialog>
    );
  }

  return (
    <ModalDialog
      label="Release to production — explicit action"
      onClose={onCancel}
      size="wide"
    >
      <h3>Release to production — explicit action</h3>
      <div className="big mono">{demand.partNumber}</div>
      <div className="sub">
        WO {workOrderNumber ?? '—'} demand · requested{' '}
        <b>{demand.requestedQuantity || '—'}</b> · already released{' '}
        <b>{demand.releasedQuantity}</b> · remaining{' '}
        <b>{demand.remainingQuantity}</b> — nothing is created until you
        confirm.
      </div>
      {loading ? (
        <LoadingState label="Loading release choices" />
      ) : loadError ? (
        <ErrorState
          message="The release choices could not be loaded."
          detail={loadError.message}
          onRetry={() => {
            areasData.reload();
            operationsData.reload();
            templatesData.reload();
          }}
        />
      ) : (
        <>
          {distribution ? (
            <div className="relwarn" role="alert">
              ⚠ <b>This PN already has active quantity:</b>{' '}
              <span className="mono">
                {distribution
                  .map(
                    (entry) =>
                      `${entry.currentAreaName} × ${entry.quantity} (flow #${entry.quantityFlowId})`,
                  )
                  .join(' · ')}
              </span>
              . Confirm intent explicitly — release always creates a{' '}
              <b>separate</b> Quantity Flow; it never automatically adds to or
              merges existing flows.
              <label className="relconfirm">
                <input
                  type="checkbox"
                  checked={confirmActive}
                  onChange={(e) => setConfirmActive(e.target.checked)}
                />{' '}
                Release a separate Quantity Flow anyway — I reviewed the
                existing active quantity.
              </label>
            </div>
          ) : null}
          <div className="relgrid">
            <label htmlFor="rel-qty">Release quantity</label>
            <input
              id="rel-qty"
              className="mono"
              inputMode="numeric"
              value={qty}
              onChange={(e) => {
                setQty(e.target.value);
                intentChanged();
              }}
            />
            <label htmlFor="rel-mode">Route Mode</label>
            <select
              id="rel-mode"
              value={routeMode}
              onChange={(e) => {
                const mode = e.target.value as 'FLOATING' | 'PLANNED';
                setRouteMode(mode);
                if (mode === 'FLOATING') setTemplateId(null);
                intentChanged();
              }}
            >
              <option value="FLOATING">FLOATING — no Route (default)</option>
              <option value="PLANNED">PLANNED — existing Planned Route</option>
            </select>
            {routeMode === 'PLANNED' ? (
              <>
                <label htmlFor="rel-route">Planned Route</label>
                <select
                  id="rel-route"
                  value={templateId ?? ''}
                  onChange={(e) => {
                    setTemplateId(
                      e.target.value === '' ? null : Number(e.target.value),
                    );
                    intentChanged();
                  }}
                >
                  <option value="">Select a Planned Route…</option>
                  {templates.map((entry) => (
                    <option key={entry.id} value={entry.id}>
                      {entry.name}
                    </option>
                  ))}
                </select>
              </>
            ) : null}
            <label htmlFor="rel-area">Starting Area</label>
            {firstStep ? (
              <div className="relfixed">
                <span
                  className="dot"
                  style={{ background: areaColor(effectiveArea) }}
                />{' '}
                {areaName} — fixed by the Route's first step
              </div>
            ) : (
              <select
                id="rel-area"
                value={areaId ?? ''}
                onChange={(e) => {
                  const next =
                    e.target.value === '' ? null : Number(e.target.value);
                  setAreaId(next);
                  setOperationId(null);
                  intentChanged();
                }}
              >
                <option value="">Select the starting Area…</option>
                {startAreas.map((area) => (
                  <option key={area.id} value={area.id}>
                    {area.name}
                  </option>
                ))}
              </select>
            )}
            <label htmlFor="rel-op">Operation</label>
            {firstStep && firstStep.operationId !== null ? (
              <div className="relfixed">
                {operationLabel} — fixed by the Route's first step
              </div>
            ) : (
              <select
                id="rel-op"
                value={effectiveOperationId ?? ''}
                onChange={(e) => {
                  setOperationId(
                    e.target.value === '' ? null : Number(e.target.value),
                  );
                  intentChanged();
                }}
                disabled={effectiveAreaId === null}
              >
                <option value="">Select the Operation…</option>
                {areaOperations.map((operation) => (
                  <option key={operation.id} value={operation.id}>
                    {operation.name ?? operation.code}
                  </option>
                ))}
              </select>
            )}
          </div>
          {overRemaining ? (
            <div className="rowerr">
              Only {demand.remainingQuantity} pcs remain to release on this
              demand line ({demand.releasedQuantity} of{' '}
              {demand.requestedQuantity} already released).
            </div>
          ) : null}
          {routeMode === 'PLANNED' && templates.length === 0 ? (
            <div className="rowerr">
              No active Planned Route exists yet — release FLOATING, or define a
              Planned Route first.
            </div>
          ) : null}
          {effectiveAreaId !== null && areaOperations.length === 0 ? (
            <div className="rowerr">
              {areaName} has no active Operation — a release starts with an
              Operation. Configure one in Administration → Operations.
            </div>
          ) : null}
          {firstStep &&
          firstStep.operationId !== null &&
          areaOperations.length > 0 &&
          !areaOperations.some((op) => op.id === firstStep.operationId) ? (
            <div className="rowerr">
              The Route's first-step Operation is not active — this Planned
              Route cannot start a release right now.
            </div>
          ) : null}
          <div className="relsum">
            Release summary: <b>× {qty || '0'}</b> pcs as a new, separate
            Quantity Flow · Route{' '}
            <b>
              {routeMode === 'PLANNED'
                ? (template?.name ?? '— select a Planned Route')
                : 'FLOATING'}
            </b>
            {routeMode === 'PLANNED' ? ' (snapshot is taken on commit)' : ''} ·
            starts in <b>{areaName}</b> (Operation <b>{operationLabel}</b>) with
            a recorded <b>RECEIVED</b> event. Existing quantity of this PN is
            never merged.
          </div>
          {serverError ? (
            <div className="rowerr" role="alert">
              {serverError} You can retry — a retry of this submission can never
              create a second Quantity Flow.
            </div>
          ) : null}
          <div className="row">
            <button className="bigbtn ghost" onClick={onCancel}>
              Cancel (Esc)
            </button>
            <button
              className="bigbtn primary"
              disabled={!submittable}
              onClick={() => void submit()}
            >
              {busy
                ? 'Releasing…'
                : serverError
                  ? 'Retry release'
                  : 'Confirm release'}
            </button>
          </div>
        </>
      )}
    </ModalDialog>
  );
}
