import { expect, test } from 'vitest';

import { compareDemandOrder } from './demand-order';
import type { DemandOrderKey } from './demand-order';

// Canonical WO Demand ordering: Hot rank → dated demands (earliest due
// first) → undated demands (by WO received date, oldest first) → stable
// internal tie-breaker. A missing due date is valid data.

const key = (partial: Partial<DemandOrderKey>): DemandOrderKey => ({
  due: null,
  received: '2026-07-01',
  seq: 0,
  ...partial,
});

test('manager-defined Hot priority is the highest criterion', () => {
  const hot2 = key({ hotRank: 2, due: '2026-07-01' });
  const hot1 = key({ hotRank: 1, due: null, seq: 5 });
  const dated = key({ due: '2026-01-01', seq: 9 });
  const sorted = [dated, hot2, hot1].sort(compareDemandOrder);
  expect(sorted).toEqual([hot1, hot2, dated]);
});

test('dated demands come before undated demands, earliest due first', () => {
  const late = key({ due: '2026-09-01', seq: 1 });
  const early = key({ due: '2026-08-01', seq: 2 });
  const undated = key({ due: null, received: '2026-01-01', seq: 3 });
  const sorted = [undated, late, early].sort(compareDemandOrder);
  expect(sorted).toEqual([early, late, undated]);
});

test('undated demands order by the WO received date, oldest first', () => {
  const newer = key({ due: null, received: '2026-07-20', seq: 1 });
  const older = key({ due: null, received: '2026-07-05', seq: 2 });
  const sorted = [newer, older].sort(compareDemandOrder);
  expect(sorted).toEqual([older, newer]);
});

test('equal values fall back to the stable internal tie-breaker', () => {
  const a = key({ due: '2026-08-01', seq: 2 });
  const b = key({ due: '2026-08-01', seq: 1 });
  expect([a, b].sort(compareDemandOrder)).toEqual([b, a]);

  const u1 = key({ due: null, received: '2026-07-01', seq: 4 });
  const u2 = key({ due: null, received: '2026-07-01', seq: 3 });
  expect([u1, u2].sort(compareDemandOrder)).toEqual([u2, u1]);
});
