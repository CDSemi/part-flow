import './work-orders.css';

import { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import type { ComponentType, LazyExoticComponent } from 'react';

import { listWorkOrders } from '../../api/work-orders';
import type { WorkOrderSummary } from '../../api/work-orders';
import { useApiData } from '../../api/use-api-data';
import { useConnectivity } from '../../app/connectivity-context';
import { Link } from '../../app/link';
import { useRouter } from '../../app/router-context';
import { getViewStatePreview } from '../../app/view-state';
import { useMockNotice } from '../../components/mock-notice';
import { PageNote } from '../../components/PageNote';
import { useUiClock } from '../../components/ui-clock';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import { DEFAULT_DUE_SOON_POLICY, dueCountdown, formatIsoDate } from '../dates';
import { partNumbersPreview } from './demand-lines';
import { CompletedWorkOrdersUnavailable } from './CompletedWorkOrdersUnavailable';
import { NewWorkOrderDialog } from './NewWorkOrderDialog';
import { ReleaseDialog } from './ReleaseDialog';
import {
  WorkOrderDetailPanel,
  workOrderStatusLabel,
} from './WorkOrderDetailPanel';
import type { ReleaseRequestContext } from './WorkOrderDetailPanel';

/** Display form of a Work Order Number — `—` when no external number
 * is known (display-only placeholder, never persisted). */
const woDisplay = (workOrderNumber: string | null) => workOrderNumber ?? '—';

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
        demandLineCount: 0,
        partNumbers: ['0118-40-0022-07-0455-88-REV-C'],
      },
    ]
  : [];

// The §11.5 Completed Work Orders page has no backend yet (completion
// = full allocation, Phase 10): the real route states that honestly.
// The development-only visual preview of the approved page design is
// reachable ONLY through this `import.meta.env.DEV`-guarded lazy
// import, so production builds drop it — and its mock history — from
// the module graph entirely.
const CompletedWorkOrdersPreview: LazyExoticComponent<ComponentType> | null =
  import.meta.env.DEV
    ? lazy(() =>
        import('./CompletedWorkOrdersView').then((m) => ({
          default: m.CompletedWorkOrdersView,
        })),
      )
    : null;

// Management sub view for manual Work Order entry and explicit
// production release, wired to the real /api/work-orders surface
// (Phase 4): saving persists business demand transactionally, and
// Release to production is the separate explicit action of §11.4.
//
// Two routes belong to this sub view (GUI_DESIGN §11): the active WO
// list on `/management/work-orders` and the read-only Completed Work
// Orders history page on `/management/work-orders/completed`. The
// Management sub-view bar keeps Work Orders active on both.
export function WorkOrdersView() {
  const { route } = useRouter();
  if (route.view === 'management' && route.page === 'completed') {
    if (CompletedWorkOrdersPreview) {
      return (
        <Suspense
          fallback={
            <section className="wo-view" aria-label="Completed Work Orders">
              <LoadingState label="Loading Completed Work Orders" />
            </section>
          }
        >
          <CompletedWorkOrdersPreview />
        </Suspense>
      );
    }
    return <CompletedWorkOrdersUnavailable />;
  }
  return <ActiveWorkOrdersView />;
}

function ActiveWorkOrdersView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const { setNavigationGuard } = useRouter();
  const writeBlocked = status !== 'connected';
  const { showNotice, noticeElement } = useMockNotice();

  const workOrdersData = useApiData(listWorkOrders);

  // Selected Work Order — its details open as a modal dialog over the
  // list (GUI_DESIGN §11.2); the list stays mounted and the URL never
  // changes.
  const [detailId, setDetailId] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [newWorkOrderOpen, setNewWorkOrderOpen] = useState(false);
  const [newWorkOrderDirty, setNewWorkOrderDirty] = useState(false);
  const [detailDirty, setDetailDirty] = useState(false);
  // Demand ids released in THIS session: their lines render read-only
  // with the release evidence. (Earlier sessions' releases are
  // enforced by the backend — a removal attempt answers 409.)
  const [sessionReleased, setSessionReleased] = useState<Set<number>>(
    new Set(),
  );
  const [releaseDialog, setReleaseDialog] = useState<{
    workOrderId: number;
    workOrderNumber: string | null;
    demand: ReleaseRequestContext;
  } | null>(null);

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

  if (preview === 'loading' || workOrdersData.state.status === 'loading') {
    return (
      <section className="wo-view" aria-label="Work Orders">
        <LoadingState label="Loading Work Orders" />
      </section>
    );
  }
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

  const listData: WorkOrderSummary[] =
    preview === 'empty'
      ? []
      : preview === 'long'
        ? [...workOrdersData.state.data, ...LONG_PREVIEW_WORK_ORDERS]
        : workOrdersData.state.data;

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
        onSearch={setSearch}
        onOpen={openWorkOrder}
        onNew={() => setNewWorkOrderOpen(true)}
      />
      {detailId !== null && (
        <WorkOrderDetailPanel
          key={detailId}
          workOrderId={detailId}
          sessionReleased={sessionReleased}
          writeBlocked={writeBlocked}
          onClose={closeDetail}
          onRelease={(demand) => {
            const summary = listData.find((w) => w.id === detailId);
            setReleaseDialog({
              workOrderId: detailId,
              workOrderNumber: summary?.workOrderNumber ?? null,
              demand,
            });
          }}
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
            // Order opens instead.
            closeNewWorkOrder();
            showNotice(
              `⚠ WO Number ${existing.workOrderNumber} already exists — opening the existing Work Order instead of duplicating it.`,
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

      {releaseDialog && (
        <ReleaseDialog
          workOrderId={releaseDialog.workOrderId}
          workOrderNumber={releaseDialog.workOrderNumber}
          demand={releaseDialog.demand}
          writeBlocked={writeBlocked}
          onCancel={() => {
            setReleaseDialog(null);
            showNotice('✕ Release cancelled — nothing was created.');
          }}
          onReleased={(result) => {
            setSessionReleased((current) =>
              new Set(current).add(releaseDialog.demand.demandId),
            );
            setReleaseDialog(null);
            workOrdersData.reload();
            showNotice(
              `✓ ${result.partNumber} released to production × ${result.quantity} · Quantity Flow #${result.quantityFlowId}.`,
            );
          }}
        />
      )}
      {noticeElement}
    </section>
  );
}

function WorkOrderListPanel({
  list,
  search,
  onSearch,
  onOpen,
  onNew,
}: {
  list: WorkOrderSummary[];
  search: string;
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
    // The policy is the shared Due Soon configuration stand-in — only
    // the `late` class is used here, but the call stays uniform.
    return dueCountdown(w.dueDate, now, {
      received: w.receivedDate,
      policy: DEFAULT_DUE_SOON_POLICY,
    }).dueClass === 'late'
      ? 'late'
      : '';
  };
  const query = search.trim().toLowerCase();
  const rows = list.filter(
    (w) =>
      !query ||
      ((w.workOrderNumber ?? '') + ' ' + partNumbersPreview(w.partNumbers))
        .toLowerCase()
        .includes(query),
  );
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
      {list.length === 0 ? (
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
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="empty">
                  No active Work Order matches “{search.trim()}” — search{' '}
                  <Link to="/management/work-orders/completed">
                    Completed Work Orders
                  </Link>
                  , check the number, or create it with ＋ New Work Order
                </td>
              </tr>
            ) : (
              rows.map((w) => (
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
              ))
            )}
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
