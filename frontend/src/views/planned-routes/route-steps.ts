// Route step editing logic (GUI_DESIGN §13.2) — shared by Management →
// Planned Routes and Tracking → Edit assigned Route.
//
// The pure rules behind the step rows: which Areas, Operations and
// Machines a step may offer, how a stored value that is no longer
// offered stays visible as `(unavailable)`, the advisory estimated
// time, the per-step client validation and the normalized comparison
// form used for dirty tracking. The server validates every step of
// every save; these only keep obviously invalid entries from
// travelling.
//
// Pure: no React, no framework imports.

import { isoDurationToMinutes, minutesToIsoDuration } from '../../api/duration';
import type { Area, Operation } from '../../api/environment';
import type { Machine } from '../../api/machines';
import type { RouteStepInput } from '../../api/route-templates';
import { operationLabel } from '../area-presentation';
import { formatEstimate, parseEstimate } from './route-duration';

/** The configuration a route step editor offers its choices from. */
export interface Catalog {
  areas: Area[];
  operations: Operation[];
  machines: Machine[];
}

/** One step as the editor holds it. */
export interface EditableStep {
  /** Render identity (stable across reorders). */
  key: number;
  areaId: number;
  operationId: number | null;
  /** The step was stored without an Operation (legacy) and still has
   * none chosen — rendered `—`, never `Select an Operation…`. */
  legacyNullOperation: boolean;
  durationText: string;
  /** The stored ISO value, sent verbatim while its text is unchanged. */
  storedDuration: string | null;
  storedDurationText: string;
  preferredMachineId: number | null;
  instructions: string;
}

const byId = <T extends { id: number }>(a: T, b: T): number => a.id - b.id;

export function findById<T extends { id: number }>(
  items: readonly T[],
  id: number | null,
): T | undefined {
  return id === null ? undefined : items.find((item) => item.id === id);
}

/** The Area's active Operations, by id. */
export function offeredOperations(
  catalog: Catalog,
  areaId: number,
): Operation[] {
  return catalog.operations
    .filter((op) => op.areaId === areaId && op.isActive)
    .sort(byId);
}

/** The Area's non-retired Machines, by id. */
export function offeredMachines(catalog: Catalog, areaId: number): Machine[] {
  return catalog.machines
    .filter((m) => m.areaId === areaId && m.retiredOn === undefined)
    .sort(byId);
}

export function activeAreas(catalog: Catalog): Area[] {
  return catalog.areas.filter((area) => area.isActive);
}

/** The advisory estimated time of a stored ISO duration, as edited. */
export function estimateText(iso: string | null): string {
  if (iso === null) return '';
  const minutes = isoDurationToMinutes(iso);
  return minutes === null ? iso : formatEstimate(minutes);
}

/** One stored step as the editor holds it. */
export function editableStep(step: RouteStepInput, key: number): EditableStep {
  const text = estimateText(step.expectedDuration);
  return {
    key,
    areaId: step.areaId,
    operationId: step.operationId,
    legacyNullOperation: step.operationId === null,
    durationText: text,
    storedDuration: step.expectedDuration,
    storedDurationText: text,
    preferredMachineId: step.preferredMachineId,
    instructions: step.instructions ?? '',
  };
}

/** The ISO duration a step saves, or false when its text is invalid:
 * unchanged text sends the stored value verbatim (no rounding of a
 * legacy sub-minute value), cleared text sends none. */
export function stepDuration(step: EditableStep): string | null | false {
  const text = step.durationText.trim();
  if (text === step.storedDurationText) return step.storedDuration;
  if (!text) return null;
  const minutes = parseEstimate(text);
  return minutes === null ? false : minutesToIsoDuration(minutes);
}

/** The write request form of one edited step. */
export function stepInput(step: EditableStep): RouteStepInput {
  const duration = stepDuration(step);
  return {
    areaId: step.areaId,
    operationId: step.operationId,
    expectedDuration: duration === false ? null : duration,
    preferredMachineId: step.preferredMachineId,
    instructions: step.instructions.trim() || null,
  };
}

/** Normalized comparison form of an edited step list (dirty tracking). */
export function stepsKey(steps: EditableStep[]): string {
  return JSON.stringify(
    steps.map((step) => {
      const duration = stepDuration(step);
      return [
        step.areaId,
        step.operationId,
        duration === false ? `invalid:${step.durationText.trim()}` : duration,
        step.preferredMachineId,
        step.instructions.trim(),
      ];
    }),
  );
}

export function areaUnavailable(catalog: Catalog, step: EditableStep): boolean {
  return !findById(catalog.areas, step.areaId)?.isActive;
}

export function operationUnavailable(
  catalog: Catalog,
  step: EditableStep,
): boolean {
  if (step.operationId === null) return false;
  const operation = findById(catalog.operations, step.operationId);
  return !operation || !operation.isActive || operation.areaId !== step.areaId;
}

export function machineUnavailable(
  catalog: Catalog,
  step: EditableStep,
): boolean {
  if (step.preferredMachineId === null) return false;
  const machine = findById(catalog.machines, step.preferredMachineId);
  return (
    !machine ||
    machine.retiredOn !== undefined ||
    machine.areaId !== step.areaId
  );
}

/**
 * The first per-step problem of an edited step list, or null. Steps
 * are numbered from `firstNumber` (a route's absolute step numbers).
 * Client validation only — it blocks the write; the server stays the
 * authority.
 */
export function validateSteps(
  steps: EditableStep[],
  catalog: Catalog,
  firstNumber: number,
): string | null {
  if (steps.some((step) => step.operationId === null)) {
    return 'Every step needs an Operation.';
  }
  for (const [index, step] of steps.entries()) {
    const n = firstNumber + index;
    if (areaUnavailable(catalog, step)) {
      return `Step ${n}: choose an available Area.`;
    }
    if (operationUnavailable(catalog, step)) {
      return `Step ${n}: choose an available Operation.`;
    }
    if (machineUnavailable(catalog, step)) {
      return `Step ${n}: choose an available Machine.`;
    }
    if (stepDuration(step) === false) {
      return `Step ${n}: enter the estimated time like 45m, 4h or 2d 03h.`;
    }
  }
  return null;
}

/** A fresh step in `area` with its first active Operation. */
export function newStep(
  catalog: Catalog,
  areaId: number,
  key: number,
): EditableStep {
  return {
    key,
    areaId,
    operationId: offeredOperations(catalog, areaId)[0]?.id ?? null,
    legacyNullOperation: false,
    durationText: '',
    storedDuration: null,
    storedDurationText: '',
    preferredMachineId: null,
    instructions: '',
  };
}

export function areaOptionLabel(catalog: Catalog, areaId: number): string {
  const area = findById(catalog.areas, areaId);
  return area ? `${area.name} (unavailable)` : `Area ${areaId} (unavailable)`;
}

export function operationOptionLabel(
  catalog: Catalog,
  operationId: number,
): string {
  const operation = findById(catalog.operations, operationId);
  return operation
    ? `${operationLabel(operation)} (unavailable)`
    : `Operation ${operationId} (unavailable)`;
}

export function machineOptionLabel(
  catalog: Catalog,
  machineId: number,
): string {
  const machine = findById(catalog.machines, machineId);
  if (!machine) return `Machine ${machineId} (unavailable)`;
  return machine.retiredOn !== undefined
    ? `${machine.name} — retired (unavailable)`
    : `${machine.name} (unavailable)`;
}
