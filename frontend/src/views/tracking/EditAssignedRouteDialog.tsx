import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import {
  ApiError,
  errorMessage,
  isReleaseMismatch,
  refusalFlag,
} from '../../api/client';
import { listAreas, listOperations } from '../../api/environment';
import { listMachines } from '../../api/machines';
import { newDeviceEventId } from '../../api/production-release';
import {
  adjustAssignedRoute,
  isRouteChanged,
  loadAssignedRoutes,
} from '../../api/route-adjustments';
import type {
  AdjustableFlow,
  EditorStep,
  RouteAdjustmentInput,
} from '../../api/route-adjustments';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { connectionReason } from '../../app/release-copy';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { ModalDialog } from '../../components/ModalDialog';
import { useUiClock } from '../../components/ui-clock';
import { ErrorState, LoadingState } from '../../components/view-states';
import { operationLabel } from '../area-presentation';
import { formatElapsedSince } from '../dates';
import { RouteStepList } from '../planned-routes/route-step-editor';
import type { Catalog, EditableStep } from '../planned-routes/route-steps';
import {
  editableStep,
  estimateText,
  findById,
  stepDuration,
  stepInput,
  stepsKey,
  validateSteps,
} from '../planned-routes/route-steps';
import { flowId } from './tracking-logic';

const DISCONNECTED =
  'Changing a route needs the connection to the PartFlow server.';
const OUTCOME_UNKNOWN =
  'The server did not answer — the route may or may not have been adjusted. Use Retry to send the same adjustment again; PartFlow applies it only once.';
const OUTCOME_UNKNOWN_AFTER_CLOSE =
  "The last route adjustment may have been applied. Check the Quantity Flow's route before trying again.";
const RETRY_SIGN_IN =
  'Sign in again, then use Retry. The adjustment is applied only once.';
const RETRY_REFUSED =
  'The adjustment may already be applied; reload the route to check.';
const RECORDED_STEP_TITLE =
  'An undone arrival recorded this step, so it stays.';
const NO_STEP_EXPECTED =
  "No step is expected next — the quantity's next arrival anywhere needs a route-deviation confirmation.";

interface EditorData {
  catalog: Catalog;
  flows: AdjustableFlow[];
}

/** What the last submission left open (null: nothing). */
type Outcome = 'unknown' | 'route-changed' | 'another-user' | null;

/** The editable tail as loaded (the steps after the kept-through step). */
function loadedTail(flow: AdjustableFlow): EditableStep[] {
  return flow.steps
    .filter((step) => !step.locked)
    .map((step, index) =>
      editableStep(
        {
          areaId: step.area.id,
          operationId: step.operation?.id ?? null,
          expectedDuration: step.expectedDuration,
          preferredMachineId: step.preferredMachine?.id ?? null,
          instructions: step.instructions,
        },
        index,
      ),
    );
}

/** Where the flow is now: Area, Machine when one holds it, time there. */
function flowChoiceText(flow: AdjustableFlow, nowMs: number): string {
  const position = flow.position;
  if (position === null) return 'no current position';
  return [
    position.area.name,
    ...(position.machine !== null ? [position.machine.name] : []),
    `${formatElapsedSince(position.since, nowMs)} in Area`,
  ].join(' · ');
}

function flowSummary(flow: AdjustableFlow, nowMs: number): string {
  return `${flowId(flow.quantityFlowId)} · ${flow.quantity} pcs · ${flowChoiceText(flow, nowMs)} · ${flow.sourceTemplate?.name ?? 'Planned Route'}`;
}

function lockedStateLabel(step: EditorStep): string {
  return step.state === 'DONE'
    ? 'Done'
    : step.state === 'CURRENT'
      ? 'Current'
      : 'Recorded';
}

/** One step as the review shows it: the locked-row content
 * (`{Area} · {Operation} · Est. {time}`) plus the preferred Machine. */
function reviewLabel(
  area: string,
  operation: string | null,
  estimate: string,
  machine: string | null,
): string {
  return [
    area,
    operation ?? '—',
    `Est. ${estimate || '—'}`,
    ...(machine !== null ? [machine] : []),
  ].join(' · ');
}

/** Review chips, or the explicit empty statement. */
function Chips({ names }: { names: string[] }) {
  if (names.length === 0) return <>no further steps</>;
  return (
    <span className="ear-chips">
      {names.flatMap((name, index) => {
        const chip = (
          <span className="ear-chip" key={`chip-${index}`}>
            {name}
          </span>
        );
        return index === 0
          ? [chip]
          : [
              <span key={`arrow-${index}`} aria-hidden="true">
                {' → '}
              </span>,
              chip,
            ];
      })}
    </span>
  );
}

/**
 * Tracking → Corrections → `Edit assigned Route…` (GUI_DESIGN §7.2;
 * Phase 14 slice 6): replace the FUTURE steps of one active Planned
 * Quantity Flow's own AssignedRoute. Past steps — the ones the quantity
 * reached or its history records — are shown locked; the tail is edited
 * with the Planned Routes step rows, numbered on from the last locked
 * step; a reason is mandatory and a review step shows the route before
 * and after. Only this Quantity Flow changes: its Planned Route, the
 * other Quantity Flows and the Movement history stay as they are.
 *
 * ONE body keeps ONE `device_event_id` through every resend (Retry); a
 * changed body after a definite refusal gets a new one. When the server
 * did not answer, the editor stays read-only until a definite answer —
 * Retry sends the identical request, which PartFlow applies once.
 * Presentation only: the server judges every write again under its
 * locks and refuses a route that changed since it was read.
 *
 * `onClose`: `changed` — the Tracking detail must be read again;
 * `notice` — the Corrections status line to show (null: none).
 */
export function EditAssignedRouteDialog({
  pn,
  onClose,
}: {
  pn: string;
  onClose: (result: { changed: boolean; notice: string | null }) => void;
}) {
  const headingId = useId();
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const nowMs = useUiClock('minute');

  const load = useCallback(async (): Promise<EditorData> => {
    const [areas, operations, machines, flows] = await Promise.all([
      listAreas(),
      listOperations(),
      listMachines(),
      loadAssignedRoutes(pn),
    ]);
    return { catalog: { areas, operations, machines }, flows };
  }, [pn]);
  const data = useApiData(load);
  const ready = data.state.status === 'ready' ? data.state.data : null;
  const flows = ready?.flows ?? [];

  const [chosenId, setChosenId] = useState<number | null>(null);
  const flow =
    flows.find((item) => item.quantityFlowId === chosenId) ??
    (flows.length === 1 ? flows[0] : null);

  // The editor follows the flow (and the read it came from): a new
  // choice or a re-read route starts from its loaded tail.
  const [base, setBase] = useState<{
    flow: AdjustableFlow;
    tail: EditableStep[];
  } | null>(null);
  const [steps, setSteps] = useState<EditableStep[]>([]);
  if (flow !== (base?.flow ?? null)) {
    const tail = flow === null ? [] : loadedTail(flow);
    setBase(flow === null ? null : { flow, tail });
    setSteps(tail);
  }

  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [stage, setStage] = useState<'confirm' | 'discard' | null>(null);
  // The last request sent: its idempotency key travels with every
  // resend of the identical body.
  const sent = useRef<{
    flowId: number;
    input: RouteAdjustmentInput;
    bodyKey: string;
  } | null>(null);

  if (reloading && data.state.status !== 'ready') setReloading(false);
  if (reloading && base !== null && flow !== base.flow) setReloading(false);

  // False once this dialog unmounted: a late answer never acts on it.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const bodyRef = useRef<HTMLDivElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const reviewRef = useRef<HTMLButtonElement>(null);
  const [reviewFocus, setReviewFocus] = useState(0);
  const initialFocusDone = useRef(false);
  const flowCount = flows.length;
  const isReady = ready !== null;
  useEffect(() => {
    if (!isReady || initialFocusDone.current) return;
    initialFocusDone.current = true;
    const body = bodyRef.current;
    if (flowCount > 1) {
      body?.querySelector<HTMLElement>('input[type="radio"]')?.focus();
    } else if (flowCount === 1) {
      (
        body?.querySelector<HTMLElement>(
          '.rt-steplist select, .rt-steplist input',
        ) ?? reasonRef.current
      )?.focus();
    }
  }, [isReady, flowCount]);
  useEffect(() => {
    if (reviewFocus > 0) reviewRef.current?.focus();
  }, [reviewFocus]);

  const locked = busy || reloading || outcome !== null;
  const tailChanged = base !== null && stepsKey(steps) !== stepsKey(base.tail);
  const dirty = tailChanged || reason.trim() !== '';
  const kept = flow?.keptThroughSequence ?? 0;
  const problem =
    ready !== null && flow !== null
      ? validateSteps(steps, ready.catalog, kept + 1)
      : null;
  const reviewable =
    !writeBlocked &&
    !busy &&
    tailChanged &&
    problem === null &&
    reason.trim() !== '';

  function requestClose() {
    if (busy || outcome === 'unknown') {
      onClose({ changed: true, notice: OUTCOME_UNKNOWN_AFTER_CLOSE });
      return;
    }
    if (outcome === 'another-user') {
      onClose({ changed: true, notice: null });
      return;
    }
    if (dirty) {
      setStage('discard');
      return;
    }
    onClose({ changed: false, notice: null });
  }

  function reloadRoute() {
    setOutcome(null);
    setServerError(null);
    setReason('');
    setReloading(true);
    // A re-read route is a new base: the editor resets to it.
    setBase(null);
    data.reload();
  }

  /** Explain a refusal; nothing was applied unless the outcome is
   * unknown. `afterUnknown`: this resend repeated a request whose
   * outcome was unknown. */
  function handleFailure(error: unknown, afterUnknown: boolean) {
    const unknown =
      !(error instanceof ApiError) ||
      error.status === 408 ||
      error.status >= 500;
    if (unknown) {
      setOutcome('unknown');
      setServerError(OUTCOME_UNKNOWN);
      return;
    }
    const detail = errorMessage(error);
    if (isReleaseMismatch(error)) {
      // PartFlow was updated while this page was open: refused before
      // the key check, so an unknown outcome stays unknown.
      setServerError(detail);
      return;
    }
    if (afterUnknown && error.status === 401) {
      setServerError(`${detail} ${RETRY_SIGN_IN}`);
      return;
    }
    if (afterUnknown && error.status === 403) {
      setServerError(`${detail} ${RETRY_REFUSED}`);
      return;
    }
    setServerError(detail);
    if (refusalFlag(error, 'recorded_by_another_user')) {
      setOutcome('another-user');
    } else if (isRouteChanged(error)) {
      setOutcome('route-changed');
    } else {
      // A definite refusal: nothing was applied, the inputs are kept.
      setOutcome(null);
    }
  }

  async function send(request: {
    flowId: number;
    input: RouteAdjustmentInput;
    bodyKey: string;
  }) {
    const afterUnknown = outcome === 'unknown';
    sent.current = request;
    setBusy(true);
    setServerError(null);
    try {
      const result = await adjustAssignedRoute(request.flowId, request.input);
      if (!alive.current) return;
      setBusy(false);
      setStage(null);
      onClose({
        changed: true,
        notice: `✓ Route adjusted for ${flowId(result.quantityFlowId)}.`,
      });
    } catch (error) {
      if (!alive.current) return;
      setBusy(false);
      setStage(null);
      handleFailure(error, afterUnknown);
    }
  }

  function confirmAdjustment() {
    if (flow === null || busy || writeBlocked) return;
    const body = {
      expectedFutureStepIds: flow.futureStepIds,
      steps: steps.map(stepInput),
      reason: reason.trim(),
    };
    const bodyKey = JSON.stringify({ flowId: flow.quantityFlowId, body });
    const deviceEventId =
      sent.current?.bodyKey === bodyKey
        ? sent.current.input.deviceEventId
        : newDeviceEventId();
    void send({
      flowId: flow.quantityFlowId,
      input: { deviceEventId, ...body },
      bodyKey,
    });
  }

  function retry() {
    if (sent.current === null || busy || writeBlocked) return;
    void send(sent.current);
  }

  function renderEditor(target: AdjustableFlow, catalog: Catalog) {
    const lockedSteps = target.steps.filter((step) => step.locked);
    const recorded = lockedSteps.find((step) => step.state === 'FUTURE');
    const lastLocked = lockedSteps[lockedSteps.length - 1];
    const areaName = (areaId: number) =>
      findById(catalog.areas, areaId)?.name ?? `Area ${areaId}`;
    const nowTail = target.steps.filter((step) => !step.locked);
    const nowNames = nowTail.map((step) =>
      reviewLabel(
        step.area.name,
        step.operation ? operationLabel(step.operation) : null,
        estimateText(step.expectedDuration),
        step.preferredMachine?.name ?? null,
      ),
    );
    const newNames = steps.map((step) => {
      const operation = findById(catalog.operations, step.operationId);
      const duration = stepDuration(step);
      return reviewLabel(
        areaName(step.areaId),
        operation ? operationLabel(operation) : null,
        duration === false ? step.durationText.trim() : estimateText(duration),
        step.preferredMachineId === null
          ? null
          : (findById(catalog.machines, step.preferredMachineId)?.name ??
              `Machine ${step.preferredMachineId}`),
      );
    });
    // Instructions are not in the chips: name the steps whose chip reads
    // the same but whose instructions change, so no change is invisible.
    const instructionSteps = steps.flatMap((step, index) => {
      const before = nowTail[index];
      return before !== undefined &&
        nowNames[index] === newNames[index] &&
        (before.instructions ?? '').trim() !== step.instructions.trim()
        ? [kept + 1 + index]
        : [];
    });
    return (
      <>
        <div className="ear-sec">
          <h4>Locked steps</h4>
          <p className="ear-note">
            Steps the quantity has reached, or that its history records, stay as
            they are.
          </p>
          {target.offRoute && target.position !== null ? (
            <p className="ear-note ear-offroute">
              This quantity is currently off its Planned Route (in{' '}
              {target.position.area.name}).
            </p>
          ) : null}
          <ol className="ear-locked">
            {lockedSteps.map((step) => (
              <li key={step.id}>
                <span>
                  {step.sequence}. {step.area.name} ·{' '}
                  {step.operation ? operationLabel(step.operation) : '—'} · Est.{' '}
                  {estimateText(step.expectedDuration) || '—'}
                </span>
                <span
                  className={`ear-state ${step.state.toLowerCase()}`}
                  title={
                    step.state === 'FUTURE' ? RECORDED_STEP_TITLE : undefined
                  }
                >
                  {lockedStateLabel(step)}
                </span>
              </li>
            ))}
          </ol>
        </div>
        <div className="ear-sec">
          <h4>Future steps</h4>
          <p className="ear-note">
            {recorded
              ? `The quantity's next on-route arrival is checked against step ${recorded.sequence} (recorded above), then the steps that follow.`
              : steps.length > 0
                ? "The quantity's next on-route arrival is checked against the first step below."
                : NO_STEP_EXPECTED}
          </p>
          <RouteStepList
            catalog={catalog}
            steps={steps}
            onChange={(update) => {
              if (!locked) setSteps(update);
            }}
            firstNumber={kept + 1}
            minSteps={0}
            fallbackAreaId={lastLocked?.area.id}
            disabled={locked}
          />
          {steps.length === 0 ? (
            <p className="ear-note ear-empty">
              {recorded
                ? `No further steps after step ${kept}.`
                : `No further steps — the route ends after step ${kept}.`}
            </p>
          ) : null}
          {problem !== null ? (
            <div className="ear-problem" role="status">
              {problem}
            </div>
          ) : null}
        </div>
        <label className="ear-label" htmlFor={`${headingId}-reason`}>
          Reason <span className="field-required">(required)</span>
        </label>
        <textarea
          id={`${headingId}-reason`}
          ref={reasonRef}
          className="ear-reason"
          rows={3}
          placeholder="Why is this route adjusted?"
          value={reason}
          readOnly={locked}
          onChange={(e) => {
            if (!locked) setReason(e.target.value);
          }}
        />
        <p className="ear-fixed">
          Only this Quantity Flow changes. Its Planned Route, the other Quantity
          Flows and the Movement history stay as they are; the previous route is
          kept in the audit history.
        </p>
        {stage === 'confirm' ? (
          <ConfirmDialog
            title="Adjust the assigned route?"
            confirmLabel="Adjust route"
            cancelLabel="Keep editing"
            confirmDisabled={writeBlocked || busy}
            onCancel={() => {
              if (busy) return;
              setStage(null);
              setReviewFocus((value) => value + 1);
            }}
            onConfirm={confirmAdjustment}
          >
            <div className="ear-review">
              <div>
                {flowId(target.quantityFlowId)} · steps after step {kept}
              </div>
              <div>
                Now: <Chips names={nowNames} />
              </div>
              <div>
                New: <Chips names={newNames} />
              </div>
              {instructionSteps.length > 0 ? (
                <div>
                  Instructions change for{' '}
                  {instructionSteps.map((n) => `step ${n}`).join(', ')}.
                </div>
              ) : null}
              <div>Reason: {reason.trim()}</div>
            </div>
          </ConfirmDialog>
        ) : null}
      </>
    );
  }

  function renderActions() {
    if (outcome === 'another-user') {
      return (
        <div className="row">
          <button className="bigbtn ghost" onClick={requestClose}>
            Close
          </button>
        </div>
      );
    }
    return (
      <div className="row">
        <button className="bigbtn ghost" onClick={requestClose}>
          Cancel (Esc)
        </button>
        {outcome === 'unknown' ? (
          <button
            className="bigbtn primary"
            disabled={busy || writeBlocked}
            onClick={retry}
          >
            Retry
          </button>
        ) : outcome === 'route-changed' ? (
          <button className="bigbtn primary" onClick={reloadRoute}>
            Reload route
          </button>
        ) : flow !== null ? (
          <button
            ref={reviewRef}
            className="bigbtn primary"
            disabled={!reviewable || locked}
            onClick={() => setStage('confirm')}
          >
            Review adjustment
          </button>
        ) : null}
      </div>
    );
  }

  let body: ReactNode;
  if (data.state.status === 'error') {
    body = (
      <>
        <ErrorState
          message={
            writeBlocked
              ? connectionReason(status, DISCONNECTED)
              : data.state.message
          }
          onRetry={data.reload}
        />
        <div className="row">
          <button className="bigbtn ghost" onClick={requestClose}>
            Close
          </button>
        </div>
      </>
    );
  } else if (ready === null) {
    body = (
      <>
        <LoadingState label="Loading the assigned routes…" />
        <div className="row">
          <button className="bigbtn ghost" onClick={requestClose}>
            Cancel (Esc)
          </button>
        </div>
      </>
    );
  } else if (flows.length === 0) {
    body = (
      <>
        <p className="ear-note">
          No active Quantity Flow of {pn} follows a Planned Route. Only an
          active Planned flow has an assigned route to change.
        </p>
        <div className="row">
          <button className="bigbtn ghost" onClick={requestClose}>
            Close
          </button>
        </div>
      </>
    );
  } else {
    body = (
      <div ref={bodyRef}>
        {flows.length > 1 ? (
          <div className="ear-sec">
            <div
              className="ear-flows"
              role="radiogroup"
              aria-label="Quantity Flow"
            >
              {flows.map((item) => (
                <label key={item.quantityFlowId} className="ear-flow">
                  <input
                    type="radio"
                    name={`${headingId}-flow`}
                    checked={flow?.quantityFlowId === item.quantityFlowId}
                    disabled={locked}
                    onChange={() => setChosenId(item.quantityFlowId)}
                  />
                  {flowSummary(item, nowMs)}
                </label>
              ))}
            </div>
            <p className="ear-note">
              Choose the Quantity Flow whose route changes — other Quantity
              Flows of {pn} keep their routes.
            </p>
          </div>
        ) : (
          <p className="ear-note ear-oneflow">{flowSummary(flows[0], nowMs)}</p>
        )}
        {flow !== null ? renderEditor(flow, ready.catalog) : null}
        {serverError !== null ? (
          <div className="ear-error" role="alert">
            {serverError}
          </div>
        ) : null}
        {writeBlocked && flow !== null ? (
          <p className="ear-note">{connectionReason(status, DISCONNECTED)}</p>
        ) : null}
        {renderActions()}
      </div>
    );
  }

  return (
    <ModalDialog
      labelledBy={headingId}
      onClose={requestClose}
      size="xwide"
      className="ear"
    >
      <div className="ear-head">
        <h3 id={headingId}>Edit assigned Route — {pn}</h3>
        {dirty ? <span className="rt-dirty">● Unsaved changes</span> : null}
      </div>
      {body}
      {stage === 'discard' ? (
        <ConfirmDialog
          title="Discard unsaved route changes?"
          confirmLabel="Discard changes"
          cancelLabel="Keep editing"
          danger
          onCancel={() => setStage(null)}
          onConfirm={() => onClose({ changed: false, notice: null })}
        >
          The changes to this route have not been saved and will be lost.
        </ConfirmDialog>
      ) : null}
    </ModalDialog>
  );
}
