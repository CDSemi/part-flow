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
 * with 7 rows stays 7 × the seconds per displayed row, never one fixed
 * constant for every page — with a floor so a near-empty last page
 * never flashes past. The values are the Department's display settings
 * (Administration → Department display settings, configured PER
 * DEPARTMENT — never globally, GUI_DESIGN §5 / §9), delivered with the
 * board feed; nothing here restates them. Whole seconds.
 */
export interface BoardRotationTiming {
  secondsPerRow: number;
  minPageSeconds: number;
}

/** Inclusive range of the seconds per displayed row (bounds only). */
export const BOARD_SECONDS_PER_ROW_RANGE = [1, 60] as const;
/** Inclusive range of the minimum page dwell in seconds (bounds only). */
export const BOARD_MIN_PAGE_SECONDS_RANGE = [1, 300] as const;

function isWholeNumberIn(
  value: unknown,
  [min, max]: readonly [number, number],
): value is number {
  return (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    value >= min &&
    value <= max
  );
}

/** Whether `value` is a whole number of seconds per displayed row in range. */
export function isBoardSecondsPerRow(value: unknown): value is number {
  return isWholeNumberIn(value, BOARD_SECONDS_PER_ROW_RANGE);
}

/** Whether `value` is a whole minimum page dwell in seconds in range. */
export function isBoardMinPageSeconds(value: unknown): value is number {
  return isWholeNumberIn(value, BOARD_MIN_PAGE_SECONDS_RANGE);
}

/**
 * Whole seconds: per row 1–60, minimum dwell 1–300 (the server's
 * ranges). Used by the API mapper and the rotation editor's inline
 * validation.
 */
export function isBoardRotationTiming(
  value: unknown,
): value is BoardRotationTiming {
  if (typeof value !== 'object' || value === null) return false;
  const { secondsPerRow, minPageSeconds } = value as Record<string, unknown>;
  return (
    isBoardSecondsPerRow(secondsPerRow) && isBoardMinPageSeconds(minPageSeconds)
  );
}

/**
 * Auto-refresh period of the board feed (GUI_DESIGN §5): the read
 * model is re-read from the server this often while the board is
 * displayed — one request at a time, the next armed only after the
 * previous answer — and immediately again when connectivity returns.
 * A refresh that fails keeps the last complete data on screen and
 * marks the feed stale; nothing partial is ever shown.
 */
export const BOARD_REFRESH_MS = 15_000;

/** Rotation dwell of a page showing `rowCount` rows: proportional, with
 * the Department's floor. */
export function rotationDurationMs(
  rowCount: number,
  rotation: BoardRotationTiming,
): number {
  return (
    Math.max(rotation.minPageSeconds, rowCount * rotation.secondsPerRow) * 1000
  );
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
