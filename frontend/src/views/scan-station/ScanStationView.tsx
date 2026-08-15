import './scan-station.css';

import {
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
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
import { ConnectivityChip } from '../../components/ConnectivityChip';
import { DevNotice } from '../../components/DevNotice';
import { AreaDot, RouteModeChip, TypeChip } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { applyQuantityKey } from '../../components/quantity-input';
import { QuantityKeypad } from '../../components/QuantityKeypad';
import { ThemeToggle } from '../../components/ThemeToggle';
import { isTouchPrimaryDevice } from '../../components/touch-device';
import { LoadingState } from '../../components/view-states';
import {
  MOCK_AREA_CARDS,
  MOCK_AREA_CARDS_LONG,
  MOCK_AREA_MACHINES,
} from '../../mocks/area-board';
import { areaByKey } from '../../mocks/areas';
import { activeMachines } from '../../mocks/machines';
import { workerIdModeFor } from '../../mocks/administration';
import type { WorkerIdMode } from '../../mocks/administration';
import {
  fixedWorkerFor,
  MOCK_MACHINE_BARCODES,
  MOCK_REPAIR_SOURCES,
  MOCK_SCAN_STATIONS,
  stationById,
  workerByBadge,
  workerSessionTimeoutMinutes,
} from '../../mocks/scan-station';
import type { MockWorker } from '../../mocks/scan-station';
import { catalogPartNumber } from '../../mocks/work-orders';
import { areaStats, splitAssignments } from '../area-monitoring';
import type { AreaAssignment } from '../area-monitoring';
import { useUiClock } from '../../components/ui-clock';
import { formatIsoDate, todayIso } from '../dates';
import type {
  MockAreaCard,
  MockAreaMachine,
  MockCompletedAction,
  MockScanStation,
  MovementType,
  RequestType,
  RouteMode,
} from '../view-models';
import {
  normalizePartNumber,
  normalizeScanInput,
  parseScan,
  SCRAP_BARCODE,
} from './barcode';
import {
  applyAssign,
  applyDone,
  applyIntroduce,
  applyQueueReturn,
  applyScrap,
  applyTransferIn,
  cardBreakdown,
  completionRequired,
  deriveSessionMachines,
} from './mock-area-state';

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

/**
 * One confirmed application command: the immutable Movement events it
 * appends (in order) plus the mock state transition. Undo reverses the
 * complete command — never one arbitrary event of it.
 */
interface Command {
  action: MockCompletedAction;
  update: (cards: MockAreaCard[]) => MockAreaCard[];
}

/**
 * One-shot dialog flows — no persistent context survives a dialog.
 *
 * `parent` is the dialog/step the flow was opened FROM within the same
 * one-shot workflow (the PN action dialog, the source selection, or
 * manual PN entry) — the complete previous flow value, so Back re-opens
 * exactly that step with its context (PN, source, selections) intact.
 * Back is pure dialog navigation and never records or changes tracking
 * data. Flows opened directly from the Scan Station surface (scan, row
 * action, Undo) carry no parent: they show no Back, and Cancel stays
 * the only exit. Variants that can never be opened from another dialog
 * (queue-return, undo, manual-pn) deliberately have no `parent`.
 */
type Flow =
  | {
      kind: 'machine-assign';
      machine: string | null;
      pn: string | null;
      parent?: Flow;
    }
  | { kind: 'pn-actions'; pn: string; parent?: Flow }
  | {
      kind: 'transfer';
      pn: string;
      source: SourceOption;
      parent?: Flow;
    }
  | {
      kind: 'source-select';
      pn: string;
      sources: SourceOption[];
      parent?: Flow;
    }
  | { kind: 'intake'; pn: string; parent?: Flow }
  | { kind: 'add-qty'; pn: string; parent?: Flow }
  | { kind: 'repair'; pn: string; parent?: Flow }
  | { kind: 'scrap'; pn: string; parent?: Flow }
  | { kind: 'queue-return'; pn: string; machine: string; max: number }
  | {
      kind: 'done';
      pn: string;
      machine: string | null;
      max: number;
      parent?: Flow;
    }
  | { kind: 'undo' }
  | { kind: 'manual-pn'; initialPn?: string };

/**
 * Flow variants the PN action dialog can open as its next step — every
 * one carries the optional `parent` back-reference, so the caller can
 * attach the action dialog as the step Back returns to.
 */
type PnActionChildFlow = Exclude<
  Flow,
  { kind: 'queue-return' } | { kind: 'undo' } | { kind: 'manual-pn' }
>;

/**
 * Scan Station routing: `/scan-station` shows the Station Selector
 * (never auto-redirecting to a station); `/scan-station/:stationId`
 * loads the station; an unknown or inactive Station ID shows an
 * explicit error and never silently falls back to another station.
 * `/scan-station/:stationId/production` loads the same station in
 * production mode: the top application navigation is hidden (App
 * shell). The footer is identical in both modes — a non-interactive
 * Station ID, mode label, and shortcut hint.
 */
export function ScanStationView() {
  const { route } = useRouter();
  if (route.view !== 'scan-station' || route.stationId === null) {
    return <StationSelector />;
  }
  const station = stationById(route.stationId);
  if (!station) return <UnknownStation stationId={route.stationId} />;
  return (
    <StationView
      key={station.stationId}
      station={station}
      productionMode={route.mode === 'production'}
    />
  );
}

/**
 * Supported Operations as individual light informational chips — the
 * same presentation the Scan Station header uses (shared `.opchips` /
 * `.opchip` styling). Labels, not controls: no action color, no
 * button-like hover, wrapping cleanly for multi-Operation Areas.
 */
function OperationChips({ operations }: { operations: readonly string[] }) {
  if (operations.length === 0) return <>—</>;
  return (
    <span className="opchips">
      {operations.map((op) => (
        <span className="opchip" key={op}>
          {op}
        </span>
      ))}
    </span>
  );
}

function StationSelector() {
  const { navigate } = useRouter();
  return (
    <section className="ss" aria-label="Scan Station">
      <div className="ss-select">
        <h1>Select a Scan Station</h1>
        <p className="ss-select-sub">
          Select the Scan Station for your work area. <b>Production mode</b>{' '}
          hides the main navigation to keep the station focused on scanning.
        </p>
        <ul className="ss-stationlist">
          {MOCK_SCAN_STATIONS.filter((s) => s.active).map((s) => {
            const area = areaByKey(s.area);
            const machines = MOCK_AREA_MACHINES[s.area] ?? [];
            return (
              // No nested interactive controls: the card's main surface
              // is ONE button (standard mode) with the Production mode
              // action as its sibling in a separate cell.
              <li key={s.stationId} className="ss-stationcard">
                <button
                  className="ss-stationmain"
                  aria-label={`Open ${s.stationId}`}
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
                    <OperationChips operations={area?.operations ?? []} />
                  </span>
                  <span className="stype">
                    {machines.length > 0
                      ? `${machines.length} Machines · Queue and assignment enabled`
                      : 'Direct Area processing · No Machine assignment'}
                  </span>
                </button>
                <div className="ss-stationacts">
                  <button
                    className="ss-openbtn"
                    aria-label={`Open ${s.stationId} in production mode`}
                    title="Opens this station with the application navigation hidden"
                    onClick={() =>
                      navigate(`/scan-station/${s.stationId}/production`)
                    }
                  >
                    Production mode
                  </button>
                </div>
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
            <div className="t1">Scan Station “{stationId}” is unavailable</div>
            <div className="t2">
              The Station ID is invalid or inactive. Select an available Scan
              Station to continue.
            </div>
          </div>
        </div>
        <button
          className="bigbtn primary"
          onClick={() => navigate('/scan-station')}
        >
          Select another Scan Station
        </button>
      </div>
    </section>
  );
}

/**
 * Development-only clickable demo barcode inside the DevNotice. The
 * button is an invisible wrapper around the shared code chip — the
 * value keeps its `<code>` presentation, hover/focus reveal the
 * affordance (scan-station.css). A click feeds the value through the
 * EXACT scanner path (`onScan` → main input + `handleScan()`); there
 * is no parallel demo scan flow.
 */
function DemoBarcode({
  value,
  onScan,
}: {
  value: string;
  onScan: (value: string) => void;
}) {
  return (
    <button
      type="button"
      className="ss-demobarcode"
      title="Click to simulate scan"
      onClick={() => onScan(value)}
    >
      <code>{value}</code>
    </button>
  );
}

function StationView({
  station,
  productionMode,
}: {
  station: MockScanStation;
  productionMode: boolean;
}) {
  const preview = getViewStatePreview();
  const { navigate } = useRouter();
  const { status } = useConnectivity();
  const disconnected = status === 'unavailable';
  const writeBlocked = status !== 'connected';

  const area = areaByKey(station.area);
  // This Area's Machines from the registry (retired Machines never
  // appear). Their monitoring state (running/idle + state age) is NOT
  // taken from the load-time mock projection — it is derived below from
  // the session-local cards, so confirmed commands are reflected.
  const stationMachines = useMemo(
    () => activeMachines().filter((m) => m.area === station.area),
    [station.area],
  );
  const hasMachines = stationMachines.length > 0;

  const inputRef = useRef<HTMLInputElement>(null);
  // Decided once per station lifecycle — pointer capabilities do not
  // change while the station is open (same pattern as QuantityKeypad).
  const [touchPrimary] = useState(isTouchPrimaryDevice);
  // Worker identification follows the Area's configured Worker ID mode
  // (PROJECT_PROFILE §19): Disabled records no Worker, Fixed Worker
  // records the Area's configured Worker, Scanned session requires an
  // active Worker Session opened by a badge scan.
  const workerMode = workerIdModeFor(station.area);
  const fixedWorker =
    workerMode === 'fixed' ? fixedWorkerFor(station.area) : null;
  const sessionTimeoutMs = workerSessionTimeoutMinutes(station.area) * 60_000;
  // Scanned-session state: the sliding inactivity deadline moves
  // forward with every VALID production interaction (refreshSession);
  // invalid or unknown scans never refresh it. No session exists until
  // the first badge scan.
  const [session, setSession] = useState<{
    worker: MockWorker;
    expiresAt: number;
  } | null>(null);
  // Expiration flips through a timer aimed at the sliding deadline —
  // the displayed countdown derives from the shared UI clock inside
  // the Worker pill (§4.3), so the big station surface never
  // re-renders per tick.
  const [sessionExpired, setSessionExpired] = useState(false);
  useEffect(() => {
    if (workerMode !== 'scanned' || !session) return;
    setSessionExpired(false);
    const ms = session.expiresAt - Date.now();
    if (ms <= 0) {
      setSessionExpired(true);
      return;
    }
    const timer = window.setTimeout(() => setSessionExpired(true), ms);
    return () => window.clearTimeout(timer);
  }, [session, workerMode]);
  // The station is session-blocked in Scanned-session mode until a
  // valid badge scan: on open (no session yet) and after expiration.
  // ONLY the Scan Station is blocked — the badge modal exists on this
  // route alone, and an open production dialog keeps its draft
  // underneath while confirmation stays blocked (§4.12).
  const sessionBlocked =
    workerMode === 'scanned' && (!session || sessionExpired);
  const activeWorker: MockWorker | null =
    workerMode === 'fixed'
      ? fixedWorker
      : workerMode === 'scanned' && session && !sessionExpired
        ? session.worker
        : null;
  const workerName = activeWorker?.name ?? null;
  const [notice, setNotice] = useState<Notice | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  // Session-local copy of the mock Area cards (all Areas): confirmed
  // commands update it so the monitoring surfaces reflect the result.
  const baseCards = preview === 'long' ? MOCK_AREA_CARDS_LONG : MOCK_AREA_CARDS;
  const [allCards, setAllCards] = useState<MockAreaCard[]>(() =>
    structuredClone(baseCards),
  );
  // Completed application commands, newest first; reversed entries stay
  // for audit display but are no longer Undo-eligible. Each entry keeps
  // the pre-command state so Undo restores the complete command.
  const [history, setHistory] = useState<
    { action: MockCompletedAction; reversed: boolean; before: MockAreaCard[] }[]
  >([]);
  // Per-Machine session monitoring state (keyed by stable Machine id):
  // `running`/`idle` derive from the quantity currently assigned on
  // each Machine in the session-local cards (queue and finished never
  // count; maintenance stays an explicit override). The ref carries
  // each Machine's `stateChangedAt` across commands, so the displayed
  // state age keeps aging while the state is unchanged and restarts
  // only when a command actually flips a Machine between Idle and
  // Running.
  const machineStateRef = useRef<Map<string, MockAreaMachine>>(new Map());

  // Reset the session-local mock state when the dev preview changes —
  // including the per-Machine session timestamps, so the next
  // derivation starts from the registry anchors again.
  useEffect(() => {
    setAllCards(structuredClone(baseCards));
    setHistory([]);
    machineStateRef.current = new Map();
  }, [baseCards]);

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
    // completed operation and dialog close. The delayed refocus must
    // never pull focus out of a dialog that opened in the meantime —
    // the dialog owns ENTER/ESC/TAB until it closes.
    setTimeout(() => {
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      inputRef.current?.focus();
    }, 30);
  }, []);

  useEffect(() => {
    if (!writeBlocked) focusScan();
  }, [writeBlocked, focusScan]);

  /**
   * Slide the Scanned-session inactivity deadline forward — called on
   * every VALID production interaction (a successfully resolved PN or
   * Machine scan, a confirmed command, a valid badge scan). Invalid,
   * unknown, or rejected scans never call this (§19).
   */
  const refreshSession = useCallback(() => {
    setSession((current) =>
      current ? { ...current, expiresAt: Date.now() + sessionTimeoutMs } : null,
    );
  }, [sessionTimeoutMs]);

  /**
   * Open (or switch to) a Worker Session from a valid badge scan.
   * Scanning a different active Worker's badge switches the active
   * Worker immediately — no sign-out step (§19).
   */
  const signInWorker = useCallback(
    (worker: MockWorker) => {
      const previous = session?.worker ?? null;
      setSession({ worker, expiresAt: Date.now() + sessionTimeoutMs });
      setNotice({
        kind: 'ok',
        icon: '✓',
        title: `Worker signed in: ${worker.name}`,
        detail:
          previous && previous.id !== worker.id
            ? `${previous.name} was signed out. New actions will be recorded under ${worker.name}.`
            : `New actions will be recorded under ${worker.name}.`,
      });
    },
    [session, sessionTimeoutMs],
  );

  const areaCards = useMemo(
    () =>
      preview === 'empty'
        ? []
        : allCards.filter((c) => c.area === station.area),
    [allCards, preview, station.area],
  );
  const { assigned } = splitAssignments(areaCards);

  // Session-local Machine monitoring cards, derived from the CURRENT
  // session cards above (never the load-time mock projection): a
  // Machine keeps its `stateChangedAt` while its derived state is
  // unchanged and gets a fresh timestamp only when a confirmed command
  // actually flips it between Idle and Running. Idempotent per card
  // state — safe under re-renders.
  const machines = useMemo(() => {
    const derived = deriveSessionMachines(
      stationMachines,
      areaCards,
      machineStateRef.current,
      new Date().toISOString(),
    );
    machineStateRef.current = derived;
    return [...derived.values()];
  }, [stationMachines, areaCards]);

  // Header fit measurement (§4.3): the Area totals drop to their
  // full-width second row only when the single-row layout genuinely
  // cannot hold the three header cells — never at a hard-coded
  // breakpoint. The probe pass applies the `measuring` class (full
  // natural single-row column widths: identity with Department, Area
  // name AND Operations chips each on one line — chips never wrap
  // before the totals drop — totals and Worker Session at content
  // width), reads the widths, and reverts — all synchronously before
  // paint, so the probe layout is never visible.
  const headRef = useRef<HTMLElement>(null);
  const [headWrapped, setHeadWrapped] = useState(false);
  const measureHead = useCallback(() => {
    const head = headRef.current;
    if (!head) return;
    head.classList.add('measuring');
    const styles = getComputedStyle(head);
    const available =
      head.clientWidth -
      (Number.parseFloat(styles.paddingLeft) || 0) -
      (Number.parseFloat(styles.paddingRight) || 0);
    const gap = Number.parseFloat(styles.columnGap) || 0;
    const cells = Array.from(head.children) as HTMLElement[];
    const required =
      cells.reduce((sum, cell) => sum + cell.getBoundingClientRect().width, 0) +
      gap * Math.max(0, cells.length - 1);
    head.classList.remove('measuring');
    // Half-pixel tolerance so subpixel rounding never flips the state.
    setHeadWrapped(required > available + 0.5);
  }, []);

  // Re-measure after every commit that can change a header cell's
  // natural width: Area identity, totals values, Worker session, and
  // the production-mode actions (the connectivity chip text follows
  // the connection status). The measurement is deterministic per
  // container width, so repeated runs are stable.
  useLayoutEffect(() => {
    measureHead();
  }, [
    measureHead,
    area,
    areaCards,
    hasMachines,
    workerMode,
    activeWorker,
    session,
    sessionExpired,
    productionMode,
    status,
  ]);

  useEffect(() => {
    window.addEventListener('resize', measureHead);
    let observer: ResizeObserver | undefined;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => measureHead());
      if (headRef.current) observer.observe(headRef.current);
    }
    return () => {
      window.removeEventListener('resize', measureHead);
      observer?.disconnect();
    };
  }, [measureHead]);

  const eligible = history.find((h) => !h.reversed);
  const lastPn = eligible?.action ?? null;

  // PNs entering this view are already canonical (uppercase,
  // whitespace-free — parseScan / normalizePartNumber), so the PN
  // string itself is the identity and direct equality compares it.
  const cardsFor = useCallback(
    (pn: string) => areaCards.filter((c) => c.pn === pn),
    [areaCards],
  );

  const sourcesFor = useCallback(
    (pn: string): SourceOption[] =>
      allCards
        .filter(
          (c) =>
            c.pn === pn &&
            c.area !== station.area &&
            c.area !== 'stockroom' &&
            c.qty > 0,
        )
        .map((card) => ({
          areaLabel: areaByKey(card.area)?.name ?? card.area,
          qty: card.qty,
          card,
        })),
    [allCards, station.area],
  );

  const queuedQtyFor = useCallback(
    (pn: string) => {
      const { queued } = splitAssignments(cardsFor(pn));
      return queued
        .filter((e) => e.state === 'queue')
        .reduce((s, e) => s + e.qty, 0);
    },
    [cardsFor],
  );

  /** Directly processing (not finished) quantity of a no-Machine Area. */
  const processingQtyFor = useCallback(
    (pn: string) =>
      cardsFor(pn).reduce((s, c) => s + cardBreakdown(c).active, 0),
    [cardsFor],
  );

  const repairSourcesFor = useCallback(
    (pn: string) => MOCK_REPAIR_SOURCES[pn] ?? [],
    [],
  );

  /**
   * Apply one confirmed application command atomically to the mock
   * state: all of its Movement events take effect together, and the
   * pre-command snapshot lets Undo reverse the whole command.
   */
  const applyCommand = useCallback(
    (command: Command) => {
      const before = structuredClone(allCards);
      setAllCards(command.update(structuredClone(allCards)));
      setHistory((current) => [
        { action: command.action, reversed: false, before },
        ...current,
      ]);
      // A confirmed production command is a valid production
      // interaction — it slides the Worker Session deadline (§19).
      refreshSession();
    },
    [allCards, refreshSession],
  );

  const blockedNotice = useCallback(() => {
    setNotice({
      kind: 'err',
      icon: '✕',
      title: 'Connection lost — scanning is paused',
      detail:
        'Reconnect to PartFlow server before continuing. No scans or production updates will be recorded while offline.',
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
    closeFlow('Cancelled. No changes were recorded.');
  }, [closeFlow]);

  /**
   * Back handler for a flow's stored parent step: re-opens exactly the
   * previous dialog/step of the same workflow (context preserved) and
   * writes nothing. Returns undefined when no parent exists, so
   * StepButtons renders no Back for flows entered directly from the
   * Scan Station surface.
   */
  const backTo = (parent?: Flow) =>
    parent ? () => setFlow(parent) : undefined;

  /**
   * Route a resolved PN to the applicable one-shot dialog. `parent` is
   * the dialog step the resolution came from (manual PN entry) so the
   * opened dialog can offer Back to it; a plain scan passes none.
   */
  const openPnFlow = useCallback(
    (pn: string, parent?: Flow) => {
      // `pn` is already the canonical PN (parseScan / manual-entry
      // normalization) — the PN string itself is the identity. A
      // successfully resolved PN is a valid production interaction.
      refreshSession();
      if (cardsFor(pn).length > 0) {
        setFlow({ kind: 'pn-actions', pn, parent });
        return;
      }
      const sources = sourcesFor(pn);
      if (sources.length === 1) {
        setFlow({ kind: 'transfer', pn, source: sources[0], parent });
        return;
      }
      if (sources.length > 1) {
        setFlow({ kind: 'source-select', pn, sources, parent });
        return;
      }
      // No active WO Demand and no active quantity: intake flow
      // (equivalent to Work Orders "Add Part") — MODIFY + FLOATING
      // defaults, both editable. The PN is created on first valid use.
      setFlow({ kind: 'intake', pn, parent });
    },
    [cardsFor, sourcesFor, refreshSession],
  );

  const handleScan = useCallback(() => {
    const input = inputRef.current;
    if (!input) return;
    if (writeBlocked) {
      blockedNotice();
      return;
    }
    const raw = input.value;
    input.value = '';
    // Refocus only in the branches that stay on the scan surface — the
    // branches that open a dialog must leave focus to the dialog (the
    // input regains focus through closeFlow/cancelFlow afterwards).
    const parsed = parseScan(raw);
    switch (parsed.kind) {
      case 'empty':
        focusScan();
        return;
      case 'scrap':
        focusScan();
        setNotice({
          kind: 'err',
          icon: '✕',
          title: 'Scrap barcode cannot be used here',
          detail:
            'Scan the Part Number, select “Scrap damaged quantity,” then scan the scrap barcode. No changes were recorded.',
        });
        return;
      case 'machine': {
        const machine = MOCK_MACHINE_BARCODES[parsed.id];
        if (!machine) {
          focusScan();
          setNotice({
            kind: 'err',
            icon: '✕',
            title: 'Machine barcode not recognized',
            detail:
              'Check the Machine barcode and scan again. No changes were recorded.',
          });
          return;
        }
        if (machine.area !== station.area) {
          focusScan();
          setNotice({
            kind: 'err',
            icon: '✕',
            title: `${machine.machine} belongs to another Area`,
            detail:
              'This Machine is assigned to a different Area and cannot be used at this station. No changes were recorded.',
          });
          return;
        }
        const status = machines.find((m) => m.name === machine.machine)?.status;
        if (status === 'maintenance') {
          focusScan();
          setNotice({
            kind: 'err',
            icon: '✕',
            title: `${machine.machine} is unavailable for production`,
            detail:
              'This Machine is under maintenance. Select another Machine or contact a supervisor.',
          });
          return;
        }
        // One-shot shortcut only: opens the Machine assignment dialog
        // with the Machine preselected. There is NO Machine Session.
        // A successfully resolved Machine scan is a valid production
        // interaction — it slides the Worker Session deadline.
        refreshSession();
        setFlow({
          kind: 'machine-assign',
          machine: machine.machine,
          pn: null,
        });
        return;
      }
      case 'area':
        focusScan();
        setNotice({
          kind: 'err',
          icon: '✕',
          title: 'Area barcode is not required here',
          detail:
            'This Scan Station is already assigned to an Area. Scan a Part Number, Worker badge, or Machine barcode. No changes were recorded.',
        });
        return;
      case 'pn':
        // Every PN resolution opens a dialog — no refocus here.
        openPnFlow(parsed.pn);
        return;
      case 'unknown': {
        focusScan();
        // Worker badges are non-PF values (the company's existing
        // employee badge, PROJECT_PROFILE §10): an unrecognized value
        // is first exact-matched against ACTIVE Worker badge barcodes.
        // Unknown or inactive badges fall through to the rejection —
        // never guessed, and a rejected scan never refreshes the
        // Worker Session deadline.
        const badgeWorker = workerByBadge(parsed.raw);
        if (badgeWorker) {
          if (workerMode !== 'scanned') {
            setNotice({
              kind: 'warn',
              icon: '⚠',
              title: 'Worker badge scans are not used in this Area',
              detail:
                workerMode === 'fixed'
                  ? 'This Area records its configured Worker automatically. No changes were recorded.'
                  : 'This Area does not record Worker identity. No changes were recorded.',
            });
            return;
          }
          // A Worker scan never replaces the Last Scanned PN; scanning
          // a different Worker's badge switches immediately (§19).
          signInWorker(badgeWorker);
          return;
        }
        setNotice({
          kind: 'err',
          icon: '✕',
          title: 'Barcode not recognized',
          detail:
            'Scan a PartFlow Part Number or Machine barcode, or a registered Worker badge. To type a Part Number, select “Enter PN manually.” No changes were recorded.',
        });
        return;
      }
    }
  }, [
    writeBlocked,
    blockedNotice,
    focusScan,
    machines,
    station.area,
    openPnFlow,
    refreshSession,
    signInWorker,
    workerMode,
  ]);

  // Development-only: a click on a DemoBarcode in the DevNotice is the
  // exact equivalent of a keyboard-wedge scan ending in Enter — the
  // value lands in the main barcode input and goes through the SAME
  // handleScan()/parseScan() path (validation, notices, dialogs).
  // While writes are blocked the value is NOT staged (mirroring the
  // wedge capture, which is inert then); handleScan still runs so the
  // click gets the same blocked notice a real scan would.
  const simulateScan = useCallback(
    (value: string) => {
      const input = inputRef.current;
      if (input && !writeBlocked) input.value = value;
      handleScan();
    },
    [writeBlocked, handleScan],
  );

  // Keyboard-wedge capture at the Scan Station level: when no dialog
  // is open, a scanned barcode reaches the main input even when the
  // input is not focused. The first character is captured (never
  // lost), focus then follows the scan; Enter submits exactly once.
  // Typing inside any other input/textarea/select/contenteditable, an
  // active dialog workflow, modifier shortcuts, and normal button
  // activation are all left alone.
  useEffect(() => {
    // While session-blocked the badge modal owns scanning (its own
    // input is focused); the main-input capture stays inert.
    if (flow || writeBlocked || sessionBlocked) return;
    function onKeyDown(event: KeyboardEvent) {
      const input = inputRef.current;
      if (!input || event.defaultPrevented) return;
      if (event.ctrlKey || event.altKey || event.metaKey) return;
      const target = event.target;
      if (target === input) return; // native typing already lands here
      if (target instanceof HTMLElement) {
        if (target.isContentEditable) return;
        const tag = target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        // Enter/Space on a button stays button activation.
        if (tag === 'BUTTON') return;
      }
      if (event.key === 'Enter') {
        if (input.value) {
          event.preventDefault();
          handleScan();
        }
        return;
      }
      if (event.key.length === 1 && event.key !== ' ') {
        event.preventDefault();
        input.value += event.key;
        input.focus();
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [flow, writeBlocked, sessionBlocked, handleScan]);

  // Ctrl+Shift+K — convenience toggle between this station's standard
  // and production routes for authorized users. The dedicated route and
  // the explicit Station Selector actions stay the primary entry; the
  // shortcut never fires inside text inputs, selects, or an active
  // dialog, never conflicts with barcode capture (which ignores
  // modifier chords), and is never a security mechanism.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!event.ctrlKey || !event.shiftKey || event.altKey || event.metaKey) {
        return;
      }
      if (event.key !== 'K' && event.key !== 'k') return;
      if (flow) return;
      const target = event.target;
      if (target instanceof HTMLElement) {
        const tag = target.tagName;
        // The main barcode input is a scanner target, not text entry —
        // the chord stays usable while it holds focus (which it almost
        // always does, §3.1); every other field keeps the chord inert.
        if (
          (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') &&
          target !== inputRef.current
        ) {
          return;
        }
        if (target.isContentEditable || target.closest('[role="dialog"]')) {
          return;
        }
      }
      event.preventDefault();
      navigate(
        productionMode
          ? `/scan-station/${station.stationId}`
          : `/scan-station/${station.stationId}/production`,
      );
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [flow, navigate, productionMode, station.stationId]);

  function undoTarget(): MockCompletedAction | null {
    return eligible?.action ?? null;
  }

  function confirmUndo() {
    const entryIndex = history.findIndex((h) => !h.reversed);
    if (entryIndex < 0) return;
    const entry = history[entryIndex];
    // Whole-command reversal: the pre-command state returns, covering
    // every Movement the command appended (e.g. AREA_COMPLETED +
    // TRANSFERRED together).
    setAllCards(structuredClone(entry.before));
    setHistory((current) =>
      current.map((h, i) => (i === entryIndex ? { ...h, reversed: true } : h)),
    );
    setFlow(null);
    setNotice({
      kind: 'warn',
      icon: '⟲',
      title: `Last action reversed — ${entry.action.pn}`,
      detail: `${entry.action.reversalEffect} The original history remains available for audit.`,
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
          title: 'Barcode not recognized',
          detail:
            'Unknown or invalid scans never update tracking data. No changes were recorded.',
        }
      : notice;

  // Row actions. Machine-card rows carry the two distinct one-shot
  // actions — DONE completes Area processing and moves the quantity to
  // the finished rack; QUEUE returns unfinished or paused quantity to
  // the Area queue. They are never merged. The shared DONE button is
  // one presentation for both surfaces (success tone, icon above the
  // label, accessible name `Complete Area processing`); while writes
  // are blocked it stays disabled in place — the rail keeps its layout
  // and no workflow that could record a false state can open.
  const doneRowAction = (entry: AreaAssignment, machine: string | null) => (
    <button
      className="rowact done"
      aria-label="Complete Area processing"
      title="Complete processing — move this quantity to the finished rack, ready to transfer"
      disabled={writeBlocked || sessionBlocked}
      onClick={() =>
        setFlow({
          kind: 'done',
          pn: entry.card.pn,
          machine,
          max: entry.qty,
        })
      }
    >
      <span className="ric" aria-hidden="true">
        ✓
      </span>
      DONE
    </button>
  );

  const machineRowAction = (entry: AreaAssignment) => (
    <>
      {doneRowAction(entry, entry.context)}
      <button
        className="rowact"
        aria-label="Return to Area queue"
        title="Return unfinished or paused quantity to the Area queue"
        disabled={writeBlocked || sessionBlocked}
        onClick={() =>
          setFlow({
            kind: 'queue-return',
            pn: entry.card.pn,
            machine: entry.context,
            max: entry.qty,
          })
        }
      >
        <span className="ric" aria-hidden="true">
          ⟲
        </span>
        QUEUE
      </button>
    </>
  );

  // `In this Area now` row actions. In an Area WITH Machines the card
  // carries NO row actions (assignment stays available through PN
  // scan, Machine scan and the action dialog; DONE / QUEUE live in the
  // Machine cards). A direct-processing Area (no Machines) is the
  // deliberate exception: its actively processing quantity has no
  // Machine card to act from, so exactly those rows carry the single
  // DONE action — the same manual DONE wizard with Machine = NULL.
  // There is no QUEUE here: a direct-processing Area has no Machine
  // queue to return quantity to. Visibility is decided by explicit
  // semantic data — the Area's Machine-less non-terminal mode plus the
  // portion's actively processing state (`direct`, or `vendor` for
  // External processing) — never by CSS selectors or Area names;
  // finished (`READY_TO_TRANSFER`) portions and quantity no longer in
  // the Area never carry the action. Management → Area Board renders
  // the same shared card without any rowAction and stays read-only.
  const directRowAction =
    hasMachines || area?.terminal
      ? undefined
      : (entry: AreaAssignment) =>
          (entry.state === 'direct' || entry.state === 'vendor') &&
          entry.qty > 0
            ? doneRowAction(entry, null)
            : null;

  return (
    <section
      className={`ss${productionMode ? ' production' : ''}`}
      aria-label="Scan Station"
    >
      {/* Header grid: the left Area identity group and the Worker
          Session always share the main row; the Area totals sit
          between them while space allows and drop to a full-width
          second row (equal cells) only when the measured single-row
          layout genuinely cannot fit (`wrapped` class from the fit
          measurement above — no hard-coded breakpoint). The header is
          the single summary surface for the Area totals — the `In
          this Area now` card carries no statistics block. */}
      <header
        ref={headRef}
        className={headWrapped ? 'ss-head wrapped' : 'ss-head'}
      >
        <div className="ss-id">
          <div className="dept">{station.department}</div>
          <div className="area">
            <AreaDot colorVar={area?.colorVar ?? 'var(--faint)'} size={16} />
            {area?.name}
          </div>
          <div className="op">
            Operations:{' '}
            <span className="opchips">
              {(area?.operations ?? []).map((op) => (
                <span className="opchip" key={op}>
                  {op}
                </span>
              ))}
            </span>
          </div>
        </div>
        <div className="ss-stats" aria-label="Area statistics">
          {(area ? areaStats(area, areaCards, hasMachines) : []).map((s) => (
            <div className="stat" key={s.label}>
              <div className={`n ${s.tone ?? ''}`}>{s.value}</div>
              <div className="l">{s.label}</div>
            </div>
          ))}
        </div>
        {/* The top application navigation is hidden in production
            mode, so the connectivity status chip and the global
            Dark/Light mode control move next to the Worker Session
            pill — connectivity must stay visible and the theme must
            stay reachable. Standard mode keeps both in the top
            navigation. Inside the shared `.ss-headgroup` wrapper the
            Worker Session pill keeps its NATURAL height and drives the
            group height; the actions column stretches to that height
            and distributes its two compact controls inside it — the
            ONLINE chip's top aligns with the pill's top edge, the
            theme control's bottom with the pill's bottom edge. No
            enclosing frame and no separator around the actions. */}
        {productionMode ? (
          <div className="ss-headgroup">
            <WorkerPill
              mode={workerMode}
              worker={activeWorker}
              expiresAt={
                workerMode === 'scanned' && session && !sessionExpired
                  ? session.expiresAt
                  : null
              }
            />
            <div className="ss-headactions">
              <ConnectivityChip />
              <ThemeToggle compact />
            </div>
          </div>
        ) : (
          <WorkerPill
            mode={workerMode}
            worker={activeWorker}
            expiresAt={
              workerMode === 'scanned' && session && !sessionExpired
                ? session.expiresAt
                : null
            }
          />
        )}
      </header>

      <div className="ss-body">
        <div className="panel">
          <div className="ph">
            Scan barcode
            <span className="spacer" />
            <span className="note">
              Scan a Part Number or Worker badge. Scanning a Machine opens a
              one-time assignment.
            </span>
          </div>
          <div className="ss-scanwrap">
            <div className="ss-scanrow">
              <input
                ref={inputRef}
                className="ss-scaninput"
                autoComplete="off"
                // Touch-primary devices suppress the native soft
                // keyboard for this scanner-driven input (GUI_DESIGN
                // §4.8, shared touch-device detection): keyboard-wedge
                // scanners and physical keyboards fire real key events
                // regardless of inputMode. Ordinary text inputs — the
                // manual PN entry dialog, reasons, notes — keep their
                // normal soft keyboard.
                inputMode={touchPrimary ? 'none' : undefined}
                disabled={writeBlocked || sessionBlocked}
                placeholder={
                  disconnected
                    ? 'Disconnected — scanning disabled'
                    : status === 'connecting'
                      ? 'Connecting…'
                      : 'Scan Part Number, Worker, or Machine barcode · Press Enter'
                }
                aria-label="Scan barcode"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleScan();
                }}
              />
              <button
                className="ss-manualbtn"
                onClick={() => setFlow({ kind: 'manual-pn' })}
                disabled={writeBlocked || sessionBlocked}
              >
                ⌨ Enter PN manually
              </button>
            </div>
            {/* Fixed content order — DOM order = visual = keyboard =
                screen-reader order (never reordered by CSS): scan row,
                the manual-entry fallback explanation directly beneath
                it, the development demo barcodes, then Last scanned
                PN / Undo. */}
            <div className="ss-manualcap">
              Use manual entry only when the scanner is unavailable. The Part
              Number will be validated before any action is recorded.
            </div>
            {/* Development-only demo barcodes: the shared DevNotice
                renders only in the dev build (the whole mock view is
                also excluded from production bundles — see
                app/dev-views.ts and the mock-sentinel build check). */}
            <DevNotice>
              Demo barcodes (development build only) — click one to simulate a
              scan — <code>PF:PN:&lt;part-number&gt;</code> (e.g.{' '}
              <DemoBarcode
                value="PF:PN:2027-60-8114-00"
                onScan={simulateScan}
              />{' '}
              in this Area ·{' '}
              <DemoBarcode value="PF:PN:118-052" onScan={simulateScan} />{' '}
              elsewhere ·{' '}
              <DemoBarcode value="PF:PN:NEW-PART-01" onScan={simulateScan} />{' '}
              unknown → intake) ·{' '}
              <DemoBarcode value="PF:MACHINE:CD-0105" onScan={simulateScan} />{' '}
              assign to Machine ·{' '}
              <DemoBarcode value="100517" onScan={simulateScan} /> worker badge
            </DevNotice>
            {/* Compact section label OUTSIDE the block (uppercase via
                CSS), then one quiet row: PN + movement summary, a
                standalone separator, and the borderless Undo text
                action. */}
            {/* Two explicit regions — the same division as a Station
                Selector card: the information region fills the
                remaining space, the Undo ACTION REGION is the block's
                complete right edge (the button itself), separated by
                its own inset vertical rule — no separator element. */}
            <div className="ss-lastpnlabel">Last Action</div>
            <div className="ss-lastpn">
              <div className="ss-lastpninfo">
                <span className="p">{lastPn?.pn ?? '—'}</span>
                <span className="d">
                  {lastPn
                    ? `${lastPn.movements.join(' + ')} · ${lastPn.description}`
                    : 'No Part Number actions yet'}
                </span>
              </div>
              <button
                className="ss-undo zone-action"
                disabled={writeBlocked || sessionBlocked || !eligible}
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
                rowAction={directRowAction}
                showStats={false}
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
          machines={machines}
          initialMachine={flow.machine}
          initialPn={flow.pn}
          queuedQtyFor={queuedQtyFor}
          areaCards={areaCards}
          worker={workerName}
          onBack={backTo(flow.parent)}
          onApply={applyCommand}
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
          directQty={hasMachines ? 0 : processingQtyFor(flow.pn)}
          sources={sourcesFor(flow.pn)}
          repairSources={repairSourcesFor(flow.pn)}
          inAreaQty={cardsFor(flow.pn).reduce((s, c) => s + c.qty, 0)}
          // Every picked child flow records THIS dialog (with its own
          // parent chain) as the step Back returns to.
          onPick={(next) => setFlow({ ...next, parent: flow })}
          onBack={backTo(flow.parent)}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'source-select' && (
        <SourceSelectDialog
          pn={flow.pn}
          sources={flow.sources}
          onPick={(source) =>
            setFlow({ kind: 'transfer', pn: flow.pn, source, parent: flow })
          }
          onBack={backTo(flow.parent)}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'transfer' && (
        <TransferDialog
          station={station}
          pn={flow.pn}
          source={flow.source}
          hasMachines={hasMachines}
          worker={workerName}
          onBack={backTo(flow.parent)}
          onApply={applyCommand}
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
          worker={workerName}
          onBack={backTo(flow.parent)}
          onApply={applyCommand}
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
          worker={workerName}
          onBack={backTo(flow.parent)}
          onApply={applyCommand}
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
          worker={workerName}
          onBack={backTo(flow.parent)}
          onApply={applyCommand}
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
          worker={workerName}
          onBack={backTo(flow.parent)}
          onApply={applyCommand}
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
          worker={workerName}
          onApply={applyCommand}
          onNotice={setNotice}
          onClose={closeFlow}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'done' && (
        <DoneDialog
          station={station}
          pn={flow.pn}
          machine={flow.machine}
          max={flow.max}
          worker={workerName}
          onBack={backTo(flow.parent)}
          onApply={applyCommand}
          onNotice={setNotice}
          onClose={closeFlow}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'undo' && undoTarget() && (
        <UndoConfirmDialog
          target={undoTarget()!}
          reversedBy={workerName}
          onConfirm={confirmUndo}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'manual-pn' && (
        <ManualEntryDialog
          initialPn={flow.initialPn}
          onCancel={cancelFlow}
          onConfirm={(pn) => {
            // `pn` is already the canonical PN ('' when the entry was
            // empty — treated like a cancelled entry, no write).
            setFlow(null);
            if (!pn) {
              focusScan();
              return;
            }
            // The resolved dialog can go Back to manual entry with the
            // canonical PN preserved for correction.
            openPnFlow(pn, { kind: 'manual-pn', initialPn: pn });
          }}
        />
      )}

      {/* Scanned-session badge modal (§4.12): rendered LAST so it sits
          above any open production dialog — the dialog's draft and
          selections stay preserved underneath while confirmation is
          blocked. It blocks only the Scan Station; it cannot be
          dismissed without a valid badge scan. */}
      {sessionBlocked && (
        <WorkerSignInDialog
          expired={session !== null}
          writeBlocked={writeBlocked}
          onSignIn={(badgeWorker) => {
            signInWorker(badgeWorker);
            // Continue where the workflow was: focus returns into the
            // open dialog if one exists, else the main barcode input.
            focusScan();
          }}
        />
      )}

      {/* One coherent non-interactive footer for both modes: the faint
          Station ID, a subtle mode label, and the mode-switch shortcut
          hint. Returning to the Station Selector is the top
          navigation's Scan Station entry — the footer never duplicates
          it, and there is no casual route away from a configured
          station (the browser itself stays outside PartFlow's
          control). */}
      <footer className="ss-stationfoot">
        Station <span className="mono">{station.stationId}</span>
        <span className="ss-modetag">
          {productionMode ? 'Production mode' : 'Standard mode'}
        </span>
        <span className="ss-foothint">Ctrl+Shift+K: switch mode</span>
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
  /** Recorded Worker per the Area's Worker ID mode; null = Disabled. */
  worker: string | null;
  /** Apply one confirmed application command (atomic in mock state). */
  onApply: (command: Command) => void;
  onNotice: (n: Notice) => void;
  onClose: (message?: string) => void;
  onCancel: () => void;
}

/** Optional row emphasis for the shared confirmation summary. */
type SummaryEmphasis = 'primary' | 'secondary';

/**
 * Controlled semantic tone for a summary VALUE (v15). Additive only:
 * emphasis (weight/size) still carries the hierarchy, so tone is never
 * the only distinction. `ok` marks a successful/recorded result,
 * `warn` a deviation worth noticing, `err` a destructive result.
 * Audit context (Worker, Scan Station, timestamps) stays `secondary`
 * muted; Area identity is shown with an AreaDot beside plain text, not
 * by recoloring the text (area hues are not readable as body text in
 * both themes).
 */
type SummaryTone = 'ok' | 'warn' | 'err';

type SummaryRow = [
  string,
  ReactNode,
  (SummaryEmphasis | undefined)?,
  (SummaryTone | undefined)?,
];

/**
 * Structured confirmation summary — the dedicated final view of every
 * production wizard. Two columns (term / value); rows without a value
 * are omitted. Never a single interpolated sentence.
 *
 * A small optional row-emphasis mechanism keeps the primary
 * operational values (PN, Quantity, Action, Source, Destination,
 * Machine, Area, Operation, Reason) easy to scan and quiets the
 * secondary audit/context rows (Worker, Scan Station, recorded event
 * names, explanatory notes) — nothing is hidden, labels never look
 * like buttons, and the distinction never relies on color alone
 * (weight and size carry it in both themes; the optional value tone is
 * additive on top).
 */
function ConfirmationSummary({ rows }: { rows: SummaryRow[] }) {
  return (
    <dl className="ss-confirm">
      {rows
        .filter((row) => row[1] !== null && row[1] !== undefined)
        .map(([label, value, emphasis, tone]) => (
          <Fragment key={label}>
            <dt className={emphasis ?? ''}>{label}</dt>
            <dd className={`${emphasis ?? ''}${tone ? ` tone-${tone}` : ''}`}>
              {value}
            </dd>
          </Fragment>
        ))}
    </dl>
  );
}

/**
 * Subtle non-interactive chip for a selected entity (Machine, Area,
 * Operation, source, Route Mode) inside recaps and confirmation
 * summaries — a label, never a control.
 */
function EntityChip({ children }: { children: ReactNode }) {
  return <span className="dlgchip">{children}</span>;
}

/**
 * Area entity inside recaps and confirmation summaries: the stable
 * Area identity dot beside plain text (v15). The dot carries the Area
 * color; the text keeps the normal value color so Area identity never
 * depends on recoloring body text.
 */
function AreaChip({
  areaKey,
  children,
}: {
  areaKey: string;
  children: ReactNode;
}) {
  return (
    <EntityChip>
      <AreaDot colorVar={areaByKey(areaKey)?.colorVar ?? 'var(--faint)'} />
      {children}
    </EntityChip>
  );
}

/**
 * Worker pill — shared by both header layouts, following the Area's
 * Worker ID mode (GUI_DESIGN §4.3, post-v18 — there is no shift-end
 * concept): Scanned session shows the active Worker's avatar + name
 * with the live `Worker session · 12m remaining` countdown; Fixed
 * Worker shows the configured Worker with a static `Fixed Worker` sub
 * line; Disabled is one quiet muted line.
 */
function WorkerPill({
  mode,
  worker,
  expiresAt,
}: {
  mode: WorkerIdMode;
  worker: MockWorker | null;
  /** Sliding session deadline (scanned mode with a valid session). */
  expiresAt: number | null;
}) {
  if (mode === 'disabled') {
    return (
      <div className="ss-pill off">
        <span className="val muted">Worker ID disabled</span>
      </div>
    );
  }
  return (
    <div className="ss-pill">
      <span className="val">
        {worker ? (
          <span className="avatar" aria-hidden="true">
            {worker.avatar}
          </span>
        ) : null}
        {worker?.name ?? 'No Worker signed in'}
      </span>
      {mode === 'fixed' ? (
        <span className="sub">Fixed Worker</span>
      ) : (
        <SessionCountdown expiresAt={expiresAt} />
      )}
    </div>
  );
}

/**
 * Blocking badge-scan modal of a Scanned-session Area (GUI_DESIGN
 * §4.12): shown when the station is opened without an active Worker
 * Session and when the session expires. Only the Scan Station is
 * blocked; Escape and backdrop clicks never dismiss it — a valid badge
 * scan is the only way through. Never `unlock` wording.
 */
function WorkerSignInDialog({
  expired,
  writeBlocked,
  onSignIn,
}: {
  /** true after a session expired; false on first open (no session). */
  expired: boolean;
  writeBlocked: boolean;
  onSignIn: (worker: MockWorker) => void;
}) {
  const fieldRef = useRef<HTMLInputElement>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  useEffect(() => {
    fieldRef.current?.focus();
  }, []);
  const title = expired ? 'Worker session expired' : 'Worker sign-in required';
  function submit() {
    const value = normalizeScanInput(fieldRef.current?.value ?? '');
    if (fieldRef.current) fieldRef.current.value = '';
    if (!value) return;
    // Exact match against ACTIVE Worker badge barcodes only — an
    // unknown, inactive, or non-badge value is rejected with nothing
    // recorded, and never refreshes anything.
    const worker = workerByBadge(value);
    if (!worker) {
      setScanError(
        'Badge not recognized. Check the badge and scan again — nothing was recorded.',
      );
      return;
    }
    onSignIn(worker);
  }
  return (
    <ModalDialog
      label={title}
      // Deliberately not dismissable: the close request is ignored —
      // the station stays blocked until a valid badge scan.
      onClose={() => {}}
    >
      <h3>{title}</h3>
      <div className="sub">Scan your badge to continue.</div>
      <input
        aria-label="Scan Worker badge"
        ref={fieldRef}
        className="field mono"
        autoComplete="off"
        disabled={writeBlocked}
        placeholder={
          writeBlocked
            ? 'Disconnected — scanning disabled'
            : 'Scan Worker badge · Press Enter'
        }
        onChange={() => setScanError(null)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit();
        }}
      />
      {scanError ? <Guidance tone="error">{scanError}</Guidance> : null}
      <DevNotice>
        Demo badges (development build only) — type one and press Enter:{' '}
        <code>100482</code> H. Nguyen · <code>100517</code> V. Tran
      </DevNotice>
    </ModalDialog>
  );
}

/** Near-expiration warning threshold of the session countdown. */
const SESSION_WARN_MS = 2 * 60_000;

/**
 * Live remaining-session line of the Worker pill, derived from the
 * sliding deadline plus the ONE shared UI clock (§3.12) — never a
 * component-owned timer. Isolated in a leaf component so only the pill
 * re-renders per tick.
 */
function SessionCountdown({ expiresAt }: { expiresAt: number | null }) {
  // The shared tick drives the per-second re-renders; the remaining
  // time derives from the wall clock at render so a render triggered
  // between ticks (badge scan, confirmed command) is never one stale
  // tick behind — the §3.12 clamping rule for values newer than the
  // tick.
  const tick = useUiClock('second');
  const now = Math.max(tick, Date.now());
  if (expiresAt === null) {
    return <span className="sub">Worker session · scan badge</span>;
  }
  const remaining = expiresAt - now;
  if (remaining <= 0) {
    return <span className="sub warn">Worker session · expired</span>;
  }
  const label =
    remaining >= 60_000
      ? `${Math.ceil(remaining / 60_000)}m remaining`
      : `${Math.max(1, Math.ceil(remaining / 1_000))}s remaining`;
  return (
    <span className={remaining <= SESSION_WARN_MS ? 'sub warn' : 'sub'}>
      Worker session · {label}
    </span>
  );
}

/** Markers keep the guidance kinds apart without relying on color. */
const GUIDE_MARKERS: Record<'info' | 'warn' | 'action' | 'error', string> = {
  info: 'ℹ',
  warn: '⚠',
  action: '›',
  error: '✕',
};

/**
 * Semantic quantity/step guidance directly above the related input or
 * choice. Four kinds only (§3.10, v15 — the former marker-less
 * `neutral` kind is retired): instructions and information are `info`,
 * important constraints and deviations are `warn`, required next
 * actions are `action`, and validation errors are `error`. Every kind
 * carries a small marker plus an accent edge — color is never the
 * only distinction, and validation reads stronger than any
 * instruction. Deliberately light: never a large framed card.
 */
function Guidance({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'warn' | 'action' | 'error';
  children: ReactNode;
}) {
  return (
    <div className={`ss-guide ${tone}`}>
      <span className="gmark" aria-hidden="true">
        {GUIDE_MARKERS[tone]}
      </span>
      <span className="gtext">{children}</span>
    </div>
  );
}

/**
 * Split a mock Work Order context label (`WO 007003 · Turning`,
 * `WO — · Turning · MODIFY`) into the WO Number value and its trailing
 * context segments — the ONE place this display string is taken apart,
 * so recaps can emphasize the number and chip the Operation instead of
 * echoing the raw string.
 */
function parseWorkOrderLabel(label: string): {
  number: string;
  segments: string[];
} {
  const [head, ...segments] = label.split(' · ');
  return { number: head.replace(/^WO\s*/, '') || '—', segments };
}

/**
 * Work Order context line for recaps: `WO` stays a plain label, the
 * WO Number value carries the shared `.rval` emphasis (`—` for an
 * internal blank number), the Operation renders as the shared entity
 * chip, and a NEW/MODIFY segment stays the shared Request Type chip.
 */
function WorkOrderRecapLine({ workOrder }: { workOrder: string }) {
  const { number, segments } = parseWorkOrderLabel(workOrder);
  return (
    <>
      WO <b className="rval">{number}</b>
      {segments.map((segment, index) => (
        <Fragment key={`${segment}-${index}`}>
          {' · '}
          {segment === 'NEW' || segment === 'MODIFY' ? (
            <TypeChip type={segment} />
          ) : (
            <EntityChip>{segment}</EntityChip>
          )}
        </Fragment>
      ))}
    </>
  );
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
 * with no write (the standard label is exactly `Cancel (Esc)`), and
 * the primary button names the actual operation.
 */
function StepButtons({
  onBack,
  onCancel,
  cancelLabel = 'Cancel (Esc)',
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
 * Central physical-key handling for quantity steps: Enter advances (to
 * the next step — never directly to a write), Escape cancels
 * (ModalDialog), and while the quantity input itself is NOT focused
 * the editing keys fall back to end-of-value editing (digits append,
 * Backspace removes the last digit, Delete clears, Space is ignored).
 * While the quantity input IS focused, the shared QuantityKeypad owns
 * cursor-aware editing (selection replacement / caret insertion — the
 * same transitions, see components/quantity-input.ts) and consumes
 * those keys before they reach this handler. Keys typed into other
 * text fields (reason, notes, scan-within-dialog) are left alone.
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
    if (
      target instanceof HTMLInputElement &&
      target.classList.contains('qtydisplay')
    ) {
      // The focused quantity input already handled (and consumed) its
      // cursor-aware editing keys; anything still bubbling from it is
      // intentionally left alone.
      return;
    }
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

/** Standard quantity validation message for a 1..max range: the range
 *  prompt is a required action; exceeding the limit is a validation
 *  error and reads visually stronger. */
function quantityValidation(
  parsed: number,
  max: number,
  overMessage: string,
): { tone: 'action' | 'error'; text: string } | null {
  if (parsed > max) return { tone: 'error', text: overMessage };
  if (parsed < 1) {
    return {
      tone: 'action',
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
  machines,
  initialMachine,
  initialPn,
  queuedQtyFor,
  areaCards,
  worker,
  onBack,
  onApply,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & {
  /** Session-derived Machine monitoring state of this Area — the
   * statuses shown and checked here follow the current assignments. */
  machines: readonly MockAreaMachine[];
  initialMachine: string | null;
  initialPn: string | null;
  queuedQtyFor: (pn: string) => number;
  areaCards: MockAreaCard[];
  /** Back from Step 1 to the parent dialog (PN action dialog); absent
   * for the Machine-scan entry, which has no previous dialog step. */
  onBack?: () => void;
}) {
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

  // Viewport-fit decision for the queued-PN selection (Machines always
  // stay explicit buttons). PN buttons are the default; the compact
  // dropdown appears ONLY while the dialog's NATURAL height with the
  // full PN button layout exceeds the dialog's viewport height cap
  // (the `.dlg` max-height, which already carries the required margin
  // to the viewport edges) — never a PN-count threshold. The invisible
  // probe copy always lays out the buttons at the cell's width, so the
  // measurement is independent of the currently applied mode and the
  // dialog returns to buttons as soon as a larger viewport fits them.
  const pnCellRef = useRef<HTMLDivElement>(null);
  const pnProbeRef = useRef<HTMLDivElement>(null);
  const [pnCombobox, setPnCombobox] = useState(false);
  const measurePnFit = useCallback(() => {
    const cell = pnCellRef.current;
    const probe = pnProbeRef.current;
    const dlg = cell?.closest('.dlg');
    if (!cell || !probe || !(dlg instanceof HTMLElement)) return;
    const cap = Number.parseFloat(getComputedStyle(dlg).maxHeight);
    if (!Number.isFinite(cap) || cap <= 0) {
      // No measurable viewport cap (no-layout environments — tests):
      // keep the default button presentation.
      setPnCombobox(false);
      return;
    }
    // Natural dialog height with PN buttons: the current content
    // height with the visible PN control's height replaced by the
    // probe's button layout height.
    const naturalWithButtons =
      dlg.scrollHeight - cell.offsetHeight + probe.offsetHeight;
    // Half-pixel tolerance so subpixel rounding never flips the mode.
    setPnCombobox(naturalWithButtons > cap + 0.5);
  }, []);
  // Re-evaluate whenever the measured content can change: entering the
  // selection view (also on Back) and a changed queued-PN list.
  useLayoutEffect(() => {
    if (step === 'select') measurePnFit();
  }, [step, queuedPns.length, measurePnFit]);
  // …and whenever the viewport size changes.
  useEffect(() => {
    const onResize = () => measurePnFit();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [measurePnFit]);

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
      // parsed.pn is the canonical PN — the identity itself.
      if (queuedQtyFor(parsed.pn) < 1) {
        setScanError(
          `${parsed.pn} has no queued quantity in this Area — selection unchanged.`,
        );
        return;
      }
      setPn(parsed.pn);
      setScanError(null);
      return;
    }
    setScanError(
      'Scan a Machine in this Area or a Part Number currently waiting in the Area queue. Your current selections were kept.',
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
    const selectedMachine = machine;
    const selectedPn = pn;
    onApply({
      action: {
        pn: selectedPn,
        movements: ['ASSIGNED_TO_MACHINE'],
        description: `${station.area} queue → ${selectedMachine} · qty ${parsedQty}`,
        qty: parsedQty,
        source: 'Area queue',
        destination: selectedMachine,
        machine: selectedMachine,
        worker,
        time: MOCK_SCAN_TIME,
        reversalEffect: `Returns ${parsedQty} pcs from ${selectedMachine} to the Area queue.`,
      },
      update: (cards) =>
        applyAssign(cards, {
          pn: selectedPn,
          area: station.area,
          machine: selectedMachine,
          qty: parsedQty,
        }),
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `${selectedPn} × ${parsedQty} → ${selectedMachine}`,
      detail: `${selectedMachine} is now processing this quantity. Scan the next barcode when ready.`,
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
    // User-facing name is `Assign to Machine`; internally this stays the
    // one-shot assignment wizard — no Machine context survives it.
    <ModalDialog
      label="Assign to Machine"
      onClose={onCancel}
      size="wide"
      onKeyDown={keys}
    >
      <h3>Assign to Machine</h3>
      {step === 'select' ? (
        <>
          <div className="sub">
            Select a Machine and a queued Part Number, then enter the quantity
            to assign. This assignment applies once and closes after
            confirmation.
          </div>
          <input
            ref={scanRef}
            className="field mono"
            autoComplete="off"
            placeholder="Scan machine or queued part barcode… (ENTER)"
            aria-label="Scan machine or queued part barcode"
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return;
              // One Enter has exactly one meaning: a filled input is a
              // selection scan, an empty input advances once the
              // Machine + PN pair is selected. Consuming the event in
              // every case keeps the dialog-level handler from acting
              // on the same keypress (never scan AND advance at once).
              e.preventDefault();
              e.stopPropagation();
              const value = e.currentTarget.value;
              if (value) {
                handleSelectScan(value);
                e.currentTarget.value = '';
                return;
              }
              if (pairSelected) goQty();
            }}
          />
          {scanError ? <Guidance tone="error">{scanError}</Guidance> : null}
          <div className="ss-dlgrid">
            <span className="lbl" id="ma-machine-lbl">
              Machine
            </span>
            {/* Machines are ALWAYS explicit selection buttons — never a
                dropdown; an Area's Machine list stays small and wraps. */}
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
                      ? 'Under maintenance · Unavailable for production'
                      : undefined
                  }
                  onClick={() => setMachine(m.name)}
                >
                  {m.name}
                  <span className="s">{m.status}</span>
                </button>
              ))}
            </div>
            {/* Light separator between the two selection groups —
                purely presentational grouping, no semantics. */}
            <div className="ss-dlgsep" aria-hidden="true" />
            <span className="lbl" id="ma-pn-lbl">
              PN <span className="field-note">(queued)</span>
            </span>
            {queuedPns.length === 0 ? (
              <span className="sub">No queued quantity in this Area.</span>
            ) : (
              <div className="ss-pncell" ref={pnCellRef}>
                {pnCombobox ? (
                  // The dialog with the full PN button layout would not
                  // fit the viewport right now — compact dropdown.
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
                        <span className="s">
                          queued {queuedQtyFor(queuedPn)}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
                {/* Invisible measurement copy of the full PN button
                    layout at the cell's width (same wrapping): its
                    height feeds the viewport-fit decision in BOTH
                    modes. Hidden from a11y and never interactive. */}
                <div className="ss-pnprobe" aria-hidden="true" ref={pnProbeRef}>
                  <div className="ss-choicerow">
                    {queuedPns.map((queuedPn) => (
                      <button
                        key={queuedPn}
                        type="button"
                        tabIndex={-1}
                        disabled
                        className="pickbtn mono"
                      >
                        <span className="pickpn">{queuedPn}</span>
                        <span className="s">
                          queued {queuedQtyFor(queuedPn)}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
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
                Assigning to <EntityChip>{machine}</EntityChip>. Queued
                quantity: <b>{max} pcs</b>.
              </>,
              <>
                Source: <EntityChip>Area queue</EntityChip> → Destination:{' '}
                <EntityChip>{machine}</EntityChip>
              </>,
            ]}
          />
          <Guidance tone="info">
            The full queued quantity is selected by default. Enter a smaller
            quantity if needed.
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
              ['Action', 'Assign to Machine', 'primary'],
              ['PN', <span className="mono">{pn}</span>, 'primary'],
              [
                'Quantity',
                <span className="mono">{parsedQty} pcs</span>,
                'primary',
              ],
              [
                'Source',
                <AreaChip areaKey={station.area}>Area queue</AreaChip>,
                'primary',
              ],
              [
                'Destination Machine',
                <EntityChip>{machine}</EntityChip>,
                'primary',
              ],
              [
                'Remaining queued after assignment',
                <span className="mono">{max - parsedQty} pcs</span>,
              ],
              ['Worker', worker, 'secondary'],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', 'ASSIGNED_TO_MACHINE', 'secondary'],
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
  directQty,
  sources,
  repairSources,
  inAreaQty,
  onPick,
  onBack,
  onCancel,
}: {
  pn: string;
  station: MockScanStation;
  hasMachines: boolean;
  queuedQty: number;
  /** Directly processing quantity (no-Machine Areas) eligible for DONE. */
  directQty: number;
  sources: SourceOption[];
  repairSources: {
    areaLabel: string;
    qty: number;
    flow: string;
    note: string;
  }[];
  inAreaQty: number;
  onPick: (flow: PnActionChildFlow) => void;
  /** Back to the parent step (manual PN entry); absent for scans. */
  onBack?: () => void;
  onCancel: () => void;
}) {
  const areaName = areaByKey(station.area)?.name ?? station.area;
  return (
    <ModalDialog label="Select an action" onClose={onCancel}>
      <h3>Select an action</h3>
      <div className="big mono">{pn}</div>
      <div className="sub">
        Only available actions are shown. No changes are recorded until you
        review and confirm an action.
      </div>
      {hasMachines && queuedQty > 0 ? (
        <button
          className="choice"
          onClick={() =>
            onPick({
              kind: 'machine-assign',
              machine: null,
              pn,
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
              {queuedQty} pcs waiting in the {areaName} queue — applies to one
              assignment; the full queued quantity is selected by default.
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
              {sources.map((s) => `${s.qty} pcs at ${s.areaLabel}`).join(' · ')}
              {'. '}Select the source Area before entering the quantity.
            </span>
          </span>
        </button>
      ) : null}
      {directQty > 0 ? (
        <button
          className="choice"
          onClick={() =>
            onPick({ kind: 'done', pn, machine: null, max: directQty })
          }
        >
          <span className="cic run" aria-hidden="true">
            DONE
          </span>
          <span>
            <span className="ct1">Complete Area processing</span>
            <br />
            <span className="ct2">
              {directQty} pcs in processing. The finished quantity moves to the
              finished rack, ready to transfer to another Area.
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
            Add physical quantity found at this Area that was not transferred
            from another Area. A reason is required.
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
            <span className="ct1">Return quantity for repair</span>
            <br />
            <span className="ct2">
              Return quantity to {areaName} so earlier work can be corrected.
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
              Scan {SCRAP_BARCODE} once for each damaged piece, then enter one
              reason for the total. Nothing is recorded until confirmation.
            </span>
          </span>
        </button>
      ) : null}
      <div className="row">
        {onBack ? (
          <button className="bigbtn ghost ss-back" onClick={onBack}>
            ‹ Back
          </button>
        ) : null}
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
        </button>
      </div>
    </ModalDialog>
  );
}

function SourceSelectDialog({
  pn,
  sources,
  onPick,
  onBack,
  onCancel,
}: {
  pn: string;
  sources: SourceOption[];
  onPick: (source: SourceOption) => void;
  /** Back to the parent step (PN action dialog or manual PN entry);
   * absent when the scan resolved directly to this selection. */
  onBack?: () => void;
  onCancel: () => void;
}) {
  return (
    <ModalDialog label="Select the source" onClose={onCancel}>
      <h3>Select the source</h3>
      <div className="big mono">{pn}</div>
      <div className="sub">
        This Part Number is available in more than one Area. Select one source
        to continue.
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
              {source.card.workOrder} · Up to {source.qty} pcs available
            </span>
          </span>
        </button>
      ))}
      <div className="row">
        {onBack ? (
          <button className="bigbtn ghost ss-back" onClick={onBack}>
            ‹ Back
          </button>
        ) : null}
        <button className="bigbtn ghost" onClick={onCancel}>
          Cancel (Esc)
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
  onBack,
  onApply,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & {
  pn: string;
  source: SourceOption;
  hasMachines: boolean;
  /** Back from the quantity view to the parent step (source selection,
   * PN action dialog, or manual PN entry); absent for direct scans. */
  onBack?: () => void;
}) {
  const areaName = areaByKey(station.area)?.name ?? station.area;
  const [step, setStep] = useState<'qty' | 'confirm'>('qty');
  const [qty, setQty] = useState(String(source.qty)); // MAX default
  const parsedQty = parseInt(qty || '0', 10);
  const valid = parsedQty >= 1 && parsedQty <= source.qty;
  const destinationNote = hasMachines
    ? `${areaName} queue (awaiting Machine)`
    : `${areaName} — direct processing`;
  // Quantity still actively processing at the source is treated as
  // completed there by this transfer: one atomic command appends
  // AREA_COMPLETED immediately before TRANSFERRED — no separate manual
  // DONE is required first. Quantity already finished (or still
  // queued) at the source transfers with TRANSFERRED alone.
  const completesQty = valid ? completionRequired(source.card, parsedQty) : 0;
  const movements: MovementType[] =
    completesQty > 0 ? ['AREA_COMPLETED', 'TRANSFERRED'] : ['TRANSFERRED'];

  function goConfirm() {
    if (valid) setStep('confirm');
  }

  function confirm() {
    if (!valid) return;
    onApply({
      action: {
        pn,
        movements,
        description: `${source.areaLabel} → ${destinationNote} · qty ${parsedQty}`,
        qty: parsedQty,
        source: source.areaLabel,
        destination: areaName,
        worker,
        time: MOCK_SCAN_TIME,
        reversalEffect:
          completesQty > 0
            ? `Returns ${parsedQty} pcs to ${source.areaLabel} and restores their processing state there.`
            : `Returns ${parsedQty} pcs to ${source.areaLabel}.`,
      },
      update: (cards) =>
        applyTransferIn(cards, {
          pn,
          fromArea: source.card.area,
          toArea: station.area,
          qty: parsedQty,
          destinationHasMachines: hasMachines,
          destinationOperation:
            areaByKey(station.area)?.operations[0] ?? areaName,
        }),
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `${pn} × ${parsedQty} → ${destinationNote}`,
      detail:
        completesQty > 0
          ? `${completesQty} pcs were completed at ${source.areaLabel} and transferred here.`
          : `The quantity moved here from ${source.areaLabel}.`,
    });
    onClose();
  }

  return (
    <ModalDialog
      label="Receive from another Area"
      onClose={onCancel}
      onKeyDown={
        step === 'qty'
          ? quantityKeyHandler(qty, setQty, goConfirm)
          : enterKeyHandler(confirm)
      }
    >
      <h3>Receive from another Area</h3>
      {step === 'qty' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <StepRecap
            lines={[
              <>
                Transfer{' '}
                <AreaChip areaKey={source.card.area}>
                  {source.areaLabel}
                </AreaChip>{' '}
                → <AreaChip areaKey={station.area}>{destinationNote}</AreaChip>
              </>,
              <WorkOrderRecapLine workOrder={source.card.workOrder} />,
            ]}
          />
          <Guidance tone="info">
            Available at {source.areaLabel}: <b>{source.qty} pcs</b>. The full
            quantity is selected by default.
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
            onBack={onBack}
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
              ['Action', 'Receive from another Area', 'primary'],
              ['PN', <span className="mono">{pn}</span>, 'primary'],
              [
                'Quantity',
                <span className="mono">{parsedQty} pcs</span>,
                'primary',
              ],
              [
                'Source',
                <AreaChip areaKey={source.card.area}>
                  {source.areaLabel}
                </AreaChip>,
                'primary',
              ],
              [
                'Destination',
                <AreaChip areaKey={station.area}>{destinationNote}</AreaChip>,
                'primary',
              ],
              [
                'Source processing',
                completesQty > 0
                  ? `${completesQty} pcs will be marked complete at the source before transfer`
                  : null,
                undefined,
                'warn',
              ],
              [
                'Remaining at source',
                <span className="mono">{source.qty - parsedQty} pcs</span>,
              ],
              ['Worker', worker, 'secondary'],
              ['Scan Station', station.stationId, 'secondary'],
              [
                movements.length > 1 ? 'Recorded events' : 'Recorded event',
                movements.length > 1 ? movements.join(', then ') : movements[0],
                'secondary',
              ],
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
  onBack,
  onApply,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & {
  pn: string;
  hasMachines: boolean;
  /** Back from the settings view to the parent step (manual PN entry);
   * absent when a scan resolved directly to this wizard. */
  onBack?: () => void;
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
  // The stored value uses the option format `<Area> — <Operation>` from
  // the first render on — the select's options and the recap/summary
  // chips always agree (the initial value is never a bare Operation
  // that matches no option).
  const [operation, setOperation] = useState(
    operations[0] ? `${areaName} — ${operations[0]}` : '',
  );
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
  const reusableInternalWo = requestType === 'MODIFY' && pn === '214-406';
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
    onApply({
      action: {
        pn,
        movements: ['RECEIVED'],
        description: `received into ${areaName}${hasMachines ? ' queue' : ''} · qty ${parsedQty} · ${requestType} · ${routeMode}`,
        qty: parsedQty,
        source: '—',
        destination: areaName,
        worker,
        time: MOCK_SCAN_TIME,
        reversalEffect: `Removes the ${parsedQty} received pcs again.`,
      },
      update: (cards) =>
        applyIntroduce(cards, {
          pn,
          area: station.area,
          qty: parsedQty,
          hasMachines,
          workOrder: `WO — · ${operation.split(' — ')[1] ?? operation} · ${requestType}`,
          job: '— (internal)',
          due: due || null,
          received: todayIso(),
        }),
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `${pn} × ${parsedQty} received into ${areaName}${hasMachines ? ' queue' : ''}`,
      detail: hasMachines
        ? 'Receipt recorded. The quantity is now waiting in the Area queue.'
        : 'Receipt recorded. The quantity is now in processing at this Area.',
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
      label="Receive Quantity"
      onClose={onCancel}
      size="wide"
      onKeyDown={keys}
    >
      <h3>Receive Quantity</h3>
      {step === 'settings' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          {/* Operator wording only. Engineering behavior behind it: the
              PartNumber master metadata record is created on first
              valid use (no preloaded catalog required); the canonical
              uppercase PN string is the identity; received_date
              defaults to the scan timestamp; the optional due date is
              stored on the WorkOrderDemand. */}
          {/* v15 layout: PN identity recap first, one short info line,
              then the intake settings — never one merged paragraph. */}
          <div className="sub">
            {isKnown ? (
              <>
                This Part Number has no active production demand. Review the
                details below before receiving quantity.
              </>
            ) : (
              <>
                New Part Number. Verify it carefully; it will be registered when
                you confirm the receipt.
              </>
            )}
          </div>
          {/* No default-selection recap here: the defaults and dates
              are visible directly in the fields below — the header
              stays the PN message plus one guidance line. */}
          <Guidance>
            Review all details before confirming. Nothing is recorded until the
            final confirmation.
          </Guidance>
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
            <label htmlFor="in-due">
              Due date <span className="field-optional">(optional)</span>
            </label>
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
              placeholder="Add a reason or note, if needed"
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
          <StepButtons
            onBack={onBack}
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
          {/* Compact two-line recap: the shared Request Type and Route
              Mode chips (the route name lives inside the Route Mode
              chip) on one line, the Work Order context on the other.
              WO/Due values carry text emphasis only (.rval) — the WO
              Number is `—` in every case here: MODIFY stays internal
              (new or reused) and a NEW request has no external number
              known at this step. */}
          <StepRecap
            lines={[
              <>
                <TypeChip type={requestType} />
                {' · '}
                <RouteModeChip
                  mode={routeMode}
                  detail={
                    routeMode === 'PLANNED' ? plannedRoute : 'actual trace'
                  }
                />
                {' · '}
                <EntityChip>{operation}</EntityChip>
              </>,
              <>
                WO <b className="rval">—</b> · Due:{' '}
                <b className="rval">{formatIsoDate(due || null)}</b>
                {notes ? <> · Notes: {notes}</> : null}
              </>,
            ]}
          />
          <Guidance>
            Enter the physical quantity received. No default quantity is
            assumed.
          </Guidance>
          {parsedQty < 1 ? (
            <Guidance tone="action">Enter a positive quantity.</Guidance>
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
          <div className="sub">Review the receipt, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'Receive Quantity', 'primary'],
              ['PN', <span className="mono">{pn}</span>, 'primary'],
              [
                'Quantity',
                <span className="mono">{parsedQty} pcs</span>,
                'primary',
              ],
              ['Request Type', <TypeChip type={requestType} />],
              [
                'Route Mode',
                // The route name lives inside the chip — no separate
                // Planned Route row.
                <RouteModeChip
                  mode={routeMode}
                  detail={
                    routeMode === 'PLANNED' ? plannedRoute : 'actual trace'
                  }
                />,
              ],
              ['Work Order', woBehavior],
              ['Due date', formatIsoDate(due || null)],
              [
                'Starting Area · Operation',
                <EntityChip>{operation}</EntityChip>,
                'primary',
              ],
              [
                'Destination',
                <AreaChip areaKey={station.area}>
                  {hasMachines
                    ? `${areaName} queue (awaiting Machine)`
                    : `${areaName} — direct processing`}
                </AreaChip>,
                'primary',
              ],
              ['Reason / notes', notes || null],
              ['Worker', worker, 'secondary'],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', 'RECEIVED', 'secondary'],
            ]}
          />
          <StepButtons
            onBack={() => setStep('qty')}
            onCancel={onCancel}
            primary={{
              label: 'Confirm receipt',
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
  onBack,
  onApply,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & {
  pn: string;
  hasMachines: boolean;
  /** Back from the entry view to the parent PN action dialog. */
  onBack?: () => void;
}) {
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
    onApply({
      action: {
        pn,
        movements: ['QUANTITY_ADJUSTED'],
        description: `+${parsedQty} pcs at ${areaName} (INCREASE) · reason: ${reason.trim()}`,
        qty: parsedQty,
        source: '—',
        destination: areaName,
        worker,
        time: MOCK_SCAN_TIME,
        reversalEffect: `Removes the ${parsedQty} added pcs again.`,
      },
      update: (cards) =>
        applyIntroduce(cards, {
          pn,
          area: station.area,
          qty: parsedQty,
          hasMachines,
          workOrder: 'WO — · Addition',
          job: '— (internal)',
          due: null,
          received: todayIso(),
        }),
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `${pn} +${parsedQty} pcs at ${areaName}`,
      detail: hasMachines
        ? 'The quantity adjustment was recorded with your reason. The added quantity is now waiting in the Area queue.'
        : 'The quantity adjustment was recorded with your reason. The added quantity is now in processing at this Area.',
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
            Add physical quantity found at this Area that was not transferred
            from another Area. A reason is required.
          </div>
          <StepRecap
            lines={[
              <>
                Adding at <AreaChip areaKey={station.area}>{areaName}</AreaChip>{' '}
                →{' '}
                <AreaChip areaKey={station.area}>
                  {hasMachines
                    ? `${areaName} queue (awaiting Machine)`
                    : `${areaName} — direct processing`}
                </AreaChip>
              </>,
            ]}
          />
          <Guidance>
            Enter the actual quantity found. No default quantity is provided.
          </Guidance>
          {parsedQty < 1 ? (
            <Guidance tone="action">A positive quantity is required.</Guidance>
          ) : null}
          <QuantityKeypad value={qty} onChange={setQty} />
          <label className="ss-reasonlbl" htmlFor="addq-reason">
            Reason <span className="field-required">(required)</span>
          </label>
          <div className="ss-fieldhint">
            This reason will be included in the adjustment history.
          </div>
          <input
            id="addq-reason"
            className="field"
            value={reason}
            placeholder="e.g. found 2 additional blanks with the lot"
            onChange={(e) => setReason(e.target.value)}
          />
          <StepButtons
            onBack={onBack}
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
              ['Action', 'Add physical quantity', 'primary'],
              ['PN', <span className="mono">{pn}</span>, 'primary'],
              [
                'Quantity',
                <span className="mono">+{parsedQty} pcs</span>,
                'primary',
              ],
              ['Area', <AreaChip areaKey={station.area}>{areaName}</AreaChip>],
              [
                'Destination',
                <AreaChip areaKey={station.area}>
                  {hasMachines
                    ? `${areaName} queue (awaiting Machine)`
                    : `${areaName} — direct processing`}
                </AreaChip>,
                'primary',
              ],
              ['Reason', reason.trim(), 'primary'],
              ['Worker', worker, 'secondary'],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', 'QUANTITY_ADJUSTED · INCREASE', 'secondary'],
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
  onBack,
  onApply,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & {
  pn: string;
  sources: { areaLabel: string; qty: number; flow: string; note: string }[];
  /** Back from the entry view to the parent PN action dialog. */
  onBack?: () => void;
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
    const selectedSource = source;
    onApply({
      action: {
        pn,
        movements: ['TRANSFERRED'],
        description: `REPAIR · ${selectedSource.areaLabel} → ${areaName} · qty ${parsedQty} · reason: ${reason.trim()}`,
        qty: parsedQty,
        source: selectedSource.areaLabel,
        destination: areaName,
        worker,
        time: MOCK_SCAN_TIME,
        reversalEffect: `Returns ${parsedQty} pcs to ${selectedSource.areaLabel}.`,
      },
      update: (cards) =>
        applyIntroduce(cards, {
          pn,
          area: station.area,
          qty: parsedQty,
          hasMachines: (MOCK_AREA_MACHINES[station.area] ?? []).length > 0,
          workOrder: 'WO — · Repair',
          job: '— (repair)',
          due: null,
          received: todayIso(),
        }),
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `Repair — ${pn} × ${parsedQty} returned to ${areaName}`,
      detail: `The selected quantity was returned from ${selectedSource.areaLabel} to ${areaName} for repair.`,
    });
    onClose();
  }

  return (
    <ModalDialog
      label="Return quantity for repair"
      onClose={onCancel}
      size="wide"
      onKeyDown={
        step === 'entry'
          ? quantityKeyHandler(qty, setQty, goConfirm)
          : enterKeyHandler(confirm)
      }
    >
      <h3>Return quantity for repair</h3>
      {step === 'entry' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <div className="sub">
            Select the source Area, enter the quantity to return, and provide
            the repair reason. This moves existing quantity; it does not create
            additional quantity.
          </div>
          <StepRecap
            lines={[
              <>
                Repair destination:{' '}
                <AreaChip areaKey={station.area}>{areaName}</AreaChip>
              </>,
            ]}
          />
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
              {/* MAX/default statement is an instruction, not a hazard
                  — `info` (v15); genuine deviations keep `warn`. */}
              <Guidance>
                Up to {max} pcs are available. The full quantity is selected by
                default; enter a smaller quantity for a partial return.
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
            Reason <span className="field-required">(required)</span>
          </label>
          <input
            id="rep-reason"
            className="field"
            value={reason}
            placeholder="Describe the work that must be corrected"
            onChange={(e) => setReason(e.target.value)}
          />
          <StepButtons
            onBack={onBack}
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
              ['Action', 'Return quantity for repair', 'primary'],
              ['PN', <span className="mono">{pn}</span>, 'primary'],
              [
                'Quantity',
                <span className="mono">{parsedQty} pcs</span>,
                'primary',
              ],
              [
                'Source',
                source ? (
                  <>
                    <EntityChip>{source.areaLabel}</EntityChip> · {source.flow}
                  </>
                ) : null,
                'primary',
              ],
              [
                'Destination',
                <AreaChip areaKey={station.area}>{areaName}</AreaChip>,
                'primary',
              ],
              [
                'Effect',
                partial
                  ? 'Partial quantity — splits off its own Quantity Flow first'
                  : 'Moves the whole Quantity Flow',
                undefined,
                partial ? 'warn' : undefined,
              ],
              ['Reason', reason.trim(), 'primary'],
              ['Worker', worker, 'secondary'],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', 'TRANSFERRED · REPAIR intent', 'secondary'],
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
  onBack,
  onApply,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & {
  pn: string;
  available: number;
  /** Back from the counting view to the parent PN action dialog. */
  onBack?: () => void;
}) {
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
        `“${value.trim()}” is not a valid scrap barcode. Scan ${SCRAP_BARCODE} to add one piece.`,
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
    onApply({
      action: {
        pn,
        movements: ['SCRAPPED'],
        description: `scrapped ${count} at ${areaName} · reason: ${reason.trim()}`,
        qty: count,
        source: areaName,
        destination: 'scrap',
        worker,
        time: MOCK_SCAN_TIME,
        reversalEffect: `Restores the ${count} scrapped pcs to active quantity.`,
      },
      update: (cards) =>
        applyScrap(cards, { pn, area: station.area, qty: count }),
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `Scrapped — ${pn} × ${count} at ${areaName}`,
      detail: `The scrap quantity and reason were recorded. Remaining active quantity: ${available - count} pcs.`,
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
          {/* Operator wording only (v15): the scrap barcode counts only
              inside this workflow, and the single SCRAPPED operation is
              created on the final confirmation — the canonical event
              name appears in the confirmation summary and history
              surfaces, never in this instruction. */}
          <div className="sub">
            Scan <code>{SCRAP_BARCODE}</code> once for each damaged piece, then
            enter one reason for the total. Nothing is recorded until
            confirmation.
          </div>
          <Guidance>
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
              Remove one
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
          {scanNote ? <Guidance tone="error">{scanNote}</Guidance> : null}
          <label className="ss-reasonlbl" htmlFor="scrap-reason">
            Scrap reason <span className="field-required">(required)</span>
          </label>
          <input
            id="scrap-reason"
            className="field"
            value={reason}
            placeholder="e.g. tool crash — gouged face"
            onChange={(e) => setReason(e.target.value)}
          />
          <StepButtons
            onBack={onBack}
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
          <div className="sub">Review the scrap operation, then confirm.</div>
          <ConfirmationSummary
            rows={[
              ['Action', 'Scrap damaged quantity', 'primary'],
              ['PN', <span className="mono">{pn}</span>, 'primary'],
              [
                'Area',
                <AreaChip areaKey={station.area}>{areaName}</AreaChip>,
                'primary',
              ],
              ['Machine', '—'],
              ['Available', <span className="mono">{available} pcs</span>],
              [
                'Scrap quantity',
                <span className="mono">{count} pcs</span>,
                'primary',
                'err',
              ],
              [
                'Remaining active quantity',
                <span className="mono">{available - count} pcs</span>,
              ],
              ['Reason', reason.trim(), 'primary'],
              ['Worker', worker, 'secondary'],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', 'SCRAPPED', 'secondary'],
            ]}
          />
          <StepButtons
            onBack={() => setStep('count')}
            onCancel={onCancel}
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
  onApply,
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
    onApply({
      action: {
        pn,
        movements: ['RELEASED_FROM_MACHINE'],
        description: `${machine} → ${areaName} queue · qty ${parsedQty}`,
        qty: parsedQty,
        source: machine,
        destination: 'Area queue',
        machine,
        worker,
        time: MOCK_SCAN_TIME,
        reversalEffect: `Re-assigns ${parsedQty} pcs to ${machine}.`,
      },
      update: (cards) =>
        applyQueueReturn(cards, {
          pn,
          area: station.area,
          machine,
          qty: parsedQty,
        }),
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `${pn} × ${parsedQty} returned to the ${areaName} queue`,
      detail: `The quantity was removed from ${machine} and returned to the ${areaName} queue. It remains unfinished.`,
    });
    onClose();
  }

  return (
    <ModalDialog
      label="Return unfinished quantity to queue"
      onClose={onCancel}
      onKeyDown={
        step === 'qty'
          ? quantityKeyHandler(qty, setQty, goConfirm)
          : enterKeyHandler(confirm)
      }
    >
      <h3>Return unfinished quantity to queue</h3>
      {step === 'qty' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <StepRecap
            lines={[
              <>
                <EntityChip>{machine}</EntityChip> →{' '}
                <EntityChip>{areaName} queue</EntityChip>
              </>,
            ]}
          />
          <Guidance tone="info">
            <b>{max} pcs</b> are assigned to {machine}. Enter a lower quantity
            to return only part of them.
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
              ['Action', 'Return unfinished quantity to queue', 'primary'],
              ['PN', <span className="mono">{pn}</span>, 'primary'],
              [
                'Quantity',
                <span className="mono">{parsedQty} pcs</span>,
                'primary',
              ],
              ['Source Machine', <EntityChip>{machine}</EntityChip>, 'primary'],
              [
                'Destination',
                <AreaChip
                  areaKey={station.area}
                >{`${areaName} queue`}</AreaChip>,
                'primary',
              ],
              [
                'Remaining on Machine',
                <span className="mono">{max - parsedQty} pcs</span>,
              ],
              ['Worker', worker, 'secondary'],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', 'RELEASED_FROM_MACHINE', 'secondary'],
            ]}
          />
          <StepButtons
            onBack={() => setStep('qty')}
            onCancel={onCancel}
            primary={{
              label: 'Confirm return to queue',
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

/**
 * Manual DONE — complete Area processing for a selected quantity. The
 * quantity leaves its Machine (when one is involved), stays located in
 * the current Area, and waits on the finished rack for transfer
 * (`READY_TO_TRANSFER`). DONE never means Work Order completion,
 * manufacturing completion, stocking, allocation, or QC approval —
 * manufacturing completion stays `STOCKED` at the terminal Stockroom.
 */
function DoneDialog({
  station,
  pn,
  machine,
  max,
  worker,
  onBack,
  onApply,
  onNotice,
  onClose,
  onCancel,
}: ActionDialogProps & {
  pn: string;
  /** Machine currently processing the quantity, or null for direct
   * Area processing (Areas without Machines). */
  machine: string | null;
  max: number;
  /** Back from the quantity view to the parent PN action dialog;
   * absent for the DONE row actions, which have no previous dialog. */
  onBack?: () => void;
}) {
  const areaName = areaByKey(station.area)?.name ?? station.area;
  const [step, setStep] = useState<'qty' | 'confirm'>('qty');
  // MAX defaults to the quantity at the current source position.
  const [qty, setQty] = useState(String(max));
  const parsedQty = parseInt(qty || '0', 10);
  const valid = parsedQty >= 1 && parsedQty <= max;

  function goConfirm() {
    if (valid) setStep('confirm');
  }

  function confirm() {
    if (!valid) return;
    onApply({
      action: {
        pn,
        movements: ['AREA_COMPLETED'],
        description: `${machine ? `${machine} · ` : ''}finished at ${areaName} · qty ${parsedQty}`,
        qty: parsedQty,
        source: machine ?? `${areaName} processing`,
        destination: `${areaName} — finished rack`,
        machine: machine ?? undefined,
        worker,
        time: MOCK_SCAN_TIME,
        reversalEffect: machine
          ? `Returns ${parsedQty} pcs to ${machine} as active processing.`
          : `Returns ${parsedQty} pcs to active processing at ${areaName}.`,
      },
      update: (cards) =>
        applyDone(cards, { pn, area: station.area, machine, qty: parsedQty }),
    });
    onNotice({
      kind: 'ok',
      icon: '✓',
      title: `${pn} × ${parsedQty} finished at ${areaName}`,
      detail: `The finished quantity ${
        machine ? `left ${machine} and ` : ''
      }now waits on the finished rack, ready to move to another Area. Scan the PN at the next Area to transfer it.`,
    });
    onClose();
  }

  return (
    <ModalDialog
      label="Complete Area processing"
      onClose={onCancel}
      onKeyDown={
        step === 'qty'
          ? quantityKeyHandler(qty, setQty, goConfirm)
          : enterKeyHandler(confirm)
      }
    >
      <h3>Complete Area processing</h3>
      {step === 'qty' ? (
        <div>
          <div className="big mono" title={pn}>
            {pn}
          </div>
          <StepRecap
            lines={[
              machine ? (
                <>
                  <EntityChip>{machine}</EntityChip> →{' '}
                  <EntityChip>{areaName} finished rack</EntityChip>
                </>
              ) : (
                <>
                  <EntityChip>{areaName} processing</EntityChip> →{' '}
                  <EntityChip>{areaName} finished rack</EntityChip>
                </>
              ),
            ]}
          />
          <Guidance tone="info">
            {machine ? (
              <>
                <b>{max} pcs</b> are available on {machine}. The full quantity
                is selected by default. Enter a smaller quantity to complete
                only part of it.
              </>
            ) : (
              <>
                <b>{max} pcs</b> in process. The full quantity is selected by
                default. Enter a smaller quantity to complete only part of it.
              </>
            )}
          </Guidance>
          {(() => {
            const v = quantityValidation(
              parsedQty,
              max,
              machine
                ? `Quantity cannot exceed the ${max} pcs currently on ${machine}.`
                : `Quantity cannot exceed the ${max} pcs currently in processing.`,
            );
            return v ? <Guidance tone={v.tone}>{v.text}</Guidance> : null;
          })()}
          <QuantityKeypad value={qty} onChange={setQty} max={max} />
          <StepButtons
            onBack={onBack}
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
          <div className="sub">
            Confirm the completed quantity. It will remain on the {areaName}{' '}
            finished rack until transferred.
          </div>
          <ConfirmationSummary
            rows={[
              ['Action', 'Complete Area processing', 'primary'],
              ['PN', <span className="mono">{pn}</span>, 'primary'],
              [
                'Quantity',
                <span className="mono">{parsedQty} pcs</span>,
                'primary',
              ],
              [
                'Area',
                <AreaChip areaKey={station.area}>{areaName}</AreaChip>,
                'primary',
              ],
              [
                'Machine',
                machine ? <EntityChip>{machine}</EntityChip> : null,
                'primary',
              ],
              ['Result', 'Finished — ready to move', 'primary', 'ok'],
              ['Worker', worker, 'secondary'],
              ['Scan Station', station.stationId, 'secondary'],
              ['Recorded event', 'AREA_COMPLETED', 'secondary'],
            ]}
          />
          <StepButtons
            onBack={() => setStep('qty')}
            onCancel={onCancel}
            primary={{
              label: 'Confirm completion',
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
  reversedBy,
  onConfirm,
  onCancel,
}: {
  target: MockCompletedAction;
  /**
   * Worker recorded on the reversal — the Worker active at the moment
   * the Undo is confirmed (§16, decided post-v18); null when the
   * Area's Worker ID mode is Disabled. Shown separately from the
   * original action's Worker.
   */
  reversedBy: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <ModalDialog
      label="Reverse the last Part Number action?"
      onClose={onCancel}
    >
      <h3>Reverse the last Part Number action?</h3>
      <div className="big mono">{target.pn}</div>
      <div className="sub">
        This will reverse the complete last action. The original history will
        remain available for audit.
      </div>
      <ConfirmationSummary
        rows={[
          ['Original action', target.movements.join(' + '), 'primary'],
          ['Quantity', <span className="mono">{target.qty}</span>, 'primary'],
          [
            'Source → destination',
            <>
              <EntityChip>{target.source}</EntityChip> →{' '}
              <EntityChip>{target.destination}</EntityChip>
            </>,
            'primary',
          ],
          [
            'Machine',
            target.machine ? <EntityChip>{target.machine}</EntityChip> : null,
          ],
          ['Worker', target.worker, 'secondary'],
          ['Time', <span className="mono">{target.time}</span>, 'secondary'],
          ['Reversed by', reversedBy, 'secondary'],
          ['Result after reversal', target.reversalEffect, 'primary', 'warn'],
        ]}
      />
      <StepButtons
        onCancel={onCancel}
        primary={{
          label: 'Confirm reversal',
          onClick: onConfirm,
          danger: true,
        }}
      />
    </ModalDialog>
  );
}

function ManualEntryDialog({
  initialPn,
  onCancel,
  onConfirm,
}: {
  /** Previously entered PN, preserved when Back returns here. */
  initialPn?: string;
  onCancel: () => void;
  /** Called with the canonical PN, or '' when the entry was empty. */
  onConfirm: (pn: string) => void;
}) {
  const fieldRef = useRef<HTMLInputElement>(null);
  const [entryError, setEntryError] = useState<string | null>(null);
  useEffect(() => {
    fieldRef.current?.focus();
  }, []);
  // Normalize to the canonical uppercase PN before resolving:
  // surrounding whitespace trims away; a value with INTERNAL whitespace
  // is invalid and stays in the dialog with an explanation — it is
  // never silently cleaned up into a valid PN.
  function submit() {
    const raw = fieldRef.current?.value ?? '';
    if (raw.trim() === '') {
      onConfirm('');
      return;
    }
    const pn = normalizePartNumber(raw);
    if (!pn) {
      setEntryError(
        'A Part Number cannot contain spaces, tabs, or other whitespace inside the value. Correct the entry and try again.',
      );
      return;
    }
    onConfirm(pn);
  }
  return (
    <ModalDialog label="Enter Part Number manually" onClose={onCancel}>
      <h3>Enter Part Number manually</h3>
      {/* Operator wording only — engineering detail: the entry is
          normalized to the canonical uppercase, whitespace-free PN; an
          unknown PN opens the intake wizard, where the PartNumber
          master metadata record is created on first valid use. */}
      <div className="sub">
        Enter the Part Number exactly as shown on the traveler or job paperwork.
        Lowercase letters are accepted and shown in uppercase. Unknown Part
        Numbers will open the receive workflow for review. Nothing is recorded
        at this step.
      </div>
      <input
        aria-label="Part Number"
        ref={fieldRef}
        className="field mono"
        autoComplete="off"
        defaultValue={initialPn}
        placeholder="Part Number, e.g. 0455-20-0118-03"
        onChange={() => setEntryError(null)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit();
        }}
      />
      {entryError ? <Guidance tone="error">{entryError}</Guidance> : null}
      <StepButtons
        onCancel={onCancel}
        primary={{
          label: 'Continue',
          onClick: submit,
        }}
      />
    </ModalDialog>
  );
}
