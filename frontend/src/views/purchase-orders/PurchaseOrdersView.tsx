import './purchase-orders.css';

import { useEffect, useRef, useState } from 'react';

import { useConnectivity } from '../../app/connectivity-context';
import { getViewStatePreview } from '../../app/view-state';
import { TypeChip } from '../../components/indicators';
import { useMockNotice } from '../../components/mock-notice';
import { ModalDialog } from '../../components/ModalDialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../components/view-states';
import {
  MOCK_PN_BARCODES,
  MOCK_PO_LIST,
  MOCK_RELEASE_DATA,
} from '../../mocks/purchase-orders';
import type { MockPo, RequestType } from '../../mocks/types';

type Panel =
  { kind: 'list' } | { kind: 'detail'; po: string } | { kind: 'new' };

interface NewPoLine {
  id: number;
  pn: string | null;
  barcodeNote: string;
  isNewPn: boolean;
  type: RequestType;
  qty: string;
  due: string;
  dueTouched: boolean;
  job: string;
  notes: string;
}

// Long-data preview rows (?state=long): many POs plus over-long PO and
// PN identifiers to exercise dense-table and truncation behavior.
const LONG_PREVIEW_POS: MockPo[] = [
  ...Array.from({ length: 20 }, (_, i): MockPo => {
    const n = i + 1;
    return {
      po: `PO-3${String(100 + n)}`,
      received: 'Jul 01, 2026',
      due: 'Sep 30, 2026',
      dueClass: '',
      status: 'Open',
      preview: `PF-LONGRUN-${String(n).padStart(3, '0')}`,
      lines: [],
    };
  }),
  {
    po: 'PO-1099-SUPPLEMENTAL-AMENDMENT-2026-REV-B',
    received: 'Jul 20, 2026',
    due: 'Oct 15, 2026',
    dueClass: '',
    status: 'Open',
    preview: 'PF-MANIFOLD-ASSY-00847-REV-C-EXTENDED-VALIDATION',
    lines: [],
  },
];

// Management sub view for manual PO entry and explicit production release.
// Phase 2: layout and local interactions only — saving and releasing are
// development mocks that change presentation state and never persist.
export function PurchaseOrdersView() {
  const preview = getViewStatePreview();
  const { status } = useConnectivity();
  const writeBlocked = status !== 'connected';
  const { showNotice, noticeElement } = useMockNotice();

  const [panel, setPanel] = useState<Panel>({ kind: 'list' });
  const [poList, setPoList] = useState<MockPo[]>(MOCK_PO_LIST);
  const [search, setSearch] = useState('');
  const [releasedLines, setReleasedLines] = useState<Set<string>>(new Set());
  const [releaseDialog, setReleaseDialog] = useState<{
    po: string;
    pn: string;
  } | null>(null);

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

  const openPo = (po: string) => setPanel({ kind: 'detail', po });

  return (
    <section className="po-view" aria-label="Purchase Orders">
      {panel.kind === 'list' && (
        <PoListPanel
          list={listData}
          search={search}
          onSearch={setSearch}
          onOpen={openPo}
          onNew={() => setPanel({ kind: 'new' })}
        />
      )}
      {panel.kind === 'detail' && (
        <PoDetailPanel
          po={listData.find((p) => p.po === panel.po)}
          releasedLines={releasedLines}
          writeBlocked={writeBlocked}
          onBack={() => setPanel({ kind: 'list' })}
          onRelease={(pn) => setReleaseDialog({ po: panel.po, pn })}
          onSave={() =>
            showNotice(
              '💾 Demand saved (mock) — business demand only. No Quantity Flow, no Movement, no release.',
            )
          }
        />
      )}
      {panel.kind === 'new' && (
        <NewPoPanel
          existing={poList.map((p) => p.po)}
          writeBlocked={writeBlocked}
          onBack={() => setPanel({ kind: 'list' })}
          onOpenExisting={(po) => {
            showNotice(
              `⚠ PO Number ${po} already exists — opening the existing PO instead of duplicating it.`,
            );
            openPo(po);
          }}
          onSave={(po) => {
            setPoList((current) => [po, ...current]);
            showNotice(
              `💾 ${po.po} saved (mock) — business demand only (${po.lines.length} line${po.lines.length > 1 ? 's' : ''}). No release.`,
            );
            setPanel({ kind: 'list' });
          }}
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
        its demand lines.
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
                  <td className="mono-sm">{p.received}</td>
                  <td className="mono-sm">
                    <span className={`duetxt ${p.dueClass}`}>{p.due}</span>
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

function PoDetailPanel({
  po,
  releasedLines,
  writeBlocked,
  onBack,
  onRelease,
  onSave,
}: {
  po: MockPo | undefined;
  releasedLines: Set<string>;
  writeBlocked: boolean;
  onBack: () => void;
  onRelease: (pn: string) => void;
  onSave: () => void;
}) {
  const [unsaved, setUnsaved] = useState(false);
  if (!po) {
    return (
      <div>
        <button className="po-back" onClick={onBack}>
          ‹ All POs
        </button>
        <EmptyState message="This PO is not available in the mock data." />
      </div>
    );
  }
  const editable = po.status === 'Open';
  return (
    <div>
      <div className="po-head">
        <button className="po-back" onClick={onBack}>
          ‹ All POs
        </button>
        <h1 className="mono">{po.po}</h1>
        <span className="spacer" />
      </div>
      <p className="po-sub">
        received <b>{po.received}</b> · PO due date{' '}
        <b className="mono">{po.due}</b> · {po.lines.length} demand line
        {po.lines.length === 1 ? '' : 's'} ·{' '}
        <span className={`postat ${po.status.toLowerCase()}`}>{po.status}</span>
        {po.internal ? ' · temporary internal PO (auditable, unique)' : ''}
      </p>
      <div className="po-card">
        {editable && (
          <div className="pc-head">
            <span className="meta">
              Demand lines — each line's due date defaults to the{' '}
              <b>PO due date</b> (<b className="mono">{po.due}</b>) and may be
              edited per line
            </span>
            <span className="spacer" />
            {unsaved ? (
              <span className="unsaved">● Unsaved changes</span>
            ) : null}
          </div>
        )}
        <div className="po-lines">
          <table className="po-table">
            <thead>
              <tr>
                <th>PN</th>
                <th>Request Type</th>
                <th>Qty</th>
                <th>Due date</th>
                <th>Job Numbers</th>
                <th>Status</th>
                {editable ? <th></th> : null}
              </tr>
            </thead>
            <tbody>
              {po.lines.map((line, i) => {
                const released =
                  line.statusClass === 'released' ||
                  releasedLines.has(`${po.po}:${line.pn}`);
                return (
                  <tr key={`${line.pn}-${i}`}>
                    <td
                      className={
                        line.statusClass === 'invalid' ? 'err-cell' : ''
                      }
                    >
                      <div
                        className="pn"
                        style={line.pn ? undefined : { color: 'var(--faint)' }}
                        title={line.pn || undefined}
                      >
                        {line.pn || '—'}
                      </div>
                      <div
                        className={`bc ${line.barcode.startsWith('new PN') ? 'newpn' : ''}`}
                      >
                        {line.barcode}
                      </div>
                    </td>
                    <td>
                      <TypeChip type={line.type} />
                    </td>
                    <td
                      className={
                        line.statusClass === 'invalid' ? 'err-cell' : ''
                      }
                    >
                      {editable ? (
                        <>
                          <input
                            className="mono"
                            defaultValue={line.qty || ''}
                            size={4}
                            aria-label={`Quantity for ${line.pn || 'new line'}`}
                            onChange={() => setUnsaved(true)}
                          />
                          {line.statusClass === 'invalid' ? (
                            <div className="rowerr">
                              quantity must be &gt; 0
                            </div>
                          ) : null}
                        </>
                      ) : (
                        <span className="mono">{line.qty}</span>
                      )}
                    </td>
                    <td>
                      {editable ? (
                        <input
                          defaultValue={line.due}
                          size={8}
                          aria-label={`Due date for ${line.pn || 'new line'}`}
                          onChange={() => setUnsaved(true)}
                        />
                      ) : (
                        <span className="mono">{line.due}</span>
                      )}
                    </td>
                    <td>
                      {editable ? (
                        <input
                          className="mono"
                          defaultValue={line.job}
                          size={10}
                          aria-label={`Job Numbers for ${line.pn || 'new line'}`}
                          onChange={() => setUnsaved(true)}
                        />
                      ) : (
                        <span className="mono">{line.job}</span>
                      )}
                    </td>
                    <td>
                      <span
                        className={`linestat ${released ? 'released' : line.statusClass}`}
                      >
                        {released && line.statusClass !== 'released'
                          ? 'Released (mock)'
                          : line.status}
                      </span>
                    </td>
                    {editable ? (
                      <td>
                        <button
                          className="rel-btn"
                          disabled={
                            writeBlocked || !line.releasable || released
                          }
                          onClick={() => onRelease(line.pn)}
                        >
                          Release to production…
                        </button>
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {editable && (
          <div className="po-actions">
            <button
              className="btn primary"
              disabled={writeBlocked}
              onClick={() => {
                setUnsaved(false);
                onSave();
              }}
            >
              Save demand
            </button>
            <span className="hint">
              Saving stores <b>business demand only</b> — no Quantity Flow, no
              Movement, no release. Invalid rows cannot be saved. (Development
              mock — nothing is persisted.)
            </span>
          </div>
        )}
      </div>
      <div className="po-note">
        An <b>inactive PN</b> is flagged in lookup and cannot be released
        without reactivation. Long PO line lists scroll with a sticky header.
        Leaving the view with unsaved changes prompts an explicit warning.
      </div>
    </div>
  );
}

function NewPoPanel({
  existing,
  writeBlocked,
  onBack,
  onOpenExisting,
  onSave,
  showNotice,
}: {
  existing: string[];
  writeBlocked: boolean;
  onBack: () => void;
  onOpenExisting: (po: string) => void;
  onSave: (po: MockPo) => void;
  showNotice: (message: string) => void;
}) {
  const [poNumber, setPoNumber] = useState('');
  const [received, setReceived] = useState('Jul 24, 2026');
  const [due, setDue] = useState('');
  const [lines, setLines] = useState<NewPoLine[]>([]);
  const nextId = useRef(1);
  const scanRef = useRef<HTMLInputElement>(null);
  const qtyRefs = useRef(new Map<number, HTMLInputElement>());
  const [focusQtyId, setFocusQtyId] = useState<number | null>(null);

  useEffect(() => {
    if (focusQtyId !== null) {
      const el = qtyRefs.current.get(focusQtyId);
      el?.focus();
      el?.select();
      setFocusQtyId(null);
    }
  }, [focusQtyId, lines]);

  function addLine(pn: string | null, barcodeNote: string, isNewPn: boolean) {
    const id = nextId.current++;
    setLines((current) => [
      ...current,
      {
        id,
        pn,
        barcodeNote,
        isNewPn,
        type: 'NEW',
        qty: '',
        due,
        dueTouched: false,
        job: '',
        notes: '',
      },
    ]);
    return id;
  }

  function handleScan(value: string) {
    const barcode = value.trim().toUpperCase();
    if (!barcode) return;
    const pn = MOCK_PN_BARCODES[barcode];
    if (!pn) {
      showNotice(
        `✕ Unknown barcode “${barcode}” — nothing added. Only PN barcodes (PF:PN:…) add demand lines.`,
      );
      scanRef.current?.focus();
      return;
    }
    const duplicate = lines.find((l) => l.pn === pn);
    if (duplicate) {
      showNotice(
        `⚠ ${pn} is already on this PO — edit its quantity instead of adding a duplicate line.`,
      );
      setFocusQtyId(duplicate.id);
      return;
    }
    const id = addLine(pn, `existing PN · barcode ${barcode}`, false);
    showNotice(
      `✓ ${pn} added — Request Type NEW · due date from PO due date. Type the quantity.`,
    );
    setFocusQtyId(id);
  }

  function updateLine(id: number, patch: Partial<NewPoLine>) {
    setLines((current) =>
      current.map((l) => (l.id === id ? { ...l, ...patch } : l)),
    );
  }

  function handleDueChange(value: string) {
    setDue(value);
    // The PO due date is the default — update lines still holding it.
    setLines((current) =>
      current.map((l) => (l.dueTouched ? l : { ...l, due: value })),
    );
  }

  function handleSave() {
    const number = poNumber.trim().toUpperCase();
    if (!number) {
      showNotice('✕ PO Number is required');
      return;
    }
    if (existing.includes(number)) {
      onOpenExisting(number);
      return;
    }
    const rows = lines.filter((l) => l.pn);
    if (!rows.length) {
      showNotice('✕ Add at least one demand line (scan a PN barcode)');
      scanRef.current?.focus();
      return;
    }
    for (const row of rows) {
      const qty = parseInt(row.qty || '0', 10);
      if (!qty || qty < 1) {
        showNotice(
          `✕ ${row.pn}: quantity must be > 0 — invalid rows cannot be saved`,
        );
        setFocusQtyId(row.id);
        return;
      }
    }
    onSave({
      po: number,
      received,
      due: due || '—',
      dueClass: '',
      status: 'Open',
      preview:
        rows
          .slice(0, 2)
          .map((r) => r.pn)
          .join(' · ') + (rows.length > 2 ? ` · ${rows.length - 2} more` : ''),
      lines: rows.map((r) => ({
        pn: r.pn ?? '—',
        barcode: 'barcode PF:PN:…',
        type: r.type,
        qty: parseInt(r.qty, 10),
        due: r.due || '—',
        job: r.job || '—',
        status: 'Saved',
        statusClass: 'saved',
      })),
    });
  }

  return (
    <div>
      <div className="po-head">
        <button className="po-back" onClick={onBack}>
          ‹ All POs
        </button>
        <h1>New PO</h1>
        <span className="spacer" />
      </div>
      <p className="po-sub">
        Enter the PO header, then <b>scan each part's PN barcode</b> and type
        its quantity — or add lines manually for a PN that does not exist yet.
        Every line defaults to Request Type <TypeChip type="NEW" /> and to the{' '}
        <b>PO due date</b>; both can be changed per line. Entering an existing
        PO Number opens that PO instead of duplicating it.
      </p>

      <div className="po-card">
        <div className="np-form">
          <label htmlFor="np-num">PO Number</label>
          <input
            id="np-num"
            className="mono"
            placeholder="PO-____"
            value={poNumber}
            onChange={(e) => setPoNumber(e.target.value)}
          />
          <label htmlFor="np-recv">Received date</label>
          <input
            id="np-recv"
            className="mono"
            value={received}
            onChange={(e) => setReceived(e.target.value)}
          />
          <label htmlFor="np-due">PO due date</label>
          <input
            id="np-due"
            className="mono"
            placeholder="e.g. Aug 30, 2026"
            value={due}
            onChange={(e) => handleDueChange(e.target.value)}
          />
          <span
            className="hint"
            style={{ fontSize: 12, color: 'var(--faint)' }}
          >
            default due date for every demand line
          </span>
        </div>
      </div>

      <div className="np-scanrow">
        <input
          ref={scanRef}
          className="np-scan"
          placeholder="Scan PN barcode (PF:PN:…) — Enter"
          aria-label="Scan PN barcode"
          autoComplete="off"
          disabled={writeBlocked}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              handleScan(e.currentTarget.value);
              e.currentTarget.value = '';
            }
          }}
        />
        <button
          className="btn ghost"
          disabled={writeBlocked}
          onClick={() =>
            addLine(
              null,
              'PN lookup — an unknown PN is created inline with its barcode',
              false,
            )
          }
        >
          ＋ Add line manually
        </button>
      </div>
      <div className="np-hint">
        Scan → the line is added and its <b>Qty</b> field gets focus → type the
        quantity → Enter returns focus to the scan input, ready for the next
        part. Demo barcodes: <code>PF:PN:1014</code> · <code>PF:PN:1021</code> ·{' '}
        <code>PF:PN:1102</code>. Scanning a PN already on this PO focuses its
        existing line instead of adding a duplicate. Unknown barcodes are
        rejected — nothing is added.
      </div>

      <div className="po-card">
        <div className="po-lines">
          <table className="po-table">
            <thead>
              <tr>
                <th>PN</th>
                <th>Request Type</th>
                <th>Qty</th>
                <th>Due date</th>
                <th>Job Numbers</th>
                <th>Notes</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {lines.length === 0 ? (
                <tr>
                  <td colSpan={7} className="np-empty">
                    No demand lines yet — scan the first PN barcode above
                  </td>
                </tr>
              ) : (
                lines.map((line) => (
                  <tr key={line.id}>
                    <td>
                      {line.pn ? (
                        <div className="pn" title={line.pn}>
                          {line.pn}
                        </div>
                      ) : (
                        <input
                          placeholder="type PN — lookup or create"
                          size={16}
                          aria-label="PartNumber lookup or create"
                          onBlur={(e) => {
                            const pn = e.target.value.trim().toUpperCase();
                            if (pn) {
                              updateLine(line.id, {
                                pn,
                                barcodeNote:
                                  'new PN — barcode created with PN master: PF:PN:…',
                                isNewPn: true,
                              });
                              setFocusQtyId(line.id);
                            }
                          }}
                        />
                      )}
                      <div className={`bc ${line.isNewPn ? 'newpn' : ''}`}>
                        {line.barcodeNote}
                      </div>
                    </td>
                    <td>
                      <select
                        value={line.type}
                        aria-label={`Request Type for ${line.pn ?? 'new line'}`}
                        onChange={(e) =>
                          updateLine(line.id, {
                            type: e.target.value as RequestType,
                          })
                        }
                      >
                        <option>NEW</option>
                        <option>REWORK</option>
                        <option>MODIFY</option>
                      </select>
                    </td>
                    <td>
                      <input
                        ref={(el) => {
                          if (el) qtyRefs.current.set(line.id, el);
                          else qtyRefs.current.delete(line.id);
                        }}
                        className="mono"
                        size={4}
                        inputMode="numeric"
                        placeholder="qty"
                        value={line.qty}
                        aria-label={`Quantity for ${line.pn ?? 'new line'}`}
                        onChange={(e) =>
                          updateLine(line.id, { qty: e.target.value })
                        }
                        onKeyDown={(e) => {
                          if (e.key !== 'Enter') return;
                          const qty = parseInt(
                            e.currentTarget.value || '0',
                            10,
                          );
                          if (!qty || qty < 1) {
                            showNotice('✕ Quantity must be > 0');
                            e.currentTarget.select();
                            return;
                          }
                          scanRef.current?.focus();
                        }}
                      />
                    </td>
                    <td>
                      <input
                        className="mono"
                        size={12}
                        placeholder="due…"
                        value={line.due}
                        aria-label={`Due date for ${line.pn ?? 'new line'}`}
                        onChange={(e) =>
                          updateLine(line.id, {
                            due: e.target.value,
                            dueTouched: true,
                          })
                        }
                      />
                    </td>
                    <td>
                      <input
                        className="mono"
                        size={10}
                        placeholder="job #…"
                        value={line.job}
                        aria-label={`Job Numbers for ${line.pn ?? 'new line'}`}
                        onChange={(e) =>
                          updateLine(line.id, { job: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <input
                        size={10}
                        placeholder="notes…"
                        value={line.notes}
                        aria-label={`Notes for ${line.pn ?? 'new line'}`}
                        onChange={(e) =>
                          updateLine(line.id, { notes: e.target.value })
                        }
                      />
                    </td>
                    <td>
                      <button
                        className="pr-x"
                        title="Remove line"
                        aria-label={`Remove line ${line.pn ?? ''}`}
                        onClick={() =>
                          setLines((current) =>
                            current.filter((l) => l.id !== line.id),
                          )
                        }
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <div className="po-actions">
          <button
            className="btn primary"
            disabled={writeBlocked}
            onClick={handleSave}
          >
            Save demand
          </button>
          <span className="hint">
            Saving stores <b>business demand only</b> — no Quantity Flow, no
            Movement, no release. (Development mock — nothing is persisted.)
          </span>
        </div>
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
      wide
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
