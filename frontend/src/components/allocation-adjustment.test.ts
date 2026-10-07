import { expect, test } from 'vitest';

import type {
  ContextAllocation,
  ContextLine,
  ManagementAllocationResult,
} from '../api/management-allocations';
import {
  afterCorrectionLine,
  allocateQtyError,
  beyondQtyError,
  beyondRange,
  committedNotice,
  reasonError,
  reversalReopens,
  routineLimit,
  sourceLabel,
  workOrderLabel,
} from './allocation-adjustment';

// The allocation adjustment rules (Phase 14 slice 5, GUI_DESIGN §11.6):
// routine allocation never beyond the line's remaining demand nor the
// available stock, the beyond-demand correction always beyond the
// remaining demand and never above the available stock, and the exact
// field and notice copy. The server judges every write again.

function line(extra: Partial<ContextLine> = {}): ContextLine {
  return {
    workOrderId: 1,
    workOrderNumber: '007201',
    workOrderCompleted: false,
    receivedDate: '2026-08-01',
    workOrderDemandId: 7,
    requestType: 'NEW',
    dueDate: null,
    priorityRank: null,
    requestedQuantity: 10,
    allocatedQuantity: 6,
    remainingShortage: 4,
    beyondDemandQuantity: 0,
    activeAllocations: [],
    ...extra,
  };
}

function allocation(quantity: number): ContextAllocation {
  return {
    allocationId: 31,
    quantity,
    source: 'STOCKROOM',
    isManualOverride: false,
    exceedsDemand: false,
    allocationReason: null,
    stationId: 'STOCK-1',
    allocatedAt: '2026-08-02T08:00:00Z',
    actorUser: null,
  };
}

test('FC-2: the routine limit is the smaller of the remaining demand and the available stock', () => {
  expect(routineLimit(line(), 6)).toBe(4);
  expect(routineLimit(line(), 3)).toBe(3);
  expect(routineLimit(line({ remainingShortage: 0 }), 5)).toBe(0);
  expect(routineLimit(line(), -2)).toBe(0);
});

test('FC-2: a correction ranges from just beyond the remaining demand up to the available stock', () => {
  expect(beyondRange(line(), 6)).toEqual({ min: 5, max: 6 });
  expect(beyondRange(line({ remainingShortage: 0 }), 2)).toEqual({
    min: 1,
    max: 2,
  });
  expect(beyondRange(line(), 4)).toBeNull();
  expect(beyondRange(line(), 3)).toBeNull();
});

test('FC-2: a reversal reopens only a completed Work Order whose line falls below its demand', () => {
  const complete = line({
    workOrderCompleted: true,
    allocatedQuantity: 12,
    remainingShortage: 0,
  });
  expect(reversalReopens(complete, allocation(2))).toBe(false);
  expect(reversalReopens(complete, allocation(3))).toBe(true);
  expect(reversalReopens(line(), allocation(6))).toBe(false);
});

test('FC-2: the Allocate from stock quantity errors', () => {
  expect(allocateQtyError('', line(), 6)).toBe(
    'Enter a whole number of pieces greater than 0.',
  );
  expect(allocateQtyError('0', line(), 6)).toBe(
    'Enter a whole number of pieces greater than 0.',
  );
  expect(allocateQtyError('2.5', line(), 6)).toBe(
    'Enter a whole number of pieces greater than 0.',
  );
  expect(allocateQtyError('5', line(), 6)).toBe(
    'Only 4 pcs are still needed on this line. To allocate more, use Allocate beyond demand….',
  );
  expect(allocateQtyError('5', line(), 4)).toBe(
    'Only 4 pcs are still needed on this line.',
  );
  expect(allocateQtyError('4', line(), 3)).toBe(
    'Only 3 pcs are available in stock.',
  );
  expect(allocateQtyError('4', line(), 6)).toBeNull();
});

test('FC-2: the beyond-demand quantity errors', () => {
  expect(beyondQtyError('x', line(), 6)).toBe(
    'Enter a whole number of pieces greater than 0.',
  );
  expect(beyondQtyError('4', line(), 6)).toBe(
    'A beyond-demand correction must be more than the 4 pcs still needed on this line.',
  );
  expect(beyondQtyError('7', line(), 6)).toBe(
    'Only 6 pcs are available in stock.',
  );
  expect(beyondQtyError('6', line(), 6)).toBeNull();
});

test('FC-2: a correction and a reversal each need a reason', () => {
  expect(reasonError('  ', 'correction')).toBe(
    'Enter the reason for this correction.',
  );
  expect(reasonError('', 'reversal')).toBe(
    'Enter the reason for this reversal.',
  );
  expect(reasonError('counted twice', 'reversal')).toBeNull();
});

test('FC-2: labels, the after-correction line and the committed notices', () => {
  expect(workOrderLabel(null)).toBe('—');
  expect(sourceLabel('STOCKROOM')).toBe('Stockroom');
  expect(sourceLabel('MANAGEMENT')).toBe('Management');
  expect(
    afterCorrectionLine(
      line({ allocatedQuantity: 10, remainingShortage: 0 }),
      2,
    ),
  ).toBe(
    'After this correction: 12 of 10 pcs allocated (2 pcs beyond demand).',
  );

  const result = (
    extra: Partial<ManagementAllocationResult>,
  ): ManagementAllocationResult => ({
    kind: 'ALLOCATE',
    partNumber: 'A-100',
    allocationQuantity: 4,
    completedWorkOrderIds: [],
    reopenedWorkOrderIds: [],
    deviceEventId: 'evt',
    created: true,
    ...extra,
  });
  const numberOf = () => '007201';
  expect(
    committedNotice(
      result({ completedWorkOrderIds: [1] }),
      { workOrderNumber: '007201' },
      numberOf,
    ),
  ).toBe(
    '✓ 4 pcs of A-100 allocated to Work Order 007201. Work Order 007201 is complete.',
  );
  expect(
    committedNotice(
      result({ kind: 'ALLOCATE_BEYOND_DEMAND', allocationQuantity: 2 }),
      { workOrderNumber: null },
      () => null,
    ),
  ).toBe(
    '✓ 2 pcs of A-100 allocated beyond demand to Work Order — — correction recorded.',
  );
  expect(
    committedNotice(
      result({ kind: 'REVERSE_ALLOCATION', reopenedWorkOrderIds: [1] }),
      { workOrderNumber: '007201' },
      numberOf,
    ),
  ).toBe(
    '✓ Allocation of 4 pcs reversed — returned to available stock. Work Order 007201 reopened.',
  );
});
