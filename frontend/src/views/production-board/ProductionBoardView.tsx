import './production-board.css';

import type { ReactNode, TouchEvent as ReactTouchEvent } from 'react';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { useConnectivity } from '../../app/connectivity-context';
import { useRouter } from '../../app/router-context';
import { getViewStatePreview } from '../../app/view-state';
import { AreaDot } from '../../components/indicators';
import { ThemeToggle } from '../../components/ThemeToggle';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { areaByKey } from '../../mocks/areas';
import {
  MOCK_BOARD_ROWS,
  MOCK_BOARD_ROWS_LONG,
} from '../../mocks/production-board';
import { useUiClock } from '../../components/ui-clock';
import {
  DEFAULT_DUE_SOON_POLICY,
  dueCountdown,
  dueSoonWindowDays,
  daysInProductionNote,
  elapsedMinutesSince,
  formatElapsedSince,
  formatIsoDateShort,
} from '../dates';
import type { MockBoardRow, MockLocationRow } from '../view-models';
import {
  autoFitScale,
  FALLBACK_PAGE_SIZE,
  fallbackPageBreaks,
  LONG_DWELL_MINUTES,
  pageBreaksByHeight,
  rotationDurationMs,
  sortBoardRows,
} from './board-logic';

/**
 * Subtle next-rotation indicator: a thin progress track plus a small
 * seconds-remaining label, rendered only while more than one page
 * exists. It reads the SAME deadline AND duration that drive the
 * actual page rotation (one timing source — never a second
 * unsynchronized timer; the duration varies per page with its row
 * count, so the track must scale from the same value) and owns its
 * own tick, so the complete board never rerenders per animation
 * frame. Under `prefers-reduced-motion` the track is hidden by
 * production-board.css while the seconds label remains.
 */
function RotationProgress({
  deadline,
  durationMs,
}: {
  deadline: number;
  durationMs: number;
}) {
  const [remaining, setRemaining] = useState(() =>
    Math.max(0, deadline - Date.now()),
  );
  useEffect(() => {
    const tick = () => setRemaining(Math.max(0, deadline - Date.now()));
    tick();
    const timer = window.setInterval(tick, 250);
    return () => window.clearInterval(timer);
  }, [deadline]);
  const pct = Math.max(0, Math.min(100, (remaining / durationMs) * 100));
  return (
    <span
      className="pb-rotate"
      title="Time until the next automatic page rotation (3 s per displayed row)"
    >
      <span className="pb-rotatetrack" aria-hidden="true">
        <i style={{ width: `${pct}%` }} />
      </span>
      <span className="pb-rotatesec">{Math.ceil(remaining / 1000)} s</span>
    </span>
  );
}

/**
 * Board title carrying the operational status (v17, restructured
 * post-v18) — the same presentation and meaning in the standard and
 * kiosk headers, styled like the Scan Station Area title. The title
 * reads `Production`; the `● Live` status sits directly after it,
 * slightly separated, in the CURRENT connection tone: while the
 * shared connectivity state is healthy the Area-indicator-style
 * rounded-square dot pulses with the shared connected heartbeat
 * (styles/global.css — identical "alive" behavior to the ONLINE
 * connectivity dot) and the status reads in the success tone; while
 * it is not, the dot stops in the warning tone, the status follows,
 * and the explicit `Feed stale — reconnecting` note appears — status
 * is never color-only. One status per header — never a second
 * `ONLINE` chip.
 */
function BoardTitle() {
  const { status } = useConnectivity();
  const stale = status !== 'connected';
  return (
    <h1 className={`live${stale ? ' stale' : ''}`}>
      Production
      <span className="livestatus">
        <span className="ld" aria-hidden="true" />
        Live
      </span>
      {stale ? (
        <span className="stalenote">Feed stale — reconnecting</span>
      ) : null}
    </h1>
  );
}

/**
 * Live clock on the shared second-precision UI clock (ui-clock.ts).
 * The optional `control` (kiosk: the compact theme toggle) renders
 * inside the time row, so it centers on the current-time text — never
 * on the taller time + date block (v17).
 */
function LiveClock({ control }: { control?: ReactNode }) {
  const now = new Date(useUiClock('second'));
  // One coherent clock block: the time leads, the date sits directly
  // beneath it as the secondary line (production-board.css).
  return (
    <span className="clockwrap">
      <span className="clockrow">
        {control}
        <span className="clock">
          {now.toLocaleTimeString(undefined, { hour12: false })}
        </span>
      </span>
      <span className="clockdate">
        {now.toLocaleDateString(undefined, {
          weekday: 'short',
          month: 'short',
          day: 'numeric',
          year: 'numeric',
        })}
      </span>
    </span>
  );
}

/**
 * Full user-facing wording for the state column — `on machine` marks
 * the active-Machine rows (never an abbreviation); canonical Movement
 * type names never appear in board text. Stocked rows keep their quiet
 * presentation (no tag — the `total … pcs stocked` line already states
 * it). The semantic tone lives on the row's QUANTITY value (v18,
 * production-board.css, via the locrow `st-*` state class), consistent
 * with the Scan Station statistics: queue → warning, on machine /
 * processing → information, done → success — while the state words
 * read as quiet secondary text. Color is never the only distinction —
 * the written state text remains beside every toned quantity.
 */
function locationStateLabel(state: MockLocationRow['state']): string {
  switch (state) {
    case 'machine':
      return 'on machine';
    case 'queue':
      return 'queue';
    case 'processing':
      return 'processing';
    case 'done':
      return 'done';
    default:
      return '';
  }
}

function BoardLocationRow({ loc }: { loc: MockLocationRow }) {
  // Derived per-location dwell time: fixed entry timestamp + shared
  // minute clock; `long` flags an unusually long dwell (≥ 3 days).
  const now = useUiClock('minute');
  const dwell = loc.since ? formatElapsedSince(loc.since, now) : '—';
  const dwellLong =
    loc.since !== null &&
    elapsedMinutesSince(loc.since, now) >= LONG_DWELL_MINUTES;
  const onMachine = loc.state === 'machine' && loc.machine !== undefined;
  // External activity (`plating`, `vendor`, …) replaces the generic
  // `processing` label with a light informational chip.
  const activityChip = loc.state === 'processing' && loc.activity;
  // The completing Machine (or External activity) on a done row is
  // secondary context only — it appears in the tooltip, never as the
  // current executor.
  const doneTitle =
    loc.state === 'done'
      ? loc.machine
        ? `Completed at ${loc.machine} — ready to transfer`
        : loc.activity
          ? `Completed (${loc.activity}) — ready to transfer`
          : 'Completed — ready to transfer'
      : undefined;
  return (
    // The explicit `st-*` state class lets the stylesheet tone the
    // row's quantity by state (v18).
    <div className={`locrow st-${loc.state}`}>
      <span className="lname">
        <AreaDot
          colorVar={areaByKey(loc.area)?.colorVar ?? 'var(--faint)'}
          size={11}
        />
        {onMachine ? (
          <span className="mchip" title={loc.machine}>
            {loc.machine}
          </span>
        ) : (
          loc.label
        )}
      </span>
      <span className="lqty">{loc.qty}</span>
      {activityChip ? (
        // External activity stays informational — the chip carries the
        // information tone like the other in-process states.
        <span className="ltag st-processing">
          <span className="actchip">{loc.activity}</span>
        </span>
      ) : (
        <span
          className={`ltag st-${loc.state}${loc.state === 'done' ? ' done' : ''}`}
          title={doneTitle}
        >
          {locationStateLabel(loc.state)}
        </span>
      )}
      <span className={`ltime ${dwellLong ? 'long' : ''}`}>{dwell}</span>
    </div>
  );
}

function BoardRowCells({ row, no }: { row: MockBoardRow; no: number }) {
  // Countdown, urgency and Total Days are DERIVED from the fixed due /
  // received dates and the shared minute clock — never stored.
  const now = useUiClock('minute');
  const dueInfo = row.totalStocked
    ? { note: '✓ stocked', dueClass: 'none' as const }
    : dueCountdown(row.due, now, {
        received: row.received,
        policy: DEFAULT_DUE_SOON_POLICY,
      });
  const urgent = dueInfo.dueClass === 'soon' || dueInfo.dueClass === 'late';
  return (
    <>
      <td className="cell-no">
        {/* The Hot flame lives in the No. column — the PN cell carries
            only the PN and description. */}
        <div
          className="no"
          aria-label={
            row.hotRank !== undefined ? `Row ${no}, Hot priority` : undefined
          }
        >
          {no}
          {row.hotRank !== undefined ? (
            <span className="hotflame" aria-hidden="true">
              {' '}
              🔥
            </span>
          ) : null}
        </div>
      </td>
      <td className="pn">
        <div className="part" title={row.pn}>
          {row.pn}
        </div>
        <div className="pname">{row.name}</div>
      </td>
      <td className="areas">
        <div className="loc">
          {row.locations.map((loc) => (
            <BoardLocationRow
              loc={loc}
              key={`${loc.area}-${loc.machine ?? ''}-${loc.state}`}
            />
          ))}
          {/* One continuous dashed separator spanning the complete
              location grid — never per-cell border fragments. */}
          <div className="locsep" aria-hidden="true" />
          <div className="locrow total">
            <span className="lname">total</span>
            <span className="lqty tot">{row.total}</span>
            <span className="ltag">
              pcs{row.totalStocked ? ' stocked' : ''}
            </span>
            <span className="ltime">
              {row.scrapped ? (
                // Scrap renders on the total line itself, anchored in
                // the right-hand time position as clear error-toned
                // plain text — no pill, no filled background, never an
                // extra row, never a ⊘ symbol.
                <span className="scraptext">{row.scrapped} scrapped</span>
              ) : null}
            </span>
          </div>
        </div>
      </td>
      <td className="due">
        <div className="d1">{formatIsoDateShort(row.due)}</div>
        {/* Only the urgency text blinks — never the PN, and the date
            itself stays steady. */}
        <div className={`d2 ${dueInfo.dueClass} ${urgent ? 'blink' : ''}`}>
          {dueInfo.note}
        </div>
      </td>
      <td className="cell-days">
        <div className="dtotal">{daysInProductionNote(row.received, now)}</div>
      </td>
      <td className="jobs">
        {row.jobs.map((job) => (
          <div className="j" key={job.job + job.meta}>
            {job.job} <span className="jm">{job.meta}</span>
          </div>
        ))}
      </td>
    </>
  );
}

function BoardColgroup() {
  return (
    <colgroup>
      <col className="col-no" />
      <col className="col-pn" />
      <col className="col-areas" />
      <col className="col-due" />
      <col className="col-days" />
      <col className="col-jobs" />
    </colgroup>
  );
}

/**
 * Minimum horizontal travel of a touch gesture that counts as a page
 * swipe (post-v18). Shorter gestures (taps, slight drags) and
 * predominantly vertical gestures (scrolling) never change pages.
 */
const SWIPE_MIN_DISTANCE_PX = 48;

// Column-header tooltips (v18): the former footer legend conventions
// live with the columns they describe — a small key/description panel
// shown on header hover (production-board.css .th-tip). Hover-only:
// the board is a read-only display with no tab order, and the hidden
// tooltip adds no height to the sticky header (pagination unaffected).
//
// The Due Date tooltip explains the Due Soon warning window. Every
// number in its copy — the percentage, the clamps, and the example
// windows — derives from the shared policy (views/dates,
// Administration-configurable later), never from duplicated literals.
const DUE_SOON = DEFAULT_DUE_SOON_POLICY;
const DUE_SOON_EXAMPLE_LEADS = [10, 30, 90];

function BoardHeadRow() {
  return (
    <tr>
      <th className="hastip">
        <span className="thlbl">No.</span>
        <span className="th-tip">
          <span className="tiprow">
            <span className="tipkey">🔥</span>
            <span className="tipdesc">Hot priority (highest first)</span>
          </span>
        </span>
      </th>
      <th className="pn">Part Number</th>
      <th>Areas &amp; Quantities · Time</th>
      <th className="hastip">
        <span className="thlbl">Due Date</span>
        <span className="th-tip">
          <span className="tiprow">
            <span className="tipkey">Blinking days count</span>
            <span className="tipdesc">due soon / overdue</span>
          </span>
          <span className="tiprow">
            <span className="tipkey">Due soon</span>
            <span className="tipdesc">
              within {Math.round(DUE_SOON.ratio * 100)}% of the lead time
              (received → due), {DUE_SOON.minDays}–{DUE_SOON.maxDays} days
            </span>
          </span>
          {DUE_SOON_EXAMPLE_LEADS.map((lead) => (
            <span className="tiprow" key={lead}>
              <span className="tipkey">{lead}-day lead</span>
              <span className="tipdesc">
                warns {dueSoonWindowDays(lead, DUE_SOON)} days ahead
              </span>
            </span>
          ))}
        </span>
      </th>
      <th>Total Days</th>
      <th>Job Numbers</th>
    </tr>
  );
}

// Read-only large-display view: mock rows, no interactive elements.
// Pagination is calculated from the actual available board height and
// the actual rendered row heights (a hidden measurement table renders
// every row); it recalculates on viewport/container resize, data
// changes and theme/font-metric changes.
export function ProductionBoardView() {
  const preview = getViewStatePreview();
  const { route, navigate } = useRouter();
  // Kiosk mode is an addressable route (`/production-board/kiosk`),
  // explicit in the router model — the top application navigation is
  // hidden by the App shell and the board renders its own coherent
  // kiosk header. Presentation only, never an authorization boundary.
  const kiosk = route.view === 'production-board' && route.mode === 'kiosk';

  const allRows: MockBoardRow[] = useMemo(() => {
    const raw =
      preview === 'empty' || preview === 'loading' || preview === 'error'
        ? []
        : preview === 'long'
          ? MOCK_BOARD_ROWS_LONG
          : MOCK_BOARD_ROWS;
    return sortBoardRows(raw);
  }, [preview]);

  const sectionRef = useRef<HTMLElement>(null);
  const headRef = useRef<HTMLDivElement>(null);
  const footRef = useRef<HTMLDivElement>(null);
  const measureTableRef = useRef<HTMLTableElement>(null);
  const measureBodyRef = useRef<HTMLTableSectionElement>(null);

  const [breaks, setBreaks] = useState<number[]>(() =>
    fallbackPageBreaks(allRows.length, FALLBACK_PAGE_SIZE),
  );

  // Automatic display scaling (v18): ON by default — the board is a
  // large-display view and fills wide screens instead of leaving the
  // columns half empty. The footer toggle turns it off (baseline
  // sizes). Presentation only, never persisted.
  const [autoScale, setAutoScale] = useState(true);

  const recalc = useCallback(() => {
    const section = sectionRef.current;
    const body = measureBodyRef.current;
    const table = measureTableRef.current;
    if (!section || !body || !table) return;
    // Every measurement below is taken at zoom 1, where offset
    // metrics, published pixel variables and viewport pixels all
    // share one coordinate space in every engine — the computed
    // auto-fit scale is applied once at the end, so measuring and
    // scaling never feed back into each other. This runs before
    // paint (useLayoutEffect / resize / ResizeObserver), so the
    // intermediate state is never visible.
    section.style.setProperty('zoom', '1');
    // Auto-fit display scale: the measurement table is forced to its
    // intrinsic max-content width for one synchronous read — the
    // width the board content needs with every column at its widest
    // real value and nothing newly wrapped. Scaling the board so
    // that width fills the actual board width closes the
    // inter-column whitespace on large displays AND shrinks the whole
    // board on small screens (post-v18 — phones/tablets get the full
    // table width at a reduced size instead of wrapping and
    // scrolling); CSS zoom multiplies
    // every length in the subtree (header, table and footer text,
    // paddings, chips, dots), so the layout scales uniformly and
    // content that fits at zoom 1 can never start wrapping at the
    // fitted scale. Guarded to environments that really support
    // zoom — elsewhere the board simply keeps its baseline.
    let scale = 1;
    if (
      autoScale &&
      typeof CSS !== 'undefined' &&
      typeof CSS.supports === 'function' &&
      CSS.supports('zoom', '1.5')
    ) {
      table.style.width = 'max-content';
      const intrinsic = table.offsetWidth;
      table.style.width = '';
      scale = autoFitScale(section.getBoundingClientRect().width, intrinsic);
    }
    // Shared content-driven location tracks: each PN row lays its
    // location fields out in its OWN grid, so cross-row alignment
    // needs a shared track width. The widest real value of each field
    // is measured in the measurement copy (which covers ALL rows and
    // always sizes from pure content — production-board.css) and
    // published as `--loc-*` custom properties consumed by every
    // visible row: identical tracks everywhere, sized by content, and
    // stable across page rotation. Skipped in environments without
    // real layout (all widths 0), where the CSS fallbacks apply.
    for (const field of ['lname', 'lqty', 'ltag', 'ltime'] as const) {
      let widest = 0;
      for (const el of table.querySelectorAll<HTMLElement>(`.loc .${field}`)) {
        // On a narrow board the measurement grid itself is compressed
        // toward its ch minimums, so the track width (offsetWidth) can
        // be SMALLER than the nowrap content — exactly the overlap the
        // shared widths must prevent. An overflowing field is measured
        // by its scroll width plus the trailing padding the overflow
        // drops, so the published track always fits the content.
        let width = el.offsetWidth;
        if (el.scrollWidth > el.clientWidth) {
          width = Math.max(
            width,
            el.scrollWidth +
              parseFloat(window.getComputedStyle(el).paddingRight || '0'),
          );
        }
        widest = Math.max(widest, width);
      }
      if (widest > 0) {
        section.style.setProperty(`--loc-${field}`, `${Math.ceil(widest)}px`);
      }
    }
    const rowHeights = Array.from(body.rows, (row) => row.offsetHeight);
    const headHeight = headRef.current?.offsetHeight ?? 0;
    const theadHeight = table.tHead?.offsetHeight ?? 0;
    const footHeight = footRef.current?.offsetHeight ?? 0;
    // The visual height budget shrinks by the display scale: every
    // measured height above is a zoom-1 pixel that renders `scale`
    // times taller, so the viewport budget is divided by the scale
    // before the zoom-1 heights are subtracted — the existing
    // height-aware pagination then recomputes rows-per-page for the
    // scaled typography on its own.
    const available =
      (window.innerHeight - section.getBoundingClientRect().top) / scale -
      headHeight -
      theadHeight -
      footHeight;
    // Apply the fit AFTER all zoom-1 measurements: one uniform zoom on
    // the board root scales header, table and footer together.
    section.style.setProperty('zoom', String(scale));
    const usable =
      available > 0 &&
      rowHeights.length > 0 &&
      rowHeights.every((height) => height > 0);
    const next = usable
      ? pageBreaksByHeight(rowHeights, available)
      : fallbackPageBreaks(rowHeights.length, FALLBACK_PAGE_SIZE);
    setBreaks((current) =>
      current.length === next.length &&
      current.every((value, i) => value === next[i])
        ? current
        : next,
    );
  }, [autoScale]);

  // Derived row content (dwell times, countdowns) changes on the
  // shared minute tick, which can change measured widths/heights — the
  // layout effect below depends on the same tick, so the measurement
  // table is re-read after each derived-content update.
  const nowMinute = useUiClock('minute');

  // Kiosk mode is read inside the layout effect indirectly: toggling
  // it swaps the header and footer structure, so their measured
  // heights change — the effect depends on `kiosk` so pagination
  // recalculates on the mode switch (v15).
  // Recalculate after every commit that can change row heights (data,
  // theme class, font metrics) — the measurement table re-renders in
  // the same commit, so useLayoutEffect reads fresh heights.
  useLayoutEffect(() => {
    recalc();
  }, [recalc, allRows, kiosk, nowMinute]);

  useEffect(() => {
    window.addEventListener('resize', recalc);
    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => recalc());
      if (sectionRef.current) observer.observe(sectionRef.current);
      if (measureTableRef.current) observer.observe(measureTableRef.current);
    }
    return () => {
      window.removeEventListener('resize', recalc);
      observer?.disconnect();
    };
  }, [recalc]);

  const pageCount = Math.max(1, breaks.length);

  const [page, setPage] = useState(0);
  // Every manual page change bumps the epoch, which restarts the
  // rotation deadline — manual navigation restarts the auto-rotation
  // timer instead of racing it.
  const [rotateEpoch, setRotateEpoch] = useState(0);
  // Clamp the active page whenever the page structure changes.
  useEffect(() => {
    setPage((current) => Math.min(current, pageCount - 1));
  }, [pageCount]);
  const safePage = Math.min(page, pageCount - 1);
  // Page-change transition bookkeeping (see the visible-table render):
  // the previously displayed page and the current direction class.
  const prevPageRef = useRef(safePage);
  const pageDirRef = useRef('');
  // Rows displayed on the CURRENT page — the rotation duration derives
  // from it (v15): 3 s per displayed row with a 6 s floor
  // (board-logic.ts), recomputed per page instead of one constant for
  // all pages. A plain number, so the rotation effect below re-arms
  // only when the actual count changes.
  const rowsOnPage = Math.max(
    0,
    (breaks[safePage + 1] ?? allRows.length) - (breaks[safePage] ?? 0),
  );
  const rotateMs = rotationDurationMs(rowsOnPage);
  // One timing source for rotation AND its indicator: every displayed
  // page arms one deadline (now + the page's own duration); the
  // timeout that fires at that deadline advances the page, and
  // RotationProgress renders the countdown to the same deadline and
  // scales its track from the same duration. Manual navigation
  // (buttons, dots, arrow keys) changes the page / bumps the epoch,
  // which re-arms the deadline — indicator and rotation can never
  // drift apart. Rotation exists only while more than one page
  // exists; automatic rotation wraps around, manual navigation never
  // does.
  const [rotateDeadline, setRotateDeadline] = useState<number | null>(null);
  useEffect(() => {
    if (pageCount <= 1) {
      setRotateDeadline(null);
      return;
    }
    setRotateDeadline(Date.now() + rotateMs);
    const timer = window.setTimeout(
      () => setPage((current) => (current + 1) % pageCount),
      rotateMs,
    );
    return () => window.clearTimeout(timer);
  }, [pageCount, rotateEpoch, safePage, rotateMs]);

  /** Manual page change: clamped, non-wrapping, restarts the timer. */
  const goToPage = useCallback((target: number, currentPageCount: number) => {
    if (target < 0 || target >= currentPageCount) return;
    setPage(target);
    setRotateEpoch((epoch) => epoch + 1);
  }, []);

  // Physical-keyboard page navigation: ArrowLeft/ArrowRight work
  // regardless of focus (the board has no normal text-entry workflow),
  // never wrap, ignore modifier chords, stay inert while a modal
  // dialog is active, and restart the rotation timer like the buttons.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      if (event.ctrlKey || event.altKey || event.metaKey) return;
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) {
        return;
      }
      const target = safePage + (event.key === 'ArrowLeft' ? -1 : 1);
      if (target < 0 || target >= pageCount) return;
      // A valid page change consumes the key — no horizontal scroll.
      event.preventDefault();
      goToPage(target, pageCount);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [safePage, pageCount, goToPage]);

  // Touch swipe page navigation (post-v18): a horizontal swipe
  // anywhere on the board changes pages exactly like the footer
  // buttons and the arrow keys — clamped, non-wrapping (goToPage
  // ignores a page that does not exist), restarting the rotation
  // timer. A short gesture, a predominantly vertical gesture
  // (scrolling), and multi-touch gestures (pinch zoom) are ignored.
  const swipeStart = useRef<{ x: number; y: number } | null>(null);
  const onTouchStart = useCallback((event: ReactTouchEvent<HTMLElement>) => {
    swipeStart.current =
      event.touches.length === 1
        ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
        : null;
  }, []);
  const onTouchCancel = useCallback(() => {
    swipeStart.current = null;
  }, []);
  const onTouchEnd = useCallback(
    (event: ReactTouchEvent<HTMLElement>) => {
      const start = swipeStart.current;
      swipeStart.current = null;
      const touch = event.changedTouches[0];
      if (!start || !touch) return;
      const dx = touch.clientX - start.x;
      const dy = touch.clientY - start.y;
      if (
        Math.abs(dx) < SWIPE_MIN_DISTANCE_PX ||
        Math.abs(dx) <= Math.abs(dy)
      ) {
        return;
      }
      goToPage(safePage + (dx < 0 ? 1 : -1), pageCount);
    },
    [goToPage, safePage, pageCount],
  );

  // Ctrl+Shift+K toggles between the standard and kiosk routes —
  // mirroring the Scan Station mode shortcut. Inert inside unrelated
  // text-entry controls and while a modal dialog is active; never an
  // authorization mechanism. The board state (current page) lives in
  // this component, which stays mounted across the toggle, so the
  // active page is preserved.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!event.ctrlKey || !event.shiftKey || event.altKey || event.metaKey) {
        return;
      }
      if (event.key !== 'K' && event.key !== 'k') return;
      const target = event.target;
      if (target instanceof HTMLElement) {
        const tag = target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        if (target.isContentEditable || target.closest('[role="dialog"]')) {
          return;
        }
      }
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) {
        return;
      }
      event.preventDefault();
      navigate(kiosk ? '/production-board' : '/production-board/kiosk');
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [kiosk, navigate]);

  if (preview === 'loading') {
    return (
      <section className="pb" aria-label="Production Board">
        <LoadingState label="Loading Production Board" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="pb" aria-label="Production Board">
        <ErrorState
          message="The production feed could not be loaded."
          detail="The board retries automatically; data shown after recovery is complete and consistent."
        />
      </section>
    );
  }

  const start = breaks[safePage] ?? 0;
  const end = breaks[safePage + 1] ?? allRows.length;
  const rows = allRows.slice(start, end);
  // Direction-aware page-change transition (post-v18): the visible
  // table is keyed by the page, so every page change remounts it and
  // replays a one-shot entry animation sliding in from the travel
  // direction — forward toward a higher page, backward toward a lower
  // one, and the auto-rotation's wrap from the last page back to the
  // first stays forward (the rotation keeps cycling in one
  // direction). The class persists until the NEXT change (the key
  // remount is what replays it), so unrelated re-renders never cancel
  // a running animation.
  if (prevPageRef.current !== safePage) {
    pageDirRef.current =
      safePage > prevPageRef.current ||
      (prevPageRef.current === pageCount - 1 && safePage === 0)
        ? ' pb-pagefwd'
        : ' pb-pageback';
    prevPageRef.current = safePage;
  }
  const activePns = allRows.filter((r) => !r.totalStocked).length;
  const inProduction = allRows
    .filter((r) => !r.totalStocked)
    .reduce((s, r) => s + r.total, 0);
  const stocked = allRows
    .filter((r) => r.totalStocked)
    .reduce((s, r) => s + r.total, 0);
  const scrappedTotal = allRows.reduce((s, r) => s + (r.scrapped ?? 0), 0);

  return (
    <section
      className={`pb${kiosk ? ' kiosk' : ''}`}
      aria-label="Production Board"
      ref={sectionRef}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      onTouchCancel={onTouchCancel}
    >
      {/* Header (restructured v17): ONE identical identity group in
          both presentations, styled like the Scan Station header —
          the Department line above the `Live Production` board title
          (BoardTitle: Area-indicator-style dot with the shared
          heartbeat, never a second `ONLINE` chip); a flexible
          center; then the clock zone. Kiosk mode renders the shared
          borderless Dark/Light control inside the clock's time row
          (centered on the time text — what the hidden navigation
          would otherwise provide) and NO app brand — the board
          identity carries the header. The explicit enter/exit action
          lives in the footer controls row. Two presentations of one
          board, not two boards. */}
      <div className={`pb-head${kiosk ? ' pbk-head' : ''}`} ref={headRef}>
        <div className="pb-headid">
          <div className="dept">Machine Shop</div>
          <BoardTitle />
        </div>
        <span className="spacer" />
        <LiveClock control={kiosk ? <ThemeToggle compact /> : undefined} />
      </div>
      {rows.length === 0 ? (
        <EmptyState message="No active production in this Department." />
      ) : (
        <table key={safePage} className={`pb-table${pageDirRef.current}`}>
          <BoardColgroup />
          <thead>
            <BoardHeadRow />
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr
                key={row.pn}
                className={
                  row.hotRank === 1
                    ? 'hotrow1'
                    : row.hotRank === 2
                      ? 'hotrow2'
                      : undefined
                }
              >
                <BoardRowCells row={row} no={start + index + 1} />
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {allRows.length > 0 ? (
        // Hidden measurement copy: every row renders here (identical
        // classes → identical metrics) so pages can be partitioned from
        // real heights without clipping.
        <div className="pb-measure" aria-hidden="true">
          <table className="pb-table" ref={measureTableRef}>
            <BoardColgroup />
            <thead>
              <BoardHeadRow />
            </thead>
            <tbody ref={measureBodyRef}>
              {allRows.map((row, index) => (
                <tr key={row.pn}>
                  <BoardRowCells row={row} no={index + 1} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {/* Footer: a normal flex child anchored to the bottom of the
          board viewport (margin-top: auto — never position: fixed), so
          it stays part of layout, never covers table content, and its
          height stays inside the pagination measurement. Two readable
          rows: pagination controls + aggregate totals, then the legend
          row carrying the user-facing sorting rule (v18 — the other
          conventions live in the column-header tooltips). */}
      <div className="pb-foot" ref={footRef}>
        <div className="pb-footrow">
          <span>
            {pageCount > 1
              ? `Page ${safePage + 1} / ${pageCount}`
              : 'Page 1 / 1'}
          </span>
          {/* Rotation countdown: same deadline as the actual page
              rotation, hidden when only one page exists. */}
          {pageCount > 1 && rotateDeadline !== null ? (
            <RotationProgress deadline={rotateDeadline} durationMs={rotateMs} />
          ) : null}
          {/* Manual page navigation: Previous/Next never wrap (automatic
              rotation still does) and every manual change restarts the
              rotation timer. ArrowLeft/ArrowRight mirror the buttons. */}
          <span className="pgnav">
            <button
              className="pgbtn"
              aria-label="Previous page"
              disabled={safePage === 0}
              onClick={() => goToPage(safePage - 1, pageCount)}
            >
              ‹
            </button>
            {Array.from({ length: pageCount }, (_, i) => (
              <button
                key={i}
                className={`pgdot ${i === safePage ? 'on' : ''}`}
                aria-label={`Go to page ${i + 1}`}
                aria-current={i === safePage ? 'page' : undefined}
                onClick={() => goToPage(i, pageCount)}
              />
            ))}
            <button
              className="pgbtn"
              aria-label="Next page"
              disabled={safePage === pageCount - 1}
              onClick={() => goToPage(safePage + 1, pageCount)}
            >
              ›
            </button>
          </span>
          <span className="spacer" />
          {/* Restrained inline aggregate summary: numeric values carry
              the weight (monospace, subtle semantic tones), the labels
              stay quiet, separators come from CSS — no pills, clearly
              subordinate to the production table. */}
          <span className="pb-agg">
            <span className="aggitem">
              <b className="aggnum">{activePns}</b> active PNs
            </span>
            <span className="aggitem">
              <b className="aggnum m">{inProduction}</b> pcs in production
            </span>
            <span className="aggitem">
              <b className="aggnum d">{stocked}</b> pcs stocked
            </span>
            <span className="aggitem">
              <b className="aggnum e">{scrappedTotal}</b> pcs scrapped
            </span>
          </span>
          {/* The kiosk mode toggle lives in the footer controls row —
              a normal layout child, so the footer height stays inside
              the pagination measurement; the shortcut lives in the
              tooltip instead of a legend line. An On/Off slide switch
              (v18) in the shared footer-switch family: aria-checked
              mirrors the active route, switching navigates between
              the standard and kiosk routes (v17) — the same position
              and presentation in both modes. */}
          <button
            type="button"
            role="switch"
            aria-checked={kiosk}
            aria-label="Kiosk mode"
            title={
              kiosk
                ? 'Exit kiosk mode (Ctrl+Shift+K)'
                : 'Enter kiosk mode (Ctrl+Shift+K)'
            }
            className={`pb-switch pb-kioskswitch${kiosk ? ' on' : ''}`}
            onClick={() =>
              navigate(kiosk ? '/production-board' : '/production-board/kiosk')
            }
          >
            <span className="swlbl">Kiosk</span>
            <span className="track" aria-hidden="true">
              <span className="knob" />
            </span>
            <span className="swstate">{kiosk ? 'On' : 'Off'}</span>
          </button>
          {/* Automatic display scaling switch (v18): the same On/Off
              slide-control language as the Machines Maintenance switch
              (role="switch", track + knob + written state). ON scales
              the whole board (header, table, footer) uniformly until
              the table content fills the display width; OFF returns to
              the baseline sizes. Presentation only. */}
          <button
            type="button"
            role="switch"
            aria-checked={autoScale}
            aria-label="Automatic display scaling"
            title="Scale the board to fill the display width"
            className={`pb-switch pb-scaleswitch${autoScale ? ' on' : ''}`}
            onClick={() => setAutoScale((current) => !current)}
          >
            <span className="swlbl">Auto scale</span>
            <span className="track" aria-hidden="true">
              <span className="knob" />
            </span>
            <span className="swstate">{autoScale ? 'On' : 'Off'}</span>
          </button>
        </div>
        {/* Legend row (v18): only the user-facing sorting rule remains
            — the flame / blink / dash conventions moved into the
            column-header tooltips of the columns they describe. */}
        <div className="pb-footrow legend">
          <span className="leg sort">
            Sorted: Hot rank first → earliest due date → no due date by oldest
            received date.
          </span>
        </div>
      </div>
    </section>
  );
}
