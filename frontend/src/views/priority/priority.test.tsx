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
import { StrictMode } from 'react';
import type { ReactNode } from 'react';

import { PERMISSIONS } from '../../api/roles';
import type { Permission } from '../../api/roles';
import { SessionContext, hasPermission } from '../../app/session-context';
import type { SessionValue } from '../../app/session-context';
import { ConnectivityProvider } from '../../app/connectivity-provider';
import { clearHotHistory } from './hot-history';
import {
  clearPriorityFocus,
  peekPriorityFocus,
  requestPriorityFocus,
} from './priority-focus';
import { PriorityView } from './PriorityView';

// Priority Management regression tests (Phase 12): the view runs against
// the REAL Hot list API — these tests exercise it against an in-memory
// fake of `/api/hot-list` with the same wire contract and semantics
// (one single-entry command against the confirmed `expected_order`, a
// stale precondition answered 409 with the current entries, eligibility
// refusals, `device_event_id` replay). Covered: the row presentation,
// add by search and by PN barcode (0 / 1 / many), the removal and
// order-change confirmations (nothing is sent on Cancel, the list is
// never renumbered before the server answers), the intent-based session
// Undo/Redo re-based on the current list, the unknown-outcome Retry
// with the SAME idempotency key, Reload list, and the write blocks.

interface FakeDemand {
  id: number;
  pn: string;
  workOrderId: number;
  workOrderNumber: string | null;
  received: string;
  completed: boolean;
  requestType: 'NEW' | 'MODIFY';
  jobs: string[];
  requested: number;
  allocated: number;
  released: number;
  due: string | null;
  rank: number | null;
}

type PostFailure =
  /** Transport loss before the server saw the request. */
  | { kind: 'drop-before' }
  /** The server committed, then the response was lost. */
  | { kind: 'drop-after-commit' }
  /** A gateway/server error after the commit. */
  | { kind: 'status-after-commit'; status: number }
  /** A refusal before anything was written (sign-in, permission, …). */
  | { kind: 'refuse'; status: number; body: Record<string, unknown> };

interface FakeState {
  demands: FakeDemand[];
  /** PN → its ACTIVE distribution (wire `part_number_locations`). */
  locations: Record<string, unknown[]>;
  departmentRefusal: { status: number; detail: string } | null;
  healthDown: boolean;
  /** Every POST body the client sent, in order. */
  posts: Record<string, unknown>[];
  /** Every non-health GET the client sent, in order. */
  reads: string[];
  committed: Map<string, { payload: string; changes: unknown[] }>;
  nextPost: PostFailure | null;
  /** Hold every POST until the test releases it. */
  holdPost: Promise<void> | null;
  /** Hold the NEXT GET of this path (with its query) until released. */
  holdRead: { path: string; until: Promise<void> } | null;
  /** Answer the NEXT GET of this path (with its query) with a 503. */
  failRead: string | null;
  /** The Due Soon warning policy (`GET /api/policies/due-soon` wire). */
  policy: Record<string, unknown>;
}

function policyWire(minDays: number, percent: number, maxDays: number) {
  return {
    due_soon_min_days: minDays,
    due_soon_lead_time_percent: percent,
    due_soon_max_days: maxDays,
    updated_at: '2026-10-01T08:00:00Z',
  };
}

const STALE =
  'The Hot list was changed elsewhere. The current list is shown; review it and try again. Nothing was changed.';
const MISSING = 'This Work Order Demand no longer exists. Nothing was changed.';
const NOT_PN_BARCODE =
  'This is not a Part Number barcode. Scan a PN barcode (PF:PN:…) or search by PN, Work Order Number or Job Number.';

function demand(
  id: number,
  pn: string,
  workOrderNumber: string | null,
  extra?: Partial<FakeDemand>,
): FakeDemand {
  return {
    id,
    pn,
    workOrderId: id * 10,
    workOrderNumber,
    received: '2026-08-01',
    completed: false,
    requestType: 'NEW',
    jobs: [],
    requested: 10,
    allocated: 0,
    released: 0,
    due: null,
    rank: null,
    ...extra,
  };
}

function seedState(): FakeState {
  return {
    demands: [
      // The Hot list, rank order.
      demand(11, 'A-100', '007001', {
        jobs: ['18112'],
        released: 10,
        due: '2026-11-20',
        rank: 1,
      }),
      // Internal Work Order: no external number, nothing released.
      demand(12, 'B-200', null, {
        received: '2026-08-05',
        requestType: 'MODIFY',
        requested: 5,
        rank: 2,
      }),
      // Completed Work Order: inactive, kept on the list.
      demand(13, 'C-300', '007003', {
        completed: true,
        requested: 8,
        allocated: 8,
        released: 8,
        due: '2026-10-30',
        rank: 3,
      }),
      // Open Work Order, fully allocated from stock — this demand
      // itself never released anything.
      demand(14, 'D-400', '007004', {
        requested: 6,
        allocated: 6,
        due: '2026-12-15',
        rank: 4,
      }),
      // Eligible candidates.
      demand(21, 'E-500', '007010', {
        jobs: ['18190'],
        requested: 12,
        due: '2026-12-01',
      }),
      demand(22, 'F-600', '007011', { requested: 3 }),
      demand(23, 'F-600', '007012', { requested: 4 }),
      demand(24, 'G-700', '007013', { requested: 2 }),
    ],
    locations: {
      'A-100': [
        {
          area: { id: 2, name: 'Lathe', color: '#f2a44a' },
          machine: { id: 7, name: 'CNC-01' },
          activity: null,
          state: 'MACHINE',
          quantity: 4,
        },
        {
          area: { id: 3, name: 'Mill', color: null },
          machine: null,
          activity: null,
          state: 'QUEUE',
          quantity: 6,
        },
      ],
      'D-400': [
        {
          area: { id: 4, name: 'External', color: null },
          machine: null,
          activity: 'Plating',
          state: 'PROCESSING',
          quantity: 3,
        },
      ],
    },
    departmentRefusal: null,
    healthDown: false,
    posts: [],
    reads: [],
    committed: new Map(),
    nextPost: null,
    holdPost: null,
    holdRead: null,
    failRead: null,
    policy: policyWire(2, 15, 7),
  };
}

let state: FakeState;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const detail = (message: string, status: number) =>
  json({ detail: message }, status);

function shortage(d: FakeDemand) {
  return Math.max(d.requested - d.allocated, 0);
}

function entryWire(d: FakeDemand) {
  return {
    work_order_demand_id: d.id,
    rank: d.rank,
    part_number: d.pn,
    work_order_id: d.workOrderId,
    work_order_number: d.workOrderNumber,
    work_order_received_date: d.received,
    work_order_completed: d.completed,
    request_type: d.requestType,
    job_numbers: d.jobs,
    requested_quantity: d.requested,
    allocated_quantity: d.allocated,
    shortage_quantity: shortage(d),
    active: !d.completed && shortage(d) > 0,
    released_quantity: d.released,
    due_date: d.due,
    part_number_locations: state.locations[d.pn] ?? [],
  };
}

function ranked(): FakeDemand[] {
  return state.demands
    .filter((d) => d.rank !== null)
    .sort((a, b) => (a.rank as number) - (b.rank as number));
}

const rankedOrder = () => ranked().map((d) => d.id);
const currentEntries = () => ranked().map(entryWire);

const eligible = (d: FakeDemand) =>
  d.rank === null && !d.completed && shortage(d) > 0;

/** Set the server-side order directly — a change made elsewhere. */
function setServerOrder(ids: number[]) {
  for (const d of state.demands) d.rank = null;
  ids.forEach((id, index) => {
    state.demands.find((d) => d.id === id)!.rank = index + 1;
  });
}

function candidates(url: URL): Response {
  const search = url.searchParams.get('search');
  const barcode = url.searchParams.get('barcode');
  let matches: FakeDemand[];
  let partNumber: string | null = null;
  if (barcode !== null) {
    const value = barcode.trim();
    if (!value.startsWith('PF:PN:')) return detail(NOT_PN_BARCODE, 422);
    partNumber = value.slice('PF:PN:'.length).toUpperCase();
    matches = state.demands.filter((d) => d.pn === partNumber);
  } else if (search !== null) {
    const term = search.trim().toLowerCase();
    matches = state.demands.filter((d) =>
      [d.pn, d.workOrderNumber ?? '', ...d.jobs].some((value) =>
        value.toLowerCase().includes(term),
      ),
    );
  } else {
    matches = state.demands;
  }
  return json({
    part_number: partNumber,
    candidates: matches.filter(eligible).map(entryWire),
    already_listed_count: matches.filter((d) => d.rank !== null).length,
    truncated: false,
  });
}

async function change(body: Record<string, unknown>): Promise<Response> {
  state.posts.push(body);
  if (state.holdPost) await state.holdPost;
  const failure = state.nextPost;
  state.nextPost = null;
  if (failure?.kind === 'drop-before') throw new TypeError('Failed to fetch');
  if (failure?.kind === 'refuse') return json(failure.body, failure.status);

  const key = String(body.device_event_id);
  const expected = body.expected_order as number[];
  const next = body.new_order as number[];
  const payload = JSON.stringify([body.action, expected, next]);
  const prior = state.committed.get(key);
  if (prior) {
    if (prior.payload !== payload) {
      return detail('This device event was already used.', 409);
    }
    // A replay answers whatever the Department configuration is now;
    // the list itself cannot be shown without one active Department.
    return json(
      {
        device_event_id: key,
        action: body.action,
        created: false,
        changes: prior.changes,
        entries: state.departmentRefusal ? null : currentEntries(),
      },
      200,
    );
  }
  if (state.departmentRefusal) {
    return detail(
      state.departmentRefusal.detail,
      state.departmentRefusal.status,
    );
  }
  const current = rankedOrder();
  if (JSON.stringify(current) !== JSON.stringify(expected)) {
    return json(
      { detail: STALE, hot_list_changed: true, entries: currentEntries() },
      409,
    );
  }
  for (const id of next.filter((value) => !current.includes(value))) {
    const target = state.demands.find((d) => d.id === id);
    if (!target) return detail(MISSING, 404);
    if (target.completed) {
      return detail(
        `Work Order ${target.workOrderNumber ?? '— (internal)'} is completed, so its demand cannot be added to the Hot list. Nothing was changed.`,
        409,
      );
    }
    if (shortage(target) === 0) {
      return detail(`${target.pn} is fully allocated.`, 409);
    }
  }
  const before = new Map(state.demands.map((d) => [d.id, d.rank]));
  setServerOrder(next);
  const changes = state.demands
    .filter((d) => before.get(d.id) !== d.rank)
    .map((d) => ({
      work_order_demand_id: d.id,
      part_number: d.pn,
      work_order_number: d.workOrderNumber,
      previous_rank: before.get(d.id) ?? null,
      new_rank: d.rank,
    }));
  state.committed.set(key, { payload, changes });
  if (failure?.kind === 'drop-after-commit') {
    throw new TypeError('Failed to fetch');
  }
  if (failure?.kind === 'status-after-commit') {
    return detail('Bad gateway.', failure.status);
  }
  return json(
    {
      device_event_id: key,
      action: body.action,
      created: true,
      changes,
      entries: currentEntries(),
    },
    201,
  );
}

async function handle(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const url = new URL(String(input), 'http://partflow.test');
  const method = init?.method ?? 'GET';
  if (url.pathname === '/api/health') {
    return state.healthDown
      ? detail('Service unavailable.', 503)
      : json({ status: 'ok' });
  }
  const read = `${url.pathname}${url.search}`;
  if (method === 'GET') {
    state.reads.push(read);
    if (state.holdRead?.path === read) {
      const { until } = state.holdRead;
      state.holdRead = null;
      await until;
    }
    if (state.failRead === read) {
      state.failRead = null;
      return detail('Service unavailable.', 503);
    }
  }
  if (url.pathname === '/api/hot-list/changes' && method === 'POST') {
    return change(JSON.parse(String(init?.body)) as Record<string, unknown>);
  }
  if (state.departmentRefusal && url.pathname.startsWith('/api/hot-list')) {
    return detail(
      state.departmentRefusal.detail,
      state.departmentRefusal.status,
    );
  }
  if (url.pathname === '/api/hot-list') {
    return json({
      department: { id: 1, name: 'Machining' },
      entries: currentEntries(),
    });
  }
  if (url.pathname === '/api/hot-list/candidates') return candidates(url);
  if (url.pathname === '/api/policies/due-soon') return json(state.policy);
  return detail('Not found.', 404);
}

beforeEach(() => {
  window.history.replaceState({}, '', '/management/priority');
  session = signedInSession();
  state = seedState();
  // The session history is module-scoped (it survives sub-view
  // switches); every test starts a fresh session.
  clearHotHistory();
  clearPriorityFocus();
  vi.stubGlobal('fetch', vi.fn(handle));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/**
 * The signed-in user of a test (Phase 14 slice 3): a Management view
 * offers its changes only to a user holding their permissions, so a
 * test holds every permission unless it signs in another user.
 */
function signedInSession(
  permissions: readonly Permission[] = PERMISSIONS,
): SessionValue {
  const user = {
    id: 90,
    loginName: 'mia',
    displayName: 'Mia Manager',
    roleId: 2,
    roleName: 'Manager',
    avatarUpdatedAt: null,
    permissions: [...permissions],
    mustChangePassword: false,
    sessionExpiresAt: null,
    themePreference: null,
  };
  return {
    status: 'signed-in',
    user,
    setupOpen: false,
    checking: false,
    endedBy: null,
    can: (permission) => hasPermission(user, permission),
    openSignIn: vi.fn(),
    openSetup: vi.fn(),
    openChangePassword: vi.fn(),
    signOut: vi.fn(async () => {}),
    refresh: vi.fn(async () => {}),
  };
}

let session: SessionValue = signedInSession();

function SignedIn({ children }: { children: ReactNode }) {
  return (
    <SessionContext.Provider value={session}>
      {children}
    </SessionContext.Provider>
  );
}

const INITIAL = ['A-100', 'B-200', 'C-300', 'D-400'];

async function renderPriority() {
  render(
    <ConnectivityProvider>
      <PriorityView />
    </ConnectivityProvider>,
    { wrapper: SignedIn },
  );
  // Wait for the list and the connectivity check so writes enable.
  await screen.findByRole('button', { name: '⟲ Undo' });
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Move A-100 down' }),
    ).toBeEnabled(),
  );
}

function listedPns(): (string | null)[] {
  return Array.from(
    document.querySelectorAll('.pr-item .pn'),
    (el) => el.textContent,
  );
}

function rowOf(pn: string): HTMLElement {
  return Array.from(document.querySelectorAll<HTMLElement>('.pr-item')).find(
    (row) => row.querySelector('.pn')?.textContent === pn,
  ) as HTMLElement;
}

const undoButton = () => screen.getByRole('button', { name: '⟲ Undo' });
const redoButton = () => screen.getByRole('button', { name: '⟳ Redo' });

/** Confirm the open order-change dialog and wait for the answer. */
async function applyRanking() {
  const posts = state.posts.length;
  fireEvent.click(screen.getByRole('button', { name: 'Apply ranking' }));
  await waitFor(() => expect(state.posts).toHaveLength(posts + 1));
  await waitFor(() =>
    expect(screen.queryByText('Applying the change…')).toBeNull(),
  );
}

/* ============ Rows ============ */

test('rows render the live Hot list with its flags, labels and distribution', async () => {
  await renderPriority();
  expect(listedPns()).toEqual(INITIAL);

  // `🔥#rank` immediately before the PN; WO + Job from explicit fields.
  const first = rowOf('A-100');
  expect(first.querySelector('.l1')?.textContent).toContain('🔥#1 A-100');
  expect(first.querySelector('.wjchip')?.textContent).toBe(
    'WO 007001 · Job 18112',
  );
  expect(first).toHaveTextContent('requested 10');
  expect(first).toHaveTextContent('allocated 0');
  expect(first).toHaveTextContent('shortage 10');
  // The PN's distribution, labeled as the PN's.
  const distribution = first.querySelector('.l3') as HTMLElement;
  expect(distribution).toHaveTextContent('A-100 in production');
  expect(distribution).toHaveTextContent('Lathe · CNC-01 4 on machine');
  expect(distribution).toHaveTextContent('Mill 6 queue');

  // Internal Work Order: `—` with the quiet label; nothing released and
  // no active quantity; no due date.
  const internal = rowOf('B-200');
  expect(internal.querySelector('.wjchip')?.textContent).toBe('WO —');
  expect(internal).toHaveTextContent(
    'internal Work Order · received Aug 05, 2026',
  );
  expect(internal.querySelector('.l3')).toHaveTextContent('Not yet released');
  expect(internal.querySelector('.due')).toHaveTextContent('No due date');

  // Completed Work Order: the status chip; released quantity no longer
  // active.
  const completed = rowOf('C-300');
  expect(completed.querySelector('.wostat.completed')).toHaveTextContent(
    'Completed',
  );
  expect(completed).not.toHaveTextContent('Fully allocated');
  expect(completed.querySelector('.l3')).toHaveTextContent(
    'No active quantity — released quantity is stocked, scrapped or awaiting allocation',
  );

  // Open Work Order, fully allocated: the quiet note; the PN's
  // locations exist but this demand released nothing.
  const allocated = rowOf('D-400');
  expect(allocated).toHaveTextContent(
    'Fully allocated — nothing left to expedite',
  );
  expect(allocated.querySelector('.wostat')).toBeNull();
  expect(allocated.querySelector('.l3')).toHaveTextContent(
    'External 3 Plating',
  );
  expect(allocated.querySelector('.l3')).toHaveTextContent(
    'not yet released for this demand',
  );

  // Inactive entries keep Remove / Move.
  expect(
    screen.getByRole('button', { name: 'Remove C-300 from Hot list' }),
  ).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Move C-300 up' })).toBeEnabled();
  expect(document.body).not.toHaveTextContent('priority_rank');
});

test('the footer says an entry leaves the list once its line is fully allocated', async () => {
  await renderPriority();
  expect(document.body).toHaveTextContent(
    'An entry leaves the list on its own once its line is fully allocated.',
  );
});

test('an empty Hot list says how to add an entry', async () => {
  setServerOrder([]);
  render(
    <ConnectivityProvider>
      <PriorityView />
    </ConnectivityProvider>,
    { wrapper: SignedIn },
  );
  expect(await screen.findByText(/No Hot WO Demand/)).toBeInTheDocument();
});

test('a Department refusal renders the error state with the server detail and Retry', async () => {
  state.departmentRefusal = {
    status: 409,
    detail:
      'Several active Departments exist (Machining, Assembly). The Hot list is managed within one Department, so nothing can be shown or changed until exactly one Department is active.',
  };
  render(
    <ConnectivityProvider>
      <PriorityView />
    </ConnectivityProvider>,
    { wrapper: SignedIn },
  );
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('The Hot list could not be loaded.');
  expect(alert).toHaveTextContent('Several active Departments exist');

  state.departmentRefusal = null;
  fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(listedPns()).toEqual(INITIAL));
});

/* ============ Order changes ============ */

test('Move Down asks for confirmation; Cancel and Escape send nothing', async () => {
  await renderPriority();

  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  const dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  expect(dialog).toHaveTextContent('Move A-100 · WO 007001 from #1 to #2');
  expect(dialog).toHaveTextContent('1 other demand will shift up.');
  expect(dialog).toHaveTextContent('Move Down');
  const newSide = dialog.querySelectorAll('.pr-snapshot')[1];
  expect(
    Array.from(
      newSide.querySelectorAll('.pr-snaprow .prr'),
      (el) => el.textContent,
    ),
  ).toEqual(['#2 → #1', '#1 → #2']);
  // The internal Work Order line names itself and its quantity.
  expect(newSide).toHaveTextContent(
    'WO — · internal Work Order · received Aug 05, 2026 · 5 pcs',
  );

  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));

  expect(listedPns()).toEqual(INITIAL);
  expect(state.posts).toEqual([]);
  expect(undoButton()).toBeDisabled();
  expect(redoButton()).toBeDisabled();
});

test('Move Down sends the exact command and renders the committed list', async () => {
  await renderPriority();
  let release!: () => void;
  state.holdPost = new Promise((resolve) => {
    release = resolve;
  });

  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply ranking' }));
  await waitFor(() => expect(state.posts).toHaveLength(1));
  expect(state.posts[0]).toEqual({
    device_event_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    action: 'MOVE_DOWN',
    expected_order: [11, 12, 13, 14],
    new_order: [12, 11, 13, 14],
  });

  // In flight: never renumbered before the server answers, and every
  // write control is frozen — no double submit.
  expect(screen.getByText('Applying the change…')).toBeInTheDocument();
  expect(listedPns()).toEqual(INITIAL);
  expect(
    screen.getByRole('button', { name: 'Move B-200 down' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: '+ Add to Hot list' }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Move B-200 down' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(state.posts).toHaveLength(1);

  state.holdPost = null;
  release();
  await waitFor(() =>
    expect(listedPns()).toEqual(['B-200', 'A-100', 'C-300', 'D-400']),
  );
  expect(rowOf('A-100').querySelector('.l1')?.textContent).toContain('🔥#2');
  expect(undoButton()).toBeEnabled();
  expect(redoButton()).toBeDisabled();
});

test('Move Up sends MOVE_UP by exactly one position', async () => {
  await renderPriority();

  fireEvent.click(screen.getByRole('button', { name: 'Move D-400 up' }));
  expect(
    screen.getByRole('dialog', { name: 'Confirm Hot ranking change' }),
  ).toHaveTextContent('Move Up');
  await applyRanking();

  expect(state.posts[0]).toMatchObject({
    action: 'MOVE_UP',
    expected_order: [11, 12, 13, 14],
    new_order: [11, 12, 14, 13],
  });
  await waitFor(() =>
    expect(listedPns()).toEqual(['A-100', 'B-200', 'D-400', 'C-300']),
  );
});

test('drag and drop asks for confirmation and sends DRAG', async () => {
  await renderPriority();
  const items = document.querySelectorAll('.pr-item');

  fireEvent.dragStart(items[0]);
  fireEvent.dragOver(items[2]);
  fireEvent.drop(items[2]);
  const dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  expect(dialog).toHaveTextContent('Drag and drop');
  expect(dialog).toHaveTextContent('Move A-100 · WO 007001 from #1 to #3');
  expect(dialog).toHaveTextContent('2 other demands will shift up.');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(state.posts).toEqual([]);

  const again = document.querySelectorAll('.pr-item');
  fireEvent.dragStart(again[0]);
  fireEvent.drop(again[2]);
  await applyRanking();
  expect(state.posts[0]).toMatchObject({
    action: 'DRAG',
    expected_order: [11, 12, 13, 14],
    new_order: [12, 13, 11, 14],
  });
  await waitFor(() =>
    expect(listedPns()).toEqual(['B-200', 'C-300', 'A-100', 'D-400']),
  );
});

test('confirmation shows Current Position and New Position snapshots', async () => {
  await renderPriority();
  const items = document.querySelectorAll('.pr-item');

  // Drop the first entry on the third: #1 → #3, two entries shift up.
  fireEvent.dragStart(items[0]);
  fireEvent.drop(items[2]);
  const dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });

  const sections = dialog.querySelectorAll('.pr-snapshot');
  expect(sections).toHaveLength(2);
  const [current, proposed] = Array.from(sections);
  expect(current.querySelector('.pr-snaptitle')?.textContent).toBe(
    'Current Position',
  );
  expect(proposed.querySelector('.pr-snaptitle')?.textContent).toBe(
    'New Position',
  );
  expect(dialog.querySelectorAll('.pr-transition')).toHaveLength(1);

  // Current Position: current rank order, per-row direction arrows, the
  // moved entry highlighted; the affected range only (#1..#3).
  const curRows = Array.from(current.querySelectorAll('.pr-snaprow'));
  expect(curRows).toHaveLength(3);
  expect(curRows[0].querySelector('.prpn')?.textContent).toBe('A-100');
  expect(curRows[0].className).toContain('moved');
  expect(curRows[0].querySelector('.dir.down')?.textContent).toBe('↓');
  expect(curRows[1].querySelector('.dir.up')?.textContent).toBe('↑');
  expect(curRows[0].querySelector('.wjchip')?.textContent).toBe(
    'WO 007001 · Job 18112',
  );
  expect(curRows[0].querySelector('.prpn')?.textContent).not.toContain('WO');

  // New Position: proposed order, complete `#old → #new` transitions.
  const newRows = Array.from(proposed.querySelectorAll('.pr-snaprow'));
  expect(newRows.map((row) => row.querySelector('.prr')?.textContent)).toEqual([
    '#2 → #1',
    '#3 → #2',
    '#1 → #3',
  ]);
  expect(proposed.querySelector('.dir')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
});

/* ============ Remove ============ */

test('removal asks for confirmation; Cancel sends nothing, confirm sends REMOVE', async () => {
  await renderPriority();

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove B-200 from Hot list' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Remove from Hot list?' });
  // The internal Work Order Demand is identified, not just `WO —`.
  expect(dialog).toHaveTextContent('B-200');
  expect(dialog).toHaveTextContent(
    'WO — · internal Work Order · received Aug 05, 2026 · 5 pcs',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(state.posts).toEqual([]);
  expect(listedPns()).toEqual(INITIAL);

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove B-200 from Hot list' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove entry' }));
  await waitFor(() => expect(listedPns()).toEqual(['A-100', 'C-300', 'D-400']));
  expect(state.posts[0]).toMatchObject({
    action: 'REMOVE',
    expected_order: [11, 12, 13, 14],
    new_order: [11, 13, 14],
  });
  expect(undoButton()).toBeEnabled();
});

test('removing an inactive entry never promises that Undo can restore it', async () => {
  await renderPriority();
  const removeDialog = () =>
    screen.getByRole('dialog', { name: 'Remove from Hot list?' });

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove B-200 from Hot list' }),
  );
  expect(removeDialog()).toHaveTextContent(
    'Remaining ranks close the gap; Undo can restore it.',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));

  // Fully allocated line of an open Work Order.
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove D-400 from Hot list' }),
  );
  expect(removeDialog()).toHaveTextContent(
    'Undo cannot add it back while the line is fully allocated.',
  );
  expect(removeDialog()).not.toHaveTextContent('Undo can restore');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));

  // Completed Work Order.
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove C-300 from Hot list' }),
  );
  expect(removeDialog()).toHaveTextContent(
    'Undo cannot add it back while its Work Order is completed.',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove entry' }));
  expect(
    await screen.findByText(
      '✕ C-300 · WO 007003 removed from Hot list — remaining ranks close the gap · Undo cannot add it back while its Work Order is completed',
    ),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Undo can restore it/)).toBeNull();
});

/* ============ Add ============ */

test('adding by search applies directly at the bottom — no order-change confirmation', async () => {
  await renderPriority();

  fireEvent.click(screen.getByRole('button', { name: '+ Add to Hot list' }));
  const dialog = screen.getByRole('dialog', {
    name: 'Add WO Demand to Hot list',
  });
  // Every eligible demand is listed; ranked, completed and fully
  // allocated demand never is (the server decides eligibility).
  await within(dialog).findByRole('button', { name: /WO 007010/ });
  expect(within(dialog).queryByRole('button', { name: /A-100/ })).toBeNull();

  fireEvent.change(
    within(dialog).getByLabelText(
      'Search PN, WO, Job Number or scan PN barcode',
    ),
    { target: { value: '18190' } },
  );
  await waitFor(() =>
    expect(state.reads).toContain('/api/hot-list/candidates?search=18190'),
  );
  await waitFor(() =>
    expect(within(dialog).queryByRole('button', { name: /G-700/ })).toBeNull(),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: /WO 007010/ }));

  expect(
    screen.queryByRole('dialog', { name: 'Confirm Hot ranking change' }),
  ).toBeNull();
  await waitFor(() => expect(listedPns()).toEqual([...INITIAL, 'E-500']));
  expect(state.posts[0]).toMatchObject({
    action: 'ADD',
    expected_order: [11, 12, 13, 14],
    new_order: [11, 12, 13, 14, 21],
  });
});

test('a PN barcode with exactly one eligible WO Demand adds directly', async () => {
  await renderPriority();

  fireEvent.click(screen.getByRole('button', { name: '+ Add to Hot list' }));
  const search = screen.getByLabelText(
    'Search PN, WO, Job Number or scan PN barcode',
  );
  fireEvent.change(search, { target: { value: 'PF:PN:G-700' } });
  fireEvent.keyDown(search, { key: 'Enter' });

  await waitFor(() => expect(listedPns()).toEqual([...INITIAL, 'G-700']));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(state.reads).toContain(
    '/api/hot-list/candidates?barcode=PF%3APN%3AG-700',
  );
  expect(state.posts[0]).toMatchObject({
    action: 'ADD',
    new_order: [11, 12, 13, 14, 24],
  });
});

test('an ambiguous PN barcode never adds by guess — it requires an explicit selection', async () => {
  await renderPriority();

  fireEvent.click(screen.getByRole('button', { name: '+ Add to Hot list' }));
  const dialog = screen.getByRole('dialog', {
    name: 'Add WO Demand to Hot list',
  });
  const search = within(dialog).getByLabelText(
    'Search PN, WO, Job Number or scan PN barcode',
  );
  fireEvent.change(search, { target: { value: 'PF:PN:F-600' } });
  fireEvent.keyDown(search, { key: 'Enter' });

  expect(
    await within(dialog).findByText(
      'Multiple eligible Work Order Demands use PN F-600 — select the Work Order to add.',
    ),
  ).toBeInTheDocument();
  const options = within(dialog).getAllByRole('button', { name: /F-600/ });
  expect(options).toHaveLength(2);
  expect(within(dialog).queryByRole('button', { name: /E-500/ })).toBeNull();
  expect(state.posts).toEqual([]);

  fireEvent.click(within(dialog).getByRole('button', { name: /WO 007012/ }));
  await waitFor(() => expect(listedPns()).toEqual([...INITIAL, 'F-600']));
  expect(state.posts[0]).toMatchObject({
    action: 'ADD',
    new_order: [11, 12, 13, 14, 23],
  });
});

test('a PN barcode with no eligible WO Demand adds nothing and says why', async () => {
  await renderPriority();

  fireEvent.click(screen.getByRole('button', { name: '+ Add to Hot list' }));
  const dialog = screen.getByRole('dialog', {
    name: 'Add WO Demand to Hot list',
  });
  const search = within(dialog).getByLabelText(
    'Search PN, WO, Job Number or scan PN barcode',
  );
  fireEvent.change(search, { target: { value: 'PF:PN:A-100' } });
  fireEvent.keyDown(search, { key: 'Enter' });
  expect(
    await within(dialog).findByText(
      'No eligible Work Order Demand for A-100 — 1 already on the Hot list. Nothing was added.',
    ),
  ).toBeInTheDocument();
  // Ready for the next scan.
  expect(search).toHaveFocus();

  // A barcode that is not a PN barcode: the server's Priority copy.
  fireEvent.change(search, { target: { value: 'PF:MACHINE:7' } });
  fireEvent.keyDown(search, { key: 'Enter' });
  expect(await within(dialog).findByText(NOT_PN_BARCODE)).toBeInTheDocument();

  expect(state.posts).toEqual([]);
  expect(listedPns()).toEqual(INITIAL);
});

async function openAddDialog() {
  fireEvent.click(screen.getByRole('button', { name: '+ Add to Hot list' }));
  const dialog = screen.getByRole('dialog', {
    name: 'Add WO Demand to Hot list',
  });
  const search = within(dialog).getByLabelText(
    'Search PN, WO, Job Number or scan PN barcode',
  ) as HTMLInputElement;
  return { dialog, search };
}

function scan(search: HTMLInputElement, barcode: string) {
  fireEvent.change(search, { target: { value: barcode } });
  fireEvent.keyDown(search, { key: 'Enter' });
}

/** Focused with its whole value selected: the next scan replaces it. */
function expectReadyForNextScan(search: HTMLInputElement) {
  expect(search).toHaveFocus();
  expect(search.selectionStart).toBe(0);
  expect(search.selectionEnd).toBe(search.value.length);
}

test('after an ambiguous or blocked PN scan the scanned value stays selected for the next scan', async () => {
  await renderPriority();
  const { dialog, search } = await openAddDialog();

  scan(search, 'PF:PN:F-600');
  await within(dialog).findByText(
    'Multiple eligible Work Order Demands use PN F-600 — select the Work Order to add.',
  );
  expectReadyForNextScan(search);

  // Blocked: the connection drops while the dialog is open.
  state.healthDown = true;
  act(() => {
    window.dispatchEvent(new Event('offline'));
  });
  scan(search, 'PF:PN:G-700');
  await within(dialog).findByText(
    'Changes are blocked right now, so nothing was added. Try again once the Hot list is ready.',
  );
  expectReadyForNextScan(search);
  expect(state.posts).toEqual([]);
});

test('an ambiguous PN scan replaces a failed candidate read with its own list', async () => {
  await renderPriority();
  state.failRead = '/api/hot-list/candidates';
  const { dialog, search } = await openAddDialog();
  expect(
    await within(dialog).findByText('Service unavailable.'),
  ).toBeInTheDocument();

  scan(search, 'PF:PN:F-600');
  await waitFor(() =>
    expect(
      within(dialog).getAllByRole('button', { name: /F-600/ }),
    ).toHaveLength(2),
  );
  expect(within(dialog).queryByText('Service unavailable.')).toBeNull();
});

test('a PN scan before the candidate list arrives never leaves the list loading', async () => {
  await renderPriority();
  let release!: () => void;
  state.holdRead = {
    path: '/api/hot-list/candidates',
    until: new Promise<void>((resolve) => {
      release = resolve;
    }),
  };
  const { dialog, search } = await openAddDialog();
  // The default read is on its way, and held.
  await waitFor(() =>
    expect(state.reads).toContain('/api/hot-list/candidates'),
  );
  expect(
    within(dialog).getByText('Loading eligible Work Order Demand…'),
  ).toBeInTheDocument();

  // The scan supersedes the pending read and lists nothing itself.
  scan(search, 'PF:PN:A-100');
  await within(dialog).findByText(
    'No eligible Work Order Demand for A-100 — 1 already on the Hot list. Nothing was added.',
  );
  await within(dialog).findByRole('button', { name: /WO 007010/ });
  expect(
    within(dialog).queryByText('Loading eligible Work Order Demand…'),
  ).toBeNull();
  release();
});

/* ============ Session Undo / Redo ============ */

test('Undo and Redo confirm with user-facing titles; Cancel keeps both histories', async () => {
  await renderPriority();
  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  const reordered = ['B-200', 'A-100', 'C-300', 'D-400'];
  await waitFor(() => expect(listedPns()).toEqual(reordered));

  fireEvent.click(undoButton());
  let dialog = screen.getByRole('dialog', { name: 'Restore previous ranking' });
  expect(dialog).toHaveTextContent('previous confirmed order');
  expect(dialog).toHaveTextContent('Undo');
  expect(dialog.querySelector('.pr-snaprow.moved')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(state.posts).toHaveLength(1);
  expect(undoButton()).toBeEnabled();
  expect(redoButton()).toBeDisabled();

  fireEvent.click(undoButton());
  await applyRanking();
  expect(state.posts[1]).toMatchObject({
    action: 'UNDO',
    expected_order: [12, 11, 13, 14],
    new_order: [11, 12, 13, 14],
  });
  await waitFor(() => expect(listedPns()).toEqual(INITIAL));
  expect(undoButton()).toBeDisabled();
  expect(redoButton()).toBeEnabled();

  fireEvent.click(redoButton());
  dialog = screen.getByRole('dialog', { name: 'Reapply ranking' });
  expect(dialog).toHaveTextContent('applied again');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
  expect(redoButton()).toBeEnabled();

  fireEvent.click(redoButton());
  await applyRanking();
  expect(state.posts[2]).toMatchObject({
    action: 'REDO',
    expected_order: [11, 12, 13, 14],
    new_order: [12, 11, 13, 14],
  });
  await waitFor(() => expect(listedPns()).toEqual(reordered));
  expect(undoButton()).toBeEnabled();
  expect(redoButton()).toBeDisabled();
});

test('Undo restores a removed entry, shown as `Not listed → #n`', async () => {
  await renderPriority();
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove B-200 from Hot list' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove entry' }));
  await waitFor(() => expect(listedPns()).toEqual(['A-100', 'C-300', 'D-400']));

  fireEvent.click(undoButton());
  const dialog = screen.getByRole('dialog', {
    name: 'Restore previous ranking',
  });
  const [current, proposed] = Array.from(
    dialog.querySelectorAll('.pr-snapshot'),
  );
  const absent = current.querySelector('.pr-snaprow.absent');
  expect(absent).toHaveTextContent('Not listed');
  expect(absent).toHaveTextContent('B-200');
  const restored = Array.from(proposed.querySelectorAll('.pr-snaprow')).find(
    (row) => row.textContent?.includes('B-200'),
  );
  expect(restored?.querySelector('.prr')?.textContent).toBe('Not listed → #2');

  await applyRanking();
  expect(state.posts[1]).toMatchObject({
    action: 'UNDO',
    expected_order: [11, 13, 14],
    new_order: [11, 12, 13, 14],
  });
  await waitFor(() => expect(listedPns()).toEqual(INITIAL));
  expect(redoButton()).toBeEnabled();
});

test('an Undo that would re-add a no longer eligible demand is refused and drops the step', async () => {
  await renderPriority();
  // D-400 is fully allocated: it may leave the list, but the server
  // refuses to add it back.
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove D-400 from Hot list' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove entry' }));
  await waitFor(() => expect(listedPns()).toEqual(['A-100', 'B-200', 'C-300']));

  fireEvent.click(undoButton());
  await applyRanking();
  expect(
    await screen.findByText(
      'D-400 is fully allocated. This step was removed from the history.',
    ),
  ).toBeInTheDocument();
  expect(listedPns()).toEqual(['A-100', 'B-200', 'C-300']);
  expect(undoButton()).toBeDisabled();
  expect(redoButton()).toBeDisabled();
});

test('a new confirmed change clears Redo', async () => {
  await renderPriority();
  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  fireEvent.click(undoButton());
  await applyRanking();
  await waitFor(() => expect(redoButton()).toBeEnabled());

  fireEvent.click(screen.getByRole('button', { name: 'Move D-400 up' }));
  await applyRanking();
  await waitFor(() =>
    expect(listedPns()).toEqual(['A-100', 'B-200', 'D-400', 'C-300']),
  );
  expect(redoButton()).toBeDisabled();
  expect(undoButton()).toBeEnabled();
});

test('a change made elsewhere: the stale refusal replaces the list, keeps the history, and Undo re-bases on it', async () => {
  await renderPriority();
  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  await waitFor(() =>
    expect(listedPns()).toEqual(['B-200', 'A-100', 'C-300', 'D-400']),
  );

  // Elsewhere: D-400 leaves the list and C-300 moves to the top.
  setServerOrder([13, 12, 11]);

  fireEvent.click(undoButton());
  await applyRanking();
  expect(state.posts[1]).toMatchObject({
    action: 'UNDO',
    expected_order: [12, 11, 13, 14],
    new_order: [11, 12, 13, 14],
  });
  // Refused as stale: the current list is shown with the warning, and
  // nothing was written — both histories stay.
  expect(await screen.findByText(STALE)).toBeInTheDocument();
  expect(listedPns()).toEqual(['C-300', 'B-200', 'A-100']);
  expect(undoButton()).toBeEnabled();

  // Undo again: the SAME step re-based on the new list (A-100 back to
  // the top), confirmed against the real current order.
  fireEvent.click(undoButton());
  const dialog = screen.getByRole('dialog', {
    name: 'Restore previous ranking',
  });
  expect(
    dialog.querySelectorAll('.pr-snapshot')[0].querySelector('.prpn')
      ?.textContent,
  ).toBe('C-300');
  await applyRanking();
  expect(state.posts[2]).toMatchObject({
    action: 'UNDO',
    expected_order: [13, 12, 11],
    new_order: [11, 13, 12],
  });
  await waitFor(() => expect(listedPns()).toEqual(['A-100', 'C-300', 'B-200']));
  expect(undoButton()).toBeDisabled();
  expect(redoButton()).toBeEnabled();
});

test('a step that no longer applies is dropped with a notice and nothing is sent', async () => {
  await renderPriority();
  fireEvent.click(screen.getByRole('button', { name: '+ Add to Hot list' }));
  fireEvent.click(await screen.findByRole('button', { name: /WO 007010/ }));
  await waitFor(() => expect(listedPns()).toEqual([...INITIAL, 'E-500']));

  // Elsewhere: E-500 leaves the Hot list. The next change is refused as
  // stale and shows the list without it.
  setServerOrder([11, 12, 13, 14]);
  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  expect(await screen.findByText(STALE)).toBeInTheDocument();
  expect(listedPns()).toEqual(INITIAL);
  const posts = state.posts.length;

  // Undo of the ADD would remove E-500 — it is no longer listed.
  fireEvent.click(undoButton());
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(
    screen.getByText(
      'E-500 · WO 007010 is no longer on the Hot list (removed elsewhere, or automatically once its line was fully allocated), so this step was removed from the history.',
    ),
  ).toBeInTheDocument();
  expect(state.posts).toHaveLength(posts);
  expect(undoButton()).toBeDisabled();
});

test('an Undo refused because the demand is gone drops that step', async () => {
  await renderPriority();
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove B-200 from Hot list' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove entry' }));
  await waitFor(() => expect(listedPns()).toEqual(['A-100', 'C-300', 'D-400']));

  // Elsewhere: the demand line itself is deleted.
  state.demands = state.demands.filter((d) => d.id !== 12);

  fireEvent.click(undoButton());
  await applyRanking();
  expect(state.posts[1]).toMatchObject({
    action: 'UNDO',
    expected_order: [11, 13, 14],
    new_order: [11, 12, 13, 14],
  });
  expect(
    await screen.findByText(
      `${MISSING} This step was removed from the history.`,
    ),
  ).toBeInTheDocument();
  expect(listedPns()).toEqual(['A-100', 'C-300', 'D-400']);
  expect(undoButton()).toBeDisabled();
  expect(redoButton()).toBeDisabled();
});

/* ============ Unknown outcome ============ */

test('a 5xx leaves the outcome unknown; Retry resends the same key and the replay completes the step', async () => {
  await renderPriority();
  state.nextPost = { kind: 'status-after-commit', status: 502 };

  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  const pending = await screen.findByText(/may already have been applied/);
  expect(pending).not.toHaveTextContent('Nothing was changed');
  // Focus lands on the one useful next step.
  expect(
    screen.getByRole('button', { name: 'Retry the same change' }),
  ).toHaveFocus();
  // Every write is frozen while the outcome is unknown.
  expect(
    screen.getByRole('button', { name: 'Move B-200 down' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: '+ Add to Hot list' }),
  ).toBeDisabled();
  expect(undoButton()).toBeDisabled();
  expect(listedPns()).toEqual(INITIAL);

  fireEvent.click(
    screen.getByRole('button', { name: 'Retry the same change' }),
  );
  await waitFor(() =>
    expect(listedPns()).toEqual(['B-200', 'A-100', 'C-300', 'D-400']),
  );
  expect(state.posts).toHaveLength(2);
  expect(state.posts[1]).toEqual(state.posts[0]);
  expect(
    await screen.findByText(
      /Hot ranking updated — it had already been applied/,
    ),
  ).toBeInTheDocument();
  // The step was recorded exactly as for a normal success.
  expect(undoButton()).toBeEnabled();
  expect(screen.queryByText(/may already have been applied/)).toBeNull();
});

test('a network failure leaves the outcome unknown; Retry with the same key applies it once', async () => {
  await renderPriority();
  state.nextPost = { kind: 'drop-before' };

  fireEvent.click(
    screen.getByRole('button', { name: 'Remove C-300 from Hot list' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove entry' }));
  await screen.findByText(/may already have been applied/);
  expect(listedPns()).toEqual(INITIAL);

  fireEvent.click(
    screen.getByRole('button', { name: 'Retry the same change' }),
  );
  await waitFor(() => expect(listedPns()).toEqual(['A-100', 'B-200', 'D-400']));
  expect(state.posts[1].device_event_id).toBe(state.posts[0].device_event_id);
  expect(undoButton()).toBeEnabled();
});

test('Reload list abandons the unknown submission and re-reads the list', async () => {
  await renderPriority();
  state.nextPost = { kind: 'drop-after-commit' };

  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  await screen.findByText(/may already have been applied/);
  const reads = state.reads.filter((read) => read === '/api/hot-list').length;

  fireEvent.click(screen.getByRole('button', { name: 'Reload list' }));
  await waitFor(() =>
    expect(listedPns()).toEqual(['B-200', 'A-100', 'C-300', 'D-400']),
  );
  expect(state.reads.filter((read) => read === '/api/hot-list')).toHaveLength(
    reads + 1,
  );
  expect(
    screen.getByText(
      'The change may already have been applied. The list was reloaded — check it before making another change.',
    ),
  ).toBeInTheDocument();
  // Abandoned: no Retry, writes available again, the history unchanged.
  expect(
    screen.queryByRole('button', { name: 'Retry the same change' }),
  ).toBeNull();
  expect(screen.getByRole('button', { name: 'Move B-200 down' })).toBeEnabled();
  expect(undoButton()).toBeDisabled();
  expect(state.posts).toHaveLength(1);
});

test('Reload list keeps writes frozen until the fresh list arrives', async () => {
  await renderPriority();
  state.nextPost = { kind: 'drop-before' };
  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  await screen.findByText(/may already have been applied/);

  let release!: () => void;
  state.holdRead = {
    path: '/api/hot-list',
    until: new Promise<void>((resolve) => {
      release = resolve;
    }),
  };
  fireEvent.click(screen.getByRole('button', { name: 'Reload list' }));
  expect(
    await screen.findByText('Reloading the Hot list…'),
  ).toBeInTheDocument();
  // The old list stays on screen, but nothing can be changed against it.
  expect(listedPns()).toEqual(INITIAL);
  expect(
    screen.getByRole('button', { name: 'Move B-200 down' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: '+ Add to Hot list' }),
  ).toBeDisabled();
  expect(screen.queryByText(/The list was reloaded/)).toBeNull();

  release();
  expect(
    await screen.findByText(
      'The change may already have been applied. The list was reloaded — check it before making another change.',
    ),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Move B-200 down' }),
    ).toBeEnabled(),
  );
  expect(screen.queryByText('Reloading the Hot list…')).toBeNull();
  expect(state.posts).toHaveLength(1);
});

test('Reload list is disabled while disconnected, so the same-key Retry survives', async () => {
  await renderPriority();
  state.nextPost = { kind: 'drop-before' };
  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  await screen.findByText(/may already have been applied/);

  state.healthDown = true;
  act(() => {
    window.dispatchEvent(new Event('offline'));
  });
  expect(screen.getByRole('button', { name: 'Reload list' })).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Retry the same change' }),
  ).toBeDisabled();
  expect(listedPns()).toEqual(INITIAL);

  state.healthDown = false;
  await act(async () => {
    window.dispatchEvent(new Event('online'));
  });
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Retry the same change' }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    screen.getByRole('button', { name: 'Retry the same change' }),
  );
  await waitFor(() =>
    expect(listedPns()).toEqual(['B-200', 'A-100', 'C-300', 'D-400']),
  );
  expect(state.posts[1].device_event_id).toBe(state.posts[0].device_event_id);
});

test('a replay that comes without the list still completes the step and re-reads the list', async () => {
  await renderPriority();
  state.nextPost = { kind: 'status-after-commit', status: 502 };
  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  await screen.findByText(/may already have been applied/);
  const reads = state.reads.filter((read) => read === '/api/hot-list').length;

  // Meanwhile a second Department was activated.
  state.departmentRefusal = {
    status: 409,
    detail:
      'Several active Departments exist (Machining, Assembly). The Hot list is managed within one Department, so nothing can be shown or changed until exactly one Department is active.',
  };
  fireEvent.click(
    screen.getByRole('button', { name: 'Retry the same change' }),
  );
  expect(
    await screen.findByText('The Hot list could not be loaded.'),
  ).toBeInTheDocument();
  expect(state.posts).toHaveLength(2);
  expect(state.posts[1]).toEqual(state.posts[0]);
  expect(state.reads.filter((read) => read === '/api/hot-list')).toHaveLength(
    reads + 1,
  );

  // Once the configuration is fixed, the committed step is in the history.
  state.departmentRefusal = null;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() =>
    expect(listedPns()).toEqual(['B-200', 'A-100', 'C-300', 'D-400']),
  );
  expect(undoButton()).toBeEnabled();
});

/* ============ Write blocks ============ */

test('disconnected: the list stays readable and every write control is disabled', async () => {
  state.healthDown = true;
  render(
    <ConnectivityProvider>
      <PriorityView />
    </ConnectivityProvider>,
    { wrapper: SignedIn },
  );
  await waitFor(() => expect(listedPns()).toEqual(INITIAL));
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: '+ Add to Hot list' }),
    ).toBeDisabled(),
  );
  expect(
    screen.getByRole('button', { name: 'Move A-100 down' }),
  ).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Remove A-100 from Hot list' }),
  ).toBeDisabled();
  expect(undoButton()).toBeDisabled();
  expect(document.querySelector('.pr-item')).toHaveAttribute(
    'draggable',
    'false',
  );
  expect(state.posts).toEqual([]);
});

/* ============ Snapshot alignment and impact/action block (GUI v14) ============ */

test('the snapshot position track sizes from content — no wide fixed label column', async () => {
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'priority.css'),
    'utf8',
  );
  // The position/rank track is content-sized (max-content) and SHARED
  // by both sections: `.pr-snapwrap` owns one grid and the section →
  // list → row subgrid chain joins its tracks, so the PN column sits
  // at the same offset in Current Position and New Position. The
  // former fixed 150px track that opened a large gap between the
  // position value and the PN is gone, and no per-section `.trans`
  // override exists (the only other definition is the narrow-screen
  // stacking fallback, which outranks the subgrid chain).
  expect(css).toMatch(
    /\.pr-snaplist \{[^}]*grid-template-columns: max-content/,
  );
  expect(css).toMatch(
    /\.pr-snapwrap \{[^}]*grid-template-columns: max-content/,
  );
  expect(css).toMatch(
    /\.pr-snapwrap \.pr-snapshot \{[^}]*grid-template-columns: subgrid/,
  );
  expect(css).toMatch(
    /\.pr-snapwrap \.pr-snaplist \{[^}]*grid-template-columns: subgrid/,
  );
  expect(css).toMatch(
    /\.pr-snapwrap \.pr-snaprow \{[^}]*grid-template-columns: subgrid/,
  );
  expect(css).not.toContain('grid-template-columns: 150px');
  expect(css).not.toMatch(/\.pr-snaprow\.trans \{[^}]*grid-template-columns/);
});

test('both snapshot sections share one wrapper grid for the common position track', async () => {
  await renderPriority();

  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  const dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  const wrap = dialog.querySelector('.pr-snapwrap');
  expect(wrap).not.toBeNull();
  // Current Position, the transition arrow, and New Position are all
  // direct children of the shared wrapper grid.
  expect(wrap?.querySelectorAll(':scope > .pr-snapshot')).toHaveLength(2);
  expect(wrap?.querySelector(':scope > .pr-transition')).not.toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
});

test('the snapshot divider spans every row and rows pin to explicit grid lines', async () => {
  await renderPriority();

  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  const dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  const lists = dialog.querySelectorAll('.pr-snaplist');
  expect(lists).toHaveLength(2);
  for (const list of Array.from(lists)) {
    // The divider is the decorative first child of each section list
    // (v15): one grid item spanning all rows for one unbroken vertical
    // rule between the shared position track and the PN column.
    const divider = list.firstElementChild as HTMLElement;
    expect(divider.className).toContain('pr-snapdivider');
    expect(divider).toHaveAttribute('aria-hidden', 'true');
    const rows = Array.from(
      list.querySelectorAll<HTMLElement>(':scope > li.pr-snaprow'),
    );
    expect(rows.length).toBeGreaterThanOrEqual(2);
    expect(divider.style.gridRow).toBe(`1 / span ${rows.length}`);
    rows.forEach((row, index) => {
      expect(row.style.gridRow).toBe(String(index + 1));
    });
  }
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
});

test('the snapshot divider enables only with subgrid and hides in the fallbacks', async () => {
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'priority.css'),
    'utf8',
  );
  // Base rule: hidden — in the no-subgrid fallback the list's tracks
  // collapse, so a track-attached divider would land at the wrong
  // offset.
  expect(css).toMatch(/^\.pr-snapdivider \{\s*display: none;\s*}/m);
  // Enabled only inside the subgrid chain: attached to the PN track's
  // start edge, pulled into the column gutter, drawn as a border.
  expect(css).toMatch(/\.pr-snapwrap \.pr-snapdivider \{[^}]*display: block/);
  expect(css).toMatch(/\.pr-snapwrap \.pr-snapdivider \{[^}]*grid-column: 2/);
  expect(css).toMatch(
    /\.pr-snapwrap \.pr-snapdivider \{[^}]*justify-self: start/,
  );
  expect(css).toMatch(/\.pr-snapwrap \.pr-snapdivider \{[^}]*border-left/);
  // The narrow-screen stacking fallback hides it again (no shared
  // position track exists in one-column rows).
  expect(css).toMatch(
    /\.pr-snapdivider,\s*\.pr-snapwrap \.pr-snapdivider \{\s*display: none/,
  );
});

test('the impact/action block separates the Action label from its emphasized value', async () => {
  await renderPriority();

  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  const dialog = screen.getByRole('dialog', {
    name: 'Confirm Hot ranking change',
  });
  const impact = dialog.querySelector('.pr-impact');
  expect(impact?.querySelector('.pr-shifts')?.textContent).toBe(
    '1 other demand will shift up.',
  );
  expect(impact?.querySelector('.pr-actionlbl')?.textContent).toBe('Action');
  expect(impact?.querySelector('.pr-actionval')?.textContent).toBe('Move Down');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));
});

/* ============ The Due Soon warning policy (S9) ============ */

/** ISO date `days` from today (local calendar). */
function isoDateIn(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

test('PR-1: a demand’s due tone follows the served Due Soon policy', async () => {
  // Due in 6 days with a 40-day lead: 15 % → a 6-day window.
  const target = state.demands.find((d) => d.pn === 'A-100')!;
  target.due = isoDateIn(6);
  target.received = isoDateIn(-34);
  const view = render(
    <ConnectivityProvider>
      <PriorityView />
    </ConnectivityProvider>,
    { wrapper: SignedIn },
  );
  await screen.findByText('A-100');
  const tone = () => rowOf('A-100').querySelector('.d2')?.className ?? '';
  expect(tone()).toContain('soon');
  view.unmount();

  state.policy = policyWire(2, 15, 5);
  render(
    <ConnectivityProvider>
      <PriorityView />
    </ConnectivityProvider>,
    { wrapper: SignedIn },
  );
  await screen.findByText('A-100');
  expect(tone()).toContain('ok');
  expect(tone()).not.toContain('soon');
});

test('PR-1: a failed policy read is the view’s error state; Retry recovers', async () => {
  state.failRead = '/api/policies/due-soon';
  render(
    <ConnectivityProvider>
      <PriorityView />
    </ConnectivityProvider>,
    { wrapper: SignedIn },
  );
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(
    'The Due Soon warning settings could not be loaded.',
  );
  expect(alert).toHaveTextContent('Service unavailable.');
  expect(document.querySelector('.pr-item')).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('A-100')).toBeInTheDocument();
  expect(
    screen.queryByText('The Due Soon warning settings could not be loaded.'),
  ).toBeNull();
});

/* ============ Phase 14 slice 3 — priority permissions and retries ============ */

const A1 =
  'You are not signed in, or your sign-in has ended. Sign in to continue.';
const A2 = 'Your account does not have permission to do this.';
const R1 =
  'This request was already recorded by another user. Nothing more was recorded — reload to see the current state.';

async function renderPriorityAs(permissions: Permission[]) {
  session = signedInSession(permissions);
  render(
    <ConnectivityProvider>
      <PriorityView />
    </ConnectivityProvider>,
    { wrapper: SignedIn },
  );
  await waitFor(() => expect(listedPns()).toEqual(INITIAL));
}

test('FM-4: Set Work Order Demand priority alone adds and removes; the order cannot be changed', async () => {
  await renderPriorityAs(['SET_DEMAND_PRIORITY']);

  expect(
    screen.getByRole('button', { name: '+ Add to Hot list' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Remove A-100 from Hot list' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Move A-100 down' })).toBeNull();
  expect(rowOf('A-100').getAttribute('draggable')).toBe('false');
  expect(rowOf('A-100').querySelector('.grip')).toBeNull();
  expect(undoButton()).toBeInTheDocument();
  expect(redoButton()).toBeInTheDocument();
  expect(screen.queryByText(/^View only — /)).toBeNull();
});

test('FM-4: Reorder Hot items alone moves and drags; nothing is added or removed', async () => {
  await renderPriorityAs(['REORDER_HOT_ITEMS']);

  expect(
    screen.queryByRole('button', { name: '+ Add to Hot list' }),
  ).toBeNull();
  expect(
    screen.queryByRole('button', { name: 'Remove A-100 from Hot list' }),
  ).toBeNull();
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Move A-100 down' }),
    ).toBeEnabled(),
  );
  expect(rowOf('A-100').getAttribute('draggable')).toBe('true');
  expect(undoButton()).toBeInTheDocument();
  expect(redoButton()).toBeInTheDocument();
});

test('FM-4: without either priority permission the Hot list only reads — no Undo or Redo, one view-only note', async () => {
  await renderPriorityAs(['VIEW_PRODUCTION_DATA']);

  expect(
    screen.getByText(
      'View only — changing this needs one of these permissions: Set Work Order Demand priority, Reorder Hot items.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '+ Add to Hot list' }),
  ).toBeNull();
  expect(screen.queryByRole('button', { name: /^Move / })).toBeNull();
  expect(screen.queryByRole('button', { name: /^Remove / })).toBeNull();
  expect(screen.queryByRole('button', { name: '⟲ Undo' })).toBeNull();
  expect(screen.queryByRole('button', { name: '⟳ Redo' })).toBeNull();
});

test('FM-4: an Undo the server refuses for a missing permission is an ordinary refusal', async () => {
  await renderPriority();
  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  await waitFor(() => expect(undoButton()).toBeEnabled());

  state.nextPost = {
    kind: 'refuse',
    status: 403,
    body: {
      detail: A2,
      permission_denied: true,
      required_permissions: ['REORDER_HOT_ITEMS'],
    },
  };
  fireEvent.click(undoButton());
  await applyRanking();
  expect(await screen.findByText(A2)).toBeInTheDocument();
  expect(screen.queryByText(/may already have been applied/)).toBeNull();
  // Nothing was written: the step is still there to try again.
  expect(undoButton()).toBeEnabled();
});

async function leaveOutcomeUnknown() {
  await renderPriority();
  state.nextPost = { kind: 'status-after-commit', status: 502 };
  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  await screen.findByText(/may already have been applied/);
}

test('FM-6: a Retry refused because the sign-in ended keeps the unknown outcome; the next Retry sends the same key', async () => {
  await leaveOutcomeUnknown();
  state.nextPost = {
    kind: 'refuse',
    status: 401,
    body: { detail: A1, authentication_required: true },
  };
  fireEvent.click(
    screen.getByRole('button', { name: 'Retry the same change' }),
  );
  expect(
    await screen.findByText(
      `${A1} Sign in again, then use Retry. The change is applied only once.`,
    ),
  ).toBeInTheDocument();
  expect(screen.getByText(/may already have been applied/)).toBeInTheDocument();
  expect(screen.queryByText(/Nothing was (recorded|changed)/)).toBeNull();

  fireEvent.click(
    screen.getByRole('button', { name: 'Retry the same change' }),
  );
  await waitFor(() =>
    expect(listedPns()).toEqual(['B-200', 'A-100', 'C-300', 'D-400']),
  );
  expect(state.posts).toHaveLength(3);
  expect(state.posts[2]).toEqual(state.posts[0]);
  expect(screen.queryByText(/may already have been applied/)).toBeNull();
});

test('FM-6: a Retry refused for a missing permission keeps the unknown outcome and says the change may already be applied', async () => {
  await leaveOutcomeUnknown();
  state.nextPost = {
    kind: 'refuse',
    status: 403,
    body: {
      detail: A2,
      permission_denied: true,
      required_permissions: ['REORDER_HOT_ITEMS'],
    },
  };
  fireEvent.click(
    screen.getByRole('button', { name: 'Retry the same change' }),
  );
  expect(
    await screen.findByText(
      `${A2} The change may already be applied; reload the list to check.`,
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Retry the same change' }),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Sign in again/)).toBeNull();
  expect(screen.queryByText(/Nothing was (recorded|changed)/)).toBeNull();
});

test('FM-6: a fresh change refused for a missing permission is an ordinary refusal', async () => {
  await renderPriority();
  state.nextPost = {
    kind: 'refuse',
    status: 403,
    body: {
      detail: A2,
      permission_denied: true,
      required_permissions: ['REORDER_HOT_ITEMS'],
    },
  };
  fireEvent.click(screen.getByRole('button', { name: 'Move A-100 down' }));
  await applyRanking();
  expect(await screen.findByText(A2)).toBeInTheDocument();
  expect(screen.queryByText(/may already have been applied/)).toBeNull();
  expect(listedPns()).toEqual(INITIAL);
});

test('FM-6: a change already recorded by another user reloads the list and keeps the step in the history', async () => {
  await renderPriority();
  fireEvent.click(
    screen.getByRole('button', { name: 'Remove D-400 from Hot list' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove entry' }));
  await waitFor(() => expect(listedPns()).toEqual(['A-100', 'B-200', 'C-300']));
  const listReads = () =>
    state.reads.filter((read) => read === '/api/hot-list').length;
  const readsBefore = listReads();

  // The Undo re-adds D-400 (an insert step); another user recorded
  // this request id.
  state.nextPost = {
    kind: 'refuse',
    status: 409,
    body: { detail: R1, recorded_by_another_user: true },
  };
  fireEvent.click(undoButton());
  await applyRanking();
  expect(await screen.findByText(R1)).toBeInTheDocument();
  await waitFor(() => expect(listReads()).toBe(readsBefore + 1));
  // Never dropped like a refused insert: the step is still offered.
  expect(undoButton()).toBeEnabled();
  expect(screen.queryByText(/removed from the history/)).toBeNull();
  expect(screen.queryByText(/may already have been applied/)).toBeNull();
});

/* ============ Arrival from Tracking (Phase 14 slice 7) ============ */

const candidateReads = () =>
  state.reads.filter((read) => read.startsWith('/api/hot-list/candidates'));

function statusLine(): string | null {
  return document.querySelector('.pr-msg[role="status"]')?.textContent ?? null;
}

async function arriveFor(pn: string) {
  requestPriorityFocus(pn);
  await renderPriority();
}

/** The open Add dialog once its focus list has answered. */
async function focusDialog() {
  const dialog = await screen.findByRole('dialog', {
    name: 'Add WO Demand to Hot list',
  });
  await waitFor(() => expect(candidateReads()).toHaveLength(1));
  await waitFor(() =>
    expect(within(dialog).queryByText(/Loading eligible/)).toBeNull(),
  );
  return dialog;
}

function decodedQuery(read: string): Record<string, string> {
  return Object.fromEntries(
    new URL(read, 'http://partflow.test').searchParams.entries(),
  );
}

test('FT-6a: the PN’s Hot entries are highlighted and their ranks named; nothing else is read or sent', async () => {
  state.demands.push(demand(15, 'B-200', '007020', { rank: 5 }));
  await arriveFor('B-200');
  await waitFor(() =>
    expect(statusLine()).toBe('B-200 is on the Hot list at #2 and #5.'),
  );
  const highlighted = Array.from(
    document.querySelectorAll('.pr-item.hot-focus'),
    (row) => row.querySelector('.pn')?.textContent,
  );
  expect(highlighted).toEqual(['B-200', 'B-200']);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(candidateReads()).toEqual([]);
  expect(state.posts).toEqual([]);
  // The hand-off is one-shot.
  expect(peekPriorityFocus()).toBeNull();
});

test('FT-6a: the highlight clears when the Add dialog opens', async () => {
  await arriveFor('A-100');
  await waitFor(() =>
    expect(statusLine()).toBe('A-100 is on the Hot list at #1.'),
  );
  expect(document.querySelectorAll('.pr-item.hot-focus')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '+ Add to Hot list' }));
  expect(document.querySelectorAll('.pr-item.hot-focus')).toHaveLength(0);
});

test('FT-6b: without a Hot entry the Add dialog lists exactly the PN’s eligible demand — an exact PN read', async () => {
  // A sibling PN and a Work Order Number containing the PN are never
  // listed: the read is the exact PN, not a text search.
  state.demands.push(
    demand(25, 'F-6000', '007030'),
    demand(26, 'K-100', 'F-600-1'),
  );
  await arriveFor('F-600');
  const dialog = await focusDialog();
  const search = within(dialog).getByLabelText(
    'Search PN, WO, Job Number or scan PN barcode',
  ) as HTMLInputElement;
  expect(search.value).toBe('');
  expect(search).toHaveFocus();
  const [read] = candidateReads();
  expect(read.startsWith('/api/hot-list/candidates?')).toBe(true);
  expect(decodedQuery(read)).toEqual({ barcode: 'PF:PN:F-600' });
  expect(
    within(dialog).getByText(
      'All Work Order Demand of F-600 that can join the Hot list — choose one to add it at the bottom.',
    ),
  ).toBeInTheDocument();
  const rows = Array.from(dialog.querySelectorAll('.hotadd-item'));
  expect(rows.map((row) => row.querySelector('.hpn')?.textContent)).toEqual([
    'F-600',
    'F-600',
  ]);
  expect(state.posts).toEqual([]);
});

test('FT-6b: one eligible demand is listed, never added until its row is clicked — Enter on the empty input adds nothing', async () => {
  await arriveFor('G-700');
  const dialog = await focusDialog();
  const search = within(dialog).getByLabelText(
    'Search PN, WO, Job Number or scan PN barcode',
  );
  expect(
    within(dialog).getByRole('button', { name: /WO 007013/ }),
  ).toBeInTheDocument();
  fireEvent.keyDown(search, { key: 'Enter' });
  await act(async () => {});
  expect(state.posts).toEqual([]);
  expect(candidateReads()).toHaveLength(1);
  expect(screen.getByRole('dialog')).toBeInTheDocument();

  fireEvent.click(within(dialog).getByRole('button', { name: /WO 007013/ }));
  await waitFor(() => expect(listedPns()).toEqual([...INITIAL, 'G-700']));
  expect(state.posts).toHaveLength(1);
  expect(state.posts[0]).toMatchObject({
    action: 'ADD',
    new_order: [11, 12, 13, 14, 24],
  });
});

test('FT-6b: a PN that itself starts with PF: lists its own demand, never the shorter PN’s', async () => {
  state.demands.push(
    demand(27, 'PF:PN:X', '007040'),
    demand(28, 'X', '007041'),
  );
  await arriveFor('PF:PN:X');
  const dialog = await focusDialog();
  expect(decodedQuery(candidateReads()[0])).toEqual({
    barcode: 'PF:PN:PF:PN:X',
  });
  expect(
    within(dialog).getByRole('button', { name: /WO 007040/ }),
  ).toBeInTheDocument();
  expect(
    within(dialog).queryByRole('button', { name: /WO 007041/ }),
  ).toBeNull();
});

test('FT-6b: a PN with no eligible demand says so and adds nothing', async () => {
  state.demands.push(demand(29, 'J-900', '007050', { completed: true }));
  await arriveFor('J-900');
  const dialog = await focusDialog();
  expect(
    within(dialog).getByText(
      'No Work Order Demand of J-900 can join the Hot list.',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).queryByText(/choose one to add it/)).toBeNull();
  expect(state.posts).toEqual([]);
});

test('FT-6b: typing searches as before; clearing the input lists the PN again', async () => {
  await arriveFor('F-600');
  const dialog = await focusDialog();
  const search = within(dialog).getByLabelText(
    'Search PN, WO, Job Number or scan PN barcode',
  );
  fireEvent.change(search, { target: { value: '18190' } });
  await waitFor(() =>
    expect(state.reads).toContain('/api/hot-list/candidates?search=18190'),
  );
  await within(dialog).findByRole('button', { name: /WO 007010/ });
  fireEvent.change(search, { target: { value: '' } });
  await waitFor(() => expect(candidateReads()).toHaveLength(3));
  expect(decodedQuery(candidateReads()[2])).toEqual({
    barcode: 'PF:PN:F-600',
  });
  await waitFor(() =>
    expect(
      within(dialog).queryByRole('button', { name: /WO 007010/ }),
    ).toBeNull(),
  );
  expect(dialog.querySelectorAll('.hotadd-item')).toHaveLength(2);
});

test('FT-6b: closing the arrival’s Add dialog returns focus to + Add to Hot list', async () => {
  await arriveFor('F-600');
  let dialog = await focusDialog();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(
    screen.getByRole('button', { name: '+ Add to Hot list' }),
  ).toHaveFocus();

  cleanup();
  state.reads = [];
  await arriveFor('F-600');
  dialog = await focusDialog();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(
    screen.getByRole('button', { name: '+ Add to Hot list' }),
  ).toHaveFocus();
  expect(state.posts).toEqual([]);
});

test('FT-6c: a user who may only reorder is told the PN is not on the Hot list; no dialog', async () => {
  requestPriorityFocus('F-600');
  await renderPriorityAs(['REORDER_HOT_ITEMS']);
  await waitFor(() =>
    expect(statusLine()).toBe(
      'F-600 is not on the Hot list. Your account can reorder the Hot list but not add to it.',
    ),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(candidateReads()).toEqual([]);
});

test('FT-6d: the arrival is one-shot — leaving and re-entering Priority shows nothing', async () => {
  await arriveFor('A-100');
  await waitFor(() =>
    expect(statusLine()).toBe('A-100 is on the Hot list at #1.'),
  );
  cleanup();
  await renderPriority();
  expect(statusLine()).toBeNull();
  expect(document.querySelectorAll('.pr-item.hot-focus')).toHaveLength(0);
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('FT-6e: under StrictMode the arrival still applies once', async () => {
  requestPriorityFocus('A-100');
  render(
    <StrictMode>
      <ConnectivityProvider>
        <PriorityView />
      </ConnectivityProvider>
    </StrictMode>,
    { wrapper: SignedIn },
  );
  await waitFor(() =>
    expect(statusLine()).toBe('A-100 is on the Hot list at #1.'),
  );
  expect(
    Array.from(
      document.querySelectorAll('.pr-item.hot-focus'),
      (row) => row.querySelector('.pn')?.textContent,
    ),
  ).toEqual(['A-100']);
  expect(peekPriorityFocus()).toBeNull();
});
