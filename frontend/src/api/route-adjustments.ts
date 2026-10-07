// AssignedRoute adjustment API (Phase 14 slice 6 — PROJECT_PROFILE
// §8.10, §17; GUI_DESIGN §7.2 Corrections).
//
// An authorized change of the FUTURE steps of one active Planned
// Quantity Flow's own AssignedRoute: past steps — every step a Movement
// of the flow references, undone Movements included — are immutable;
// the request replaces only the steps after them, with a mandatory
// reason, idempotent per `device_event_id` and audited as
// `ROUTE_ADJUSTED` with the signed-in User. The Planned Route, the
// other Quantity Flows and the Movement history never change.
//
// One read serves the editor: the active Planned flows of a PN, each
// with its whole route (locked past steps and the editable tail) and
// the tail's step ids as read — the write sends them back so a route
// that changed in between is refused (`route_changed`), never
// overwritten.
//
// These calls are signed-in Management requests: they never carry a
// Scan Station's enrolled-device header. Converters throw on a
// malformed answer instead of rendering a wrong route.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest, refusalFlag } from './client';
import type { RouteStepInput, RouteTemplateStep } from './route-templates';
import type {
  LocationState,
  RouteStepState,
  TrackingAreaRef,
  TrackingFlowPosition,
  TrackingMachineRef,
  TrackingOperationRef,
} from './tracking';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** One step of an assigned route as the editor reads it. */
export interface EditorStep {
  id: number;
  sequence: number;
  area: TrackingAreaRef;
  operation: TrackingOperationRef | null;
  /** Advisory estimated time — ISO 8601 (`PT4H`) as delivered. */
  expectedDuration: string | null;
  preferredMachine: TrackingMachineRef | null;
  instructions: string | null;
  state: RouteStepState;
  /** A past step (at or before the kept-through sequence): never edited. */
  locked: boolean;
}

/** One active Planned Quantity Flow whose future steps may change. */
export interface AdjustableFlow {
  quantityFlowId: number;
  quantity: number;
  position: TrackingFlowPosition | null;
  offRoute: boolean;
  sourceTemplate: { id: number; name: string } | null;
  /** The highest step sequence any Movement of the flow references. */
  keptThroughSequence: number;
  /** The editable tail as read, in sequence order (sent back on write). */
  futureStepIds: number[];
  /** The whole route in sequence order. */
  steps: EditorStep[];
}

export interface RouteAdjustmentInput {
  deviceEventId: string;
  expectedFutureStepIds: number[];
  /** The new tail in route order (empty: the route ends after the
   * kept-through step). */
  steps: RouteStepInput[];
  reason: string;
}

/** The committed adjustment (201 fresh, 200 replay — the same answer). */
export interface RouteAdjustmentResult {
  deviceEventId: string;
  quantityFlowId: number;
  partNumber: string;
  assignedRouteId: number;
  keptThroughSequence: number;
  reason: string;
  /** The whole route after the adjustment. */
  steps: RouteTemplateStep[];
}

// ---------------------------------------------------------------------------
// Wire mapping (checked)
// ---------------------------------------------------------------------------

type Wire = Record<string, unknown>;

const STEP_STATES: readonly RouteStepState[] = ['DONE', 'CURRENT', 'FUTURE'];
const LOCATION_STATES: readonly LocationState[] = [
  'MACHINE',
  'QUEUE',
  'PROCESSING',
  'DONE',
  'STOCKED',
];

function malformed(): Error {
  return new Error('The server answered a malformed assigned route.');
}

function record(value: unknown): Wire {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw malformed();
  }
  return value as Wire;
}

function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw malformed();
  return value;
}

function int(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value)) throw malformed();
  return value;
}

function text(value: unknown): string {
  if (typeof value !== 'string') throw malformed();
  return value;
}

function textOrNull(value: unknown): string | null {
  return value === null ? null : text(value);
}

function flag(value: unknown): boolean {
  if (typeof value !== 'boolean') throw malformed();
  return value;
}

function oneOf<T extends string>(value: unknown, known: readonly T[]): T {
  const found = known.find((item) => item === value);
  if (found === undefined) throw malformed();
  return found;
}

function toArea(value: unknown): TrackingAreaRef {
  const wire = record(value);
  return {
    id: int(wire.id),
    name: text(wire.name),
    color: textOrNull(wire.color),
    isTerminal: flag(wire.is_terminal),
  };
}

function toOperation(value: unknown): TrackingOperationRef {
  const wire = record(value);
  return {
    id: int(wire.id),
    code: text(wire.code),
    name: textOrNull(wire.name),
    isExternal: flag(wire.is_external),
  };
}

function toMachine(value: unknown): TrackingMachineRef {
  const wire = record(value);
  return { id: int(wire.id), name: text(wire.name) };
}

function toPosition(value: unknown): TrackingFlowPosition | null {
  if (value === null) return null;
  const wire = record(value);
  return {
    area: toArea(wire.area),
    machine: wire.machine === null ? null : toMachine(wire.machine),
    operation: toOperation(wire.operation),
    activity: textOrNull(wire.activity),
    state: oneOf(wire.state, LOCATION_STATES),
    since: text(wire.since),
    expectedBy: textOrNull(wire.expected_by),
  };
}

function toEditorStep(value: unknown): EditorStep {
  const wire = record(value);
  return {
    id: int(wire.id),
    sequence: int(wire.sequence),
    area: toArea(wire.area),
    operation: wire.operation === null ? null : toOperation(wire.operation),
    expectedDuration: textOrNull(wire.expected_duration),
    preferredMachine:
      wire.preferred_machine === null
        ? null
        : toMachine(wire.preferred_machine),
    instructions: textOrNull(wire.instructions),
    state: oneOf(wire.state, STEP_STATES),
    locked: flag(wire.locked),
  };
}

function toAdjustableFlow(value: unknown): AdjustableFlow {
  const wire = record(value);
  const template =
    wire.source_template === null ? null : record(wire.source_template);
  return {
    quantityFlowId: int(wire.quantity_flow_id),
    quantity: int(wire.quantity),
    position: toPosition(wire.position),
    offRoute: flag(wire.off_route),
    sourceTemplate:
      template === null
        ? null
        : { id: int(template.id), name: text(template.name) },
    keptThroughSequence: int(wire.kept_through_sequence),
    futureStepIds: list(wire.future_step_ids).map(int),
    steps: list(wire.steps).map(toEditorStep),
  };
}

function toResultStep(value: unknown): RouteTemplateStep {
  const wire = record(value);
  return {
    id: int(wire.id),
    sequence: int(wire.sequence),
    areaId: int(wire.area_id),
    operationId: wire.operation_id === null ? null : int(wire.operation_id),
    expectedDuration: textOrNull(wire.expected_duration),
    preferredMachineId:
      wire.preferred_machine_id === null
        ? null
        : int(wire.preferred_machine_id),
    instructions: textOrNull(wire.instructions),
  };
}

function toResult(value: unknown): RouteAdjustmentResult {
  const wire = record(value);
  return {
    deviceEventId: text(wire.device_event_id),
    quantityFlowId: int(wire.quantity_flow_id),
    partNumber: text(wire.part_number),
    assignedRouteId: int(wire.assigned_route_id),
    keptThroughSequence: int(wire.kept_through_sequence),
    reason: text(wire.reason),
    steps: list(wire.steps).map(toResultStep),
  };
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

/**
 * The active Planned Quantity Flows of a PN with their whole assigned
 * route, newest first. A read — an advisory snapshot; the write is
 * judged again by the server under its locks.
 */
export async function loadAssignedRoutes(
  pn: string,
): Promise<AdjustableFlow[]> {
  const params = new URLSearchParams({ part_number: pn });
  const wire = await apiRequest<unknown>(
    `/api/tracking/assigned-routes?${params.toString()}`,
  );
  return list(record(wire).flows).map(toAdjustableFlow);
}

/**
 * Replace the future steps of one flow's assigned route. Resolves only
 * when the server confirmed the write (201 fresh, 200 replay of the
 * same `device_event_id` and body).
 */
export async function adjustAssignedRoute(
  flowId: number,
  input: RouteAdjustmentInput,
): Promise<RouteAdjustmentResult> {
  const data = await apiRequest<unknown>(
    `/api/quantity-flows/${flowId}/route-adjustments`,
    {
      method: 'POST',
      body: {
        device_event_id: input.deviceEventId,
        expected_future_step_ids: input.expectedFutureStepIds,
        steps: input.steps.map((step) => ({
          area_id: step.areaId,
          operation_id: step.operationId,
          expected_duration: step.expectedDuration,
          preferred_machine_id: step.preferredMachineId,
          instructions: step.instructions,
        })),
        reason: input.reason,
      },
    },
  );
  return toResult(data);
}

/** The route changed since it was read (409 `route_changed`). */
export function isRouteChanged(error: unknown): boolean {
  return refusalFlag(error, 'route_changed');
}
