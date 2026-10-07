// Movement-history retention period helpers of Administration →
// History archival & purge (PROJECT_PROFILE §28): parse the operator's
// whole-month entry and state it in years and months. The bounds are
// input validation only — the server re-validates and stays
// authoritative; no retention period is ever implied here (the stored
// value comes only from the server, and nothing has a default).

export const RETENTION_MONTHS_MIN = 12;
export const RETENTION_MONTHS_MAX = 1200;

/** Whole months 12–1200 held by the text, else null (never rounded or
 * clamped). */
export function parseRetentionMonths(text: string): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return Number.isInteger(value) &&
    value >= RETENTION_MONTHS_MIN &&
    value <= RETENTION_MONTHS_MAX
    ? value
    : null;
}

/** "1 year", "1 year 6 months", "10 years", "100 years" (months ≥ 12
 * only). */
export function formatRetentionPeriod(months: number): string {
  const years = Math.floor(months / 12);
  const rest = months % 12;
  const yearText = `${years} year${years === 1 ? '' : 's'}`;
  return rest === 0
    ? yearText
    : `${yearText} ${rest} month${rest === 1 ? '' : 's'}`;
}
