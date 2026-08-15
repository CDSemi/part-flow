import './work-orders.css';

import { useCallback, useEffect, useState } from 'react';

import { useConnectivity } from '../../app/connectivity-context';
import { useRouter } from '../../app/router-context';
import { getViewStatePreview } from '../../app/view-state';
import { DevNotice } from '../../components/DevNotice';
import { useMockNotice } from '../../components/mock-notice';
import { ModalDialog } from '../../components/ModalDialog';
import { PageNote } from '../../components/PageNote';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import {
  MOCK_COMPLETED_WORK_ORDERS,
  MOCK_RELEASE_DATA,
  MOCK_WORK_ORDER_LIST,
} from '../../mocks/work-orders';
import { Link } from '../../app/link';
import { useUiClock } from '../../components/ui-clock';
import { DEFAULT_DUE_SOON_POLICY, dueCountdown, formatIsoDate } from '../dates';
import type { MockWorkOrder } from '../view-models';
import { CompletedWorkOrdersView } from './CompletedWorkOrdersView';
import { NewWorkOrderDialog } from './NewWorkOrderDialog';
import { WorkOrderDetailPanel } from './WorkOrderDetailPanel';

/** Display form of a Work Order Number — `—` when no external number
 * is known (display-only placeholder, never persisted). */
const woDisplay = (workOrderNumber: string | null) => workOrderNumber ?? '—';

// Long-data preview rows (?state=long): many Work Orders plus over-long
// WO and PN identifiers to exercise dense-table and truncation behavior.
const LONG_PREVIEW_WORK_ORDERS: MockWorkOrder[] = [
  ...Array.from({ length: 20 }, (_, i): MockWorkOrder => {
    const n = i + 1;
    return {
      id: `wo-long-${n}`,
      workOrderNumber: String(7300 + n).padStart(6, '0'),
      received: '2026-07-01',
      // Every fifth long-preview Work Order has no due date (valid).
      due: n % 5 === 0 ? null : '2026-09-30',
      status: 'Open',
      preview: `0114-60-${String(100 + n).padStart(4, '0')}-00`,
      lines: [],
    };
  }),
  {
    id: 'wo-long-supplemental',
    workOrderNumber: '007099-SUPPLEMENTAL-AMENDMENT-2026-REV-B',
    received: '2026-07-20',
    due: '2026-10-15',
    status: 'Open',
    preview: '0118-40-0022-07-0455-88-REV-C',
    lines: [],
  },
];

// Management sub view for manual Work Order entry and explicit production
// release. Phase 2: layout and local interactions only — saving and
// releasing are development mocks that change presentation state and
// never persist.
//
// Two routes belong to this sub view (GUI_DESIGN §11): the active WO
// list on `/management/work-orders` and the read-only Completed Work
// Orders history page on `/management/work-orders/completed`. The
// Management sub-view bar keeps Work Orders active on both.
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
  const { showNotice, noticeElement } = useMockNotice();

  // Selected Work Order — its details open as a modal dialog over the
  // list (GUI_DESIGN §11.2); the list stays mounted and the URL never
  // changes.
  const [detailId, setDetailId] = useState<string | null>(null);
  const [workOrderList, setWorkOrderList] =
    useState<MockWorkOrder[]>(MOCK_WORK_ORDER_LIST);
  const [search, setSearch] = useState('');
  const [newWorkOrderOpen, setNewWorkOrderOpen] = useState(false);
  const [newWorkOrderDirty, setNewWorkOrderDirty] = useState(false);
  const [detailDirty, setDetailDirty] = useState(false);
  const [releasedLines, setReleasedLines] = useState<Set<string>>(new Set());
  const [releaseDialog, setReleaseDialog] = useState<{
    workOrderId: string;
    pn: string;
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

  if (preview === 'loading') {
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

  const listData: MockWorkOrder[] =
    preview === 'empty'
      ? []
      : preview === 'long'
        ? [...workOrderList, ...LONG_PREVIEW_WORK_ORDERS]
        : workOrderList;

  const openWorkOrder = (id: string) => {
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
          workOrder={
            // A completed Work Order is only reached here through the
            // New Work Order duplicate check (its number already
            // exists in history) — the dialog is read-only for it.
            listData.find((w) => w.id === detailId) ??
            MOCK_COMPLETED_WORK_ORDERS.find((w) => w.id === detailId)
          }
          releasedLines={releasedLines}
          writeBlocked={writeBlocked}
          onClose={closeDetail}
          onRelease={(pn) => setReleaseDialog({ workOrderId: detailId, pn })}
          onSaveDetail={(updated) => {
            setWorkOrderList((current) =>
              current.map((w) => (w.id === updated.id ? updated : w)),
            );
            showNotice(
              `💾 WO ${woDisplay(updated.workOrderNumber)} demand updated — business demand only.`,
            );
          }}
          onDirtyChange={handleDetailDirtyChange}
          showNotice={showNotice}
        />
      )}

      {newWorkOrderOpen && (
        <NewWorkOrderDialog
          // WO-Number uniqueness spans the whole history including
          // completed Work Orders (PROJECT_PROFILE §8.2) — a completed
          // number is never silently reusable.
          existing={[...workOrderList, ...MOCK_COMPLETED_WORK_ORDERS].flatMap(
            (w) => (w.workOrderNumber === null ? [] : [w.workOrderNumber]),
          )}
          writeBlocked={writeBlocked}
          onClose={closeNewWorkOrder}
          onOpenExisting={(workOrderNumber) => {
            closeNewWorkOrder();
            const existing = workOrderList.find(
              (w) => w.workOrderNumber === workOrderNumber,
            );
            if (existing) {
              showNotice(
                `⚠ WO Number ${workOrderNumber} already exists — opening the existing Work Order instead of duplicating it.`,
              );
              openWorkOrder(existing.id);
              return;
            }
            const completed = MOCK_COMPLETED_WORK_ORDERS.find(
              (w) => w.workOrderNumber === workOrderNumber,
            );
            if (completed) {
              showNotice(
                `⚠ WO Number ${workOrderNumber} already exists and is Complete — opening its read-only details.`,
              );
              openWorkOrder(completed.id);
            }
          }}
          onSave={(workOrder) => {
            setWorkOrderList((current) => [workOrder, ...current]);
            closeNewWorkOrder();
            showNotice(
              `💾 WO ${woDisplay(workOrder.workOrderNumber)} saved — business demand only (${workOrder.lines.length} line${workOrder.lines.length > 1 ? 's' : ''}).`,
            );
          }}
          onDirtyChange={setNewWorkOrderDirty}
          showNotice={showNotice}
        />
      )}

      {releaseDialog && (
        <ReleaseDialog
          pn={releaseDialog.pn}
          onCancel={() => {
            setReleaseDialog(null);
            showNotice('✕ Release cancelled — nothing was created.');
          }}
          onConfirm={(qty, route) => {
            setReleasedLines((current) =>
              new Set(current).add(
                `${releaseDialog.workOrderId}:${releaseDialog.pn}`,
              ),
            );
            setReleaseDialog(null);
            showNotice(
              `✓ ${releaseDialog.pn} released to production × ${qty} · Route “${route}”.`,
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
  list: MockWorkOrder[];
  search: string;
  onSearch: (v: string) => void;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  // Due-date lateness is DERIVED from the fixed due date and the
  // shared UI clock (a completed Work Order is never flagged late) —
  // the urgency keeps updating while the view stays open.
  const now = useUiClock('minute');
  const dueTone = (w: MockWorkOrder): string => {
    if (!w.due) return 'none';
    if (w.status === 'Complete') return '';
    // The policy is the shared Due Soon configuration stand-in — only
    // the `late` class is used here, but the call stays uniform.
    return dueCountdown(w.due, now, {
      received: w.received,
      policy: DEFAULT_DUE_SOON_POLICY,
    }).dueClass === 'late'
      ? 'late'
      : '';
  };
  const query = search.trim().toLowerCase();
  const rows = list.filter(
    (w) =>
      !query ||
      ((w.workOrderNumber ?? '') + ' ' + w.preview)
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
      <DevNotice>
        Development preview — saves and releases update sample data in this
        browser session only.
      </DevNotice>
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
                      {w.internal ? (
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
                    {formatIsoDate(w.received)}
                  </td>
                  <td className="mono-sm" data-label="Due date">
                    <span className={`duetxt ${dueTone(w)}`}>
                      {formatIsoDate(w.due)}
                    </span>
                  </td>
                  <td data-label="Demand lines">
                    {w.lines.length}
                    <div className="sub mono-sm">{w.preview}</div>
                  </td>
                  <td>
                    <span className={`wostat ${w.status.toLowerCase()}`}>
                      {w.status}
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

function ReleaseDialog({
  pn,
  onCancel,
  onConfirm,
}: {
  pn: string;
  onCancel: () => void;
  onConfirm: (qty: number, route: string) => void;
}) {
  const data = MOCK_RELEASE_DATA[pn];
  const [qty, setQty] = useState(String(data?.requested ?? ''));
  const [route, setRoute] = useState(data?.routes[0] ?? '—');
  const parsedQty = parseInt(qty || '0', 10);
  return (
    <ModalDialog
      label="Release to production — explicit action"
      onClose={onCancel}
      size="wide"
    >
      <h3>Release to production — explicit action</h3>
      <div className="big mono">{pn}</div>
      <div className="sub">
        WO 007010 demand · requested <b>{data?.requested ?? '—'}</b> — nothing
        is created until you confirm.
      </div>
      {data?.activeDistribution ? (
        <div className="relwarn">
          ⚠ <b>This PN already has active quantity:</b>{' '}
          <span className="mono">{data.activeDistribution}</span>. Confirm
          intent explicitly — release always creates a <b>separate</b> Quantity
          Flow; it never automatically adds to or merges existing flows.
        </div>
      ) : null}
      <div className="relgrid">
        <label htmlFor="rel-qty">Release quantity</label>
        <input
          id="rel-qty"
          className="mono"
          inputMode="numeric"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
        />
        <label htmlFor="rel-route">Route</label>
        <select
          id="rel-route"
          value={route}
          onChange={(e) => setRoute(e.target.value)}
        >
          {(data?.routes ?? ['—']).map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <label htmlFor="rel-area">Starting Area · Operation</label>
        <select
          id="rel-area"
          defaultValue="Material — Receiving (configured start)"
        >
          <option>Material — Receiving (configured start)</option>
        </select>
      </div>
      <div className="relsum">
        Release summary: <b>× {qty || '0'}</b> pcs as a new, separate Quantity
        Flow · Route <b>“{route}”</b> · starts in <b>Material</b> (Operation{' '}
        <b>Receiving</b>) with a recorded <b>RECEIVED</b> event. Existing
        quantity of this PN is never merged.
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          disabled={!parsedQty || parsedQty < 1}
          onClick={() => onConfirm(parsedQty, route)}
        >
          Confirm release
        </button>
      </div>
    </ModalDialog>
  );
}
