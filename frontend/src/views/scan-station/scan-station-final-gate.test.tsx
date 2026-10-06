import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';

// Real Scan Station with the badge-confirmation final gate (Phase 13 —
// PROJECT_PROFILE §16, §19; GUI_DESIGN §4.6, §4.12) against a fake
// in-memory `/api` that models the server's wire contract exactly: the
// context reports `worker_identification.final_gates` per sensitive
// action; DONE (`/area-completions`), QUEUE (`/machine-releases`) and
// Undo (`/undos`) accept an optional `confirming_badge`; the server
// enforces ITS gate form — a missing badge is refused 409
// `badge_confirmation_required`, an unexpected one 409
// `badge_confirmation_not_expected`, an unknown or inactive one 422
// `badge_not_recognized`, all with nothing recorded — and a valid gate
// badge signs that Worker in and is recorded on the action. Assignments
// refuse the field (an extra field → 422). A committed
// `device_event_id` replays whatever the badge.

const LATHE = 'LATHE-ST-01';
const CUT = 'CUT-ST-01';
const MINUTE = 60_000;
const E_G1 =
  'This action is now confirmed by a Worker badge scan. Scan your badge to confirm. Nothing was recorded.';
const E_G2 =
  'This action is no longer confirmed by a badge scan. Confirm it again. Nothing was recorded.';
const E_G3 =
  'Badge not recognized. Check the badge and scan again — nothing was recorded.';
const E_S1 =
  'No Worker is signed in at this Scan Station. Scan your badge to continue. Nothing was recorded.';
const E_S4 =
  'Another badge was scanned at this Scan Station at the same moment. Scan your badge again. Nothing was recorded.';
const GUIDANCE =
  'Scan a Worker badge to confirm — the badge identifies the confirming Worker and completes the action.';

type Gate = 'BADGE' | 'QUESTION';
type Gates = { done: Gate; queue: Gate; undo: Gate };
type State = 'QUEUED' | 'ON_MACHINE' | 'PROCESSING' | 'READY_TO_TRANSFER';

interface FakeWorker {
  id: number;
  name: string;
  badge: string;
  active: boolean;
}

const NGUYEN: FakeWorker = {
  id: 7,
  name: 'H. Nguyen',
  badge: '100482',
  active: true,
};
const TRAN: FakeWorker = {
  id: 8,
  name: 'V. Tran',
  badge: 'V-100517',
  active: true,
};
const MAI: FakeWorker = { id: 9, name: 'Mai', badge: 'B-77', active: false };
const WORKERS = [NGUYEN, TRAN, MAI];

const AREAS = [
  { id: 2, name: 'Lathe', color: '#33aa66' },
  { id: 3, name: 'Cut', color: '#3366ff' },
];
const STATIONS = [
  { station_id: LATHE, area_id: 2 },
  { station_id: CUT, area_id: 3 },
];
const OPERATIONS = [
  { id: 20, area_id: 2, code: 'TURN', name: 'Turning' },
  { id: 30, area_id: 3, code: 'CUT', name: 'Cutting' },
];
const MACHINE = { id: 1, name: 'Lathe 1', tag: 'CD-0001' };

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
  state: State;
  machineId: number | null;
}

interface Recorded {
  type: string;
  flowId: number;
  pn: string;
  qty: number;
  areaId: number;
  machineId: number | null;
  before: { state: State; machineId: number | null };
  worker: FakeWorker | null;
  reversed: boolean;
}

interface ServerSession {
  worker: FakeWorker;
  startedAt: number;
  expiresAt: number;
}

let flows: Flow[];
/** The final-gate forms the context READ reports. */
let contextGates: Gates;
/** The final-gate forms the server ENFORCES on a command. */
let serverGates: Gates;
let serverSession: ServerSession | null;
let committed: Map<string, { status: number; body: unknown }>;
let recorded: Map<string, Recorded>;
let commandLog: string[];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextMovementId: number;
let healthDown: boolean;
/** Failure injected into the NEXT gated command. */
let writeFailure: null | 'network' | { status: number; body: unknown };
/** While set, the station context reads stay pending until it resolves. */
let contextHold: Promise<void> | null;

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

function validSession(): ServerSession | null {
  return serverSession !== null && serverSession.expiresAt > Date.now()
    ? serverSession
    : null;
}

function workerRef(worker: FakeWorker) {
  return { id: worker.id, name: worker.name, avatar_updated_at: null };
}

function sessionWire() {
  const session = validSession();
  if (session === null) return null;
  return {
    worker: workerRef(session.worker),
    started_at: iso(session.startedAt),
    expires_at: iso(session.expiresAt),
    server_now: iso(Date.now()),
  };
}

function signIn(worker: FakeWorker) {
  const current = validSession();
  if (current && current.worker.id === worker.id) {
    current.expiresAt = Date.now() + 15 * MINUTE;
    return;
  }
  serverSession = {
    worker,
    startedAt: Date.now(),
    expiresAt: Date.now() + 15 * MINUTE,
  };
}

function areaRef(areaId: number) {
  const area = AREAS.find((a) => a.id === areaId)!;
  return {
    id: area.id,
    name: area.name,
    color: area.color,
    description: null,
    is_terminal: false,
  };
}

function operationRef(areaId: number) {
  return {
    ...OPERATIONS.find((o) => o.area_id === areaId)!,
    is_external: false,
  };
}

function machineRef() {
  const assigned = flows
    .filter((f) => f.state === 'ON_MACHINE' && f.machineId === MACHINE.id)
    .reduce((s, f) => s + f.qty, 0);
  return {
    id: MACHINE.id,
    name: MACHINE.name,
    asset_tag: MACHINE.tag,
    barcode_value: `PF:MACHINE:${MACHINE.tag}`,
    operational_state: assigned > 0 ? 'RUNNING' : 'IDLE',
    state_changed_at: '2026-10-01T06:00:00Z',
    maintenance_since: null,
    maintenance_note: null,
    maintenance_expected_return: null,
  };
}

function actionsOf(state: State) {
  return state === 'QUEUED'
    ? ['ASSIGN', 'TRANSFER', 'SCRAP']
    : state === 'ON_MACHINE'
      ? ['DONE', 'QUEUE', 'TRANSFER', 'SCRAP']
      : state === 'PROCESSING'
        ? ['DONE', 'TRANSFER', 'SCRAP']
        : ['TRANSFER', 'SCRAP'];
}

function flowWire(flow: Flow) {
  return {
    part_number: flow.pn,
    quantity_flow_id: flow.id,
    quantity: flow.qty,
    route_mode: 'FLOATING',
    operation: { ...operationRef(flow.areaId), is_active: true },
    processing_state: flow.state,
    machine_id: flow.machineId,
    completed_machine: null,
    entered_at: '2026-10-01T06:30:00Z',
    available_actions: actionsOf(flow.state),
    work_order: null,
  };
}

function lines(items: Flow[]) {
  const byPn = new Map<string, Flow[]>();
  for (const flow of items) {
    byPn.set(flow.pn, [...(byPn.get(flow.pn) ?? []), flow]);
  }
  return [...byPn].map(([pn, group]) => ({
    part_number: pn,
    total_quantity: group.reduce((s, f) => s + f.qty, 0),
    flows: group.map(flowWire),
  }));
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function stationOf(url: string): { station_id: string; area_id: number } {
  const id = decodeURIComponent(/\/scan-stations\/([^/]+)\//.exec(url)![1]);
  return STATIONS.find((s) => s.station_id === id)!;
}

function inventory(areaId: number) {
  const here = flows.filter((f) => f.areaId === areaId);
  const byState = (state: State) => here.filter((f) => f.state === state);
  const sum = (items: Flow[]) => items.reduce((s, f) => s + f.qty, 0);
  const onMachine = byState('ON_MACHINE');
  return json({
    area: areaRef(areaId),
    demand_context: [],
    scrapped: [],
    has_machines: areaId === 2,
    lines: lines(here),
    total_part_numbers: lines(here).length,
    total_quantity: sum(here),
    queued: lines(byState('QUEUED')),
    queued_quantity: sum(byState('QUEUED')),
    machines:
      areaId === 2
        ? [
            {
              machine: machineRef(),
              lines: lines(onMachine),
              total_quantity: sum(onMachine),
            },
          ]
        : [],
    on_machine_quantity: sum(onMachine),
    processing: lines(byState('PROCESSING')),
    processing_quantity: sum(byState('PROCESSING')),
    finished: lines(byState('READY_TO_TRANSFER')),
    finished_quantity: sum(byState('READY_TO_TRANSFER')),
  });
}

/**
 * The server's final-gate judgement of one command (after the
 * idempotency fast path): null to proceed — with the Worker the command
 * records — or the refusal (nothing recorded).
 */
function judgeGate(
  key: keyof Gates,
  badge: unknown,
): { refusal: Response } | { worker: FakeWorker } {
  if (serverGates[key] === 'BADGE') {
    if (badge === undefined || badge === null) {
      return {
        refusal: json({ detail: E_G1, badge_confirmation_required: true }, 409),
      };
    }
    const canonical = String(badge).trim().toUpperCase();
    const worker = WORKERS.find((w) => w.active && w.badge === canonical);
    if (!worker) {
      return {
        refusal: json({ detail: E_G3, badge_not_recognized: true }, 422),
      };
    }
    // The gate badge signs that Worker in (open / switch / refresh).
    signIn(worker);
    return { worker };
  }
  if (badge !== undefined && badge !== null) {
    return {
      refusal: json(
        { detail: E_G2, badge_confirmation_not_expected: true },
        409,
      ),
    };
  }
  const session = validSession();
  if (!session) {
    return {
      refusal: json({ detail: E_S1, worker_session_required: true }, 409),
    };
  }
  session.expiresAt = Date.now() + 15 * MINUTE;
  return { worker: session.worker };
}

function injectedFailure(): Response | null {
  if (writeFailure === 'network') {
    writeFailure = null;
    throw new TypeError('Failed to fetch');
  }
  if (writeFailure) {
    const failure = writeFailure;
    writeFailure = null;
    return json(failure.body, failure.status);
  }
  return null;
}

function commit(deviceEventId: string, body: unknown): Response {
  committed.set(deviceEventId, { status: 200, body });
  return json(body, 201);
}

/** The Due Soon warning policy on the wire (the initial values). */
const DUE_SOON_POLICY_WIRE = {
  due_soon_min_days: 2,
  due_soon_lead_time_percent: 15,
  due_soon_max_days: 7,
  updated_at: '2026-10-01T08:00:00Z',
};

function handle(url: string, method: string, body: unknown): Response {
  // The Due Soon warning policy of the `In this Area now` due tones.
  if (url === '/api/policies/due-soon') return json(DUE_SOON_POLICY_WIRE);
  if (url === '/api/health') {
    return healthDown
      ? json({ status: 'unavailable' }, 503)
      : json({ status: 'ok' });
  }
  if (url === '/api/machines') return json([]);
  if (url === '/api/workers') {
    return json(
      WORKERS.map((worker) => ({
        id: worker.id,
        name: worker.name,
        badge_barcode: worker.badge,
        is_active: worker.active,
        avatar_updated_at: null,
        created_at: '2026-08-01T00:00:00Z',
        updated_at: '2026-08-01T00:00:00Z',
      })),
    );
  }
  if (/\/context$/.test(url)) {
    const station = stationOf(url);
    return json({
      station_id: station.station_id,
      department: { id: 1, name: 'Machining' },
      area: areaRef(station.area_id),
      operations: [operationRef(station.area_id)],
      has_machines: station.area_id === 2,
      worker_identification: {
        mode: 'SCANNED',
        fixed_worker: null,
        session: sessionWire(),
        final_gates: { ...contextGates },
      },
    });
  }
  const inv = /^\/api\/areas\/(\d+)\/inventory$/.exec(url);
  if (inv) return inventory(Number(inv[1]));

  if (/\/scans\/resolve$/.test(url)) {
    const station = stationOf(url);
    const pn = String((body as { barcode: string }).barcode)
      .slice('PF:PN:'.length)
      .toUpperCase();
    const inArea = flows.filter(
      (f) => f.pn === pn && f.areaId === station.area_id,
    );
    return json({
      part_number: pn,
      station_id: station.station_id,
      area: areaRef(station.area_id),
      resolution: inArea.length
        ? 'ALREADY_IN_AREA'
        : 'NO_TRANSFERABLE_QUANTITY',
      in_area: inArea.map(flowWire),
      candidates: [],
      operations: [operationRef(station.area_id)],
      has_active_demand: true,
      intake_available: false,
      part_number_known: true,
      internal_work_orders: [],
      active_quantity: [],
      transfer_blocked_reason: null,
      requires_selection: inArea.length > 1,
      combine_groups: [],
      scrapped_quantity: 0,
      stocked_quantity: 0,
      available_stocked_quantity: 0,
      stock_available: false,
      scanned_at: iso(Date.now()),
      worker_session: sessionWire(),
    });
  }
  if (/\/machine-scans\/resolve$/.test(url)) {
    const station = stationOf(url);
    const queued = flows.filter(
      (f) => f.areaId === station.area_id && f.state === 'QUEUED',
    );
    return json({
      station_id: station.station_id,
      area: areaRef(station.area_id),
      machine: machineRef(),
      assigned_quantity: 0,
      queued: queued.map(flowWire),
      requires_selection: queued.length > 1,
      worker_session: sessionWire(),
    });
  }
  if (/\/badge-scans$/.test(url)) {
    const badge = String((body as { badge: string }).badge)
      .trim()
      .toUpperCase();
    const worker = WORKERS.find((w) => w.active && w.badge === badge);
    if (!worker) {
      return json({
        outcome: 'UNKNOWN',
        mode: 'SCANNED',
        worker_session: sessionWire(),
        previous_worker: null,
      });
    }
    const previous = validSession()?.worker ?? null;
    const outcome =
      previous === null
        ? 'SIGNED_IN'
        : previous.id === worker.id
          ? 'REFRESHED'
          : 'SWITCHED';
    signIn(worker);
    return json({
      outcome,
      mode: 'SCANNED',
      worker_session: sessionWire(),
      previous_worker:
        outcome === 'SWITCHED' && previous ? workerRef(previous) : null,
    });
  }

  const action =
    /\/(machine-assignments|machine-releases|area-completions)$/.exec(url);
  if (action && method === 'POST') {
    const station = stationOf(url);
    const request = body as {
      part_number: string;
      quantity_flow_id: number;
      machine_id?: number;
      quantity: number;
      device_event_id: string;
      confirming_badge?: string | null;
    };
    const kind = action[1];
    if (kind === 'machine-assignments' && 'confirming_badge' in request) {
      return json({ detail: 'Extra inputs are not permitted.' }, 422);
    }
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay.body, replay.status);
    const failed = injectedFailure();
    if (failed) return failed;
    let worker: FakeWorker | null = validSession()?.worker ?? null;
    if (kind !== 'machine-assignments') {
      const judged = judgeGate(
        kind === 'area-completions' ? 'done' : 'queue',
        request.confirming_badge,
      );
      if ('refusal' in judged) return judged.refusal;
      worker = judged.worker;
    }
    const flow = flows.find((f) => f.id === request.quantity_flow_id)!;
    const before = { state: flow.state, machineId: flow.machineId };
    const type =
      kind === 'machine-assignments'
        ? 'ASSIGNED_TO_MACHINE'
        : kind === 'area-completions'
          ? 'AREA_COMPLETED'
          : 'RELEASED_FROM_MACHINE';
    flow.state =
      kind === 'machine-assignments'
        ? 'ON_MACHINE'
        : kind === 'area-completions'
          ? 'READY_TO_TRANSFER'
          : 'QUEUED';
    flow.machineId =
      kind === 'machine-assignments' ? (request.machine_id ?? null) : null;
    recorded.set(request.device_event_id, {
      type,
      flowId: flow.id,
      pn: flow.pn,
      qty: flow.qty,
      areaId: station.area_id,
      machineId: request.machine_id ?? null,
      before,
      worker,
      reversed: false,
    });
    commandLog.push(request.device_event_id);
    return commit(request.device_event_id, {
      movement_id: nextMovementId++,
      movement_type: type,
      quantity_flow_id: flow.id,
      part_number: flow.pn,
      quantity: flow.qty,
      area_id: station.area_id,
      machine_id: request.machine_id ?? null,
      operation_id: operationRef(station.area_id).id,
      station_id: station.station_id,
      processing_state: flow.state,
      source_quantity_flow_id: null,
      remainder_quantity_flow_id: null,
      remainder_quantity: null,
      device_event_id: request.device_event_id,
      occurred_at: '2026-10-05T12:00:00Z',
    });
  }

  const preview = /\/undo-preview\/([^/]+)$/.exec(url);
  if (preview && method === 'GET') {
    const eventId = decodeURIComponent(preview[1]);
    const record = recorded.get(eventId);
    if (!record) return json({ detail: 'No production event.' }, 404);
    const eligible =
      !record.reversed && commandLog[commandLog.length - 1] === eventId;
    const reversedBy = validSession()?.worker ?? null;
    return json({
      reverses_device_event_id: eventId,
      station_id: LATHE,
      kind: 'DONE',
      part_number: record.pn,
      quantity: record.qty,
      occurred_at: '2026-10-05T12:00:00Z',
      eligible,
      ineligible_reason: eligible ? null : 'This action was reversed.',
      movements: [
        {
          movement_id: 900,
          movement_type: record.type,
          movement_reason: null,
          quantity: record.qty,
          from_area: null,
          to_area: areaRef(record.areaId),
          machine_id: record.machineId,
          operation_id: operationRef(record.areaId).id,
        },
      ],
      restored: [
        {
          quantity_flow_id: record.flowId,
          quantity: record.qty,
          status: 'ACTIVE',
          area: areaRef(record.areaId),
          machine_id: record.before.machineId,
          processing_state: record.before.state,
        },
      ],
      worker: record.worker ? workerRef(record.worker) : null,
      reversed_by: reversedBy ? workerRef(reversedBy) : null,
      reason_required: false,
    });
  }
  if (/\/undos$/.test(url) && method === 'POST') {
    const request = body as {
      reverses_device_event_id: string;
      device_event_id: string;
      confirming_badge?: string | null;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay.body, replay.status);
    const failed = injectedFailure();
    if (failed) return failed;
    const judged = judgeGate('undo', request.confirming_badge);
    if ('refusal' in judged) return judged.refusal;
    const record = recorded.get(request.reverses_device_event_id)!;
    record.reversed = true;
    const flow = flows.find((f) => f.id === record.flowId)!;
    flow.state = record.before.state;
    flow.machineId = record.before.machineId;
    return commit(request.device_event_id, {
      reverses_device_event_id: request.reverses_device_event_id,
      reversed_kind: 'DONE',
      part_number: record.pn,
      station_id: LATHE,
      movements: [
        {
          movement_id: nextMovementId++,
          reverses_movement_id: 900,
          original_movement_type: record.type,
        },
      ],
      flows: [
        {
          quantity_flow_id: record.flowId,
          quantity: record.qty,
          status: 'ACTIVE',
          current_area_id: record.areaId,
          current_machine_id: flow.machineId,
        },
      ],
      device_event_id: request.device_event_id,
      occurred_at: '2026-10-05T12:05:00Z',
    });
  }
  return json({ detail: `Unhandled ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  flows = [
    {
      id: 103,
      pn: 'PN-ON',
      qty: 3,
      areaId: 2,
      state: 'ON_MACHINE',
      machineId: 1,
    },
    {
      id: 100,
      pn: 'PN-Q',
      qty: 5,
      areaId: 2,
      state: 'QUEUED',
      machineId: null,
    },
    {
      id: 105,
      pn: 'PN-CUT',
      qty: 6,
      areaId: 3,
      state: 'PROCESSING',
      machineId: null,
    },
  ];
  contextGates = { done: 'BADGE', queue: 'BADGE', undo: 'BADGE' };
  serverGates = { done: 'BADGE', queue: 'BADGE', undo: 'BADGE' };
  serverSession = {
    worker: NGUYEN,
    startedAt: Date.now(),
    expiresAt: Date.now() + 15 * MINUTE,
  };
  committed = new Map();
  recorded = new Map();
  commandLog = [];
  requests = [];
  nextMovementId = 500;
  healthDown = false;
  writeFailure = null;
  contextHold = null;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (!url.endsWith('/api/health') && url !== '/api/policies/due-soon')
        requests.push({ url, method, body });
      const hold =
        contextHold && url.endsWith('/context')
          ? contextHold
          : Promise.resolve();
      return hold.then(() => handle(url, method, body));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderStation(stationId = LATHE) {
  window.history.replaceState({}, '', `/scan-station/${stationId}`);
  render(<App />);
  const input = await screen.findByLabelText('Scan barcode');
  await screen.findByText('Total PNs');
  return input as HTMLInputElement;
}

function scan(value: string) {
  const input = screen.getByLabelText('Scan barcode');
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

function machineCard(): HTMLElement {
  const heading = screen.getByText(MACHINE.name, { selector: '.mname' });
  return heading.closest('.abd-machine') as HTMLElement;
}

function gateDialog(title: string) {
  return screen.findByRole('dialog', { name: title });
}

function gateField(gate: HTMLElement) {
  return within(gate).getByLabelText('Scan Worker badge') as HTMLInputElement;
}

function scanGateBadge(gate: HTMLElement, value: string) {
  const field = gateField(gate);
  fireEvent.change(field, { target: { value } });
  fireEvent.keyDown(field, { key: 'Enter' });
}

function commands(path: string) {
  return requests.filter((r) => r.method === 'POST' && r.url.endsWith(path));
}

function contextReads() {
  return requests.filter((r) => /\/context$/.test(r.url)).length;
}

async function notice() {
  return waitFor(() => {
    const toast = document.querySelector('.ss-toast');
    if (!toast) throw new Error('no notice');
    return toast as HTMLElement;
  });
}

/** Machine card → `Complete Area processing` / `Return to Area queue`
 * wizard → Next; returns the summary dialog. */
async function openMachineSummary(kind: 'DONE' | 'QUEUE') {
  fireEvent.click(
    within(machineCard()).getByRole('button', {
      name:
        kind === 'DONE' ? 'Complete Area processing' : 'Return to Area queue',
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name:
      kind === 'DONE'
        ? 'Complete Area processing'
        : 'Return unfinished quantity to queue',
  });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  return dlg;
}

function confirmButton(summary: HTMLElement, name: string | RegExp) {
  return within(summary).getByRole('button', { name });
}

/** A Machine DONE through the question form (the Undo target). */
async function completeWithQuestion() {
  serverGates.done = 'QUESTION';
  contextGates.done = 'QUESTION';
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const question = await gateDialog('Confirm finished quantity?');
  fireEvent.click(
    within(question).getByRole('button', { name: 'Yes — finished' }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Complete Area processing' }),
    ).toBeNull(),
  );
}

async function openUndo() {
  const undo = document.querySelector('button.ss-undo') as HTMLButtonElement;
  await waitFor(() => expect(undo).toBeEnabled());
  fireEvent.click(undo);
  return screen.findByRole('dialog', {
    name: 'Reverse this Part Number action?',
  });
}

/* ============ The BADGE form ============ */

test('DONE with the BADGE form opens the badge gate with the facts and the focused field; nothing is sent before the scan', async () => {
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));

  const gate = await gateDialog('Scan badge to confirm completion');
  expect(gate.className).toContain('alertdlg');
  expect(gate.className).toContain('tone-info');
  expect(gate.querySelector('.alertbadge')).toHaveTextContent('i');
  expect(gate).toHaveTextContent(
    'Are you sure Lathe 1 has finished 3 pcs of PN-ON?',
  );
  expect(within(gate).getByText('PN-ON', { selector: 'b.mono' })).toBeTruthy();
  expect(gate).toHaveTextContent(GUIDANCE);
  const field = gateField(gate);
  expect(field).toHaveAttribute(
    'placeholder',
    'Scan Worker badge · Press Enter',
  );
  await waitFor(() => expect(document.activeElement).toBe(field));
  expect(
    screen.queryByRole('dialog', { name: 'Confirm finished quantity?' }),
  ).toBeNull();
  expect(commands('/area-completions')).toHaveLength(0);
  // An empty Enter sends nothing.
  fireEvent.keyDown(field, { key: 'Enter' });
  expect(commands('/area-completions')).toHaveLength(0);
});

test('a scanned gate badge travels in the one request, trimmed with its case kept, and the completion is recorded', async () => {
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const gate = await gateDialog('Scan badge to confirm completion');
  const readsBefore = contextReads();

  scanGateBadge(gate, '  v-100517 ');
  const toast = await notice();
  expect(toast).toHaveTextContent('PN-ON × 3 finished on Lathe 1');
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toEqual({
    part_number: 'PN-ON',
    quantity_flow_id: 103,
    machine_id: 1,
    quantity: 3,
    device_event_id: sent[0].body.device_event_id,
    confirming_badge: 'v-100517',
  });
  // The server signed the badge Worker in; the context is re-read.
  expect(recorded.get(sent[0].body.device_event_id)!.worker).toBe(TRAN);
  await waitFor(() => expect(contextReads()).toBeGreaterThan(readsBefore));
  await waitFor(() =>
    expect(document.querySelector('.ss-pill')).toHaveTextContent('V. Tran'),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('QUEUE and Undo use their own badge gates and send the badge to their own routes', async () => {
  await renderStation();
  const summary = await openMachineSummary('QUEUE');
  fireEvent.click(confirmButton(summary, 'Confirm return to queue'));
  const queueGate = await gateDialog('Scan badge to confirm the queue return');
  expect(queueGate.className).toContain('tone-warning');
  expect(queueGate.querySelector('.alertbadge')).toHaveTextContent('!');
  expect(queueGate).toHaveTextContent(
    'Are you sure you want to return 3 pcs of PN-ON running on Lathe 1 back to the Lathe queue?',
  );
  scanGateBadge(queueGate, '100482');
  await notice();
  const queue = commands('/machine-releases');
  expect(queue).toHaveLength(1);
  expect(queue[0].body).toEqual({
    part_number: 'PN-ON',
    quantity_flow_id: 103,
    machine_id: 1,
    quantity: 3,
    device_event_id: queue[0].body.device_event_id,
    confirming_badge: '100482',
  });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

  const undo = await openUndo();
  fireEvent.click(
    within(undo).getByRole('button', { name: 'Confirm reversal' }),
  );
  const undoGate = await gateDialog('Scan badge to confirm the reversal');
  expect(undoGate.className).toContain('tone-warning');
  expect(undoGate).toHaveTextContent(
    'Are you sure you want to reverse RELEASED_FROM_MACHINE — 3 pcs of PN-ON?',
  );
  scanGateBadge(undoGate, 'v-100517');
  await waitFor(() => expect(commands('/undos')).toHaveLength(1));
  expect(commands('/undos')[0].body).toEqual({
    part_number: 'PN-ON',
    reverses_device_event_id: queue[0].body.device_event_id,
    device_event_id: commands('/undos')[0].body.device_event_id,
    confirming_badge: 'v-100517',
  });
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', {
        name: 'Reverse this Part Number action?',
      }),
    ).toBeNull(),
  );
  expect(flows.find((f) => f.id === 103)!.state).toBe('ON_MACHINE');
});

test('an unrecognized badge is refused in place; the next badge resends under the same device_event_id', async () => {
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const gate = await gateDialog('Scan badge to confirm completion');

  scanGateBadge(gate, 'B-77'); // an inactive Worker's badge
  expect(await within(gate).findByText(E_G3)).toBeInTheDocument();
  expect(
    screen.getByRole('dialog', { name: 'Scan badge to confirm completion' }),
  ).toBe(gate);
  const field = gateField(gate);
  expect(field).toHaveValue('');
  await waitFor(() => expect(document.activeElement).toBe(field));
  // No Retry-only state on the summary underneath.
  expect(within(summary).queryByRole('button', { name: /Retry/ })).toBeNull();
  expect(committed.size).toBe(0);

  scanGateBadge(gate, '100482');
  await notice();
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(2);
  expect(sent[1].body.device_event_id).toBe(sent[0].body.device_event_id);
  expect(sent.map((r) => r.body.confirming_badge)).toEqual(['B-77', '100482']);
  expect(committed.size).toBe(1);
});

test('Cancel and Escape in the badge gate return to the summary with nothing sent; Confirm reopens the gate', async () => {
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  let gate = await gateDialog('Scan badge to confirm completion');
  fireEvent.click(within(gate).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(
    screen.queryByRole('dialog', { name: 'Scan badge to confirm completion' }),
  ).toBeNull();
  expect(screen.getByRole('dialog', { name: 'Complete Area processing' })).toBe(
    summary,
  );

  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  gate = await gateDialog('Scan badge to confirm completion');
  fireEvent.keyDown(gate, { key: 'Escape' });
  expect(
    screen.queryByRole('dialog', { name: 'Scan badge to confirm completion' }),
  ).toBeNull();
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  expect(await gateDialog('Scan badge to confirm completion')).toBeTruthy();
  expect(commands('/area-completions')).toHaveLength(0);
});

/* ============ The QUESTION form ============ */

test('with the QUESTION form the existing question confirms and the body carries no badge', async () => {
  contextGates = { done: 'QUESTION', queue: 'QUESTION', undo: 'QUESTION' };
  serverGates = { ...contextGates };
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const question = await gateDialog('Confirm finished quantity?');
  expect(
    screen.queryByRole('dialog', { name: 'Scan badge to confirm completion' }),
  ).toBeNull();
  fireEvent.click(
    within(question).getByRole('button', { name: 'Yes — finished' }),
  );
  await notice();
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(1);
  expect(sent[0].body).not.toHaveProperty('confirming_badge');
  expect(recorded.get(sent[0].body.device_event_id)!.worker).toBe(NGUYEN);
});

/* ============ Typed gate refusals switch the form ============ */

test('a question refused with badge_confirmation_required switches straight to the badge gate with the reason inside it', async () => {
  contextGates.done = 'QUESTION';
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const question = await gateDialog('Confirm finished quantity?');
  const readsBefore = contextReads();
  // The background re-read never answers: the switch must not wait on it.
  contextHold = new Promise<void>(() => undefined);
  fireEvent.click(
    within(question).getByRole('button', { name: 'Yes — finished' }),
  );

  const gate = await gateDialog('Scan badge to confirm completion');
  const reason = within(gate).getByText(E_G1);
  expect(reason.closest('.ss-guide')!.className).toContain('warn');
  const field = gateField(gate);
  await waitFor(() => expect(document.activeElement).toBe(field));
  expect(contextReads()).toBe(readsBefore + 1);
  // Nothing recorded; the draft (quantity, selection) is intact.
  expect(committed.size).toBe(0);
  expect(summary).toHaveTextContent('3 pcs');

  // Cancel, then Confirm: the badge gate again (the server named it).
  fireEvent.click(within(gate).getByRole('button', { name: 'Cancel (Esc)' }));
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const again = await gateDialog('Scan badge to confirm completion');
  expect(again).not.toHaveTextContent(E_G1);
  scanGateBadge(again, '100482');
  await notice();
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(2);
  expect(sent[0].body).not.toHaveProperty('confirming_badge');
  expect(sent[1].body.confirming_badge).toBe('100482');
  expect(sent[1].body.device_event_id).toBe(sent[0].body.device_event_id);
  expect(sent[1].body.quantity).toBe(3);
});

test('a badge refused with badge_confirmation_not_expected closes the gate; the next Confirm asks the question', async () => {
  serverGates.done = 'QUESTION';
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const gate = await gateDialog('Scan badge to confirm completion');
  const readsBefore = contextReads();
  scanGateBadge(gate, '100482');

  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', {
        name: 'Scan badge to confirm completion',
      }),
    ).toBeNull(),
  );
  const reason = within(summary).getByText(E_G2);
  expect(reason.closest('.ss-guide')!.className).toContain('warn');
  await waitFor(() => expect(contextReads()).toBe(readsBefore + 1));
  expect(committed.size).toBe(0);

  // The re-read still answers BADGE (stale); the refusal's form wins.
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const question = await gateDialog('Confirm finished quantity?');
  fireEvent.click(
    within(question).getByRole('button', { name: 'Yes — finished' }),
  );
  await notice();
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(2);
  expect(sent[0].body.confirming_badge).toBe('100482');
  expect(sent[1].body).not.toHaveProperty('confirming_badge');
  expect(sent[1].body.device_event_id).toBe(sent[0].body.device_event_id);
});

/* ============ Retry rules ============ */

test('a transport failure in the badge gate closes it; Retry resends the identical body with its badge', async () => {
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const gate = await gateDialog('Scan badge to confirm completion');
  writeFailure = 'network';
  scanGateBadge(gate, '100482');

  await waitFor(() =>
    expect(summary).toHaveTextContent('may or may not have been recorded'),
  );
  expect(
    screen.queryByRole('dialog', { name: 'Scan badge to confirm completion' }),
  ).toBeNull();
  fireEvent.click(confirmButton(summary, 'Retry the same completion'));
  expect(
    screen.queryByRole('dialog', { name: 'Scan badge to confirm completion' }),
  ).toBeNull();
  await notice();
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(2);
  expect(JSON.stringify(sent[1].body)).toBe(JSON.stringify(sent[0].body));
  expect(sent[1].body.confirming_badge).toBe('100482');
});

test('a typed gate refusal of an unknown-outcome Retry ends the unknown outcome; the same intent is confirmed again', async () => {
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const gate = await gateDialog('Scan badge to confirm completion');
  writeFailure = 'network';
  scanGateBadge(gate, '100482');
  await waitFor(() =>
    expect(summary).toHaveTextContent('may or may not have been recorded'),
  );

  // An administrator turns the DONE badge option off meanwhile: the
  // frozen badge request is refused after the idempotency fast path.
  serverGates.done = 'QUESTION';
  fireEvent.click(confirmButton(summary, 'Retry the same completion'));

  expect(await within(summary).findByText(E_G2)).toBeInTheDocument();
  expect(summary).not.toHaveTextContent('may or may not have been recorded');
  expect(
    within(summary).queryByRole('button', { name: 'Leave — check the Area' }),
  ).toBeNull();
  expect(confirmButton(summary, 'Cancel (Esc)')).toBeInTheDocument();
  expect(committed.size).toBe(0);

  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const question = await gateDialog('Confirm finished quantity?');
  fireEvent.click(
    within(question).getByRole('button', { name: 'Yes — finished' }),
  );
  await notice();
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(3);
  expect(sent[1].body.confirming_badge).toBe('100482');
  expect(sent[2].body).not.toHaveProperty('confirming_badge');
  expect(sent[2].body.device_event_id).toBe(sent[0].body.device_event_id);
  expect(committed.size).toBe(1);
});

test('after a generic refusal of a badge request, Retry asks for a NEW badge under the same device_event_id', async () => {
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const gate = await gateDialog('Scan badge to confirm completion');
  writeFailure = { status: 409, body: { detail: E_S4 } };
  scanGateBadge(gate, '100482');

  expect(await within(summary).findByText(E_S4)).toBeInTheDocument();
  expect(
    screen.queryByRole('dialog', { name: 'Scan badge to confirm completion' }),
  ).toBeNull();
  const before = commands('/area-completions').length;
  fireEvent.click(confirmButton(summary, 'Retry completion'));
  const again = await gateDialog('Scan badge to confirm completion');
  expect(commands('/area-completions')).toHaveLength(before);
  // Cancel and Retry again: still the badge gate, never a resend.
  fireEvent.click(within(again).getByRole('button', { name: 'Cancel (Esc)' }));
  fireEvent.click(confirmButton(summary, 'Retry completion'));
  const third = await gateDialog('Scan badge to confirm completion');
  expect(commands('/area-completions')).toHaveLength(before);

  scanGateBadge(third, 'v-100517');
  await notice();
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(2);
  expect(sent[1].body.confirming_badge).toBe('v-100517');
  expect(sent[1].body.device_event_id).toBe(sent[0].body.device_event_id);
  expect(recorded.get(sent[1].body.device_event_id)!.worker).toBe(TRAN);
});

test('after a generic refusal of a question request, Retry resends without asking again', async () => {
  contextGates = { done: 'QUESTION', queue: 'QUESTION', undo: 'QUESTION' };
  serverGates = { ...contextGates };
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const question = await gateDialog('Confirm finished quantity?');
  writeFailure = { status: 409, body: { detail: 'The flow moved meanwhile.' } };
  fireEvent.click(
    within(question).getByRole('button', { name: 'Yes — finished' }),
  );
  expect(
    await within(summary).findByText('The flow moved meanwhile.'),
  ).toBeInTheDocument();
  fireEvent.click(confirmButton(summary, 'Retry completion'));
  expect(
    screen.queryByRole('dialog', { name: 'Confirm finished quantity?' }),
  ).toBeNull();
  await notice();
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(2);
  expect(JSON.stringify(sent[1].body)).toBe(JSON.stringify(sent[0].body));
});

/* ============ Offline, DEV demo badges, session expiry ============ */

test('while disconnected the gate field is disabled and nothing is sent', async () => {
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const gate = await gateDialog('Scan badge to confirm completion');
  healthDown = true;
  const field = gateField(gate);
  await waitFor(() => expect(field).toBeDisabled(), { timeout: 4000 });
  expect(field).toHaveAttribute(
    'placeholder',
    'Disconnected — scanning disabled',
  );
  fireEvent.keyDown(field, { key: 'Enter' });
  const demo = await within(gate).findByRole('note');
  fireEvent.click(within(demo).getByRole('button', { name: '100482' }));
  expect(commands('/area-completions')).toHaveLength(0);
});

test('the DEV demo badges in the gate are the active Workers; a click confirms like a wedge scan', async () => {
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const gate = await gateDialog('Scan badge to confirm completion');
  const note = await within(gate).findByRole('note');
  expect(note).toHaveTextContent(
    'Demo badges (development build only) — click one to simulate a badge scan:',
  );
  expect(
    within(note)
      .getAllByRole('button')
      .map((button) => button.textContent),
  ).toEqual(['100482', 'V-100517']);
  fireEvent.click(within(note).getByRole('button', { name: 'V-100517' }));
  await notice();
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(1);
  expect(sent[0].body.confirming_badge).toBe('V-100517');
});

test('a session expiring while the badge gate is open raises the sign-in modal above it; focus returns to the gate field', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  await renderStation();
  const summary = await openMachineSummary('DONE');
  fireEvent.click(confirmButton(summary, 'Confirm completion'));
  const gate = await gateDialog('Scan badge to confirm completion');
  const field = gateField(gate);
  await waitFor(() => expect(document.activeElement).toBe(field));

  await act(async () => {
    await vi.advanceTimersByTimeAsync(16 * MINUTE);
  });
  const modal = await screen.findByRole('dialog', {
    name: 'Worker session expired',
  });
  // The gate and its draft stay underneath.
  expect(
    screen.getByRole('dialog', { name: 'Scan badge to confirm completion' }),
  ).toBe(gate);
  const modalField = within(modal).getByLabelText('Scan Worker badge');
  fireEvent.change(modalField, { target: { value: '100482' } });
  fireEvent.keyDown(modalField, { key: 'Enter' });
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Worker session expired' }),
    ).toBeNull(),
  );
  await waitFor(() => expect(document.activeElement).toBe(field));

  scanGateBadge(gate, 'v-100517');
  await notice();
  expect(commands('/area-completions')).toHaveLength(1);
  expect(commands('/area-completions')[0].body.confirming_badge).toBe(
    'v-100517',
  );
});

/* ============ Other entries ============ */

test('an assignment never carries a badge, even where every gate is a badge', async () => {
  await renderStation();
  scan(`PF:MACHINE:${MACHINE.tag}`);
  const dlg = await screen.findByRole('dialog', { name: 'Assign to Machine' });
  fireEvent.click(
    within(within(dlg).getByRole('group', { name: /PN/ })).getByRole('button', {
      name: /PN-Q/,
    }),
  );
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', { name: 'Next' }),
  );
  fireEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', {
      name: 'Confirm assignment',
    }),
  );
  await notice();
  const sent = commands('/machine-assignments');
  expect(sent).toHaveLength(1);
  expect(sent[0].body).not.toHaveProperty('confirming_badge');
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('the direct-processing DONE of an Area without Machines goes through the badge gate', async () => {
  await renderStation(CUT);
  const row = within(document.querySelector('.abd-summary') as HTMLElement)
    .getByText('PN-CUT')
    .closest('li') as HTMLElement;
  fireEvent.click(
    within(row).getByRole('button', { name: 'Complete Area processing' }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  fireEvent.click(confirmButton(dlg, 'Confirm completion'));
  const gate = await gateDialog('Scan badge to confirm completion');
  expect(gate).toHaveTextContent(
    'Are you sure Cut has finished processing 6 pcs of PN-CUT?',
  );
  expect(within(gate).getByText('Cut', { selector: 'b' })).toBeTruthy();
  expect(within(gate).getByText('PN-CUT', { selector: 'b.mono' })).toBeTruthy();
  scanGateBadge(gate, '100482');
  await notice();
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(1);
  expect(sent[0].url).toMatch(/\/scan-stations\/CUT-ST-01\/area-completions$/);
  expect(sent[0].body).toEqual({
    part_number: 'PN-CUT',
    quantity_flow_id: 105,
    quantity: 6,
    device_event_id: sent[0].body.device_event_id,
    confirming_badge: '100482',
  });
});

test('the PN action dialog DONE entry goes through the same badge gate with its Machine', async () => {
  await renderStation();
  scan('PF:PN:PN-ON');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', {
      name: /Complete Area processing on Lathe 1/,
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  fireEvent.click(confirmButton(dlg, 'Confirm completion'));
  const gate = await gateDialog('Scan badge to confirm completion');
  scanGateBadge(gate, '100482');
  await notice();
  const sent = commands('/area-completions');
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toMatchObject({
    machine_id: 1,
    quantity_flow_id: 103,
    confirming_badge: '100482',
  });
});

test('Undo with the BADGE form after a question-form DONE records under the gate badge', async () => {
  contextGates.done = 'QUESTION';
  await renderStation();
  await completeWithQuestion();
  const undo = await openUndo();
  fireEvent.click(
    within(undo).getByRole('button', { name: 'Confirm reversal' }),
  );
  const gate = await gateDialog('Scan badge to confirm the reversal');
  expect(gate.querySelector('.alertbadge')).toHaveTextContent('!');
  scanGateBadge(gate, 'zz-unknown');
  expect(await within(gate).findByText(E_G3)).toBeInTheDocument();
  scanGateBadge(gate, 'v-100517');
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', {
        name: 'Reverse this Part Number action?',
      }),
    ).toBeNull(),
  );
  const sent = commands('/undos');
  expect(sent).toHaveLength(2);
  expect(sent[1].body.device_event_id).toBe(sent[0].body.device_event_id);
  expect(sent[1].body.confirming_badge).toBe('v-100517');
  expect(flows.find((f) => f.id === 103)!.state).toBe('ON_MACHINE');
});
