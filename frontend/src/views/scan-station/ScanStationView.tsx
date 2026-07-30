import './scan-station.css';

import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';

import { useConnectivity } from '../../app/connectivity-context';
import { useRouter } from '../../app/router-context';
import { getViewStatePreview } from '../../app/view-state';
import {
  AreaMachineLayout,
  AreaSummaryCard,
  MachineMonitoringCard,
} from '../../components/area-monitoring';
import { AreaDot } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { applyQuantityKey } from '../../components/quantity-input';
import { QuantityKeypad } from '../../components/QuantityKeypad';
import { LoadingState } from '../../components/view-states';
import {
  MOCK_AREA_CARDS,
  MOCK_AREA_CARDS_LONG,
  MOCK_AREA_MACHINES,
} from '../../mocks/area-board';
import { areaByKey } from '../../mocks/areas';
import {
  MOCK_MACHINE_BARCODES,
  MOCK_REPAIR_SOURCES,
  MOCK_SCAN_STATIONS,
  MOCK_WORKER,
  MOCK_WORKER_BARCODES,
  stationById,
} from '../../mocks/scan-station';
import { catalogPartNumber } from '../../mocks/work-orders';
import { splitAssignments } from '../area-monitoring';
import type { AreaAssignment } from '../area-monitoring';
import type {
  MockAreaCard,
  MockCompletedAction,
  MockScanStation,
  RequestType,
  RouteMode,
} from '../view-models';
import { parseScan, pnKey, SCRAP_BARCODE } from './barcode';

/**
 * One floating scan notification. Success/info notices auto-dismiss
 * after ~4 s, warnings and errors after ~8 s; a new notice replaces
 * the previous one and restarts the timer. The persistent OFFLINE
 * application banner is NOT a notice — it stays until reconnection.
 */
type Notice = {
  kind: 'ok' | 'warn' | 'err' | 'info';
  icon?: string;
  title: string;
  detail?: string;
};

const NOTICE_OK_MS = 4000;
const NOTICE_WARN_MS = 8000;

// Mock-only "clock" for newly recorded actions — deterministic on purpose.
const MOCK_SCAN_TIME = '14:32';

/** A transferable source position of a PN outside the station's Area. */
interface SourceOption {
  areaLabel: string;
  qty: number;
  card: MockAreaCard;
}

/** One-shot dialog flows — no persistent context survives a dialog. */
type Flow =
  | {
      kind: 'machine-assign';
      machine: string | null;
      pn: string | null;
      /** True when opened from the PN action dialog (enables Back). */
      fromActions?: boolean;
    }
  | { kind: 'pn-actions'; pn: string }
  | {
      kind: 'transfer';
      pn: string;
      source: SourceOption;
    }
  | { kind: 'source-select'; pn: string; sources: SourceOption[] }
  | { kind: 'intake'; pn: string }
  | { kind: 'add-qty'; pn: string }
  | { kind: 'repair'; pn: string }
  | { kind: 'scrap'; pn: string }
  | { kind: 'queue-return'; pn: string; machine: string; max: number }
  | { kind: 'undo' }
  | { kind: 'manual-pn' };

/**
 * Scan Station routing: `/scan-station` shows the Station Selector
 * (never auto-redirecting to a station); `/scan-station/:stationId`
 * loads the station; an unknown or inactive Station ID shows an
 * explicit error and never silently falls back to another station.
 */
export function ScanStationView() {
  const { route } = useRouter();
  const stationId = route.view === 'scan-station' ? route.stationId : null;
  if (stationId === null) return <StationSelector />;
  const station = stationById(stationId);
  if (!station) return <UnknownStation stationId={stationId} />;
  return <StationView key={station.stationId} station={station} />;
}

function StationSelector() {
  const { navigate } = useRouter();
  return (
    <section className="ss" aria-label="Scan Station">
      <div className="ss-select">
        <h1>Select a Scan Station</h1>
        <p className="ss-select-sub">
          Each Scan Station is bound to one Area. Choose the station you are
          working at — the station keeps its own URL (
          <span className="mono">/scan-station/&lt;station-id&gt;</span>
          ).
        </p>
        <ul className="ss-stationlist">
          {MOCK_SCAN_STATIONS.filter((s) => s.active).map((s) => {
            const area = areaByKey(s.area);
            const machines = MOCK_AREA_MACHINES[s.area] ?? [];
            return (
              <li key={s.stationId}>
                <button
                  className="ss-stationbtn"
                  onClick={() => navigate(`/scan-station/${s.stationId}`)}
                >
                  <span className="sid mono">{s.stationId}</span>
                  <span className="smeta">
                    {s.department} ·{' '}
                    <AreaDot
                      colorVar={area?.colorVar ?? 'var(--faint)'}
                      size={10}
                    />{' '}
                    {area?.name} · Operations:{' '}
                    {area?.operations.join(', ') ?? '—'}
                  </span>
                  <span className="stype">
                    {machines.length > 0
                      ? `${machines.length} Machines — queue & assign`
                      : 'No Machines — direct Area processing'}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

function UnknownStation({ stationId }: { stationId: string }) {
  const { navigate } = useRouter();
  return (
    <section className="ss" aria-label="Scan Station">
      <div className="ss-select">
        <div className="ss-feedback err" role="alert">
          <div className="fic" aria-hidden="true">
            ✕
          </div>
          <div>
            <div className="t1">
              Unknown or inactive Scan Station “{stationId}”
            </div>
            <div className="t2">
              This Station ID is not an active Scan Station. Nothing is loaded —
              a station is never silently substituted.
            </div>
          </div>
        </div>
        <button
          className="bigbtn primary"
          onClick={() => navigate('/scan-station')}
        >
          Choose a Scan Station
        </button>
      </div>
    </section>
  );
}

function StationView({ station }: { station: MockScanStation }) {
  const preview = getViewStatePreview();
  const { navigate } = useRouter();
  const { status } = useConnectivity();
  const disconnected = status === 'unavailable';
  const writeBlocked = status !== 'connected';

  const area = areaByKey(station.area);
  const machines = MOCK_AREA_MACHINES[station.area] ?? [];
  const hasMachines = machines.length > 0;

  const inputRef = useRef<HTMLInputElement>(null);
  const [worker, setWorker] = useState(MOCK_WORKER.name);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  // Completed PN operations, newest first; reversed entries stay for
  // audit display but are no longer Undo-eligible.
  const [history, setHistory] = useState<
    { action: MockCompletedAction; reversed: boolean }[]
  >([]);
  // PNs created on first valid use in this session (mock): identity is
  // case-insensitive, the first-entered casing is preserved for display.
  const [createdPns, setCreatedPns] = useState<Map<string, string>>(
    () => new Map(),
  );

  // Auto-dismiss the floating notification: ~4 s for success/info,
  // ~8 s for warnings and errors. A replacing notice restarts the
  // timer; the cleanup clears it on replacement and unmount.
  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(
      () => setNotice(null),
      notice.kind === 'warn' || notice.kind === 'err'
        ? NOTICE_WARN_MS
        : NOTICE_OK_MS,
    );
    return () => window.clearTimeout(timeout);
  }, [notice]);

  const focusScan = useCallback(() => {
    // §3.1 focus discipline: the barcode input regains focus after every
    // completed operation and dialog close.
    setTimeout(() => inputRef.current?.focus(), 30);
  }, []);

  useEffect(() => {
    if (!writeBlocked) focusScan();
  }, [writeBlocked, focusScan]);

  const areaCards = useMemo(() => {
    const all = preview === 'long' ? MOCK_AREA_CARDS_LONG : MOCK_AREA_CARDS;
    return preview === 'empty'
      ? []
      : all.filter((c) => c.area === station.area);
  }, [preview, station.area]);
  const { assigned } = splitAssignments(areaCards);

  const eligible = history.find((h) => !h.reversed);
  const lastPn = eligible?.action ?? null;

  /** Resolve the display PN: catalog / session-created, else as scanned. */
  const resolvePn = useCallback(
    (pn: string) =>
      catalogPartNumber(pn)?.pn ?? createdPns.get(pnKey(pn)) ?? pn.trim(),
    [createdPns],
  );

  const cardsFor = useCallback(
    (pn: string) => areaCards.filter((c) => pnKey(c.pn) === pnKey(pn)),
    [areaCards],
  );

  const sourcesFor = useCallback(
    (pn: string): SourceOption[] =>
      MOCK_AREA_CARDS.filter(
        (c) =>
          pnKey(c.pn) === pnKey(pn) &&
          c.area !== station.area &&
          c.area !== 'stockroom',
      ).map((card) => ({
        areaLabel: areaByKey(card.area)?.name ?? card.area,
        qty: card.qty,
        card,
      })),
    [station.area],
  );

  const queuedQtyFor = useCallback(
    (pn: string) => {
      const { queued } = splitAssignments(cardsFor(pn));
      return queued.reduce((s, e) => s + e.qty, 0);
    },
    [cardsFor],
  );

  const repairSourcesFor = useCallback((pn: string) => {
    const key = Object.keys(MOCK_REPAIR_SOURCES).find(
      (k) => pnKey(k) === pnKey(pn),
    );
    return key ? MOCK_REPAIR_SOURCES[key] : [];
  }, []);

  const record = useCallback((action: MockCompletedAction) => {
    setHistory((current) => [{ action, reversed: false }, ...current]);
  }, []);

  const blockedNotice = useCallback(() => {
    setNotice({
      kind: 'err',
      icon: '✕',
      title: 'Disconnected — production writes are blocked',
      detail:
        'No scan is recorded or queued while offline. Restore the connection to resume scanning.',
    });
  }, []);

  const closeFlow = useCallback(
    (message?: string) => {
      setFlow(null);
      if (message) {
        setNotice({ kind: 'info', title: message });
      }
      focusScan();
    },
    [focusScan],
  );

  const cancelFlow = useCallback(() => {
    // Cancel always means no write; the temporary context is cleared
    // and never replaces the Last Scanned PN.
    closeFlow('Cancelled — nothing recorded.');
  }, [closeFlow]);

  /** Route a resolved PN to the applicable one-shot dialog. */
  const openPnFlow = useCallback(
    (rawPn: string) => {
      const pn = resolvePn(rawPn);
      if (cardsFor(pn).length > 0) {
        setFlow({ kind: 'pn-actions', pn });
        return;
      }
      const sources = sourcesFor(pn);
      if (sources.length === 1) {
        setFlow({ kind: 'transfer', pn, source: sources[0] });
        return;
      }
      if (sources.length > 1) {
        setFlow({ kind: 'source-select', pn, sources });
        return;
      }
      // No active WO Demand and no active quantity: intake flow
      // (equivalent to Work Orders "Add Part") — MODIFY + FLOATING
      // defaults, both editable. The PN is created on first valid use.
      setFlow({ kind: 'intake', pn });
    },
    [cardsFor, resolvePn, sourcesFor],
  );

  function handleScan() {
    const input = inputRef.current;
    if (!input) return;
    if (writeBlocked) {
      blockedNotice();
      return;
    }
    const raw = input.value;
    input.value = '';
    focusScan();
    const parsed = parseScan(raw);
    switch (parsed.kind) {
      case 'empty':
        return;
      case 'scrap':
        setNotice({
          kind: 'err',
          icon: '✕',
          title: `${SCRAP_BARCODE} is accepted only inside the Scrap workflow`,
          detail:
            'Open “Scrap damaged quantity” from a PN first — the scrap barcode is context-sensitive. Nothing recorded.',
        });
        return;
      case 'worker': {
        const name = MOCK_WORKER_BARCODES[parsed.id];
        if (!name) {
          setNotice({
            kind: 'err',
            icon: '✕',
            title: 'Unknown Worker barcode',
            detail: 'Rejected — nothing recorded (mock).',
          });
          return;
        }
        setWorker(name);
        // A Worker scan never replaces the Last Scanned PN.
        setNotice({
          kind: 'ok',
          icon: '✓',
          title: `Worker session: ${name}`,
          detail:
            'Previous Worker signed out. Worker identity is accountability metadata — it never determines business correctness.',
        });
        return;
      }
      case 'machine': {
        const machine = MOCK_MACHINE_BARCODES[parsed.id];
        if (!machine) {
          setNotice({
            kind: 'err',
            icon: '✕',
            title: 'Unknown Machine barcode',
            detail: 'Rejected — nothing recorded (mock).',
          });
          return;
        }
        if (machine.area !== station.area) {
          setNotice({
            kind: 'err',
            icon: '✕',
            title: `${machine.machine} belongs to another Area`,
            detail:
              'Invalid Area/Machine combination — rejected, nothing recorded.',
          });
          return;
        }
        const status = machines.find((m) => m.name === machine.machine)?.status;
        if (status === 'maintenance') {
          setNotice({
            kind: 'err',
            icon: '✕',
            title: `${machine.machine} is inactive (maintenance)`,
            detail: 'Inactive entities must not accept production updates.',
          });
          return;
        }
        // One-shot shortcut only: opens the Machine assignment dialog
        // with the Machine preselected. There is NO Machine Session.
        setFlow({
          kind: 'machine-assign',
          machine: machine.machine,
          pn: null,
        });
        return;
      }
      case 'area':
        setNotice({
          kind: 'err',
          icon: '✕',
          title: 'Area barcodes are not used at a Scan Station',
          detail: 'This station is bound to one Area. Nothing recorded.',
        });
        return;
      case 'pn':
        openPnFlow(parsed.pn);
        return;
      case 'unknown':
        setNotice({
          kind: 'err',
          icon: '✕',
          title: 'Unrecognized barcode',
          detail:
            'Only PF:-prefixed PartFlow barcodes are accepted — unrelated factory/vendor barcodes and raw PN text are rejected. Nothing recorded.',
        });
        return;
    }
  }

  function undoTarget(): MockCompletedAction | null {
    return eligible?.action ?? null;
  }

  function confirmUndo() {
    const target = undoTarget();
    if (!target) return;
    setHistory((current) => {
      const index = current.findIndex((h) => !h.reversed);
      if (index < 0) return current;
      return current.map((h, i) =>
        i === index ? { ...h, reversed: true } : h,
      );
    });
    setFlow(null);
    setNotice({
      kind: 'warn',
      icon: '⟲',
      title: `Undo recorded as REVERSED — ${target.pn}`,
      detail: `${target.reversalEffect} The original ${target.movementType} Movement is preserved; a compensating event references it (mock presentation).`,
    });
    focusScan();
  }

  if (preview === 'loading') {
    return (
      <section className="ss" aria-label="Scan Station">
        <LoadingState label="Loading Scan Station" />
        <div className="ss-scanwrap">
          <div className="ss-scanrow">
            <input
              className="ss-scaninput"
              disabled
              placeholder="Connecting…"
              aria-label="Scan barcode"
            />
          </div>
        </div>
      </section>
    );
  }

  const shownNotice: Notice | null =
    preview === 'error'
      ? {
          kind: 'err',
          icon: '✕',
          title: 'Unrecognized barcode',
          detail:
            'Rejected — unknown or invalid scans never update tracking data. Nothing recorded.',
        }
      : notice;

  // Row actions: `In this Area now` carries NO row actions (assignment
  // stays available through PN scan, Machine scan and the action
  // dialog); Machine cards carry only the one-shot QUEUE return.
  const machineRowAction = (entry: AreaAssignment) => (
    <button
      className="rowact"
      disabled={writeBlocked}
      onClick={() =>
        setFlow({
          kind: 'queue-return',
          pn: entry.card.pn,
          machine: entry.context,
          max: entry.qty,
        })
      }
    >
      QUEUE
    </button>
  );

  return (
    <section className="ss" aria-label="Scan Station">
      <header className="ss-head">
        <div>
          <div className="dept">{station.department}</div>
          <div className="area">
            <AreaDot colorVar={area?.colorVar ?? 'var(--faint)'} size={16} />
            {area?.name}
          </div>
          <div className="op">
            Operations: <b>{area?.operations.join(', ')}</b>
          </div>
        </div>
        <span className="spacer" />
        <div className="ss-stats" aria-label="Area statistics">
          {(hasMachines
            ? [
                { label: 'Total PNs', value: areaCards.length },
                {
                  label: 'Total pcs',
                  value: areaCards.reduce((s, c) => s + c.qty, 0),
                },
                {
                  label: 'Queued',
                  value: areaCards.reduce(
                    (s, c) =>
                      s +
                      c.machines
                        .filter(([m]) => m === 'queue')
                        .reduce((x, [, q]) => x + q, 0),
                    0,
                  ),
                },
                {
                  label: 'On machines',
                  value: assigned.reduce((s, e) => s + e.qty, 0),
                },
                {
                  label: 'Hot',
                  value:
                    areaCards.filter((c) => c.hotRank !== undefined).length ||
                    '—',
                },
              ]
            : [
                { label: 'Total PNs', value: areaCards.length },
                {
                  label: 'Total pcs',
                  value: areaCards.reduce((s, c) => s + c.qty, 0),
                },
                {
                  label: 'Processing',
                  value: areaCards.reduce((s, c) => s + c.qty, 0),
                },
                {
                  label: 'Hot',
                  value:
                    areaCards.filter((c) => c.hotRank !== undefined).length ||
                    '—',
                },
              ]
          ).map((s) => (
            <div className="stat" key={s.label}>
              <div className="n">{s.value}</div>
              <div className="l">{s.label}</div>
            </div>
          ))}
        </div>
        <div className="ss-pill">
          <span className="lbl">Worker session</span>
          <span className="val">
            <span className="sdot" aria-hidden="true" />
            {worker}
          </span>
          <span className="sub">{MOCK_WORKER.note}</span>
        </div>
      </header>

      <div className="ss-body">
        <div className="panel">
          <div className="ph">
            Scan barcode
            <span className="spacer" />
            <span className="note">
              PN and Worker barcodes; a Machine barcode is a one-shot assignment
              shortcut — nothing stays armed
            </span>
          </div>
          <div className="ss-scanwrap">
            <div className="ss-scanrow">
              <input
                ref={inputRef}
                className="ss-scaninput"
                autoComplete="off"
                disabled={writeBlocked}
                placeholder={
                  disconnected
                    ? 'Disconnected — scanning blocked'
                    : status === 'connecting'
                      ? 'Connecting…'
                      : 'Scan PN / Worker / Machine barcode… (ENTER)'
                }
                aria-label="Scan barcode"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleScan();
                }}
              />
              <button
                className="ss-manualbtn"
                onClick={() => setFlow({ kind: 'manual-pn' })}
                disabled={writeBlocked}
              >
                ⌨ Enter PN manually
              </button>
            </div>
            <div className="ss-hint">
              Demo barcodes — <code>PF:PN:&lt;part-number&gt;</code> (e.g.{' '}
              <code>PF:PN:2027-60-8114-00</code> in this Area ·{' '}
              <code>PF:PN:118-052</code> elsewhere ·{' '}
              <code>PF:PN:NEW-PART-01</code> unknown → intake) ·{' '}
              <code>PF:MACHINE:L2</code> one-shot assign ·{' '}
              <code>PF:WORKER:88</code> worker
            </div>
            <div className="ss-manualcap">
              Manual PN entry is the fallback when the scanner is unavailable —
              any non-empty PN value is validated exactly like a scan; raw PN
              text is never treated as barcode input.
            </div>
            <div className="ss-lastpn">
              <span className="l">Last scanned PN</span>
              <span className="p">{lastPn?.pn ?? '—'}</span>
              <span className="d">
                {lastPn
                  ? `${lastPn.movementType} · ${lastPn.description}`
                  : 'no completed PN operations yet'}
              </span>
              <button
                className="ss-undo"
                disabled={writeBlocked || !eligible}
                onClick={() => setFlow({ kind: 'undo' })}
              >
                ⟲ UNDO
              </button>
            </div>
          </div>
        </div>

        <AreaMachineLayout
          summary={
            area ? (
              <AreaSummaryCard
                area={area}
                cards={areaCards}
                machines={machines}
                title="In this Area now"
              />
            ) : null
          }
          machineCards={machines.map((machine) => (
            <MachineMonitoringCard
              key={machine.name}
              machine={machine}
              entries={assigned.filter((e) => e.context === machine.name)}
              rowAction={machineRowAction}
            />
          ))}
        />
      </div>

      {shownNotice ? (
        <FloatingNotice notice={shownNotice} onClose={() => setNotice(null)} />
      ) : null}

      {flow?.kind === 'machine-assign' && (
        <MachineAssignDialog
          station={station}
          initialMachine={flow.machine}
          initialPn={flow.pn}
          queuedQtyFor={queuedQtyFor}
          resolvePn={resolvePn}
          areaCards={areaCards}
          worker={worker}
          onBack={
            flow.fromActions && flow.pn
              ? () => setFlow({ kind: 'pn-actions', pn: flow.pn! })
              : undefined
          }
          onRecord={record}
          onNotice={setNotice}
          onClose={closeFlow}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'pn-actions' && (
        <PnActionsDialog
          pn={flow.pn}
          station={station}
          hasMachines={hasMachines}
          queuedQty={queuedQtyFor(flow.pn)}
          sources={sourcesFor(flow.pn)}
          repairSources={repairSourcesFor(flow.pn)}
          inAreaQty={cardsFor(flow.pn).reduce((s, c) => s + c.qty, 0)}
          onPick={(next) => setFlow(next)}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'source-select' && (
        <SourceSelectDialog
          pn={flow.pn}
          sources={flow.sources}
          onPick={(source) =>
            setFlow({ kind: 'transfer', pn: flow.pn, source })
          }
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'transfer' && (
        <TransferDialog
          station={station}
          pn={flow.pn}
          source={flow.source}
          hasMachines={hasMachines}
          worker={worker}
          onRecord={record}
          onNotice={setNotice}
          onClose={closeFlow}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'intake' && (
        <IntakeDialog
          station={station}
          pn={flow.pn}
          hasMachines={hasMachines}
          worker={worker}
          onCreatePn={(pn) =>
            setCreatedPns((current) => {
              if (current.has(pnKey(pn)) || catalogPartNumber(pn)) {
                return current;
              }
              const next = new Map(current);
              next.set(pnKey(pn), pn);
              return next;
            })
          }
          onRecord={record}
          onNotice={setNotice}
          onClose={closeFlow}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'add-qty' && (
        <AddQuantityDialog
          station={station}
          pn={flow.pn}
          hasMachines={hasMachines}
          worker={worker}
          onRecord={record}
          onNotice={setNotice}
          onClose={closeFlow}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'repair' && (
        <RepairDialog
          station={station}
          pn={flow.pn}
          sources={repairSourcesFor(flow.pn)}
          worker={worker}
          onRecord={record}
          onNotice={setNotice}
          onClose={closeFlow}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'scrap' && (
        <ScrapDialog
          station={station}
          pn={flow.pn}
          available={cardsFor(flow.pn).reduce((s, c) => s + c.qty, 0)}
          worker={worker}
          onRecord={record}
          onNotice={setNotice}
          onClose={closeFlow}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'queue-return' && (
        <QueueReturnDialog
          station={station}
          pn={flow.pn}
          machine={flow.machine}
          max={flow.max}
          worker={worker}
          onRecord={record}
          onNotice={setNotice}
          onClose={closeFlow}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'undo' && undoTarget() && (
        <UndoConfirmDialog
          target={undoTarget()!}
          onConfirm={confirmUndo}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'manual-pn' && (
        <ManualEntryDialog
          onCancel={cancelFlow}
          onConfirm={(pnText) => {
            const pn = pnText.trim();
            setFlow(null);
            if (!pn) {
              focusScan();
              return;
            }
            openPnFlow(pn);
          }}
        />
      )}

      {/* Faint diagnostic caption: subtly clickable to switch stations —
          deliberately unobtrusive, not a normal operator workflow. */}
      <footer className="ss-stationfoot">
        Station{' '}
        <button
          className="ss-stationswitch mono"
          title="Switch Scan Station"
          onClick={() => navigate('/scan-station')}
        >
          {station.stationId}
        </button>
      </footer>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Floating notification                                               */
/* ------------------------------------------------------------------ */

/**
 * The single floating scan notification. It never reserves layout
 * space, shows only the most recent notice, carries an explicit close
 * button, and stays clear of the barcode input (bottom edge) and of
 * dialog actions (it renders beneath the modal overlay).
 */
function FloatingNotice({
  notice,
  onClose,
}: {
  notice: Notice;
  onClose: () => void;
}) {
  const alerting = notice.kind === 'warn' || notice.kind === 'err';
  return (
    <div
      className={`ss-toast ${notice.kind}`}
      role={alerting ? 'alert' : 'status'}
    >
      {notice.icon ? (
        <div className="fic" aria-hidden="true">
          {notice.icon}
        </div>
      ) : null}
      <div className="tx">
        <div className="t1">{notice.title}</div>
        {notice.detail ? <div className="t2">{notice.detail}</div> : null}
      </div>
      <button
        type="button"
        className="tclose"
        aria-label="Dismiss notification"
        onClick={onClose}
      >
        ×
      </button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Wizard building blocks                                              */
/* ------------------------------------------------------------------ */

interface ActionDialogProps {
  station: MockScanStation;
  worker: string;
  onRecord: (action: MockCompletedAction) => void;
  onNotice: (n: Notice) => void;
  onClose: (message?: string) => void;
  onCancel: () => void;
}

/**
 * Structured confirmation summary — the dedicated final view of every
 * production wizard. Two columns (term / value); rows without a value
 * are omitted. Never a single interpolated sentence.
 */
function ConfirmationSummary({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="ss-confirm">
      {rows
        .filter(([, value]) => value !== null && value !== undefined)
        .map(([label, value]) => (
          <Fragment key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </Fragment>
        ))}
    </dl>
  );
}

/** Semantic quantity/step guidance directly above the related input. */
function Guidance({
  tone = 'neutral',
  children,
}: {
  tone?: 'neutral' | 'info' | 'warn' | 'error';
  children: ReactNode;
}) {
  return <div className={`ss-guide ${tone}`}>{children}</div>;
}

/** Concise recap of the selections carried into the current step. */
function StepRecap({ lines }: { lines: ReactNode[] }) {
  return (
    <div className="ss-recap">
      {lines.filter(Boolean).map((line, index) => (
        <div key={index} className="ss-recapline">
          {line}
        </div>
      ))}
    </div>
  );
}

/**
 * Wizard navigation row: Back only when a meaningful previous view
 * exists, Cancel (Esc) always abandons the whole one-shot workflow
 * with no write, and the primary button names the actual operation.
 */
function StepButtons({
  onBack,
  onCancel,
  cancelLabel = 'Cancel (Esc) — nothing recorded',
  primary,
}: {
  onBack?: () => void;
  onCancel: () => void;
  cancelLabel?: string;
  primary: {
    label: string;
    onClick: () => void;
    disabled?: boolean;
    danger?: boolean;
    autoFocus?: boolean;
  };
}) {
  const primaryRef = useRef<HTMLButtonElement>(null);
  const { autoFocus } = primary;
  useEffect(() => {
    if (autoFocus) primaryRef.current?.focus();
  }, [autoFocus]);
  return (
    <div className="row">
      {onBack ? (
        <button className="bigbtn ghost ss-back" onClick={onBack}>
          ‹ Back
        </button>
      ) : null}
      <button className="bigbtn ghost" onClick={onCancel}>
        {cancelLabel}
      </button>
      <button
        ref={primaryRef}
        className={`bigbtn ${primary.danger ? 'danger' : 'primary'}`}
        disabled={primary.disabled}
        onClick={primary.onClick}
      >
        {primary.label}
      </button>
    </div>
  );
}

/**
 * Central physical-key handling for quantity steps: 0–9 append,
 * Backspace removes, Delete clears, Enter advances (to the next step —
 * never directly to a write), Escape cancels (ModalDialog), Space is
 * ignored. Keys typed into other text fields (reason, notes,
 * scan-within-dialog) are left alone.
 */
function quantityKeyHandler(
  value: string,
  onChange: (next: string) => void,
  onAdvance: () => void,
) {
  return (event: React.KeyboardEvent) => {
    const target = event.target;
    // Focusable dialog buttons (Cancel, Back, selection buttons) keep
    // their native keyboard activation. Virtual keypad buttons are
    // type="button", non-focusable (tabIndex -1) and never take focus
    // on click, so a previously clicked keypad button can never
    // reclaim Enter or Space.
    if (target instanceof HTMLButtonElement) return;
    if (event.key === 'Enter') {
      // Enter always means "advance" for the quantity step.
      event.preventDefault();
      onAdvance();
      return;
    }
    const inOtherField =
      (target instanceof HTMLInputElement &&
        !target.classList.contains('qtydisplay')) ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement;
    if (inOtherField) return;
    if (event.key === ' ') {
      event.preventDefault(); // Space is ignored
      return;
    }
    const next = applyQuantityKey(value, event.key);
    if (next !== null) {
      event.preventDefault();
      onChange(next);
    }
  };
}

/**
 * Enter handling for non-quantity steps (settings/confirmation):
 * Enter performs the given action unless focus sits on a button or in
 * a text-entry control (those keep their native behavior).
 */
function enterKeyHandler(onEnter: () => void) {
  return (event: React.KeyboardEvent) => {
    const target = event.target;
    if (
      target instanceof HTMLButtonElement ||
      target instanceof HTMLInputElement ||
      target instanceof HTMLSelectElement ||
      target instanceof HTMLTextAreaElement
    ) {
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      onEnter();
    }
  };
}

/** Standard quantity validation message for a 1..max range. */
function quantityValidation(
  parsed: number,
  max: number,
  overMessage: string,
): { tone: 'neutral' | 'error'; text: string } | null {
  if (parsed > max) return { tone: 'error', text: overMessage };
  if (parsed < 1) {
    return {
      tone: 'neutral',
      text: `Enter a quantity between 1 and ${max}.`,
    };
  }
  return null;
}

/* ------------------------------------------------------------------ */
/* One-shot dialogs (temporary wizards)                                */
/* ------------------------------------------------------------------ */

function MachineAssignDialog({
  station,
  initialMachine,
  initialPn,
  queuedQtyFor,
  resolvePn,
  areaCards,
  worker,
  onBack,
  onRecord,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & {
  initialMachine: string | null;
  initialPn: string | null;
  queuedQtyFor: (pn: string) => number;
  resolvePn: (pn: string) => string;
  areaCards: MockAreaCard[];
  /** Back from Step 1 to the PN action dialog (PN-first entry only). */
  onBack?: () => void;
}) {
  const machines = MOCK_AREA_MACHINES[station.area] ?? [];
  const [step, setStep] = useState<'select' | 'qty' | 'confirm'>('select');
  const [machine, setMachine] = useState<string | null>(initialMachine);
  const [pn, setPn] = useState<string | null>(initialPn);
  const [scanError, setScanError] = useState<string | null>(null);
  const scanRef = useRef<HTMLInputElement>(null);
  const max = pn ? queuedQtyFor(pn) : 0;
  const [qty, setQty] = useState('');
  // MAX is the default for assignment quantity.
  useEffect(() => {
    setQty(max > 0 ? String(max) : '');
  }, [max]);
  // The barcode-selection input receives focus when entering Step 1.
  useEffect(() => {
    if (step === 'select') scanRef.current?.focus();
  }, [step]);

  const queuedPns = Array.from(
    new Set(
      areaCards
        .filter((c) => c.machines.some(([m]) => m === 'queue'))
        .map((c) => c.pn),
    ),
  );

  const parsedQty = parseInt(qty || '0', 10);
  const pairSelected = machine !== null && pn !== null && max > 0;
  const qtyValid = parsedQty >= 1 && parsedQty <= max;
  const valid = pairSelected && qtyValid;

  /**
   * Step 1 barcode selection: a Machine barcode of this Area selects
   * the Machine; a queued PN barcode selects the PN. Everything else
   * is an inline error with no selection change — and nothing is ever
   * recorded during selection.
   */
  function handleSelectScan(raw: string) {
    const parsed = parseScan(raw);
    if (parsed.kind === 'empty') return;
    if (parsed.kind === 'machine') {
      const known = MOCK_MACHINE_BARCODES[parsed.id];
      if (!known) {
        setScanError('Unknown Machine barcode — selection unchanged.');
        return;
      }
      if (known.area !== station.area) {
        setScanError(
          `${known.machine} belongs to another Area — selection unchanged.`,
        );
        return;
      }
      const status = machines.find((m) => m.name === known.machine)?.status;
      if (status === 'maintenance') {
        setScanError(
          `${known.machine} is inactive (maintenance) — selection unchanged.`,
        );
        return;
      }
      setMachine(known.machine);
      setScanError(null);
      return;
    }
    if (parsed.kind === 'pn') {
      const resolved = resolvePn(parsed.pn);
      if (queuedQtyFor(resolved) < 1) {
        setScanError(
          `${resolved} has no queued quantity in this Area — selection unchanged.`,
        );
        return;
      }
      setPn(resolved);
      setScanError(null);
      return;
    }
    setScanError(
      'Only a Machine barcode of this Area or a queued PN barcode selects here — selection unchanged, nothing recorded.',
    );
  }

  function goQty() {
    if (pairSelected) setStep('qty');
  }

  function goConfirm() {
    if (valid) setStep('confirm');
  }

  function confirm() {
    if (!valid || !machine || !pn) return;
    onRecord({
      pn,
      movementType: 'ASSIGNED_TO_MACHINE',
      description: `${station.area} queue → ${machine} · qty ${parsedQty}`,
      qty: parsedQty,
      source: 'Area queue',
      destination: machine,
      machine,
      worker,
      time: MOCK_SCAN_TIME,
      reversalEffect: `Returns ${parsedQty} pcs from ${machine} to the Area queue.`,
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `${pn} × ${parsedQty} → ${machine}`,
      detail:
        'ASSIGNED_TO_MACHINE (mock presentation only — no production write). One-shot action complete; no Machine context stays armed.',
    });
    onClose();
  }

  const keys =
    step === 'qty'
      ? quantityKeyHandler(qty, setQty, goConfirm)
      : step === 'confirm'
        ? enterKeyHandler(confirm)
        : enterKeyHandler(goQty);

  return (
    <ModalDialog
      label="One-shot Machine assignment"
      onClose={onCancel}
      size="wide"
      onKeyDown={keys}
    >
      <h3>One-shot Machine assignment</h3>
      {step === 'select' ? (
        <>
          <div className="sub">
            Assign queued quantity to a Machine. This is a single action — there
            is no persistent Machine Session; when this dialog closes, nothing
            stays armed.
          </div>
          <input
            ref={scanRef}
            className="field mono"
            autoComplete="off"
            placeholder="Scan Machine or queued PN barcode… (ENTER)"
            aria-label="Scan Machine or queued PN barcode"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleSelectScan(e.currentTarget.value);
                e.currentTarget.value = '';
              }
            }}
          />
          {scanError ? <div className="ss-scannote">{scanError}</div> : null}
          <div className="ss-dlgrid">
            <span className="lbl" id="ma-machine-lbl">
              Machine
            </span>
            {machines.length > 6 ? (
              <select
                aria-label="Machine"
                value={machine ?? ''}
                onChange={(e) => setMachine(e.target.value)}
              >
                <option value="" disabled>
                  Select a Machine…
                </option>
                {machines.map((m) => (
                  <option
                    key={m.name}
                    value={m.name}
                    disabled={m.status === 'maintenance'}
                  >
                    {m.name}
                    {m.status === 'maintenance'
                      ? ' — maintenance (unavailable)'
                      : ''}
                  </option>
                ))}
              </select>
            ) : (
              <div
                className="ss-choicerow"
                role="group"
                aria-labelledby="ma-machine-lbl"
              >
                {machines.map((m) => (
                  <button
                    key={m.name}
                    type="button"
                    className={`pickbtn ${machine === m.name ? 'sel' : ''}`}
                    disabled={m.status === 'maintenance'}
                    title={
                      m.status === 'maintenance'
                        ? 'Under maintenance — accepts no production'
                        : undefined
                    }
                    onClick={() => setMachine(m.name)}
                  >
                    {m.name}
                    <span className="s">{m.status}</span>
                  </button>
                ))}
              </div>
            )}
            <span className="lbl" id="ma-pn-lbl">
              PN (queued)
            </span>
            {queuedPns.length === 0 ? (
              <span className="sub">No queued quantity in this Area.</span>
            ) : queuedPns.length > 6 ? (
              <select
                aria-label="PN (queued)"
                className="mono"
                value={pn ?? ''}
                onChange={(e) => setPn(e.target.value)}
              >
                <option value="" disabled>
                  Select a queued PN…
                </option>
                {queuedPns.map((queuedPn) => (
                  <option key={queuedPn} value={queuedPn}>
                    {queuedPn} — queued {queuedQtyFor(queuedPn)}
                  </option>
                ))}
              </select>
            ) : (
              <div
                className="ss-choicerow"
                role="group"
                aria-labelledby="ma-pn-lbl"
              >
                {queuedPns.map((queuedPn) => (
                  <button
                    key={queuedPn}
                    type="button"
                    className={`pickbtn mono ${pn === queuedPn ? 'sel' : ''}`}
                    onClick={() => setPn(queuedPn)}
                  >
                    <span className="pickpn" title={queuedPn}>
                      {queuedPn}
                    </span>
                    <span className="s">queued {queuedQtyFor(queuedPn)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <StepButtons
            onBack={onBack}
            onCancel={onCancel}
            primary={{
              label: 'Next',
              onClick: goQty,
              disabled: !pairSelected,
            }}
          />
        </>
      ) : null}
      {step === 'qty' && pn ? (
        <>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <StepRecap
            lines={[
              <>
                Assigning to <b>{machine}</b>. Queued quantity: <b>{max} pcs</b>
                .
              </>,
              <>Source: Area queue → Destination: {machine}</>,
            ]}
          />
          <Guidance tone="info">
            MAX defaults to the queued quantity. Enter a smaller quantity if
            needed.
          </Guidance>
          {(() => {
            const v = quantityValidation(
              parsedQty,
              max,
              `Quantity cannot exceed the ${max} pcs currently queued in this Area.`,
            );
            return v ? <Guidance tone={v.tone}>{v.text}</Guidance> : null;
          })()}
          <QuantityKeypad value={qty} onChange={setQty} max={max} />
          <StepButtons
            onBack={() => setStep('select')}
            onCancel={onCancel}
            primary={{
              label: 'Next',
              onClick: goConfirm,
              disabled: !valid,
            }}
          />
        </>
      ) : null}
      {step === 'confirm' && pn && machine ? (
        <>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">Review the assignment, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'ASSIGNED_TO_MACHINE'],
              ['PN', <span className="mono">{pn}</span>],
              ['Quantity', <span className="mono">{parsedQty} pcs</span>],
              ['Source', 'Area queue'],
              ['Destination Machine', machine],
              [
                'Remaining queued after assignment',
                <span className="mono">{max - parsedQty} pcs</span>,
              ],
              ['Worker', worker],
              ['Scan Station', station.stationId],
            ]}
          />
          <StepButtons
            onBack={() => setStep('qty')}
            onCancel={onCancel}
            primary={{
              label: 'Confirm assignment',
              onClick: confirm,
              disabled: !valid,
              autoFocus: true,
            }}
          />
        </>
      ) : null}
    </ModalDialog>
  );
}

function PnActionsDialog({
  pn,
  station,
  hasMachines,
  queuedQty,
  sources,
  repairSources,
  inAreaQty,
  onPick,
  onCancel,
}: {
  pn: string;
  station: MockScanStation;
  hasMachines: boolean;
  queuedQty: number;
  sources: SourceOption[];
  repairSources: {
    areaLabel: string;
    qty: number;
    flow: string;
    note: string;
  }[];
  inAreaQty: number;
  onPick: (flow: Flow) => void;
  onCancel: () => void;
}) {
  const areaName = areaByKey(station.area)?.name ?? station.area;
  return (
    <ModalDialog label="Choose the action for this PN" onClose={onCancel}>
      <h3>Choose the action for this PN</h3>
      <div className="big mono">{pn}</div>
      <div className="sub">
        Only currently valid choices are shown. Nothing is recorded until one
        action is completed; Cancel abandons the scan with no write.
      </div>
      {hasMachines && queuedQty > 0 ? (
        <button
          className="choice"
          onClick={() =>
            onPick({
              kind: 'machine-assign',
              machine: null,
              pn,
              fromActions: true,
            })
          }
        >
          <span className="cic que" aria-hidden="true">
            ASN
          </span>
          <span>
            <span className="ct1">Assign queued quantity to a Machine</span>
            <br />
            <span className="ct2">
              {queuedQty} pcs waiting in the {areaName} queue — one-shot
              assignment, MAX defaults to the queued quantity.
            </span>
          </span>
        </button>
      ) : null}
      {sources.length > 0 ? (
        <button
          className="choice"
          onClick={() =>
            sources.length === 1
              ? onPick({
                  kind: 'transfer',
                  pn,
                  source: sources[0],
                })
              : onPick({ kind: 'source-select', pn, sources })
          }
        >
          <span className="cic run" aria-hidden="true">
            MOVE
          </span>
          <span>
            <span className="ct1">Receive more quantity from another Area</span>
            <br />
            <span className="ct2">
              {sources.map((s) => `${s.qty} pcs at ${s.areaLabel}`).join(' · ')}{' '}
              — sources are never combined silently.
            </span>
          </span>
        </button>
      ) : null}
      <button
        className="choice"
        onClick={() => onPick({ kind: 'add-qty', pn })}
      >
        <span className="cic add" aria-hidden="true">
          ADD
        </span>
        <span>
          <span className="ct1">Add more quantity</span>
          <br />
          <span className="ct2">
            Intentionally introduces additional physical quantity — reason
            required, auditable (QUANTITY_ADJUSTED · INCREASE), no Manager
            approval needed.
          </span>
        </span>
      </button>
      {repairSources.length > 0 ? (
        <button
          className="choice"
          onClick={() => onPick({ kind: 'repair', pn })}
        >
          <span className="cic rep" aria-hidden="true">
            REP
          </span>
          <span>
            <span className="ct1">Send quantity here for repair</span>
            <br />
            <span className="ct2">
              Return quantity that previously passed {areaName} to correct
              earlier work — explicit intent, never assumed from history.
            </span>
          </span>
        </button>
      ) : null}
      {inAreaQty > 0 ? (
        <button
          className="choice"
          onClick={() => onPick({ kind: 'scrap', pn })}
        >
          <span className="cic scr" aria-hidden="true">
            SCR
          </span>
          <span>
            <span className="ct1">Scrap damaged quantity</span>
            <br />
            <span className="ct2">
              Count damaged pieces with the {SCRAP_BARCODE} barcode, give a
              common reason, confirm one auditable SCRAPPED operation.
            </span>
          </span>
        </button>
      ) : null}
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc) — nothing recorded
        </button>
      </div>
    </ModalDialog>
  );
}

function SourceSelectDialog({
  pn,
  sources,
  onPick,
  onCancel,
}: {
  pn: string;
  sources: SourceOption[];
  onPick: (source: SourceOption) => void;
  onCancel: () => void;
}) {
  return (
    <ModalDialog label="Select the source" onClose={onCancel}>
      <h3>Select the source</h3>
      <div className="big mono">{pn}</div>
      <div className="sub">
        This PN has quantity in more than one source position. Select exactly
        one — quantities from multiple sources are never combined silently.
      </div>
      {sources.map((source) => (
        <button
          key={source.areaLabel}
          className="choice"
          onClick={() => onPick(source)}
        >
          <span className="cic run" aria-hidden="true">
            SRC
          </span>
          <span>
            <span className="ct1">
              {source.areaLabel} — {source.qty} pcs available
            </span>
            <br />
            <span className="ct2">
              {source.card.workOrder} · quantity entry follows (MAX {source.qty}
              ).
            </span>
          </span>
        </button>
      ))}
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc) — nothing recorded
        </button>
      </div>
    </ModalDialog>
  );
}

function TransferDialog({
  station,
  pn,
  source,
  hasMachines,
  worker,
  onRecord,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & {
  pn: string;
  source: SourceOption;
  hasMachines: boolean;
}) {
  const areaName = areaByKey(station.area)?.name ?? station.area;
  const [step, setStep] = useState<'qty' | 'confirm'>('qty');
  const [qty, setQty] = useState(String(source.qty)); // MAX default
  const parsedQty = parseInt(qty || '0', 10);
  const valid = parsedQty >= 1 && parsedQty <= source.qty;
  const destinationNote = hasMachines
    ? `${areaName} queue (awaiting Machine)`
    : `${areaName} — direct processing (Machine = NULL)`;

  function goConfirm() {
    if (valid) setStep('confirm');
  }

  function confirm() {
    if (!valid) return;
    onRecord({
      pn,
      movementType: 'TRANSFERRED',
      description: `${source.areaLabel} → ${destinationNote} · qty ${parsedQty}`,
      qty: parsedQty,
      source: source.areaLabel,
      destination: areaName,
      worker,
      time: MOCK_SCAN_TIME,
      reversalEffect: `Returns ${parsedQty} pcs to ${source.areaLabel}.`,
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `${pn} × ${parsedQty} → ${destinationNote}`,
      detail:
        'TRANSFERRED (mock presentation only — no production write; the real write is server-confirmed).',
    });
    onClose();
  }

  return (
    <ModalDialog
      label="Transfer into this Area"
      onClose={onCancel}
      onKeyDown={
        step === 'qty'
          ? quantityKeyHandler(qty, setQty, goConfirm)
          : enterKeyHandler(confirm)
      }
    >
      <h3>Transfer into this Area</h3>
      {step === 'qty' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <StepRecap
            lines={[
              <>
                Transfer {source.areaLabel} → {destinationNote}
              </>,
              <>{source.card.workOrder}</>,
            ]}
          />
          <Guidance tone="info">
            Available at {source.areaLabel}: <b>{source.qty} pcs</b>. MAX is
            selected by default.
          </Guidance>
          {(() => {
            const v = quantityValidation(
              parsedQty,
              source.qty,
              `Quantity cannot exceed the ${source.qty} pcs currently available at the source.`,
            );
            return v ? <Guidance tone={v.tone}>{v.text}</Guidance> : null;
          })()}
          <QuantityKeypad value={qty} onChange={setQty} max={source.qty} />
          <StepButtons
            onCancel={onCancel}
            primary={{
              label: 'Next',
              onClick: goConfirm,
              disabled: !valid,
            }}
          />
        </div>
      ) : (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">Review the transfer, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'TRANSFERRED'],
              ['PN', <span className="mono">{pn}</span>],
              ['Quantity', <span className="mono">{parsedQty} pcs</span>],
              ['Source', source.areaLabel],
              ['Destination', destinationNote],
              [
                'Remaining at source',
                <span className="mono">{source.qty - parsedQty} pcs</span>,
              ],
              ['Worker', worker],
              ['Scan Station', station.stationId],
            ]}
          />
          <StepButtons
            onBack={() => setStep('qty')}
            onCancel={onCancel}
            primary={{
              label: 'Confirm transfer',
              onClick: confirm,
              disabled: !valid,
              autoFocus: true,
            }}
          />
        </div>
      )}
    </ModalDialog>
  );
}

function IntakeDialog({
  station,
  pn,
  hasMachines,
  worker,
  onCreatePn,
  onRecord,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & {
  pn: string;
  hasMachines: boolean;
  onCreatePn: (pn: string) => void;
}) {
  const areaName = areaByKey(station.area)?.name ?? station.area;
  const operations = areaByKey(station.area)?.operations ?? [];
  const [step, setStep] = useState<'settings' | 'qty' | 'confirm'>('settings');
  const [qty, setQty] = useState(''); // intake has no MAX and no default
  const [requestType, setRequestType] = useState<RequestType>('MODIFY');
  const [routeMode, setRouteMode] = useState<RouteMode>('FLOATING');
  const [plannedRoute, setPlannedRoute] = useState('Bracket std v3');
  const [due, setDue] = useState('');
  const [notes, setNotes] = useState('');
  const [operation, setOperation] = useState(operations[0] ?? '');
  const firstFieldRef = useRef<HTMLSelectElement>(null);
  useEffect(() => {
    if (step === 'settings') firstFieldRef.current?.focus();
  }, [step]);
  const parsedQty = parseInt(qty || '0', 10);
  const settingsValid = routeMode === 'FLOATING' || plannedRoute !== '';
  const valid = parsedQty >= 1 && settingsValid;
  const isKnown = catalogPartNumber(pn) !== undefined;
  // One clearly applicable blank-number MODIFY Work Order is reused;
  // with several plausible ones an explicit selection dialog would
  // appear (never a guess). The mock data carries one such WO.
  const reusableInternalWo =
    requestType === 'MODIFY' && pnKey(pn) === pnKey('214-406');
  const woBehavior = reusableInternalWo
    ? 'Reuses the applicable internal MODIFY Work Order (WO —)'
    : requestType === 'MODIFY'
      ? 'Creates an internal Work Order without an external number (displays —)'
      : 'Creates/uses the applicable Work Order';

  function goQty() {
    if (settingsValid) setStep('qty');
  }

  function goConfirm() {
    if (valid) setStep('confirm');
  }

  function confirm() {
    if (!valid) return;
    onCreatePn(pn);
    onRecord({
      pn,
      movementType: 'RECEIVED',
      description: `intake into ${areaName}${hasMachines ? ' queue' : ''} · qty ${parsedQty} · ${requestType} · ${routeMode}`,
      qty: parsedQty,
      source: '—',
      destination: areaName,
      worker,
      time: MOCK_SCAN_TIME,
      reversalEffect: `Removes the ${parsedQty} pcs introduced by this intake.`,
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `${pn} × ${parsedQty} received into ${areaName}${hasMachines ? ' queue' : ''}`,
      detail: `Mock presentation of the future intake transaction: ${
        isKnown ? 'reuses' : 'creates'
      } the PartNumber, ${
        reusableInternalWo
          ? 'reuses the applicable internal MODIFY Work Order (WO —)'
          : requestType === 'MODIFY'
            ? 'creates an internal Work Order without an external number (displays —)'
            : 'creates/uses the applicable Work Order'
      }, creates WorkOrderDemand + QuantityFlow (${routeMode}), records RECEIVED, ${
        hasMachines
          ? 'places quantity in the Area queue'
          : 'places quantity directly in Area processing (Machine = NULL)'
      }. No production write occurred (Phase 2).`,
    });
    onClose();
  }

  const keys =
    step === 'qty'
      ? quantityKeyHandler(qty, setQty, goConfirm)
      : step === 'confirm'
        ? enterKeyHandler(confirm)
        : enterKeyHandler(goQty);

  return (
    <ModalDialog
      label="Receive quantity — intake"
      onClose={onCancel}
      size="wide"
      onKeyDown={keys}
    >
      <h3>Receive quantity — intake</h3>
      {step === 'settings' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">
            {isKnown ? (
              <>This PN has no active Work Order Demand.</>
            ) : (
              <>
                New PN — the internal PartNumber record is created on first
                valid use (no preloaded catalog required; identity is
                case-insensitive, this exact text is preserved).
              </>
            )}{' '}
            Defaults: Request Type <b>MODIFY</b>, Route Mode <b>FLOATING</b> —
            both editable. Received date defaults to the scan timestamp; the due
            date belongs to the WorkOrderDemand and may stay empty. Quantity
            follows on the next step.
          </div>
          <div className="ss-dlgrid">
            <label htmlFor="in-type">Request Type</label>
            <select
              id="in-type"
              ref={firstFieldRef}
              value={requestType}
              onChange={(e) => setRequestType(e.target.value as RequestType)}
            >
              <option>MODIFY</option>
              <option>NEW</option>
            </select>
            <label htmlFor="in-route">Route Mode</label>
            <select
              id="in-route"
              value={routeMode}
              onChange={(e) => setRouteMode(e.target.value as RouteMode)}
            >
              <option>FLOATING</option>
              <option>PLANNED</option>
            </select>
            {routeMode === 'PLANNED' ? (
              <>
                <label htmlFor="in-planned">Planned Route</label>
                <select
                  id="in-planned"
                  value={plannedRoute}
                  onChange={(e) => setPlannedRoute(e.target.value)}
                >
                  <option>Bracket std v3</option>
                  <option>Shaft std v2</option>
                </select>
              </>
            ) : null}
            <label htmlFor="in-due">Due date (optional)</label>
            <input
              id="in-due"
              type="date"
              className="mono"
              value={due}
              onChange={(e) => setDue(e.target.value)}
            />
            <label htmlFor="in-op">Starting Area · Operation</label>
            <select
              id="in-op"
              value={operation}
              onChange={(e) => setOperation(e.target.value)}
            >
              {operations.map((op) => (
                <option key={op}>{`${areaName} — ${op}`}</option>
              ))}
            </select>
            <label htmlFor="in-notes">Reason / notes</label>
            <input
              id="in-notes"
              value={notes}
              placeholder="optional for MODIFY intake"
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
          <div className="ss-recap">
            <div className="ss-recapline">Work Order: {woBehavior}.</div>
          </div>
          <StepButtons
            onCancel={onCancel}
            primary={{
              label: 'Next',
              onClick: goQty,
              disabled: !settingsValid,
            }}
          />
        </div>
      ) : null}
      {step === 'qty' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <StepRecap
            lines={[
              <>
                {requestType} · {routeMode}
                {routeMode === 'PLANNED' ? <> · “{plannedRoute}”</> : null}
              </>,
              <>{operation}</>,
              <>Due: {due || '—'}</>,
              <>
                {reusableInternalWo || requestType === 'MODIFY'
                  ? 'Internal WO —'
                  : 'Work Order to be created/selected'}
              </>,
              notes ? <>Notes: {notes}</> : null,
            ]}
          />
          <Guidance>
            Enter the physical quantity received. No default quantity is
            assumed.
          </Guidance>
          {parsedQty < 1 ? (
            <Guidance>Enter a positive quantity.</Guidance>
          ) : null}
          <QuantityKeypad value={qty} onChange={setQty} />
          <StepButtons
            onBack={() => setStep('settings')}
            onCancel={onCancel}
            primary={{
              label: 'Next',
              onClick: goConfirm,
              disabled: !valid,
            }}
          />
        </div>
      ) : null}
      {step === 'confirm' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">Review the intake, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'Receive quantity — intake (RECEIVED)'],
              ['PN', <span className="mono">{pn}</span>],
              ['Quantity', <span className="mono">{parsedQty} pcs</span>],
              ['Request Type', requestType],
              ['Route Mode', routeMode],
              routeMode === 'PLANNED'
                ? ['Planned Route', plannedRoute]
                : ['Planned Route', null],
              ['Work Order', woBehavior],
              ['Due date', due || '—'],
              ['Starting Area · Operation', operation],
              [
                'Destination',
                hasMachines
                  ? `${areaName} queue (awaiting Machine)`
                  : `${areaName} — direct processing (Machine = NULL)`,
              ],
              ['Reason / notes', notes || null],
              ['Worker', worker],
              ['Scan Station', station.stationId],
            ]}
          />
          <StepButtons
            onBack={() => setStep('qty')}
            onCancel={onCancel}
            primary={{
              label: 'Confirm intake',
              onClick: confirm,
              disabled: !valid,
              autoFocus: true,
            }}
          />
        </div>
      ) : null}
    </ModalDialog>
  );
}

function AddQuantityDialog({
  station,
  pn,
  hasMachines,
  worker,
  onRecord,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & { pn: string; hasMachines: boolean }) {
  const areaName = areaByKey(station.area)?.name ?? station.area;
  const [step, setStep] = useState<'entry' | 'confirm'>('entry');
  const [qty, setQty] = useState(''); // deliberately no MAX, no default
  const [reason, setReason] = useState('');
  const parsedQty = parseInt(qty || '0', 10);
  const valid = parsedQty >= 1 && reason.trim() !== '';

  function goConfirm() {
    if (valid) setStep('confirm');
  }

  function confirm() {
    if (!valid) return;
    onRecord({
      pn,
      movementType: 'QUANTITY_ADJUSTED',
      description: `+${parsedQty} pcs at ${areaName} (INCREASE) · reason: ${reason.trim()}`,
      qty: parsedQty,
      source: '—',
      destination: areaName,
      worker,
      time: MOCK_SCAN_TIME,
      reversalEffect: `Removes the ${parsedQty} added pcs again.`,
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `${pn} +${parsedQty} pcs at ${areaName}`,
      detail: `QUANTITY_ADJUSTED · direction INCREASE (auditable; never an ordinary transfer, never changes the WO Demand requested quantity). ${
        hasMachines
          ? 'Added quantity enters the Area queue.'
          : 'Added quantity enters direct Area processing.'
      } Mock only — no production write.`,
    });
    onClose();
  }

  return (
    <ModalDialog
      label="Add more quantity"
      onClose={onCancel}
      onKeyDown={
        step === 'entry'
          ? quantityKeyHandler(qty, setQty, goConfirm)
          : enterKeyHandler(confirm)
      }
    >
      <h3>Add more quantity</h3>
      {step === 'entry' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">
            Intentionally introduces additional physical quantity at {areaName}.
            Operators may do this without Manager approval; the reason is
            mandatory and the event is auditable (QUANTITY_ADJUSTED · INCREASE).
          </div>
          <Guidance>
            There is deliberately no MAX and no default — enter the actual
            physical count.
          </Guidance>
          {parsedQty < 1 ? (
            <Guidance>Enter a positive quantity.</Guidance>
          ) : null}
          <QuantityKeypad value={qty} onChange={setQty} />
          <label className="ss-reasonlbl" htmlFor="addq-reason">
            Reason (required)
          </label>
          <input
            id="addq-reason"
            className="field"
            value={reason}
            placeholder="e.g. found 2 additional blanks with the lot"
            onChange={(e) => setReason(e.target.value)}
          />
          <StepButtons
            onCancel={onCancel}
            primary={{
              label: 'Next',
              onClick: goConfirm,
              disabled: !valid,
            }}
          />
        </div>
      ) : (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">Review the quantity addition, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'QUANTITY_ADJUSTED · INCREASE'],
              ['PN', <span className="mono">{pn}</span>],
              ['Quantity', <span className="mono">+{parsedQty} pcs</span>],
              ['Area', areaName],
              [
                'Destination',
                hasMachines
                  ? `${areaName} queue (awaiting Machine)`
                  : `${areaName} — direct processing (Machine = NULL)`,
              ],
              ['Reason', reason.trim()],
              ['Worker', worker],
              ['Scan Station', station.stationId],
            ]}
          />
          <StepButtons
            onBack={() => setStep('entry')}
            onCancel={onCancel}
            primary={{
              label: 'Confirm addition',
              onClick: confirm,
              disabled: !valid,
              autoFocus: true,
            }}
          />
        </div>
      )}
    </ModalDialog>
  );
}

function RepairDialog({
  station,
  pn,
  sources,
  worker,
  onRecord,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & {
  pn: string;
  sources: { areaLabel: string; qty: number; flow: string; note: string }[];
}) {
  const areaName = areaByKey(station.area)?.name ?? station.area;
  const [step, setStep] = useState<'entry' | 'confirm'>('entry');
  const [source, setSource] = useState(
    sources.length === 1 ? sources[0] : null,
  );
  const max = source?.qty ?? 0;
  const [qty, setQty] = useState('');
  const [reason, setReason] = useState('');
  useEffect(() => {
    setQty(max > 0 ? String(max) : '');
  }, [max]);
  const parsedQty = parseInt(qty || '0', 10);
  const valid =
    source !== null &&
    parsedQty >= 1 &&
    parsedQty <= max &&
    reason.trim() !== '';
  const partial = source !== null && parsedQty >= 1 && parsedQty < source.qty;

  function goConfirm() {
    if (valid) setStep('confirm');
  }

  function confirm() {
    if (!valid || !source) return;
    onRecord({
      pn,
      movementType: 'TRANSFERRED',
      description: `REPAIR · ${source.areaLabel} → ${areaName} · qty ${parsedQty} · reason: ${reason.trim()}`,
      qty: parsedQty,
      source: source.areaLabel,
      destination: areaName,
      worker,
      time: MOCK_SCAN_TIME,
      reversalEffect: `Returns ${parsedQty} pcs to ${source.areaLabel}.`,
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `REPAIR — ${pn} × ${parsedQty} returns to ${areaName}`,
      detail: `TRANSFERRED with movement_reason REPAIR (${source.flow}${
        partial
          ? ' — partial quantity requires a QuantityFlow SPLIT first (Phase 8)'
          : ''
      }). Repair creates no new quantity and no new demand; the Movement history stays authoritative. Mock only — no production write.`,
    });
    onClose();
  }

  return (
    <ModalDialog
      label="Send quantity here for repair"
      onClose={onCancel}
      size="wide"
      onKeyDown={
        step === 'entry'
          ? quantityKeyHandler(qty, setQty, goConfirm)
          : enterKeyHandler(confirm)
      }
    >
      <h3>Send quantity here for repair</h3>
      {step === 'entry' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">
            An explicit Repair intent: quantity already in production returns to{' '}
            {areaName} — an Area it previously visited — to correct earlier
            work. Repair is a movement (TRANSFERRED · movement_reason REPAIR),
            never a Request Type and never new demand or new quantity. Returning
            to a previously visited Area is <b>not</b> assumed to be Repair —
            you chose it here.
          </div>
          <div className="ss-dlgrid">
            <span className="lbl">Source</span>
            <div className="ss-choicerow">
              {sources.map((s) => (
                <button
                  key={s.areaLabel}
                  type="button"
                  className={`pickbtn ${source?.areaLabel === s.areaLabel ? 'sel' : ''}`}
                  onClick={() => setSource(s)}
                >
                  {s.areaLabel} · {s.qty} pcs · {s.flow}
                  <span className="s">{s.note}</span>
                </button>
              ))}
            </div>
          </div>
          {source ? (
            <>
              <Guidance tone="warn">
                Repair quantity — MAX {max} pcs, the default. A partial quantity
                splits the QuantityFlow (requires SPLIT — Phase 8); the full
                quantity moves the whole flow.
              </Guidance>
              {(() => {
                const v = quantityValidation(
                  parsedQty,
                  max,
                  `Quantity cannot exceed the ${max} pcs of the selected source flow.`,
                );
                return v ? <Guidance tone={v.tone}>{v.text}</Guidance> : null;
              })()}
              <QuantityKeypad value={qty} onChange={setQty} max={max} />
            </>
          ) : null}
          <label className="ss-reasonlbl" htmlFor="rep-reason">
            Reason (required)
          </label>
          <input
            id="rep-reason"
            className="field"
            value={reason}
            placeholder="what must be corrected?"
            onChange={(e) => setReason(e.target.value)}
          />
          <StepButtons
            onCancel={onCancel}
            primary={{
              label: 'Next',
              onClick: goConfirm,
              disabled: !valid,
            }}
          />
        </div>
      ) : (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">Review the Repair movement, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'TRANSFERRED · movement_reason REPAIR'],
              ['PN', <span className="mono">{pn}</span>],
              ['Quantity', <span className="mono">{parsedQty} pcs</span>],
              [
                'Source',
                source ? `${source.areaLabel} · ${source.flow}` : null,
              ],
              ['Destination', areaName],
              [
                'Effect',
                partial
                  ? 'Partial quantity — the QuantityFlow splits first (SPLIT, Phase 8)'
                  : 'Moves the whole Quantity Flow',
              ],
              ['Reason', reason.trim()],
              ['Worker', worker],
              ['Scan Station', station.stationId],
            ]}
          />
          <StepButtons
            onBack={() => setStep('entry')}
            onCancel={onCancel}
            primary={{
              label: 'Confirm repair',
              onClick: confirm,
              disabled: !valid,
              autoFocus: true,
            }}
          />
        </div>
      )}
    </ModalDialog>
  );
}

function ScrapDialog({
  station,
  pn,
  available,
  worker,
  onRecord,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & { pn: string; available: number }) {
  const areaName = areaByKey(station.area)?.name ?? station.area;
  const [step, setStep] = useState<'count' | 'confirm'>('count');
  const [count, setCount] = useState(0);
  const [reason, setReason] = useState('');
  const [scanNote, setScanNote] = useState<string | null>(null);
  const scanRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (step === 'count') scanRef.current?.focus();
  }, [step]);
  const valid = count >= 1 && count <= available && reason.trim() !== '';

  function handleScrapScan(value: string) {
    const parsed = parseScan(value);
    if (parsed.kind === 'empty') return;
    if (parsed.kind !== 'scrap') {
      setScanNote(
        `Rejected “${value.trim()}” — only ${SCRAP_BARCODE} counts here.`,
      );
      return;
    }
    // Counting changes no production state — only the pending count.
    setCount((c) => c + 1);
    setScanNote(null);
  }

  function goConfirm() {
    if (valid) setStep('confirm');
  }

  function confirm() {
    if (!valid) return;
    onRecord({
      pn,
      movementType: 'SCRAPPED',
      description: `scrapped ${count} at ${areaName} · reason: ${reason.trim()}`,
      qty: count,
      source: areaName,
      destination: 'scrap',
      worker,
      time: MOCK_SCAN_TIME,
      reversalEffect: `Restores the ${count} scrapped pcs to active quantity.`,
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `SCRAPPED — ${pn} × ${count} at ${areaName}`,
      detail:
        'One auditable scrap operation for the counted total. Scrap never reduces the WO Demand requested quantity; reconciliation: introduced = active + stocked + scrapped. Mock only — no production write.',
    });
    onClose();
  }

  const countKeys = (event: React.KeyboardEvent) => {
    const target = event.target;
    if (target instanceof HTMLButtonElement) return;
    if (
      target instanceof HTMLInputElement &&
      target.getAttribute('aria-label') === 'Scrap barcode input'
    ) {
      return; // the scrap counting input owns its Enter handling
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      goConfirm();
    }
  };

  return (
    <ModalDialog
      label="Scrap damaged quantity"
      onClose={onCancel}
      size="wide"
      onKeyDown={step === 'count' ? countKeys : enterKeyHandler(confirm)}
    >
      <h3>Scrap damaged quantity</h3>
      {step === 'count' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">
            Scan <code>{SCRAP_BARCODE}</code> once per damaged piece — the
            barcode is context-sensitive and counts only inside this workflow.
            Counting changes no production state; one auditable SCRAPPED
            operation is created only on the final confirmation.
          </div>
          <Guidance tone="warn">
            Available at {areaName}: <b>{available} pcs</b> · pending scrap{' '}
            <b>{count}</b> · remaining after scrap{' '}
            <b>{Math.max(0, available - count)} pcs</b>.
          </Guidance>
          {count > available ? (
            <Guidance tone="error">
              Scrap count cannot exceed the {available} pcs available at{' '}
              {areaName}.
            </Guidance>
          ) : null}
          <div className="ss-scrapcount" role="status">
            <span className="lbl">Pending scrap count</span>
            <span className="cnt mono">{count}</span>
            <button
              type="button"
              className="pickbtn"
              disabled={count === 0}
              onClick={() => setCount((c) => Math.max(0, c - 1))}
            >
              −1 correct
            </button>
            <button
              type="button"
              className="pickbtn"
              disabled={count === 0}
              onClick={() => setCount(0)}
            >
              Reset
            </button>
          </div>
          <input
            ref={scanRef}
            className="field mono"
            placeholder={`Scan ${SCRAP_BARCODE} — Enter`}
            aria-label="Scrap barcode input"
            autoComplete="off"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleScrapScan(e.currentTarget.value);
                e.currentTarget.value = '';
              }
            }}
          />
          {scanNote ? <div className="ss-scannote">{scanNote}</div> : null}
          <label className="ss-reasonlbl" htmlFor="scrap-reason">
            Common scrap reason (required)
          </label>
          <input
            id="scrap-reason"
            className="field"
            value={reason}
            placeholder="e.g. tool crash — gouged face"
            onChange={(e) => setReason(e.target.value)}
          />
          <StepButtons
            onCancel={onCancel}
            cancelLabel="Cancel (Esc) — discard count, nothing recorded"
            primary={{
              label: 'Next',
              onClick: goConfirm,
              disabled: !valid,
            }}
          />
        </div>
      ) : (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">Review the scrap operation, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'SCRAPPED'],
              ['PN', <span className="mono">{pn}</span>],
              ['Area', areaName],
              ['Machine', '—'],
              ['Available', <span className="mono">{available} pcs</span>],
              ['Scrap quantity', <span className="mono">{count} pcs</span>],
              [
                'Remaining active quantity',
                <span className="mono">{available - count} pcs</span>,
              ],
              ['Reason', reason.trim()],
              ['Worker', worker],
              ['Scan Station', station.stationId],
            ]}
          />
          <StepButtons
            onBack={() => setStep('count')}
            onCancel={onCancel}
            cancelLabel="Cancel (Esc) — discard count, nothing recorded"
            primary={{
              label: 'Confirm scrap',
              onClick: confirm,
              disabled: !valid,
              autoFocus: true,
            }}
          />
        </div>
      )}
    </ModalDialog>
  );
}

function QueueReturnDialog({
  station,
  pn,
  machine,
  max,
  worker,
  onRecord,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & { pn: string; machine: string; max: number }) {
  const areaName = areaByKey(station.area)?.name ?? station.area;
  const [step, setStep] = useState<'qty' | 'confirm'>('qty');
  const [qty, setQty] = useState(String(max)); // MAX default
  const parsedQty = parseInt(qty || '0', 10);
  const valid = parsedQty >= 1 && parsedQty <= max;

  function goConfirm() {
    if (valid) setStep('confirm');
  }

  function confirm() {
    if (!valid) return;
    onRecord({
      pn,
      movementType: 'RELEASED_FROM_MACHINE',
      description: `${machine} → ${areaName} queue · qty ${parsedQty}`,
      qty: parsedQty,
      source: machine,
      destination: 'Area queue',
      machine,
      worker,
      time: MOCK_SCAN_TIME,
      reversalEffect: `Re-assigns ${parsedQty} pcs to ${machine}.`,
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `${pn} × ${parsedQty} returned to the ${areaName} queue`,
      detail:
        'RELEASED_FROM_MACHINE (mock presentation only — no production write).',
    });
    onClose();
  }

  return (
    <ModalDialog
      label="Return quantity to the Area queue"
      onClose={onCancel}
      onKeyDown={
        step === 'qty'
          ? quantityKeyHandler(qty, setQty, goConfirm)
          : enterKeyHandler(confirm)
      }
    >
      <h3>Return quantity to the Area queue</h3>
      {step === 'qty' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <StepRecap
            lines={[
              <>
                {machine} → {areaName} queue
              </>,
            ]}
          />
          <Guidance tone="info">
            Assigned on {machine}: <b>{max} pcs</b>. MAX is selected by default.
          </Guidance>
          {(() => {
            const v = quantityValidation(
              parsedQty,
              max,
              `Quantity cannot exceed the ${max} pcs currently assigned on ${machine}.`,
            );
            return v ? <Guidance tone={v.tone}>{v.text}</Guidance> : null;
          })()}
          <QuantityKeypad value={qty} onChange={setQty} max={max} />
          <StepButtons
            onCancel={onCancel}
            primary={{
              label: 'Next',
              onClick: goConfirm,
              disabled: !valid,
            }}
          />
        </div>
      ) : (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">Review the queue return, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'RELEASED_FROM_MACHINE'],
              ['PN', <span className="mono">{pn}</span>],
              ['Quantity', <span className="mono">{parsedQty} pcs</span>],
              ['Source Machine', machine],
              ['Destination', `${areaName} queue`],
              [
                'Remaining on Machine',
                <span className="mono">{max - parsedQty} pcs</span>,
              ],
              ['Worker', worker],
              ['Scan Station', station.stationId],
            ]}
          />
          <StepButtons
            onBack={() => setStep('qty')}
            onCancel={onCancel}
            primary={{
              label: 'Confirm queue return',
              onClick: confirm,
              disabled: !valid,
              autoFocus: true,
            }}
          />
        </div>
      )}
    </ModalDialog>
  );
}

function UndoConfirmDialog({
  target,
  onConfirm,
  onCancel,
}: {
  target: MockCompletedAction;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <ModalDialog label="Undo last PN operation?" onClose={onCancel}>
      <h3>Undo last PN operation?</h3>
      <div className="big mono">{target.pn}</div>
      <div className="sub">
        Undo creates a compensating REVERSED Movement — the original action is
        preserved, never deleted.
      </div>
      <ConfirmationSummary
        rows={[
          ['Original action', target.movementType],
          ['Quantity', <span className="mono">{target.qty}</span>],
          ['Source → destination', `${target.source} → ${target.destination}`],
          ['Machine', target.machine ?? null],
          ['Worker', target.worker],
          ['Time', <span className="mono">{target.time}</span>],
          ['Effect of the reversal', target.reversalEffect],
        ]}
      />
      <StepButtons
        onCancel={onCancel}
        cancelLabel="Cancel — keep the operation"
        primary={{
          label: 'Confirm Undo',
          onClick: onConfirm,
          danger: true,
        }}
      />
    </ModalDialog>
  );
}

function ManualEntryDialog({
  onCancel,
  onConfirm,
}: {
  onCancel: () => void;
  onConfirm: (pn: string) => void;
}) {
  const fieldRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    fieldRef.current?.focus();
  }, []);
  return (
    <ModalDialog label="Manual PN entry — explicit fallback" onClose={onCancel}>
      <h3>Manual PN entry — explicit fallback</h3>
      <div className="sub">
        Type the <b>exact PartNumber</b> (not a barcode). Any non-empty value is
        accepted — PN identity is case-insensitive, and a PN not seen before
        opens the intake flow (created on first valid use). Nothing is recorded
        by this step.
      </div>
      <input
        aria-label="Exact PartNumber"
        ref={fieldRef}
        className="field mono"
        autoComplete="off"
        placeholder="Exact PartNumber, e.g. 0455-20-0118-03"
        onKeyDown={(e) => {
          if (e.key === 'Enter') onConfirm(e.currentTarget.value);
        }}
      />
      <div className="row">
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
        <button
          className="bigbtn primary"
          onClick={() => onConfirm(fieldRef.current?.value ?? '')}
        >
          Look up PN
        </button>
      </div>
    </ModalDialog>
  );
}
