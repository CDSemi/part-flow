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
 * Automatic page rotation timing (v15): the dwell time of a page is
 * proportional to the number of rows it actually displays — a page
 * with 7 rows stays 7 × ROTATE_MS_PER_ROW, never one fixed constant
 * for every page — with a floor so a near-empty last page never
 * flashes past. These named defaults are deliberately NOT inlined in
 * the component: a future Administration page exposes them as
 * Department display settings, configured PER DEPARTMENT — never
 * globally (GUI_DESIGN §5 / §9, decided post-v18); the board consumes
 * only `rotationDurationMs`.
 */
/**
 * A location dwell of at least this long (3 days) is flagged as an
 * unusually long stay (`ltime.long` warning tone) — derived at render
 * from the fixed entry timestamp, never stored.
 */
export const LONG_DWELL_MINUTES = 3 * 24 * 60;

export const ROTATE_MS_PER_ROW = 3_000;
export const ROTATE_MS_MIN = 6_000;

/** Rotation dwell time for a page showing `rowCount` rows. */
export function rotationDurationMs(rowCount: number): number {
  return Math.max(ROTATE_MS_MIN, rowCount * ROTATE_MS_PER_ROW);
}

/**
 * Fixed width allowance subtracted before computing the auto-fit
 * scale: the visible table can need a few pixels more than the
 * measured copy (the Hot-row accent border is absent there, plus
 * sub-pixel rounding), which would otherwise wrap the Job Numbers
 * column at an exact fit.
 */
const FIT_WIDTH_ALLOWANCE_PX = 8;

/**
 * Automatic display scale (v18): the factor that makes the table's
 * intrinsic (max-content) width fill the available board width, so the
 * inter-column whitespace closes on large displays while every column
 * keeps its full unwrapped content. Never below 1 — a viewport
 * narrower than the content keeps the baseline size and the existing
 * wrapping behavior — and 1 whenever real measurements are unavailable
 * (first paint before layout, or DOM environments without layout).
 */
export function autoFitScale(
  boardWidth: number,
  intrinsicTableWidth: number,
): number {
  if (boardWidth <= 0 || intrinsicTableWidth <= 0) return 1;
  return Math.max(
    1,
    (boardWidth - FIT_WIDTH_ALLOWANCE_PX) / intrinsicTableWidth,
  );
}

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
