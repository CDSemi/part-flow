// Est. time language of the Planned Route editor (GUI_DESIGN §13.2).
//
// A step's advisory estimated time is entered and shown in the shared
// duration tokens of the monitoring surfaces (`45m`, `1h 24m`,
// `2d 03h` — `formatDuration` in views/dates.ts), but LOSSLESSLY: the
// shared monitoring format drops the minutes at one day and above,
// which an editor must never do, so `1d 02h 30m` keeps them. Parsing
// accepts exactly the token language (`45m`, `4h`, `1h 30m`, `2d 03h`,
// `1d2h30m`) and round-trips the formatted text.
//
// Production-safe: pure logic only, no mock data, no framework imports.

const MINUTES_PER_HOUR = 60;
const MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR;

/** `<int>d`, `<int>h`, `<int>m` in that order, any non-empty subset,
 * optional whitespace between the tokens (never inside one). */
const ESTIMATE_PATTERN = /^(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?$/;

const pad = (value: number): string => String(value).padStart(2, '0');

/** Whole minutes as Est. time text: `45m`, `4h 00m`, `2d 03h`,
 * `1d 02h 30m`. */
export function formatEstimate(minutes: number): string {
  if (minutes < MINUTES_PER_HOUR) return `${minutes}m`;
  if (minutes < MINUTES_PER_DAY) {
    return `${Math.floor(minutes / MINUTES_PER_HOUR)}h ${pad(
      minutes % MINUTES_PER_HOUR,
    )}m`;
  }
  const days = Math.floor(minutes / MINUTES_PER_DAY);
  const hours = Math.floor((minutes % MINUTES_PER_DAY) / MINUTES_PER_HOUR);
  const rest = minutes % MINUTES_PER_HOUR;
  return `${days}d ${pad(hours)}h${rest > 0 ? ` ${pad(rest)}m` : ''}`;
}

/** Whole minutes of Est. time text, or null for anything that is not
 * the token language or not longer than zero. */
export function parseEstimate(text: string): number | null {
  const match = ESTIMATE_PATTERN.exec(text.trim());
  if (!match) return null;
  const [, days, hours, minutes] = match;
  if (days === undefined && hours === undefined && minutes === undefined) {
    return null;
  }
  const total =
    Number(days ?? 0) * MINUTES_PER_DAY +
    Number(hours ?? 0) * MINUTES_PER_HOUR +
    Number(minutes ?? 0);
  return total > 0 && Number.isSafeInteger(total) ? total : null;
}
