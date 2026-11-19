import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import type { Permission } from '../../api/roles';
import type { SessionUser } from '../../api/session';
import { ConnectivityContext } from '../../app/connectivity-context';
import { archiveViewerPath, resolvePath } from '../../app/router-core';
import { RouterProvider } from '../../app/router-provider';
import { SessionContext, hasPermission } from '../../app/session-context';
import type { SessionValue } from '../../app/session-context';
import { archivedTimestamp } from '../tracking/tracking-logic';
import { AdministrationView } from './AdministrationView';
import { formatInstant } from './archive-runs';

// The archive viewer of Administration → History archival & purge
// (archival step 5, D16; GUI_DESIGN §9): its address inside
// Administration, the key it needs, the permanent mark of archived
// history from run N, the search with every term and the server's paging
// cursor, a flow's whole archived history with its lineage in the
// modeless panel, and the loading, empty, error, waiting, closed and
// unknown-view states — every refusal as the server words it. The /api
// surface is faked in memory with the B5 contract's routes and shapes.

const OPENED = '2026-11-18T10:05:33.000000Z';
const OPEN_UNTIL = '2026-11-25T10:05:33.000000Z';
const ARCHIVE = 'run-2-20261117T092000Z';

function viewWire(extra: Record<string, unknown> = {}) {
  return {
    id: 4,
    run_id: 2,
    state: 'OPEN',
    requested_by: { user_id: 90, display_name: 'Ada Admin' },
    requested_at: '2026-11-18T10:00:00.000000Z',
    to_load: {
      archive_name: ARCHIVE,
      movements: 3,
      flows: 2,
      lineage_edges: 1,
      assigned_routes: 1,
      archive_bytes: 4096,
    },
    started_at: '2026-11-18T10:05:00.000000Z',
    opened_at: OPENED,
    open_until: OPEN_UNTIL,
    loaded: {
      movements: 3,
      flows: 2,
      lineage_edges: 1,
      assigned_routes: 1,
      names: 9,
    },
    closing_started_at: null,
    closed_by: null,
    cancelled_by: null,
    finished_at: null,
    failure_reason: null,
    ...extra,
  };
}

const NAMES = {
  areas: [
    { id: 3, name: 'MAT' },
    { id: 5, name: null },
  ],
  operations: [{ id: 7, code: 'OP-MAT', name: null }],
  machines: [{ id: 11, name: 'Lathe 1' }],
  workers: [{ id: 8, name: 'Alex' }],
};

function movement(extra: Record<string, unknown>) {
  return {
    id: 1,
    quantity_flow_id: 5512,
    part_number: 'AD-0001',
    movement_type: 'RECEIVED',
    quantity: 10,
    from_area: null,
    to_area: { id: 3, name: 'MAT' },
    operation: { id: 7, code: 'OP-MAT', name: null },
    source_machine: null,
    destination_machine: null,
    station_id: null,
    worker: null,
    occurred_at: '2023-11-01T08:00:00.000000Z',
    device_event_id: 'evt-1',
    command_sequence: 1,
    movement_reason: null,
    reason: null,
    reverses_movement_id: null,
    reversed_by_movement_id: null,
    assigned_route_step: null,
    route_deviation: null,
    lineage: [],
    demand: null,
    actor_user: null,
    ...extra,
  };
}

/** The flow released for WO-1 on its Planned Route, received by Ann. */
const RECEIVED = movement({
  id: 1,
  assigned_route_step: { id: 41, sequence: 1 },
  demand: {
    work_order_demand_id: 90,
    work_order_id: 31,
    work_order_number: 'WO-1',
    request_type: 'NEW',
  },
  actor_user: { user_id: 1, display_name: 'Ann' },
});
/** Its split into QF-5513. */
const SPLIT = movement({
  id: 2,
  movement_type: 'SPLIT',
  quantity: 4,
  occurred_at: '2023-11-02T08:00:00.000000Z',
  device_event_id: 'evt-2',
  lineage: [
    {
      parent_flow_id: 5512,
      child_flow_id: 5513,
      relation: 'SPLIT',
      void: false,
    },
  ],
});
/** QF-5513 moved to an Area the archive does not name. */
const TRANSFERRED = movement({
  id: 3,
  quantity_flow_id: 5513,
  movement_type: 'TRANSFERRED',
  quantity: 4,
  from_area: { id: 3, name: 'MAT' },
  to_area: { id: 5, name: null },
  worker: { id: 8, name: 'Alex' },
  station_id: 'MAT-ST-01',
  occurred_at: '2023-11-03T08:00:00.000000Z',
  device_event_id: 'evt-3',
});

const FLOWS: Record<number, unknown> = {
  5512: {
    id: 5512,
    part_number: 'AD-0001',
    part_number_name: 'Bracket',
    component_id: 5512,
    quantity: 6,
    status: 'SPLIT',
    route_mode: 'PLANNED',
    created_at: '2023-11-01T08:00:00.000000Z',
    closed_at: '2023-11-02T08:00:00.000000Z',
    record: { flow_status: 'SPLIT', stocked_quantity: null },
    stocked_area: null,
    scrapped_area: null,
    demand: {
      work_order_demand_id: 90,
      work_order_id: 31,
      work_order_number: 'WO-1',
      request_type: 'NEW',
    },
    work_orders: [{ work_order_id: 31, work_order_number: 'WO-1' }],
    parents: [],
    children: [
      { quantity_flow_id: 5513, relation: 'SPLIT', void: false },
      { quantity_flow_id: 5514, relation: 'SPLIT', void: true },
    ],
    route: {
      assigned_route_id: 12,
      source_route_template_id: 3,
      created_at: '2023-11-01T08:00:00.000000Z',
      steps: [
        {
          id: 41,
          sequence: 1,
          area: { id: 3, name: 'MAT' },
          operation: { id: 7, code: 'OP-MAT', name: null },
          preferred_machine: null,
          expected_duration: null,
          instructions: null,
        },
        {
          id: 42,
          sequence: 2,
          area: { id: 5, name: null },
          operation: null,
          preferred_machine: null,
          expected_duration: null,
          instructions: null,
        },
      ],
    },
    movements: [SPLIT, RECEIVED],
  },
  5513: {
    id: 5513,
    part_number: 'AD-0001',
    part_number_name: null,
    component_id: 5512,
    quantity: 4,
    status: 'STOCKED',
    route_mode: 'FLOATING',
    created_at: '2023-11-02T08:00:00.000000Z',
    closed_at: '2023-11-03T09:00:00.000000Z',
    record: { flow_status: 'STOCKED', stocked_quantity: 4 },
    stocked_area: { id: 9, name: 'STOCK' },
    scrapped_area: null,
    demand: null,
    work_orders: [{ work_order_id: 31, work_order_number: 'WO-1' }],
    parents: [{ quantity_flow_id: 5512, relation: 'SPLIT', void: false }],
    children: [],
    route: null,
    movements: [TRANSFERRED],
  },
};

type Failure = { status: number; detail: string };

let session: SessionValue;
let views: unknown[];
/** The answer of the names (the server's 404 or 409 when set). */
let namesFailure: Failure | null;
/** Answers by route key (`GET views`, `GET movements`, `GET flow`). */
let failures: Record<string, Failure>;
/** Every request, in order. */
let requests: string[];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** One page of the Movements: newest first, two per page. */
function movementsPage(url: URL) {
  const every = [TRANSFERRED, SPLIT, RECEIVED];
  const before = url.searchParams.get('before');
  const start =
    before === null ? 0 : every.findIndex((m) => m.id === Number(before)) + 1;
  const page = every.slice(start, start + 2);
  const more = start + 2 < every.length;
  return {
    movements: page,
    total: every.length,
    has_more: more,
    next_before_movement_id: more ? page[page.length - 1].id : null,
  };
}

async function handle(input: string): Promise<Response> {
  requests.push(input);
  const url = new URL(input, 'http://x');
  const path = url.pathname;
  if (path === '/api/history-archive/views') {
    const failure = failures['GET views'];
    if (failure) return json({ detail: failure.detail }, failure.status);
    return json(views);
  }
  if (path === '/api/history-archive/views/4/names') {
    if (namesFailure) {
      return json({ detail: namesFailure.detail }, namesFailure.status);
    }
    return json(NAMES);
  }
  if (path === '/api/history-archive/views/4/movements') {
    const failure = failures['GET movements'];
    if (failure) return json({ detail: failure.detail }, failure.status);
    if (url.searchParams.get('part_number') === 'NONE') {
      return json({
        movements: [],
        total: 0,
        has_more: false,
        next_before_movement_id: null,
      });
    }
    return json(movementsPage(url));
  }
  const flow = /^\/api\/history-archive\/views\/4\/flows\/(\d+)$/.exec(path);
  if (flow) {
    const failure = failures['GET flow'];
    if (failure) return json({ detail: failure.detail }, failure.status);
    const found = FLOWS[Number(flow[1])];
    if (!found) {
      return json(
        { detail: `Quantity Flow ${flow[1]} is not in view 4.` },
        404,
      );
    }
    return json(found);
  }
  if (path === '/api/policies/data-retention') {
    return json({
      retention_period_months: 24,
      shortest_retention_months: 12,
      archive_start_minute: 20,
      archive_preview_seconds: 90,
      archive_max_wait_days: 7,
      archive_purge_batch_movements: 50000,
      archive_purge_batch_part_numbers: 20,
      archive_automatic_runs: false,
      archive_measure: 'LIVE_MOVEMENT_ROWS',
      archive_limit_movement_rows: 5000000,
      archive_limit_history_months: 1,
      archive_limit_database_gib: 20,
      archive_approval_required: true,
      archive_interval_days: 7,
      archive_view_days: 7,
      archive_view_max_loaded: 1,
      standing_authorization: null,
      updated_at: '2026-11-18T08:00:00Z',
    });
  }
  if (path === '/api/history-archive/runs') return json([]);
  return json({ detail: `Unhandled fake route: ${input}` }, 500);
}

function sessionWith(permissions: Permission[]): SessionValue {
  const user: SessionUser = {
    id: 90,
    loginName: 'admin',
    displayName: 'Ada Admin',
    roleId: 1,
    roleName: 'Administrator',
    avatarUpdatedAt: null,
    permissions,
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
    openTwoStepSignIn: vi.fn(),
    openEmailAddress: vi.fn(),
    signOut: vi.fn(async () => {}),
    refresh: vi.fn(async () => {}),
  };
}

function renderViewer(path = archiveViewerPath(4)) {
  window.history.replaceState({}, '', path);
  return render(
    <RouterProvider>
      <ConnectivityContext.Provider
        value={{ status: 'connected', retry: vi.fn() }}
      >
        <SessionContext.Provider value={session}>
          <AdministrationView />
        </SessionContext.Provider>
      </ConnectivityContext.Provider>
    </RouterProvider>,
  );
}

beforeEach(() => {
  session = sessionWith(['RUN_HISTORY_ARCHIVAL_AND_PURGE']);
  views = [viewWire()];
  namesFailure = null;
  failures = {};
  requests = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => handle(String(input))),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState({}, '', '/');
  // jsdom has no scrollIntoView; VW-6 records the page's scroll with one.
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

/** The rows of the results: the time, the badges and the description. */
function resultRows(): string[][] {
  const list = document.querySelector('.admin-arv-list');
  return Array.from(list?.querySelectorAll('li') ?? [], (li) =>
    Array.from(li.children, (part) => part.textContent ?? ''),
  );
}

/** The query of the last search sent, by term. */
function lastSearch(): Record<string, string> {
  const last = requests.filter((url) => url.includes('/movements')).at(-1);
  return Object.fromEntries(new URL(last!, 'http://x').searchParams);
}

async function pageReady() {
  await screen.findByRole('heading', { name: 'Archived history of run 2' });
  await waitFor(() => expect(resultRows()).toHaveLength(2));
}

test('VW-1: the viewer has its own address under Administration', () => {
  expect(archiveViewerPath(4)).toBe('/administration/history-archival/views/4');
  expect(
    resolvePath('/administration/history-archival/views/4', 'tracking'),
  ).toEqual({ view: 'administration', archiveViewId: 4 });
  expect(
    resolvePath('/administration/history-archival/views/12/', 'tracking'),
  ).toEqual({ view: 'administration', archiveViewId: 12 });
  expect(resolvePath('/administration', 'tracking')).toEqual({
    view: 'administration',
  });
  for (const path of [
    '/administration/history-archival/views',
    '/administration/history-archival/views/0',
    '/administration/history-archival/views/x',
    '/administration/history-archival/views/4/flows',
  ]) {
    expect(resolvePath(path, 'tracking')).toEqual({ view: 'not-found', path });
  }
});

test('VW-2: the page keeps History archival & purge active, marks archived history from run N and lists its Movements, newest first, paging with the server cursor', async () => {
  renderViewer();
  await pageReady();
  const nav = screen.getByRole('navigation', {
    name: 'Administration sections',
  });
  expect(
    within(nav).getByRole('button', { name: 'History archival & purge' }),
  ).toHaveAttribute('aria-current', 'true');
  // Arriving on the page moves focus to its heading.
  expect(document.activeElement).toBe(
    screen.getByRole('heading', { name: 'Archived history of run 2' }),
  );
  expect(
    screen.getByRole('note', { name: 'Archived history' }),
  ).toHaveTextContent(
    'ArchivedArchived history from run 2 — a read-only copy, not live history',
  );
  expect(document.querySelector('.admin-arv-about')?.textContent).toBe(
    `A copy of the archive ${ARCHIVE}, loaded for viewing on ${formatInstant(OPENED)} and open until ${formatInstant(OPEN_UNTIL)}: 3 Movements in 2 flows. Nothing here can be changed, and none of it is part of the live history or of PartFlow's figures. Names read as they were when the archive was written.`,
  );
  // The first page: no term, the page size.
  expect(lastSearch()).toEqual({ limit: '50' });
  expect(resultRows()).toEqual([
    [
      archivedTimestamp('2023-11-03T08:00:00.000000Z'),
      'TRANSFERRED',
      'AD-0001 · MAT → Area 5 · qty 4 · QF-5513 · W: Alex · MAT-ST-01',
    ],
    [
      archivedTimestamp('2023-11-02T08:00:00.000000Z'),
      'SPLIT',
      'AD-0001 · QF-5512 (4) → QF-5513 · at MAT',
    ],
  ]);
  expect(
    screen.getByText('3 archived Movements in this archive.'),
  ).toHaveAttribute('role', 'status');
  const paging = document.querySelector('.tk-paging') as HTMLElement;
  expect(paging.textContent).toContain('Showing 2 of 3 Movements');

  // Show older Movements continues below the cursor the server answered.
  fireEvent.click(
    within(paging).getByRole('button', { name: 'Show older Movements' }),
  );
  await waitFor(() => expect(resultRows()).toHaveLength(3));
  expect(lastSearch()).toEqual({ before: '2', limit: '50' });
  expect(resultRows()[2]).toEqual([
    archivedTimestamp('2023-11-01T08:00:00.000000Z'),
    'RECEIVED',
    'AD-0001 · Received into MAT · qty 10 · QF-5512 · WO WO-1 release · Planned Route step 1 · by Ann',
  ]);
  expect(
    (document.querySelector('.tk-paging') as HTMLElement).textContent,
  ).toBe('Showing 3 of 3 Movements');
  // Nothing on this page can change anything: every request is a read.
  expect(
    vi
      .mocked(fetch)
      .mock.calls.every(([, init]) => (init?.method ?? 'GET') === 'GET'),
  ).toBe(true);
});

test('VW-3: the search sends every term as the server takes it, refuses a flow it cannot read, and shows refusals, empty results and Clear', async () => {
  renderViewer();
  await pageReady();
  const form = screen.getByRole('form', {
    name: 'Search the archived Movements',
  });
  // The names to search by, as they were at export.
  const options = (label: string) =>
    Array.from(
      (within(form).getByLabelText(label) as HTMLSelectElement).options,
      (option) => option.textContent,
    );
  expect(options('Area')).toEqual(['All', 'MAT', 'Area 5']);
  expect(options('Operation')).toEqual(['All', 'OP-MAT']);
  expect(options('Machine')).toEqual(['All', 'Lathe 1']);
  expect(options('Worker')).toEqual(['All', 'Alex']);

  const set = (label: string, value: string) =>
    fireEvent.change(within(form).getByLabelText(label), {
      target: { value },
    });
  set('Part Number', 'ad 0001');
  set('Work Order Number', ' WO-1 ');
  set('Quantity Flow', 'qf-5512');
  set('Area', '5');
  set('Operation', '7');
  set('Machine', '11');
  set('Worker', '8');
  set('First day', '2023-11-01');
  set('Last day', '2023-11-30');
  fireEvent.click(within(form).getByRole('button', { name: 'Search' }));
  await waitFor(() =>
    expect(lastSearch()).toEqual({
      part_number: 'ad 0001',
      work_order_number: ' WO-1 ',
      quantity_flow_id: '5512',
      area_id: '5',
      operation_id: '7',
      machine_id: '11',
      worker_id: '8',
      date_from: '2023-11-01',
      date_to: '2023-11-30',
      limit: '50',
    }),
  );
  await waitFor(() =>
    expect(
      screen.getByText('3 archived Movements match the search.'),
    ).toBeInTheDocument(),
  );

  // A flow that is not a number is refused before anything is sent.
  const sent = requests.length;
  set('Quantity Flow', 'flow five');
  fireEvent.click(within(form).getByRole('button', { name: 'Search' }));
  expect(within(form).getByRole('alert')).toHaveTextContent(
    'Enter a flow number, such as 5512 or QF-5512.',
  );
  expect(requests).toHaveLength(sent);

  // The server's refusal of a search, as given.
  set('Quantity Flow', '');
  const REFUSED = 'The first day of the search must not be after its last day.';
  failures['GET movements'] = { status: 422, detail: REFUSED };
  set('First day', '2023-12-01');
  fireEvent.click(within(form).getByRole('button', { name: 'Search' }));
  expect(
    await within(
      document.querySelector('.admin-arv-results') as HTMLElement,
    ).findByRole('alert'),
  ).toHaveTextContent(REFUSED);
  delete failures['GET movements'];

  // Nothing matches.
  fireEvent.click(within(form).getByRole('button', { name: 'Clear' }));
  set('Part Number', 'NONE');
  fireEvent.click(within(form).getByRole('button', { name: 'Search' }));
  expect(
    await screen.findByText('No archived Movement matches the search.'),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      'Every term must match, and a Work Order Number must be typed exactly as it is stored. Clear shows every Movement of the archive.',
    ),
  ).toBeInTheDocument();

  // Clear empties every field and lists the whole archive again.
  fireEvent.click(within(form).getByRole('button', { name: 'Clear' }));
  await waitFor(() => expect(resultRows()).toHaveLength(2));
  expect(lastSearch()).toEqual({ limit: '50' });
  expect(within(form).getByLabelText('Part Number')).toHaveValue('');
  expect(within(form).getByLabelText('Area')).toHaveValue('');
});

test("VW-4: a row opens its flow's whole archived history with its lineage in the modeless panel; a lineage link switches flows and Escape returns focus to the row", async () => {
  renderViewer();
  await pageReady();
  const rowButton = screen.getByRole('button', {
    name: 'Show the archived history of QF-5513 (AD-0001)',
  });
  fireEvent.click(rowButton);
  const panel = await screen.findByRole('complementary', {
    name: 'Archived history of QF-5513',
  });
  await within(panel).findByText('FLOATING — actual trace');
  expect(panel.querySelector('.admin-arv-flowmark')?.textContent).toBe(
    'Archived history from run 2 · read-only',
  );
  expect(panel.querySelector('.jsub')?.textContent).toBe('AD-0001 · name —');
  expect(panel.querySelector('.qf-head')?.textContent).toBe(
    'QF-55134 pcsFLOATING — actual tracestocked — complete · SPLIT from QF-5512',
  );
  expect(
    Array.from(panel.querySelectorAll('.devnote'), (note) => note.textContent),
  ).toEqual([
    'Floating Route — its actual trace is the archived Movement history below.',
    'Work Order WO-1',
    'Stocked 4 at STOCK',
    `Created ${formatInstant('2023-11-02T08:00:00.000000Z')} · closed ${formatInstant('2023-11-03T09:00:00.000000Z')}`,
  ]);
  expect(
    Array.from(
      panel.querySelectorAll('.admin-arv-lineage li'),
      (li) => li.textContent,
    ),
  ).toEqual(['SPLIT from QF-5512']);
  expect(
    Array.from(panel.querySelectorAll('ul.mv li'), (li) =>
      Array.from(li.children, (part) => part.textContent),
    ),
  ).toEqual([
    [
      archivedTimestamp('2023-11-03T08:00:00.000000Z'),
      'TRANSFERRED',
      'MAT → Area 5 · qty 4 · QF-5513 · W: Alex · MAT-ST-01',
    ],
  ]);
  // Every code stays whole in the narrow panel: the flows, the station,
  // the Part Number and the Work Order Number are never broken at a hyphen.
  expect(
    Array.from(
      panel.querySelectorAll('.wholecode'),
      (code) => code.textContent,
    ),
  ).toEqual(['AD-0001', 'QF-5512', 'WO-1', 'QF-5513', 'MAT-ST-01']);
  // The rows of the open flow are marked; the panel never moved the list.
  expect(rowButton).toHaveAttribute('aria-pressed', 'true');

  // The lineage leads to the flow it was split from.
  fireEvent.click(
    within(panel).getByRole('button', {
      name: 'Show the archived history of QF-5512',
    }),
  );
  const parent = await screen.findByRole('complementary', {
    name: 'Archived history of QF-5512',
  });
  await within(parent).findByText('PLANNED — snapshot');
  expect(document.activeElement).toBe(
    within(parent).getByRole('heading', { name: 'QF-5512' }),
  );
  expect(
    Array.from(parent.querySelectorAll('.route .rstep'), (step) => [
      step.textContent,
      step.getAttribute('title'),
    ]),
  ).toEqual([
    ['MAT', 'OP-MAT'],
    ['Area 5', null],
  ]);
  expect(parent.querySelector('.qf-pos:last-child')?.textContent).toBe(
    'split — consumed · SPLIT into QF-5513',
  );
  expect(parent.textContent).toContain(
    'Planned Route (snapshot) — guidance only; the archived Movement history below stays authoritative.',
  );
  // An undone split stays listed, marked.
  expect(
    Array.from(
      parent.querySelectorAll('.admin-arv-lineage li'),
      (li) => li.textContent,
    ),
  ).toEqual(['SPLIT into QF-5513', 'SPLIT into QF-5514 (undone)']);
  expect(
    Array.from(parent.querySelectorAll('ul.mv li .desc'), (d) => d.textContent),
  ).toEqual([
    'QF-5512 (4) → QF-5513 · at MAT',
    'Received into MAT · qty 10 · QF-5512 · WO WO-1 release · Planned Route step 1 · by Ann',
  ]);

  // Escape closes the panel and gives the focus back to the row.
  fireEvent.keyDown(window, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('complementary')).toBeNull());
  expect(document.activeElement).toBe(rowButton);

  // Clicking the open flow's row again closes it, as in Tracking.
  fireEvent.click(rowButton);
  await screen.findByRole('complementary', {
    name: 'Archived history of QF-5513',
  });
  fireEvent.click(rowButton);
  await waitFor(() => expect(screen.queryByRole('complementary')).toBeNull());
});

test('VW-5: loading, error with Retry, a failed search, an unknown view, a view still waiting with Check again, and a closed view show their states', async () => {
  // The views could not be read: Retry.
  failures['GET views'] = { status: 500, detail: 'Database unavailable.' };
  renderViewer();
  expect(
    screen.getByRole('status', { name: 'Loading archived history' }),
  ).toBeInTheDocument();
  const error = await screen.findByText(
    'The archived history could not be loaded.',
  );
  expect(error.closest('[role="alert"]')).toHaveTextContent(
    'Database unavailable.',
  );
  delete failures['GET views'];
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await pageReady();
  cleanup();

  // The search itself failed: Retry reads it again.
  failures['GET movements'] = { status: 500, detail: 'Database unavailable.' };
  renderViewer();
  expect(
    await screen.findByText('The archived Movements could not be read.'),
  ).toBeInTheDocument();
  delete failures['GET movements'];
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(resultRows()).toHaveLength(2));
  cleanup();

  // An unknown view: the server's words, and no search.
  views = [];
  namesFailure = { status: 404, detail: 'There is no archive view 4.' };
  renderViewer();
  expect(
    await screen.findByText('There is no archive view 4.'),
  ).toBeInTheDocument();
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
    'Archived history',
  );
  expect(screen.queryByRole('form')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Check again' })).toBeNull();
  cleanup();

  // A view still waiting to be loaded: Check again, until it opens.
  const WAITING =
    'View 4 of run 2 is not loaded yet (REQUESTED): the host job loads it within minutes of the request.';
  views = [
    viewWire({
      state: 'REQUESTED',
      started_at: null,
      opened_at: null,
      open_until: null,
      loaded: null,
    }),
  ];
  namesFailure = { status: 409, detail: WAITING };
  renderViewer();
  expect(await screen.findByText(WAITING)).toBeInTheDocument();
  expect(screen.queryByRole('note')).toBeNull();
  namesFailure = null;
  views = [viewWire()];
  fireEvent.click(screen.getByRole('button', { name: 'Check again' }));
  await pageReady();
  cleanup();

  // A closed view: the server's words, nothing to check again.
  const CLOSED =
    'View 4 of run 2 is closed (CLOSED): its loaded rows are removed. Open the run for viewing again to read it.';
  views = [viewWire({ state: 'CLOSED' })];
  namesFailure = { status: 409, detail: CLOSED };
  renderViewer();
  expect(await screen.findByText(CLOSED)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Check again' })).toBeNull();
  expect(screen.queryByRole('form')).toBeNull();
});

test('VW-6: a view closed while it is read turns the page into its closed state, with the server text, back at its top and in focus', async () => {
  // jsdom lays nothing out: record where the page scrolls to.
  const scrolled: Element[] = [];
  Element.prototype.scrollIntoView = function (this: Element) {
    scrolled.push(this);
  };
  renderViewer();
  await pageReady();
  const CLOSING =
    'View 4 of run 2 is closed (CLOSING): the host job removes its loaded rows at its next start. Open the run' +
    ' for viewing again to read it.';
  failures['GET movements'] = { status: 409, detail: CLOSING };
  const older = screen.getByRole('button', { name: 'Show older Movements' });
  older.focus();
  fireEvent.click(older);
  expect(await screen.findByText(CLOSING)).toBeInTheDocument();
  expect(screen.queryByRole('form')).toBeNull();
  expect(document.querySelector('.admin-arv-list')).toBeNull();
  expect(screen.queryByRole('note')).toBeNull();
  // The control in focus went away with the results: the page returns to
  // its top and the focus moves to the server's words.
  const message = screen.getByText(CLOSING).closest('.admin-arv-unavailable');
  await waitFor(() => expect(document.activeElement).toBe(message));
  expect(scrolled.at(-1)).toBe(document.querySelector('.admin-arv'));
  cleanup();

  // The same when the flow is read.
  delete failures['GET movements'];
  renderViewer();
  await pageReady();
  failures['GET flow'] = { status: 409, detail: CLOSING };
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Show the archived history of QF-5513 (AD-0001)',
    }),
  );
  expect(await screen.findByText(CLOSING)).toBeInTheDocument();
  expect(screen.queryByRole('complementary')).toBeNull();
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByText(CLOSING).closest('.admin-arv-unavailable'),
    ),
  );
});

test('VW-7: without Run history archival and purge the page says what it needs and reads nothing', async () => {
  session = sessionWith(['CONFIGURE_SYSTEM_SETTINGS']);
  renderViewer();
  expect(
    await screen.findByText(
      'Your account cannot open archived history. Opening it needs the Run history archival and purge permission.',
    ),
  ).toBeInTheDocument();
  expect(screen.queryByRole('form')).toBeNull();
  expect(screen.queryByRole('note')).toBeNull();
  expect(
    requests.filter((url) => url.startsWith('/api/history-archive')),
  ).toEqual([]);
});

test('VW-8: the way back shows History archival & purge at /administration, with focus on its section', async () => {
  renderViewer();
  await pageReady();
  fireEvent.click(
    screen.getByRole('link', { name: '← History archival & purge' }),
  );
  expect(window.location.pathname).toBe('/administration');
  expect(
    await screen.findByRole('heading', {
      name: 'History archival & purge',
      level: 1,
    }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('form')).toBeNull();
  await waitFor(() =>
    expect(document.activeElement).toBe(
      within(
        screen.getByRole('navigation', { name: 'Administration sections' }),
      ).getByRole('button', { name: 'History archival & purge' }),
    ),
  );
});
