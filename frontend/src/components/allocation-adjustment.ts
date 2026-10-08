// Allocation adjustment presentation logic (Phase 14 slice 5 —
// GUI_DESIGN §11.6; PROJECT_PROFILE §8.12, §18).
//
// The pure rules and copy of the `Adjust WO Allocation` dialog: the
// routine limit of `Allocate from stock` (never beyond the line's
// remaining demand, never beyond the available stocked quantity), the
// range of the authorized beyond-demand correction (more than the
// remaining demand, never above the available stock), whether a
// reversal reopens a completed Work Order, the early field errors and
// the committed notices. The server is authoritative and judges every
// write again under its locks — these only keep obviously invalid
// entries from travelling.
//
// Pure: no React, no framework imports.

import type {
  ContextAllocation,
  ContextLine,
  ManagementAllocationResult,
} from '../api/management-allocations';
import { formatTimestampShort } from '../views/dates';

// ---------------------------------------------------------------------------
// Copy
// ---------------------------------------------------------------------------

export const NO_OPEN_DEMAND =
  "No open Work Order Demand for this PN. A completed Work Order's allocation is adjusted from its Work Order Details.";

export const FULLY_ALLOCATED_NOTE =
  'This demand line is fully allocated. Allocating more is a correction — use Allocate beyond demand….';

export const BEYOND_DEMAND_WARNING =
  'This allocates more than the Work Order Demand requests. Use it only to record a correction — routine allocation never exceeds the remaining demand. It is recorded with your name and the reason, and it can be reversed.';

export const OUTCOME_UNKNOWN =
  'The PartFlow server did not answer, so this may or may not be recorded. Submit again to finish — PartFlow records it only once.';

export const OUTCOME_UNKNOWN_AFTER_CLOSE =
  'The last allocation change may have been recorded. Check the allocation before trying again.';

export const SIGN_IN_AGAIN =
  'Sign in again, then submit again. PartFlow records this only once.';

export const MAY_BE_RECORDED =
  'It may already be recorded; close this dialog and open it again to check.';

export const SAVE_DEMAND_FIRST =
  'Save or discard the demand changes first — allocation works on the saved demand.';

export const QUANTITY_REQUIRED =
  'Enter a whole number of pieces greater than 0.';

// ---------------------------------------------------------------------------
// Rules
// ---------------------------------------------------------------------------

/** The most `Allocate from stock` may allocate to the line. */
export function routineLimit(line: ContextLine, available: number): number {
  return Math.max(0, Math.min(line.remainingShortage, available));
}

/** The quantities a beyond-demand correction of the line may record,
 * or null when the available stock does not exceed its remaining
 * demand (nothing beyond demand can be allocated). */
export function beyondRange(
  line: ContextLine,
  available: number,
): { min: number; max: number } | null {
  return available > line.remainingShortage
    ? { min: line.remainingShortage + 1, max: available }
    : null;
}

/** Reversing the allocation takes the completed Work Order of the line
 * back to Open (the line falls below its requested quantity). */
export function reversalReopens(
  line: ContextLine,
  allocation: ContextAllocation,
): boolean {
  return (
    line.workOrderCompleted &&
    line.allocatedQuantity - allocation.quantity < line.requestedQuantity
  );
}

/** A positive whole number of pieces, or null. */
export function parseQuantity(raw: string): number | null {
  const text = raw.trim();
  if (!/^\d+$/.test(text)) return null;
  const value = Number.parseInt(text, 10);
  return value >= 1 && Number.isSafeInteger(value) ? value : null;
}

/** The early error of an `Allocate from stock` quantity, or null. */
export function allocateQtyError(
  raw: string,
  line: ContextLine,
  available: number,
): string | null {
  const quantity = parseQuantity(raw);
  if (quantity === null) return QUANTITY_REQUIRED;
  if (quantity > line.remainingShortage) {
    return beyondRange(line, available) !== null
      ? `Only ${line.remainingShortage} pcs are still needed on this line. To allocate more, use Allocate beyond demand….`
      : `Only ${line.remainingShortage} pcs are still needed on this line.`;
  }
  if (quantity > available) return availableError(available);
  return null;
}

/** The early error of a beyond-demand correction quantity, or null. */
export function beyondQtyError(
  raw: string,
  line: ContextLine,
  available: number,
): string | null {
  const quantity = parseQuantity(raw);
  if (quantity === null) return QUANTITY_REQUIRED;
  if (quantity <= line.remainingShortage) {
    return `A beyond-demand correction must be more than the ${line.remainingShortage} pcs still needed on this line.`;
  }
  if (quantity > available) return availableError(available);
  return null;
}

function availableError(available: number): string {
  return `Only ${Math.max(available, 0)} pcs are available in stock.`;
}

/** The mandatory reason of a correction or a reversal, or null. */
export function reasonError(
  raw: string,
  kind: 'correction' | 'reversal',
): string | null {
  if (raw.trim() !== '') return null;
  return kind === 'correction'
    ? 'Enter the reason for this correction.'
    : 'Enter the reason for this reversal.';
}

// ---------------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------------

/** The Work Order Number, or `—` for an internal Work Order. */
export function workOrderLabel(workOrderNumber: string | null): string {
  return workOrderNumber ?? '—';
}

export function sourceLabel(source: 'STOCKROOM' | 'MANAGEMENT'): string {
  return source === 'STOCKROOM' ? 'Stockroom' : 'Management';
}

/** The local Tracking history timestamp (`Jul 24 08:12`). */
export function allocationTimestamp(iso: string): string {
  return formatTimestampShort(iso);
}

/** The line after a beyond-demand correction of `quantity` pcs. */
export function afterCorrectionLine(
  line: ContextLine,
  quantity: number,
): string {
  const after = line.allocatedQuantity + quantity;
  return `After this correction: ${after} of ${line.requestedQuantity} pcs allocated (${after - line.requestedQuantity} pcs beyond demand).`;
}

/**
 * The notice of a committed command: what was recorded, then the
 * completion effect the server derived (a Work Order completed or
 * reopened). `workOrderNumberOf` names a Work Order by its id.
 */
export function committedNotice(
  result: ManagementAllocationResult,
  target: { workOrderNumber: string | null },
  workOrderNumberOf: (workOrderId: number) => string | null,
): string {
  const quantity = result.allocationQuantity;
  const head =
    result.kind === 'REVERSE_ALLOCATION'
      ? `✓ Allocation of ${quantity} pcs reversed — returned to available stock.`
      : result.kind === 'ALLOCATE_BEYOND_DEMAND'
        ? `✓ ${quantity} pcs of ${result.partNumber} allocated beyond demand to Work Order ${workOrderLabel(target.workOrderNumber)} — correction recorded.`
        : `✓ ${quantity} pcs of ${result.partNumber} allocated to Work Order ${workOrderLabel(target.workOrderNumber)}.`;
  const completed = result.completedWorkOrderIds.map(
    (id) => ` Work Order ${workOrderLabel(workOrderNumberOf(id))} is complete.`,
  );
  const reopened = result.reopenedWorkOrderIds.map(
    (id) => ` Work Order ${workOrderLabel(workOrderNumberOf(id))} reopened.`,
  );
  return [head, ...completed, ...reopened].join('');
}
