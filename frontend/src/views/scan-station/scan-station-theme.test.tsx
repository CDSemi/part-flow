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
import { STATION_PERMISSIONS } from '../../api/scan-station';

// The Scan Station theme tier (Phase 13 slice 10, GUI_DESIGN §2.1)
// against a fake in-memory `/api` that models the wire contract exactly:
// the station context reports `theme_preference` (`DARK` / `LIGHT` /
// null = no preference) and `PUT /api/scan-stations/{id}/theme-preference`
// saves an absolute value and echoes the request value. Each answer is
// computed when the request is SENT (a held answer reports the server
// state of that moment), so a held context read models a read served
// before a later save committed. Covers the saved theme applying on
// station routes, the save of the station's own toggle (connected and
// loaded only, one request in flight, the latest choice wins),
// session-only toggles offline / with the context in error / elsewhere,
// the failed-save warning, stale and overlapping reads, standard ↔
// production switching, Worker Session events (R62) and focus.

const STATION = 'S1';
const MINUTE = 60_000;
const E_S1 =
  'No Worker is signed in at this Scan Station. Scan your badge to continue. Nothing was recorded.';
const THEME_BUTTON = /^(🌙 Dark|☀️ Light)$/;

interface FakeWorker {
  id: number;
  name: string;
  badge: string;
}

const NGUYEN: FakeWorker = { id: 7, name: 'H. Nguyen', badge: '100482' };
const TRAN: FakeWorker = { id: 8, name: 'V. Tran', badge: '100517' };
const WORKERS = [NGUYEN, TRAN];

const AREAS = [
  { id: 2, name: 'Plating', color: '#33aa66' },
  { id: 3, name: 'Cut', color: '#3366ff' },
];
const PLATING_OP = { id: 20, code: 'PLATE', name: 'Plating' };

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
}

interface Recorded {
  pn: string;
  qty: number;
  flowId: number;
  reversed: boolean;
}

/** One planned answer of a theme PUT: held until released, and/or
 * answered with an error status (nothing saved). */
interface PutPlan {
  hold?: Promise<void>;
  status?: number;
}

let mode: 'DISABLED' | 'SCANNED';
let undoGate: 'BADGE' | 'QUESTION';
let themePreference: 'DARK' | 'LIGHT' | null;
let flows: Flow[];
let serverSession: { worker: FakeWorker; expiresAt: number } | null;
let recorded: Map<string, Recorded>;
let commandLog: string[];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextMovementId: number;
let healthDown: boolean;
/** The status every context read answers with while set. */
let contextFailure: number | null;
/** Held context reads, one per read, in send order. */
let contextHolds: Promise<void>[];
let putPlans: PutPlan[];
/** The status every transfer POST answers with while set (no write). */
let transferFailure: number | null;

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

function validSession() {
  return serverSession !== null && serverSession.expiresAt > Date.now()
    ? serverSession
    : null;
}

function workerRef(worker: FakeWorker) {
  return { id: worker.id, name: worker.name, avatar_updated_at: null };
}

function sessionWire() {
  const session = mode === 'SCANNED' ? validSession() : null;
  if (session === null) return null;
  return {
    worker: workerRef(session.worker),
    started_at: iso(Date.now()),
    expires_at: iso(session.expiresAt),
    server_now: iso(Date.now()),
  };
}

function signIn(worker: FakeWorker) {
  serverSession = { worker, expiresAt: Date.now() + 15 * MINUTE };
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

function operationRef() {
  return { ...PLATING_OP, is_external: false };
}

function flowWire(flow: Flow) {
  return {
    part_number: flow.pn,
    quantity_flow_id: flow.id,
    quantity: flow.qty,
    route_mode: 'FLOATING',
    operation: { ...operationRef(), is_active: true },
    processing_state: 'PROCESSING',
    machine_id: null,
    completed_machine: null,
    entered_at: '2026-10-01T06:30:00Z',
    available_actions: ['DONE', 'TRANSFER', 'SCRAP'],
    work_order: null,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function sessionRequired(): Response | null {
  if (mode !== 'SCANNED' || validSession() !== null) return null;
  return json({ detail: E_S1, worker_session_required: true }, 409);
}

function handle(
  url: string,
  method: string,
  body: unknown,
  plan: PutPlan | undefined,
): Response {
  if (url === '/api/policies/due-soon') {
    return json({
      due_soon_min_days: 2,
      due_soon_lead_time_percent: 15,
      due_soon_max_days: 7,
      updated_at: '2026-10-01T08:00:00Z',
    });
  }
  if (url === '/api/health') {
    return healthDown
      ? json({ status: 'unavailable' }, 503)
      : json({ status: 'ok' });
  }
  if (url === '/api/machines') return json([]);
  if (url === `/api/scan-stations/${STATION}/context`) {
    if (contextFailure !== null) {
      return json(
        {
          detail:
            contextFailure === 409
              ? `Scan Station '${STATION}' is inactive.`
              : 'The server is restarting.',
        },
        contextFailure,
      );
    }
    return json({
      station_id: STATION,
      department: { id: 1, name: 'Finishing' },
      area: areaRef(2),
      operations: [operationRef()],
      has_machines: false,
      worker_identification: {
        mode,
        fixed_worker: null,
        session: sessionWire(),
        final_gates: { done: 'QUESTION', queue: 'QUESTION', undo: undoGate },
      },
      theme_preference: themePreference,
      device: { id: 1, label: 'Station PC' },
      station_permissions: [...STATION_PERMISSIONS],
    });
  }
  if (
    url === `/api/scan-stations/${STATION}/theme-preference` &&
    method === 'PUT'
  ) {
    if (plan?.status !== undefined) {
      return json({ detail: 'The server is restarting.' }, plan.status);
    }
    const value = (body as { theme_preference: 'DARK' | 'LIGHT' })
      .theme_preference;
    themePreference = value;
    return json({ station_id: STATION, theme_preference: value });
  }
  const inventory = /^\/api\/areas\/(\d+)\/inventory$/.exec(url);
  if (inventory) {
    const areaId = Number(inventory[1]);
    const here = flows.filter((f) => f.areaId === areaId);
    const lines = here.map((flow) => ({
      part_number: flow.pn,
      total_quantity: flow.qty,
      flows: [flowWire(flow)],
    }));
    const total = here.reduce((s, f) => s + f.qty, 0);
    return json({
      area: areaRef(areaId),
      demand_context: [],
      scrapped: [],
      has_machines: false,
      lines,
      total_part_numbers: lines.length,
      total_quantity: total,
      queued: [],
      queued_quantity: 0,
      machines: [],
      on_machine_quantity: 0,
      processing: lines,
      processing_quantity: total,
      finished: [],
      finished_quantity: 0,
    });
  }
  if (url === `/api/scan-stations/${STATION}/scans/resolve`) {
    const pn = String((body as { barcode: string }).barcode).slice(
      'PF:PN:'.length,
    );
    const candidates = flows.filter((f) => f.pn === pn && f.areaId !== 2);
    return json({
      part_number: pn,
      station_id: STATION,
      area: areaRef(2),
      resolution: candidates.length
        ? 'TRANSFER_SOURCE_AVAILABLE'
        : 'NO_TRANSFERABLE_QUANTITY',
      in_area: [],
      candidates: candidates.map((flow) => ({
        ...flowWire(flow),
        current_area: areaRef(flow.areaId),
        route_status: 'FLOATING',
        expected_next_area: null,
        expected_operation_id: null,
        suggested_operation_id: PLATING_OP.id,
        repair_available: false,
      })),
      operations: [operationRef()],
      has_active_demand: true,
      intake_available: false,
      part_number_known: true,
      internal_work_orders: [],
      active_quantity: [],
      transfer_blocked_reason: null,
      requires_selection: false,
      combine_groups: [],
      scrapped_quantity: 0,
      stocked_quantity: 0,
      available_stocked_quantity: 0,
      stock_available: false,
      scanned_at: iso(Date.now()),
      worker_session: sessionWire(),
    });
  }
  if (url === `/api/scan-stations/${STATION}/badge-scans`) {
    const badge = String((body as { badge: string }).badge)
      .trim()
      .toUpperCase();
    const worker = WORKERS.find((w) => w.badge === badge);
    if (mode !== 'SCANNED' || !worker) {
      return json({
        outcome: worker ? 'NOT_USED_IN_AREA' : 'UNKNOWN',
        mode,
        worker_session: sessionWire(),
        previous_worker: null,
      });
    }
    const current = validSession();
    const previous =
      current && current.worker.id !== worker.id ? current.worker : null;
    const outcome =
      current === null ? 'SIGNED_IN' : previous ? 'SWITCHED' : 'REFRESHED';
    signIn(worker);
    return json({
      outcome,
      mode,
      worker_session: sessionWire(),
      previous_worker: previous ? workerRef(previous) : null,
    });
  }
  if (url === `/api/scan-stations/${STATION}/transfers` && method === 'POST') {
    const request = body as {
      quantity_flow_id: number;
      source_area_id: number;
      quantity: number;
      device_event_id: string;
    };
    const refused = sessionRequired();
    if (refused) return refused;
    if (transferFailure !== null) {
      return json({ detail: 'The server is restarting.' }, transferFailure);
    }
    const flow = flows.find((f) => f.id === request.quantity_flow_id)!;
    flow.areaId = 2;
    recorded.set(request.device_event_id, {
      pn: flow.pn,
      qty: request.quantity,
      flowId: flow.id,
      reversed: false,
    });
    commandLog.push(request.device_event_id);
    return json(
      {
        movement_id: nextMovementId++,
        movement_type: 'TRANSFERRED',
        quantity_flow_id: flow.id,
        part_number: flow.pn,
        quantity: request.quantity,
        from_area_id: request.source_area_id,
        to_area_id: 2,
        operation_id: PLATING_OP.id,
        station_id: STATION,
        assigned_route_step_id: null,
        movement_reason: null,
        reason: null,
        route_deviation: null,
        completed_movement_id: null,
        completed_machine_id: null,
        device_event_id: request.device_event_id,
        occurred_at: '2026-10-05T12:00:00Z',
      },
      201,
    );
  }
  const preview = /\/undo-preview\/([^/]+)$/.exec(url);
  if (preview && method === 'GET') {
    const eventId = decodeURIComponent(preview[1]);
    const record = recorded.get(eventId);
    if (!record) return json({ detail: 'No production event.' }, 404);
    const eligible =
      !record.reversed && commandLog[commandLog.length - 1] === eventId;
    const reversedBy = mode === 'SCANNED' ? validSession()?.worker : null;
    return json({
      reverses_device_event_id: eventId,
      station_id: STATION,
      kind: 'TRANSFER',
      part_number: record.pn,
      quantity: record.qty,
      occurred_at: '2026-10-05T12:00:00Z',
      eligible,
      ineligible_reason: eligible ? null : 'This action was reversed.',
      movements: [
        {
          movement_id: 900,
          movement_type: 'TRANSFERRED',
          movement_reason: null,
          quantity: record.qty,
          from_area: areaRef(3),
          to_area: areaRef(2),
          machine_id: null,
          operation_id: PLATING_OP.id,
        },
      ],
      restored: [
        {
          quantity_flow_id: record.flowId,
          quantity: record.qty,
          status: 'ACTIVE',
          area: areaRef(3),
          machine_id: null,
          processing_state: 'PROCESSING',
        },
      ],
      worker: null,
      reversed_by: reversedBy ? workerRef(reversedBy) : null,
      reason_required: false,
    });
  }
  if (url === `/api/scan-stations/${STATION}/undos` && method === 'POST') {
    const request = body as {
      reverses_device_event_id: string;
      device_event_id: string;
      confirming_badge?: string;
    };
    if (undoGate === 'BADGE') {
      const badge = request.confirming_badge?.trim().toUpperCase();
      const worker = WORKERS.find((w) => w.badge === badge);
      if (!worker) {
        return json(
          {
            detail: 'Scan your badge to confirm.',
            badge_confirmation_required: true,
          },
          409,
        );
      }
      signIn(worker);
    } else {
      const refused = sessionRequired();
      if (refused) return refused;
    }
    const record = recorded.get(request.reverses_device_event_id)!;
    record.reversed = true;
    flows.find((f) => f.id === record.flowId)!.areaId = 3;
    return json(
      {
        reverses_device_event_id: request.reverses_device_event_id,
        reversed_kind: 'TRANSFER',
        part_number: record.pn,
        station_id: STATION,
        movements: [
          {
            movement_id: nextMovementId++,
            reverses_movement_id: 900,
            original_movement_type: 'TRANSFERRED',
          },
        ],
        flows: [
          {
            quantity_flow_id: record.flowId,
            quantity: record.qty,
            status: 'ACTIVE',
            current_area_id: 3,
            current_machine_id: null,
          },
        ],
        device_event_id: request.device_event_id,
        occurred_at: '2026-10-05T12:05:00Z',
      },
      201,
    );
  }
  return json({ detail: `Unhandled ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  document.body.className = '';
  mode = 'DISABLED';
  undoGate = 'QUESTION';
  themePreference = null;
  flows = [
    { id: 100, pn: 'PN-W', qty: 6, areaId: 3 },
    { id: 101, pn: 'PN-V', qty: 2, areaId: 3 },
    { id: 102, pn: 'PN-X', qty: 3, areaId: 3 },
    { id: 200, pn: 'PN-H', qty: 4, areaId: 2 },
  ];
  serverSession = null;
  recorded = new Map();
  commandLog = [];
  requests = [];
  nextMovementId = 500;
  healthDown = false;
  contextFailure = null;
  contextHolds = [];
  putPlans = [];
  transferFailure = null;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (!url.endsWith('/api/health') && url !== '/api/policies/due-soon')
        requests.push({ url, method, body });
      const plan = url.endsWith('/theme-preference')
        ? putPlans.shift()
        : undefined;
      const hold = url.endsWith('/context') ? contextHolds.shift() : plan?.hold;
      // The answer reflects the server state when the request was SENT.
      const response = handle(url, method, body, plan);
      return (hold ?? Promise.resolve()).then(() => response);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/* ============ Helpers ============ */

async function renderStation(path = `/scan-station/${STATION}`) {
  window.history.replaceState({}, '', path);
  render(<App />);
  const input = await screen.findByLabelText('Scan barcode');
  await screen.findByText('Total PNs');
  return input as HTMLInputElement;
}

/** In-app navigation (the router follows history). */
function go(path: string) {
  act(() => {
    window.history.pushState({}, '', path);
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
}

function shown(): string {
  return document.body.className;
}

function themeButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: THEME_BUTTON });
}

function toggle() {
  fireEvent.click(themeButton());
}

function puts() {
  return requests.filter(
    (r) => r.method === 'PUT' && r.url.endsWith('/theme-preference'),
  );
}

function putBodies() {
  return puts().map((r) => r.body.theme_preference as string);
}

function contextReads() {
  return requests.filter((r) => r.url.endsWith('/context')).length;
}

/** Let pending answers and effects settle. */
async function settle(ms = 50) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

function holdUntilReleased(): { hold: Promise<void>; release: () => void } {
  let release!: () => void;
  const hold = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { hold, release };
}

async function goOffline() {
  healthDown = true;
  window.dispatchEvent(new Event('offline'));
  await screen.findByText(/OFFLINE — Connection to the PartFlow server/);
}

async function goOnline() {
  healthDown = false;
  window.dispatchEvent(new Event('online'));
  await waitFor(() =>
    expect(
      screen.queryByText(/OFFLINE — Connection to the PartFlow server/),
    ).toBeNull(),
  );
  await settle();
}

function scan(value: string) {
  const input = screen.getByLabelText('Scan barcode');
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

async function toast() {
  return waitFor(() => {
    const found = document.querySelector('.ss-toast');
    if (!found) throw new Error('no notice');
    return found as HTMLElement;
  });
}

function themeNotice(): HTMLElement | null {
  const found = document.querySelector('.ss-toast');
  return found?.textContent?.includes('Theme not confirmed')
    ? (found as HTMLElement)
    : null;
}

async function openTransferSummary(pn: string) {
  scan(`PF:PN:${pn}`);
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  await within(box).findByText('Review the transfer, then confirm.');
  return box;
}

/** A confirmed transfer — the station then re-reads its context. */
async function completeTransfer(pn: string) {
  await confirmTransfer(await openTransferSummary(pn));
}

async function confirmTransfer(box: HTMLElement) {
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Receive from another Area' }),
    ).toBeNull(),
  );
}

function signInModal() {
  return screen.queryByRole('dialog', {
    name: /^Worker (sign-in required|session expired)$/,
  });
}

function scanBadgeInModal(value: string) {
  const field = within(signInModal()!).getByLabelText('Scan Worker badge');
  fireEvent.change(field, { target: { value } });
  fireEvent.keyDown(field, { key: 'Enter' });
}

/* ============ Applying the saved theme ============ */

test('FE-1: a saved LIGHT preference applies on the production route without a save', async () => {
  themePreference = 'LIGHT';
  await renderStation(`/scan-station/${STATION}/production`);

  await waitFor(() => expect(shown()).toBe('light'));
  expect(themeButton()).toHaveAccessibleName('☀️ Light');
  expect(puts()).toHaveLength(0);
});

test.each([['DARK' as const], [null]])(
  'FE-1: a saved %s preference shows Dark',
  async (saved) => {
    themePreference = saved;
    await renderStation(`/scan-station/${STATION}/production`);
    await settle();

    expect(shown()).toBe('dark');
    expect(themeButton()).toHaveAccessibleName('🌙 Dark');
    expect(puts()).toHaveLength(0);
  },
);

test.each([
  [null, 'dark'],
  ['LIGHT' as const, 'light'],
])(
  'FE-2: entering a station with preference %s after a session choice shows %s',
  async (saved, expected) => {
    themePreference = saved;
    window.history.replaceState({}, '', '/management/area-board');
    render(<App />);
    toggle();
    expect(shown()).toBe('light');
    toggle();
    toggle();
    expect(shown()).toBe('light');
    // Away from a station route the toggle never saves.
    expect(puts()).toHaveLength(0);

    go(`/scan-station/${STATION}`);
    await screen.findByText('Total PNs');
    await waitFor(() => expect(shown()).toBe(expected));
    expect(puts()).toHaveLength(0);
  },
);

/* ============ Saving the station's own choice ============ */

test.each([
  ['standard', `/scan-station/${STATION}`],
  ['production', `/scan-station/${STATION}/production`],
])(
  'FE-3: the %s-mode toggle switches at once and saves the station preference once',
  async (_mode, path) => {
    await renderStation(path);
    const { hold, release } = holdUntilReleased();
    putPlans.push({ hold });

    toggle();
    expect(shown()).toBe('light');
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].url).toBe(
      `/api/scan-stations/${STATION}/theme-preference`,
    );
    expect(puts()[0].body).toEqual({ theme_preference: 'LIGHT' });

    release();
    await settle();
    expect(shown()).toBe('light');
    expect(puts()).toHaveLength(1);
    expect(themePreference).toBe('LIGHT');
  },
);

test('FE-4: quick toggles keep one request in flight; the latest choice is sent next', async () => {
  await renderStation();
  const { hold, release } = holdUntilReleased();
  putPlans.push({ hold });

  toggle();
  toggle();
  expect(shown()).toBe('dark');
  await settle();
  expect(putBodies()).toEqual(['LIGHT']);

  release();
  await waitFor(() => expect(putBodies()).toEqual(['LIGHT', 'DARK']));
  await settle();
  expect(shown()).toBe('dark');
  expect(puts()).toHaveLength(2);
  expect(themePreference).toBe('DARK');
});

test('FE-4b: a context read that overlaps the in-flight save never reverts the screen', async () => {
  await renderStation();
  const { hold, release } = holdUntilReleased();
  putPlans.push({ hold });

  toggle();
  toggle();
  expect(shown()).toBe('dark');
  // The PUT LIGHT committed but its answer is held; a confirmed command
  // re-reads the context, which answers LIGHT.
  const reads = contextReads();
  await completeTransfer('PN-W');
  await waitFor(() => expect(contextReads()).toBeGreaterThan(reads));
  await settle();
  expect(shown()).toBe('dark');
  expect(putBodies()).toEqual(['LIGHT']);

  release();
  await waitFor(() => expect(putBodies()).toEqual(['LIGHT', 'DARK']));
  await settle();
  expect(shown()).toBe('dark');

  // A later read answering DARK applies nothing and saves nothing.
  const before = contextReads();
  await completeTransfer('PN-V');
  await waitFor(() => expect(contextReads()).toBeGreaterThan(before));
  await settle();
  expect(shown()).toBe('dark');
  expect(puts()).toHaveLength(2);
});

/* ============ Session-only toggles ============ */

test('FE-5: offline the toggle is session-only; nothing is sent or queued for reconnection', async () => {
  await renderStation();
  await goOffline();

  toggle();
  expect(shown()).toBe('light');
  await settle();
  expect(puts()).toHaveLength(0);
  expect(themeNotice()).toBeNull();

  await goOnline();
  expect(puts()).toHaveLength(0);
  expect(shown()).toBe('light');
});

test('FE-6: a failed save shows the warning naming the theme on screen; no retry; switching away and back saves again', async () => {
  await renderStation();
  putPlans.push({ status: 500 });

  toggle();
  const notice = await toast();
  expect(notice.className).toContain('warn');
  expect(notice.querySelector('.fic')).toHaveTextContent('⚠');
  expect(notice.querySelector('.t1')).toHaveTextContent(
    'Theme not confirmed for this Scan Station',
  );
  expect(notice.querySelector('.t2')?.textContent).toBe(
    'Light mode applies to this browser session only — S1 did not confirm saving it. To save Light for this station, switch to Dark and back.',
  );
  expect(shown()).toBe('light');
  await settle();
  expect(puts()).toHaveLength(1);

  toggle();
  toggle();
  await waitFor(() => expect(putBodies()).toEqual(['LIGHT', 'DARK', 'LIGHT']));
  await settle();
  expect(shown()).toBe('light');
  expect(themePreference).toBe('LIGHT');
});

test('FE-6b: a newer choice is sent once after a failure; the notice names the theme on screen', async () => {
  await renderStation();
  const first = holdUntilReleased();
  const second = holdUntilReleased();
  putPlans.push({ hold: first.hold, status: 500 });
  putPlans.push({ hold: second.hold, status: 500 });

  toggle();
  toggle();
  first.release();
  await waitFor(() => expect(putBodies()).toEqual(['LIGHT', 'DARK']));
  await settle();
  expect(themeNotice()).toBeNull();

  second.release();
  const notice = await toast();
  expect(notice.querySelector('.t2')?.textContent).toBe(
    'Dark mode applies to this browser session only — S1 did not confirm saving it. To save Dark for this station, switch to Light and back.',
  );
  await settle();
  expect(puts()).toHaveLength(2);
});

test('FE-6b: a connection lost before the failure sends no newer choice and shows no notice', async () => {
  await renderStation();
  const first = holdUntilReleased();
  putPlans.push({ hold: first.hold, status: 500 });

  toggle();
  toggle();
  await goOffline();
  first.release();
  await settle();
  expect(putBodies()).toEqual(['LIGHT']);
  expect(themeNotice()).toBeNull();
  expect(shown()).toBe('dark');

  await goOnline();
  expect(puts()).toHaveLength(1);
});

test('FE-6c: a failed save never replaces an unresolved production warning', async () => {
  await renderStation();
  const { hold, release } = holdUntilReleased();
  putPlans.push({ hold, status: 500 });
  toggle();
  await waitFor(() => expect(putBodies()).toEqual(['LIGHT']));

  // A transfer whose answer is lost leaves the outcome-unknown warning.
  transferFailure = 503;
  const box = await openTransferSummary('PN-W');
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() =>
    expect(box).toHaveTextContent('may or may not have been recorded'),
  );
  fireEvent.click(
    within(box).getByRole('button', { name: 'Leave — check the Area' }),
  );
  expect(await toast()).toHaveTextContent('Transfer outcome unknown');

  release();
  await settle();
  expect(await toast()).toHaveTextContent('Transfer outcome unknown');
  expect(themeNotice()).toBeNull();
  expect(shown()).toBe('light');
  expect(puts()).toHaveLength(1);
});

/* ============ Reads after a command ============ */

test('FE-7: an unchanged reload keeps an offline session-only choice', async () => {
  await renderStation();
  await goOffline();
  toggle();
  expect(shown()).toBe('light');
  await goOnline();

  const reads = contextReads();
  await completeTransfer('PN-W');
  await waitFor(() => expect(contextReads()).toBeGreaterThan(reads));
  await settle();
  expect(shown()).toBe('light');
  expect(puts()).toHaveLength(0);
});

test("FE-7: another browser's saved change applies on the next fresh read", async () => {
  await renderStation();
  expect(shown()).toBe('dark');
  themePreference = 'LIGHT';

  await completeTransfer('PN-W');
  await waitFor(() => expect(shown()).toBe('light'));
  await settle();
  expect(puts()).toHaveLength(0);
});

test('FE-13: a read sent before the save started never reverts it; a later fresh read applies nothing', async () => {
  await renderStation();
  const box = await openTransferSummary('PN-W');
  const { hold, release } = holdUntilReleased();
  contextHolds.push(hold);

  // The transfer's context re-read is sent (answering no preference) and
  // held; the toggle then saves LIGHT, which settles first.
  const reads = contextReads();
  await confirmTransfer(box);
  await waitFor(() => expect(contextReads()).toBeGreaterThan(reads));
  toggle();
  await waitFor(() => expect(putBodies()).toEqual(['LIGHT']));
  await settle();

  // The held re-read is the only context read since the confirmation.
  expect(contextReads()).toBe(reads + 1);
  release();
  await settle();
  expect(shown()).toBe('light');
  expect(puts()).toHaveLength(1);

  const before = contextReads();
  await completeTransfer('PN-V');
  await waitFor(() => expect(contextReads()).toBeGreaterThan(before));
  await settle();
  expect(shown()).toBe('light');
  expect(puts()).toHaveLength(1);
});

test('FE-13: a read sent before the save started never reverts it, even when it carries a different value', async () => {
  await renderStation();
  // Another browser saved DARK; this station's next read reports it but
  // is held until after this station's own LIGHT save settled.
  const box = await openTransferSummary('PN-W');
  themePreference = 'DARK';
  const { hold, release } = holdUntilReleased();
  contextHolds.push(hold);
  const reads = contextReads();
  await confirmTransfer(box);
  await waitFor(() => expect(contextReads()).toBeGreaterThan(reads));
  toggle();
  await waitFor(() => expect(putBodies()).toEqual(['LIGHT']));
  await settle();

  // The held re-read is the only context read since the confirmation.
  expect(contextReads()).toBe(reads + 1);
  release();
  await settle();
  expect(shown()).toBe('light');
  expect(puts()).toHaveLength(1);
});

/* ============ Routes ============ */

test('FE-8: switching between standard and production routes keeps the theme without a save or re-apply', async () => {
  await renderStation(`/scan-station/${STATION}/production`);
  toggle();
  await waitFor(() => expect(putBodies()).toEqual(['LIGHT']));
  await settle();

  fireEvent.keyDown(window, { key: 'K', ctrlKey: true, shiftKey: true });
  await waitFor(() =>
    expect(window.location.pathname).toBe(`/scan-station/${STATION}`),
  );
  await settle();
  expect(shown()).toBe('light');
  expect(themeButton()).toHaveAccessibleName('☀️ Light');

  fireEvent.keyDown(window, { key: 'K', ctrlKey: true, shiftKey: true });
  await waitFor(() =>
    expect(window.location.pathname).toBe(
      `/scan-station/${STATION}/production`,
    ),
  );
  await settle();
  expect(shown()).toBe('light');
  expect(puts()).toHaveLength(1);
});

test('FE-9: a toggle elsewhere is session-only; returning to the station applies its saved theme again', async () => {
  themePreference = 'LIGHT';
  await renderStation();
  await waitFor(() => expect(shown()).toBe('light'));

  go('/management/area-board');
  toggle();
  expect(shown()).toBe('dark');
  await settle();
  expect(puts()).toHaveLength(0);

  go(`/scan-station/${STATION}`);
  await screen.findByText('Total PNs');
  await waitFor(() => expect(shown()).toBe('light'));
  expect(puts()).toHaveLength(0);
});

test('FE-9b: returning while the station save is in flight keeps the choice being saved; no later flip', async () => {
  await renderStation();
  const { hold, release } = holdUntilReleased();
  putPlans.push({ hold });
  toggle();
  await waitFor(() => expect(putBodies()).toEqual(['LIGHT']));
  // The PUT has not committed yet: the re-entry read answers no preference.
  themePreference = null;

  go('/management/area-board');
  await waitFor(
    () => expect(screen.queryByLabelText('Scan barcode')).toBeNull(),
    { timeout: 5000 },
  );
  const reentry = contextReads();
  go(`/scan-station/${STATION}`);
  await screen.findByText('Total PNs');
  await settle();
  expect(contextReads()).toBe(reentry + 1);
  expect(shown()).toBe('light');

  themePreference = 'LIGHT';
  release();
  await settle();
  expect(shown()).toBe('light');

  // The next fresh read reports the committed LIGHT: nothing flips.
  const reads = contextReads();
  await completeTransfer('PN-W');
  await waitFor(() => expect(contextReads()).toBeGreaterThan(reads));
  await settle();
  expect(shown()).toBe('light');
  expect(puts()).toHaveLength(1);
});

test('FE-9b: when that save fails, the next fresh read applies the stored theme', async () => {
  await renderStation();
  const { hold, release } = holdUntilReleased();
  putPlans.push({ hold, status: 500 });
  toggle();
  await waitFor(() => expect(putBodies()).toEqual(['LIGHT']));

  go('/management/area-board');
  await waitFor(
    () => expect(screen.queryByLabelText('Scan barcode')).toBeNull(),
    { timeout: 5000 },
  );
  const reentry = contextReads();
  go(`/scan-station/${STATION}`);
  await screen.findByText('Total PNs');
  await settle();
  expect(contextReads()).toBe(reentry + 1);
  expect(shown()).toBe('light');

  release();
  await settle();
  expect(shown()).toBe('light');

  await completeTransfer('PN-W');
  await waitFor(() => expect(shown()).toBe('dark'));
  expect(puts()).toHaveLength(1);
});

/* ============ Worker Sessions never affect the theme (R62) ============ */

test('FE-10: badge sign-in, switch, session expiry and a badge-gated confirmation leave the theme alone', async () => {
  mode = 'SCANNED';
  undoGate = 'BADGE';
  themePreference = 'LIGHT';
  await renderStation();
  await waitFor(() => expect(shown()).toBe('light'));

  // Sign-in through the blocking modal.
  await waitFor(() => expect(signInModal()).not.toBeNull());
  scanBadgeInModal(NGUYEN.badge);
  await waitFor(() => expect(signInModal()).toBeNull());
  expect(shown()).toBe('light');

  // A switch to another Worker on the main input.
  scan(TRAN.badge);
  await waitFor(async () =>
    expect(await toast()).toHaveTextContent('Worker signed in: V. Tran'),
  );
  expect(shown()).toBe('light');

  // The server ended the session: the command is refused with
  // worker_session_required, the expiry modal takes a badge, and the
  // unchanged request is confirmed again.
  const box = await openTransferSummary('PN-W');
  serverSession = null;
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() =>
    expect(signInModal()).toHaveAccessibleName('Worker session expired'),
  );
  expect(shown()).toBe('light');
  scanBadgeInModal(NGUYEN.badge);
  await waitFor(() => expect(signInModal()).toBeNull());
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Receive from another Area' }),
    ).toBeNull(),
  );
  expect(shown()).toBe('light');

  // An Undo confirmed through the badge gate.
  const undo = document.querySelector('button.ss-undo') as HTMLButtonElement;
  await waitFor(() => expect(undo).toBeEnabled());
  fireEvent.click(undo);
  const reversal = await screen.findByRole('dialog', {
    name: 'Reverse this Part Number action?',
  });
  const confirm = within(reversal).getByRole('button', {
    name: 'Confirm reversal',
  });
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.click(confirm);
  const gate = await screen.findByRole('dialog', {
    name: 'Scan badge to confirm the reversal',
  });
  const field = within(gate).getByLabelText('Scan Worker badge');
  fireEvent.change(field, { target: { value: TRAN.badge } });
  fireEvent.keyDown(field, { key: 'Enter' });
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', {
        name: 'Reverse this Part Number action?',
      }),
    ).toBeNull(),
  );
  expect(
    requests.filter((r) => r.url.endsWith('/undos'))[0].body.confirming_badge,
  ).toBe(TRAN.badge);
  await settle();
  expect(shown()).toBe('light');
  expect(puts()).toHaveLength(0);
});

/* ============ The context in error ============ */

test('FE-11: with the context in error on first load the toggle is session-only', async () => {
  contextFailure = 409;
  window.history.replaceState({}, '', `/scan-station/${STATION}`);
  render(<App />);
  await screen.findByText(`Scan Station “${STATION}” is unavailable`);

  toggle();
  expect(shown()).toBe('light');
  await settle();
  expect(puts()).toHaveLength(0);
});

test.each([[500], [409]])(
  'FE-11b: after a failed reload (%s) the toggle is session-only; a Retry with the unchanged value keeps it',
  async (status) => {
    await renderStation();
    contextFailure = status;
    await completeTransfer('PN-W');
    await screen.findByText(`Scan Station “${STATION}” is unavailable`);

    toggle();
    expect(shown()).toBe('light');
    await settle();
    expect(puts()).toHaveLength(0);

    contextFailure = null;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('Total PNs');
    await settle();
    expect(shown()).toBe('light');
    expect(puts()).toHaveLength(0);
  },
);

test('FE-11c: with the context in error and the connection lost, nothing is sent or queued', async () => {
  await renderStation();
  contextFailure = 500;
  await completeTransfer('PN-W');
  await screen.findByText(`Scan Station “${STATION}” is unavailable`);
  await goOffline();

  toggle();
  expect(shown()).toBe('light');
  await settle();
  expect(puts()).toHaveLength(0);

  await goOnline();
  expect(puts()).toHaveLength(0);
});

/* ============ Focus ============ */

test('FE-12: after a toggle a wedge scan still reaches the main input', async () => {
  const input = await renderStation();
  const button = themeButton();
  button.focus();
  toggle();
  expect(button).toHaveFocus();

  for (const key of 'PF:PN:PN-W') fireEvent.keyDown(button, { key });
  expect(input.value).toBe('PF:PN:PN-W');
  expect(input).toHaveFocus();
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(
    await screen.findByRole('dialog', { name: 'Receive from another Area' }),
  ).toBeInTheDocument();
});
