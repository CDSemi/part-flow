// Backup policy helpers of Administration → Backups (Phase 16, owner
// decision of 2026-11-13): the intervals and ranges the server admits,
// the parsing of the operator's whole-number entries and the wording of
// the stored values. The bounds are input validation only — the server
// re-validates and stays authoritative, and the stored policy comes only
// from the server.

import type { BackupPolicy } from '../../api/policies';

/** The intervals that divide the day — the only ones the server admits. */
export const BACKUP_INTERVAL_HOURS: readonly number[] = [
  1, 2, 3, 4, 6, 8, 12, 24,
];
export const KEEP_HOURLY_RANGE = [0, 720] as const;
export const KEEP_DAILY_RANGE = [1, 3650] as const;
export const KEEP_WEEKLY_RANGE = [0, 520] as const;

/** A whole number within the range held by the text, else null (never
 * rounded or clamped). */
export function parseKept(
  text: string,
  [min, max]: readonly [number, number],
): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return Number.isInteger(value) && value >= min && value <= max ? value : null;
}

/** "Every hour", "Every 6 hours". */
export function formatBackupInterval(hours: number): string {
  return hours === 1 ? 'Every hour' : `Every ${hours} hours`;
}

/** What the rotation keeps, in words: one backup per period, and the
 * most it can keep (fewer while the periods overlap). */
export function keptSummary(policy: BackupPolicy): string {
  const periods = [
    [policy.keepHourly, 'hour'],
    [policy.keepDaily, 'day'],
    [policy.keepWeekly, 'week'],
  ] as const;
  const named = periods
    .filter(([count]) => count > 0)
    .map(
      ([count, unit]) =>
        `per ${unit} for the last ${count === 1 ? unit : `${count} ${unit}s`}`,
    );
  const list =
    named.length > 1
      ? `${named.slice(0, -1).join(', ')} and ${named[named.length - 1]}`
      : named[0];
  const most = policy.keepHourly + policy.keepDaily + policy.keepWeekly;
  return `Keeps one scheduled backup ${list} — at most ${most} scheduled backup${most === 1 ? '' : 's'}.`;
}
