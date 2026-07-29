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

import { catalogPartNumber } from '../../mocks/work-orders';
import { parseScan, pnKey } from '../scan-station/barcode';
import type { MockWorkOrderLine, RequestType } from '../view-models';

export interface DemandLineDraft {
  id: number;
  /** null while a manual row still needs its PN lookup / inline create. */
  pn: string | null;
  barcodeNote: string;
  isNewPn: boolean;
  type: RequestType;
  qty: string;
  /**
   * ISO `YYYY-MM-DD`, or '' when the line has no due date. A blank due
   * date is valid data — it never blocks saving.
   */
  due: string;
  /**
   * True once the line's due date was edited away from the WO default —
   * including an explicit `No due date` choice. Only untouched lines
   * follow later WO due-date changes.
   */
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
  workOrderDue: string | null,
): DemandLineDraft {
  const saved = line.statusClass !== 'invalid';
  return createDraftLine({
    pn: line.pn || null,
    barcodeNote: line.barcode,
    isNewPn: line.barcode.startsWith('new PN'),
    type: line.type,
    qty: line.qty > 0 ? String(line.qty) : '',
    due: line.due ?? '',
    // A line still holding the WO due date follows later WO-due edits;
    // a line with its own date (or explicit No due date) keeps it.
    dueTouched: (line.due ?? '') !== (workOrderDue ?? ''),
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
  | { kind: 'new'; pn: string; barcode: string; isNewPn: boolean };

/**
 * Resolve a scanned PN barcode. `PF:PN:<part-number>` carries the PN
 * itself: the entire non-empty suffix is the PN (no format validation,
 * no opaque id mapping). A PN outside the catalog is create-on-first-
 * use; identity is case-insensitive and an existing PN keeps its
 * stored casing. Non-PN barcodes never add demand lines.
 */
export function processScan(
  value: string,
  lines: readonly DemandLineDraft[],
): ScanResult | null {
  const parsed = parseScan(value);
  if (parsed.kind === 'empty') return null;
  if (parsed.kind !== 'pn') {
    return { kind: 'invalid', barcode: value.trim() };
  }
  const known = catalogPartNumber(parsed.pn);
  const pn = known?.pn ?? parsed.pn;
  const duplicate = lines.find(
    (line) => line.pn !== null && pnKey(line.pn) === pnKey(pn),
  );
  if (duplicate) {
    return {
      kind: 'duplicate',
      lineId: duplicate.id,
      pn: duplicate.pn ?? pn,
      released: duplicate.released,
    };
  }
  return {
    kind: 'new',
    pn,
    barcode: `PF:PN:${pn}`,
    isNewPn: known === undefined,
  };
}

/**
 * The WO due date is the entry default: update lines still inheriting
 * it. A line whose due date was edited — including an explicit
 * `No due date` — is user-owned and never overwritten.
 */
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
 * Mock validation for saving demand: every line needs a PN and a
 * positive whole quantity; duplicate PNs are rejected. A missing due
 * date is NOT a validation error — it is summarized by the Save Demand
 * confirmation instead. Incomplete rows are never silently filtered
 * out.
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
  }
  return errors;
}

/**
 * Information that may be absent when demand is saved. None of these
 * are validation errors — they are summarized in an explicit Save
 * Demand confirmation before anything is applied.
 */
export interface MissingDemandInfo {
  /**
   * No external WO Number — the Work Order is saved with a null number
   * (internal), displays as `—`, and the number can be added later.
   */
  noWorkOrderNumber: boolean;
  /** No WO due date — the Work Order stays unscheduled. */
  noWorkOrderDue: boolean;
  /** Demand lines without a due date (lowest date priority later). */
  undatedLineCount: number;
}

export function collectMissingDemandInfo(
  workOrderNumber: string,
  workOrderDue: string,
  lines: readonly DemandLineDraft[],
): MissingDemandInfo | null {
  const info: MissingDemandInfo = {
    noWorkOrderNumber: workOrderNumber.trim() === '',
    noWorkOrderDue: workOrderDue === '',
    undatedLineCount: lines.filter((line) => line.due === '').length,
  };
  return info.noWorkOrderNumber || info.noWorkOrderDue || info.undatedLineCount
    ? info
    : null;
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
    due: line.due || null,
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
