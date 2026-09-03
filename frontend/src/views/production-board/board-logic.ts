// Framework-independent Production Board presentation logic: rotation
// and refresh timing, display scaling and viewport-aware pagination.
// Row ORDER is the server's: the board renders its rows exactly in the
// canonical board order the read model delivers (GUI_DESIGN §5). Kept outside the component so
// it is directly testable.

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

/**
 * Auto-refresh period of the board feed (GUI_DESIGN §5): the read
 * model is re-read from the server this often while the board is
 * displayed — one request at a time, the next armed only after the
 * previous answer — and immediately again when connectivity returns.
 * A refresh that fails keeps the last complete data on screen and
 * marks the feed stale; nothing partial is ever shown.
 */
export const BOARD_REFRESH_MS = 15_000;

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
 * Minimum automatic display scale (post-v18): a deliberate near-zero
 * guard against degenerate measurements only — small screens are
 * MEANT to scale the board down as far as their width requires, so
 * the floor is the smallest practical value rather than a legibility
 * clamp.
 */
export const FIT_SCALE_MIN = 0.1;

/**
 * Automatic display scale (v18, scale-down added post-v18): the
 * factor that makes the table's intrinsic (max-content) width fill
 * the available board width, so the inter-column whitespace closes on
 * large displays AND the whole board shrinks to fit small screens
 * (phones/tablets) — every column keeps its full unwrapped content in
 * both directions. Clamped only by the near-zero FIT_SCALE_MIN guard,
 * and 1 whenever real measurements are unavailable (first paint
 * before layout, or DOM environments without layout).
 */
export function autoFitScale(
  boardWidth: number,
  intrinsicTableWidth: number,
): number {
  if (boardWidth <= 0 || intrinsicTableWidth <= 0) return 1;
  return Math.max(
    FIT_SCALE_MIN,
    (boardWidth - FIT_WIDTH_ALLOWANCE_PX) / intrinsicTableWidth,
  );
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
