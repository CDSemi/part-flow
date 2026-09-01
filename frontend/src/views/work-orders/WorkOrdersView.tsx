import './work-orders.css';

import { useCallback, useEffect, useState } from 'react';

import { WORK_ORDER_LIST_LIMIT, listWorkOrders } from '../../api/work-orders';
import type { WorkOrderSummary } from '../../api/work-orders';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { Link } from '../../app/link';
import { useRouter } from '../../app/router-context';
import { getViewStatePreview } from '../../app/view-state';
import { useToastNotice } from '../../components/toast-notice';
import { PageNote } from '../../components/PageNote';
import { useUiClock } from '../../components/ui-clock';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { DEFAULT_DUE_SOON_POLICY, dueCountdown, formatIsoDate } from '../dates';
import { partNumbersPreview, workOrderStatusLabel } from './demand-lines';
import { CompletedWorkOrdersView } from './CompletedWorkOrdersView';
import { NewWorkOrderDialog } from './NewWorkOrderDialog';
import { WorkOrderDetailPanel } from './WorkOrderDetailPanel';

/** Display form of a Work Order Number — `—` when no external number
 * is known (display-only placeholder, never persisted). */
const woDisplay = (workOrderNumber: string | null) => workOrderNumber ?? '—';

/** Wait out the typing burst before asking the server (§11.1 search). */
const SEARCH_DEBOUNCE_MS = 250;

// Long-data preview rows (?state=long, development only): many Work
// Orders plus over-long WO and PN identifiers to exercise dense-table
// and truncation behavior. Compiled out of production builds.
const LONG_PREVIEW_WORK_ORDERS: WorkOrderSummary[] = import.meta.env.DEV
  ? [
      ...Array.from({ length: 20 }, (_, i): WorkOrderSummary => {
        const n = i + 1;
        return {
          id: -1000 - n,
          workOrderNumber: String(7300 + n).padStart(6, '0'),
          receivedDate: '2026-07-01',
          // Every fifth long-preview Work Order has no due date (valid).
          dueDate: n % 5 === 0 ? null : '2026-09-30',
          status: 'OPEN',
          completedAt: null,
          demandLineCount: 0,
          partNumbers: [`0114-60-${String(100 + n).padStart(4, '0')}-00`],
        };
      }),
      {
        id: -1099,
        workOrderNumber: '007099-SUPPLEMENTAL-AMENDMENT-2026-REV-B',
        receivedDate: '2026-07-20',
        dueDate: '2026-10-15',
        status: 'OPEN',
        completedAt: null,
        demandLineCount: 0,
        partNumbers: ['0118-40-0022-07-0455-88-REV-C'],
      },
    ]
  : [];

// Management sub view for manual Work Order entry and explicit
// production release, wired to the real /api/work-orders surface
// (Phase 4): saving persists business demand transactionally, and
// Release to production is the separate explicit action of §11.4.
//
// Two routes belong to this sub view (GUI_DESIGN §11): the active WO
// list on `/management/work-orders` and the read-only Completed Work
// Orders history page on `/management/work-orders/completed` — a REAL
// view on `/api/work-orders/completed` since Phase 10 (completion is
// derived by the server from allocation). The Management sub-view bar
// keeps Work Orders active on both.
export function WorkOrdersView() {
  const { route } = useRouter();
  if (route.view === 'management' && route.page === 'completed') {
    return <CompletedWorkOrdersView />;
  }
  return <ActiveWorkOrdersView />;
}

function ActiveWorkOrdersView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const { setNavigationGuard } = useRouter();
  const writeBlocked = status !== 'connected';
  const { showNotice, noticeElement } = useToastNotice();

  // Selected Work Order — its details open as a modal dialog over the
  // list (GUI_DESIGN §11.2); the list stays mounted and the URL never
  // changes.
  const [detailId, setDetailId] = useState<number | null>(null);
  const [search, setSearch] = useState('');

  // Search runs on the SERVER (GUI_DESIGN §11.1 — a contains-match over
  // the Work Order Number), so the typing burst is waited out instead
  // of one request per keystroke. Only allocation-derived completion
  // (Phase 10) takes a Work Order off the active list, so it stays
  // large: it is never downloaded whole to be filtered in the browser.
  const [settledSearch, setSettledSearch] = useState(search);
  useEffect(() => {
    const timer = setTimeout(
      () => setSettledSearch(search),
      SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [search]);
  // The page carries the entry it answers for. `useApiData` keeps the
  // PREVIOUS ready value while the next request is in flight, so "there
  // is a ready result" alone says nothing about the entry on screen —
  // the answered query has to be compared to it (the same rule the Add
  // Part lookup follows).
  const loadWorkOrders = useCallback(
    async (): Promise<{ query: string; rows: WorkOrderSummary[] }> => ({
      query: settledSearch,
      rows: await listWorkOrders(settledSearch),
    }),
    [settledSearch],
  );
  const workOrdersData = useApiData(loadWorkOrders);

  const answered =
    workOrdersData.state.status === 'ready' ? workOrdersData.state.data : null;
  // The page for exactly what is in the field, or null across BOTH
  // unanswered windows: the debounce has not asked yet (`search` has
  // moved past `settledSearch`), or the request for it has not come
  // back. Nothing derived from the current search — "no match", the
  // bound — may be concluded from anything but this.
  const currentRows =
    answered !== null && answered.query === search ? answered.rows : null;
  // The last page that did answer stays on screen while the next one
  // loads, so the list does not flicker and the search field keeps
  // focus. It is presentation only — never treated as this search's
  // answer.
  const [retainedRows, setRetainedRows] = useState<WorkOrderSummary[] | null>(
    null,
  );
  useEffect(() => {
    if (answered) setRetainedRows(answered.rows);
  }, [answered]);
  const [newWorkOrderOpen, setNewWorkOrderOpen] = useState(false);
  const [newWorkOrderDirty, setNewWorkOrderDirty] = useState(false);
  const [detailDirty, setDetailDirty] = useState(false);

  const dirty =
    (newWorkOrderOpen && newWorkOrderDirty) ||
    (detailId !== null && detailDirty);

  // Unsaved-change protection for top-level navigation, Management
  // sub-navigation and browser back/forward (the router consults the
  // guard), plus reload / tab close via beforeunload.
  useEffect(() => {
    if (!dirty) {
      setNavigationGuard(null);
      return;
    }
    setNavigationGuard(() =>
      window.confirm(
        'Work Orders has unsaved changes. Discard them and leave this view?',
      ),
    );
    return () => setNavigationGuard(null);
  }, [dirty, setNavigationGuard]);

  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  const handleDetailDirtyChange = useCallback(
    (value: boolean) => setDetailDirty(value),
    [],
  );

  if (preview === 'error') {
    return (
      <section className="wo-view" aria-label="Work Orders">
        <ErrorState
          message="Work Orders could not be loaded."
          detail="Check the backend connection and try again."
        />
      </section>
    );
  }
  if (workOrdersData.state.status === 'error') {
    return (
      <section className="wo-view" aria-label="Work Orders">
        <ErrorState
          message="Work Orders could not be loaded."
          detail={workOrdersData.state.message}
          onRetry={workOrdersData.reload}
        />
      </section>
    );
  }
  const loadedRows = currentRows ?? retainedRows;
  if (preview === 'loading' || loadedRows === null) {
    return (
      <section className="wo-view" aria-label="Work Orders">
        <LoadingState label="Loading Work Orders" />
      </section>
    );
  }

  const listData: WorkOrderSummary[] =
    preview === 'empty'
      ? []
      : preview === 'long'
        ? [...loadedRows, ...LONG_PREVIEW_WORK_ORDERS]
        : loadedRows;
  // A search is unanswered until its OWN page comes back; the rows on
  // screen may still be the previous one.
  const searching = currentRows === null;
  // The server bounds the page; a full page means "refine", not "these
  // are all of them". Measured on the answer for the CURRENT search
  // only — a previous full page says nothing about this one, and the
  // development-only `?state=` fixtures are not a server answer at all.
  const bounded =
    preview === null &&
    currentRows !== null &&
    currentRows.length >= WORK_ORDER_LIST_LIMIT;

  const openWorkOrder = (id: number) => {
    setDetailDirty(false);
    setDetailId(id);
  };

  const closeDetail = () => {
    setDetailDirty(false);
    setDetailId(null);
  };

  const closeNewWorkOrder = () => {
    setNewWorkOrderOpen(false);
    setNewWorkOrderDirty(false);
  };

  return (
    <section className="wo-view" aria-label="Work Orders">
      <WorkOrderListPanel
        list={listData}
        search={search}
        searching={searching}
        bounded={bounded}
        onSearch={setSearch}
        onOpen={openWorkOrder}
        onNew={() => setNewWorkOrderOpen(true)}
      />
      {detailId !== null && (
        <WorkOrderDetailPanel
          key={detailId}
          workOrderId={detailId}
          writeBlocked={writeBlocked}
          onClose={closeDetail}
          onChanged={workOrdersData.reload}
          onDirtyChange={handleDetailDirtyChange}
          showNotice={showNotice}
        />
      )}

      {newWorkOrderOpen && (
        <NewWorkOrderDialog
          writeBlocked={writeBlocked}
          onClose={closeNewWorkOrder}
          onOpenExisting={(existing) => {
            // A WO Number is never duplicated (uniqueness spans the
            // whole history, PROJECT_PROFILE §8.2) — the existing Work
            // Order opens instead: a completed one opens read-only
            // from the permanent history.
            closeNewWorkOrder();
            showNotice(
              existing.status === 'COMPLETED'
                ? `⚠ WO Number ${existing.workOrderNumber} already exists and is completed — opening its read-only details from the completed history instead of duplicating it.`
                : `⚠ WO Number ${existing.workOrderNumber} already exists — opening the existing Work Order instead of duplicating it.`,
            );
            openWorkOrder(existing.id);
          }}
          onSaved={(saved) => {
            closeNewWorkOrder();
            workOrdersData.reload();
            showNotice(
              `💾 WO ${woDisplay(saved.workOrderNumber)} saved — business demand only (${saved.demands.length} line${saved.demands.length > 1 ? 's' : ''}).`,
            );
          }}
          onDirtyChange={setNewWorkOrderDirty}
          showNotice={showNotice}
        />
      )}

      {noticeElement}
    </section>
  );
}

function WorkOrderListPanel({
  list,
  search,
  searching,
  bounded,
  onSearch,
  onOpen,
  onNew,
}: {
  /** The server's page — already filtered and already bounded. */
  list: WorkOrderSummary[];
  search: string;
  /** No page for the current entry has come back yet — the rows below
   * may still be the previous search's. Nothing about the current
   * search may be concluded from them. */
  searching: boolean;
  /** The page came back full: there may be more behind the bound. */
  bounded: boolean;
  onSearch: (v: string) => void;
  onOpen: (id: number) => void;
  onNew: () => void;
}) {
  // Due-date lateness is DERIVED from the fixed due date and the
  // shared UI clock — the urgency keeps updating while the view stays
  // open.
  const now = useUiClock('minute');
  const dueTone = (w: WorkOrderSummary): string => {
    if (!w.dueDate) return 'none';
    // Colour-ramped like every other due date (GUI_DESIGN §11.1/§3.9):
    // late → soon → ok, from the shared Due Soon configuration
    // stand-in.
    return dueCountdown(w.dueDate, now, {
      received: w.receivedDate,
      policy: DEFAULT_DUE_SOON_POLICY,
    }).dueClass;
  };
  // The rows come from the server — no second, local filter with its
  // own accidental semantics. While `searching`, they are the PREVIOUS
  // page kept on screen to avoid a flicker, so "there is nothing here"
  // is only ever said once the current search has answered.
  const rows = list;
  const hasSearch = search.trim() !== '';
  const noRows = !searching && rows.length === 0;
  return (
    <div>
      <div className="wo-head">
        <h1>Work Orders</h1>
      </div>
      <p className="wo-sub">
        Manual Work Order entry and explicit production release.{' '}
        <b>Saving demand never creates production quantity</b> — physical
        quantity enters production only through the explicit{' '}
        <b>Release to production</b> action on a demand line. Select a Work
        Order to open its details.
      </p>
      {/* Toolbar (v15): search + primary action on one row, the action
          right-aligned with the full-width list — the same layout as
          the Machines page. */}
      <div className="wo-tools">
        <input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search WO Number…"
          aria-label="Search WO Number"
        />
        <span className="spacer" />
        {/* Quiet entry to the read-only completed history (§11.5) —
            deliberately secondary beside the primary action. */}
        <Link
          className="btn ghost cwo-link"
          to="/management/work-orders/completed"
        >
          Completed Work Orders ›
        </Link>
        <button className="btn primary" onClick={onNew}>
          ＋ New Work Order
        </button>
      </div>
      {searching ? (
        <div className="wo-bound" role="status">
          Searching Work Orders…
        </div>
      ) : bounded ? (
        <div className="wo-bound">
          Showing the first {WORK_ORDER_LIST_LIMIT} Work Orders — refine the
          search to narrow it.
        </div>
      ) : null}
      {noRows && !hasSearch ? (
        <EmptyState message="No Work Orders yet — create the first one with ＋ New Work Order." />
      ) : (
        <table className="wolist">
          <thead>
            <tr>
              <th>WO Number</th>
              <th>Received</th>
              <th>Due date</th>
              <th>Demand lines</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {/* The "nothing matches" row is a claim about the CURRENT
                search, so it waits for that search's own page: while
                one is in flight the previous rows stay on screen and
                this stays out. (Reaching it implies a search — an
                answered empty page with no search renders the empty
                state instead of the table.) */}
            {noRows ? (
              <tr>
                <td colSpan={5} className="empty">
                  No active Work Order matches “{search.trim()}” — search{' '}
                  <Link to="/management/work-orders/completed">
                    Completed Work Orders
                  </Link>
                  , check the number, or create it with ＋ New Work Order
                </td>
              </tr>
            ) : null}
            {rows.map((w) => (
              // The COMPLETE row opens the Work Order Details dialog
              // (v15): the WO-cell button stays the keyboard
              // (Enter/Space) and screen-reader entry point — its
              // activation bubbles to this row handler; no other
              // interactive control lives inside the row.
              <tr key={w.id} className="selrow" onClick={() => onOpen(w.id)}>
                <td>
                  <button
                    className="rowbtn"
                    aria-label={`Open Work Order ${woDisplay(w.workOrderNumber)}`}
                  >
                    <span className="wo" title={woDisplay(w.workOrderNumber)}>
                      {woDisplay(w.workOrderNumber)}
                    </span>
                    {w.workOrderNumber === null ? (
                      <span className="sub" style={{ display: 'block' }}>
                        internal Work Order — no external number yet
                      </span>
                    ) : null}
                  </button>
                </td>
                {/* data-label: inline column captions in the
                      collapsed stacked layout (GUI_DESIGN §2.5) —
                      bare dates and a line count are not self-evident
                      without the header row. */}
                <td className="mono-sm" data-label="Received">
                  {formatIsoDate(w.receivedDate)}
                </td>
                <td className="mono-sm" data-label="Due date">
                  <span className={`duetxt ${dueTone(w)}`}>
                    {formatIsoDate(w.dueDate)}
                  </span>
                </td>
                <td data-label="Demand lines">
                  {w.demandLineCount}
                  <div className="sub mono-sm">
                    {partNumbersPreview(w.partNumbers)}
                  </div>
                </td>
                <td>
                  <span
                    className={`wostat ${workOrderStatusLabel(w.status).toLowerCase()}`}
                  >
                    {workOrderStatusLabel(w.status)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <PageNote>
        Completed Work Orders leave the active list and stay permanently
        available in{' '}
        <Link to="/management/work-orders/completed">
          Completed Work Orders
        </Link>
        . An internal Work Order without an external number displays{' '}
        <span className="mono">—</span>; the real number can be added later
        through an audited edit.
      </PageNote>
    </div>
  );
}
