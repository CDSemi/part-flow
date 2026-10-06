import { expect, test } from 'vitest';

import {
  FIT_SCALE_MIN,
  autoFitScale,
  fallbackPageBreaks,
  isBoardRotationTiming,
  pageBreaksByHeight,
  rotationDurationMs,
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

/* ============ Automatic display scale (v18) ============ */

test('the auto-fit scale fills the board width from the intrinsic table width', () => {
  // A 3840px display over 1900px of intrinsic content: the scale is
  // (3840 − allowance) / 1900 — the content grows until it fills the
  // width, minus the small fixed allowance for the Hot-row border.
  expect(autoFitScale(3840, 1900)).toBeCloseTo((3840 - 8) / 1900, 10);
  // An exact fit lands on 1 (the allowance prevents a marginal
  // overflow from wrapping the Job Numbers column).
  expect(autoFitScale(1908, 1900)).toBe(1);
});

test('the auto-fit scale shrinks the board on small screens (post-v18)', () => {
  // A viewport narrower than the content scales the whole board DOWN
  // so the full table width fits phones and tablets — the same
  // (width − allowance) / intrinsic factor, below 1.
  expect(autoFitScale(1200, 1900)).toBeCloseTo((1200 - 8) / 1900, 10);
  expect(autoFitScale(390, 1900)).toBeCloseTo((390 - 8) / 1900, 10);
});

test('the near-zero scale floor guards only degenerate measurements', () => {
  // The floor is deliberately the smallest practical value — small
  // screens are meant to shrink as far as their width requires, and
  // the clamp exists only against degenerate measurements.
  expect(FIT_SCALE_MIN).toBeLessThanOrEqual(0.1);
  expect(autoFitScale(100, 1900)).toBe(FIT_SCALE_MIN);
});

test('the auto-fit scale is 1 when measurements are unavailable', () => {
  // jsdom and the first paint before layout report zero widths.
  expect(autoFitScale(0, 1900)).toBe(1);
  expect(autoFitScale(3840, 0)).toBe(1);
  expect(autoFitScale(0, 0)).toBe(1);
});

/* ============ Per-page rotation timing (v15) ============ */

test('rotation dwell time is proportional to the displayed rows, per the Department', () => {
  const timing = { secondsPerRow: 3, minPageSeconds: 6 };
  expect(rotationDurationMs(3, timing)).toBe(9_000);
  expect(rotationDurationMs(10, { secondsPerRow: 2, minPageSeconds: 10 })).toBe(
    20_000,
  );
});

test('a near-empty page never flashes past — the Department floor applies', () => {
  const timing = { secondsPerRow: 3, minPageSeconds: 6 };
  expect(rotationDurationMs(0, timing)).toBe(6_000);
  expect(rotationDurationMs(1, timing)).toBe(6_000);
  expect(rotationDurationMs(10, { secondsPerRow: 2, minPageSeconds: 25 })).toBe(
    25_000,
  );
});

test('isBoardRotationTiming admits exactly the server ranges', () => {
  expect(isBoardRotationTiming({ secondsPerRow: 1, minPageSeconds: 1 })).toBe(
    true,
  );
  expect(
    isBoardRotationTiming({ secondsPerRow: 60, minPageSeconds: 300 }),
  ).toBe(true);
  for (const rejected of [
    { secondsPerRow: 0, minPageSeconds: 6 },
    { secondsPerRow: 61, minPageSeconds: 6 },
    { secondsPerRow: 3, minPageSeconds: 0 },
    { secondsPerRow: 3, minPageSeconds: 301 },
    { secondsPerRow: 3.5, minPageSeconds: 6 },
    { secondsPerRow: 3 },
    { secondsPerRow: Number.NaN, minPageSeconds: 6 },
    { secondsPerRow: undefined, minPageSeconds: undefined },
  ]) {
    expect(isBoardRotationTiming(rejected)).toBe(false);
  }
});
