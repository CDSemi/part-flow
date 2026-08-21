// Shared demand-line draft model for the Work Orders view: the New
// Work Order dialog and the OPEN Work Order detail edit the same kind
// of line drafts.
//
// Phase 4 scope: presentation-side draft bookkeeping and pre-flight
// validation over REAL server state. The canonical business rules
// (Work Order Intake workflow, Work Order Demand removal) are owned by
// PROJECT_PROFILE §13 and enforced transactionally in the backend
// Application layer — this module only prepares one Save's request
// (`line_edits` diff + `new_lines`) and mirrors the entry rules so
// obviously invalid rows never travel.

import type {
  DemandLineEdit,
  NewDemandLine,
  WorkOrderDemand,
} from '../../api/work-orders';
import { parseScan } from '../scan-station/barcode';
import type { RequestType } from '../view-models';

export interface DemandLineDraft {
  /** Local draft key — stable for React keys and field focus. */
  id: number;
  /** The saved WorkOrderDemand id, or null for an unsaved draft line. */
  demandId: number | null;
  /** null while a manual row still needs its PN lookup / inline create. */
  pn: string | null;
  /** True for a PN with no PartNumber master yet — the line still
   * carries its barcode, which derives from the canonical PN alone. */
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
  /** Job Numbers as entered — comma-separated display text. */
  job: string;
  notes: string;
  /** True when the line exists in saved server state. */
  saved: boolean;
  /** Server-derived release evidence (`has_released_quantity`): the
   * line renders Released and accepts the restricted edit only — Qty,
   * due date and Job Numbers (GUI_DESIGN §11.2). Never a
   * client-session guess — it survives any reload. */
  released: boolean;
  /** Server-derived released and remaining quantity of the demand. A
   * demand may be released in several parts, so the release action
   * stays offered while `remainingQuantity > 0` — the backend enforces
   * the same cap. */
  releasedQuantity: number;
  remainingQuantity: number;
  /** Server-owned allocated quantity (Phase 10). Together with the
   * released quantity it is what production has already committed —
   * the floor a saved Qty can never fall below. */
  allocatedQuantity: number;
  statusLabel: string;
}

export type LineField = 'pn' | 'qty' | 'due';

export interface LineError {
  lineId: number;
  field: LineField;
  message: string;
}

/** Human status of the server-derived read value (`OPEN` → `Open`,
 * `RELEASED` → `Released`). */
export function workOrderStatusLabel(status: string): string {
  return status.charAt(0) + status.slice(1).toLowerCase();
}

export const RELEASED_REMOVE_EXPLANATION =
  'Cannot remove: production quantity has already been released.';

/**
 * The quantity a saved demand line can never fall below: what
 * production has already committed to it (PROJECT_PROFILE §13). A
 * released line may be raised freely and lowered down to exactly this
 * value — never under it. The backend enforces the same floor.
 */
function committedQuantity(line: DemandLineDraft): number {
  return Math.max(line.releasedQuantity, line.allocatedQuantity);
}

/** The one wording of the floor — the larger commitment names itself. */
function belowCommittedMessage(line: DemandLineDraft): string {
  const reason =
    line.allocatedQuantity > line.releasedQuantity
      ? 'already allocated'
      : 'already released';
  return `quantity cannot go below ${committedQuantity(line)} — that much is ${reason}`;
}

/**
 * The Qty error of one line as it is being typed: a line may never be
 * taken below what production has already committed to it
 * (PROJECT_PROFILE §13), and the field says so at entry time instead
 * of waiting for Save. Returns null while the entry is acceptable — a
 * blank or not-yet-numeric entry is not an entry-time error, so a
 * half-typed value never shouts; `validateDemandLines` still catches
 * it when Save runs.
 */
export function qtyEntryError(
  line: DemandLineDraft,
  raw: string,
): string | null {
  if (!isPositiveInteger(raw)) return null;
  return Number.parseInt(raw, 10) < committedQuantity(line)
    ? belowCommittedMessage(line)
    : null;
}

let nextDraftId = 1;

export function createDraftLine(
  init: Partial<DemandLineDraft> & { due: string },
): DemandLineDraft {
  return {
    id: nextDraftId++,
    demandId: null,
    pn: null,
    isNewPn: false,
    type: 'NEW',
    qty: '',
    dueTouched: false,
    job: '',
    notes: '',
    saved: false,
    released: false,
    releasedQuantity: 0,
    remainingQuantity: 0,
    allocatedQuantity: 0,
    statusLabel: 'Draft (unsaved)',
    ...init,
  };
}

/** Job Numbers list ↔ the comma-separated entry text. */
export function jobNumbersToText(jobNumbers: readonly string[]): string {
  return jobNumbers.join(', ');
}

export function textToJobNumbers(text: string): string[] {
  return text
    .split(',')
    .map((value) => value.trim())
    .filter((value) => value !== '');
}

/** Load one saved server demand line into an editable draft. The
 * Released state — and with it the restricted edit — comes from the
 * server's release evidence, never from local session state. */
export function draftFromDemand(
  demand: WorkOrderDemand,
  workOrderDue: string | null,
): DemandLineDraft {
  const released = demand.hasReleasedQuantity;
  return createDraftLine({
    releasedQuantity: demand.releasedQuantity,
    remainingQuantity: demand.remainingQuantity,
    allocatedQuantity: demand.allocatedQuantity,
    demandId: demand.id,
    pn: demand.partNumber,
    isNewPn: false,
    type: demand.requestType,
    qty: String(demand.requestedQuantity),
    due: demand.dueDate ?? '',
    // A line still holding the WO due date follows later WO-due edits;
    // a line with its own date (or explicit No due date) keeps it.
    dueTouched: (demand.dueDate ?? '') !== (workOrderDue ?? ''),
    job: jobNumbersToText(demand.jobNumbers),
    notes: demand.notes ?? '',
    saved: true,
    released,
    // A partly released line states what is actually released, so the
    // remaining quantity is never mistaken for "nothing released" or
    // for "fully released".
    statusLabel: !released
      ? 'Saved'
      : demand.remainingQuantity > 0
        ? `Released ${demand.releasedQuantity}/${demand.requestedQuantity}`
        : 'Released',
  });
}

export type ScanResult =
  | { kind: 'invalid'; barcode: string }
  | { kind: 'duplicate'; lineId: number; pn: string; released: boolean }
  | { kind: 'pn'; pn: string; barcode: string };

/**
 * Resolve a scanned PN barcode. `PF:PN:<part-number>` carries the PN
 * itself: the whitespace-free suffix, canonicalized to uppercase, is
 * the canonical PN (no format validation, no opaque id mapping).
 * Whether the PN already has a master record is an async server
 * lookup the caller performs — a PN outside the masters is
 * create-on-first-use. Non-PN barcodes never add demand lines.
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
  // parsed.pn is the canonical PN — the PN string itself is identity.
  const duplicate = lines.find(
    (line) => line.pn !== null && line.pn === parsed.pn,
  );
  if (duplicate) {
    return {
      kind: 'duplicate',
      lineId: duplicate.id,
      pn: duplicate.pn ?? parsed.pn,
      released: duplicate.released,
    };
  }
  return { kind: 'pn', pn: parsed.pn, barcode: `PF:PN:${parsed.pn}` };
}

/**
 * The WO due date is the entry default: update lines still inheriting
 * it. A line whose due date was edited — including an explicit
 * `No due date` — is user-owned and never overwritten. A released line
 * follows the same rule: its due date stays editable after release, so
 * an inherited one still inherits.
 */
export function applyWorkOrderDueDateChange(
  lines: readonly DemandLineDraft[],
  newDue: string,
): DemandLineDraft[] {
  return lines.map((line) =>
    line.dueTouched ? line : { ...line, due: newDue },
  );
}

export function isPositiveInteger(raw: string): boolean {
  return /^\d+$/.test(raw.trim()) && Number.parseInt(raw, 10) >= 1;
}

/**
 * Pre-flight validation before Save demand travels: every line needs a
 * PN and a positive whole quantity; duplicate PNs are rejected. A
 * released line keeps its (unchangeable) PN and is validated too — its
 * quantity may not drop below what production already committed
 * (PROJECT_PROFILE §13). A missing due date is NOT a validation error
 * — it is summarized by the Save Demand confirmation instead.
 * Incomplete rows are never silently filtered out. The backend
 * re-validates everything transactionally.
 */
export function validateDemandLines(
  lines: readonly DemandLineDraft[],
): LineError[] {
  const errors: LineError[] = [];
  const seen = new Map<string, number>();
  for (const line of lines) {
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
    } else if (Number.parseInt(line.qty, 10) < committedQuantity(line)) {
      errors.push({
        lineId: line.id,
        field: 'qty',
        message: belowCommittedMessage(line),
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
 * Presentation mirror of the canonical WorkOrderDemand removal rule
 * (PROJECT_PROFILE §13): an unsaved draft is removed immediately, a
 * saved line needs explicit confirmation (the backend enforces the
 * released-quantity and last-line rules transactionally and answers
 * 409 removing nothing), and a line whose released quantity is known
 * to this session never offers removal at all.
 */
export function lineRemoveRule(line: DemandLineDraft): RemoveRule {
  if (line.released) return 'blocked';
  return line.saved ? 'confirm' : 'draft';
}

/** One unsaved draft line as its `new_lines` request entry. */
export function draftToNewLine(line: DemandLineDraft): NewDemandLine {
  return {
    partNumber: line.pn ?? '',
    requestedQuantity: Number.parseInt(line.qty, 10),
    requestType: line.type,
    dueDate: line.due || null,
    jobNumbers: textToJobNumbers(line.job),
    notes: line.notes.trim() ? line.notes : null,
  };
}

/**
 * The `line_edits` diff of one Save demand: for every saved line, only
 * fields that differ from the loaded server demand travel — an
 * unchanged line produces no edit at all, and `request_type` never
 * travels as null (the backend rejects that instead of reinterpreting
 * it).
 *
 * A released line is NOT skipped: it still contributes its Qty, due
 * date and Job Numbers (PROJECT_PROFILE §13). Only its locked fields
 * stay out of the request — the UI renders them read-only, so they
 * cannot differ, and sending them would be a 409 either way.
 */
export function buildLineEdits(
  lines: readonly DemandLineDraft[],
  demands: readonly WorkOrderDemand[],
): DemandLineEdit[] {
  const demandById = new Map(demands.map((demand) => [demand.id, demand]));
  const edits: DemandLineEdit[] = [];
  for (const line of lines) {
    if (line.demandId === null) continue;
    const demand = demandById.get(line.demandId);
    if (!demand) continue;
    const edit: DemandLineEdit = { id: line.demandId };
    let changed = false;
    if (!line.released && line.type !== demand.requestType) {
      edit.requestType = line.type;
      changed = true;
    }
    const quantity = Number.parseInt(line.qty, 10);
    if (quantity !== demand.requestedQuantity) {
      edit.requestedQuantity = quantity;
      changed = true;
    }
    if ((line.due || null) !== demand.dueDate) {
      edit.dueDate = line.due || null;
      changed = true;
    }
    const jobNumbers = textToJobNumbers(line.job);
    if (jobNumbersToText(demand.jobNumbers) !== jobNumbersToText(jobNumbers)) {
      edit.jobNumbers = jobNumbers;
      changed = true;
    }
    const notes = line.notes.trim() ? line.notes : null;
    if (!line.released && notes !== demand.notes) {
      edit.notes = notes;
      changed = true;
    }
    if (changed) edits.push(edit);
  }
  return edits;
}

/** PN preview text for a Work Order list row. */
export function partNumbersPreview(partNumbers: readonly string[]): string {
  const head = partNumbers.slice(0, 2).join(' · ');
  return partNumbers.length > 2
    ? `${head} · ${partNumbers.length - 2} more`
    : head;
}
