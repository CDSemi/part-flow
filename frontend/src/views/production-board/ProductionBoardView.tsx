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
import { getViewStatePreview } from '../../app/view-state';
import { AreaDot, HotPn } from '../../components/indicators';
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
import type { MockBoardRow } from '../view-models';
import {
  FALLBACK_PAGE_SIZE,
  fallbackPageBreaks,
  pageBreaksByHeight,
  sortBoardRows,
} from './board-logic';

const ROTATE_MS = 12_000;

/** Self-contained live clock: only this component rerenders per tick. */
function LiveClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <span className="clockwrap">
      <span className="clockdate">
        {now.toLocaleDateString(undefined, {
          weekday: 'short',
          month: 'short',
          day: 'numeric',
          year: 'numeric',
        })}
      </span>
      <span className="clock">
        {now.toLocaleTimeString(undefined, { hour12: false })}
      </span>
    </span>
  );
}

function BoardRowCells({ row, no }: { row: MockBoardRow; no: number }) {
  const urgent = row.dueClass === 'soon' || row.dueClass === 'late';
  return (
    <>
      <td className="cell-no">
        <div className="no">{no}</div>
      </td>
      <td>
        <div className="part">
          <HotPn rank={row.hotRank} pn={row.pn} />
        </div>
        <div className="pname">{row.name}</div>
      </td>
      <td className="areas">
        <div className="loc">
          {row.locations.map((loc) => (
            <div className="locrow" key={`${loc.label}-${loc.tag ?? ''}`}>
              <span className="lname">
                <AreaDot
                  colorVar={areaByKey(loc.area)?.colorVar ?? 'var(--faint)'}
                  size={11}
                />
                {loc.label}
              </span>
              <span className="lqty">{loc.qty}</span>
              <span className="ltag">{loc.tag ?? ''}</span>
              <span className={`ltime ${loc.timeLong ? 'long' : ''}`}>
                {loc.time}
              </span>
            </div>
          ))}
          <div className="locrow total">
            <span className="lname">total</span>
            <span className="lqty tot">{row.total}</span>
            <span className="ltag">
              pcs{row.totalStocked ? ' stocked' : ''}
            </span>
            <span className="ltime" />
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
      <th>Part Number</th>
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
  const { status } = useConnectivity();

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
  // Clamp the active page whenever the page structure changes.
  useEffect(() => {
    setPage((current) => Math.min(current, pageCount - 1));
  }, [pageCount]);
  // Rotate only while more than one page exists; the previous timer is
  // cleaned up when pagination changes or the view unmounts.
  useEffect(() => {
    if (pageCount <= 1) return;
    const timer = window.setInterval(
      () => setPage((current) => (current + 1) % pageCount),
      ROTATE_MS,
    );
    return () => window.clearInterval(timer);
  }, [pageCount]);
  const safePage = Math.min(page, pageCount - 1);

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

  return (
    <section className="pb" aria-label="Production Board" ref={sectionRef}>
      <div className="pb-head" ref={headRef}>
        <h1>Machine Shop — Production</h1>
        <span className="live">
          <span className="ld" aria-hidden="true" />
          {status === 'connected' ? 'Live' : 'Feed stale — reconnecting'}
        </span>
        <span className="spacer" />
        <LiveClock />
      </div>
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
      <div className="pb-foot" ref={footRef}>
        <span>
          {pageCount > 1
            ? `Page ${safePage + 1} / ${pageCount} · rotates every ${
                ROTATE_MS / 1000
              } s`
            : 'Page 1 / 1'}
        </span>
        {Array.from({ length: pageCount }, (_, i) => (
          <span
            key={i}
            className={`pgdot ${i === safePage ? 'on' : ''}`}
            aria-hidden="true"
          />
        ))}
        <span className="spacer" />
        <span>
          🔥#n before the PN = Hot priority rank · blinking days count = due
          soon / overdue (the date and PN stay steady) · — = no due date ·{' '}
          {activePns} active PNs · {inProduction} pcs in production · {stocked}{' '}
          pcs stocked
        </span>
      </div>
    </section>
  );
}
