import './scan-station.css';

import {
  lazy,
  Suspense,
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
import { isMockPreviewRequested } from '../../app/view-state';
import { errorMessage } from '../../api/client';
import {
  listAreas,
  listDepartments,
  listOperations,
  listScanStations,
} from '../../api/environment';
import type { Area, Department, Operation } from '../../api/environment';
import { listMachines } from '../../api/machines';
import { newDeviceEventId } from '../../api/production-release';
import {
  areaRefColor,
  getAreaInventory,
  getStationContext,
  resolveMachineScan,
  resolveScan,
  routeDeviationConfirmation,
  transferOutcomeUnknown,
  transferToStationArea,
} from '../../api/scan-station';
import type {
  AreaInventory,
  AreaRef,
  FlowInArea,
  MachineActionResult,
  MachineRef,
  OperationRef,
  ScanResolution,
  StationContext,
  TransferCandidate,
  TransferResult,
  WorkOrderContext,
} from '../../api/scan-station';
import { useApiData } from '../../api/use-api-data';
import {
  AreaMachineLayout,
  AreaSummaryCard,
  MachineMonitoringCard,
} from '../../components/area-monitoring';
import { ConnectivityChip } from '../../components/ConnectivityChip';
import { DevNotice } from '../../components/DevNotice';
import { AreaDot, RouteModeChip, TypeChip } from '../../components/indicators';
import { ModalDialog } from '../../components/ModalDialog';
import { QuantityKeypad } from '../../components/QuantityKeypad';
import { ThemeToggle } from '../../components/ThemeToggle';
import { isTouchPrimaryDevice } from '../../components/touch-device';
import { ErrorState, LoadingState } from '../../components/view-states';
import { areaStats, splitAssignments } from '../area-monitoring';
import type { AreaAssignment } from '../area-monitoring';
import type { MockArea, MockAreaCard, MockAreaMachine } from '../view-models';
import { normalizeScanInput, parseScan } from './barcode';
import {
  AssignToMachineDialog,
  MachineActionDialog,
} from './scan-station-machine-dialogs';
import {
  ConfirmationSummary,
  EntityChip,
  FloatingNotice,
  Guidance,
  HeaderOperations,
  ManualEntryDialog,
  OperationChips,
  StepButtons,
  StepRecap,
  UnknownStation,
} from './scan-station-presentation';
import {
  enterKeyHandler,
  NOTICE_OK_MS,
  NOTICE_WARN_MS,
  quantityKeyHandler,
} from './scan-station-wizard';
import type { Notice } from './scan-station-presentation';

/**
 * Scan Station — the REAL Phase 5/6 view (GUI_DESIGN §4; IMPLEMENTATION_
 * ROADMAP Phases 5–6). The Station Selector and the station itself read
 * real server state through `/api`, PN and Machine barcodes and manual
 * entries resolve on the server, and the production commands recorded
 * here are the source-explicit transfer of a whole Quantity Flow into
 * the station's Area (completing ON_MACHINE quantity implicitly) and
 * the Phase 6 one-shot Machine-Area actions — `Assign to Machine`
 * (Machine-first from a Machine scan, PN-first from a queued row), and
 * the two distinct Machine-card actions DONE (`Complete Area
 * processing`) and QUEUE (`Return to Area queue`) — every one recorded
 * by the server before anything reads as success, with no Machine or
 * PN context surviving a dialog. The remaining approved workflows
 * (direct-processing DONE, Repair, Scrap, Undo, Worker sessions,
 * Receive Quantity from the station) are NOT implemented here: they
 * stay honest placeholders, and the mock preview of them survives only
 * behind the development-only boundary below (`?preview=mock`).
 */

// Development-only preview of the mock Scan Station (Phase 6+
// workflows). The conditional is compiled away in production builds,
// so the mock view and its datasets never enter the module graph.
const MockPreview = import.meta.env.DEV
  ? lazy(() =>
      import('./ScanStationMockView').then((m) => ({
        default: m.ScanStationMockView,
      })),
    )
  : null;

export function ScanStationView() {
  const { route } = useRouter();
  if (MockPreview && isMockPreviewRequested()) {
    return (
      <Suspense fallback={<LoadingState label="Loading Scan Station" />}>
        <MockPreview />
      </Suspense>
    );
  }
  if (route.view !== 'scan-station' || route.stationId === null) {
    return <StationSelector />;
  }
  return (
    <StationView
      key={route.stationId}
      stationId={route.stationId}
      productionMode={route.mode === 'production'}
    />
  );
}

/* ------------------------------------------------------------------ */
/* Station Selector                                                    */
/* ------------------------------------------------------------------ */

interface SelectorData {
  stations: { stationId: string; areaId: number }[];
  areas: Map<number, Area>;
  departments: Map<number, Department>;
  operationsByArea: Map<number, Operation[]>;
  machineCountByArea: Map<number, number>;
}

async function loadSelectorData(): Promise<SelectorData> {
  const [stations, areas, departments, operations, machines] =
    await Promise.all([
      listScanStations(),
      listAreas(),
      listDepartments(),
      listOperations(),
      listMachines(),
    ]);
  const operationsByArea = new Map<number, Operation[]>();
  for (const operation of operations) {
    if (!operation.isActive) continue;
    const list = operationsByArea.get(operation.areaId) ?? [];
    list.push(operation);
    operationsByArea.set(operation.areaId, list);
  }
  const machineCountByArea = new Map<number, number>();
  for (const machine of machines) {
    if (machine.retiredOn !== undefined) continue;
    machineCountByArea.set(
      machine.areaId,
      (machineCountByArea.get(machine.areaId) ?? 0) + 1,
    );
  }
  return {
    stations: stations
      .filter((station) => station.isActive)
      .map((station) => ({
        stationId: station.stationId,
        areaId: station.areaId,
      })),
    areas: new Map(areas.map((area) => [area.id, area])),
    departments: new Map(
      departments.map((department) => [department.id, department]),
    ),
    operationsByArea,
    machineCountByArea,
  };
}

function operationLabel(operation: Pick<Operation, 'code' | 'name'>): string {
  return operation.name ?? operation.code;
}

function StationSelector() {
  const data = useApiData(loadSelectorData);
  return (
    <section className="ss" aria-label="Scan Station">
      <div className="ss-select">
        <h1>Select a Scan Station</h1>
        <p className="ss-select-sub">
          Select the Scan Station for your work area. <b>Production mode</b>{' '}
          hides the main navigation to keep the station focused on scanning.
        </p>
        {data.state.status === 'loading' ? (
          <LoadingState label="Loading Scan Stations" />
        ) : data.state.status === 'error' ? (
          <ErrorState
            message="Scan Stations could not be loaded."
            detail={data.state.message}
            onRetry={data.reload}
          />
        ) : data.state.data.stations.length === 0 ? (
          <div className="ss-feedback idle">
            <div>
              <div className="t1">No active Scan Station is configured</div>
              <div className="t2">
                Configure Scan Stations in Administration → Scan Stations, bound
                to an active Area.
              </div>
            </div>
          </div>
        ) : (
          <StationList data={data.state.data} />
        )}
      </div>
    </section>
  );
}

function StationList({ data }: { data: SelectorData }) {
  const { navigate } = useRouter();
  const { areas, departments, operationsByArea, machineCountByArea } = data;
  return (
    <ul className="ss-stationlist">
      {data.stations.map((station) => {
        const area = areas.get(station.areaId);
        const department = area
          ? departments.get(area.departmentId)
          : undefined;
        const operations = operationsByArea.get(station.areaId) ?? [];
        const machineCount = machineCountByArea.get(station.areaId) ?? 0;
        return (
          // No nested interactive controls: the card's main
          // surface is ONE button (standard mode) with the
          // Production mode action as its sibling in a separate
          // cell.
          <li key={station.stationId} className="ss-stationcard">
            <button
              className="ss-stationmain"
              aria-label={`Open ${station.stationId}`}
              onClick={() => navigate(`/scan-station/${station.stationId}`)}
            >
              <span className="sid mono">{station.stationId}</span>
              <span className="smeta">
                {department?.name ?? '—'} ·{' '}
                <AreaDot colorVar={area?.color ?? 'var(--faint)'} size={10} />{' '}
                {area?.name ?? '—'} · Operations:{' '}
                <OperationChips operations={operations.map(operationLabel)} />
              </span>
              <span className="stype">
                {machineCount > 0
                  ? `${machineCount} Machine${machineCount === 1 ? '' : 's'} · Quantity waits in the Area queue`
                  : 'No Machines · Direct Area processing'}
              </span>
            </button>
            <div className="ss-stationacts">
              <button
                className="ss-openbtn"
                aria-label={`Open ${station.stationId} in production mode`}
                title="Opens this station with the application navigation hidden"
                onClick={() =>
                  navigate(`/scan-station/${station.stationId}/production`)
                }
              >
                Production mode
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/* ------------------------------------------------------------------ */
/* Presentation adapters                                               */
/* ------------------------------------------------------------------ */

/**
 * The shared Area/Machine monitoring components take the presentation
 * shapes of `views/view-models` (their `key`/`area` fields carry the
 * development registry's Area keys, which the components never read
 * — they only use name, color, description, Operations and terminal
 * flag). A real Area is identified by its server id; the key below is
 * a stable render identity only.
 */
function presentationArea(area: AreaRef, operations: OperationRef[]): MockArea {
  return {
    key: `area-${area.id}` as MockArea['key'],
    name: area.name,
    colorVar: areaRefColor(area),
    description: area.description ?? '',
    operations: operations.map(operationLabel),
    terminal: area.isTerminal,
  };
}

/** Work Order context line of a flow, in the shared card label form. */
function workOrderLabel(workOrder: WorkOrderContext | null): string {
  if (!workOrder) return 'WO —';
  return `WO ${workOrder.workOrderNumber ?? '—'} · ${workOrder.requestType}`;
}

/**
 * The presentation of one Area inventory: one presence card per
 * Quantity Flow in the Area, its portion placed by the server's derived
 * processing state — the Area queue (`queue` context), the Machine it
 * is on (the Machine's name — the shared grouping puts it on that
 * Machine's card), or the finished rack (`READY_TO_TRANSFER`, Area
 * summary only). In an Area without Machines every flow is a direct
 * portion. `flowOf` maps a rendered row back to its flow so a row
 * action knows exactly which Quantity Flow it acts on. Due dates, Job
 * Numbers and time in Area arrive with the later monitoring read
 * models.
 */
interface InventoryPresentation {
  cards: MockAreaCard[];
  machines: MockAreaMachine[];
  machineByName: Map<string, MachineRef>;
  flowOf: Map<MockAreaCard, FlowInArea>;
}

const MACHINE_CARD_STATUS: Record<
  MachineRef['operationalState'],
  MockAreaMachine['status']
> = { RUNNING: 'running', IDLE: 'idle', MAINTENANCE: 'maintenance' };

function presentInventory(
  inventory: AreaInventory,
  areaKey: MockArea['key'],
  hasMachines: boolean,
): InventoryPresentation {
  const machineByName = new Map<string, MachineRef>();
  const machineById = new Map<number, MachineRef>();
  for (const card of inventory.machines) {
    machineByName.set(card.machine.name, card.machine);
    machineById.set(card.machine.id, card.machine);
  }
  const cards: MockAreaCard[] = [];
  const flowOf = new Map<MockAreaCard, FlowInArea>();
  for (const line of inventory.lines) {
    for (const flow of line.flows) {
      const onMachine =
        flow.processingState === 'ON_MACHINE' && flow.machineId !== null
          ? machineById.get(flow.machineId)
          : undefined;
      const card: MockAreaCard = {
        area: areaKey,
        pn: flow.partNumber,
        workOrder: workOrderLabel(flow.workOrder),
        job: '—',
        qty: flow.quantity,
        machines: !hasMachines
          ? []
          : onMachine
            ? [[onMachine.name, flow.quantity]]
            : flow.processingState === 'QUEUED'
              ? [['queue', flow.quantity]]
              : [],
        finished:
          flow.processingState === 'READY_TO_TRANSFER'
            ? [{ qty: flow.quantity }]
            : undefined,
        due: null,
        enteredAreaAt: null,
        received: '',
      };
      cards.push(card);
      flowOf.set(card, flow);
    }
  }
  return {
    cards,
    machines: inventory.machines.map(({ machine }) => ({
      name: machine.name,
      status: MACHINE_CARD_STATUS[machine.operationalState],
      stateChangedAt: machine.stateChangedAt,
      maintenanceNote: machine.maintenanceNote ?? undefined,
      expectedReturn: machine.maintenanceExpectedReturn ?? undefined,
    })),
    machineByName,
    flowOf,
  };
}

const EMPTY_PRESENTATION: InventoryPresentation = {
  cards: [],
  machines: [],
  machineByName: new Map(),
  flowOf: new Map(),
};

/* ------------------------------------------------------------------ */
/* Station                                                             */
/* ------------------------------------------------------------------ */

/** The last confirmed action of this station session (Last Action block). */
interface LastAction {
  pn: string;
  summary: string;
}

/**
 * One-shot dialog flows — no persistent context survives a dialog.
 * `parent` is the step Back returns to within the same workflow
 * (source selection, the in-Area dialog, manual PN entry); flows
 * opened directly from a scan carry none and show no Back.
 */
type Flow =
  | { kind: 'manual-pn'; initialPn?: string }
  | {
      kind: 'source-select';
      resolution: ScanResolution;
      parent?: Flow;
    }
  | {
      kind: 'transfer';
      resolution: ScanResolution;
      candidate: TransferCandidate;
      parent?: Flow;
    }
  | { kind: 'in-area'; resolution: ScanResolution; parent?: Flow }
  | { kind: 'no-quantity'; resolution: ScanResolution; parent?: Flow }
  | {
      // Assign to Machine: Machine-first carries the scanned Machine
      // and the queued flows the server returned with it; PN-first
      // carries the queued flow the action was taken on.
      kind: 'assign';
      machines: MachineRef[];
      queued: FlowInArea[];
      machineId?: number;
      flow?: FlowInArea;
      parent?: Flow;
    }
  | {
      kind: 'machine-action';
      action: 'DONE' | 'QUEUE';
      flow: FlowInArea;
      machine: MachineRef;
      parent?: Flow;
    };

function StationView({
  stationId,
  productionMode,
}: {
  stationId: string;
  productionMode: boolean;
}) {
  const { navigate } = useRouter();
  const { status } = useConnectivity();
  const disconnected = status === 'unavailable';
  const writeBlocked = status !== 'connected';

  const loadContext = useCallback(
    () => getStationContext(stationId),
    [stationId],
  );
  const context = useApiData(loadContext);
  const ready = context.state.status === 'ready' ? context.state.data : null;
  const areaId = ready?.area.id ?? null;
  const loadInventory = useCallback(
    () =>
      areaId === null
        ? Promise.resolve<AreaInventory | null>(null)
        : getAreaInventory(areaId),
    [areaId],
  );
  const inventory = useApiData(loadInventory);

  const inputRef = useRef<HTMLInputElement>(null);
  const [touchPrimary] = useState(isTouchPrimaryDevice);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [resolving, setResolving] = useState(false);
  const [lastAction, setLastAction] = useState<LastAction | null>(null);

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
    // completed operation and dialog close — never pulling focus out of
    // a dialog that opened in the meantime.
    setTimeout(() => {
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      inputRef.current?.focus();
    }, 30);
  }, []);

  useEffect(() => {
    if (!writeBlocked && ready) focusScan();
  }, [writeBlocked, ready, focusScan]);

  // Header fit measurement (§4.3) — identical to the approved
  // presentation: the Area totals drop to their second row only when
  // the measured single-row layout genuinely cannot fit.
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
    setHeadWrapped(required > available + 0.5);
  }, []);
  // The inventory counts as loaded only once the FIRST response for
  // the rendered Area arrived: the placeholder before the station
  // context resolved, and data of a previously bound Area during a
  // rebind reload, never render as a (false) empty Area.
  const inventoryReady =
    inventory.state.status === 'ready' &&
    inventory.state.data !== null &&
    inventory.state.data.area.id === areaId
      ? inventory.state.data
      : null;
  const inventoryLoading =
    inventory.state.status !== 'error' && inventoryReady === null;
  useLayoutEffect(() => {
    measureHead();
  }, [measureHead, ready, inventoryReady, productionMode, status]);
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

  // Ctrl+Shift+K — the same standard/production toggle as the approved
  // presentation (never inside text fields, selects, or a dialog).
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
          ? `/scan-station/${stationId}`
          : `/scan-station/${stationId}/production`,
      );
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [flow, navigate, productionMode, stationId]);

  const closeFlow = useCallback(
    (message?: string) => {
      setFlow(null);
      if (message) setNotice({ kind: 'info', title: message });
      focusScan();
    },
    [focusScan],
  );

  const cancelFlow = useCallback(() => {
    // Cancel always means no write; the temporary context is cleared
    // and never replaces the Last Action.
    closeFlow('Cancelled. No changes were recorded.');
  }, [closeFlow]);

  const backTo = (parent?: Flow) =>
    parent ? () => setFlow(parent) : undefined;

  /**
   * Route a server resolution to the applicable one-shot dialog
   * (GUI_DESIGN §4.7): quantity already in the Area → the action
   * dialog (Phase 5: receive more from another Area only); exactly one
   * valid source → the transfer's quantity/review flow; several → the
   * explicit source selection — never an automatic pick; nothing
   * transferable → the honest placeholder.
   */
  const openResolution = useCallback(
    (resolution: ScanResolution, parent?: Flow) => {
      if (resolution.transferBlockedReason !== null) {
        // The station's Area never receives a transfer (a terminal
        // Area — the Stockroom workflow, a later release): no Receive
        // action at all, whatever quantity sits here or elsewhere; the
        // candidates are information only.
        setFlow({ kind: 'no-quantity', resolution, parent });
        return;
      }
      if (resolution.resolution === 'ALREADY_IN_AREA') {
        setFlow({ kind: 'in-area', resolution, parent });
        return;
      }
      if (resolution.candidates.length === 1) {
        setFlow({
          kind: 'transfer',
          resolution,
          candidate: resolution.candidates[0],
          parent,
        });
        return;
      }
      if (resolution.candidates.length > 1) {
        setFlow({ kind: 'source-select', resolution, parent });
        return;
      }
      setFlow({ kind: 'no-quantity', resolution, parent });
    },
    [],
  );

  const resolvePn = useCallback(
    async (
      input: { barcode: string } | { partNumber: string },
      parent?: Flow,
    ) => {
      setResolving(true);
      try {
        const resolution = await resolveScan(stationId, input);
        if (ready && resolution.area.id !== ready.area.id) {
          // The station was rebound since this page loaded: the
          // rendered context is stale. Never continue against it —
          // drop the pending workflow, reload the station and its
          // inventory, and ask for the scan again.
          setFlow(null);
          context.reload();
          inventory.reload();
          setNotice({
            kind: 'warn',
            icon: '⚠',
            title: 'Scan Station configuration changed',
            detail: `${stationId} is now bound to ${resolution.area.name}. The station was reloaded — scan the Part Number again. No changes were recorded.`,
          });
          focusScan();
          return;
        }
        openResolution(resolution, parent);
      } catch (error) {
        focusScan();
        setNotice({
          kind: 'err',
          icon: '✕',
          title: 'Part Number could not be resolved',
          detail: `${errorMessage(error)} No changes were recorded.`,
        });
      } finally {
        setResolving(false);
      }
    },
    [stationId, ready, context, inventory, openResolution, focusScan],
  );

  const resolveMachine = useCallback(
    async (barcode: string) => {
      setResolving(true);
      try {
        const resolution = await resolveMachineScan(stationId, { barcode });
        if (ready && resolution.area.id !== ready.area.id) {
          setFlow(null);
          context.reload();
          inventory.reload();
          setNotice({
            kind: 'warn',
            icon: '⚠',
            title: 'Scan Station configuration changed',
            detail: `${stationId} is now bound to ${resolution.area.name}. The station was reloaded — scan again. No changes were recorded.`,
          });
          focusScan();
          return;
        }
        const known = inventoryReady?.machines.map((card) => card.machine);
        const machines = known?.some(
          (machine) => machine.id === resolution.machine.id,
        )
          ? known.map((machine) =>
              machine.id === resolution.machine.id
                ? resolution.machine
                : machine,
            )
          : [...(known ?? []), resolution.machine];
        setFlow({
          kind: 'assign',
          machines,
          queued: resolution.queued,
          machineId: resolution.machine.id,
        });
      } catch (error) {
        // A retired, other-Area or maintenance Machine, or an unknown
        // Asset Tag: refused by the server with nothing resolved — the
        // station stays exactly as it was.
        focusScan();
        setNotice({
          kind: 'err',
          icon: '✕',
          title: 'Machine cannot be used here',
          detail: `${errorMessage(error)} No changes were recorded.`,
        });
      } finally {
        setResolving(false);
      }
    },
    [stationId, ready, context, inventory, inventoryReady, focusScan],
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

  const handleScan = useCallback(() => {
    const input = inputRef.current;
    if (!input) return;
    if (writeBlocked) {
      blockedNotice();
      return;
    }
    const raw = input.value;
    input.value = '';
    const parsed = parseScan(raw);
    switch (parsed.kind) {
      case 'empty':
        focusScan();
        return;
      case 'pn':
        // The verbatim scanner value goes to the server, which owns the
        // canonical PN rules (PROJECT_PROFILE §10); a PN resolution
        // opens a dialog — no refocus here.
        void resolvePn({ barcode: normalizeScanInput(raw) });
        return;
      case 'scrap':
        focusScan();
        setNotice({
          kind: 'err',
          icon: '✕',
          title: 'Scrap barcode cannot be used here',
          detail:
            'Scrap recording is not available at this station in this release. No changes were recorded.',
        });
        return;
      case 'machine':
        // Machine-first (GUI_DESIGN §4.6): the scan is a one-shot
        // shortcut into `Assign to Machine` with the Machine
        // preselected — the server validates the Machine (active, not
        // under maintenance, in this Area) and lists the queued
        // quantity; nothing sticks after the dialog.
        void resolveMachine(normalizeScanInput(raw));
        return;
      case 'area':
        focusScan();
        setNotice({
          kind: 'err',
          icon: '✕',
          title: 'Area barcode is not required here',
          detail:
            'This Scan Station is already assigned to an Area. Scan a Part Number barcode. No changes were recorded.',
        });
        return;
      case 'unknown':
        focusScan();
        setNotice({
          kind: 'err',
          icon: '✕',
          title: 'Barcode not recognized',
          detail:
            'Scan a PartFlow Part Number barcode. To type a Part Number, select “Enter PN manually.” No changes were recorded.',
        });
        return;
    }
  }, [writeBlocked, blockedNotice, focusScan, resolvePn, resolveMachine]);

  // Keyboard-wedge capture (§4.4): the main input never loses a scan
  // while no dialog is open — identical to the approved presentation.
  useEffect(() => {
    if (flow || writeBlocked || resolving) return;
    function onKeyDown(event: KeyboardEvent) {
      const input = inputRef.current;
      if (!input || event.defaultPrevented) return;
      if (event.ctrlKey || event.altKey || event.metaKey) return;
      const target = event.target;
      if (target === input) return;
      if (target instanceof HTMLElement) {
        if (target.isContentEditable) return;
        const tag = target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        // A focused non-dialog button keeps its native keyboard
        // activation (Enter/Space) while NO scan is buffered; scanner
        // characters are captured into the main input regardless — a
        // barcode is never lost because a button held focus — and the
        // terminating Enter of a buffered scan submits it.
        if (tag === 'BUTTON' && (event.key === ' ' || event.key === 'Enter')) {
          if (event.key === 'Enter' && input.value) {
            event.preventDefault();
            handleScan();
          }
          return;
        }
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
  }, [flow, writeBlocked, resolving, handleScan]);

  /** A transfer the SERVER confirmed: refresh the Area, note it, refocus. */
  const completeTransfer = useCallback(
    (
      result: TransferResult,
      candidate: TransferCandidate,
      destination: string,
    ) => {
      // A transfer of ON_MACHINE quantity is ONE application command:
      // the server appended AREA_COMPLETED then TRANSFERRED together.
      const completed = result.completedMovementId !== null;
      const events = completed
        ? `AREA_COMPLETED #${result.completedMovementId} + TRANSFERRED #${result.movementId}`
        : `TRANSFERRED #${result.movementId}`;
      setLastAction({
        pn: result.partNumber,
        summary: `${completed ? 'AREA_COMPLETED + TRANSFERRED' : 'TRANSFERRED'} · ${candidate.currentArea.name} → ${destination} · qty ${result.quantity}`,
      });
      setNotice({
        kind: 'ok',
        icon: '✓',
        title: `${result.partNumber} × ${result.quantity} → ${destination}`,
        detail: result.created
          ? `${completed ? `Processing at ${candidate.currentArea.name} was completed and the` : 'The'} quantity moved here from ${candidate.currentArea.name}. Recorded by the server (${events}).`
          : `This transfer was already recorded by the server (${events}) — nothing was recorded twice.`,
      });
      // The station context, the Area inventory (with its Machine
      // cards) and the header totals refresh from the server
      // (PROJECT_PROFILE §15 step 10) — never from an optimistic guess.
      context.reload();
      inventory.reload();
      setFlow(null);
      focusScan();
    },
    [context, inventory, focusScan],
  );

  /** A Machine-Area action the SERVER confirmed: refresh, note, refocus. */
  const completeMachineAction = useCallback(
    (result: MachineActionResult, machineName: string) => {
      const areaName = ready?.area.name ?? 'the Area';
      const description =
        result.movementType === 'ASSIGNED_TO_MACHINE'
          ? `Area queue → ${machineName}`
          : result.movementType === 'AREA_COMPLETED'
            ? `${machineName} → Finished — ready to move`
            : `${machineName} → ${areaName} queue`;
      setLastAction({
        pn: result.partNumber,
        summary: `${result.movementType} · ${description} · qty ${result.quantity}`,
      });
      const outcome =
        result.movementType === 'ASSIGNED_TO_MACHINE'
          ? `assigned to ${machineName}`
          : result.movementType === 'AREA_COMPLETED'
            ? `finished on ${machineName} — on the ${areaName} finished rack, ready to transfer`
            : `returned from ${machineName} to the ${areaName} queue — it remains unfinished`;
      setNotice({
        kind: 'ok',
        icon: '✓',
        title: `${result.partNumber} × ${result.quantity} ${outcome}`,
        detail: result.created
          ? `Recorded by the server (${result.movementType} #${result.movementId}).`
          : `This action was already recorded by the server (${result.movementType} #${result.movementId}) — nothing was recorded twice.`,
      });
      context.reload();
      inventory.reload();
      setFlow(null);
      focusScan();
    },
    [context, inventory, focusScan, ready],
  );

  /**
   * The server refused a write (nothing recorded — the flow moved, the
   * Machine changed, the station was rebound…): re-read the Area so the
   * state the operator returns to after Back/Cancel is the server's,
   * not the one the server just rejected. The open dialog keeps its
   * own entered values.
   */
  const refreshAfterRejection = useCallback(() => {
    context.reload();
    inventory.reload();
  }, [context, inventory]);

  const abandonUnknown = useCallback(
    (what: string) => {
      // The operator leaves an action whose outcome the server never
      // answered: the Area is re-read from the server and the next scan
      // shows where the quantity actually is.
      setFlow(null);
      context.reload();
      inventory.reload();
      setNotice({
        kind: 'warn',
        icon: '⚠',
        title: `${what} outcome unknown`,
        detail: `The server did not answer, so the ${what.toLowerCase()} may or may not have been recorded. The Area was reloaded — scan again to see where the quantity is.`,
      });
      focusScan();
    },
    [context, inventory, focusScan],
  );

  const area = ready ? presentationArea(ready.area, ready.operations) : null;
  const hasMachines =
    ready?.hasMachines ?? (inventoryReady?.machines.length ?? 0) > 0;
  const presented = useMemo(
    () =>
      inventoryReady && area
        ? presentInventory(inventoryReady, area.key, hasMachines)
        : EMPTY_PRESENTATION,
    [inventoryReady, area, hasMachines],
  );
  const { cards, machines } = presented;
  const machineCardEntries = useMemo(
    () => splitAssignments(cards).assigned,
    [cards],
  );

  /**
   * Machine-card row actions (GUI_DESIGN §4.6): DONE and QUEUE — two
   * distinct one-shot actions, never merged. Each opens its wizard for
   * exactly the Quantity Flow of that row; while writes are blocked
   * they stay disabled in place.
   */
  const machineRowAction = (entry: AreaAssignment) => {
    const flowOfRow = presented.flowOf.get(entry.card);
    const machineOfRow = presented.machineByName.get(entry.context);
    if (!flowOfRow || !machineOfRow) return null;
    const open = (action: 'DONE' | 'QUEUE') =>
      setFlow({
        kind: 'machine-action',
        action,
        flow: flowOfRow,
        machine: machineOfRow,
      });
    return (
      <>
        <button
          className="rowact done"
          aria-label="Complete Area processing"
          title="Complete processing — move this quantity to the finished rack, ready to transfer"
          disabled={writeBlocked}
          onClick={() => open('DONE')}
        >
          <span className="ric" aria-hidden="true">
            ✓
          </span>
          DONE
        </button>
        <button
          className="rowact"
          aria-label="Return to Area queue"
          title="Return unfinished or paused quantity to the Area queue"
          disabled={writeBlocked}
          onClick={() => open('QUEUE')}
        >
          <span className="ric" aria-hidden="true">
            ⟲
          </span>
          QUEUE
        </button>
      </>
    );
  };

  if (context.state.status === 'loading') {
    // No scan input yet: the station is usable only once its context
    // (Area, Operations) is known — a scan before that has nowhere to
    // resolve.
    return (
      <section className="ss" aria-label="Scan Station">
        <LoadingState label="Loading Scan Station" />
      </section>
    );
  }
  if (context.state.status === 'error') {
    return (
      <UnknownStation
        stationId={stationId}
        detail={context.state.message}
        onRetry={context.reload}
      />
    );
  }
  const station = ready!;
  const destinationNote = hasMachines
    ? `${station.area.name} queue (awaiting Machine)`
    : `${station.area.name} — direct processing`;

  return (
    <section
      className={`ss${productionMode ? ' production' : ''}`}
      aria-label="Scan Station"
    >
      <header
        ref={headRef}
        className={headWrapped ? 'ss-head wrapped' : 'ss-head'}
      >
        <div className="ss-id">
          <div className="dept">{station.department.name}</div>
          <div className="area">
            <AreaDot colorVar={areaRefColor(station.area)} size={16} />
            {station.area.name}
          </div>
          <HeaderOperations
            operations={station.operations.map(operationLabel)}
          />
        </div>
        {/* The Area totals render only from a server inventory —
            never a zero row before the first response. */}
        <div className="ss-stats" aria-label="Area statistics">
          {(area && inventoryReady
            ? areaStats(area, cards, hasMachines)
            : []
          ).map((s) => (
            <div className="stat" key={s.label}>
              <div className={`n ${s.tone ?? ''}`}>{s.value}</div>
              <div className="l">{s.label}</div>
            </div>
          ))}
        </div>
        {/* Worker identity arrives with the Worker-session workflows
            (the Area's Worker ID mode is not configured before then):
            no Worker pill renders, exactly like a Disabled Area. In
            production mode the connectivity chip and the theme control
            keep their place in the header actions column. */}
        {productionMode ? (
          <div className="ss-headgroup">
            <div className="ss-headactions">
              <ConnectivityChip />
              <ThemeToggle compact />
            </div>
          </div>
        ) : null}
      </header>

      <div className="ss-body">
        <div className="panel">
          <div className="ph">
            Scan barcode
            <span className="spacer" />
            <span className="note">
              {hasMachines
                ? 'Scan a Part Number to receive or assign its quantity, or a Machine to assign queued quantity to it.'
                : 'Scan a Part Number to receive its quantity from another Area.'}
            </span>
          </div>
          <div className="ss-scanwrap">
            <div className="ss-scanrow">
              <input
                ref={inputRef}
                className="ss-scaninput"
                autoComplete="off"
                inputMode={touchPrimary ? 'none' : undefined}
                disabled={writeBlocked || resolving}
                placeholder={
                  disconnected
                    ? 'Disconnected — scanning disabled'
                    : status === 'connecting'
                      ? 'Connecting…'
                      : resolving
                        ? 'Resolving Part Number…'
                        : 'Scan Part Number or Machine barcode · Press Enter'
                }
                aria-label="Scan barcode"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleScan();
                }}
              />
              <button
                className="ss-manualbtn"
                onClick={() => setFlow({ kind: 'manual-pn' })}
                disabled={writeBlocked || resolving}
              >
                ⌨ Enter PN manually
              </button>
            </div>
            <div className="ss-manualcap">
              Use manual entry only when the scanner is unavailable. The Part
              Number will be validated before any action is recorded.
            </div>
            <DevNotice>
              Development build — this station records real transfers and
              Machine actions on the server. The mock preview of the later
              workflows (direct-processing DONE, Repair, Scrap, Undo, Worker
              sessions) opens with <code>?preview=mock</code> on this route.
            </DevNotice>
            <div className="ss-lastpnlabel">Last Action</div>
            <div className="ss-lastpn">
              <div className="ss-lastpninfo">
                <span className="p">{lastAction?.pn ?? '—'}</span>
                <span className="d">
                  {lastAction?.summary ?? 'No Part Number actions yet'}
                </span>
              </div>
              {/* Undo reverses a complete application command (Phase 9);
                  until then the action region stays present and
                  disabled — never a hidden control. */}
              <button
                className="ss-undo zone-action"
                disabled
                title="Undo is not available in this release"
              >
                ⟲ UNDO
              </button>
            </div>
          </div>
        </div>

        {inventory.state.status === 'error' ? (
          <ErrorState
            message="The Area inventory could not be loaded."
            detail={inventory.state.message}
            onRetry={inventory.reload}
          />
        ) : inventoryLoading ? (
          <LoadingState label="Loading Area inventory" />
        ) : (
          <AreaMachineLayout
            summary={
              area ? (
                // In an Area with Machines the summary carries no row
                // actions (assignment comes through PN scan, Machine
                // scan and the action dialog; DONE / QUEUE live on the
                // Machine cards). Direct-processing DONE is Phase 7.
                <AreaSummaryCard
                  area={area}
                  cards={cards}
                  machines={machines}
                  title="In this Area now"
                  showStats={false}
                />
              ) : null
            }
            machineCards={machines.map((machine) => (
              <MachineMonitoringCard
                key={machine.name}
                machine={machine}
                entries={machineCardEntries.filter(
                  (entry) => entry.context === machine.name,
                )}
                rowAction={machineRowAction}
              />
            ))}
          />
        )}
      </div>

      {notice ? (
        <FloatingNotice notice={notice} onClose={() => setNotice(null)} />
      ) : null}

      {flow?.kind === 'source-select' && (
        <SourceSelectDialog
          resolution={flow.resolution}
          onPick={(candidate) =>
            setFlow({
              kind: 'transfer',
              resolution: flow.resolution,
              candidate,
              parent: flow,
            })
          }
          onBack={backTo(flow.parent)}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'transfer' && (
        <TransferDialog
          station={station}
          resolution={flow.resolution}
          candidate={flow.candidate}
          destinationNote={destinationNote}
          writeBlocked={writeBlocked}
          onBack={backTo(flow.parent)}
          onDone={(result) =>
            completeTransfer(result, flow.candidate, destinationNote)
          }
          onCancel={cancelFlow}
          onRejected={refreshAfterRejection}
          onAbandonUnknown={() => abandonUnknown('Transfer')}
        />
      )}
      {flow?.kind === 'assign' && (
        <AssignToMachineDialog
          station={station}
          machines={flow.machines}
          queued={flow.queued}
          preselectedMachineId={flow.machineId}
          preselectedFlow={flow.flow}
          writeBlocked={writeBlocked}
          onBack={backTo(flow.parent)}
          onCancel={cancelFlow}
          onDone={(result, machine) =>
            completeMachineAction(result, machine.name)
          }
          onRejected={refreshAfterRejection}
          onAbandonUnknown={() => abandonUnknown('Assignment')}
        />
      )}
      {flow?.kind === 'machine-action' && (
        <MachineActionDialog
          kind={flow.action}
          station={station}
          flow={flow.flow}
          machine={flow.machine}
          writeBlocked={writeBlocked}
          onBack={backTo(flow.parent)}
          onCancel={cancelFlow}
          onDone={(result) => completeMachineAction(result, flow.machine.name)}
          onRejected={refreshAfterRejection}
          onAbandonUnknown={() =>
            abandonUnknown(
              flow.action === 'DONE' ? 'Completion' : 'Queue return',
            )
          }
        />
      )}
      {flow?.kind === 'in-area' && (
        <InAreaDialog
          resolution={flow.resolution}
          hasMachines={hasMachines}
          machines={inventoryReady?.machines.map((card) => card.machine) ?? []}
          onAssign={(queuedFlow) =>
            setFlow({
              kind: 'assign',
              machines:
                inventoryReady?.machines.map((card) => card.machine) ?? [],
              queued:
                inventoryReady?.queued.flatMap((line) => line.flows) ?? [],
              flow: queuedFlow,
              parent: flow,
            })
          }
          onComplete={(onMachineFlow, machine) =>
            setFlow({
              kind: 'machine-action',
              action: 'DONE',
              flow: onMachineFlow,
              machine,
              parent: flow,
            })
          }
          onReceiveMore={() =>
            flow.resolution.candidates.length === 1
              ? setFlow({
                  kind: 'transfer',
                  resolution: flow.resolution,
                  candidate: flow.resolution.candidates[0],
                  parent: flow,
                })
              : setFlow({
                  kind: 'source-select',
                  resolution: flow.resolution,
                  parent: flow,
                })
          }
          onBack={backTo(flow.parent)}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'no-quantity' && (
        <NoQuantityDialog
          resolution={flow.resolution}
          onBack={backTo(flow.parent)}
          onCancel={cancelFlow}
        />
      )}
      {flow?.kind === 'manual-pn' && (
        <ManualEntryDialog
          initialPn={flow.initialPn}
          examplePn="1234-56-7890-01"
          onCancel={cancelFlow}
          onConfirm={(pn) => {
            setFlow(null);
            if (!pn) {
              focusScan();
              return;
            }
            // The resolved dialog can go Back to manual entry with the
            // entered PN preserved for correction.
            void resolvePn(
              { partNumber: pn },
              { kind: 'manual-pn', initialPn: pn },
            );
          }}
        />
      )}

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
/* Dialog building blocks                                              */
/* ------------------------------------------------------------------ */

/** Area entity inside recaps and confirmation summaries: the stable
 * Area color dot beside plain text. */
function AreaChip({ area, children }: { area: AreaRef; children: ReactNode }) {
  return (
    <EntityChip>
      <AreaDot colorVar={areaRefColor(area)} />
      {children}
    </EntityChip>
  );
}

/**
 * Work Order context line of a transfer recap (GUI_DESIGN §4.7 item
 * 2): `WO` stays a plain label, the WO Number value carries the shared
 * `.rval` emphasis (`—` for an internal blank number) and the Request
 * Type is the shared chip.
 */
function WorkOrderContextLine({
  workOrder,
}: {
  workOrder: WorkOrderContext | null;
}) {
  if (!workOrder) return <>WO —</>;
  return (
    <>
      WO <b className="rval">{workOrder.workOrderNumber ?? '—'}</b>
      {' · '}
      <TypeChip type={workOrder.requestType} />
    </>
  );
}

/** Route-mode chip detail: the actual trace for FLOATING, the route
 * expectation for PLANNED (GUI_DESIGN §7.2 `RouteModeChip`). */
function routeDetail(candidate: TransferCandidate): string {
  if (candidate.routeMode === 'FLOATING') return 'actual trace';
  return candidate.routeStatus === 'ON_ROUTE' ? 'next step' : 'route deviation';
}

function candidateLabel(candidate: TransferCandidate): string {
  return `${candidate.currentArea.name} — ${candidate.quantity} pcs available`;
}

/* ------------------------------------------------------------------ */
/* Source selection (several valid sources)                            */
/* ------------------------------------------------------------------ */

function SourceSelectDialog({
  resolution,
  onPick,
  onBack,
  onCancel,
}: {
  resolution: ScanResolution;
  onPick: (candidate: TransferCandidate) => void;
  onBack?: () => void;
  onCancel: () => void;
}) {
  return (
    <ModalDialog label="Select the source" onClose={onCancel}>
      <h3>Select the source</h3>
      <div className="big mono">{resolution.partNumber}</div>
      <div className="sub">
        This Part Number is available in more than one place. Select exactly one
        source to continue — quantities are never combined.
      </div>
      {resolution.candidates.map((candidate) => (
        <button
          key={candidate.quantityFlowId}
          className="choice"
          onClick={() => onPick(candidate)}
        >
          <span className="cic run" aria-hidden="true">
            SRC
          </span>
          <span>
            <span className="ct1">{candidateLabel(candidate)}</span>
            <br />
            <span className="ct2">
              {workOrderLabel(candidate.workOrder)} ·{' '}
              <RouteModeChip
                mode={candidate.routeMode}
                detail={routeDetail(candidate)}
              />
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

/* ------------------------------------------------------------------ */
/* Transfer — quantity/review → confirmation → server write            */
/* ------------------------------------------------------------------ */

function TransferDialog({
  station,
  resolution,
  candidate,
  destinationNote,
  writeBlocked,
  onBack,
  onDone,
  onCancel,
  onRejected,
  onAbandonUnknown,
}: {
  station: StationContext;
  resolution: ScanResolution;
  candidate: TransferCandidate;
  destinationNote: string;
  writeBlocked: boolean;
  onBack?: () => void;
  /** Called ONLY with a server-confirmed result. */
  onDone: (result: TransferResult) => void;
  onCancel: () => void;
  /** The server refused the transfer (nothing recorded): the owner
   * re-reads the Area so Back/Cancel never return to refused state. */
  onRejected?: () => void;
  /** The operator abandons a transfer whose outcome is UNKNOWN (the
   * server never answered): the owner re-reads the Area. */
  onAbandonUnknown: () => void;
}) {
  const pn = resolution.partNumber;
  const operations = resolution.operations;
  // The destination is the Area the scan just resolved against — the
  // one the operator confirms — never a stale station context (the
  // owner already verified both agree before opening this dialog).
  const destination = resolution.area;
  const [step, setStep] = useState<'qty' | 'confirm'>('qty');
  // MAX = the whole Quantity Flow, and in this release the ONLY valid
  // quantity: partial movement needs SPLIT (a later release) and is
  // refused clearly — never submitted.
  const [qty, setQty] = useState(String(candidate.quantity));
  const parsedQty = parseInt(qty || '0', 10);
  const fullQuantity = parsedQty === candidate.quantity;
  // Several active Operations at the destination ALWAYS take an
  // explicit choice — the Planned Route's expected Operation is
  // guidance shown beside the choice, never a pre-selection that hides
  // it. A single Operation resolves itself.
  const operationRequired = operations.length > 1;
  const [operationId, setOperationId] = useState<number | null>(
    operationRequired ? null : (candidate.suggestedOperationId ?? null),
  );
  const operation = operations.find((item) => item.id === operationId) ?? null;
  const valid = fullQuantity && operation !== null;
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  // The server never answered a submission: it may have committed the
  // transfer and lost the response. From here on the intent is FROZEN
  // (no Operation/reason/Back changes) and the only way forward is the
  // exact same request with the same device_event_id — which replays
  // the committed transfer or records it once.
  const [outcomeUnknown, setOutcomeUnknown] = useState(false);
  // A known refusal: the dialog keeps only Retry / Cancel — Back would
  // return to the source/action snapshot the server just refused.
  const [rejected, setRejected] = useState(false);
  // One idempotency key per transfer intent, reused verbatim on every
  // retry — a retry after an unknown outcome replays the committed
  // transfer instead of recording it twice.
  const deviceEventId = useRef(newDeviceEventId());

  // Route deviation (PROJECT_PROFILE §17): the station's Area is not
  // the Planned Route's next step, or the chosen Operation is not the
  // step's Operation. Both need explicit confirmation with a reason;
  // the server decides authoritatively and may ask again.
  const [serverDeviation, setServerDeviation] = useState<string | null>(null);
  const localDeviation =
    candidate.routeStatus === 'DEVIATION'
      ? candidate.expectedNextArea
        ? `The Planned Route expects this quantity at ${candidate.expectedNextArea.name} next, not at ${station.area.name}.`
        : `${station.area.name} is not on this quantity's Planned Route.`
      : candidate.routeStatus === 'ON_ROUTE' &&
          candidate.expectedOperationId !== null &&
          operation !== null &&
          operation.id !== candidate.expectedOperationId
        ? `The Planned Route expects Operation ${operationLabel(
            operations.find(
              (item) => item.id === candidate.expectedOperationId,
            ) ?? {
              code: '?',
              name: null,
            },
          )} at ${destination.name}, not ${operationLabel(operation)}.`
        : null;
  const deviation = serverDeviation ?? localDeviation;
  const reasonMissing = deviation !== null && reason.trim() === '';
  // Quantity still ON a Machine at the source is completed implicitly
  // by this transfer (PROJECT_PROFILE §8.11): one application command
  // appends AREA_COMPLETED then TRANSFERRED — the confirmation says so.
  const implicitCompletion = candidate.processingState === 'ON_MACHINE';

  function goConfirm() {
    if (valid) setStep('confirm');
  }

  async function confirm() {
    if (!valid || busy || reasonMissing) return;
    if (writeBlocked) {
      setServerError(
        'Connection lost — the transfer was not sent. Reconnect and confirm again; nothing was recorded.',
      );
      return;
    }
    setBusy(true);
    setServerError(null);
    // Only the request itself is guarded: a server-confirmed result
    // must never be re-classified as an unknown outcome because the
    // completion handler failed afterwards.
    let result: TransferResult;
    try {
      result = await transferToStationArea({
        stationId: station.stationId,
        partNumber: pn,
        quantityFlowId: candidate.quantityFlowId,
        sourceAreaId: candidate.currentArea.id,
        targetAreaId: destination.id,
        quantity: candidate.quantity,
        operationId: operation!.id,
        confirmRouteDeviation: deviation !== null,
        routeDeviationReason: deviation !== null ? reason.trim() : null,
        deviceEventId: deviceEventId.current,
      });
    } catch (error) {
      if (transferOutcomeUnknown(error)) {
        // Transport failure, timeout or 5xx: the request may or may
        // not have reached and been committed by the server. NEVER
        // "nothing changed" — the outcome is unknown until the same
        // request is answered by the application.
        setOutcomeUnknown(true);
        setServerError(null);
      } else if (routeDeviationConfirmation(error)) {
        // The server sees a deviation the candidate did not show (the
        // route position changed meanwhile): present it and require the
        // reason before the SAME intent is resubmitted. Nothing was
        // recorded.
        setServerDeviation(errorMessage(error));
        setRejected(true);
        onRejected?.();
      } else {
        // An explicit application rejection (4xx): nothing was recorded.
        setServerError(errorMessage(error));
        setRejected(true);
        onRejected?.();
      }
      setBusy(false);
      return;
    }
    setBusy(false);
    onDone(result);
  }

  const cancel = outcomeUnknown ? onAbandonUnknown : onCancel;

  const quantityGuidance =
    parsedQty > candidate.quantity ? (
      <Guidance tone="error">
        Quantity cannot exceed the {candidate.quantity} pcs currently available
        at {candidate.currentArea.name}.
      </Guidance>
    ) : parsedQty < candidate.quantity ? (
      <Guidance tone="error">
        Partial transfer is not available in this release: this quantity of{' '}
        {candidate.quantity} pcs moves as a whole. Enter {candidate.quantity} or
        cancel — nothing is recorded.
      </Guidance>
    ) : null;

  return (
    <ModalDialog
      label="Receive from another Area"
      onClose={busy ? () => undefined : cancel}
      onKeyDown={
        step === 'qty'
          ? quantityKeyHandler(qty, setQty, goConfirm)
          : enterKeyHandler(() => void confirm())
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
                <AreaChip area={candidate.currentArea}>
                  {candidate.currentArea.name}
                </AreaChip>{' '}
                → <AreaChip area={destination}>{destinationNote}</AreaChip>
              </>,
              <WorkOrderContextLine workOrder={candidate.workOrder} />,
              ...(implicitCompletion
                ? [
                    <>
                      This quantity is still on a Machine at{' '}
                      {candidate.currentArea.name}: transferring it completes
                      that processing first.
                    </>,
                  ]
                : []),
            ]}
          />
          {operationRequired ? (
            <>
              <Guidance tone="action">
                {destination.name} supports several Operations. Select the
                Operation this quantity is transferred for.
                {candidate.expectedOperationId !== null
                  ? ' The Planned Route expects the one marked planned; another choice is a route deviation.'
                  : ''}
              </Guidance>
              <div className="ss-choices" role="group" aria-label="Operation">
                {operations.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`choice${operationId === item.id ? ' selected' : ''}`}
                    aria-pressed={operationId === item.id}
                    onClick={() => setOperationId(item.id)}
                  >
                    <span className="cic" aria-hidden="true">
                      OP
                    </span>
                    <span>
                      <span className="ct1">{operationLabel(item)}</span>
                      {item.id === candidate.expectedOperationId ? (
                        <>
                          <br />
                          <span className="ct2">planned for this step</span>
                        </>
                      ) : null}
                    </span>
                  </button>
                ))}
              </div>
            </>
          ) : operation ? (
            <StepRecap
              lines={[
                <>
                  Operation <EntityChip>{operationLabel(operation)}</EntityChip>
                </>,
              ]}
            />
          ) : (
            <Guidance tone="error">
              {destination.name} has no active Operation configured. Configure
              one in Administration → Operations before receiving quantity.
            </Guidance>
          )}
          <Guidance tone="info">
            Available at {candidate.currentArea.name}:{' '}
            <b>{candidate.quantity} pcs</b>. The full quantity moves as a whole.
          </Guidance>
          {quantityGuidance}
          <QuantityKeypad
            value={qty}
            onChange={setQty}
            max={candidate.quantity}
          />
          <StepButtons
            onBack={onBack}
            onCancel={onCancel}
            primary={{ label: 'Next', onClick: goConfirm, disabled: !valid }}
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
                <span className="mono">{candidate.quantity} pcs</span>,
                'primary',
              ],
              [
                'Source',
                <AreaChip area={candidate.currentArea}>
                  {candidate.currentArea.name}
                </AreaChip>,
                'primary',
              ],
              [
                'Destination',
                <AreaChip area={destination}>{destinationNote}</AreaChip>,
                'primary',
              ],
              [
                'Operation',
                operation ? (
                  <EntityChip>{operationLabel(operation)}</EntityChip>
                ) : null,
                'primary',
              ],
              [
                'Work Order',
                <WorkOrderContextLine workOrder={candidate.workOrder} />,
              ],
              [
                'Route',
                <RouteModeChip
                  mode={candidate.routeMode}
                  detail={routeDetail(candidate)}
                />,
                'secondary',
              ],
              ['Route deviation', deviation, undefined, 'warn'],
              [
                'Source processing',
                implicitCompletion
                  ? `Completed at ${candidate.currentArea.name} by this transfer`
                  : null,
                'primary',
                'warn',
              ],
              ['Remaining at source', <span className="mono">0 pcs</span>],
              ['Scan Station', station.stationId, 'secondary'],
              [
                'Recorded event',
                implicitCompletion
                  ? 'AREA_COMPLETED + TRANSFERRED (one command)'
                  : 'TRANSFERRED',
                'secondary',
              ],
            ]}
          />
          {deviation !== null ? (
            <>
              <Guidance tone="warn">
                Confirming records the actual transfer as a route deviation. A
                reason is required.
              </Guidance>
              <input
                aria-label="Route deviation reason"
                className="field"
                autoComplete="off"
                value={reason}
                disabled={busy || outcomeUnknown}
                placeholder="Reason for leaving the Planned Route"
                onChange={(event) => setReason(event.target.value)}
              />
            </>
          ) : null}
          {outcomeUnknown ? (
            <Guidance tone="warn">
              The server did not answer — this transfer may or may not have been
              recorded. Retry the exact same transfer to find out: the server
              answers with the recorded result, or records it once. Nothing can
              be changed until then.
            </Guidance>
          ) : null}
          {serverError ? <Guidance tone="error">{serverError}</Guidance> : null}
          {writeBlocked && !serverError && !outcomeUnknown ? (
            <Guidance tone="error">
              Disconnected — the transfer cannot be recorded until the
              connection returns.
            </Guidance>
          ) : null}
          {/* With the outcome unknown the intent is frozen: no Back to
              the choices (a different Operation or reason under the
              same device_event_id would be a different request). */}
          <StepButtons
            onBack={
              outcomeUnknown || rejected
                ? undefined
                : () => {
                    setServerError(null);
                    setStep('qty');
                  }
            }
            onCancel={cancel}
            cancelLabel={
              outcomeUnknown ? 'Leave — check the Area' : 'Cancel (Esc)'
            }
            primary={{
              label: outcomeUnknown
                ? 'Retry the same transfer'
                : serverError
                  ? 'Retry transfer'
                  : busy
                    ? 'Recording…'
                    : 'Confirm transfer',
              onClick: () => void confirm(),
              disabled: !valid || busy || reasonMissing || writeBlocked,
              autoFocus: true,
            }}
          />
        </div>
      )}
    </ModalDialog>
  );
}

/* ------------------------------------------------------------------ */
/* PN already in the Area                                              */
/* ------------------------------------------------------------------ */

function InAreaDialog({
  resolution,
  hasMachines,
  machines,
  onAssign,
  onComplete,
  onReceiveMore,
  onBack,
  onCancel,
}: {
  resolution: ScanResolution;
  hasMachines: boolean;
  /** The active Machines of the station's Area (for the ON_MACHINE rows). */
  machines: MachineRef[];
  /** PN-first assignment of ONE queued flow (GUI_DESIGN §4.7). */
  onAssign: (flow: FlowInArea) => void;
  /** `Complete Area processing on {Machine}` for ONE ON_MACHINE flow. */
  onComplete: (flow: FlowInArea, machine: MachineRef) => void;
  onReceiveMore: () => void;
  onBack?: () => void;
  onCancel: () => void;
}) {
  const inAreaQty = resolution.inArea.reduce(
    (sum, flow) => sum + flow.quantity,
    0,
  );
  const elsewhere = resolution.candidates.reduce(
    (sum, candidate) => sum + candidate.quantity,
    0,
  );
  // The valid choices come from the server's derived state of EACH
  // flow (PROJECT_PROFILE §12): a queued flow offers Assign, a flow on a
  // Machine offers completion on that Machine. Several flows of the PN
  // are several explicit choices — never one merged action.
  const queued = hasMachines
    ? resolution.inArea.filter((flow) =>
        flow.availableActions.includes('ASSIGN'),
      )
    : [];
  const onMachine = hasMachines
    ? resolution.inArea.flatMap((flow) => {
        if (!flow.availableActions.includes('DONE')) return [];
        const machine = machines.find((item) => item.id === flow.machineId);
        return machine ? [{ flow, machine }] : [];
      })
    : [];
  const finishedQty = resolution.inArea
    .filter((flow) => flow.processingState === 'READY_TO_TRANSFER')
    .reduce((sum, flow) => sum + flow.quantity, 0);
  return (
    <ModalDialog label="Select an action" onClose={onCancel}>
      <h3>Select an action</h3>
      <div className="big mono">{resolution.partNumber}</div>
      <div className="sub">
        <b>{inAreaQty} pcs</b> of this Part Number are already in{' '}
        {resolution.area.name}.
        {resolution.requiresSelection
          ? ' Several separate quantities are here — every choice below names exactly one.'
          : ''}
      </div>
      {queued.map((flow) => (
        <button
          key={`assign-${flow.quantityFlowId}`}
          className="choice"
          onClick={() => onAssign(flow)}
        >
          <span className="cic run" aria-hidden="true">
            ASG
          </span>
          <span>
            <span className="ct1">Assign to Machine</span>
            <br />
            <span className="ct2">
              {flow.quantity} pcs queued · {workOrderLabel(flow.workOrder)}
            </span>
          </span>
        </button>
      ))}
      {onMachine.map(({ flow, machine }) => (
        <button
          key={`done-${flow.quantityFlowId}`}
          className="choice"
          onClick={() => onComplete(flow, machine)}
        >
          <span className="cic" aria-hidden="true">
            ✓
          </span>
          <span>
            <span className="ct1">
              Complete Area processing on {machine.name}
            </span>
            <br />
            <span className="ct2">
              {flow.quantity} pcs on {machine.name} ·{' '}
              {workOrderLabel(flow.workOrder)}
            </span>
          </span>
        </button>
      ))}
      {finishedQty > 0 ? (
        <Guidance tone="info">
          {finishedQty} pcs are finished — ready to move. They leave this Area
          through a transfer at the destination station.
        </Guidance>
      ) : null}
      {resolution.candidates.length > 0 ? (
        <button className="choice" onClick={onReceiveMore}>
          <span className="cic run" aria-hidden="true">
            RCV
          </span>
          <span>
            <span className="ct1">Receive more quantity from another Area</span>
            <br />
            <span className="ct2">
              {elsewhere} pcs available in{' '}
              {resolution.candidates.length === 1
                ? resolution.candidates[0].currentArea.name
                : `${resolution.candidates.length} places`}
            </span>
          </span>
        </button>
      ) : (
        <Guidance tone="info">
          No other quantity of this Part Number is available to receive.
        </Guidance>
      )}
      <Guidance tone="info">
        {hasMachines
          ? 'Quantity additions, repair and scrap arrive with a later release.'
          : 'Completion in an Area without Machines, quantity additions, repair and scrap arrive with a later release.'}{' '}
        Nothing is recorded at this step.
      </Guidance>
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

/* ------------------------------------------------------------------ */
/* Nothing to transfer                                                 */
/* ------------------------------------------------------------------ */

function NoQuantityDialog({
  resolution,
  onBack,
  onCancel,
}: {
  resolution: ScanResolution;
  onBack?: () => void;
  onCancel: () => void;
}) {
  return (
    <ModalDialog label="No quantity to receive" onClose={onCancel}>
      <h3>No quantity to receive</h3>
      <div className="big mono">{resolution.partNumber}</div>
      <div className="sub">
        {resolution.transferBlockedReason
          ? resolution.transferBlockedReason
          : resolution.hasActiveDemand
            ? 'This Part Number has no active production quantity in another Area. Release quantity from its Work Order in Management → Work Orders first.'
            : 'This Part Number has no active Work Order Demand and no production quantity. Receiving new quantity at the station arrives with a later release — create the demand in Management → Work Orders.'}
      </div>
      <Guidance tone="info">Nothing was recorded.</Guidance>
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
