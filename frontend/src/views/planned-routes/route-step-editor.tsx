import './route-step-editor.css';

import { useState } from 'react';
import type { DragEvent } from 'react';

import { operationLabel } from '../area-presentation';
import type { Catalog, EditableStep } from './route-steps';
import {
  activeAreas,
  areaOptionLabel,
  areaUnavailable,
  findById,
  machineOptionLabel,
  machineUnavailable,
  newStep,
  offeredMachines,
  offeredOperations,
  operationOptionLabel,
  operationUnavailable,
} from './route-steps';

/**
 * The editable route step rows (GUI_DESIGN §13.2), shared by Management
 * → Planned Routes and Tracking → Edit assigned Route: the column head
 * row, one row per step (Area, Operation from the Area's active
 * Operations, advisory Est. time, optional preferred Machine from the
 * Area's active Machines, optional instructions) and `+ Add step`.
 * Steps reorder by drag-and-drop with the explicit up/down controls
 * kept as the keyboard and touch path — drag is never the only way.
 *
 * Steps are numbered from `firstNumber` (an assigned route's future
 * steps continue its numbering); ✕ is disabled at `minSteps`. A new
 * step starts in the last row's Area, else in `fallbackAreaId`, else
 * in the first active Area. `disabled` makes every control read-only.
 */
export function RouteStepList({
  catalog,
  steps,
  onChange,
  firstNumber = 1,
  minSteps = 1,
  fallbackAreaId,
  disabled = false,
}: {
  catalog: Catalog;
  steps: EditableStep[];
  /** Applies one change to the current list (a state updater). */
  onChange: (update: (steps: EditableStep[]) => EditableStep[]) => void;
  firstNumber?: number;
  minSteps?: number;
  fallbackAreaId?: number;
  disabled?: boolean;
}) {
  const [dragKey, setDragKey] = useState<number | null>(null);

  const setStep = (key: number, change: Partial<EditableStep>) =>
    onChange((list) =>
      list.map((step) => (step.key === key ? { ...step, ...change } : step)),
    );

  /** Area change keeps the Operation / Machine only when the new Area
   * still offers them; otherwise its first active Operation and no
   * preferred Machine. */
  const changeArea = (key: number, areaId: number) =>
    onChange((list) =>
      list.map((step) => {
        if (step.key !== key) return step;
        const operations = offeredOperations(catalog, areaId);
        const machines = offeredMachines(catalog, areaId);
        return {
          ...step,
          areaId,
          operationId: operations.some((op) => op.id === step.operationId)
            ? step.operationId
            : (operations[0]?.id ?? null),
          legacyNullOperation: false,
          preferredMachineId: machines.some(
            (m) => m.id === step.preferredMachineId,
          )
            ? step.preferredMachineId
            : null,
        };
      }),
    );

  const addStep = () =>
    onChange((list) => {
      const last = list[list.length - 1];
      const fallback =
        fallbackAreaId !== undefined &&
        findById(catalog.areas, fallbackAreaId)?.isActive
          ? fallbackAreaId
          : undefined;
      const areaId =
        last && !areaUnavailable(catalog, last)
          ? last.areaId
          : (fallback ?? activeAreas(catalog)[0]?.id);
      if (areaId === undefined) return list;
      const key = 1 + Math.max(-1, ...list.map((step) => step.key));
      return [...list, newStep(catalog, areaId, key)];
    });

  const move = (index: number, delta: -1 | 1) =>
    onChange((list) => {
      const target = index + delta;
      if (target < 0 || target >= list.length) return list;
      const next = [...list];
      const [step] = next.splice(index, 1);
      next.splice(target, 0, step);
      return next;
    });

  const handleDrop = (targetKey: number) => {
    if (dragKey === null || dragKey === targetKey) return;
    onChange((list) => {
      const from = list.findIndex((step) => step.key === dragKey);
      const to = list.findIndex((step) => step.key === targetKey);
      if (from < 0 || to < 0) return list;
      const next = [...list];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  };

  return (
    <>
      {/* Column labels for the step fields — the bottom-border-only
          instruction/duration inputs stay labelled (v15). */}
      <div className="rt-stephead" aria-hidden="true">
        <span />
        <span />
        <span>Area</span>
        <span>Operation</span>
        <span>Est. time</span>
        <span>Preferred Machine</span>
        <span />
      </div>
      <div className="rt-steplist">
        {steps.map((step, index) => {
          const n = firstNumber + index;
          const operations = offeredOperations(catalog, step.areaId);
          const machines = offeredMachines(catalog, step.areaId);
          return (
            // Drag-and-drop reorder (HTML5 DnD, same pattern as the
            // Priority list) with ↑/↓ as the keyboard/touch path —
            // drag is never the only way to reorder.
            <div
              className={`rt-steprow${dragKey === step.key ? ' dragging' : ''}`}
              key={step.key}
              draggable={!disabled}
              onDragStart={() => setDragKey(step.key)}
              onDragEnd={() => setDragKey(null)}
              onDragOver={(event: DragEvent) => event.preventDefault()}
              onDrop={(event: DragEvent) => {
                event.preventDefault();
                if (!disabled) handleDrop(step.key);
              }}
            >
              <span className="grip" aria-hidden="true">
                ⠿
              </span>
              <span className="idx">{n}</span>
              <select
                aria-label={`Step ${n} Area`}
                value={String(step.areaId)}
                disabled={disabled}
                onChange={(e) => changeArea(step.key, Number(e.target.value))}
              >
                {areaUnavailable(catalog, step) ? (
                  // A stored Area that is no longer offered stays
                  // visible as an explicit unavailable value — never
                  // silently replaced.
                  <option value={String(step.areaId)}>
                    {areaOptionLabel(catalog, step.areaId)}
                  </option>
                ) : null}
                {activeAreas(catalog).map((area) => (
                  <option key={area.id} value={String(area.id)}>
                    {area.name}
                  </option>
                ))}
              </select>
              <select
                aria-label={`Step ${n} Operation`}
                value={
                  step.operationId === null ? '' : String(step.operationId)
                }
                disabled={disabled}
                onChange={(e) => {
                  if (e.target.value) {
                    setStep(step.key, {
                      operationId: Number(e.target.value),
                      legacyNullOperation: false,
                    });
                  }
                }}
              >
                {step.operationId === null ? (
                  <option value="" disabled>
                    {step.legacyNullOperation ? '—' : 'Select an Operation…'}
                  </option>
                ) : null}
                {step.operationId !== null &&
                operationUnavailable(catalog, step) ? (
                  <option value={String(step.operationId)}>
                    {operationOptionLabel(catalog, step.operationId)}
                  </option>
                ) : null}
                {operations.map((operation) => (
                  <option key={operation.id} value={String(operation.id)}>
                    {operationLabel(operation)}
                  </option>
                ))}
              </select>
              <input
                className="uline"
                aria-label={`Step ${n} expected duration`}
                placeholder="e.g. 4h"
                value={step.durationText}
                readOnly={disabled}
                onChange={(e) =>
                  setStep(step.key, { durationText: e.target.value })
                }
              />
              <select
                aria-label={`Step ${n} preferred Machine`}
                value={
                  step.preferredMachineId === null
                    ? ''
                    : String(step.preferredMachineId)
                }
                disabled={disabled}
                onChange={(e) =>
                  setStep(step.key, {
                    preferredMachineId: e.target.value
                      ? Number(e.target.value)
                      : null,
                  })
                }
              >
                <option value="">— no preferred Machine</option>
                {step.preferredMachineId !== null &&
                machineUnavailable(catalog, step) ? (
                  // A retired / moved / missing Machine stays visible
                  // as an explicit unavailable value — never silently
                  // cleared; choosing another value replaces it.
                  <option value={String(step.preferredMachineId)}>
                    {machineOptionLabel(catalog, step.preferredMachineId)}
                  </option>
                ) : null}
                {machines.map((machine) => (
                  <option key={machine.id} value={String(machine.id)}>
                    {machine.name}
                  </option>
                ))}
              </select>
              <span className="steppbtns">
                <button
                  aria-label={`Move step ${n} up`}
                  disabled={disabled || index === 0}
                  onClick={() => move(index, -1)}
                >
                  ↑
                </button>
                <button
                  aria-label={`Move step ${n} down`}
                  disabled={disabled || index === steps.length - 1}
                  onClick={() => move(index, 1)}
                >
                  ↓
                </button>
                <button
                  aria-label={`Remove step ${n}`}
                  disabled={disabled || steps.length <= minSteps}
                  onClick={() =>
                    onChange((list) => list.filter((s) => s.key !== step.key))
                  }
                >
                  ✕
                </button>
              </span>
              <label className="instrwrap">
                <span className="flbl">Instructions</span>
                <input
                  className="instr uline"
                  placeholder="optional"
                  value={step.instructions}
                  readOnly={disabled}
                  onChange={(e) =>
                    setStep(step.key, { instructions: e.target.value })
                  }
                />
              </label>
            </div>
          );
        })}
      </div>
      <button className="rt-addstep" disabled={disabled} onClick={addStep}>
        + Add step
      </button>
    </>
  );
}
