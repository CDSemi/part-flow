import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import type { ReactNode } from 'react';

import { PERMISSIONS } from '../../api/roles';
import type { Permission } from '../../api/roles';
import { SessionContext, hasPermission } from '../../app/session-context';
import type { SessionValue } from '../../app/session-context';
import { ConnectivityContext } from '../../app/connectivity-context';
import { PlannedRoutesView } from './PlannedRoutesView';

// Management → Planned Routes (GUI_DESIGN §13): a REAL view since Phase
// 13, exercised here against an in-memory fake of the
// `/api/route-templates` surface (plus the Areas, Operations and
// Machines listings) with the routes, bodies and status codes of the
// backend contract. Editing a route affects future assignments only,
// a used route archives instead of deleting, archived routes stay
// visible but are never offered for release, and a stored step value
// that is no longer offered stays visible as `(unavailable)` until it
// is chosen again.

type RouteKey =
  | 'GET list'
  | 'GET usage'
  | 'POST'
  | 'PUT'
  | 'ARCHIVE'
  | 'DELETE'
  | 'GET areas'
  | 'GET operations'
  | 'GET machines';

type Failure = { status: number; detail: string } | 'network';

interface Write {
  method: string;
  url: string;
  body?: Record<string, unknown>;
}

interface StepSeed {
  area: number;
  op: number | null;
  d?: string;
  m?: number;
  i?: string;
}

interface StepWire {
  id: number;
  sequence: number;
  area_id: number;
  operation_id: number | null;
  expected_duration: string | null;
  preferred_machine_id: number | null;
  instructions: string | null;
}

interface TemplateWire {
  id: number;
  name: string;
  description: string | null;
  archived_at: string | null;
  archived_on: string | null;
  created_at: string;
  updated_at: string;
  updated_on: string;
  ever_used: boolean;
  usage_count: number;
  steps: StepWire[];
}

const T0 = '2026-07-24T08:00:00.000000+00:00';
const TODAY = '2026-10-06';

let templates: TemplateWire[];
let usages: Record<number, unknown>;
let calls: string[];
let writes: Write[];
let failures: Partial<Record<RouteKey, Failure>>;
let holds: Partial<Record<RouteKey, Promise<void>>>;
let nextId: number;

function steps(templateId: number, seeds: StepSeed[]): StepWire[] {
  return seeds.map((s, i) => ({
    id: templateId * 100 + i,
    sequence: i + 1,
    area_id: s.area,
    operation_id: s.op,
    expected_duration: s.d ?? null,
    preferred_machine_id: s.m ?? null,
    instructions: s.i ?? null,
  }));
}

function template(
  id: number,
  name: string,
  seeds: StepSeed[],
  extra: Partial<TemplateWire> = {},
): TemplateWire {
  return {
    id,
    name,
    description: null,
    archived_at: null,
    archived_on: null,
    created_at: T0,
    updated_at: T0,
    updated_on: '2026-07-24',
    ever_used: false,
    usage_count: 0,
    steps: steps(id, seeds),
    ...extra,
  };
}

function area(
  id: number,
  name: string,
  color: string,
  extra: { terminal?: boolean; active?: boolean } = {},
) {
  return {
    id,
    department_id: 1,
    name,
    barcode_value: `PF:AREA:${id}`,
    description: null,
    color,
    icon_url: null,
    is_terminal: extra.terminal ?? false,
    is_active: extra.active ?? true,
    worker_identification_mode: 'DISABLED',
    fixed_worker_id: null,
    worker_session_timeout_minutes: null,
  };
}

function operation(
  id: number,
  areaId: number,
  code: string,
  name: string | null,
  active = true,
) {
  return {
    id,
    area_id: areaId,
    code,
    name,
    description: null,
    default_expected_duration: null,
    is_external: false,
    is_active: active,
  };
}

function machine(
  id: number,
  areaId: number,
  name: string,
  retiredOn: string | null = null,
) {
  return {
    id,
    area_id: areaId,
    name,
    asset_tag: `CD-${id}`,
    barcode_value: `PF:MACHINE:CD-${id}`,
    description: null,
    manufacturer: null,
    model: null,
    serial_number: null,
    installed_on: null,
    notes: null,
    maintenance_since: null,
    maintenance_note: null,
    maintenance_expected_return: null,
    state_changed_at: T0,
    retired_on: retiredOn,
    operational_state: 'IDLE',
    assigned_quantity: 0,
    assigned_lines: [],
  };
}

// The terminal Stockroom is listed FIRST: a new route still starts in
// the first active NON-terminal Area.
const AREAS = [
  area(4, 'Stockroom', '#2e7d32', { terminal: true }),
  area(1, 'Material', '#8d6e63'),
  area(2, 'Lathe', '#1565c0'),
  area(3, 'Mill', '#6a1b9a'),
  area(5, 'Paint', '#c62828', { active: false }),
  area(6, 'Empty cell', '#455a64'),
];

const OPERATIONS = [
  operation(11, 1, 'RCV', 'Receiving'),
  operation(22, 2, 'BORE', null),
  operation(21, 2, 'TURN', 'Turning'),
  operation(23, 2, 'KNURL', 'Knurling', false),
  operation(31, 3, 'MILL', 'Milling'),
  operation(41, 4, 'STOCK', 'Stocking'),
  operation(51, 5, 'PAINT', 'Painting'),
];

const MACHINES = [
  machine(201, 2, 'Lathe 1'),
  machine(202, 2, 'Lathe 2', '2026-08-01'),
  machine(203, 3, 'Mill 1'),
  machine(204, 3, 'Mill 2'),
];

function seedTemplates(): TemplateWire[] {
  return [
    template(
      7,
      'Bracket std v3',
      [
        { area: 1, op: 11 },
        { area: 2, op: 21, d: 'PT4H', m: 201, i: 'Check runout' },
        { area: 4, op: 41 },
      ],
      { description: 'Standard bracket', ever_used: true, usage_count: 2 },
    ),
    template(8, 'Lathe trial', [
      { area: 1, op: 11 },
      { area: 2, op: 22, d: 'PT90M' },
    ]),
    template(
      9,
      'Legacy plating route',
      [
        { area: 1, op: 11 },
        { area: 5, op: 51, d: 'P3D' },
      ],
      {
        archived_at: '2026-06-30T10:00:00+00:00',
        archived_on: '2026-06-30',
        ever_used: true,
        usage_count: 1,
      },
    ),
    template(10, 'Stale refs route', [
      { area: 5, op: 51 },
      { area: 2, op: 23 },
      { area: 2, op: 21, m: 202 },
      { area: 2, op: 21, m: 204 },
      { area: 2, op: null },
      { area: 2, op: 21, m: 999 },
      { area: 2, op: 777 },
      { area: 99, op: 21 },
    ]),
  ];
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeKey(method: string, path: string): RouteKey | null {
  if (method === 'GET' && path === '/api/route-templates/management') {
    return 'GET list';
  }
  if (method === 'GET' && /^\/api\/route-templates\/-?\d+\/usage$/.test(path)) {
    return 'GET usage';
  }
  if (method === 'POST' && path === '/api/route-templates') return 'POST';
  if (method === 'PUT' && /^\/api\/route-templates\/-?\d+$/.test(path)) {
    return 'PUT';
  }
  if (
    method === 'POST' &&
    /^\/api\/route-templates\/-?\d+\/archive$/.test(path)
  ) {
    return 'ARCHIVE';
  }
  if (method === 'DELETE' && /^\/api\/route-templates\/-?\d+$/.test(path)) {
    return 'DELETE';
  }
  if (method === 'GET' && path === '/api/areas') return 'GET areas';
  if (method === 'GET' && path === '/api/operations') return 'GET operations';
  if (method === 'GET' && path === '/api/machines') return 'GET machines';
  return null;
}

/** Template id addressed by a `/api/route-templates/{id}…` path. */
function addressed(path: string): number {
  return Number(/^\/api\/route-templates\/(-?\d+)/.exec(path)![1]);
}

interface StepBody {
  area_id: number;
  operation_id: number | null;
  expected_duration: string | null;
  preferred_machine_id: number | null;
  instructions: string | null;
}

function applyBody(
  target: TemplateWire,
  body: { name: string; description: string | null; steps: StepBody[] },
): void {
  target.name = body.name;
  target.description = body.description;
  target.updated_on = TODAY;
  target.steps = body.steps.map((s, i) => ({
    id: nextId * 100 + i,
    sequence: i + 1,
    ...s,
  }));
  nextId += 1;
}

function listing(): TemplateWire[] {
  return [...templates].sort(
    (a, b) =>
      Number(a.archived_at !== null) - Number(b.archived_at !== null) ||
      a.name.localeCompare(b.name) ||
      a.id - b.id,
  );
}

async function handle(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const url = String(input);
  const method = init?.method ?? 'GET';
  calls.push(`${method} ${url}`);
  const body =
    typeof init?.body === 'string'
      ? (JSON.parse(init.body) as Record<string, unknown>)
      : undefined;
  if (method !== 'GET') writes.push({ method, url, body });
  const key = routeKey(method, url);
  if (key === null) return json({ detail: 'Not Found' }, 404);
  const hold = holds[key];
  if (hold) await hold;
  const failure = failures[key];
  if (failure === 'network') throw new TypeError('Failed to fetch');
  if (failure) return json({ detail: failure.detail }, failure.status);

  switch (key) {
    case 'GET list':
      return json(listing());
    case 'GET areas':
      return json(AREAS);
    case 'GET operations':
      return json(OPERATIONS);
    case 'GET machines':
      return json(MACHINES);
    case 'GET usage':
      return json(usages[addressed(url)]);
    case 'POST': {
      const created = template(nextId, '', []);
      nextId += 1;
      applyBody(created, body as unknown as Parameters<typeof applyBody>[1]);
      templates.push(created);
      return json(created, 201);
    }
    case 'PUT': {
      const target = templates.find((t) => t.id === addressed(url))!;
      applyBody(target, body as unknown as Parameters<typeof applyBody>[1]);
      return json(target);
    }
    case 'ARCHIVE': {
      const target = templates.find((t) => t.id === addressed(url))!;
      target.archived_at = `${TODAY}T09:00:00+00:00`;
      target.archived_on = TODAY;
      target.updated_on = TODAY;
      return json(target);
    }
    case 'DELETE':
      templates = templates.filter((t) => t.id !== addressed(url));
      return new Response(null, { status: 204 });
  }
}

beforeEach(() => {
  window.history.replaceState({}, '', '/management/planned-routes');
  session = signedInSession();
  templates = seedTemplates();
  usages = {
    7: {
      template_id: 7,
      total: 3,
      flows: [
        {
          quantity_flow_id: 140,
          part_number: '2027-60-8114-00',
          released_on: '2026-07-20',
        },
        {
          quantity_flow_id: 133,
          part_number: '0455-20-0118-03',
          released_on: '2026-07-11',
        },
      ],
    },
    9: {
      template_id: 9,
      total: 1,
      flows: [
        {
          quantity_flow_id: 61,
          part_number: '214-406',
          released_on: '2026-05-02',
        },
      ],
    },
  };
  calls = [];
  writes = [];
  failures = {};
  holds = {};
  nextId = 50;
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

function view(status: 'connected' | 'unavailable') {
  return (
    <SignedIn>
      <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
        <PlannedRoutesView />
      </ConnectivityContext.Provider>
    </SignedIn>
  );
}

/** Render Planned Routes with a fixed connectivity status and wait for
 * the listing. */
async function renderPlannedRoutes(
  status: 'connected' | 'unavailable' = 'connected',
) {
  const utils = render(view(status));
  await screen.findByText('Bracket std v3');
  return {
    ...utils,
    setStatus: (next: typeof status) => utils.rerender(view(next)),
  };
}

/** A controllable gate for one route of the fake server. */
function holdRoute(key: RouteKey): () => void {
  let release!: () => void;
  holds[key] = new Promise<void>((resolve) => {
    release = resolve;
  });
  return () => {
    delete holds[key];
    release();
  };
}

const listCalls = () =>
  calls.filter((c) => c === 'GET /api/route-templates/management').length;

function routeRow(name: string): HTMLElement {
  const row = Array.from(document.querySelectorAll('.rt-table tbody tr')).find(
    (tr) => tr.querySelector('.rtname')?.textContent === name,
  );
  expect(row, `row ${name}`).toBeDefined();
  return row as HTMLElement;
}

function openEdit(name: string): HTMLElement {
  fireEvent.click(
    within(routeRow(name)).getByRole('button', { name: `Edit ${name}` }),
  );
  return screen.getByRole('dialog', { name: 'Edit Planned Route' });
}

function select(dialog: HTMLElement, label: string): HTMLSelectElement {
  return within(dialog).getByLabelText(label) as HTMLSelectElement;
}

const optionTexts = (el: HTMLSelectElement) =>
  Array.from(el.options, (o) => o.textContent);

const selectedText = (el: HTMLSelectElement) =>
  el.options[el.selectedIndex]?.textContent;

async function dialogClosed() {
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
}

/* ============ List ============ */

test('active and archived routes render from the management listing', async () => {
  await renderPlannedRoutes();

  const bracket = routeRow('Bracket std v3');
  expect(bracket.className).toContain('selrow');
  expect(bracket.textContent).toContain('Standard bracket');
  const chips = Array.from(
    bracket.querySelectorAll<HTMLElement>('.rt-steps .rt-stepchip'),
  );
  expect(chips.map((el) => el.textContent)).toEqual([
    'Material',
    'Lathe',
    'Stockroom',
  ]);
  expect(chips[1].style.getPropertyValue('--acol')).toBe('#1565c0');
  expect(chips[1].title).toBe('Lathe — Turning');
  expect(bracket.querySelector('.rt-status')?.textContent).toBe('Active');
  expect(bracket.querySelector('.rt-statusdate')?.textContent).toBe(
    'updated Jul 24, 2026',
  );
  expect(
    within(bracket).getByRole('button', { name: '2 Quantity Flows…' }),
  ).toBeInTheDocument();
  expect(routeRow('Lathe trial').textContent).toContain('Never used');
  // Unknown references never break the list: the chip names the id.
  const stale = Array.from(
    routeRow('Stale refs route').querySelectorAll<HTMLElement>('.rt-stepchip'),
  );
  expect(stale[7].textContent).toBe('Area 99');
  expect(stale[4].title).toBe('Lathe — —');
  expect(stale[6].title).toBe('Lathe — Operation 777');

  const activeTable = document.querySelectorAll('.rt-table')[0];
  expect(
    Array.from(
      activeTable.querySelectorAll('thead th'),
      (th) => th.textContent,
    ),
  ).toEqual(['Planned Route', 'Steps', 'Status', 'Used by']);

  const legacy = routeRow('Legacy plating route');
  expect(legacy.closest('.rt-archived')).not.toBeNull();
  expect(legacy.className).toContain('archived');
  expect(legacy.className).not.toContain('selrow');
  expect(legacy.querySelector('.rt-statusdate')?.textContent).toBe(
    'since Jun 30, 2026',
  );
  expect(within(legacy).queryByRole('button', { name: /^Edit / })).toBeNull();
  fireEvent.click(legacy);
  expect(screen.queryByRole('dialog')).toBeNull();

  // Real data only: no development-preview notice.
  expect(document.body.textContent).not.toContain('Development preview');
  expect(calls).toEqual(
    expect.arrayContaining([
      'GET /api/route-templates/management',
      'GET /api/areas',
      'GET /api/operations',
      'GET /api/machines',
    ]),
  );
});

test('search covers name, description and Operation labels', async () => {
  await renderPlannedRoutes();
  const search = screen.getByLabelText('Search Planned Routes');

  fireEvent.change(search, { target: { value: 'bore' } });
  expect(document.querySelectorAll('.rt-table tbody tr')).toHaveLength(1);
  expect(routeRow('Lathe trial')).toBeTruthy();

  fireEvent.change(search, { target: { value: 'standard' } });
  expect(routeRow('Bracket std v3')).toBeTruthy();
  expect(document.querySelectorAll('.rt-table tbody tr')).toHaveLength(1);

  fireEvent.change(search, { target: { value: 'painting' } });
  expect(routeRow('Legacy plating route')).toBeTruthy();

  fireEvent.change(search, { target: { value: ' zzz ' } });
  expect(
    screen.getByText('No Planned Routes match “zzz”.'),
  ).toBeInTheDocument();
});

test('an empty listing says so', async () => {
  templates = [];
  render(view('connected'));
  expect(
    await screen.findByText('No Planned Routes defined yet.'),
  ).toBeInTheDocument();
});

test('loading and a failed load keep the page frame; Retry refetches', async () => {
  const release = holdRoute('GET list');
  failures['GET list'] = { status: 503, detail: 'Database unavailable.' };
  render(view('connected'));

  expect(
    screen.getByRole('status', { name: 'Loading Planned Routes' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('heading', { name: 'Planned Routes' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: '+ New Planned Route' }),
  ).toBeDisabled();
  release();

  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain(
    'Planned Route data could not be loaded.',
  );
  expect(alert.textContent).toContain('Database unavailable.');
  expect(screen.getByLabelText('Search Planned Routes')).toBeInTheDocument();

  delete failures['GET list'];
  fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('Bracket std v3')).toBeInTheDocument();
  expect(listCalls()).toBe(2);
});

/* ============ Usage ============ */

test('the usage dialog lists the newest released Quantity Flows', async () => {
  await renderPlannedRoutes();

  fireEvent.click(
    within(routeRow('Bracket std v3')).getByRole('button', {
      name: '2 Quantity Flows…',
    }),
  );
  // The usage cell is an interactive island — never the edit dialog.
  expect(
    screen.queryByRole('dialog', { name: 'Edit Planned Route' }),
  ).toBeNull();
  const dialog = screen.getByRole('dialog', {
    name: 'Usage of Bracket std v3',
  });
  expect(
    within(dialog).getByRole('status', { name: 'Loading route usage' }),
  ).toBeInTheDocument();
  expect(await within(dialog).findByText('#140')).toBeInTheDocument();
  expect(calls).toContain('GET /api/route-templates/7/usage');
  const rows = Array.from(dialog.querySelectorAll('.rt-usagelist li'), (li) =>
    Array.from(li.children, (c) => c.textContent),
  );
  expect(rows).toEqual([
    ['#140', '2027-60-8114-00', 'released Jul 20, 2026'],
    ['#133', '0455-20-0118-03', 'released Jul 11, 2026'],
  ]);
  expect(dialog.textContent).toContain(
    'Bracket std v3 was assigned to 3 released Quantity Flows. Each keeps its own route snapshot from release time.',
  );
  expect(dialog.textContent).toContain('Showing the 2 most recent.');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
  expect(screen.queryByRole('dialog')).toBeNull();

  // Archived routes show their usage too; no limit line when complete.
  fireEvent.click(
    within(routeRow('Legacy plating route')).getByRole('button', {
      name: '1 Quantity Flow…',
    }),
  );
  const archived = screen.getByRole('dialog', {
    name: 'Usage of Legacy plating route',
  });
  expect(await within(archived).findByText('#61')).toBeInTheDocument();
  expect(archived.textContent).toContain('1 released Quantity Flow.');
  expect(archived.textContent).not.toContain('most recent');
});

test('a failed usage load offers Retry', async () => {
  await renderPlannedRoutes();
  failures['GET usage'] = { status: 500, detail: 'Usage failed.' };
  fireEvent.click(
    within(routeRow('Bracket std v3')).getByRole('button', {
      name: '2 Quantity Flows…',
    }),
  );
  const dialog = screen.getByRole('dialog', {
    name: 'Usage of Bracket std v3',
  });
  const alert = await within(dialog).findByRole('alert');
  expect(alert.textContent).toContain('Route usage could not be loaded.');
  delete failures['GET usage'];
  fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
  expect(await within(dialog).findByText('#140')).toBeInTheDocument();
});

/* ============ Editor selects ============ */

test('stored values that are no longer offered stay visible as unavailable', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Stale refs route');

  // Step 1: an inactive Area — kept, never silently replaced.
  const area1 = select(dialog, 'Step 1 Area');
  expect(area1).toHaveValue('5');
  expect(optionTexts(area1)).toEqual([
    'Paint (unavailable)',
    'Stockroom',
    'Material',
    'Lathe',
    'Mill',
    'Empty cell',
  ]);
  // Step 2: an inactive Operation; the Area's ACTIVE Operations by id.
  const op2 = select(dialog, 'Step 2 Operation');
  expect(selectedText(op2)).toBe('Knurling (unavailable)');
  expect(optionTexts(op2)).toEqual([
    'Knurling (unavailable)',
    'Turning',
    'BORE',
  ]);
  // Step 3: a retired Machine; only non-retired Machines of the Area.
  const machine3 = select(dialog, 'Step 3 preferred Machine');
  expect(optionTexts(machine3)).toEqual([
    '— no preferred Machine',
    'Lathe 2 — retired (unavailable)',
    'Lathe 1',
  ]);
  expect(machine3).toHaveValue('202');
  // Step 4: a Machine that now belongs to another Area.
  expect(selectedText(select(dialog, 'Step 4 preferred Machine'))).toBe(
    'Mill 2 (unavailable)',
  );
  // Step 5: a legacy step without an Operation renders `—`.
  const op5 = select(dialog, 'Step 5 Operation');
  expect(op5).toHaveValue('');
  expect(selectedText(op5)).toBe('—');
  expect(op5.options[0].disabled).toBe(true);
  // Steps 6–8: unknown ids.
  expect(selectedText(select(dialog, 'Step 6 preferred Machine'))).toBe(
    'Machine 999 (unavailable)',
  );
  expect(selectedText(select(dialog, 'Step 7 Operation'))).toBe(
    'Operation 777 (unavailable)',
  );
  expect(selectedText(select(dialog, 'Step 8 Area'))).toBe(
    'Area 99 (unavailable)',
  );
  expect(selectedText(select(dialog, 'Step 8 Operation'))).toBe(
    'Turning (unavailable)',
  );

  // Save is blocked until every stale value is chosen again — the
  // first problem in route order is reported, nothing is sent.
  const save = within(dialog).getByRole('button', { name: 'Save route' });
  const alertText = () => within(dialog).getByRole('alert').textContent;
  fireEvent.click(save);
  expect(alertText()).toBe('Every step needs an Operation.');
  fireEvent.change(op5, { target: { value: '21' } });
  fireEvent.click(save);
  expect(alertText()).toBe('Step 1: choose an available Area.');
  fireEvent.change(area1, { target: { value: '1' } });
  expect(select(dialog, 'Step 1 Operation')).toHaveValue('11');
  fireEvent.click(save);
  expect(alertText()).toBe('Step 2: choose an available Operation.');
  fireEvent.change(op2, { target: { value: '22' } });
  fireEvent.click(save);
  expect(alertText()).toBe('Step 3: choose an available Machine.');
  fireEvent.change(machine3, { target: { value: '201' } });
  fireEvent.change(select(dialog, 'Step 4 preferred Machine'), {
    target: { value: '' },
  });
  fireEvent.change(select(dialog, 'Step 6 preferred Machine'), {
    target: { value: '' },
  });
  fireEvent.change(select(dialog, 'Step 7 Operation'), {
    target: { value: '21' },
  });
  fireEvent.click(save);
  expect(alertText()).toBe('Step 8: choose an available Area.');
  fireEvent.change(select(dialog, 'Step 8 Area'), { target: { value: '3' } });
  expect(writes).toEqual([]);

  fireEvent.click(save);
  await dialogClosed();
  expect(writes).toHaveLength(1);
  expect(writes[0].method).toBe('PUT');
  expect(writes[0].url).toBe('/api/route-templates/10');
});

test('changing a step Area keeps only what the new Area offers', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Bracket std v3');

  const op2 = select(dialog, 'Step 2 Operation');
  const machine2 = select(dialog, 'Step 2 preferred Machine');
  expect(op2).toHaveValue('21');
  expect(machine2).toHaveValue('201');
  expect(selectedText(machine2)).toBe('Lathe 1');

  fireEvent.change(select(dialog, 'Step 2 Area'), { target: { value: '3' } });
  expect(op2).toHaveValue('31');
  expect(optionTexts(op2)).toEqual(['Milling']);
  expect(machine2).toHaveValue('');
  expect(optionTexts(machine2)).toEqual([
    '— no preferred Machine',
    'Mill 1',
    'Mill 2',
  ]);

  // An Area without an active Operation leaves the step without one.
  fireEvent.change(select(dialog, 'Step 2 Area'), { target: { value: '6' } });
  expect(op2).toHaveValue('');
  expect(selectedText(op2)).toBe('Select an Operation…');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));
  expect(within(dialog).getByRole('alert').textContent).toBe(
    'Every step needs an Operation.',
  );
  expect(writes).toEqual([]);
});

test('a terminal Area never starts a route; later steps may use one', async () => {
  await renderPlannedRoutes();

  fireEvent.click(screen.getByRole('button', { name: '+ New Planned Route' }));
  const dialog = screen.getByRole('dialog', { name: 'New Planned Route' });
  // The first active NON-terminal Area, with its first Operation.
  expect(select(dialog, 'Step 1 Area')).toHaveValue('1');
  expect(select(dialog, 'Step 1 Operation')).toHaveValue('11');
  expect(select(dialog, 'Step 1 preferred Machine')).toHaveValue('');
  expect(
    within(dialog).getByRole('button', { name: 'Remove step 1' }),
  ).toBeDisabled();

  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Ship only' },
  });
  fireEvent.change(select(dialog, 'Step 1 Area'), { target: { value: '4' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create route' }));
  expect(within(dialog).getByRole('alert').textContent).toBe(
    "Step 1: Area 'Stockroom' is a terminal Area and never starts production. Choose a starting Area for the first step.",
  );
  expect(writes).toEqual([]);

  // As step 2 it is fine.
  fireEvent.click(within(dialog).getByRole('button', { name: '+ Add step' }));
  expect(select(dialog, 'Step 2 Area')).toHaveValue('4');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Move step 2 up' }),
  );
  fireEvent.change(select(dialog, 'Step 1 Area'), { target: { value: '2' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create route' }));
  await dialogClosed();
  expect(writes[0].body).toEqual({
    name: 'Ship only',
    description: null,
    steps: [
      {
        area_id: 2,
        operation_id: 21,
        expected_duration: null,
        preferred_machine_id: null,
        instructions: null,
      },
      {
        area_id: 4,
        operation_id: 41,
        expected_duration: null,
        preferred_machine_id: null,
        instructions: null,
      },
    ],
  });
});

/* ============ Est. time ============ */

test('Est. time is shown and entered in duration tokens', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Lathe trial');

  const est1 = within(dialog).getByLabelText('Step 1 expected duration');
  const est2 = within(dialog).getByLabelText('Step 2 expected duration');
  expect(est1).toHaveValue('');
  expect(est2).toHaveValue('1h 30m');

  fireEvent.change(est1, { target: { value: 'soon' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));
  expect(within(dialog).getByRole('alert').textContent).toBe(
    'Step 1: enter the estimated time like 45m, 4h or 2d 03h.',
  );
  expect(writes).toEqual([]);

  fireEvent.change(est1, { target: { value: '4h' } });
  fireEvent.change(est2, { target: { value: '' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));
  await dialogClosed();
  const sent = writes[0].body!.steps as StepBody[];
  expect(sent.map((s) => s.expected_duration)).toEqual(['PT240M', null]);
});

test('an untouched Est. time is sent verbatim', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Bracket std v3');
  expect(within(dialog).getByLabelText('Step 2 expected duration')).toHaveValue(
    '4h 00m',
  );
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Bracket std v4' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));
  await dialogClosed();
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/route-templates/7',
      body: {
        name: 'Bracket std v4',
        description: 'Standard bracket',
        steps: [
          {
            area_id: 1,
            operation_id: 11,
            expected_duration: null,
            preferred_machine_id: null,
            instructions: null,
          },
          {
            area_id: 2,
            operation_id: 21,
            expected_duration: 'PT4H',
            preferred_machine_id: 201,
            instructions: 'Check runout',
          },
          {
            area_id: 4,
            operation_id: 41,
            expected_duration: null,
            preferred_machine_id: null,
            instructions: null,
          },
        ],
      },
    },
  ]);
  // The list reloads after the completed write.
  expect(await screen.findByText('Bracket std v4')).toBeInTheDocument();
  expect(listCalls()).toBe(2);
});

/* ============ Save ============ */

test('Save without changes closes without a request', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Bracket std v3');
  // A change undone is no change.
  const name = within(dialog).getByLabelText('Route name');
  fireEvent.change(name, { target: { value: 'Bracket x' } });
  expect(dialog.textContent).toContain('● Unsaved changes');
  fireEvent.change(name, { target: { value: ' Bracket std v3 ' } });
  expect(dialog.textContent).not.toContain('● Unsaved changes');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(writes).toEqual([]);
  expect(listCalls()).toBe(1);
});

test('a refused save keeps the input and shows the server message', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Bracket std v3');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Bracket std v4' },
  });
  failures.PUT = {
    status: 409,
    detail:
      "Planned Route 'Bracket std v3' is archived and cannot be edited. Duplicate it to create an editable copy.",
  };
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));
  expect((await within(dialog).findByRole('alert')).textContent).toContain(
    'is archived and cannot be edited',
  );
  expect(within(dialog).getByLabelText('Route name')).toHaveValue(
    'Bracket std v4',
  );
  // Closing (discarding the kept input) refreshes the list.
  fireEvent.keyDown(dialog, { key: 'Escape' });
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  await dialogClosed();
  await waitFor(() => expect(listCalls()).toBe(2));
});

test('an unanswered save says the outcome is unknown', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Lathe trial');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Lathe trial 2' },
  });
  failures.PUT = 'network';
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));
  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    'The server did not answer — this change may or may not have been saved. Close this window to refresh the list, then check the route before trying again.',
  );
  expect(within(dialog).getByLabelText('Route name')).toHaveValue(
    'Lathe trial 2',
  );
  expect(writes).toHaveLength(1);
});

test('Cancel and Escape are ignored while a save is in flight', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Lathe trial');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Lathe trial 2' },
  });
  const release = holdRoute('PUT');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(
    within(dialog).getByRole('button', { name: 'Save route' }),
  ).toBeDisabled();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(
    screen.getByRole('dialog', { name: 'Edit Planned Route' }),
  ).toBeInTheDocument();
  release();
  await dialogClosed();
  expect(await screen.findByText('Lathe trial 2')).toBeInTheDocument();
});

test('a new route needs a name, then posts its steps in order', async () => {
  await renderPlannedRoutes();

  fireEvent.click(screen.getByRole('button', { name: '+ New Planned Route' }));
  const dialog = screen.getByRole('dialog', { name: 'New Planned Route' });
  // The route name is the first thing to type.
  expect(document.activeElement).toBe(
    within(dialog).getByLabelText('Route name'),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create route' }));
  expect(within(dialog).getByRole('alert').textContent).toBe(
    'A route name is required.',
  );

  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: '  Deburr path ' },
  });
  fireEvent.change(within(dialog).getByLabelText('Description (optional)'), {
    target: { value: '   ' },
  });
  // A new step continues in the last step's Area.
  fireEvent.click(within(dialog).getByRole('button', { name: '+ Add step' }));
  expect(select(dialog, 'Step 2 Area')).toHaveValue('1');
  fireEvent.change(select(dialog, 'Step 2 Area'), { target: { value: '2' } });
  fireEvent.change(select(dialog, 'Step 2 Operation'), {
    target: { value: '22' },
  });
  fireEvent.change(select(dialog, 'Step 2 preferred Machine'), {
    target: { value: '201' },
  });
  fireEvent.change(within(dialog).getAllByPlaceholderText('optional')[1], {
    target: { value: ' Light pass ' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create route' }));
  await dialogClosed();

  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/route-templates',
      body: {
        name: 'Deburr path',
        description: null,
        steps: [
          {
            area_id: 1,
            operation_id: 11,
            expected_duration: null,
            preferred_machine_id: null,
            instructions: null,
          },
          {
            area_id: 2,
            operation_id: 22,
            expected_duration: null,
            preferred_machine_id: 201,
            instructions: 'Light pass',
          },
        ],
      },
    },
  ]);
  const row = await waitFor(() => routeRow('Deburr path'));
  expect(row.textContent).toContain('Never used');
});

/* ============ Reorder ============ */

test('Move up / down reorders the steps sent', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Lathe trial');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Move step 2 up' }),
  );
  expect(select(dialog, 'Step 1 Area')).toHaveValue('2');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));
  await dialogClosed();
  const sent = writes[0].body!.steps as StepBody[];
  expect(
    sent.map((s) => [s.area_id, s.operation_id, s.expected_duration]),
  ).toEqual([
    [2, 22, 'PT90M'],
    [1, 11, null],
  ]);
});

test('drag and drop reorders the steps sent', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Lathe trial');
  const rows = dialog.querySelectorAll('.rt-steprow');
  expect(rows[0]).toHaveAttribute('draggable', 'true');
  fireEvent.dragStart(rows[1]);
  fireEvent.dragOver(rows[0]);
  fireEvent.drop(rows[0]);
  fireEvent.dragEnd(rows[1]);
  expect(select(dialog, 'Step 1 Area')).toHaveValue('2');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save route' }));
  await dialogClosed();
  const sent = writes[0].body!.steps as StepBody[];
  expect(sent.map((s) => s.area_id)).toEqual([2, 1]);
});

/* ============ Archive / Delete ============ */

test('a used route archives after saving its edits and typing the name', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Bracket std v3');
  expect(dialog.textContent).toContain(
    'Changes apply to future assignments only. The 2 Quantity Flows already released with this route keep the assigned route unchanged',
  );
  expect(within(dialog).queryByRole('button', { name: 'Delete…' })).toBeNull();
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Bracket std v4' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Archive…' }));

  const choice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  fireEvent.click(
    within(choice).getByRole('button', { name: 'Save changes, then archive' }),
  );
  const archiveDialog = await screen.findByRole('dialog', {
    name: 'Archive Planned Route',
  });
  expect(writes.map((w) => `${w.method} ${w.url}`)).toEqual([
    'PUT /api/route-templates/7',
  ]);
  expect(archiveDialog.textContent).toContain(
    'Type Bracket std v4 (route name) to confirm',
  );
  const confirm = within(archiveDialog).getByRole('button', {
    name: 'Archive route',
  });
  expect(confirm).toBeDisabled();
  fireEvent.change(within(archiveDialog).getByLabelText(/to confirm$/), {
    target: { value: '  bracket STD v4 ' },
  });
  fireEvent.click(confirm);
  await dialogClosed();
  expect(writes.map((w) => `${w.method} ${w.url}`)).toEqual([
    'PUT /api/route-templates/7',
    'POST /api/route-templates/7/archive',
  ]);
  await waitFor(() =>
    expect(routeRow('Bracket std v4').closest('.rt-archived')).not.toBeNull(),
  );
});

test('discarding the edits archives the route as saved', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Bracket std v3');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Bracket std v4' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Archive…' }));
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  const archiveDialog = screen.getByRole('dialog', {
    name: 'Archive Planned Route',
  });
  expect(archiveDialog.textContent).toContain(
    'Type Bracket std v3 (route name) to confirm',
  );
  expect(within(dialog).getByLabelText('Route name')).toHaveValue(
    'Bracket std v3',
  );
  expect(writes).toEqual([]);
});

test('a never-used route deletes; 204 and 404 both close and reload', async () => {
  await renderPlannedRoutes();
  let dialog = openEdit('Lathe trial');
  expect(within(dialog).queryByRole('button', { name: 'Archive…' })).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete…' }));
  let confirm = screen.getByRole('dialog', { name: 'Delete Planned Route' });
  expect(confirm.textContent).toContain('never been used');
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Delete route' }),
  );
  await dialogClosed();
  expect(writes.map((w) => `${w.method} ${w.url}`)).toEqual([
    'DELETE /api/route-templates/8',
  ]);
  await waitFor(() => expect(screen.queryByText('Lathe trial')).toBeNull());

  // Already gone elsewhere: a 404 is the same outcome.
  failures.DELETE = { status: 404, detail: 'Planned Route 10 does not exist.' };
  dialog = openEdit('Stale refs route');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Stale x' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete…' }));
  confirm = screen.getByRole('dialog', { name: 'Delete Planned Route' });
  expect(confirm.textContent).toContain(
    '(unsaved edits are discarded with it)',
  );
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Delete route' }),
  );
  await dialogClosed();
  await waitFor(() => expect(listCalls()).toBe(3));
});

test('a route used meanwhile cannot be deleted: Archive… replaces Delete…', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Lathe trial');
  failures.DELETE = {
    status: 409,
    detail:
      "Planned Route 'Lathe trial' has been used by released Quantity Flows, so it cannot be deleted. Archive it instead.",
  };
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete…' }));
  fireEvent.click(screen.getByRole('button', { name: 'Delete route' }));
  expect((await within(dialog).findByRole('alert')).textContent).toContain(
    'so it cannot be deleted. Archive it instead.',
  );
  expect(
    screen.queryByRole('dialog', { name: 'Delete Planned Route' }),
  ).toBeNull();
  expect(
    within(dialog).getByRole('button', { name: 'Archive…' }),
  ).toBeEnabled();
  expect(within(dialog).queryByRole('button', { name: 'Delete…' })).toBeNull();
});

test('a refused archive keeps its confirmation open without reloading', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Bracket std v3');
  failures.ARCHIVE = { status: 500, detail: 'Internal error.' };
  fireEvent.click(within(dialog).getByRole('button', { name: 'Archive…' }));
  const confirm = screen.getByRole('dialog', { name: 'Archive Planned Route' });
  fireEvent.change(within(confirm).getByLabelText(/to confirm$/), {
    target: { value: 'Bracket std v3' },
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Archive route' }),
  );
  expect((await within(confirm).findByRole('alert')).textContent).toBe(
    'Internal error.',
  );
  expect(
    screen.getByRole('dialog', { name: 'Archive Planned Route' }),
  ).toBeInTheDocument();
  expect(listCalls()).toBe(1);
  // Cancel returns to the editor.
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  expect(
    screen.queryByRole('dialog', { name: 'Archive Planned Route' }),
  ).toBeNull();
  expect(
    screen.getByRole('dialog', { name: 'Edit Planned Route' }),
  ).toBeInTheDocument();
});

test('an unanswered archive or delete says so and closing reloads the list', async () => {
  await renderPlannedRoutes();
  let dialog = openEdit('Bracket std v3');
  failures.ARCHIVE = 'network';
  fireEvent.click(within(dialog).getByRole('button', { name: 'Archive…' }));
  let confirm = screen.getByRole('dialog', { name: 'Archive Planned Route' });
  fireEvent.change(within(confirm).getByLabelText(/to confirm$/), {
    target: { value: 'Bracket std v3' },
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Archive route' }),
  );
  expect((await within(confirm).findByRole('alert')).textContent).toBe(
    'The server did not answer — the route may or may not have been archived. Close this window to refresh the list, then check the route.',
  );
  expect(
    within(confirm).getByRole('button', { name: 'Archive route' }),
  ).toBeDisabled();
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  await dialogClosed();
  await waitFor(() => expect(listCalls()).toBe(2));

  failures.DELETE = 'network';
  dialog = openEdit('Lathe trial');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete…' }));
  confirm = screen.getByRole('dialog', { name: 'Delete Planned Route' });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Delete route' }),
  );
  expect((await within(confirm).findByRole('alert')).textContent).toBe(
    'The server did not answer — the route may or may not have been deleted. Close this window to refresh the list, then check the route.',
  );
  fireEvent.keyDown(confirm, { key: 'Escape' });
  await dialogClosed();
  await waitFor(() => expect(listCalls()).toBe(3));
  // Nothing was retried automatically.
  expect(writes.map((w) => w.method)).toEqual(['POST', 'DELETE']);
});

test('Cancel and Escape are ignored while an archive or delete is in flight', async () => {
  await renderPlannedRoutes();
  let dialog = openEdit('Bracket std v3');
  let release = holdRoute('ARCHIVE');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Archive…' }));
  let confirm = screen.getByRole('dialog', { name: 'Archive Planned Route' });
  fireEvent.change(within(confirm).getByLabelText(/to confirm$/), {
    target: { value: 'Bracket std v3' },
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Archive route' }),
  );
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(
    within(confirm).getByRole('button', { name: 'Archive route' }),
  ).toBeDisabled();
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  fireEvent.keyDown(confirm, { key: 'Escape' });
  expect(
    screen.getByRole('dialog', { name: 'Archive Planned Route' }),
  ).toBeInTheDocument();
  release();
  await dialogClosed();

  dialog = openEdit('Lathe trial');
  release = holdRoute('DELETE');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete…' }));
  confirm = screen.getByRole('dialog', { name: 'Delete Planned Route' });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Delete route' }),
  );
  await waitFor(() => expect(writes).toHaveLength(2));
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  fireEvent.keyDown(confirm, { key: 'Escape' });
  expect(
    screen.getByRole('dialog', { name: 'Delete Planned Route' }),
  ).toBeInTheDocument();
  release();
  await dialogClosed();
});

/* ============ Duplicate ============ */

test('an archived route duplicates into an editable never-used variant', async () => {
  await renderPlannedRoutes();
  fireEvent.click(
    within(routeRow('Legacy plating route')).getByRole('button', {
      name: 'Duplicate',
    }),
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'Edit Planned Route',
  });
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/route-templates',
      body: {
        name: 'Legacy plating route (variant)',
        description: null,
        steps: [
          {
            area_id: 1,
            operation_id: 11,
            expected_duration: null,
            preferred_machine_id: null,
            instructions: null,
          },
          {
            area_id: 5,
            operation_id: 51,
            expected_duration: 'P3D',
            preferred_machine_id: null,
            instructions: null,
          },
        ],
      },
    },
  ]);
  expect(within(dialog).getByLabelText('Route name')).toHaveValue(
    'Legacy plating route (variant)',
  );
  // A fresh variant is never used: no future-assignments note, Delete….
  expect(dialog.textContent).not.toContain('future assignments only');
  expect(within(dialog).getByRole('button', { name: 'Delete…' })).toBeEnabled();
  await waitFor(() => routeRow('Legacy plating route (variant)'));
});

test('a refused duplicate opens New Planned Route prefilled with the copy', async () => {
  await renderPlannedRoutes();
  failures.POST = {
    status: 409,
    detail: "Step 2: Area 'Paint' is inactive. Choose an active Area.",
  };
  fireEvent.click(
    within(routeRow('Legacy plating route')).getByRole('button', {
      name: 'Duplicate',
    }),
  );
  const dialog = await screen.findByRole('dialog', {
    name: 'New Planned Route',
  });
  expect(within(dialog).getByRole('alert').textContent).toBe(
    "Step 2: Area 'Paint' is inactive. Choose an active Area.",
  );
  expect(within(dialog).getByLabelText('Route name')).toHaveValue(
    'Legacy plating route (variant)',
  );
  expect(selectedText(select(dialog, 'Step 2 Area'))).toBe(
    'Paint (unavailable)',
  );
  expect(within(dialog).getByLabelText('Step 2 expected duration')).toHaveValue(
    '3d 00h',
  );
  // Nothing was created.
  expect(screen.queryByText('Legacy plating route (variant)')).toBeNull();

  delete failures.POST;
  fireEvent.change(select(dialog, 'Step 2 Area'), { target: { value: '3' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create route' }));
  await dialogClosed();
  const created = writes[1].body!;
  expect(created.name).toBe('Legacy plating route (variant)');
  expect(created.steps).toEqual([
    {
      area_id: 1,
      operation_id: 11,
      expected_duration: null,
      preferred_machine_id: null,
      instructions: null,
    },
    {
      area_id: 3,
      operation_id: 31,
      expected_duration: 'P3D',
      preferred_machine_id: null,
      instructions: null,
    },
  ]);
  await waitFor(() => routeRow('Legacy plating route (variant)'));
});

test('Duplicate from the dialog: clean, save-then-duplicate, or the saved route', async () => {
  await renderPlannedRoutes();

  // Clean: the saved route, durations verbatim.
  let dialog = openEdit('Bracket std v3');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Duplicate' }));
  // The variant's editor replaces the original one.
  await screen.findByDisplayValue('Bracket std v3 (variant)');
  dialog = screen.getByRole('dialog', { name: 'Edit Planned Route' });
  expect(writes[0].method).toBe('POST');
  expect((writes[0].body!.steps as StepBody[])[1]).toEqual({
    area_id: 2,
    operation_id: 21,
    expected_duration: 'PT4H',
    preferred_machine_id: 201,
    instructions: 'Check runout',
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  await dialogClosed();

  // Unsaved edits → Save, then duplicate: PUT, then POST of the saved.
  writes = [];
  dialog = openEdit('Lathe trial');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Lathe experimental' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Duplicate' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save, then duplicate' }));
  await screen.findByDisplayValue('Lathe experimental (variant)');
  dialog = screen.getByRole('dialog', { name: 'Edit Planned Route' });
  expect(
    writes.map((w) => `${w.method} ${w.url} ${String(w.body?.name)}`),
  ).toEqual([
    'PUT /api/route-templates/8 Lathe experimental',
    'POST /api/route-templates Lathe experimental (variant)',
  ]);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  await dialogClosed();

  // Unsaved edits → Duplicate the saved route: POST only.
  writes = [];
  dialog = openEdit('Bracket std v3');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Bracket abandoned' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Duplicate' }));
  fireEvent.click(
    screen.getByRole('button', { name: 'Duplicate the saved route' }),
  );
  await screen.findByDisplayValue('Bracket std v3 (variant)');
  expect(writes.map((w) => `${w.method} ${String(w.body?.name)}`)).toEqual([
    'POST Bracket std v3 (variant)',
  ]);
});

test('an unanswered duplicate says the outcome is unknown', async () => {
  await renderPlannedRoutes();
  failures.POST = 'network';
  const dialog = openEdit('Bracket std v3');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Duplicate' }));
  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    'The server did not answer — this change may or may not have been saved. Close this window to refresh the list, then check the route before trying again.',
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  await dialogClosed();
  await waitFor(() => expect(listCalls()).toBe(2));

  // From an archived row: a notice, closing reloads.
  fireEvent.click(
    within(routeRow('Legacy plating route')).getByRole('button', {
      name: 'Duplicate',
    }),
  );
  const notice = await screen.findByRole('dialog', {
    name: 'Duplicate Planned Route',
  });
  expect(within(notice).getByRole('alert').textContent).toContain(
    'The server did not answer',
  );
  fireEvent.click(within(notice).getByRole('button', { name: 'Close' }));
  await dialogClosed();
  await waitFor(() => expect(listCalls()).toBe(3));
});

test('a duplicate answered by a gateway error is an unknown outcome, not a refusal', async () => {
  await renderPlannedRoutes();
  failures.POST = { status: 504, detail: 'Gateway Timeout' };
  fireEvent.click(
    within(routeRow('Legacy plating route')).getByRole('button', {
      name: 'Duplicate',
    }),
  );
  const notice = await screen.findByRole('dialog', {
    name: 'Duplicate Planned Route',
  });
  expect(within(notice).getByRole('alert').textContent).toBe(
    'The server did not answer — this change may or may not have been saved. Close this window to refresh the list, then check the route before trying again.',
  );
  // The copy may exist: no prefilled New Planned Route invites a second one.
  expect(
    screen.queryByRole('dialog', { name: 'New Planned Route' }),
  ).toBeNull();
  expect(writes).toHaveLength(1);
  fireEvent.click(within(notice).getByRole('button', { name: 'Close' }));
  await dialogClosed();
  await waitFor(() => expect(listCalls()).toBe(2));
});

test('a create answered by a server error is an unknown outcome; closing reloads', async () => {
  await renderPlannedRoutes();
  fireEvent.click(screen.getByRole('button', { name: '+ New Planned Route' }));
  const dialog = screen.getByRole('dialog', { name: 'New Planned Route' });
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Deburr path' },
  });
  failures.POST = { status: 502, detail: 'Bad Gateway' };
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create route' }));
  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    'The server did not answer — this change may or may not have been saved. Close this window to refresh the list, then check the route before trying again.',
  );
  expect(within(dialog).getByLabelText('Route name')).toHaveValue(
    'Deburr path',
  );
  expect(writes).toHaveLength(1);
  fireEvent.keyDown(dialog, { key: 'Escape' });
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  await dialogClosed();
  await waitFor(() => expect(listCalls()).toBe(2));
});

test('closing a dirty route dialog asks before discarding', async () => {
  await renderPlannedRoutes();
  const dialog = openEdit('Lathe trial');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Trial v2' },
  });
  fireEvent.keyDown(dialog, { key: 'Escape' });
  const confirm = screen.getByRole('dialog', {
    name: 'Discard unsaved route changes?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Keep editing' }),
  );
  expect(within(dialog).getByLabelText('Route name')).toHaveValue('Trial v2');

  fireEvent.keyDown(dialog, { key: 'Escape' });
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(writes).toEqual([]);
  expect(listCalls()).toBe(1);
});

/* ============ Offline write-block ============ */

test('offline disables New Planned Route and archived-row Duplicate; reading stays available', async () => {
  await renderPlannedRoutes('unavailable');

  expect(
    screen.getByRole('button', { name: '+ New Planned Route' }),
  ).toBeDisabled();
  expect(
    within(routeRow('Legacy plating route')).getByRole('button', {
      name: 'Duplicate',
    }),
  ).toBeDisabled();

  // Reading, search and usage stay available offline.
  const dialog = openEdit('Bracket std v3');
  expect(dialog).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  fireEvent.click(
    within(routeRow('Bracket std v3')).getByRole('button', {
      name: '2 Quantity Flows…',
    }),
  );
  expect(await screen.findByText('#140')).toBeInTheDocument();
  expect(writes).toEqual([]);
});

test('offline disables Save/Duplicate/Archive…/Delete… inside the route dialog', async () => {
  await renderPlannedRoutes('unavailable');

  const used = openEdit('Bracket std v3');
  // Fields stay editable.
  fireEvent.change(within(used).getByLabelText('Route name'), {
    target: { value: 'Bracket offline' },
  });
  expect(
    within(used).getByRole('button', { name: 'Save route' }),
  ).toBeDisabled();
  expect(
    within(used).getByRole('button', { name: 'Duplicate' }),
  ).toBeDisabled();
  expect(within(used).getByRole('button', { name: 'Archive…' })).toBeDisabled();
  fireEvent.keyDown(used, { key: 'Escape' });
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));

  const neverUsed = openEdit('Lathe trial');
  expect(
    within(neverUsed).getByRole('button', { name: 'Delete…' }),
  ).toBeDisabled();
  expect(writes).toEqual([]);
});

test('reconnecting re-enables the write actions', async () => {
  const { setStatus } = await renderPlannedRoutes('unavailable');
  expect(
    screen.getByRole('button', { name: '+ New Planned Route' }),
  ).toBeDisabled();
  setStatus('connected');
  expect(
    screen.getByRole('button', { name: '+ New Planned Route' }),
  ).toBeEnabled();
});

test('offline mid-flow disables the Archive typed-confirm even once the name matches — nothing archives', async () => {
  const { setStatus } = await renderPlannedRoutes('connected');

  const dialog = openEdit('Bracket std v3');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Archive…' }));
  const archiveDialog = screen.getByRole('dialog', {
    name: 'Archive Planned Route',
  });
  fireEvent.change(within(archiveDialog).getByLabelText(/to confirm$/), {
    target: { value: 'Bracket std v3' },
  });
  expect(
    within(archiveDialog).getByRole('button', { name: 'Archive route' }),
  ).toBeEnabled();

  setStatus('unavailable');
  const stillDisabled = within(
    screen.getByRole('dialog', { name: 'Archive Planned Route' }),
  ).getByRole('button', { name: 'Archive route' });
  expect(stillDisabled).toBeDisabled();
  fireEvent.click(stillDisabled);
  expect(writes).toEqual([]);
});

test('offline mid-flow disables both Save and Discard in the Duplicate unsaved-changes dialog — nothing is duplicated', async () => {
  const { setStatus } = await renderPlannedRoutes('connected');

  const dialog = openEdit('Bracket std v3');
  fireEvent.change(within(dialog).getByLabelText('Route name'), {
    target: { value: 'Bracket experimental' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Duplicate' }));
  const choice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  expect(
    within(choice).getByRole('button', { name: 'Save, then duplicate' }),
  ).toBeEnabled();
  expect(
    within(choice).getByRole('button', { name: 'Duplicate the saved route' }),
  ).toBeEnabled();

  setStatus('unavailable');
  const stillChoice = screen.getByRole('dialog', { name: 'Unsaved changes' });
  const saveThen = within(stillChoice).getByRole('button', {
    name: 'Save, then duplicate',
  });
  const savedRoute = within(stillChoice).getByRole('button', {
    name: 'Duplicate the saved route',
  });
  expect(saveThen).toBeDisabled();
  expect(savedRoute).toBeDisabled();
  fireEvent.click(saveThen);
  fireEvent.click(savedRoute);
  expect(writes).toEqual([]);
});

/* ============ ?state=long ============ */

test('?state=long adds read-only negative-id preview routes', async () => {
  window.history.replaceState({}, '', '/management/planned-routes?state=long');
  await renderPlannedRoutes();

  // Server data is still present…
  expect(routeRow('Bracket std v3')).toBeTruthy();
  // …plus the long-preview rows built from the loaded Areas.
  const supplemental = routeRow(
    'Supplemental long-preview route — multi-stage housing assembly with outside plating, secondary deburr, and final inspection rework loop',
  );
  expect(supplemental.querySelectorAll('.rt-steps .rt-stepchip')).toHaveLength(
    10,
  );
  expect(supplemental.textContent).toContain('Never used');
  expect(
    routeRow('Long preview route variant 1 — extended qualification cell'),
  ).toBeTruthy();

  const dialog = openEdit(
    'Long preview route variant 1 — extended qualification cell',
  );
  expect(
    within(dialog).getByRole('button', { name: 'Save route' }),
  ).toBeDisabled();
  expect(
    within(dialog).getByRole('button', { name: 'Duplicate' }),
  ).toBeDisabled();
  expect(
    within(dialog).getByRole('button', { name: 'Delete…' }),
  ).toBeDisabled();
  // Fields stay viewable (and editable) — only writing is blocked.
  expect(within(dialog).getByLabelText('Route name')).toBeEnabled();
  expect(writes).toEqual([]);
  expect(calls.some((c) => c.includes('/usage'))).toBe(false);
});

/* ============ Phase 14 slice 3 — without Manage Planned Routes ============ */

test('FM-4: without Manage Planned Routes nothing opens an editor — no New, no row edit, no archived Duplicate; Used by stays', async () => {
  session = signedInSession(['VIEW_PRODUCTION_DATA']);
  await renderPlannedRoutes();

  expect(
    screen.getByText(
      'View only — changing this needs the Manage Planned Routes permission.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '+ New Planned Route' }),
  ).toBeNull();
  const bracket = routeRow('Bracket std v3');
  expect(
    within(bracket).queryByRole('button', { name: 'Edit Bracket std v3' }),
  ).toBeNull();
  expect(bracket.className).not.toContain('selrow');
  fireEvent.click(bracket);
  expect(screen.queryByRole('dialog')).toBeNull();

  const legacy = routeRow('Legacy plating route');
  expect(
    within(legacy).queryByRole('button', { name: 'Duplicate' }),
  ).toBeNull();

  // The usage read stays available.
  fireEvent.click(
    within(bracket).getByRole('button', { name: '2 Quantity Flows…' }),
  );
  const usage = screen.getByRole('dialog', { name: 'Usage of Bracket std v3' });
  expect(await within(usage).findByText('#140')).toBeInTheDocument();
  expect(calls.filter((call) => !call.startsWith('GET '))).toEqual([]);
});

test('FM-4: with Manage Planned Routes no view-only note shows', async () => {
  await renderPlannedRoutes();
  expect(screen.queryByText(/^View only — /)).toBeNull();
  expect(
    screen.getByRole('button', { name: '+ New Planned Route' }),
  ).toBeInTheDocument();
});
