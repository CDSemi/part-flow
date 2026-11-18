import { expect, test } from 'vitest';

import {
  BACKUP_INTERVAL_HOURS,
  KEEP_DAILY_RANGE,
  KEEP_HOURLY_RANGE,
  KEEP_PRE_PURGE_RANGE,
  KEEP_WEEKLY_RANGE,
  formatBackupInterval,
  keptSummary,
  parseKept,
} from './backup-policy';

// Backup policy helpers of Administration → Backups: the server's
// intervals and ranges (app/infrastructure/models.py), whole-number
// parsing without rounding or clamping, and the wording.

test('the intervals and ranges are the server rules', () => {
  expect(BACKUP_INTERVAL_HOURS).toEqual([1, 2, 3, 4, 6, 8, 12, 24]);
  expect(KEEP_HOURLY_RANGE).toEqual([0, 720]);
  expect(KEEP_DAILY_RANGE).toEqual([1, 3650]);
  expect(KEEP_WEEKLY_RANGE).toEqual([0, 520]);
  expect(KEEP_PRE_PURGE_RANGE).toEqual([1, 100]);
});

test('parseKept admits whole numbers in range only', () => {
  expect(parseKept('0', KEEP_HOURLY_RANGE)).toBe(0);
  expect(parseKept('720', KEEP_HOURLY_RANGE)).toBe(720);
  expect(parseKept('721', KEEP_HOURLY_RANGE)).toBeNull();
  expect(parseKept('0', KEEP_DAILY_RANGE)).toBeNull();
  expect(parseKept('1', KEEP_DAILY_RANGE)).toBe(1);
  expect(parseKept('2.5', KEEP_WEEKLY_RANGE)).toBeNull();
  expect(parseKept('', KEEP_WEEKLY_RANGE)).toBeNull();
  expect(parseKept('  ', KEEP_WEEKLY_RANGE)).toBeNull();
  expect(parseKept('-1', KEEP_WEEKLY_RANGE)).toBeNull();
  expect(parseKept('0', KEEP_PRE_PURGE_RANGE)).toBeNull();
  expect(parseKept('100', KEEP_PRE_PURGE_RANGE)).toBe(100);
  expect(parseKept('101', KEEP_PRE_PURGE_RANGE)).toBeNull();
});

test('formatBackupInterval names one hour and several', () => {
  expect(formatBackupInterval(1)).toBe('Every hour');
  expect(formatBackupInterval(24)).toBe('Every 24 hours');
});

test('keptSummary lists the periods kept, the most it keeps and the pre-purge backups kept', () => {
  expect(
    keptSummary({
      intervalHours: 1,
      keepHourly: 48,
      keepDaily: 30,
      keepWeekly: 52,
      keepPrePurge: 3,
    }),
  ).toBe(
    'Keeps one scheduled backup per hour for the last 48 hours, per day for the last 30 days and per week for the last 52 weeks — at most 130 scheduled backups. Keeps the newest 3 pre-purge backups.',
  );
  expect(
    keptSummary({
      intervalHours: 24,
      keepHourly: 0,
      keepDaily: 1,
      keepWeekly: 0,
      keepPrePurge: 1,
    }),
  ).toBe(
    'Keeps one scheduled backup per day for the last day — at most 1 scheduled backup. Keeps the newest pre-purge backup.',
  );
  expect(
    keptSummary({
      intervalHours: 6,
      keepHourly: 1,
      keepDaily: 7,
      keepWeekly: 0,
      keepPrePurge: 100,
    }),
  ).toBe(
    'Keeps one scheduled backup per hour for the last hour and per day for the last 7 days — at most 8 scheduled backups. Keeps the newest 100 pre-purge backups.',
  );
});
