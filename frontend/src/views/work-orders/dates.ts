// Date helpers for the Work Orders view.
//
// Editable date fields hold ISO `YYYY-MM-DD` values (native
// <input type="date">); read-only presentation formats them as
// `Jul 24, 2026`. Formatting is string-based on purpose: no timezone
// conversion may ever shift a business date.

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

/** `2026-07-24` → `Jul 24, 2026`; non-ISO input is shown verbatim. */
export function formatIsoDate(iso: string): string {
  const match = ISO_DATE.exec(iso);
  if (!match) return iso || '—';
  const [, year, month, day] = match;
  return `${MONTHS[Number(month) - 1]} ${day}, ${year}`;
}

/** Today's local date as ISO `YYYY-MM-DD`. */
export function todayIso(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}
