import { expect, test } from 'vitest';

import { isoDurationToMinutes, minutesToIsoDuration } from './duration';

// ISO 8601 duration handling for the Operation expected-duration wire
// value: the UI edits whole minutes; the API travels Pydantic's
// canonical timedelta form.

test('parses the day/hour/minute/second subset to whole minutes', () => {
  expect(isoDurationToMinutes('PT30M')).toBe(30);
  expect(isoDurationToMinutes('PT2H')).toBe(120);
  expect(isoDurationToMinutes('P1DT2H30M')).toBe(1590);
  expect(isoDurationToMinutes('PT90S')).toBe(2); // rounded
  expect(isoDurationToMinutes(' PT45M ')).toBe(45);
  expect(isoDurationToMinutes('-PT30M')).toBe(-30);
});

test('rejects malformed or unsupported values instead of guessing', () => {
  expect(isoDurationToMinutes('')).toBeNull();
  expect(isoDurationToMinutes('P')).toBeNull();
  expect(isoDurationToMinutes('30M')).toBeNull();
  expect(isoDurationToMinutes('PT')).toBeNull();
  // Year/month components have no defined length — unsupported.
  expect(isoDurationToMinutes('P1M')).toBeNull();
  expect(isoDurationToMinutes('P1Y')).toBeNull();
});

test('formats whole minutes as the canonical PT…M form', () => {
  expect(minutesToIsoDuration(30)).toBe('PT30M');
  expect(minutesToIsoDuration(1)).toBe('PT1M');
});
