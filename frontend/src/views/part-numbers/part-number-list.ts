// The bounded server-side Part Numbers list (GUI_DESIGN §14.1, §14.5):
// the bounds and timing shared by the Part Numbers table and the flat
// list of Print barcodes, and the sort choices of Print barcodes.
//
// Production-safe: no mock data, no framework imports.

import type { PartNumberSort } from '../../api/part-numbers';

/** First page size and the most rows a list ever shows. */
export const PART_NUMBERS_PAGE_SIZE = 100;
export const PART_NUMBERS_MAX_ROWS = 200;
/** Typing pause before a search reaches the server. */
export const SEARCH_DEBOUNCE_MS = 250;

/** The sort choices of the flat Print barcodes list, in menu order. */
export const PART_NUMBER_SORT_OPTIONS: readonly {
  value: PartNumberSort;
  label: string;
}[] = [
  { value: 'part_number', label: 'Part Number (A–Z)' },
  { value: 'name', label: 'Name (A–Z)' },
  { value: 'added', label: 'Date added (newest first)' },
  { value: 'last_used', label: 'Last used in a Work Order (newest first)' },
];

/** A server timestamp as the local calendar date `YYYY-MM-DD`. */
export function localDate(iso: string): string {
  const date = new Date(iso);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
