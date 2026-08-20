import './work-orders.css';

import { useId, useMemo, useState } from 'react';

import { Link } from '../../app/link';
import { getViewStatePreview } from '../../app/view-state';
import { DevNotice } from '../../components/DevNotice';
import { PageNote } from '../../components/PageNote';
import { useUiClock } from '../../components/ui-clock';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { MOCK_COMPLETED_WORK_ORDERS } from '../../mocks/work-orders';
import { TypeChip } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { daysBetweenIso, formatIsoDate, todayIso } from '../dates';
import type { MockWorkOrder } from '../view-models';

// Completed Work Orders — the read-only history page on
// `/management/work-orders/completed` (GUI_DESIGN §11.5). Completed
// history is retained permanently and therefore unbounded: search,
// the Done-range default, filtering, ordering and paging model the
// server-side contract. In Phase 2 they run against the generated
// mock history in this browser session only.

const woDisplay = (workOrderNumber: string | null) => workOrderNumber ?? '—';

/** Rows loaded per page — `Show more` appends the next slice (the
 * keyset-continuation stand-in; never offset paging in production). */
const PAGE_SIZE = 50;

type RangeKey = '30d' | '90d' | 'year' | 'lastyear' | 'all' | 'custom';
type OutcomeKey = 'all' | 'ontime' | 'late' | 'nodue';
type SortKey = 'wo' | 'done' | 'received' | 'due';
type SortDir = 'asc' | 'desc';

/** The page's default order: most recently completed first. */
const DEFAULT_SORT = { key: 'done', dir: 'desc' } as const;

/** ISO `YYYY-MM-DD` local date `days` before now. Calendar-day
 * arithmetic (not ms) so a DST boundary never shifts the date. */
function isoDaysAgo(nowMs: number, days: number): string {
  const date = new Date(nowMs);
  date.setDate(date.getDate() - days);
  return todayIso(date.getTime());
}

/** Inclusive done-date bounds of the selected range (null = open). */
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

/** Derived due outcome of a completed Work Order — done vs due. */
function dueOutcome(w: MockWorkOrder): OutcomeKey {
  if (!w.due) return 'nodue';
  if (w.done && w.done > w.due) return 'late';
  return 'ontime';
}

/**
 * Stable one-column sort. Equal primary values keep the done-date
 * order (newest first), then the input order — the same rows never
 * swap between renders.
 */
function sortRows(
  rows: MockWorkOrder[],
  key: SortKey,
  dir: SortDir,
): MockWorkOrder[] {
  const value = (w: MockWorkOrder): string => {
    switch (key) {
      case 'wo':
        return w.workOrderNumber ?? '';
      case 'done':
        return w.done ?? '';
      case 'received':
        return w.received;
      case 'due':
        return w.due ?? '';
    }
  };
  return rows
    .map((w, index) => ({ w, index }))
    .sort((a, b) => {
      const va = value(a.w);
      const vb = value(b.w);
      const primary = va < vb ? -1 : va > vb ? 1 : 0;
      const da = a.w.done ?? '';
      const db = b.w.done ?? '';
      const doneDesc = da < db ? 1 : da > db ? -1 : 0;
      return (
        (dir === 'asc' ? primary : -primary) || doneDesc || a.index - b.index
      );
    })
    .map((entry) => entry.w);
}

/** One sortable column header — the shared §12.1 presentation. */
function SortHeader({
  label,
  active,
  dir,
  onToggle,
}: {
  label: string;
  active: boolean;
  dir: SortDir;
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
        {active ? (dir === 'asc' ? '↑' : '↓') : '↕'}
      </span>
    </button>
  );
}

export function CompletedWorkOrdersView() {
  const preview = getViewStatePreview();
  const now = useUiClock('minute');

  const [search, setSearch] = useState('');
  const [range, setRange] = useState<RangeKey>('90d');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const [outcome, setOutcome] = useState<OutcomeKey>('all');
  // null = the default Done ↓ order; a header cycles asc → desc →
  // back to the default (an unbounded history has no registry order).
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir } | null>(null);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [detail, setDetail] = useState<MockWorkOrder | null>(null);

  const effectiveSort = sort ?? DEFAULT_SORT;
  const resetPaging = () => setLimit(PAGE_SIZE);

  const toggleSort = (key: SortKey) => {
    setSort((current) => {
      // From the default Done ↓ order every column starts ascending.
      if (current === null) return { key, dir: 'asc' };
      if (current.key !== key) return { key, dir: 'asc' };
      if (current.dir === 'asc') return { key, dir: 'desc' };
      return null;
    });
    resetPaging();
  };

  const all = useMemo(
    () => (preview === 'empty' ? [] : MOCK_COMPLETED_WORK_ORDERS),
    [preview],
  );
  const bounds = rangeBounds(range, now, customFrom, customTo);
  const query = search.trim().toLowerCase();

  const filtered = useMemo(() => {
    const matches = all.filter((w) => {
      const done = w.done ?? '';
      if (bounds.from && done < bounds.from) return false;
      if (bounds.to && done > bounds.to) return false;
      if (outcome !== 'all' && dueOutcome(w) !== outcome) return false;
      if (query) {
        const haystack = [
          w.workOrderNumber ?? '',
          w.preview,
          ...w.lines.flatMap((l) => [l.pn, l.job]),
        ]
          .join(' ')
          .toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    });
    return sortRows(matches, effectiveSort.key, effectiveSort.dir);
  }, [all, bounds.from, bounds.to, outcome, query, effectiveSort]);

  const visible = filtered.slice(0, limit);

  if (preview === 'loading') {
    return (
      <section className="wo-view" aria-label="Completed Work Orders">
        <LoadingState label="Loading Completed Work Orders" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="wo-view" aria-label="Completed Work Orders">
        <ErrorState
          message="Completed Work Orders could not be loaded."
          detail="Check the backend connection and try again."
        />
      </section>
    );
  }

  const sortableColumns: { key: SortKey; label: string }[] = [
    { key: 'wo', label: 'WO Number' },
    { key: 'done', label: 'Done' },
    { key: 'received', label: 'Received' },
    { key: 'due', label: 'Due' },
  ];

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
        been fully allocated, and completed Work Orders are kept{' '}
        <b>permanently</b>. Select a Work Order to open its details.
      </p>
      <DevNotice>
        Development preview — the completed history is generated sample data in
        this browser session only.
      </DevNotice>
      <div className="wo-tools cwo-tools">
        <input
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            resetPaging();
          }}
          placeholder="Search WO Number · PN · Job Number…"
          aria-label="Search completed Work Orders"
        />
        <label className="cwo-filter">
          <span>Done</span>
          <select
            value={range}
            aria-label="Done date range"
            onChange={(e) => {
              setRange(e.target.value as RangeKey);
              resetPaging();
            }}
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
              onChange={(e) => {
                setCustomFrom(e.target.value);
                resetPaging();
              }}
            />
            <span className="cwo-dash" aria-hidden="true">
              –
            </span>
            <input
              type="date"
              className="mono cwo-date"
              aria-label="Done to"
              value={customTo}
              onChange={(e) => {
                setCustomTo(e.target.value);
                resetPaging();
              }}
            />
          </>
        ) : null}
        <label className="cwo-filter">
          <span>Due outcome</span>
          <select
            value={outcome}
            aria-label="Due outcome"
            onChange={(e) => {
              setOutcome(e.target.value as OutcomeKey);
              resetPaging();
            }}
          >
            <option value="all">All</option>
            <option value="ontime">On time</option>
            <option value="late">Late</option>
            <option value="nodue">No due date</option>
          </select>
        </label>
      </div>
      {all.length === 0 ? (
        <EmptyState message="No completed Work Orders yet — a Work Order completes when every demand line has been fully allocated." />
      ) : (
        <>
          <p className="cwo-summary">
            Showing <b>{visible.length}</b> of <b>{filtered.length}</b>{' '}
            completed Work Order{filtered.length === 1 ? '' : 's'} ·{' '}
            {rangeSummary(range, bounds.from, bounds.to)}
          </p>
          <table className="wolist cwo-list">
            <thead>
              <tr>
                {sortableColumns.map((column) => (
                  <th
                    key={column.key}
                    aria-sort={
                      effectiveSort.key === column.key
                        ? effectiveSort.dir === 'asc'
                          ? 'ascending'
                          : 'descending'
                        : undefined
                    }
                  >
                    <SortHeader
                      label={column.label}
                      active={effectiveSort.key === column.key}
                      dir={effectiveSort.dir}
                      onToggle={() => toggleSort(column.key)}
                    />
                  </th>
                ))}
                <th>Demand lines</th>
              </tr>
            </thead>
            <tbody>
              {visible.length === 0 ? (
                <tr>
                  <td colSpan={5} className="empty">
                    No completed Work Orders match in this range — widen the
                    Done range or clear filters.
                    {query && range !== 'all' ? (
                      <div className="cwo-allhistory">
                        <button
                          className="btn ghost"
                          onClick={() => {
                            setRange('all');
                            resetPaging();
                          }}
                        >
                          Search all history
                        </button>
                      </div>
                    ) : null}
                  </td>
                </tr>
              ) : (
                visible.map((w) => {
                  const outcomeKey = dueOutcome(w);
                  const daysLate =
                    outcomeKey === 'late' && w.due && w.done
                      ? daysBetweenIso(w.due, w.done)
                      : null;
                  return (
                    <tr
                      key={w.id}
                      className="selrow"
                      onClick={() => setDetail(w)}
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
                          {w.internal ? (
                            <span className="sub" style={{ display: 'block' }}>
                              internal Work Order — no external number yet
                            </span>
                          ) : null}
                        </button>
                      </td>
                      <td className="mono-sm cwo-done">
                        {formatIsoDate(w.done ?? null)}
                      </td>
                      <td className="mono-sm">{formatIsoDate(w.received)}</td>
                      <td className="mono-sm">
                        {formatIsoDate(w.due)}
                        {w.due ? (
                          <div className={`sub cwo-outcome ${outcomeKey}`}>
                            {outcomeKey === 'late'
                              ? `✕ ${daysLate} day${daysLate === 1 ? '' : 's'} late`
                              : '✓ On time'}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        {w.lines.length}
                        <div className="sub mono-sm">{w.preview}</div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
          {visible.length < filtered.length ? (
            <div className="cwo-more">
              <button
                className="btn ghost"
                onClick={() => setLimit((current) => current + PAGE_SIZE)}
              >
                Show more
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
      {detail !== null && (
        <CompletedDetailDialog
          key={detail.id}
          workOrder={detail}
          onClose={() => setDetail(null)}
        />
      )}
    </section>
  );
}

/**
 * Read-only Work Order Details for the completed-history preview
 * (§11.5 row activation): the same dialog title and meta-line shape as
 * the real Work Order Details, with `Done <date>` added — but purely
 * presentational over the mock history (the real details dialog reads
 * live server state, and no Work Order can complete before the
 * allocation workflow exists).
 */
function CompletedDetailDialog({
  workOrder,
  onClose,
}: {
  workOrder: MockWorkOrder;
  onClose: () => void;
}) {
  const headingId = useId();
  return (
    <ModalDialog labelledBy={headingId} onClose={onClose} size="xwide">
      <div className="wo-head">
        <h2 id={headingId} className="nwo-title">
          Work Order Details
        </h2>
      </div>
      <div className="big mono">{woDisplay(workOrder.workOrderNumber)}</div>
      <p className="wo-sub">
        received <b className="mono">{formatIsoDate(workOrder.received)}</b> ·
        WO due date <b className="mono">{formatIsoDate(workOrder.due)}</b> ·{' '}
        {workOrder.lines.length} demand line
        {workOrder.lines.length === 1 ? '' : 's'} ·{' '}
        <span className={`wostat ${workOrder.status.toLowerCase()}`}>
          {workOrder.status}
        </span>
        {workOrder.done ? (
          // Done date (`completed_at`, GUI_DESIGN §11.5) — present
          // exactly on completed Work Orders.
          <>
            {' '}
            · Done <b className="mono">{formatIsoDate(workOrder.done)}</b>
          </>
        ) : null}
        {workOrder.internal
          ? ' · internal Work Order — no external number yet (displays —)'
          : ''}
      </p>
      <div className="wo-lines">
        <table className="wo-table">
          <thead>
            <tr>
              <th>PN</th>
              <th>Request Type</th>
              <th>Qty</th>
              <th>Due date</th>
              <th>Job Numbers</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {workOrder.lines.map((line) => (
              <tr key={line.pn}>
                <td data-label="PN">
                  <div className="pn" title={line.pn}>
                    {line.pn}
                  </div>
                  <div className="bc">{line.barcode}</div>
                </td>
                <td data-label="Request Type">
                  <TypeChip type={line.type} />
                </td>
                <td data-label="Qty">
                  <span className="mono">{line.qty}</span>
                </td>
                <td data-label="Due date">
                  <span className="mono">{formatIsoDate(line.due)}</span>
                </td>
                <td data-label="Job Numbers">
                  <span className="mono">{line.job || '—'}</span>
                </td>
                <td data-label="Status">
                  <span className={`linestat ${line.statusClass}`}>
                    {line.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="wo-actions nwo-actions">
        <button className="btn ghost" onClick={onClose}>
          Cancel (Esc)
        </button>
        <span className="hint">
          This Work Order is <b>{workOrder.status}</b> — demand lines are
          read-only. Editing is available only while a Work Order is Open.
        </span>
      </div>
    </ModalDialog>
  );
}
