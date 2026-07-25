// Shared demand-line draft model for the Work Orders view: the New
// Work Order dialog and the OPEN Work Order detail edit the same kind
// of line drafts.
//
// Phase 2 scope: this is PRESENTATION-side mock validation and draft
// bookkeeping against local mock state only. The canonical business
// rules (Work Order Intake workflow, Work Order Demand removal) are
// owned by
// PROJECT_PROFILE §13 and are enforced transactionally in the
// Application/Domain layer when the Phase 4 backend slice exists.

import { MOCK_PN_BARCODES } from '../../mocks/work-orders';
import type { MockWorkOrderLine, RequestType } from '../view-models';

export interface DemandLineDraft {
  id: number;
  /** null while a manual row still needs its PN lookup / inline create. */
  pn: string | null;
  barcodeNote: string;
  isNewPn: boolean;
  type: RequestType;
  qty: string;
  /** ISO `YYYY-MM-DD` (or '' while missing). */
  due: string;
  /** True once the line's due date was edited away from the WO default. */
  dueTouched: boolean;
  job: string;
  notes: string;
  /** True when the line exists in saved mock state (not an unsaved draft). */
  saved: boolean;
  /** True when production quantity was released for this WorkOrderDemand. */
  released: boolean;
  releasable: boolean;
  statusLabel: string;
}

export type LineField = 'pn' | 'qty' | 'due';

export interface LineError {
  lineId: number;
  field: LineField;
  message: string;
}

export const RELEASED_REMOVE_EXPLANATION =
  'Cannot remove: production quantity has already been released.';

let nextDraftId = 1;

export function createDraftLine(
  init: Partial<DemandLineDraft> & { due: string },
): DemandLineDraft {
  return {
    id: nextDraftId++,
    pn: null,
    barcodeNote: 'PN lookup — an unknown PN is created inline with its barcode',
    isNewPn: false,
    type: 'NEW',
    qty: '',
    dueTouched: false,
    job: '',
    notes: '',
    saved: false,
    released: false,
    releasable: false,
    statusLabel: 'Draft (unsaved)',
    ...init,
  };
}

/** Load a saved mock line into an editable draft. */
export function draftFromSavedLine(
  line: MockWorkOrderLine,
  workOrderDue: string,
): DemandLineDraft {
  const saved = line.statusClass !== 'invalid';
  return createDraftLine({
    pn: line.pn || null,
    barcodeNote: line.barcode,
    isNewPn: line.barcode.startsWith('new PN'),
    type: line.type,
    qty: line.qty > 0 ? String(line.qty) : '',
    due: line.due,
    // A line still holding the WO due date follows later WO-due edits;
    // a line with its own date keeps it (GUI_DESIGN §11).
    dueTouched: line.due !== workOrderDue,
    job: line.job === '—' ? '' : line.job,
    notes: line.notes ?? '',
    saved,
    released: line.statusClass === 'released',
    releasable: line.releasable ?? false,
    statusLabel: saved ? line.status : 'Draft (unsaved)',
  });
}

export type ScanResult =
  | { kind: 'invalid'; barcode: string }
  | { kind: 'duplicate'; lineId: number; pn: string; released: boolean }
  | { kind: 'new'; pn: string; barcode: string };

/** Resolve a PN barcode against the mock catalog and the current lines. */
export function processScan(
  value: string,
  lines: readonly DemandLineDraft[],
): ScanResult | null {
  const barcode = value.trim().toUpperCase();
  if (!barcode) return null;
  const pn = MOCK_PN_BARCODES[barcode];
  if (!pn) return { kind: 'invalid', barcode };
  const duplicate = lines.find((line) => line.pn === pn);
  if (duplicate) {
    return {
      kind: 'duplicate',
      lineId: duplicate.id,
      pn,
      released: duplicate.released,
    };
  }
  return { kind: 'new', pn, barcode };
}

/** The WO due date is the default: update lines still holding it. */
export function applyWorkOrderDueDateChange(
  lines: readonly DemandLineDraft[],
  newDue: string,
): DemandLineDraft[] {
  return lines.map((line) =>
    line.dueTouched || line.released ? line : { ...line, due: newDue },
  );
}

export function isPositiveInteger(raw: string): boolean {
  return /^\d+$/.test(raw.trim()) && Number.parseInt(raw, 10) >= 1;
}

/**
 * Mock validation for saving demand: every line needs a PN, a positive
 * whole quantity and a due date; duplicate PNs are rejected. Incomplete
 * rows are never silently filtered out.
 */
export function validateDemandLines(
  lines: readonly DemandLineDraft[],
): LineError[] {
  const errors: LineError[] = [];
  const seen = new Map<string, number>();
  for (const line of lines) {
    if (line.released) continue; // read-only; already valid in mock state
    if (!line.pn) {
      errors.push({
        lineId: line.id,
        field: 'pn',
        message: 'PN is required — look up or create the PartNumber',
      });
    } else if (seen.has(line.pn)) {
      errors.push({
        lineId: line.id,
        field: 'pn',
        message: `duplicate PN — ${line.pn} is already on this Work Order`,
      });
    } else {
      seen.set(line.pn, line.id);
    }
    if (!isPositiveInteger(line.qty)) {
      errors.push({
        lineId: line.id,
        field: 'qty',
        message: 'quantity must be a positive whole number',
      });
    }
    if (!line.due) {
      errors.push({
        lineId: line.id,
        field: 'due',
        message: 'due date is required',
      });
    }
  }
  return errors;
}

export type RemoveRule = 'draft' | 'confirm' | 'blocked';

/**
 * Phase 2 mirror of the canonical WorkOrderDemand removal rule
 * (PROJECT_PROFILE §13): an unsaved draft is removed immediately, a
 * saved line with no released production quantity needs explicit
 * confirmation, and a line with released quantity can never be removed
 * from Work Orders.
 */
export function lineRemoveRule(line: DemandLineDraft): RemoveRule {
  if (line.released) return 'blocked';
  return line.saved ? 'confirm' : 'draft';
}

/** Convert validated drafts back into saved mock lines. */
export function draftsToSavedLines(
  lines: readonly DemandLineDraft[],
): MockWorkOrderLine[] {
  return lines.map((line) => ({
    pn: line.pn ?? '—',
    barcode: line.barcodeNote,
    type: line.type,
    qty: Number.parseInt(line.qty, 10),
    due: line.due,
    job: line.job || '—',
    notes: line.notes || undefined,
    status: line.released ? line.statusLabel : 'Saved',
    statusClass: line.released ? ('released' as const) : ('saved' as const),
    releasable: line.released ? undefined : true,
  }));
}

/** PN preview text for the Work Order list row. */
export function linesPreview(lines: readonly MockWorkOrderLine[]): string {
  const pns = lines.map((line) => line.pn).filter(Boolean);
  const head = pns.slice(0, 2).join(' · ');
  return pns.length > 2 ? `${head} · ${pns.length - 2} more` : head;
}
