import { expect, test } from 'vitest';

import {
  daysBetweenIso,
  daysInProductionNote,
  DUE_SOON_DAYS,
  dueCountdown,
  elapsedMinutesSince,
  formatDuration,
  formatElapsedSince,
  formatIsoDate,
  formatIsoDateShort,
  todayIso,
} from './dates';

// One fixed reference instant: 2026-08-05 10:00 local time.
const NOW = new Date(2026, 7, 5, 10, 0, 0).getTime();

/* ============ Fixed-date formatting (unchanged contract) ============ */

test('formatIsoDate / short render business dates without timezone shifts', () => {
  expect(formatIsoDate('2026-07-24')).toBe('Jul 24, 2026');
  expect(formatIsoDateShort('2026-07-24')).toBe('Jul 24');
  expect(formatIsoDate(null)).toBe('—');
  expect(formatIsoDate('not-a-date')).toBe('not-a-date');
});

test('todayIso derives the local ISO date from an explicit instant', () => {
  expect(todayIso(NOW)).toBe('2026-08-05');
});

/* ============ Shared elapsed-duration language ============ */

test('formatDuration speaks the one compact duration language', () => {
  expect(formatDuration(0)).toBe('<1m');
  expect(formatDuration(59_000)).toBe('<1m');
  expect(formatDuration(60_000)).toBe('1m');
  expect(formatDuration(18 * 60_000)).toBe('18m');
  expect(formatDuration((60 + 24) * 60_000)).toBe('1h 24m');
  expect(formatDuration((2 * 1440 + 3 * 60) * 60_000)).toBe('2d 03h');
});

test('a timestamp newer than the clock tick clamps to <1m, never negative', () => {
  // Immediately after a movement the data timestamp can be NEWER than
  // the last shared clock tick — the derived value must read <1m.
  expect(formatDuration(-45_000)).toBe('<1m');
  const justRecorded = new Date(NOW + 30_000).toISOString();
  expect(formatElapsedSince(justRecorded, NOW)).toBe('<1m');
  expect(elapsedMinutesSince(justRecorded, NOW)).toBe(0);
});

test('formatElapsedSince and elapsedMinutesSince derive from the same timestamp', () => {
  const entered = new Date(NOW - 125 * 60_000).toISOString();
  expect(formatElapsedSince(entered, NOW)).toBe('2h 05m');
  expect(elapsedMinutesSince(entered, NOW)).toBe(125);
});

/* ============ Derived due countdown ============ */

test('daysBetweenIso is calendar-day arithmetic on plain ISO dates', () => {
  expect(daysBetweenIso('2026-08-05', '2026-08-07')).toBe(2);
  expect(daysBetweenIso('2026-08-05', '2026-08-05')).toBe(0);
  expect(daysBetweenIso('2026-08-05', '2026-07-30')).toBe(-6);
  expect(daysBetweenIso('2026-08-05', 'garbage')).toBeNull();
});

test('dueCountdown derives the one shared countdown language', () => {
  expect(dueCountdown(null, NOW)).toEqual({
    note: 'No due date',
    dueClass: 'none',
  });
  expect(dueCountdown('2026-08-05', NOW)).toEqual({
    note: 'due today',
    dueClass: 'soon',
  });
  expect(dueCountdown('2026-08-06', NOW)).toEqual({
    note: '1 day left',
    dueClass: 'soon',
  });
  expect(dueCountdown(todayIso(NOW + DUE_SOON_DAYS * 86_400_000), NOW)).toEqual(
    { note: `${DUE_SOON_DAYS} days left`, dueClass: 'soon' },
  );
  expect(dueCountdown('2026-08-14', NOW)).toEqual({
    note: '9 days left',
    dueClass: 'ok',
  });
  expect(dueCountdown('2026-08-04', NOW)).toEqual({
    note: 'overdue 1 day',
    dueClass: 'late',
  });
  expect(dueCountdown('2026-07-30', NOW)).toEqual({
    note: 'overdue 6 days',
    dueClass: 'late',
  });
});

test('daysInProductionNote derives Total Days from the received date', () => {
  expect(daysInProductionNote('2026-07-26', NOW)).toBe('10 d');
  expect(daysInProductionNote('2026-08-05', NOW)).toBe('0 d');
  // A received date in the future (clock skew) clamps to 0, and bad
  // input renders the explicit placeholder.
  expect(daysInProductionNote('2026-08-09', NOW)).toBe('0 d');
  expect(daysInProductionNote('garbage', NOW)).toBe('—');
});
