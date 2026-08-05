// Shared time anchors for the development mock datasets.
//
// The mocks author RELATIVE offsets ("entered this Area 45 minutes
// ago", "due in 2 days") and resolve them ONCE at module load into the
// fixed ISO timestamps the data model stores. From then on the values
// are ordinary fixed data: the views derive elapsed durations and due
// countdowns from them through the shared UI clock, exactly as they
// will from real backend timestamps — and the sample data always
// demonstrates the intended mix of states (due soon / overdue / long
// in an Area) regardless of when the dev build is opened.

import { todayIso } from '../views/dates';

/** One load-time anchor for every mock module — values stay coherent. */
const ANCHOR_MS = Date.now();

/** ISO timestamp `minutes` ago (mock Area-entry / state anchors). */
export function minutesAgoIso(minutes: number): string {
  return new Date(ANCHOR_MS - minutes * 60_000).toISOString();
}

/** ISO `YYYY-MM-DD` local date `days` from today (negative = past). */
export function isoDateIn(days: number): string {
  const date = new Date(ANCHOR_MS);
  // Calendar-day arithmetic (not ms), so a DST boundary between the
  // anchor and the target can never shift the resulting date.
  date.setDate(date.getDate() + days);
  return todayIso(date.getTime());
}
