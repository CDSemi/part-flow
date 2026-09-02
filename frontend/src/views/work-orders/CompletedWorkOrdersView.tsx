import './work-orders.css';

import { useCallback, useEffect, useRef, useState } from 'react';

import { listCompletedWorkOrders } from '../../api/work-orders';
import type {
  CompletedCursor,
  DueOutcome,
  WorkOrderSummary,
} from '../../api/work-orders';
import { errorMessage } from '../../api/client';
import { useConnectivity } from '../../app/connectivity-context';
import { Link } from '../../app/link';
import { getViewStatePreview } from '../../app/view-state';
import { PageNote } from '../../components/PageNote';
import { useToastNotice } from '../../components/toast-notice';
import { useUiClock } from '../../components/ui-clock';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { daysBetweenIso, formatIsoDate, todayIso } from '../dates';
import { partNumbersPreview } from './demand-lines';
import { WorkOrderDetailPanel } from './WorkOrderDetailPanel';

// Completed Work Orders — the read-only history page on
// `/management/work-orders/completed` (GUI_DESIGN §11.5), a REAL view
// on `GET /api/work-orders/completed` (Phase 10). A Work Order
// completes when the SERVER derives that every demand line is fully
// allocated from stocked quantity; it then leaves the active list and
// appears here permanently. The history is unbounded by design, so the
// search (WO Number, PN, Job Number), the Done-date range, the due
// outcome filter, the newest-first order and the keyset paging all run
// on the server — the page never downloads the history to filter it.

const woDisplay = (workOrderNumber: string | null) => workOrderNumber ?? '—';

/** Wait out the typing burst before asking the server. */
const SEARCH_DEBOUNCE_MS = 250;

type RangeKey = '30d' | '90d' | 'year' | 'lastyear' | 'all' | 'custom';
type OutcomeKey = 'all' | 'ontime' | 'late' | 'nodue';

const DUE_OUTCOME: Record<OutcomeKey, DueOutcome> = {
  all: 'ALL',
  ontime: 'ON_TIME',
  late: 'LATE',
  nodue: 'NO_DUE_DATE',
};

/** ISO `YYYY-MM-DD` local date `days` before now. Calendar-day
 * arithmetic (not ms) so a DST boundary never shifts the date. */
function isoDaysAgo(nowMs: number, days: number): string {
  const date = new Date(nowMs);
  date.setDate(date.getDate() - days);
  return todayIso(date.getTime());
}

/** Inclusive done-DATE bounds of the selected range (null = open). */
function rangeBounds(
  range: RangeKey,
  nowMs: number,
  customFrom: string,
  customTo: string,
): { from: string | null; to: string | null } {
  const year = todayIso(nowMs).slice(0, 4);
  switch (range) {
    case '30d':
      return { from: isoDaysAgo(nowMs, 30), to: null };
    case '90d':
      return { from: isoDaysAgo(nowMs, 90), to: null };
    case 'year':
      return { from: `${year}-01-01`, to: null };
    case 'lastyear': {
      const last = String(Number(year) - 1);
      return { from: `${last}-01-01`, to: `${last}-12-31` };
    }
    case 'all':
      return { from: null, to: null };
    case 'custom':
      return { from: customFrom || null, to: customTo || null };
  }
}

/** Range restatement for the result summary line. */
function rangeSummary(
  range: RangeKey,
  from: string | null,
  to: string | null,
): string {
  switch (range) {
    case '30d':
      return 'last 30 days';
    case '90d':
      return 'last 90 days';
    case 'year':
      return 'this year';
    case 'lastyear':
      return 'last year';
    case 'all':
      return 'all time';
    case 'custom':
      if (from && to) return `${formatIsoDate(from)} – ${formatIsoDate(to)}`;
      if (from) return `from ${formatIsoDate(from)}`;
      if (to) return `through ${formatIsoDate(to)}`;
      return 'all time';
  }
}

/** Local midnight of an ISO date as the server's timestamp bound. */
function startOfLocalDay(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00`).toISOString();
}

/** The exclusive upper bound of an inclusive ISO date: the next midnight. */
function endOfLocalDay(isoDate: string): string {
  const date = new Date(`${isoDate}T00:00:00`);
  date.setDate(date.getDate() + 1);
  return date.toISOString();
}

/** The done DATE (local) of a completed Work Order. */
function doneDate(w: WorkOrderSummary): string | null {
  return w.completedAt ? todayIso(new Date(w.completedAt).getTime()) : null;
}

/** Derived due outcome of a completed Work Order — done vs due. */
function dueOutcome(w: WorkOrderSummary): OutcomeKey {
  if (!w.dueDate) return 'nodue';
  const done = doneDate(w);
  if (done && done > w.dueDate) return 'late';
  return 'ontime';
}

interface LoadedPage {
  rows: WorkOrderSummary[];
  total: number;
  historyTotal: number;
  nextCursor: CompletedCursor | null;
  /** The query (filters + reload generation) these rows belong to — a
   * keyset continuation is applied only while it is still the loaded
   * one, so a page requested for earlier filters never lands in the
   * page of the current ones. */
  query: number;
}

export function CompletedWorkOrdersView() {
  const preview = getViewStatePreview();
  const now = useUiClock('minute');
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const { showNotice, noticeElement } = useToastNotice();

  const [search, setSearch] = useState('');
  const [settledSearch, setSettledSearch] = useState('');
  useEffect(() => {
    const timer = setTimeout(
      () => setSettledSearch(search),
      SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [search]);
  const [range, setRange] = useState<RangeKey>('90d');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const [outcome, setOutcome] = useState<OutcomeKey>('all');
  const [detailId, setDetailId] = useState<number | null>(null);

  const bounds = rangeBounds(range, now, customFrom, customTo);
  const doneFrom = bounds.from ? startOfLocalDay(bounds.from) : null;
  const doneTo = bounds.to ? endOfLocalDay(bounds.to) : null;

  // The loaded page(s) for the CURRENT filters: the first page replaces,
  // `Show more` appends the next keyset page. A filter change starts
  // over — never an offset over an unbounded history.
  const [page, setPage] = useState<LoadedPage | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [generation, setGeneration] = useState(0);
  // Increments on every first-page load — the identity of the query
  // whose page is on screen.
  const queryRef = useRef(0);
  const reload = useCallback(() => {
    setLoadError(null);
    setGeneration((value) => value + 1);
  }, []);

  useEffect(() => {
    if (preview !== null) return;
    let cancelled = false;
    queryRef.current += 1;
    const query = queryRef.current;
    setPage(null);
    setLoadError(null);
    setLoadingMore(false);
    listCompletedWorkOrders({
      search: settledSearch,
      doneFrom,
      doneTo,
      dueOutcome: DUE_OUTCOME[outcome],
    }).then(
      (fresh) => {
        if (cancelled) return;
        setPage({
          rows: fresh.workOrders,
          total: fresh.total,
          historyTotal: fresh.historyTotal,
          nextCursor: fresh.nextCursor,
          query,
        });
      },
      (error: unknown) => {
        if (!cancelled) setLoadError(errorMessage(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [preview, settledSearch, doneFrom, doneTo, outcome, generation]);

  const showMore = async () => {
    if (page === null || page.nextCursor === null || loadingMore) return;
    const { query } = page;
    setLoadingMore(true);
    try {
      const next = await listCompletedWorkOrders({
        search: settledSearch,
        doneFrom,
        doneTo,
        dueOutcome: DUE_OUTCOME[outcome],
        cursor: page.nextCursor,
      });
      // Stale continuation (the filters changed meanwhile): ignored — the
      // rows, total and cursor belong to a query no longer on screen.
      setPage((current) =>
        current === null || current.query !== query
          ? current
          : {
              rows: [...current.rows, ...next.workOrders],
              total: next.total,
              historyTotal: next.historyTotal,
              nextCursor: next.nextCursor,
              query,
            },
      );
    } catch (error) {
      if (queryRef.current === query) {
        showNotice(
          `⚠ More completed Work Orders could not be loaded: ${errorMessage(error)}`,
        );
      }
    } finally {
      if (queryRef.current === query) setLoadingMore(false);
    }
  };

  if (preview === 'loading') {
    return (
      <section className="wo-view" aria-label="Completed Work Orders">
        <LoadingState label="Loading Completed Work Orders" />
      </section>
    );
  }
  if (preview === 'error' || loadError !== null) {
    return (
      <section className="wo-view" aria-label="Completed Work Orders">
        <ErrorState
          message="Completed Work Orders could not be loaded."
          detail={loadError ?? 'Check the backend connection and try again.'}
          onRetry={loadError !== null ? reload : undefined}
        />
      </section>
    );
  }

  const query = search.trim();
  // The page for exactly the filters on screen: null while the debounce
  // or the request is still pending for them.
  const current = search === settledSearch ? page : null;
  const rows = preview === 'empty' ? [] : (current?.rows ?? null);
  const total = preview === 'empty' ? 0 : (current?.total ?? 0);
  // "None ever" is the SERVER's whole-history count, not the absence of
  // filters: the default 90-day window is always a filter (GUI_DESIGN
  // §11.5 — a plain empty state only when nothing ever completed).
  const noneEver =
    rows !== null && rows.length === 0 && (current?.historyTotal ?? 0) === 0;

  return (
    <section className="wo-view" aria-label="Completed Work Orders">
      <Link to="/management/work-orders" className="cwo-back">
        ‹ Work Orders
      </Link>
      <div className="wo-head">
        <h1>Completed Work Orders</h1>
      </div>
      <p className="wo-sub">
        Read-only history — a Work Order completes when every demand line has
        been fully allocated from stocked quantity, and completed Work Orders
        are kept <b>permanently</b>. Select a Work Order to open its details.
      </p>
      <div className="wo-tools cwo-tools">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search WO Number · PN · Job Number…"
          aria-label="Search completed Work Orders"
        />
        <label className="cwo-filter">
          <span>Done</span>
          <select
            value={range}
            aria-label="Done date range"
            onChange={(e) => setRange(e.target.value as RangeKey)}
          >
            <option value="30d">Last 30 days</option>
            <option value="90d">Last 90 days</option>
            <option value="year">This year</option>
            <option value="lastyear">Last year</option>
            <option value="all">All time</option>
            <option value="custom">Custom…</option>
          </select>
        </label>
        {range === 'custom' ? (
          <>
            <input
              type="date"
              className="mono cwo-date"
              aria-label="Done from"
              value={customFrom}
              onChange={(e) => setCustomFrom(e.target.value)}
            />
            <span className="cwo-dash" aria-hidden="true">
              –
            </span>
            <input
              type="date"
              className="mono cwo-date"
              aria-label="Done to"
              value={customTo}
              onChange={(e) => setCustomTo(e.target.value)}
            />
          </>
        ) : null}
        <label className="cwo-filter">
          <span>Due outcome</span>
          <select
            value={outcome}
            aria-label="Due outcome"
            onChange={(e) => setOutcome(e.target.value as OutcomeKey)}
          >
            <option value="all">All</option>
            <option value="ontime">On time</option>
            <option value="late">Late</option>
            <option value="nodue">No due date</option>
          </select>
        </label>
      </div>
      {rows === null ? (
        <LoadingState label="Loading Completed Work Orders" />
      ) : noneEver ? (
        <EmptyState message="No completed Work Orders yet — a Work Order completes when every demand line has been fully allocated." />
      ) : (
        <>
          <p className="cwo-summary">
            Showing <b>{rows.length.toLocaleString()}</b> of{' '}
            <b>{total.toLocaleString()}</b> completed Work Order
            {total === 1 ? '' : 's'} ·{' '}
            {rangeSummary(range, bounds.from, bounds.to)}
          </p>
          <table className="wolist cwo-list">
            <thead>
              <tr>
                <th>WO Number</th>
                <th>Done ↓</th>
                <th>Received</th>
                <th>Due</th>
                <th>Demand lines</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={5} className="empty">
                    No completed Work Orders match in this range — widen the
                    Done range or clear filters.
                    {query && range !== 'all' ? (
                      <div className="cwo-allhistory">
                        <button
                          className="btn ghost"
                          onClick={() => setRange('all')}
                        >
                          Search all history
                        </button>
                      </div>
                    ) : null}
                  </td>
                </tr>
              ) : (
                rows.map((w) => {
                  const outcomeKey = dueOutcome(w);
                  const done = doneDate(w);
                  const daysLate =
                    outcomeKey === 'late' && w.dueDate && done
                      ? daysBetweenIso(w.dueDate, done)
                      : null;
                  return (
                    <tr
                      key={w.id}
                      className="selrow"
                      onClick={() => setDetailId(w.id)}
                    >
                      <td>
                        <button
                          className="rowbtn"
                          aria-label={`Open Work Order ${woDisplay(w.workOrderNumber)}`}
                        >
                          <span
                            className="wo"
                            title={woDisplay(w.workOrderNumber)}
                          >
                            {woDisplay(w.workOrderNumber)}
                          </span>
                          {w.workOrderNumber === null ? (
                            <span className="sub" style={{ display: 'block' }}>
                              internal Work Order — no external number yet
                            </span>
                          ) : null}
                        </button>
                      </td>
                      <td className="mono-sm cwo-done" data-label="Done">
                        {formatIsoDate(done)}
                      </td>
                      <td className="mono-sm" data-label="Received">
                        {formatIsoDate(w.receivedDate)}
                      </td>
                      <td className="mono-sm" data-label="Due">
                        {formatIsoDate(w.dueDate)}
                        {w.dueDate ? (
                          <div className={`sub cwo-outcome ${outcomeKey}`}>
                            {outcomeKey === 'late'
                              ? `✕ ${daysLate} day${daysLate === 1 ? '' : 's'} late`
                              : '✓ On time'}
                          </div>
                        ) : null}
                      </td>
                      <td data-label="Demand lines">
                        {w.demandLineCount}
                        <div className="sub mono-sm">
                          {partNumbersPreview(w.partNumbers)}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
          {current?.nextCursor ? (
            <div className="cwo-more">
              <button
                className="btn ghost"
                disabled={loadingMore}
                onClick={() => void showMore()}
              >
                {loadingMore ? 'Loading…' : 'Show more'}
              </button>
            </div>
          ) : null}
        </>
      )}
      <PageNote>
        Completed Work Orders stay here permanently. A later audited allocation
        adjustment can reopen a Work Order — it then returns to the active Work
        Orders list; nothing is ever deleted.
      </PageNote>
      {detailId !== null && (
        <WorkOrderDetailPanel
          key={detailId}
          workOrderId={detailId}
          writeBlocked={writeBlocked}
          onClose={() => setDetailId(null)}
          onChanged={reload}
          onDirtyChange={() => undefined}
          showNotice={showNotice}
        />
      )}
      {noticeElement}
    </section>
  );
}
