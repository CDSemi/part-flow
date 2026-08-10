// Shared date/duration helpers for the mock views.
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
 * demand's total lead time (received → due): `ratio` of the lead
 * days, clamped into [`minDays`, `maxDays`]. These are configuration
 * values owned by the future Administration → Policies → Due Soon
 * warning settings (GUI_DESIGN §9) — business logic receives a policy
 * and never hard-codes the numbers.
 */
export interface DueSoonPolicy {
  /** Lower clamp — the warning window never shrinks below this. */
  minDays: number;
  /** Fraction (0–1) of the total lead time that reads as `soon`. */
  ratio: number;
  /** Upper clamp — the warning window never grows beyond this. */
  maxDays: number;
}

/**
 * Initial Due Soon defaults (Minimum warning days = 2, Lead-time
 * warning percentage = 15%, Maximum warning days = 7). This object is
 * the stand-in for the Administration-configured policy until that
 * page exists — call sites pass it in; nothing else may restate the
 * numbers.
 */
export const DEFAULT_DUE_SOON_POLICY: DueSoonPolicy = {
  minDays: 2,
  ratio: 0.15,
  maxDays: 7,
};

/**
 * Days-before-due threshold at or below which a due date reads as
 * `soon`: `ceil(totalLeadDays × ratio)` clamped into
 * [`minDays`, `maxDays`]. An unknown or invalid lead time (no
 * received date, malformed dates, or a non-positive lead) falls back
 * to the policy's minimum warning window.
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
    Math.max(policy.minDays, Math.ceil(totalLeadDays * policy.ratio)),
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
 * supplied by the caller (future Administration configuration).
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
