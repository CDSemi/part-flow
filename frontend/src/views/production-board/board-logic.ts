// Framework-independent Production Board presentation logic: row
// ordering and viewport-aware pagination. Kept outside the component so
// it is directly testable.

import { compareDemandOrder } from '../demand-order';
import type { MockBoardRow } from '../view-models';

/**
 * Rows/page used when real measurements are unavailable (first paint
 * before layout, or DOM environments without layout such as jsdom).
 */
export const FALLBACK_PAGE_SIZE = 10;

/**
 * Board row order: canonical demand order (Hot rank → earliest due date
 * → undated by WO received date → stable creation order). Stocked rows
 * are completed demand and stay after every active row regardless of
 * their historical due date — a board presentation choice, not a
 * business rule.
 */
export function sortBoardRows(rows: readonly MockBoardRow[]): MockBoardRow[] {
  return rows
    .map((row, seq) => ({ row, seq }))
    .sort((a, b) => {
      if (!!a.row.totalStocked !== !!b.row.totalStocked) {
        return a.row.totalStocked ? 1 : -1;
      }
      return compareDemandOrder(
        {
          hotRank: a.row.hotRank,
          due: a.row.due,
          received: a.row.received,
          seq: a.seq,
        },
        {
          hotRank: b.row.hotRank,
          due: b.row.due,
          received: b.row.received,
          seq: b.seq,
        },
      );
    })
    .map((entry) => entry.row);
}

/**
 * Partition rows into pages that fit `availableHeight`, using the
 * actual rendered height of every row (rows may hold different numbers
 * of Area/Machine lines and wrapped descriptions). Returns the start
 * index of each page. Every page holds at least one row — a row taller
 * than the available height gets a page of its own instead of clipping
 * others.
 */
export function pageBreaksByHeight(
  rowHeights: readonly number[],
  availableHeight: number,
): number[] {
  const breaks: number[] = [];
  let used = 0;
  let rowsInPage = 0;
  for (let i = 0; i < rowHeights.length; i += 1) {
    const height = rowHeights[i];
    if (rowsInPage === 0 || used + height > availableHeight) {
      breaks.push(i);
      used = height;
      rowsInPage = 1;
    } else {
      used += height;
      rowsInPage += 1;
    }
  }
  return breaks;
}

/** Fixed-size chunking used when measurements are unavailable. */
export function fallbackPageBreaks(
  rowCount: number,
  pageSize: number,
): number[] {
  const breaks: number[] = [];
  for (let i = 0; i < rowCount; i += pageSize) breaks.push(i);
  return breaks;
}
