import { expect, test } from 'vitest';

import type { WorkOrderDemand } from '../../api/work-orders';
import {
  buildLineEdits,
  createDraftLine,
  draftFromDemand,
  hotListExitNotices,
  lineRemoveRule,
  qtyEntryError,
  validateDemandLines,
} from './demand-lines';

// The demand-line removal rule is the presentation mirror of the
// backend refusals (PROJECT_PROFILE §13, the Hot line confirmation of
// the Phase 12 follow-up): the server stays the authority and answers
// 409 removing nothing.

test('an unsaved draft line is removed directly; a saved line asks first', () => {
  expect(lineRemoveRule(createDraftLine({ due: '' }), 2)).toBe('draft');
  expect(
    lineRemoveRule(createDraftLine({ due: '', saved: true, demandId: 1 }), 2),
  ).toBe('confirm');
});

test('a released line never offers removal', () => {
  expect(
    lineRemoveRule(
      createDraftLine({ due: '', saved: true, demandId: 1, released: true }),
      2,
    ),
  ).toBe('blocked');
});

test('an unallocated Hot line beside another saved line asks for the typed confirmation', () => {
  expect(
    lineRemoveRule(
      createDraftLine({ due: '', saved: true, demandId: 1, hotRank: 3 }),
      2,
    ),
  ).toBe('confirm-hot');
});

test('a Hot line with allocated quantity gets the plain confirmation — the server refuses it first', () => {
  expect(
    lineRemoveRule(
      createDraftLine({
        due: '',
        saved: true,
        demandId: 1,
        hotRank: 3,
        allocatedQuantity: 2,
      }),
      2,
    ),
  ).toBe('confirm');
});

test('a Hot line with reversed allocation history gets the plain confirmation — the server refuses it first', () => {
  // A reversed allocation leaves allocated quantity 0, but the line
  // stays unremovable: a typed confirmation would be typed in vain.
  expect(
    lineRemoveRule(
      createDraftLine({
        due: '',
        saved: true,
        demandId: 1,
        hotRank: 3,
        hasAllocationHistory: true,
      }),
      2,
    ),
  ).toBe('confirm');
});

test('a Hot line that is the only saved line gets the plain confirmation', () => {
  expect(
    lineRemoveRule(
      createDraftLine({ due: '', saved: true, demandId: 1, hotRank: 1 }),
      1,
    ),
  ).toBe('confirm');
});

test('released takes precedence over Hot — the permanent reason is shown', () => {
  expect(
    lineRemoveRule(
      createDraftLine({
        due: '',
        saved: true,
        demandId: 1,
        released: true,
        hotRank: 1,
      }),
      2,
    ),
  ).toBe('blocked');
});

function savedDemand(
  id: number,
  partNumber: string,
  extra: Partial<WorkOrderDemand>,
): WorkOrderDemand {
  return {
    id,
    workOrderId: 1,
    partNumber,
    requestType: 'NEW',
    requestedQuantity: 10,
    allocatedQuantity: 0,
    dueDate: null,
    priorityRank: null,
    jobNumbers: [],
    requester: null,
    reason: null,
    notes: null,
    hasReleasedQuantity: false,
    releasedQuantity: 0,
    remainingQuantity: 10,
    hasAllocationHistory: false,
    ...extra,
  };
}

test('a save notice names only the lines that left the Hot list fully allocated', () => {
  const previous = [
    savedDemand(1, 'A-100', { priorityRank: 2, allocatedQuantity: 4 }),
    savedDemand(2, 'B-200', { priorityRank: 1 }),
    savedDemand(3, 'C-300', { priorityRank: 3 }),
  ];
  const fresh = [
    // Lowered to its allocated quantity: the server took it off.
    savedDemand(1, 'A-100', {
      requestedQuantity: 4,
      allocatedQuantity: 4,
    }),
    // Removed from the Hot list elsewhere meanwhile: no notice.
    savedDemand(2, 'B-200', {}),
    // Still Hot (re-ranked): no notice.
    savedDemand(3, 'C-300', { priorityRank: 2 }),
  ];
  expect(hotListExitNotices(previous, fresh)).toEqual([
    '🔥 A-100 left the Hot list (was #2) — the line is now fully allocated.',
  ]);
});

// Phase 14 slice 5: a line allocated beyond its demand by an authorized
// correction (10 requested, 12 allocated) keeps its other fields
// editable — only a CHANGED Qty is judged against the floor.

test('FC-8: an over-allocated line with its saved Qty passes the entry check and the save check', () => {
  const demand = savedDemand(1, 'A-100', {
    allocatedQuantity: 12,
    hasAllocationHistory: true,
  });
  const line = draftFromDemand(demand, null);
  expect(line.savedQty).toBe(10);
  expect(qtyEntryError(line, '10')).toBeNull();
  expect(validateDemandLines([line])).toEqual([]);

  // A changed Qty below the allocated quantity still errors.
  expect(qtyEntryError(line, '11')).toBe('≥ 12 pcs allocated');
  expect(validateDemandLines([{ ...line, qty: '11' }])).toEqual([
    { lineId: line.id, field: 'qty', message: '≥ 12 pcs allocated' },
  ]);
  expect(qtyEntryError(line, '12')).toBeNull();

  // Saving another field sends no requested_quantity.
  expect(
    buildLineEdits(
      [{ ...line, due: '2026-10-01', dueTouched: true }],
      [demand],
    ),
  ).toEqual([{ id: 1, dueDate: '2026-10-01' }]);
});

test('FC-8: an unsaved draft line is always judged — it has no saved Qty', () => {
  const draft = createDraftLine({ due: '', allocatedQuantity: 5 });
  expect(draft.savedQty).toBeNull();
  expect(qtyEntryError(draft, '4')).toBe('≥ 5 pcs allocated');
});
