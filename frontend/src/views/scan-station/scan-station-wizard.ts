// Scan Station wizard helpers (Phase 5) — non-component logic shared by
// the real Scan Station view and the development-only mock preview:
// notice timing, guidance markers, Work Order label parsing,
// quantity-step key handling, and quantity validation. Production-safe
// — no mock imports.

import type { FlowInArea, MachineRef } from '../../api/scan-station';
import { applyQuantityKey } from '../../components/quantity-input';

export const NOTICE_OK_MS = 4000;
export const NOTICE_WARN_MS = 8000;
export const GUIDE_MARKERS: Record<
  'info' | 'warn' | 'action' | 'error',
  string
> = {
  info: 'ℹ',
  warn: '⚠',
  action: '›',
  error: '✕',
};
/**
 * Split a mock Work Order context label (`WO 007003 · Turning`,
 * `WO — · Turning · MODIFY`) into the WO Number value and its trailing
 * context segments — the ONE place this display string is taken apart,
 * so recaps can emphasize the number and chip the Operation instead of
 * echoing the raw string.
 */
export function parseWorkOrderLabel(label: string): {
  number: string;
  segments: string[];
} {
  const [head, ...segments] = label.split(' · ');
  return { number: head.replace(/^WO\s*/, '') || '—', segments };
}
/**
 * Central physical-key handling for quantity steps: Enter advances (to
 * the next step — never directly to a write), Escape cancels
 * (ModalDialog), and while the quantity input itself is NOT focused
 * the editing keys fall back to end-of-value editing (digits append,
 * Backspace removes the last digit, Delete clears, Space is ignored).
 * While the quantity input IS focused, the shared QuantityKeypad owns
 * cursor-aware editing (selection replacement / caret insertion — the
 * same transitions, see components/quantity-input.ts) and consumes
 * those keys before they reach this handler. Keys typed into other
 * text fields (reason, notes, scan-within-dialog) are left alone.
 */
export function quantityKeyHandler(
  value: string,
  onChange: (next: string) => void,
  onAdvance: () => void,
) {
  return (event: React.KeyboardEvent) => {
    const target = event.target;
    // Focusable dialog buttons (Cancel, Back, selection buttons) keep
    // their native keyboard activation. Virtual keypad buttons are
    // type="button", non-focusable (tabIndex -1) and never take focus
    // on click, so a previously clicked keypad button can never
    // reclaim Enter or Space.
    if (target instanceof HTMLButtonElement) return;
    if (event.key === 'Enter') {
      // Enter always means "advance" for the quantity step.
      event.preventDefault();
      onAdvance();
      return;
    }
    const inOtherField =
      (target instanceof HTMLInputElement &&
        !target.classList.contains('qtydisplay')) ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement;
    if (inOtherField) return;
    if (
      target instanceof HTMLInputElement &&
      target.classList.contains('qtydisplay')
    ) {
      // The focused quantity input already handled (and consumed) its
      // cursor-aware editing keys; anything still bubbling from it is
      // intentionally left alone.
      return;
    }
    if (event.key === ' ') {
      event.preventDefault(); // Space is ignored
      return;
    }
    const next = applyQuantityKey(value, event.key);
    if (next !== null) {
      event.preventDefault();
      onChange(next);
    }
  };
}
/**
 * Enter handling for non-quantity steps (settings/confirmation):
 * Enter performs the given action unless focus sits on a button or in
 * a text-entry control (those keep their native behavior).
 */
export function enterKeyHandler(onEnter: () => void) {
  return (event: React.KeyboardEvent) => {
    const target = event.target;
    if (
      target instanceof HTMLButtonElement ||
      target instanceof HTMLInputElement ||
      target instanceof HTMLSelectElement ||
      target instanceof HTMLTextAreaElement
    ) {
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      onEnter();
    }
  };
}
export function quantityValidation(
  parsed: number,
  max: number,
  overMessage: string,
): { tone: 'action' | 'error'; text: string } | null {
  if (parsed > max) return { tone: 'error', text: overMessage };
  if (parsed < 1) {
    return {
      tone: 'action',
      text: `Enter a quantity between 1 and ${max}.`,
    };
  }
  return null;
}

/**
 * Quantity validation of the production wizards (GUI_DESIGN §4.8,
 * Phase 8): any whole number from 1 to MAX is valid — a smaller
 * quantity acts on that part only and the SERVER splits the flow inside
 * the same command; 0 and anything above MAX are refused before
 * submission. The client never splits anything itself.
 */
export function quantityValid(parsed: number, max: number): boolean {
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= max;
}

/** How a portion of quantity reads to the operator (Phase 8 Combine):
 * its holding state, and where — never an internal flow id. */
export function portionState(
  flow: FlowInArea,
  machines: Pick<MachineRef, 'id' | 'name'>[],
): string {
  switch (flow.processingState) {
    case 'QUEUED':
      return 'Queued';
    case 'PROCESSING':
      return flow.operation.isExternal
        ? 'External processing'
        : 'In processing';
    case 'ON_MACHINE': {
      const machine = machines.find((item) => item.id === flow.machineId);
      return machine ? `On ${machine.name}` : 'On Machine';
    }
    case 'READY_TO_TRANSFER':
      return 'Finished — ready to move';
  }
}

/** `6 pcs · In processing · Milling` — the operator's view of one portion. */
export function portionLabel(
  flow: FlowInArea,
  machines: Pick<MachineRef, 'id' | 'name'>[],
): string {
  return `${flow.quantity} pcs · ${portionState(flow, machines)} · ${operationLabel(
    flow.operation,
  )}`;
}

/** Operator-facing Operation label: the name when configured, else the code. */
export function operationLabel(operation: {
  code: string;
  name: string | null;
}): string {
  return operation.name ?? operation.code;
}
