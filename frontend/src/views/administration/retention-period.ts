// Movement-history retention period wording of Administration →
// History archival & purge (PROJECT_PROFILE §28): a whole number of
// months stated in years and months. The ranges are the server's: it
// validates every value and its messages are shown as given.

/** "6 months", "1 year", "1 year 6 months", "10 years", "100 years". */
export function formatRetentionPeriod(months: number): string {
  const years = Math.floor(months / 12);
  const rest = months % 12;
  const monthText = `${rest} month${rest === 1 ? '' : 's'}`;
  if (years === 0) return monthText;
  const yearText = `${years} year${years === 1 ? '' : 's'}`;
  return rest === 0 ? yearText : `${yearText} ${monthText}`;
}
