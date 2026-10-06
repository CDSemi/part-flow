import { expect, test } from 'vitest';

import { formatEstimate, parseEstimate } from './route-duration';

// The Planned Route editor's Est. time language: the shared duration
// tokens, but lossless at one day and above.

test.each([
  [45, '45m'],
  [60, '1h 00m'],
  [90, '1h 30m'],
  [240, '4h 00m'],
  [1439, '23h 59m'],
  [1440, '1d 00h'],
  [2 * 1440 + 180, '2d 03h'],
  [1440 + 150, '1d 02h 30m'],
])('formatEstimate(%i) is %s', (minutes, text) => {
  expect(formatEstimate(minutes)).toBe(text);
});

test.each([
  ['45m', 45],
  ['4h', 240],
  ['90m', 90],
  ['1h 30m', 90],
  ['2d 03h', 2 * 1440 + 180],
  ['1d2h30m', 1440 + 150],
  ['  4h  ', 240],
  ['0h 30m', 30],
])('parseEstimate(%s) is %i', (text, minutes) => {
  expect(parseEstimate(text)).toBe(minutes);
});

test.each([
  '',
  '0m',
  '0d 0h',
  '4 hours',
  '4 h',
  '-1h',
  'h',
  '4h30',
  '30m 4h',
  '1.5h',
  '4H',
])('parseEstimate(%j) is null', (text) => {
  expect(parseEstimate(text)).toBeNull();
});

test('every formatted estimate parses back to its minutes', () => {
  for (const minutes of [
    1, 45, 59, 60, 61, 240, 1439, 1440, 1441, 3060, 9999,
  ]) {
    expect(parseEstimate(formatEstimate(minutes))).toBe(minutes);
  }
});
