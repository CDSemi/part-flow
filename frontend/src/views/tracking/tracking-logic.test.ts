import { expect, test } from 'vitest';

import type { TrackingDetail } from '../../api/tracking';
import { detailRevisions } from './tracking-logic';

// The per-section revision signatures of a polled Tracking detail: a
// change means the appended (older) pages of that section are read
// again; an unchanged signature keeps them.

/** The figures `detailRevisions` reads; the rest of the detail is not
 * part of any signature. */
function detail(
  figures: {
    movements?: number;
    scrap?: number;
    scrapped?: number;
    flows?: number;
    allocations?: number;
    routeAdjustments?: number;
  } = {},
): TrackingDetail {
  return {
    movements: { total: figures.movements ?? 9 },
    scrapHistory: { total: figures.scrap ?? 2 },
    scrappedQuantity: figures.scrapped ?? 1,
    flows: { total: figures.flows ?? 2 },
    allocations: { total: figures.allocations ?? 0 },
    routeAdjustmentTotal: figures.routeAdjustments ?? 0,
  } as unknown as TrackingDetail;
}

test('FE-13: a route adjustment alone changes the flow revision and nothing else', () => {
  const before = detailRevisions(detail());
  const after = detailRevisions(detail({ routeAdjustments: 1 }));
  expect(after.flows).not.toBe(before.flows);
  expect(after.movements).toBe(before.movements);
  expect(after.scrap).toBe(before.scrap);
  expect(after.allocations).toBe(before.allocations);
});

test('the flow revision still follows the flow and Movement counts', () => {
  const base = detailRevisions(detail()).flows;
  expect(detailRevisions(detail({ flows: 3 })).flows).not.toBe(base);
  expect(detailRevisions(detail({ movements: 10 })).flows).not.toBe(base);
  expect(detailRevisions(detail()).flows).toBe(base);
});
