// Movement-history retention period wording of Administration →
// History archival & purge (PROJECT_PROFILE §28): a whole number of
// months stated in years and months, and the whole-number entry that the
// section's settings dialogs share. The ranges are the server's: it
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

/** The one refusal the settings dialogs make themselves. */
export const WHOLE_NUMBER_ERROR = 'Enter a whole number.';

/** A settings save whose answer was lost. */
export const SETTINGS_OUTCOME_UNKNOWN =
  'The server did not answer — the settings may or may not have been saved. Close this window to reload them before trying again.';

/** The whole number in `text`, else null (never rounded; the server
 * checks the range and answers its own message). */
export function parseWhole(text: string): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return Number.isInteger(value) ? value : null;
}
