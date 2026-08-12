import { expect, test } from 'vitest';

import {
  CODE128_PATTERNS,
  code128ModuleCount,
  encodeCode128B,
} from './code128';

// Code 128 structural guarantees: transcription errors in the pattern
// table would break the fixed 11-module symbol width (13 for stop), so
// the table is verified exhaustively instead of trusting eyeballs.

test('every symbol pattern is 11 modules wide; the stop pattern is 13', () => {
  expect(CODE128_PATTERNS).toHaveLength(107);
  CODE128_PATTERNS.forEach((pattern, value) => {
    const modules = pattern
      .split('')
      .reduce((sum, digit) => sum + Number(digit), 0);
    if (value === 106) {
      expect(pattern).toHaveLength(7);
      expect(modules).toBe(13);
    } else {
      expect(pattern).toHaveLength(6);
      expect(modules).toBe(11);
    }
  });
});

test('an encoded value has the exact expected total width', () => {
  const value = 'PF:MACHINE:CD-0512';
  const runs = encodeCode128B(value);
  expect(runs).not.toBeNull();
  // start + data + checksum symbols (11 modules each) + stop (13).
  expect(code128ModuleCount(runs!)).toBe((value.length + 2) * 11 + 13);
  // Runs strictly alternate bar/space and start and end with a bar.
  expect(runs![0].bar).toBe(true);
  expect(runs![runs!.length - 1].bar).toBe(true);
  runs!.forEach((run, i) => {
    expect(run.bar).toBe(i % 2 === 0);
    expect(run.width).toBeGreaterThanOrEqual(1);
    expect(run.width).toBeLessThanOrEqual(4);
  });
});

test('the checksum is the weighted sum mod 103', () => {
  // Hand-computed reference: "AB" in set B — start 104, A=33 (×1),
  // B=34 (×2) → 104 + 33 + 68 = 205; 205 mod 103 = 102.
  const runs = encodeCode128B('AB')!;
  // Symbol sequence: 104, 33, 34, 102, 106 — compare via pattern
  // concatenation to avoid exposing internals.
  const expected = [104, 33, 34, 102, 106]
    .map((v) => CODE128_PATTERNS[v])
    .join('');
  const actual = runs.map((run) => run.width).join('');
  expect(actual).toBe(expected);
});

test('values outside printable ASCII are rejected, never mis-encoded', () => {
  expect(encodeCode128B('')).toBeNull();
  expect(encodeCode128B('CD-0512\n')).toBeNull();
  expect(encodeCode128B('CD–0512')).toBeNull(); // en dash, not ASCII
});
