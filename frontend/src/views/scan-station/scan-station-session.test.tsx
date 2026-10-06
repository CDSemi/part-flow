import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';
import type { StationContext, TransferResult } from '../../api/scan-station';
import { AllocationDialog } from './scan-station-allocation-dialog';
import {
  StationSessionContext,
  useWorkerSessionClock,
} from './scan-station-session';

// Real Scan Station in a Scanned-session Area (Phase 13 — Worker
// Sessions, PROJECT_PROFILE §19, GUI_DESIGN §4.12) against a fake
// in-memory `/api` that models the server's session exactly as the
// wire contract states it: the context carries
// `worker_identification.session`, every successful PN / Machine
// resolve refreshes a valid session and answers `worker_session`, a
// badge scan signs in / switches / refreshes (or answers UNKNOWN), every
// command refreshes the valid session or is refused with 409
// `worker_session_required` and nothing recorded, and every session
// answer carries the server clock (`server_now`). Covers the blocking
// sign-in modal, the pill countdown with server-clock correction,
// expiry above an open dialog with its draft kept, the unchanged resend
// after a `worker_session_required` refusal (incl. the allocation
// dialog), main-input sign-in and switch, server-side refresh through
// every resolve, the DEV demo badges, the Undo `Reversed by` re-read and
// the send-order ticket of session answers.

const STATION = 'PLATE-ST-01';
const E_S1 =
  'No Worker is signed in at this Scan Station. Scan your badge to continue. Nothing was recorded.';
const MINUTE = 60_000;
const BADGE_CONFLICT =
  'Another badge was scanned at this Scan Station at the same moment. Scan your badge again. Nothing was recorded.';

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
  badge: '100517',
  active: true,
};
const MAI: FakeWorker = { id: 9, name: 'Mai', badge: 'B-77', active: false };
const WORKERS = [NGUYEN, TRAN, MAI];

const AREAS = [
  { id: 2, name: 'Plating', color: '#33aa66' },
  { id: 3, name: 'Cut', color: '#3366ff' },
];
const PLATING_OP = { id: 20, code: 'PLATE', name: 'Plating' };
const MACHINE = { id: 50, name: 'Plater 1', tag: 'P-001' };

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
}

interface ServerSession {
  worker: FakeWorker;
  startedAt: number;
  expiresAt: number;
}

interface Recorded {
  pn: string;
  qty: number;
  flowId: number;
  worker: FakeWorker | null;
  reversed: boolean;
}

let mode: 'DISABLED' | 'FIXED' | 'SCANNED';
let flows: Flow[];
let serverSession: ServerSession | null;
/** The station's session timeout (the effective Area/default value). */
let timeoutMs: number;
/** Server clock minus client clock. */
let serverOffsetMs: number;
/** Reads report no session although the server holds one (a client
 * whose view is behind the server — e.g. a session started elsewhere). */
let hideSessionInReads: boolean;
let recorded: Map<string, Recorded>;
let commandLog: string[];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextMovementId: number;
let healthDown: boolean;
let badgeFailure: boolean;
/** The sign-in loses the open-session race (the server's own 409). */
let badgeConflict: boolean;
let previewFailure: boolean;
/** While set, the matching reads stay pending until it resolves. */
let contextHold: Promise<void> | null;
let previewHold: Promise<void> | null;

function serverNow(): number {
  return Date.now() + serverOffsetMs;
}

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

function validSession(): ServerSession | null {
  return serverSession !== null && serverSession.expiresAt > serverNow()
    ? serverSession
    : null;
}

function workerRef(worker: FakeWorker) {
  return { id: worker.id, name: worker.name, avatar_updated_at: null };
}

function sessionWire() {
  const session = mode === 'SCANNED' ? validSession() : null;
  if (session === null || hideSessionInReads) return null;
  return {
    worker: workerRef(session.worker),
    started_at: iso(session.startedAt),
    expires_at: iso(session.expiresAt),
    server_now: iso(serverNow()),
  };
}

/** The server-side sliding refresh of a valid session. */
function refresh() {
  const session = mode === 'SCANNED' ? validSession() : null;
  if (session) session.expiresAt = serverNow() + timeoutMs;
}

function signIn(worker: FakeWorker) {
  const now = serverNow();
  serverSession = { worker, startedAt: now, expiresAt: now + timeoutMs };
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

function machineRef() {
  return {
    id: MACHINE.id,
    name: MACHINE.name,
    asset_tag: MACHINE.tag,
    barcode_value: `PF:MACHINE:${MACHINE.tag}`,
    operational_state: 'IDLE',
    state_changed_at: '2026-10-01T06:00:00Z',
    maintenance_since: null,
    maintenance_note: null,
    maintenance_expected_return: null,
  };
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

function handle(url: string, method: string, body: unknown): Response {
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
  if (url === `/api/scan-stations/${STATION}/context`) {
    return json({
      station_id: STATION,
      department: { id: 1, name: 'Finishing' },
      area: areaRef(2),
      operations: [operationRef()],
      has_machines: false,
      worker_identification: {
        mode,
        fixed_worker: mode === 'FIXED' ? workerRef(NGUYEN) : null,
        session: sessionWire(),
        final_gates: { done: 'QUESTION', queue: 'QUESTION', undo: 'QUESTION' },
      },
    });
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
    refresh();
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
      scanned_at: iso(serverNow()),
      worker_session: sessionWire(),
    });
  }
  if (url === `/api/scan-stations/${STATION}/machine-scans/resolve`) {
    refresh();
    return json({
      station_id: STATION,
      area: areaRef(2),
      machine: machineRef(),
      assigned_quantity: 0,
      queued: [],
      requires_selection: false,
      worker_session: sessionWire(),
    });
  }
  if (url === `/api/scan-stations/${STATION}/badge-scans`) {
    if (badgeFailure) {
      return json({ detail: 'The badge check is unavailable.' }, 503);
    }
    if (badgeConflict) {
      return json({ detail: BADGE_CONFLICT }, 409);
    }
    const badge = String((body as { badge: string }).badge)
      .trim()
      .toUpperCase();
    const worker = WORKERS.find((w) => w.active && w.badge === badge);
    if (mode !== 'SCANNED') {
      return json({
        outcome: worker ? 'NOT_USED_IN_AREA' : 'UNKNOWN',
        mode,
        worker_session: null,
        previous_worker: null,
      });
    }
    if (!worker) {
      return json({
        outcome: 'UNKNOWN',
        mode,
        worker_session: sessionWire(),
        previous_worker: null,
      });
    }
    const current = validSession();
    let outcome: 'SIGNED_IN' | 'SWITCHED' | 'REFRESHED' = 'SIGNED_IN';
    let previous: FakeWorker | null = null;
    if (current && current.worker.id === worker.id) {
      outcome = 'REFRESHED';
      current.expiresAt = serverNow() + timeoutMs;
    } else {
      if (current) {
        outcome = 'SWITCHED';
        previous = current.worker;
      }
      signIn(worker);
    }
    hideSessionInReads = false;
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
    refresh();
    const flow = flows.find((f) => f.id === request.quantity_flow_id)!;
    flow.areaId = 2;
    recorded.set(request.device_event_id, {
      pn: flow.pn,
      qty: request.quantity,
      flowId: flow.id,
      worker: validSession()?.worker ?? null,
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
    if (previewFailure) {
      return json({ detail: 'The server is restarting.' }, 503);
    }
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
      worker: record.worker ? workerRef(record.worker) : null,
      reversed_by: reversedBy ? workerRef(reversedBy) : null,
      reason_required: false,
    });
  }
  if (url === `/api/scan-stations/${STATION}/undos` && method === 'POST') {
    const request = body as {
      reverses_device_event_id: string;
      device_event_id: string;
    };
    const refused = sessionRequired();
    if (refused) return refused;
    refresh();
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
  mode = 'SCANNED';
  flows = [
    { id: 100, pn: 'PN-W', qty: 6, areaId: 3 },
    { id: 101, pn: 'PN-V', qty: 2, areaId: 3 },
    { id: 200, pn: 'PN-H', qty: 4, areaId: 2 },
  ];
  serverSession = null;
  timeoutMs = 15 * MINUTE;
  serverOffsetMs = 0;
  hideSessionInReads = false;
  recorded = new Map();
  commandLog = [];
  requests = [];
  nextMovementId = 500;
  healthDown = false;
  badgeFailure = false;
  badgeConflict = false;
  previewFailure = false;
  contextHold = null;
  previewHold = null;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (!url.endsWith('/api/health')) requests.push({ url, method, body });
      const hold =
        contextHold && url.endsWith('/context')
          ? contextHold
          : previewHold && url.includes('/undo-preview/')
            ? previewHold
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

/** Fake timers that still advance with real time (so RTL's waits
 * work); `advance` jumps the clock and runs what falls due. */
function startSteppedClock() {
  vi.useFakeTimers({ shouldAdvanceTime: true });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function renderStation() {
  window.history.replaceState({}, '', `/scan-station/${STATION}`);
  render(<App />);
  const input = await screen.findByLabelText('Scan barcode');
  await screen.findByText('Total PNs');
  return input as HTMLInputElement;
}

function signInModal() {
  return screen.queryByRole('dialog', {
    name: /^Worker (sign-in required|session expired)$/,
  });
}

function badgeField() {
  return within(signInModal()!).getByLabelText(
    'Scan Worker badge',
  ) as HTMLInputElement;
}

function scanBadgeInModal(value: string) {
  const field = badgeField();
  fireEvent.change(field, { target: { value } });
  fireEvent.keyDown(field, { key: 'Enter' });
}

function scan(value: string) {
  const input = screen.getByLabelText('Scan barcode');
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

async function notice() {
  return waitFor(() => {
    const toast = document.querySelector('.ss-toast');
    if (!toast) throw new Error('no notice');
    return toast as HTMLElement;
  });
}

function pill() {
  return document.querySelector('.ss-pill') as HTMLElement | null;
}

function pillSub() {
  return pill()?.querySelector('.sub') as HTMLElement | null;
}

/** Whole minutes left on the pill countdown (`Session: 9m 59s` → 9). */
function minutesLeft(): number {
  const match = /Session: (\d+)m/.exec(pillSub()?.textContent ?? '');
  if (!match) throw new Error(`no countdown: ${pillSub()?.textContent}`);
  return Number(match[1]);
}

function badgeRequests() {
  return requests.filter((r) => r.url.endsWith('/badge-scans'));
}

function writes(path: string) {
  return requests.filter((r) => r.method === 'POST' && r.url.endsWith(path));
}

function previewReads() {
  return requests.filter((r) => r.url.includes('/undo-preview/'));
}

function summaryValue(box: HTMLElement, term: string): string | null {
  const dt = within(box).queryByText(term, { selector: 'dt' });
  return dt ? (dt.nextElementSibling?.textContent ?? '') : null;
}

/** A hold that the test releases explicitly. */
function holdUntilReleased(): { hold: Promise<void>; release: () => void } {
  let release!: () => void;
  const hold = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { hold, release };
}

/** Scan a PN at Cut and continue to the transfer summary. */
async function openTransferSummary(pn = 'PN-W') {
  scan(`PF:PN:${pn}`);
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  await within(box).findByText('Review the transfer, then confirm.');
  return box;
}

async function completeTransfer(pn = 'PN-W') {
  const box = await openTransferSummary(pn);
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Receive from another Area' }),
    ).toBeNull(),
  );
}

async function openUndoDialog() {
  const undo = document.querySelector('button.ss-undo') as HTMLButtonElement;
  await waitFor(() => expect(undo).toBeEnabled());
  fireEvent.click(undo);
  return screen.findByRole('dialog', {
    name: 'Reverse this Part Number action?',
  });
}

/* ============ The blocking sign-in modal ============ */

test('a Scanned-session station without a session is blocked by the sign-in modal', async () => {
  const input = await renderStation();

  const modal = await waitFor(() => {
    const found = signInModal();
    if (!found) throw new Error('no modal');
    return found;
  });
  expect(modal).toHaveAccessibleName('Worker sign-in required');
  expect(modal).toHaveTextContent('Scan your badge to continue.');
  expect(badgeField()).toHaveAttribute(
    'placeholder',
    'Scan Worker badge · Press Enter',
  );
  await waitFor(() => expect(document.activeElement).toBe(badgeField()));
  // Escape and the backdrop never dismiss it.
  fireEvent.keyDown(modal, { key: 'Escape' });
  fireEvent.mouseDown(modal.parentElement!);
  expect(signInModal()).toBe(modal);
  // The scan card is inert: the input is disabled and wedge characters
  // typed outside the modal are not captured into it.
  expect(input).toBeDisabled();
  expect(
    screen.getByRole('button', { name: '⌨ Enter PN manually' }),
  ).toBeDisabled();
  fireEvent.keyDown(document.body, { key: 'P' });
  expect(input.value).toBe('');
  // The pill shows the unsigned state.
  expect(pill()).toHaveTextContent('No Worker');
  expect(pillSub()).toHaveTextContent('Session · scan badge');
  expect(requests.filter((r) => r.url.includes('/resolve'))).toHaveLength(0);
});

test('a badge in the modal signs in; the countdown corrects the server clock offset', async () => {
  startSteppedClock();
  // The server clock runs ten minutes ahead of the station's.
  serverOffsetMs = 10 * MINUTE;
  const input = await renderStation();
  await waitFor(() => expect(signInModal()).not.toBeNull());

  scanBadgeInModal(' 100482 ');
  await waitFor(() => expect(signInModal()).toBeNull());
  expect(badgeRequests()).toHaveLength(1);
  expect(badgeRequests()[0].body).toEqual({ badge: '100482' });
  const toast = await notice();
  expect(toast).toHaveTextContent('Worker signed in: H. Nguyen');
  expect(toast).toHaveTextContent(
    'New actions will be recorded under H. Nguyen.',
  );
  expect(toast.className).toContain('ok');
  expect(pill()).toHaveTextContent('H. Nguyen');
  expect(pill()!.querySelector('.worker-avatar.pill')).toHaveTextContent('HN');
  await advance(1_000);
  await waitFor(() => expect(pillSub()).toHaveTextContent('Session: 14m 59s'));
  expect(pillSub()!.className).not.toContain('warn');
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('a modal sign-in that switches the server session names the signed-out Worker', async () => {
  // The server still holds H. Nguyen's session, but the station's read
  // did not report it: the station shows the modal.
  signIn(NGUYEN);
  hideSessionInReads = true;
  await renderStation();
  await waitFor(() => expect(signInModal()).not.toBeNull());

  scanBadgeInModal('100517');
  await waitFor(() => expect(signInModal()).toBeNull());
  const toast = await notice();
  expect(toast).toHaveTextContent('Worker signed in: V. Tran');
  expect(toast).toHaveTextContent(
    'H. Nguyen was signed out. New actions will be recorded under V. Tran.',
  );
  expect(pill()).toHaveTextContent('V. Tran');
});

test('an unknown badge, a failed check and a disconnected station keep the modal with nothing recorded', async () => {
  await renderStation();
  await waitFor(() => expect(signInModal()).not.toBeNull());

  scanBadgeInModal('B-77'); // an inactive Worker's badge
  expect(
    await within(signInModal()!).findByText(
      'Badge not recognized. Check the badge and scan again — nothing was recorded.',
    ),
  ).toBeInTheDocument();
  expect(serverSession).toBeNull();

  badgeFailure = true;
  scanBadgeInModal('100482');
  expect(
    await within(signInModal()!).findByText(
      'Badge could not be checked — The badge check is unavailable. Nothing was recorded.',
    ),
  ).toBeInTheDocument();
  expect(signInModal()).toHaveAccessibleName('Worker sign-in required');
  expect(badgeRequests()).toHaveLength(2);

  // A server refusal that already says nothing was recorded is shown
  // once, without the suffix repeated.
  badgeFailure = false;
  badgeConflict = true;
  scanBadgeInModal('100482');
  expect(
    await within(signInModal()!).findByText(
      `Badge could not be checked — ${BADGE_CONFLICT}`,
    ),
  ).toBeInTheDocument();
  expect(badgeRequests()).toHaveLength(3);
  badgeConflict = false;
  cleanup();

  healthDown = true;
  badgeFailure = false;
  window.history.replaceState({}, '', `/scan-station/${STATION}`);
  render(<App />);
  await waitFor(() => expect(signInModal()).not.toBeNull());
  await waitFor(() => expect(badgeField()).toBeDisabled());
  expect(badgeField()).toHaveAttribute(
    'placeholder',
    'Disconnected — scanning disabled',
  );
  fireEvent.keyDown(badgeField(), { key: 'Enter' });
  expect(badgeRequests()).toHaveLength(3);
});

test('a badge answer under a mode other than Scanned re-reads the station and lifts the modal', async () => {
  await renderStation();
  await waitFor(() => expect(signInModal()).not.toBeNull());
  const contextReads = () =>
    requests.filter((r) => r.url === `/api/scan-stations/${STATION}/context`)
      .length;
  const readsBefore = contextReads();

  // The Area left Scanned session mode meanwhile; an inactive Worker's
  // badge is answered UNKNOWN under the new mode.
  mode = 'FIXED';
  scanBadgeInModal('B-77');
  await waitFor(() => expect(signInModal()).toBeNull());
  expect(contextReads()).toBeGreaterThan(readsBefore);
  expect(screen.queryByText(/Badge not recognized/)).toBeNull();
  expect(serverSession).toBeNull();
});

test('the countdown warns at two minutes and the expired modal appears at zero', async () => {
  startSteppedClock();
  timeoutMs = 2 * MINUTE + 30_000;
  signIn(NGUYEN);
  await renderStation();
  expect(signInModal()).toBeNull();
  await waitFor(() => expect(pillSub()).toHaveTextContent('Session: 2m 30s'));
  expect(pillSub()!.className).not.toContain('warn');

  await advance(31_000);
  await waitFor(() => expect(pillSub()!.className).toContain('warn'));
  expect(pillSub()).toHaveTextContent(/Session: 1m 5\ds/);

  await advance(2 * MINUTE);
  const modal = await waitFor(() => {
    const found = signInModal();
    if (!found) throw new Error('no modal');
    return found;
  });
  expect(modal).toHaveAccessibleName('Worker session expired');
  expect(modal).toHaveTextContent('Scan your badge to continue.');
  expect(pill()).toHaveTextContent('No Worker');
});

test('expiry above an open transfer keeps its draft; after the badge focus returns into it and Confirm records', async () => {
  startSteppedClock();
  timeoutMs = 2 * MINUTE;
  signIn(NGUYEN);
  await renderStation();

  scan('PF:PN:PN-W');
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  const quantity = within(box).getByLabelText(/^Quantity: /);
  fireEvent.change(quantity, { target: { value: '4' } });
  quantity.focus();
  expect(within(box).getByLabelText('Quantity: 4')).toBe(quantity);

  await advance(2 * MINUTE + 1_000);
  await waitFor(() =>
    expect(signInModal()).toHaveAccessibleName('Worker session expired'),
  );
  // The modal renders above (after) the open dialog; the draft is kept.
  const dialogs = screen.getAllByRole('dialog');
  expect(dialogs[dialogs.length - 1]).toBe(signInModal());
  expect(within(box).getByLabelText('Quantity: 4')).toBeInTheDocument();

  scanBadgeInModal('100517');
  await waitFor(() => expect(signInModal()).toBeNull());
  await waitFor(() => expect(box.contains(document.activeElement)).toBe(true));
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  await within(box).findByText('Review the transfer, then confirm.');
  expect(summaryValue(box, 'Worker')).toBe('V. Tran');
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() => expect(writes('/transfers')).toHaveLength(1));
  expect(writes('/transfers')[0].body.quantity).toBe(4);
  expect(
    recorded.get(writes('/transfers')[0].body.device_event_id)?.worker,
  ).toBe(TRAN);
});

/* ============ worker_session_required ============ */

test('a worker_session_required refusal raises the modal; after the badge Confirm resends the identical request', async () => {
  signIn(NGUYEN);
  await renderStation();
  const box = await openTransferSummary();
  // The server ended the session meanwhile (e.g. a station rebind).
  serverSession = null;

  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() =>
    expect(signInModal()).toHaveAccessibleName('Worker session expired'),
  );
  expect(writes('/transfers')).toHaveLength(1);
  // Not a rejection: no error, no Retry-only state.
  expect(box).not.toHaveTextContent(E_S1);
  expect(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  ).toBeInTheDocument();
  expect(within(box).queryByRole('button', { name: /Retry/ })).toBeNull();

  scanBadgeInModal('100517');
  await waitFor(() => expect(signInModal()).toBeNull());
  await waitFor(() => expect(summaryValue(box, 'Worker')).toBe('V. Tran'));
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() => expect(writes('/transfers')).toHaveLength(2));
  const [first, second] = writes('/transfers');
  expect(second.body).toEqual(first.body);
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Receive from another Area' }),
    ).toBeNull(),
  );
  expect(recorded.get(first.body.device_event_id)?.worker).toBe(TRAN);
});

test('the allocation dialog treats worker_session_required the same way: modal request, draft and key kept', async () => {
  const station: StationContext = {
    stationId: 'STOCK-ST-01',
    department: { id: 1, name: 'Shipping' },
    area: {
      id: 5,
      name: 'Stockroom',
      color: '#888888',
      description: null,
      isTerminal: true,
    },
    operations: [],
    hasMachines: false,
    workerIdentification: {
      mode: 'SCANNED',
      fixedWorker: null,
      session: null,
      finalGates: { done: 'QUESTION', queue: 'QUESTION', undo: 'QUESTION' },
    },
  };
  const stocked = {
    movementId: 1,
    movementType: 'STOCKED',
    quantityFlowId: 300,
    partNumber: 'PN-S',
    quantity: 5,
    fromAreaId: 3,
    toAreaId: 5,
    operationId: 1,
    stationId: 'STOCK-ST-01',
    assignedRouteStepId: null,
    routeDeviation: null,
    completedMovementId: null,
    completedMachineId: null,
    sourceQuantityFlowId: null,
    remainderQuantityFlowId: null,
    remainderQuantity: null,
    movementReason: null,
    reason: null,
    deviceEventId: 'stock-1',
    occurredAt: '2026-10-05T12:00:00Z',
    created: true,
  } satisfies TransferResult;
  let refuse = true;
  const allocationPosts: unknown[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith('/api/allocations/suggestion')) {
        return json({
          part_number: 'PN-S',
          quantity: 5,
          stocked_quantity: 5,
          active_allocated_quantity: 0,
          available_stocked_quantity: 5,
          proposed_total: 5,
          unallocated_quantity: 0,
          lines: [
            {
              work_order_id: 40,
              work_order_number: '007010',
              received_date: '2026-09-01',
              work_order_demand_id: 41,
              priority_rank: null,
              due_date: null,
              requested_quantity: 5,
              previously_allocated_quantity: 0,
              remaining_shortage: 5,
              proposed_quantity: 5,
            },
          ],
        });
      }
      if (url === '/api/allocations' && init?.method === 'POST') {
        allocationPosts.push(JSON.parse(String(init.body)));
        if (refuse) {
          return json({ detail: E_S1, worker_session_required: true }, 409);
        }
        return json(
          {
            part_number: 'PN-S',
            allocation_quantity: 5,
            rows: [
              {
                allocation_id: 1,
                work_order_demand_id: 41,
                work_order_id: 40,
                quantity: 5,
                is_manual_override: false,
              },
            ],
            completed_work_order_ids: [],
            device_event_id: (
              JSON.parse(String(init.body)) as { device_event_id: string }
            ).device_event_id,
          },
          201,
        );
      }
      return json({ detail: `Unhandled ${url}` }, 500);
    }),
  );
  const requireSession = vi.fn();
  const onDone = vi.fn();
  render(
    <StationSessionContext.Provider
      value={{
        requireSession,
        ticket: () => 0,
        applyWorkerSession: () => undefined,
      }}
    >
      <AllocationDialog
        station={station}
        stocked={stocked}
        sourceArea={{
          id: 3,
          name: 'Cut',
          color: null,
          description: null,
          isTerminal: false,
        }}
        writeBlocked={false}
        onDone={onDone}
        onLeave={() => undefined}
        onAbandonUnknown={() => undefined}
      />
    </StationSessionContext.Provider>,
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Allocate stocked quantity',
  });
  const confirm = await within(dialog).findByRole('button', {
    name: 'Confirm allocation',
  });
  await waitFor(() => expect(confirm).toBeEnabled());

  fireEvent.click(confirm);
  await waitFor(() => expect(requireSession).toHaveBeenCalledTimes(1));
  expect(dialog).not.toHaveTextContent(E_S1);
  expect(within(dialog).queryByRole('alert')).toBeNull();
  await waitFor(() => expect(confirm).toBeEnabled());

  refuse = false;
  fireEvent.click(confirm);
  await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  expect(allocationPosts).toHaveLength(2);
  expect(allocationPosts[1]).toEqual(allocationPosts[0]);
  // The suggestion was not re-read for this refusal.
  expect(
    vi
      .mocked(fetch)
      .mock.calls.filter(([url]) =>
        String(url).startsWith('/api/allocations/suggestion'),
      ),
  ).toHaveLength(1);
});

/* ============ Main-input badge scans ============ */

test('a main-input badge switches the Worker without touching the Last Action; the next summary names the new Worker', async () => {
  signIn(NGUYEN);
  const input = await renderStation();
  await completeTransfer();
  const lastPn = document.querySelector('.ss-lastpn') as HTMLElement;
  await waitFor(() =>
    expect(lastPn.querySelector('.p')).toHaveTextContent('PN-W'),
  );
  const lastBefore = lastPn.textContent;
  await waitFor(() => expect(document.activeElement).toBe(input));

  scan('100517');
  const toast = await waitFor(async () => {
    const found = await notice();
    if (!found.textContent?.includes('V. Tran')) throw new Error('old');
    return found;
  });
  expect(toast).toHaveTextContent('Worker signed in: V. Tran');
  expect(toast).toHaveTextContent(
    'H. Nguyen was signed out. New actions will be recorded under V. Tran.',
  );
  expect(pill()).toHaveTextContent('V. Tran');
  expect(lastPn.textContent).toBe(lastBefore);
  expect(screen.queryByRole('dialog')).toBeNull();

  const box = await openTransferSummary('PN-V');
  expect(summaryValue(box, 'Worker')).toBe('V. Tran');
});

/* ============ Server-side refresh through resolves ============ */

test('every resolve answer extends the countdown without a context re-read', async () => {
  startSteppedClock();
  signIn(NGUYEN);
  await renderStation();
  await waitFor(() => expect(pillSub()).toHaveTextContent('Session: 15m 0s'));
  // From here on every context re-read stays unanswered: only the
  // resolve answers themselves can move the countdown.
  contextHold = new Promise<void>(() => undefined);

  // A PN resolve near the old deadline.
  await advance(14 * MINUTE + 30_000);
  await waitFor(() => expect(minutesLeft()).toBe(0));
  scan('PF:PN:PN-NONE');
  const none = await screen.findByRole('dialog', {
    name: 'No quantity to receive',
  });
  await waitFor(() => expect(minutesLeft()).toBeGreaterThanOrEqual(14));
  await advance(60_000);
  expect(signInModal()).toBeNull();
  fireEvent.keyDown(none, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

  // A main-input Machine resolve.
  await advance(5 * MINUTE);
  await waitFor(() => expect(minutesLeft()).toBeLessThanOrEqual(9));
  scan(`PF:MACHINE:${MACHINE.tag}`);
  const assign = await screen.findByRole('dialog', {
    name: 'Assign to Machine',
  });
  await waitFor(() => expect(minutesLeft()).toBeGreaterThanOrEqual(14));

  // A Machine scanned inside the open assignment dialog.
  await advance(5 * MINUTE);
  await waitFor(() => expect(minutesLeft()).toBeLessThanOrEqual(10));
  const dialogScan = within(assign).getByLabelText(
    'Scan Machine or queued PN barcode',
  );
  fireEvent.change(dialogScan, {
    target: { value: `PF:MACHINE:${MACHINE.tag}` },
  });
  fireEvent.keyDown(dialogScan, { key: 'Enter' });
  await waitFor(() =>
    expect(
      requests.filter((r) => r.url.endsWith('/machine-scans/resolve')),
    ).toHaveLength(2),
  );
  await waitFor(() => expect(minutesLeft()).toBeGreaterThanOrEqual(14));
  expect(signInModal()).toBeNull();
});

/* ============ DEV demo badges ============ */

test('the modal lists the active Workers as DEV demo badges; a click is a badge scan', async () => {
  await renderStation();
  await waitFor(() => expect(signInModal()).not.toBeNull());
  const modal = signInModal()!;
  const note = await within(modal).findByRole('note');
  expect(note).toHaveTextContent(
    'Demo badges (development build only) — click one to simulate a badge scan:',
  );
  const badges = within(note)
    .getAllByRole('button')
    .map((button) => button.textContent);
  expect(badges).toEqual(['100482', '100517']);
  expect(note).toHaveTextContent('H. Nguyen');
  expect(note).not.toHaveTextContent('Mai');

  fireEvent.click(within(note).getByRole('button', { name: '100517' }));
  await waitFor(() => expect(signInModal()).toBeNull());
  expect(badgeRequests().map((r) => r.body)).toEqual([{ badge: '100517' }]);
  expect(pill()).toHaveTextContent('V. Tran');
});

/* ============ Other modes ============ */

test('Fixed Worker and Disabled stations never show the sign-in modal', async () => {
  mode = 'FIXED';
  await renderStation();
  expect(pill()).toHaveTextContent('Fixed Worker');
  expect(signInModal()).toBeNull();
  cleanup();

  mode = 'DISABLED';
  await renderStation();
  expect(pill()).toBeNull();
  expect(signInModal()).toBeNull();
  expect(screen.getByLabelText('Scan barcode')).toBeEnabled();
});

/* ============ Undo `Reversed by` follows the session ============ */

async function undoAfterExpiry(nextBadge: string) {
  startSteppedClock();
  timeoutMs = 2 * MINUTE;
  signIn(NGUYEN);
  await renderStation();
  await completeTransfer();
  const box = await openUndoDialog();
  expect(summaryValue(box, 'Reversed by')).toBe('H. Nguyen');
  expect(previewReads()).toHaveLength(1);

  await advance(2 * MINUTE + 1_000);
  await waitFor(() =>
    expect(signInModal()).toHaveAccessibleName('Worker session expired'),
  );
  return {
    box,
    signIn: async () => {
      scanBadgeInModal(nextBadge);
      await waitFor(() => expect(signInModal()).toBeNull());
    },
  };
}

test('Undo: a different Worker signing in re-reads the preview once; Confirm waits for it', async () => {
  const { box, signIn: signInNext } = await undoAfterExpiry('100517');
  const held = holdUntilReleased();
  previewHold = held.hold;

  await signInNext();
  await waitFor(() => expect(previewReads()).toHaveLength(2));
  const confirm = within(box).getByRole('button', { name: 'Confirm reversal' });
  expect(confirm).toBeDisabled();
  await act(async () => {
    held.release();
    await held.hold;
  });
  await waitFor(() => expect(summaryValue(box, 'Reversed by')).toBe('V. Tran'));
  await waitFor(() => expect(confirm).toBeEnabled());
  expect(summaryValue(box, 'Worker')).toBe('H. Nguyen');
  await advance(1_000);
  expect(previewReads()).toHaveLength(2);
});

test('Undo: the same Worker signing in again does not re-read the preview', async () => {
  const { box, signIn: signInNext } = await undoAfterExpiry('100482');

  await signInNext();
  await advance(1_000);
  expect(previewReads()).toHaveLength(1);
  expect(summaryValue(box, 'Reversed by')).toBe('H. Nguyen');
  expect(
    within(box).getByRole('button', { name: 'Confirm reversal' }),
  ).toBeEnabled();
});

test('Undo: a failed preview re-read omits Reversed by and keeps the other rows', async () => {
  const { box, signIn: signInNext } = await undoAfterExpiry('100517');
  previewFailure = true;

  await signInNext();
  await waitFor(() => expect(previewReads()).toHaveLength(2));
  await waitFor(() => expect(summaryValue(box, 'Reversed by')).toBeNull());
  expect(summaryValue(box, 'Original action')).toBe('TRANSFERRED');
  expect(summaryValue(box, 'Worker')).toBe('H. Nguyen');
  await waitFor(() =>
    expect(
      within(box).getByRole('button', { name: 'Confirm reversal' }),
    ).toBeEnabled(),
  );
});

test('Undo: a worker_session_required refusal re-reads the preview after sign-in and resends the identical reversal', async () => {
  signIn(NGUYEN);
  await renderStation();
  await completeTransfer();
  const box = await openUndoDialog();
  serverSession = null;

  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm reversal' }),
  );
  fireEvent.click(
    await screen.findByRole('button', { name: 'Yes — reverse it' }),
  );
  await waitFor(() =>
    expect(signInModal()).toHaveAccessibleName('Worker session expired'),
  );
  expect(writes('/undos')).toHaveLength(1);
  expect(box).not.toHaveTextContent(E_S1);

  scanBadgeInModal('100517');
  await waitFor(() => expect(signInModal()).toBeNull());
  await waitFor(() => expect(summaryValue(box, 'Reversed by')).toBe('V. Tran'));
  expect(previewReads()).toHaveLength(2);
  const confirm = within(box).getByRole('button', { name: 'Confirm reversal' });
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.click(confirm);
  fireEvent.click(
    await screen.findByRole('button', { name: 'Yes — reverse it' }),
  );
  await waitFor(() => expect(writes('/undos')).toHaveLength(2));
  const [first, second] = writes('/undos');
  expect(second.body).toEqual(first.body);
});

/* ============ Answer ordering ============ */

test('answers apply in send order: a held reload or resolve never restores an older session', async () => {
  signIn(NGUYEN);
  await renderStation();
  const box = await openTransferSummary();
  serverSession = null;
  // The context reload the refusal triggers stays pending.
  const reload = holdUntilReleased();
  contextHold = reload.hold;

  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm transfer' }),
  );
  await waitFor(() => expect(signInModal()).not.toBeNull());
  await waitFor(() =>
    expect(
      requests.filter((r) => r.url.endsWith('/context')).length,
    ).toBeGreaterThanOrEqual(2),
  );
  scanBadgeInModal('100517');
  await waitFor(() => expect(signInModal()).toBeNull());
  expect(pill()).toHaveTextContent('V. Tran');
  // The held reload was sent before the sign-in: it answers no session.
  hideSessionInReads = true;
  await act(async () => {
    reload.release();
    await reload.hold;
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(signInModal()).toBeNull();
  expect(pill()).toHaveTextContent('V. Tran');
  fireEvent.keyDown(box, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  contextHold = null;
  hideSessionInReads = false;
});

test('the session clock ignores an answer sent before one already applied', () => {
  const at = (worker: typeof NGUYEN, expiresInMs: number) => ({
    worker: { id: worker.id, name: worker.name, avatarUpdatedAt: null },
    startedAt: iso(Date.now()),
    expiresAt: iso(Date.now() + expiresInMs),
    serverNow: iso(Date.now()),
  });
  const { result } = renderHook(() => useWorkerSessionClock());

  // A PN resolve is sent (ticket 1), then a main-input switch (ticket
  // 2); the switch answers first, the resolve's answer arrives late.
  const resolveTicket = result.current.ticket();
  const switchTicket = result.current.ticket();
  act(() => result.current.apply(at(TRAN, 15 * MINUTE), switchTicket));
  act(() => result.current.apply(at(NGUYEN, 15 * MINUTE), resolveTicket));
  expect(result.current.live?.worker.name).toBe('V. Tran');
  expect(result.current.hadSession).toBe(true);

  // A refusal drops the session and outdates every answer sent before.
  const lateTicket = result.current.ticket();
  act(() => result.current.markRequired());
  expect(result.current.live).toBeNull();
  act(() => result.current.apply(at(TRAN, 15 * MINUTE), lateTicket));
  expect(result.current.live).toBeNull();
  const fresh = result.current.ticket();
  act(() => result.current.apply(at(NGUYEN, 15 * MINUTE), fresh));
  expect(result.current.live?.worker.name).toBe('H. Nguyen');
});
