import './work-orders.css';

import { useCallback, useEffect, useRef, useState } from 'react';

import { listCompletedWorkOrders } from '../../api/work-orders';
import type {
  CompletedSort,
  DoneRangePreset,
  DueOutcome,
  SortDirection,
  WorkOrderSummary,
} from '../../api/work-orders';
import { errorMessage } from '../../api/client';
import { useConnectivity } from '../../app/connectivity-context';
import { Link } from '../../app/link';
import { getViewStatePreview } from '../../app/view-state';
import { PageNote } from '../../components/PageNote';
import { useToastNotice } from '../../components/toast-notice';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { formatIsoDate } from '../dates';
import { partNumbersPreview } from './demand-lines';
import { WorkOrderDetailPanel } from './WorkOrderDetailPanel';

// Completed Work Orders — the read-only history page on
// `/management/work-orders/completed` (GUI_DESIGN §11.5), a REAL view
// on `GET /api/work-orders/completed` (Phase 10). A Work Order
// completes when the SERVER derives that every demand line is fully
// allocated from stocked quantity; it then leaves the active list and
// appears here permanently. The history is unbounded by design, so the
// search (WO Number, PN, Job Number), the Done-date range, the due
// outcome filter, the column sort (Done descending by default) and the
// keyset paging all run on the server — the page never downloads the
// history to filter or sort it, and every date judgement (the done
// date, on time / late, the window a Done range preset stands for) is
// the SERVER's, on the site calendar — never this device's clock.

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

/** The Done range as the server takes it: a preset it anchors to the
 * site's current date (`SITE_TIMEZONE` is the server's — this device
 * holds no second calendar that could drift from it), or the explicit
 * inclusive done DATES of the Custom range; `all` sends no bound. */
function rangeQuery(
  range: RangeKey,
  customFrom: string,
  customTo: string,
): {
  preset: DoneRangePreset | null;
  from: string | null;
  to: string | null;
} {
  switch (range) {
    case '30d':
      return { preset: 'LAST_30_DAYS', from: null, to: null };
    case '90d':
      return { preset: 'LAST_90_DAYS', from: null, to: null };
    case 'year':
      return { preset: 'THIS_YEAR', from: null, to: null };
    case 'lastyear':
      return { preset: 'LAST_YEAR', from: null, to: null };
    case 'all':
      return { preset: null, from: null, to: null };
    case 'custom':
      return { preset: null, from: customFrom || null, to: customTo || null };
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

/** The one server-side sort of the page (GUI_DESIGN §11.5): the column
 * and its direction. `Done` descending is the default — and what the
 * unsorted state of every header cycle returns to. */
interface SortState {
  key: CompletedSort;
  dir: SortDirection;
}
const DEFAULT_SORT: SortState = { key: 'DONE', dir: 'DESC' };

/**
 * The next sort after activating `key` (the shared §12.1 cycle):
 * ascending → descending → the default order. The default column's
 * descending order IS the default, so on that header the cycle is
 * ascending ↔ descending — a click always changes the order, and both
 * directions of every column stay reachable.
 */
function nextSort(current: SortState, key: CompletedSort): SortState {
  if (current.key !== key) return { key, dir: 'ASC' };
  if (current.dir === 'ASC') return { key, dir: 'DESC' };
  return key === DEFAULT_SORT.key ? { key, dir: 'ASC' } : DEFAULT_SORT;
}

/**
 * One sortable column header (the shared §12.1 idiom): ascending →
 * descending → the default order (`nextSort`). The arrow names the
 * direction; the active sort renders emphasized. `aria-sort` lives on
 * the owning th.
 */
function SortHeader({
  label,
  active,
  dir,
  onToggle,
}: {
  label: string;
  active: boolean;
  dir: SortDirection;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className={`wo-sortbtn${active ? ' on' : ''}`}
      aria-label={`Sort by ${label}`}
      onClick={onToggle}
    >
      {label}
      <span className="arrow" aria-hidden="true">
        {active ? (dir === 'ASC' ? '↑' : '↓') : '↕'}
      </span>
    </button>
  );
}

const SORT_COLUMNS: readonly { key: CompletedSort; label: string }[] = [
  { key: 'NUMBER', label: 'WO Number' },
  { key: 'DONE', label: 'Done' },
  { key: 'RECEIVED', label: 'Received' },
  { key: 'DUE', label: 'Due' },
];

interface LoadedPage {
  rows: WorkOrderSummary[];
  total: number;
  historyTotal: number;
  nextCursor: string | null;
  /** The query (filters + reload generation) these rows belong to — a
   * keyset continuation is applied only while it is still the loaded
   * one, so a page requested for earlier filters never lands in the
   * page of the current ones. */
  query: number;
}

export function CompletedWorkOrdersView() {
  const preview = getViewStatePreview();
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
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const toggleSort = (key: CompletedSort) =>
    setSort((current) => nextSort(current, key));
  const [detailId, setDetailId] = useState<number | null>(null);

  // The Done range travels as the server takes it: a preset it resolves
  // on the site's calendar, or the Custom range's explicit done DATES.
  const bounds = rangeQuery(range, customFrom, customTo);
  const doneRange = bounds.preset;
  const doneFrom = bounds.from;
  const doneTo = bounds.to;

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
      doneRange,
      doneFrom,
      doneTo,
      dueOutcome: DUE_OUTCOME[outcome],
      sort: sort.key,
      direction: sort.dir,
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
  }, [
    preview,
    settledSearch,
    doneRange,
    doneFrom,
    doneTo,
    outcome,
    sort.key,
    sort.dir,
    generation,
  ]);

  const showMore = async () => {
    if (page === null || page.nextCursor === null || loadingMore) return;
    const { query } = page;
    setLoadingMore(true);
    try {
      const next = await listCompletedWorkOrders({
        search: settledSearch,
        doneRange,
        doneFrom,
        doneTo,
        dueOutcome: DUE_OUTCOME[outcome],
        sort: sort.key,
        direction: sort.dir,
        cursor: page.nextCursor,
      });
      // Stale continuation (the filters or the sort changed meanwhile):
      // ignored — the
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
                {SORT_COLUMNS.map((column) => (
                  <th
                    key={column.key}
                    aria-sort={
                      sort.key === column.key
                        ? sort.dir === 'ASC'
                          ? 'ascending'
                          : 'descending'
                        : undefined
                    }
                  >
                    <SortHeader
                      label={column.label}
                      active={sort.key === column.key}
                      dir={sort.key === column.key ? sort.dir : 'ASC'}
                      onToggle={() => toggleSort(column.key)}
                    />
                  </th>
                ))}
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
                  // The server's judgement on the site calendar — the
                  // same rule the due-outcome filter applied.
                  const outcomeKey: OutcomeKey =
                    w.dueOutcome === 'LATE'
                      ? 'late'
                      : w.dueOutcome === 'ON_TIME'
                        ? 'ontime'
                        : 'nodue';
                  const daysLate = w.daysLate;
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
                        {formatIsoDate(w.doneDate)}
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
