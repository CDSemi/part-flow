import { expect, test } from 'vitest';

import type { WorkOrderDemand } from '../../api/work-orders';
import {
  createDraftLine,
  hotListExitNotices,
  lineRemoveRule,
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
