import { expect, test } from 'vitest';

import type { MockBoardRow } from '../view-models';
import {
  fallbackPageBreaks,
  pageBreaksByHeight,
  sortBoardRows,
} from './board-logic';

/* ============ Height-aware page calculation ============ */

test('rows partition into pages that fit the available height', () => {
  // 100-high rows into 250 of space → 2 per page.
  expect(pageBreaksByHeight([100, 100, 100, 100, 100], 250)).toEqual([0, 2, 4]);
});

test('variable row heights partition without clipping', () => {
  // 120+80 fits 260; 90+200 would clip → page break before the 200-high
  // row; 200+40 fits; the final 40 starts a new page.
  expect(pageBreaksByHeight([120, 80, 90, 200, 40, 40], 260)).toEqual([
    0, 2, 3, 5,
  ]);
});

test('every page holds at least one row, even oversized rows', () => {
  expect(pageBreaksByHeight([500, 500], 250)).toEqual([0, 1]);
});

test('empty input produces no page breaks', () => {
  expect(pageBreaksByHeight([], 400)).toEqual([]);
});

test('fallback chunking when measurements are unavailable', () => {
  expect(fallbackPageBreaks(25, 10)).toEqual([0, 10, 20]);
  expect(fallbackPageBreaks(10, 10)).toEqual([0]);
  expect(fallbackPageBreaks(0, 10)).toEqual([]);
});

/* ============ Board row ordering ============ */

const row = (
  partial: Partial<MockBoardRow> & { pn: string },
): MockBoardRow => ({
  name: '',
  locations: [],
  total: 1,
  jobs: [],
  due: null,
  dueNote: '',
  dueClass: 'none',
  totalDays: '',
  received: '2026-07-01',
  ...partial,
});

test('board rows: Hot first, then dated, then undated, stocked last', () => {
  const rows = [
    row({ pn: 'UNDATED', due: null, received: '2026-07-10' }),
    row({ pn: 'STOCKED', due: '2026-07-01', totalStocked: true }),
    row({ pn: 'DATED-LATE', due: '2026-09-01' }),
    row({ pn: 'HOT', hotRank: 1, due: null }),
    row({ pn: 'DATED-EARLY', due: '2026-08-01' }),
    row({ pn: 'UNDATED-OLDER', due: null, received: '2026-07-02' }),
  ];
  expect(sortBoardRows(rows).map((r) => r.pn)).toEqual([
    'HOT',
    'DATED-EARLY',
    'DATED-LATE',
    'UNDATED-OLDER',
    'UNDATED',
    'STOCKED',
  ]);
});
