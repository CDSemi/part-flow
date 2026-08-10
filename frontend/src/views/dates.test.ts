import { expect, test } from 'vitest';

import {
  daysBetweenIso,
  daysInProductionNote,
  DEFAULT_DUE_SOON_POLICY,
  dueCountdown,
  dueSoonWindowDays,
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

// The initial Due Soon defaults stand in for the future
// Administration-configured policy at every call site.
const dueSoon = (received: string | null) => ({
  received,
  policy: DEFAULT_DUE_SOON_POLICY,
});

test('DEFAULT_DUE_SOON_POLICY carries the initial Administration defaults', () => {
  expect(DEFAULT_DUE_SOON_POLICY).toEqual({
    minDays: 2,
    ratio: 0.15,
    maxDays: 7,
  });
});

test('dueSoonWindowDays scales with the lead time, clamped by the policy', () => {
  const policy = DEFAULT_DUE_SOON_POLICY;
  expect(dueSoonWindowDays(10, policy)).toBe(2); // ceil(1.5) = 2
  expect(dueSoonWindowDays(20, policy)).toBe(3); // ceil(3.0) = 3
  expect(dueSoonWindowDays(30, policy)).toBe(5); // ceil(4.5) = 5
  expect(dueSoonWindowDays(46, policy)).toBe(7); // ceil(6.9) = 7
  expect(dueSoonWindowDays(90, policy)).toBe(7); // clamped to maxDays
  expect(dueSoonWindowDays(1, policy)).toBe(2); // clamped to minDays
  // An unknown or invalid lead time falls back to the minimum window.
  expect(dueSoonWindowDays(null, policy)).toBe(policy.minDays);
  expect(dueSoonWindowDays(0, policy)).toBe(policy.minDays);
  expect(dueSoonWindowDays(-4, policy)).toBe(policy.minDays);
});

test('dueCountdown derives the one shared countdown language', () => {
  expect(dueCountdown(null, NOW, dueSoon('2026-07-20'))).toEqual({
    note: 'No due date',
    dueClass: 'none',
  });
  expect(dueCountdown('2026-08-05', NOW, dueSoon('2026-07-20'))).toEqual({
    note: 'due today',
    dueClass: 'soon',
  });
  expect(dueCountdown('2026-08-06', NOW, dueSoon('2026-07-20'))).toEqual({
    note: '1 day left',
    dueClass: 'soon',
  });
  expect(dueCountdown('2026-08-14', NOW, dueSoon('2026-07-20'))).toEqual({
    note: '9 days left',
    dueClass: 'ok',
  });
  expect(dueCountdown('2026-08-04', NOW, dueSoon('2026-07-20'))).toEqual({
    note: 'overdue 1 day',
    dueClass: 'late',
  });
  expect(dueCountdown('2026-07-30', NOW, dueSoon('2026-06-20'))).toEqual({
    note: 'overdue 6 days',
    dueClass: 'late',
  });
});

test('the due-soon window scales with the demand lead time', () => {
  // Short lead (received 2026-08-01 → due 2026-08-08 = 7 days):
  // window = clamp(2, ceil(7 × 0.15) = 2, 7) = 2 days.
  expect(dueCountdown('2026-08-08', NOW, dueSoon('2026-08-01'))).toEqual({
    note: '3 days left',
    dueClass: 'ok',
  });
  expect(dueCountdown('2026-08-07', NOW, dueSoon('2026-08-01'))).toEqual({
    note: '2 days left',
    dueClass: 'soon',
  });
  // Long lead (received 2026-06-03 → due 2026-08-12 = 70 days):
  // window = clamp(2, ceil(70 × 0.15) = 11, 7) = 7 days.
  expect(dueCountdown('2026-08-12', NOW, dueSoon('2026-06-03'))).toEqual({
    note: '7 days left',
    dueClass: 'soon',
  });
  expect(dueCountdown('2026-08-13', NOW, dueSoon('2026-06-03'))).toEqual({
    note: '8 days left',
    dueClass: 'ok',
  });
});

test('an unknown or invalid lead time falls back to the policy minimum window', () => {
  // No received date (e.g. Priority Hot entries carry none).
  expect(dueCountdown('2026-08-07', NOW, dueSoon(null))).toEqual({
    note: '2 days left',
    dueClass: 'soon',
  });
  expect(dueCountdown('2026-08-08', NOW, dueSoon(null))).toEqual({
    note: '3 days left',
    dueClass: 'ok',
  });
  // A malformed received date or one after the due date behaves the same.
  expect(dueCountdown('2026-08-07', NOW, dueSoon('garbage')).dueClass).toBe(
    'soon',
  );
  expect(dueCountdown('2026-08-08', NOW, dueSoon('2026-09-01')).dueClass).toBe(
    'ok',
  );
});

test('daysInProductionNote derives Total Days from the received date', () => {
  expect(daysInProductionNote('2026-07-26', NOW)).toBe('10 d');
  expect(daysInProductionNote('2026-08-05', NOW)).toBe('0 d');
  // A received date in the future (clock skew) clamps to 0, and bad
  // input renders the explicit placeholder.
  expect(daysInProductionNote('2026-08-09', NOW)).toBe('0 d');
  expect(daysInProductionNote('garbage', NOW)).toBe('—');
});
