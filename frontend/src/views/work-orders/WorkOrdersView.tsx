import './work-orders.css';

import { useCallback, useEffect, useState } from 'react';

import { useConnectivity } from '../../app/connectivity-context';
import { useRouter } from '../../app/router-context';
import { getViewStatePreview } from '../../app/view-state';
import { useMockNotice } from '../../components/mock-notice';
import { ModalDialog } from '../../components/ModalDialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import {
  MOCK_RELEASE_DATA,
  MOCK_WORK_ORDER_LIST,
} from '../../mocks/work-orders';
import type { MockWorkOrder } from '../view-models';
import { formatIsoDate } from './dates';
import { NewWorkOrderDialog } from './NewWorkOrderDialog';
import { WorkOrderDetailPanel } from './WorkOrderDetailPanel';

type Panel = { kind: 'list' } | { kind: 'detail'; workOrderNumber: string };

// Long-data preview rows (?state=long): many Work Orders plus over-long
// WO and PN identifiers to exercise dense-table and truncation behavior.
const LONG_PREVIEW_WORK_ORDERS: MockWorkOrder[] = [
  ...Array.from({ length: 20 }, (_, i): MockWorkOrder => {
    const n = i + 1;
    return {
      workOrderNumber: String(7300 + n).padStart(6, '0'),
      received: '2026-07-01',
      due: '2026-09-30',
      dueClass: '',
      status: 'Open',
      preview: `0114-60-${String(100 + n).padStart(4, '0')}-00`,
      lines: [],
    };
  }),
  {
    workOrderNumber: '007099-SUPPLEMENTAL-AMENDMENT-2026-REV-B',
    received: '2026-07-20',
    due: '2026-10-15',
    dueClass: '',
    status: 'Open',
    preview: '0118-40-0022-07-0455-88-REV-C',
    lines: [],
  },
];

// Management sub view for manual Work Order entry and explicit production
// release. Phase 2: layout and local interactions only — saving and
// releasing are development mocks that change presentation state and
// never persist.
export function WorkOrdersView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const { setNavigationGuard } = useRouter();
  const writeBlocked = status !== 'connected';
  const { showNotice, noticeElement } = useMockNotice();

  const [panel, setPanel] = useState<Panel>({ kind: 'list' });
  const [workOrderList, setWorkOrderList] =
    useState<MockWorkOrder[]>(MOCK_WORK_ORDER_LIST);
  const [search, setSearch] = useState('');
  const [newWorkOrderOpen, setNewWorkOrderOpen] = useState(false);
  const [newWorkOrderDirty, setNewWorkOrderDirty] = useState(false);
  const [detailDirty, setDetailDirty] = useState(false);
  const [releasedLines, setReleasedLines] = useState<Set<string>>(new Set());
  const [releaseDialog, setReleaseDialog] = useState<{
    workOrderNumber: string;
    pn: string;
  } | null>(null);

  const dirty =
    (newWorkOrderOpen && newWorkOrderDirty) ||
    (panel.kind === 'detail' && detailDirty);

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
          detail="Check the backend connection, then retry from the offline banner."
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

  const openWorkOrder = (workOrderNumber: string) => {
    setDetailDirty(false);
    setPanel({ kind: 'detail', workOrderNumber });
  };

  const closeNewWorkOrder = () => {
    setNewWorkOrderOpen(false);
    setNewWorkOrderDirty(false);
  };

  return (
    <section className="wo-view" aria-label="Work Orders">
      {panel.kind === 'list' && (
        <WorkOrderListPanel
          list={listData}
          search={search}
          onSearch={setSearch}
          onOpen={openWorkOrder}
          onNew={() => setNewWorkOrderOpen(true)}
        />
      )}
      {panel.kind === 'detail' && (
        <WorkOrderDetailPanel
          key={panel.workOrderNumber}
          workOrder={listData.find(
            (w) => w.workOrderNumber === panel.workOrderNumber,
          )}
          releasedLines={releasedLines}
          writeBlocked={writeBlocked}
          onBack={() => {
            setDetailDirty(false);
            setPanel({ kind: 'list' });
          }}
          onRelease={(pn) =>
            setReleaseDialog({ workOrderNumber: panel.workOrderNumber, pn })
          }
          onSaveDetail={(updated) => {
            setWorkOrderList((current) =>
              current.map((w) =>
                w.workOrderNumber === updated.workOrderNumber ? updated : w,
              ),
            );
            showNotice(
              `💾 WO ${updated.workOrderNumber} demand updated (mock) — business demand only, local state only. No Quantity Flow, no Movement, no release; nothing was persisted to the backend.`,
            );
          }}
          onDirtyChange={handleDetailDirtyChange}
          showNotice={showNotice}
        />
      )}

      {newWorkOrderOpen && (
        <NewWorkOrderDialog
          existing={workOrderList.map((w) => w.workOrderNumber)}
          writeBlocked={writeBlocked}
          onClose={closeNewWorkOrder}
          onOpenExisting={(workOrderNumber) => {
            closeNewWorkOrder();
            showNotice(
              `⚠ WO Number ${workOrderNumber} already exists — opening the existing Work Order instead of duplicating it.`,
            );
            openWorkOrder(workOrderNumber);
          }}
          onSave={(workOrder) => {
            setWorkOrderList((current) => [workOrder, ...current]);
            closeNewWorkOrder();
            showNotice(
              `💾 WO ${workOrder.workOrderNumber} saved (mock) — business demand only (${workOrder.lines.length} line${workOrder.lines.length > 1 ? 's' : ''}), local state only. Nothing was persisted to the backend. No release.`,
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
                `${releaseDialog.workOrderNumber}:${releaseDialog.pn}`,
              ),
            );
            setReleaseDialog(null);
            showNotice(
              `✓ ${releaseDialog.pn} marked released (mock) × ${qty} · Route “${route}” — presentation only, no production write occurred.`,
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
  onOpen: (workOrderNumber: string) => void;
  onNew: () => void;
}) {
  const query = search.trim().toLowerCase();
  const rows = list.filter(
    (w) =>
      !query ||
      (w.workOrderNumber + ' ' + w.preview).toLowerCase().includes(query),
  );
  return (
    <div>
      <div className="wo-head">
        <h1>Work Orders</h1>
        <span className="spacer" />
        <button className="btn primary" onClick={onNew}>
          ＋ New Work Order
        </button>
      </div>
      <p className="wo-sub">
        Manual Work Order entry and explicit production release.{' '}
        <b>Saving demand never creates production quantity</b> — physical
        quantity enters production only through the explicit{' '}
        <b>Release to production</b> action on a demand line. Select a Work
        Order to see its demand lines. <b>＋ New Work Order</b> opens a dialog
        over this list — the URL does not change.
      </p>
      <div className="wo-tools">
        <input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search WO Number… (an existing WO Number is opened — duplicates are never created)"
          aria-label="Search WO Number"
        />
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
                  No Work Order matches “{search.trim()}” — check the number, or
                  create it with ＋ New Work Order
                </td>
              </tr>
            ) : (
              rows.map((w) => (
                <tr key={w.workOrderNumber}>
                  <td>
                    <button
                      className="rowbtn"
                      onClick={() => onOpen(w.workOrderNumber)}
                    >
                      <span className="wo" title={w.workOrderNumber}>
                        {w.workOrderNumber}
                      </span>
                      {w.internal ? (
                        <span className="sub" style={{ display: 'block' }}>
                          temporary internal Work Order
                        </span>
                      ) : null}
                    </button>
                  </td>
                  <td className="mono-sm">{formatIsoDate(w.received)}</td>
                  <td className="mono-sm">
                    <span className={`duetxt ${w.dueClass}`}>
                      {formatIsoDate(w.due)}
                    </span>
                  </td>
                  <td>
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
      <div className="wo-note">
        Completed Work Orders (every Work Order Demand fully allocated) move out
        of the active list but remain permanently available in history.
        Temporary internal Work Orders (
        <span className="mono">TMP-…-REWORK/MODIFY</span>) appear here like any
        other Work Order. Work Orders handles business demand only — it is not
        customer, pricing, invoicing, shipping, purchasing, or accounting
        functionality.
      </div>
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
      <h3>Release to production — explicit action (development mock)</h3>
      <div className="big mono">{pn}</div>
      <div className="sub">
        WO 007010 demand · requested <b>{data?.requested ?? '—'}</b> · nothing
        is created until you confirm — and in Phase 2 confirming changes{' '}
        <b>local presentation state only</b>.
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
        Release summary — would create: Quantity Flow <b>× {qty || '0'}</b> ·
        independent Route snapshot <b>“{route}”</b> · <b>RECEIVED</b> into{' '}
        <b>Material</b> (Operation <b>Receiving</b>) · current position derived
        atomically. Existing flows are never merged.
      </div>
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel — nothing created
        </button>
        <button
          className="bigbtn primary"
          disabled={!parsedQty || parsedQty < 1}
          onClick={() => onConfirm(parsedQty, route)}
        >
          Confirm release (mock)
        </button>
      </div>
    </ModalDialog>
  );
}
