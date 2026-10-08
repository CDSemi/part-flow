// Shared date/duration helpers for the Work Orders, Machines and
// monitoring views.
//
// Editable date fields hold ISO `YYYY-MM-DD` values (native
// <input type="date">); read-only presentation formats them as
// `Jul 24, 2026` (or `Jul 24` where space is tight). Formatting is
// string-based on purpose: no timezone conversion may ever shift a
// business date. A null/blank due date is valid data and renders as `—`.
//
// Derived time values (elapsed durations, due-date countdowns, days in
// production) are never stored: every surface derives them at render
// from the fixed source timestamp plus the shared UI clock
// (components/ui-clock.ts), through the helpers below — one derivation,
// one display language, no per-view drift.

import type { DueClass } from './view-models';

const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** `2026-07-24` → `Jul 24, 2026`; null/blank → `—`; else verbatim. */
export function formatIsoDate(iso: string | null): string {
  if (!iso) return '—';
  const match = ISO_DATE.exec(iso);
  if (!match) return iso;
  const [, year, month, day] = match;
  return `${MONTHS[Number(month) - 1]} ${day}, ${year}`;
}

/** `2026-07-24` → `Jul 24`; null/blank → `—`; else verbatim. */
export function formatIsoDateShort(iso: string | null): string {
  if (!iso) return '—';
  const match = ISO_DATE.exec(iso);
  if (!match) return iso;
  const [, , month, day] = match;
  return `${MONTHS[Number(month) - 1]} ${day}`;
}

/** Local date of `now` (epoch ms; default: current) as ISO `YYYY-MM-DD`. */
export function todayIso(nowMs: number = Date.now()): string {
  const now = new Date(nowMs);
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}

/**
 * Compact elapsed-duration language shared by every monitoring surface:
 * `<1m`, `18m`, `1h 24m`, `2d 03h`. Sub-minute durations render as
 * `<1m`; a negative duration (a timestamp newer than the last clock
 * tick — e.g. immediately after a movement) clamps to `<1m` instead of
 * going negative.
 */
export function formatDuration(elapsedMs: number): string {
  const minutes = Math.floor(elapsedMs / 60_000);
  if (!Number.isFinite(minutes) || minutes < 1) return '<1m';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`;
  }
  const days = Math.floor(hours / 24);
  return `${days}d ${String(hours % 24).padStart(2, '0')}h`;
}

/** Wall-clock time of an ISO timestamp as `HH:MM` (24-hour). */
export function formatTimeOfDay(iso: string): string {
  const date = new Date(iso);
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${hours}:${minutes}`;
}

/** Local date and time of an ISO instant as `Jul 24 08:15`: the date
 * and the time of day come from the same local clock, so an instant
 * whose UTC date differs from the local one shows the local date. */
export function formatTimestampShort(iso: string): string {
  return `${formatIsoDateShort(todayIso(new Date(iso).getTime()))} ${formatTimeOfDay(iso)}`;
}

/** Elapsed duration since an ISO timestamp, in the shared language. */
export function formatElapsedSince(sinceIso: string, nowMs: number): string {
  return formatDuration(nowMs - new Date(sinceIso).getTime());
}

/** Whole elapsed minutes since an ISO timestamp (sortable; min 0). */
export function elapsedMinutesSince(sinceIso: string, nowMs: number): number {
  const minutes = Math.floor((nowMs - new Date(sinceIso).getTime()) / 60_000);
  return Number.isFinite(minutes) ? Math.max(0, minutes) : 0;
}

/**
 * Whether a position has exceeded its expected duration (PROJECT_PROFILE
 * §17): the server states the fixed instant `expectedBy` at which the
 * effective expected duration of the position — of its earliest-due
 * portion, for an aggregated location — elapses; the shared UI clock
 * decides. Nothing is judged without an expected duration (null or
 * absent): there is no built-in fallback rule. Advisory only — a
 * warning highlight, never a gate on any action.
 */
export function exceedsExpectedDuration(
  expectedBy: string | null | undefined,
  nowMs: number,
): boolean {
  if (!expectedBy) return false;
  const deadline = new Date(expectedBy).getTime();
  return Number.isFinite(deadline) && nowMs > deadline;
}

/**
 * Whole calendar days from `fromIso` to `toIso` (both `YYYY-MM-DD`;
 * positive when `toIso` is later). String-parsed on purpose — no
 * timezone conversion may shift a business date. Null when either
 * value is not a plain ISO date.
 */
export function daysBetweenIso(fromIso: string, toIso: string): number | null {
  const from = ISO_DATE.exec(fromIso);
  const to = ISO_DATE.exec(toIso);
  if (!from || !to) return null;
  const fromUtc = Date.UTC(
    Number(from[1]),
    Number(from[2]) - 1,
    Number(from[3]),
  );
  const toUtc = Date.UTC(Number(to[1]), Number(to[2]) - 1, Number(to[3]));
  return Math.round((toUtc - fromUtc) / 86_400_000);
}

/**
 * Due Soon policy — the single source of truth for when a due date
 * starts reading as `soon`. The warning window scales with the
 * demand's total lead time (received → due): `leadTimePercent` of the
 * lead days, clamped into [`minDays`, `maxDays`]. The values are the
 * server's persisted Administration → Settings → Due Soon warning
 * policy (GUI_DESIGN §9), loaded as part of each view's ready state —
 * business logic receives a policy, and nothing in production code
 * restates the numbers (there is no frontend default).
 */
export interface DueSoonPolicy {
  /** Lower clamp in whole days (0–365). */
  minDays: number;
  /** Whole percent (1–100) of the received → due lead time. */
  leadTimePercent: number;
  /** Upper clamp in whole days (0–365, ≥ minDays). */
  maxDays: number;
}

/** Inclusive range of both warning-day clamps (bounds, never defaults). */
export const DUE_SOON_DAYS_RANGE = [0, 365] as const;
/** Inclusive range of the lead-time warning percentage. */
export const DUE_SOON_PERCENT_RANGE = [1, 100] as const;

function isWholeNumberIn(
  value: unknown,
  [min, max]: readonly [number, number],
): value is number {
  return (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    value >= min &&
    value <= max
  );
}

/** Whether `value` is a whole number of warning days in range. */
export function isDueSoonDays(value: unknown): value is number {
  return isWholeNumberIn(value, DUE_SOON_DAYS_RANGE);
}

/** Whether `value` is a whole lead-time warning percentage in range. */
export function isDueSoonPercent(value: unknown): value is number {
  return isWholeNumberIn(value, DUE_SOON_PERCENT_RANGE);
}

/**
 * Whether `value` is a well-formed Due Soon policy: whole numbers,
 * days 0–365, percent 1–100, minDays ≤ maxDays (the server's ranges,
 * PUT validation). Used by the API mapper and the Settings editor's
 * inline validation — one client-side statement of the ranges.
 */
export function isDueSoonPolicy(value: unknown): value is DueSoonPolicy {
  if (typeof value !== 'object' || value === null) return false;
  const { minDays, leadTimePercent, maxDays } = value as Record<
    string,
    unknown
  >;
  return (
    isDueSoonDays(minDays) &&
    isDueSoonPercent(leadTimePercent) &&
    isDueSoonDays(maxDays) &&
    minDays <= maxDays
  );
}

/**
 * Days-before-due threshold at or below which a due date reads as
 * `soon`: `ceil(totalLeadDays × leadTimePercent / 100)` in exact
 * integer arithmetic (a float ratio rounds 7 % of 100 days up to 8),
 * clamped into [`minDays`, `maxDays`]. An unknown or invalid lead time
 * (no received date, malformed dates, or a non-positive lead) falls
 * back to the policy's minimum warning window.
 */
export function dueSoonWindowDays(
  totalLeadDays: number | null,
  policy: DueSoonPolicy,
): number {
  if (
    totalLeadDays === null ||
    !Number.isFinite(totalLeadDays) ||
    totalLeadDays <= 0
  ) {
    return policy.minDays;
  }
  return Math.min(
    policy.maxDays,
    Math.max(
      policy.minDays,
      Math.ceil((totalLeadDays * policy.leadTimePercent) / 100),
    ),
  );
}

/** Lead-time context + policy required to derive the Due Soon window. */
export interface DueSoonContext {
  /**
   * Parent Work Order received date (ISO `YYYY-MM-DD`), or null where
   * the surface has no received date — the window then falls back to
   * the policy's minimum warning window.
   */
  received: string | null;
  policy: DueSoonPolicy;
}

/**
 * Derived due-date countdown in the one shared language: `N days left`
 * (`soon` within the lead-time-proportional Due Soon window derived
 * from `dueSoon`, `due today` at zero), `overdue N days` (`late`), or
 * `No due date` (`none`). Never stored — derived at render from the
 * fixed due/received dates plus the shared UI clock, with the policy
 * supplied by the caller (the server's Due Soon warning policy).
 */
export function dueCountdown(
  due: string | null,
  nowMs: number,
  dueSoon: DueSoonContext,
): { note: string; dueClass: DueClass | 'none' } {
  if (!due) return { note: 'No due date', dueClass: 'none' };
  const days = daysBetweenIso(todayIso(nowMs), due);
  if (days === null) return { note: due, dueClass: 'none' };
  if (days < 0) {
    return {
      note: `overdue ${-days} day${days === -1 ? '' : 's'}`,
      dueClass: 'late',
    };
  }
  if (days === 0) return { note: 'due today', dueClass: 'soon' };
  const totalLeadDays = dueSoon.received
    ? daysBetweenIso(dueSoon.received, due)
    : null;
  return {
    note: `${days} day${days === 1 ? '' : 's'} left`,
    dueClass:
      days <= dueSoonWindowDays(totalLeadDays, dueSoon.policy) ? 'soon' : 'ok',
  };
}

/**
 * Derived `Total Days` in production since the received date, in the
 * board's compact `N d` language (clamped at 0; `—` for bad input).
 */
export function daysInProductionNote(
  receivedIso: string,
  nowMs: number,
): string {
  const days = daysBetweenIso(receivedIso, todayIso(nowMs));
  return days === null ? '—' : `${Math.max(0, days)} d`;
}
