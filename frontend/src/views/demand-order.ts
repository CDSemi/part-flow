// Canonical Work Order Demand ordering (PROJECT_PROFILE): manager-defined
// Hot priority is always the highest criterion. Within the same priority
// level:
//   1. demands WITH a due date come first, earliest due date first;
//   2. demands WITHOUT a due date come after all dated demands;
//   3. undated demands order by the parent Work Order received_date,
//      oldest first;
//   4. equal values fall back to a stable deterministic internal
//      tie-breaker (creation order / internal sequence).
//
// This module is framework-independent and production-safe (no mock
// data); the mock views apply it wherever due-date ordering is shown.

export interface DemandOrderKey {
  /** Manager-defined Hot rank (1 = highest); undefined when not Hot. */
  hotRank?: number;
  /** ISO `YYYY-MM-DD` due date, or null when the demand has none. */
  due: string | null;
  /** Parent Work Order received date, ISO `YYYY-MM-DD`. */
  received: string;
  /** Stable internal tie-breaker (creation order / internal id). */
  seq: number;
}

export function compareDemandOrder(a: DemandOrderKey, b: DemandOrderKey) {
  const hotA = a.hotRank ?? Number.POSITIVE_INFINITY;
  const hotB = b.hotRank ?? Number.POSITIVE_INFINITY;
  if (hotA !== hotB) return hotA - hotB;
  if (a.due !== null && b.due !== null && a.due !== b.due) {
    return a.due < b.due ? -1 : 1;
  }
  if ((a.due === null) !== (b.due === null)) return a.due === null ? 1 : -1;
  if (a.due === null && a.received !== b.received) {
    return a.received < b.received ? -1 : 1;
  }
  return a.seq - b.seq;
}
