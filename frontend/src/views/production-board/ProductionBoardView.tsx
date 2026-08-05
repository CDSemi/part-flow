import './production-board.css';

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
import { formatIsoDateShort } from '../dates';
import type { MockBoardRow, MockLocationRow } from '../view-models';
import {
  FALLBACK_PAGE_SIZE,
  fallbackPageBreaks,
  pageBreaksByHeight,
  sortBoardRows,
} from './board-logic';

const ROTATE_MS = 12_000;

/**
 * Subtle next-rotation indicator: a thin progress track plus a small
 * seconds-remaining label, rendered only while more than one page
 * exists. It reads the SAME deadline that drives the actual page
 * rotation (one timing source — never a second unsynchronized timer)
 * and owns its own tick, so the complete board never rerenders per
 * animation frame. Under `prefers-reduced-motion` the track is hidden
 * by production-board.css while the seconds label remains.
 */
function RotationProgress({ deadline }: { deadline: number }) {
  const [remaining, setRemaining] = useState(() =>
    Math.max(0, deadline - Date.now()),
  );
  useEffect(() => {
    const tick = () => setRemaining(Math.max(0, deadline - Date.now()));
    tick();
    const timer = window.setInterval(tick, 250);
    return () => window.clearInterval(timer);
  }, [deadline]);
  const pct = Math.max(0, Math.min(100, (remaining / ROTATE_MS) * 100));
  return (
    <span
      className="pb-rotate"
      title="Time until the next automatic page rotation"
    >
      <span className="pb-rotatetrack" aria-hidden="true">
        <i style={{ width: `${pct}%` }} />
      </span>
      <span className="pb-rotatesec">{Math.ceil(remaining / 1000)} s</span>
    </span>
  );
}

/**
 * Board operational status — the same meaning in the standard and
 * kiosk presentations: `Live` with the shared connected heartbeat
 * (styles/global.css — identical to the ONLINE connectivity dot) while
 * the shared connectivity state is healthy, an explicit non-pulsing
 * warning while it is not. The label is `Live` (board feed running),
 * never a second `ONLINE` chip — one status per header.
 */
function BoardLiveStatus() {
  const { status } = useConnectivity();
  return (
    <span className={`live${status === 'connected' ? '' : ' stale'}`}>
      <span className="ld" aria-hidden="true" />
      {status === 'connected' ? 'Live' : 'Feed stale — reconnecting'}
    </span>
  );
}

/** Self-contained live clock: only this component rerenders per tick. */
function LiveClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  // One coherent clock block: the time leads, the date sits directly
  // beneath it as the secondary line (production-board.css).
  return (
    <span className="clockwrap">
      <span className="clock">
        {now.toLocaleTimeString(undefined, { hour12: false })}
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
 * it). Each state also carries its semantic text tone (production-
 * board.css), consistent with the Scan Station statistics: queue →
 * warning, on machine / processing → information, done → success.
 * Color is never the only distinction — the state text remains.
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
    <div className="locrow">
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
      <span className={`ltime ${loc.timeLong ? 'long' : ''}`}>{loc.time}</span>
    </div>
  );
}

function BoardRowCells({ row, no }: { row: MockBoardRow; no: number }) {
  const urgent = row.dueClass === 'soon' || row.dueClass === 'late';
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
        <div className={`d2 ${row.dueClass} ${urgent ? 'blink' : ''}`}>
          {row.dueNote}
        </div>
      </td>
      <td className="cell-days">
        <div className="dtotal">{row.totalDays}</div>
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

function BoardHeadRow() {
  return (
    <tr>
      <th>No.</th>
      <th className="pn">Part Number</th>
      <th>Areas &amp; Quantities · Time</th>
      <th>Due Date</th>
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

  const recalc = useCallback(() => {
    const section = sectionRef.current;
    const body = measureBodyRef.current;
    const table = measureTableRef.current;
    if (!section || !body || !table) return;
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
    const available =
      window.innerHeight -
      section.getBoundingClientRect().top -
      headHeight -
      theadHeight -
      footHeight;
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
  }, []);

  // Recalculate after every commit that can change row heights (data,
  // theme class, font metrics) — the measurement table re-renders in
  // the same commit, so useLayoutEffect reads fresh heights.
  useLayoutEffect(() => {
    recalc();
  }, [recalc, allRows]);

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
  // One timing source for rotation AND its indicator: every displayed
  // page arms one deadline (now + ROTATE_MS); the timeout that fires
  // at that deadline advances the page, and RotationProgress renders
  // the countdown to the same deadline. Manual navigation (buttons,
  // dots, arrow keys) changes the page / bumps the epoch, which
  // re-arms the deadline — indicator and rotation can never drift
  // apart. Rotation exists only while more than one page exists;
  // automatic rotation wraps around, manual navigation never does.
  const [rotateDeadline, setRotateDeadline] = useState<number | null>(null);
  useEffect(() => {
    if (pageCount <= 1) {
      setRotateDeadline(null);
      return;
    }
    setRotateDeadline(Date.now() + ROTATE_MS);
    const timer = window.setTimeout(
      () => setPage((current) => (current + 1) % pageCount),
      ROTATE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [pageCount, rotateEpoch, safePage]);

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
    >
      {kiosk ? (
        // Kiosk header: one coherent board-owned row — compact brand
        // mark, board title, and the SAME `Live` operational status as
        // the standard header (BoardLiveStatus, shared heartbeat and
        // shared connectivity state — never a second `ONLINE` chip
        // repeating the same connectivity), plus what the hidden
        // navigation would otherwise provide: the shared borderless
        // Dark/Light control and an explicit exit action beside the
        // clock. Two presentations of one board, not two boards.
        <div className="pb-head pbk-head" ref={headRef}>
          <span className="pbk-brand" aria-hidden="true">
            <span className="mark">⇄</span>
            Part<span className="pf">Flow</span>
          </span>
          <h1>Machine Shop — Production</h1>
          <BoardLiveStatus />
          <span className="spacer" />
          <div className="pbk-actions">
            <ThemeToggle compact />
            <button
              className="pbk-exit"
              aria-label="Exit kiosk mode"
              title="Exit kiosk mode (Ctrl+Shift+K)"
              onClick={() => navigate('/production-board')}
            >
              Exit kiosk
            </button>
          </div>
          <LiveClock />
        </div>
      ) : (
        <div className="pb-head" ref={headRef}>
          <h1>Machine Shop — Production</h1>
          <BoardLiveStatus />
          <span className="spacer" />
          <LiveClock />
        </div>
      )}
      {rows.length === 0 ? (
        <EmptyState message="No active production in this Department." />
      ) : (
        <table className="pb-table">
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
          rows: pagination controls + aggregate totals, then the
          legends including the user-facing sorting rule. */}
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
            <RotationProgress deadline={rotateDeadline} />
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
        </div>
        <div className="pb-footrow legend">
          <span className="leg">
            🔥 in the No. column = Hot priority (highest first)
          </span>
          <span className="leg">
            blinking days count = due soon / overdue (the date and PN stay
            steady)
          </span>
          <span className="leg">— = no due date / no external WO Number</span>
          <span className="leg sort">
            Order: Hot rank first → earliest due date → no due date by oldest
            received date.
          </span>
          {kiosk ? (
            <span className="leg">Ctrl+Shift+K: exit kiosk mode</span>
          ) : null}
        </div>
      </div>
    </section>
  );
}
