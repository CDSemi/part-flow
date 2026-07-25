import './purchase-orders.css';

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
import { MOCK_PO_LIST, MOCK_RELEASE_DATA } from '../../mocks/purchase-orders';
import type { MockPo } from '../view-models';
import { formatIsoDate } from './dates';
import { NewPoDialog } from './NewPoDialog';
import { PoDetailPanel } from './PoDetailPanel';

type Panel = { kind: 'list' } | { kind: 'detail'; po: string };

// Long-data preview rows (?state=long): many POs plus over-long PO and
// PN identifiers to exercise dense-table and truncation behavior.
const LONG_PREVIEW_POS: MockPo[] = [
  ...Array.from({ length: 20 }, (_, i): MockPo => {
    const n = i + 1;
    return {
      po: `PO-3${String(100 + n)}`,
      received: '2026-07-01',
      due: '2026-09-30',
      dueClass: '',
      status: 'Open',
      preview: `0114-60-${String(100 + n).padStart(4, '0')}-00`,
      lines: [],
    };
  }),
  {
    po: 'PO-1099-SUPPLEMENTAL-AMENDMENT-2026-REV-B',
    received: '2026-07-20',
    due: '2026-10-15',
    dueClass: '',
    status: 'Open',
    preview: '0118-40-0022-07-0455-88-REV-C',
    lines: [],
  },
];

// Management sub view for manual PO entry and explicit production release.
// Phase 2: layout and local interactions only — saving and releasing are
// development mocks that change presentation state and never persist.
export function PurchaseOrdersView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const { setNavigationGuard } = useRouter();
  const writeBlocked = status !== 'connected';
  const { showNotice, noticeElement } = useMockNotice();

  const [panel, setPanel] = useState<Panel>({ kind: 'list' });
  const [poList, setPoList] = useState<MockPo[]>(MOCK_PO_LIST);
  const [search, setSearch] = useState('');
  const [newPoOpen, setNewPoOpen] = useState(false);
  const [newPoDirty, setNewPoDirty] = useState(false);
  const [detailDirty, setDetailDirty] = useState(false);
  const [releasedLines, setReleasedLines] = useState<Set<string>>(new Set());
  const [releaseDialog, setReleaseDialog] = useState<{
    po: string;
    pn: string;
  } | null>(null);

  const dirty =
    (newPoOpen && newPoDirty) || (panel.kind === 'detail' && detailDirty);

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
        'Purchase Orders has unsaved changes. Discard them and leave this view?',
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
      <section className="po-view" aria-label="Purchase Orders">
        <LoadingState label="Loading Purchase Orders" />
      </section>
    );
  }
  if (preview === 'error') {
    return (
      <section className="po-view" aria-label="Purchase Orders">
        <ErrorState
          message="Purchase Orders could not be loaded."
          detail="Check the backend connection, then retry from the offline banner."
        />
      </section>
    );
  }

  const listData: MockPo[] =
    preview === 'empty'
      ? []
      : preview === 'long'
        ? [...poList, ...LONG_PREVIEW_POS]
        : poList;

  const openPo = (po: string) => {
    setDetailDirty(false);
    setPanel({ kind: 'detail', po });
  };

  const closeNewPo = () => {
    setNewPoOpen(false);
    setNewPoDirty(false);
  };

  return (
    <section className="po-view" aria-label="Purchase Orders">
      {panel.kind === 'list' && (
        <PoListPanel
          list={listData}
          search={search}
          onSearch={setSearch}
          onOpen={openPo}
          onNew={() => setNewPoOpen(true)}
        />
      )}
      {panel.kind === 'detail' && (
        <PoDetailPanel
          key={panel.po}
          po={listData.find((p) => p.po === panel.po)}
          releasedLines={releasedLines}
          writeBlocked={writeBlocked}
          onBack={() => {
            setDetailDirty(false);
            setPanel({ kind: 'list' });
          }}
          onRelease={(pn) => setReleaseDialog({ po: panel.po, pn })}
          onSaveDetail={(updated) => {
            setPoList((current) =>
              current.map((p) => (p.po === updated.po ? updated : p)),
            );
            showNotice(
              `💾 ${updated.po} demand updated (mock) — business demand only, local state only. No Quantity Flow, no Movement, no release; nothing was persisted to the backend.`,
            );
          }}
          onDirtyChange={handleDetailDirtyChange}
          showNotice={showNotice}
        />
      )}

      {newPoOpen && (
        <NewPoDialog
          existing={poList.map((p) => p.po)}
          writeBlocked={writeBlocked}
          onClose={closeNewPo}
          onOpenExisting={(po) => {
            closeNewPo();
            showNotice(
              `⚠ PO Number ${po} already exists — opening the existing PO instead of duplicating it.`,
            );
            openPo(po);
          }}
          onSave={(po) => {
            setPoList((current) => [po, ...current]);
            closeNewPo();
            showNotice(
              `💾 ${po.po} saved (mock) — business demand only (${po.lines.length} line${po.lines.length > 1 ? 's' : ''}), local state only. Nothing was persisted to the backend. No release.`,
            );
          }}
          onDirtyChange={setNewPoDirty}
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
              new Set(current).add(`${releaseDialog.po}:${releaseDialog.pn}`),
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

function PoListPanel({
  list,
  search,
  onSearch,
  onOpen,
  onNew,
}: {
  list: MockPo[];
  search: string;
  onSearch: (v: string) => void;
  onOpen: (po: string) => void;
  onNew: () => void;
}) {
  const query = search.trim().toLowerCase();
  const rows = list.filter(
    (p) => !query || (p.po + ' ' + p.preview).toLowerCase().includes(query),
  );
  return (
    <div>
      <div className="po-head">
        <h1>Purchase Orders</h1>
        <span className="spacer" />
        <button className="btn primary" onClick={onNew}>
          ＋ New PO
        </button>
      </div>
      <p className="po-sub">
        Manual Purchase Order entry and explicit production release.{' '}
        <b>Saving demand never creates production quantity</b> — physical
        quantity enters production only through the explicit{' '}
        <b>Release to production</b> action on a demand line. Select a PO to see
        its demand lines. <b>＋ New PO</b> opens a dialog over this list — the
        URL does not change.
      </p>
      <div className="po-tools">
        <input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search PO Number… (an existing PO Number is opened — duplicates are never created)"
          aria-label="Search PO Number"
        />
      </div>
      {list.length === 0 ? (
        <EmptyState message="No Purchase Orders yet — create the first one with ＋ New PO." />
      ) : (
        <table className="polist">
          <thead>
            <tr>
              <th>PO Number</th>
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
                  No PO matches “{search.trim()}” — check the number, or create
                  it with ＋ New PO
                </td>
              </tr>
            ) : (
              rows.map((p) => (
                <tr key={p.po}>
                  <td>
                    <button className="rowbtn" onClick={() => onOpen(p.po)}>
                      <span className="po" title={p.po}>
                        {p.po}
                      </span>
                      {p.internal ? (
                        <span className="sub" style={{ display: 'block' }}>
                          temporary internal PO
                        </span>
                      ) : null}
                    </button>
                  </td>
                  <td className="mono-sm">{formatIsoDate(p.received)}</td>
                  <td className="mono-sm">
                    <span className={`duetxt ${p.dueClass}`}>
                      {formatIsoDate(p.due)}
                    </span>
                  </td>
                  <td>
                    {p.lines.length}
                    <div className="sub mono-sm">{p.preview}</div>
                  </td>
                  <td>
                    <span className={`postat ${p.status.toLowerCase()}`}>
                      {p.status}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      )}
      <div className="po-note">
        Completed POs (every PO Demand fully allocated) move out of the active
        list but remain permanently available in history. Temporary internal POs
        (<span className="mono">TMP-…-REWORK/MODIFY</span>) appear here like any
        other PO. Purchase Orders handles business demand only — it is not
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
        PO-1010 demand · requested <b>{data?.requested ?? '—'}</b> · nothing is
        created until you confirm — and in Phase 2 confirming changes{' '}
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
