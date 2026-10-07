import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../../App';

// Real Scan Station Undo with the Undo reason policy (Phase 13 —
// PROJECT_PROFILE §16 "require a reason when configured"; GUI_DESIGN
// §4.5, §4.6) against a fake in-memory `/api` that models the server's
// wire contract exactly: the undo preview reports `reason_required` (the
// policy at the time of the read); `/undos` accepts an optional
// `reason`, judges the policy AFTER the idempotency fast path — a
// reason-less Undo while the policy is on is refused 409
// `{detail, undo_reason_required: true}` with nothing recorded — and
// BEFORE the final-gate judgement (a reason refusal never follows a
// gate badge sign-in); the recorded reason is part of the request
// fingerprint (a different reason under a committed id is a conflict)
// and is answered back as `reason`. Helpers are copied from the
// final-gate suite.

const LATHE = 'LATHE-ST-01';
const MINUTE = 60_000;
const E_R1 =
  'A reason is required to reverse this action. Enter the reason and confirm again. Nothing was reversed.';
const E_G1 =
  'This action is now confirmed by a Worker badge scan. Scan your badge to confirm. Nothing was recorded.';
const E_G2 =
  'This action is no longer confirmed by a badge scan. Confirm it again. Nothing was recorded.';
const E_G3 =
  'Badge not recognized. Check the badge and scan again — nothing was recorded.';
const E_S1 =
  'No Worker is signed in at this Scan Station. Scan your badge to continue. Nothing was recorded.';
const ALREADY_REVERSED =
  'This action has already been reversed. Nothing was reversed.';
const UNKNOWN_OUTCOME = 'may or may not have been recorded';

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
const WORKERS = [NGUYEN, TRAN];

const AREA = { id: 2, name: 'Lathe', color: '#33aa66' };
const OPERATION = { id: 20, area_id: 2, code: 'TURN', name: 'Turning' };
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
/** The station Area's Worker identification mode. */
let mode: 'SCANNED' | 'DISABLED';
/** The final-gate forms the context READ reports. */
let contextGates: Gates;
/** The final-gate forms the server ENFORCES on a command. */
let serverGates: Gates;
/** `application_policy.undo_reason_required` on the server. */
let reasonPolicy: boolean;
let serverSession: ServerSession | null;
let committed: Map<
  string,
  { status: number; body: unknown; reason: string | null }
>;
let recorded: Map<string, Recorded>;
let commandLog: string[];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextMovementId: number;
let healthDown: boolean;
/** Failure injected into the NEXT Undo. */
let writeFailure: null | 'network' | { status: number; body: unknown };

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

function validSession(): ServerSession | null {
  return mode === 'SCANNED' &&
    serverSession !== null &&
    serverSession.expiresAt > Date.now()
    ? serverSession
    : null;
}

function startSession(worker: FakeWorker) {
  serverSession = {
    worker,
    startedAt: Date.now(),
    expiresAt: Date.now() + 15 * MINUTE,
  };
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

function areaRef() {
  return {
    id: AREA.id,
    name: AREA.name,
    color: AREA.color,
    description: null,
    is_terminal: false,
  };
}

function operationRef() {
  return { ...OPERATION, is_external: false };
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
    operation: { ...operationRef(), is_active: true },
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

function inventory() {
  const here = flows.filter((f) => f.areaId === AREA.id);
  const byState = (state: State) => here.filter((f) => f.state === state);
  const sum = (items: Flow[]) => items.reduce((s, f) => s + f.qty, 0);
  const onMachine = byState('ON_MACHINE');
  return json({
    area: areaRef(),
    demand_context: [],
    scrapped: [],
    has_machines: true,
    lines: lines(here),
    total_part_numbers: lines(here).length,
    total_quantity: sum(here),
    queued: lines(byState('QUEUED')),
    queued_quantity: sum(byState('QUEUED')),
    machines: [
      {
        machine: machineRef(),
        lines: lines(onMachine),
        total_quantity: sum(onMachine),
      },
    ],
    on_machine_quantity: sum(onMachine),
    processing: lines(byState('PROCESSING')),
    processing_quantity: sum(byState('PROCESSING')),
    finished: lines(byState('READY_TO_TRANSFER')),
    finished_quantity: sum(byState('READY_TO_TRANSFER')),
  });
}

/**
 * The server's final-gate judgement of one command: the Worker the
 * command records, or the refusal (nothing recorded). A Disabled Area
 * always has the question and records no Worker.
 */
function judgeGate(
  key: keyof Gates,
  badge: unknown,
): { refusal: Response } | { worker: FakeWorker | null } {
  const given = badge !== undefined && badge !== null;
  if (mode === 'SCANNED' && serverGates[key] === 'BADGE') {
    if (!given) {
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
    if (validSession()?.worker.id !== worker.id) startSession(worker);
    return { worker };
  }
  if (given) {
    return {
      refusal: json(
        { detail: E_G2, badge_confirmation_not_expected: true },
        409,
      ),
    };
  }
  if (mode === 'DISABLED') return { worker: null };
  const session = validSession();
  if (!session) {
    return {
      refusal: json({ detail: E_S1, worker_session_required: true }, 409),
    };
  }
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
  if (/\/context$/.test(url)) {
    return json({
      station_id: LATHE,
      department: { id: 1, name: 'Machining' },
      area: areaRef(),
      operations: [operationRef()],
      has_machines: true,
      worker_identification: {
        mode,
        fixed_worker: null,
        session: sessionWire(),
        final_gates: { ...contextGates },
      },
      theme_preference: null,
    });
  }
  if (/^\/api\/areas\/\d+\/inventory$/.test(url)) return inventory();

  if (/\/area-completions$/.test(url) && method === 'POST') {
    const request = body as {
      quantity_flow_id: number;
      machine_id?: number;
      device_event_id: string;
      confirming_badge?: string | null;
    };
    const judged = judgeGate('done', request.confirming_badge);
    if ('refusal' in judged) return judged.refusal;
    const flow = flows.find((f) => f.id === request.quantity_flow_id)!;
    const before = { state: flow.state, machineId: flow.machineId };
    flow.state = 'READY_TO_TRANSFER';
    flow.machineId = null;
    recorded.set(request.device_event_id, {
      type: 'AREA_COMPLETED',
      flowId: flow.id,
      pn: flow.pn,
      qty: flow.qty,
      areaId: AREA.id,
      machineId: request.machine_id ?? null,
      before,
      worker: judged.worker,
      reversed: false,
    });
    commandLog.push(request.device_event_id);
    return json(
      {
        movement_id: nextMovementId++,
        movement_type: 'AREA_COMPLETED',
        quantity_flow_id: flow.id,
        part_number: flow.pn,
        quantity: flow.qty,
        area_id: AREA.id,
        machine_id: request.machine_id ?? null,
        operation_id: OPERATION.id,
        station_id: LATHE,
        processing_state: flow.state,
        source_quantity_flow_id: null,
        remainder_quantity_flow_id: null,
        remainder_quantity: null,
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
          to_area: areaRef(),
          machine_id: record.machineId,
          operation_id: OPERATION.id,
        },
      ],
      restored: [
        {
          quantity_flow_id: record.flowId,
          quantity: record.qty,
          status: 'ACTIVE',
          area: areaRef(),
          machine_id: record.before.machineId,
          processing_state: record.before.state,
        },
      ],
      worker: record.worker ? workerRef(record.worker) : null,
      reversed_by: reversedBy ? workerRef(reversedBy) : null,
      reason_required: reasonPolicy,
    });
  }
  if (/\/undos$/.test(url) && method === 'POST') {
    const request = body as {
      reverses_device_event_id: string;
      device_event_id: string;
      confirming_badge?: string | null;
      reason?: string | null;
    };
    // Stripped; blank is absent.
    const reason = request.reason?.trim() || null;
    // Fast path: a committed Undo replays whatever the policy; another
    // reason under its id is the idempotency conflict.
    const replay = committed.get(request.device_event_id);
    if (replay) {
      return replay.reason === reason
        ? json(replay.body, replay.status)
        : json({ detail: 'Idempotency conflict.' }, 409);
    }
    const failed = injectedFailure();
    if (failed) return failed;
    // The Undo reason policy: after every state refusal, before the
    // identity resolver (whose badge path signs a Worker in).
    if (reasonPolicy && reason === null) {
      return json({ detail: E_R1, undo_reason_required: true }, 409);
    }
    const judged = judgeGate('undo', request.confirming_badge);
    if ('refusal' in judged) return judged.refusal;
    const record = recorded.get(request.reverses_device_event_id)!;
    record.reversed = true;
    const flow = flows.find((f) => f.id === record.flowId)!;
    flow.state = record.before.state;
    flow.machineId = record.before.machineId;
    const response = {
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
      reason,
    };
    committed.set(request.device_event_id, {
      status: 200,
      body: response,
      reason,
    });
    return json(response, 201);
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
  ];
  // Default: a Disabled Area — the final gate is always the question.
  mode = 'DISABLED';
  contextGates = { done: 'QUESTION', queue: 'QUESTION', undo: 'QUESTION' };
  serverGates = { done: 'QUESTION', queue: 'QUESTION', undo: 'QUESTION' };
  reasonPolicy = false;
  serverSession = null;
  committed = new Map();
  recorded = new Map();
  commandLog = [];
  requests = [];
  nextMovementId = 500;
  healthDown = false;
  writeFailure = null;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (!url.endsWith('/api/health') && url !== '/api/policies/due-soon')
        requests.push({ url, method, body });
      return Promise.resolve().then(() => handle(url, method, body));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** A Scanned-session Area with a valid session of H. Nguyen. */
function scannedArea(undoGate: Gate) {
  mode = 'SCANNED';
  contextGates.undo = undoGate;
  serverGates.undo = undoGate;
  startSession(NGUYEN);
}

async function renderStation() {
  window.history.replaceState({}, '', `/scan-station/${LATHE}`);
  render(<App />);
  await screen.findByLabelText('Scan barcode');
  await screen.findByText('Total PNs');
}

function machineCard(): HTMLElement {
  const heading = screen.getByText(MACHINE.name, { selector: '.mname' });
  return heading.closest('.abd-machine') as HTMLElement;
}

function commands(path: string) {
  return requests.filter((r) => r.method === 'POST' && r.url.endsWith(path));
}

function previewReads() {
  return requests.filter((r) => /\/undo-preview\//.test(r.url));
}

/** A Machine DONE through the question form (the Undo target). */
async function completeWithQuestion() {
  fireEvent.click(
    within(machineCard()).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  const dlg = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.click(within(dlg).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(dlg).getByRole('button', { name: 'Confirm completion' }),
  );
  const question = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
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

/** Render the station, record the DONE and open its Undo summary. */
async function undoSummary() {
  await renderStation();
  await completeWithQuestion();
  return openUndo();
}

function reasonField(box: HTMLElement) {
  return within(box).getByLabelText('Reason (required)') as HTMLInputElement;
}

function typeReason(box: HTMLElement, value: string) {
  fireEvent.change(reasonField(box), { target: { value } });
}

function confirmReversal(box: HTMLElement) {
  return within(box).getByRole('button', { name: 'Confirm reversal' });
}

function questionGate() {
  return screen.findByRole('dialog', { name: 'Reverse this action?' });
}

function badgeGate() {
  return screen.findByRole('dialog', {
    name: 'Scan badge to confirm the reversal',
  });
}

function scanGateBadge(gate: HTMLElement, value: string) {
  const field = within(gate).getByLabelText(
    'Scan Worker badge',
  ) as HTMLInputElement;
  fireEvent.change(field, { target: { value } });
  fireEvent.keyDown(field, { key: 'Enter' });
}

/** The restated facts of an open final gate. */
function gateFacts(gate: HTMLElement): string {
  const facts = within(gate).getByText(/Are you sure you want to reverse/);
  return (facts.textContent ?? '').replace(/\s+/g, ' ').trim();
}

async function answerYes() {
  const gate = await questionGate();
  fireEvent.click(
    within(gate).getByRole('button', { name: 'Yes — reverse it' }),
  );
}

async function reversalRecorded() {
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', {
        name: 'Reverse this Part Number action?',
      }),
    ).toBeNull(),
  );
  expect(flows[0].state).toBe('ON_MACHINE');
}

function undoTarget(): string {
  return commands('/area-completions')[0].body.device_event_id as string;
}

/* ============ Policy Off — the summary is unchanged ============ */

test('FR-1: with no reason required the summary has no Reason field, keeps its focus and sends the reason-less body', async () => {
  const box = await undoSummary();

  expect(within(box).queryByLabelText(/Reason/)).toBeNull();
  expect(box).not.toHaveTextContent('(required)');
  const confirm = confirmReversal(box);
  expect(confirm).toBeEnabled();
  // The pre-S6 focus target: the dialog root, never the danger primary.
  await waitFor(() => expect(document.activeElement).toBe(box));
  expect(document.activeElement).not.toBe(confirm);

  fireEvent.click(confirm);
  const gate = await questionGate();
  expect(gateFacts(gate)).not.toContain('Reason:');
  await answerYes();
  await reversalRecorded();
  const sent = commands('/undos');
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toEqual({
    part_number: 'PN-ON',
    reverses_device_event_id: undoTarget(),
    device_event_id: sent[0].body.device_event_id,
  });
});

/* ============ Policy On — the required field ============ */

test('FR-2: a required reason is asked in the summary, focused, required for Confirm, restated by the gate and sent trimmed', async () => {
  reasonPolicy = true;
  const box = await undoSummary();

  const field = reasonField(box);
  expect(box).toHaveTextContent('Reason (required)');
  expect(box).toHaveTextContent(
    'This reason will be included in the reversal history.',
  );
  expect(field).toHaveAttribute(
    'placeholder',
    'e.g. scanned the wrong Part Number',
  );
  expect(field).not.toHaveAttribute('readonly');
  await waitFor(() => expect(document.activeElement).toBe(field));
  const confirm = confirmReversal(box);
  expect(confirm).toBeDisabled();
  typeReason(box, '   ');
  expect(confirm).toBeDisabled();
  typeReason(box, '');

  // Enter on the dialog with the reason missing: nothing sent, no gate,
  // focus goes to the field.
  box.focus();
  fireEvent.keyDown(box, { key: 'Enter' });
  expect(
    screen.queryByRole('dialog', { name: 'Reverse this action?' }),
  ).toBeNull();
  expect(document.activeElement).toBe(field);

  // Enter IN the field does nothing (scrap / addition precedent).
  typeReason(box, '  wrong PN ');
  fireEvent.keyDown(field, { key: 'Enter' });
  expect(
    screen.queryByRole('dialog', { name: 'Reverse this action?' }),
  ).toBeNull();
  expect(commands('/undos')).toHaveLength(0);

  expect(confirm).toBeEnabled();
  fireEvent.click(confirm);
  const gate = await questionGate();
  expect(gateFacts(gate)).toMatch(/ Reason: wrong PN\.$/);
  expect(commands('/undos')).toHaveLength(0);
  await answerYes();
  await reversalRecorded();
  const sent = commands('/undos');
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toEqual({
    part_number: 'PN-ON',
    reverses_device_event_id: undoTarget(),
    device_event_id: sent[0].body.device_event_id,
    reason: 'wrong PN',
  });
  expect(committed.get(sent[0].body.device_event_id)!.reason).toBe('wrong PN');
});

/* ============ The typed refusal ============ */

test('FR-3: the typed refusal shows the field with the server reason and the same device_event_id is confirmed again', async () => {
  const box = await undoSummary();
  expect(within(box).queryByLabelText(/Reason/)).toBeNull();
  // The policy is switched On after the summary was read.
  reasonPolicy = true;

  fireEvent.click(confirmReversal(box));
  await answerYes();
  await waitFor(() => expect(box).toHaveTextContent(E_R1));
  expect(
    screen.queryByRole('dialog', { name: 'Reverse this action?' }),
  ).toBeNull();
  const field = reasonField(box);
  await waitFor(() => expect(document.activeElement).toBe(field));
  expect(field).not.toHaveAttribute('readonly');
  // The summary is intact; no write error, no unknown outcome.
  expect(box).toHaveTextContent('Original action');
  expect(box).toHaveTextContent('PN-ON');
  expect(box.querySelector('.ss-guide.error')).toBeNull();
  expect(box).not.toHaveTextContent(UNKNOWN_OUTCOME);
  const confirm = confirmReversal(box);
  expect(confirm).toBeDisabled();

  typeReason(box, 'wrong PN');
  fireEvent.click(confirm);
  const gate = await questionGate();
  expect(gateFacts(gate)).toMatch(/ Reason: wrong PN\.$/);
  await answerYes();
  await reversalRecorded();
  const sent = commands('/undos');
  expect(sent).toHaveLength(2);
  expect(sent[1].body).toEqual({
    part_number: 'PN-ON',
    reverses_device_event_id: undoTarget(),
    device_event_id: sent[0].body.device_event_id,
    reason: 'wrong PN',
  });
});

/* ============ Frozen intent ============ */

test('FR-4: after an unknown outcome the reason is read-only and the retry resends the identical body without the gate', async () => {
  reasonPolicy = true;
  const box = await undoSummary();
  typeReason(box, 'wrong PN');
  fireEvent.click(confirmReversal(box));
  writeFailure = 'network';
  await answerYes();

  await waitFor(() => expect(box).toHaveTextContent(UNKNOWN_OUTCOME));
  expect(reasonField(box)).toHaveAttribute('readonly');
  expect(reasonField(box)).toHaveAttribute('aria-readonly', 'true');
  fireEvent.click(
    within(box).getByRole('button', { name: 'Retry the same reversal' }),
  );
  await reversalRecorded();
  expect(
    screen.queryByRole('dialog', { name: 'Reverse this action?' }),
  ).toBeNull();
  const sent = commands('/undos');
  expect(sent).toHaveLength(2);
  expect(sent[1].body).toEqual(sent[0].body);
  expect(sent[1].body.reason).toBe('wrong PN');
});

/** Preview without a reason; the first POST is lost, the policy is
 * switched On, and the retry is refused for the missing reason. */
async function unknownOutcomeThenReasonRefusal() {
  const box = await undoSummary();
  fireEvent.click(confirmReversal(box));
  writeFailure = 'network';
  await answerYes();
  await waitFor(() => expect(box).toHaveTextContent(UNKNOWN_OUTCOME));
  expect(
    within(box).getByRole('button', { name: 'Leave — check the Area' }),
  ).toBeInTheDocument();
  reasonPolicy = true;
  fireEvent.click(
    within(box).getByRole('button', { name: 'Retry the same reversal' }),
  );
  await waitFor(() => expect(box).toHaveTextContent(E_R1));
  return box;
}

test('FR-4b: the reason refusal after an unknown outcome ends it; the reason is confirmed through the gate under the same key', async () => {
  const box = await unknownOutcomeThenReasonRefusal();

  expect(box).not.toHaveTextContent(UNKNOWN_OUTCOME);
  expect(
    within(box).queryByRole('button', { name: 'Retry the same reversal' }),
  ).toBeNull();
  expect(
    within(box).queryByRole('button', { name: 'Leave — check the Area' }),
  ).toBeNull();
  expect(
    within(box).getByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeInTheDocument();
  const field = reasonField(box);
  expect(field).not.toHaveAttribute('readonly');
  await waitFor(() => expect(document.activeElement).toBe(field));
  const confirm = confirmReversal(box);
  expect(confirm).toBeDisabled();

  typeReason(box, 'wrong PN');
  fireEvent.click(confirm);
  // The gate opens again — no direct resend.
  await questionGate();
  expect(commands('/undos')).toHaveLength(2);
  await answerYes();
  await reversalRecorded();
  const sent = commands('/undos');
  expect(sent).toHaveLength(3);
  expect(sent[2].body).toEqual({
    ...sent[0].body,
    reason: 'wrong PN',
  });
});

test('FR-4b: after the reason refusal Cancel closes the summary without an unknown-outcome notice', async () => {
  const box = await unknownOutcomeThenReasonRefusal();

  fireEvent.click(within(box).getByRole('button', { name: 'Cancel (Esc)' }));
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', {
        name: 'Reverse this Part Number action?',
      }),
    ).toBeNull(),
  );
  expect(document.body).not.toHaveTextContent('Reversal outcome unknown');
  expect(commands('/undos')).toHaveLength(2);
});

test('FR-4c: a generic refusal, then the reason refusal on Retry: the field is editable and the next Confirm opens the gate', async () => {
  const box = await undoSummary();
  writeFailure = { status: 409, body: { detail: ALREADY_REVERSED } };
  fireEvent.click(confirmReversal(box));
  await answerYes();
  await waitFor(() => expect(box).toHaveTextContent(ALREADY_REVERSED));

  reasonPolicy = true;
  fireEvent.click(within(box).getByRole('button', { name: 'Retry reversal' }));
  await waitFor(() => expect(box).toHaveTextContent(E_R1));
  expect(box).not.toHaveTextContent(ALREADY_REVERSED);
  const field = reasonField(box);
  expect(field).not.toHaveAttribute('readonly');
  const confirm = confirmReversal(box);
  expect(confirm).toBeDisabled();

  typeReason(box, 'wrong PN');
  fireEvent.click(confirm);
  await questionGate();
  expect(commands('/undos')).toHaveLength(2);
  await answerYes();
  await reversalRecorded();
  const sent = commands('/undos');
  expect(sent).toHaveLength(3);
  expect(sent[2].body.device_event_id).toBe(sent[0].body.device_event_id);
  expect(sent[2].body.reason).toBe('wrong PN');
});

test('FR-5: after a generic refusal the reason is read-only and Retry resends the same body', async () => {
  reasonPolicy = true;
  const box = await undoSummary();
  typeReason(box, 'wrong PN');
  writeFailure = { status: 409, body: { detail: ALREADY_REVERSED } };
  fireEvent.click(confirmReversal(box));
  await answerYes();
  await waitFor(() => expect(box).toHaveTextContent(ALREADY_REVERSED));

  expect(reasonField(box)).toHaveAttribute('readonly');
  fireEvent.click(within(box).getByRole('button', { name: 'Retry reversal' }));
  await reversalRecorded();
  const sent = commands('/undos');
  expect(sent).toHaveLength(2);
  expect(sent[1].body).toEqual(sent[0].body);
  expect(sent[1].body.reason).toBe('wrong PN');
});

test('FR-6: offline the reason stays editable and Confirm is disabled; after reconnecting the typed reason is sent', async () => {
  reasonPolicy = true;
  const box = await undoSummary();
  healthDown = true;
  const confirm = confirmReversal(box);
  await waitFor(() => expect(box).toHaveTextContent('Disconnected'));
  expect(confirm).toBeDisabled();
  typeReason(box, 'wrong PN');
  expect(reasonField(box)).toHaveValue('wrong PN');
  expect(reasonField(box)).not.toHaveAttribute('readonly');
  expect(confirm).toBeDisabled();

  healthDown = false;
  await waitFor(() => expect(confirm).toBeEnabled());
  fireEvent.click(confirm);
  await answerYes();
  await reversalRecorded();
  const sent = commands('/undos');
  expect(sent).toHaveLength(1);
  expect(sent[0].body.reason).toBe('wrong PN');
});

/* ============ The BADGE form ============ */

test('FR-7: the badge gate restates the reason and the one request carries the reason and the badge', async () => {
  scannedArea('BADGE');
  reasonPolicy = true;
  const box = await undoSummary();
  typeReason(box, 'wrong PN');
  fireEvent.click(confirmReversal(box));

  const gate = await badgeGate();
  expect(gateFacts(gate)).toMatch(/ Reason: wrong PN\.$/);
  scanGateBadge(gate, '100482');
  await reversalRecorded();
  const sent = commands('/undos');
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toEqual({
    part_number: 'PN-ON',
    reverses_device_event_id: undoTarget(),
    device_event_id: sent[0].body.device_event_id,
    confirming_badge: '100482',
    reason: 'wrong PN',
  });
});

test('FR-7: the reason refusal after a badge scan closes the badge gate; the next Confirm asks for a new scan', async () => {
  scannedArea('BADGE');
  const box = await undoSummary();
  fireEvent.click(confirmReversal(box));
  const gate = await badgeGate();
  reasonPolicy = true;
  scanGateBadge(gate, 'v-100517');

  await waitFor(() => expect(box).toHaveTextContent(E_R1));
  expect(
    screen.queryByRole('dialog', {
      name: 'Scan badge to confirm the reversal',
    }),
  ).toBeNull();
  const field = reasonField(box);
  await waitFor(() => expect(document.activeElement).toBe(field));
  // The refusal preceded the gate: no Worker was signed in by the badge.
  expect(serverSession!.worker).toBe(NGUYEN);

  typeReason(box, 'wrong PN');
  fireEvent.click(confirmReversal(box));
  const again = await badgeGate();
  expect(gateFacts(again)).toMatch(/ Reason: wrong PN\.$/);
  expect(commands('/undos')).toHaveLength(1);
  scanGateBadge(again, '100482');
  await reversalRecorded();
  const sent = commands('/undos');
  expect(sent).toHaveLength(2);
  expect(sent[0].body.confirming_badge).toBe('v-100517');
  expect(sent[0].body).not.toHaveProperty('reason');
  expect(sent[1].body).toEqual({
    ...sent[0].body,
    confirming_badge: '100482',
    reason: 'wrong PN',
  });
});

/* ============ Preview re-read ============ */

test('FR-8: once asked, the reason stays visible and required across a preview re-read that no longer requires it', async () => {
  scannedArea('QUESTION');
  reasonPolicy = true;
  const box = await undoSummary();
  typeReason(box, 'wrong PN');
  expect(previewReads()).toHaveLength(1);

  // Meanwhile: the policy is switched Off, another Worker signs in, and
  // the server now wants a badge for Undo (the context still says the
  // question) — the typed gate refusal re-reads the context, whose new
  // Worker re-reads the preview (`Reversed by`).
  reasonPolicy = false;
  startSession(TRAN);
  serverGates.undo = 'BADGE';
  fireEvent.click(confirmReversal(box));
  const question = await questionGate();
  expect(gateFacts(question)).toMatch(/ Reason: wrong PN\.$/);
  await answerYes();
  const gate = await badgeGate();
  await waitFor(() => expect(previewReads()).toHaveLength(2));
  await waitFor(() => expect(box).toHaveTextContent('V. Tran'));
  fireEvent.click(within(gate).getByRole('button', { name: 'Cancel (Esc)' }));

  // (a) The draft is kept; (b) the field stays visible, editable and
  // required although the fresh preview requires no reason.
  const field = reasonField(box);
  expect(field).toHaveValue('wrong PN');
  expect(field).not.toHaveAttribute('readonly');
  expect(box).toHaveTextContent('Reason (required)');
  const confirm = confirmReversal(box);
  typeReason(box, '');
  expect(confirm).toBeDisabled();
  typeReason(box, 'wrong PN');
  expect(confirm).toBeEnabled();

  fireEvent.click(confirm);
  const again = await badgeGate();
  expect(gateFacts(again)).toMatch(/ Reason: wrong PN\.$/);
  scanGateBadge(again, 'v-100517');
  await reversalRecorded();
  const sent = commands('/undos');
  expect(sent).toHaveLength(2);
  expect(sent[1].body).toEqual({
    part_number: 'PN-ON',
    reverses_device_event_id: undoTarget(),
    device_event_id: sent[0].body.device_event_id,
    confirming_badge: 'v-100517',
    reason: 'wrong PN',
  });
});
