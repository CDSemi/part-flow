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
import type { ConnectivityStatus } from '../../app/connectivity-context';
import { SessionContext, hasPermission } from '../../app/session-context';
import type { SessionValue } from '../../app/session-context';
import { formatInstant } from './archive-runs';
import { HistoryArchivalSection } from './HistoryArchivalSection';

// Administration → History archival & purge (Phase 16, archival design B
// steps 1 and 2): the settings card and its partial-PUT dialog (with the
// purge batch limits), the preview with its progress and the server's
// refusals, the request of a run (and a request whose answer was lost),
// what a run purges, and the runs table with the purge's states, results,
// counts and evidence, Cancel and Confirm off-host copy; and (step 3,
// D17) the Permanent deletion requests card, shown only to holders of its
// key, with Cancel. The /api surface is faked in memory with the
// contracts' routes and shapes; the server's messages are shown as given.

const T0 = '2026-11-17T08:00:00Z';
const SHA = 'a'.repeat(64);
const RECEIPT_SHA = 'b'.repeat(64);
/** The pre-purge backup disclosure, its hyphen non-breaking (U+2011). */
const PRE_PURGE = 'Pre‑purge backup';

interface RunWire {
  id: number;
  state: string;
  cutoff: string;
  cutoff_date: string;
  groups: string[];
  reason: string;
  requested_by: { user_id: number; display_name: string };
  requested_at: string;
  preview: {
    components: number;
    flows: number;
    movements: number;
    first_occurred_at: string | null;
    last_occurred_at: string | null;
    estimated_archive_bytes: number;
  };
  started_at: string | null;
  archive_name: string | null;
  exported_at: string | null;
  exported: {
    components: number;
    flows: number;
    movements: number;
    lineage_edges: number;
    assigned_routes: number;
  } | null;
  archive_bytes: number | null;
  manifest_sha256: string | null;
  verified_at: string | null;
  skipped: { components: number; flows: number } | null;
  copied_at: string | null;
  copy_confirmed_at: string | null;
  copy_confirmed_by: { user_id: number; display_name: string } | null;
  cancelled_by: { user_id: number; display_name: string } | null;
  pre_purge_backup: { name: string; dump_started_at: string } | null;
  purge_started_at: string | null;
  purged: { components: number; flows: number; movements: number } | null;
  receipt_name: string | null;
  receipt_sha256: string | null;
  finished_at: string | null;
  result: string | null;
  failure_reason: string | null;
}

function run(id: number, overrides: Partial<RunWire> = {}): RunWire {
  return {
    id,
    state: 'APPROVED',
    cutoff: '2024-11-17T08:00:00Z',
    cutoff_date: '2024-11-17',
    groups: ['MOVEMENT_HISTORY'],
    reason: 'First archive',
    requested_by: { user_id: 1, display_name: 'Ann' },
    requested_at: '2026-11-17T09:00:00Z',
    preview: {
      components: 120,
      flows: 310,
      movements: 4210,
      first_occurred_at: '2023-01-03T10:00:00Z',
      last_occurred_at: '2024-11-16T10:00:00Z',
      estimated_archive_bytes: 812345,
    },
    started_at: null,
    archive_name: null,
    exported_at: null,
    exported: null,
    archive_bytes: null,
    manifest_sha256: null,
    verified_at: null,
    skipped: null,
    copied_at: null,
    copy_confirmed_at: null,
    copy_confirmed_by: null,
    cancelled_by: null,
    pre_purge_backup: null,
    purge_started_at: null,
    purged: null,
    receipt_name: null,
    receipt_sha256: null,
    finished_at: null,
    result: null,
    failure_reason: null,
    ...overrides,
  };
}

const WAITING = (id: number) =>
  run(id, {
    state: 'WAITING_FOR_COPY',
    started_at: '2026-11-17T09:20:00Z',
    archive_name: `run-${id}-20261117T092000Z`,
    exported_at: '2026-11-17T09:21:00Z',
    exported: {
      components: 120,
      flows: 310,
      movements: 4210,
      lineage_edges: 40,
      assigned_routes: 12,
    },
    archive_bytes: 790001,
    manifest_sha256: SHA,
    verified_at: '2026-11-17T09:22:00Z',
    skipped: { components: 0, flows: 0 },
  });

/** A run the job copied, backed up and is purging (step 2). */
const PURGING = (id: number) =>
  run(id, {
    ...WAITING(id),
    state: 'PURGING',
    copied_at: '2026-11-17T09:23:00Z',
    pre_purge_backup: {
      name: `20261117T092400Z-pre-purge-run-${id}-20261117T092000Z`,
      dump_started_at: '2026-11-17T09:24:00Z',
    },
    purge_started_at: '2026-11-17T09:26:00Z',
    purged: { components: 40, flows: 100, movements: 1400 },
  });

/** A run that purged every component it archived. */
const PURGED = (id: number) =>
  run(id, {
    ...PURGING(id),
    state: 'COMPLETED',
    purged: { components: 120, flows: 310, movements: 4210 },
    receipt_name: `run-${id}-20261117T092000Z.purged.json`,
    receipt_sha256: RECEIPT_SHA,
    finished_at: '2026-11-17T09:40:00Z',
    result: 'PURGED',
  });

interface Policy {
  retention_period_months: number | null;
  shortest_retention_months: number;
  archive_start_minute: number;
  archive_preview_seconds: number;
  archive_max_wait_days: number;
  archive_purge_batch_movements: number;
  archive_purge_batch_part_numbers: number;
}

/** A permanent deletion request (archival step 3, D17) as the API
 * answers it. */
interface DeletionWire {
  id: number;
  state: string;
  scope: { work_order_numbers: string[]; part_numbers: string[] };
  reason: string;
  requested_by: { user_id: number; display_name: string } | null;
  requested_by_operator: string | null;
  requested_at: string;
  work_orders: {
    id: number;
    work_order_number: string | null;
    internal: boolean;
    named: boolean;
    part_numbers: string[];
    demand_lines: number;
  }[];
  part_numbers: { part_number: string; master: boolean }[];
  rows: Record<string, number>;
  started_at: string | null;
  backup_label: string | null;
  pre_purge_backup: { name: string; dump_started_at: string } | null;
  finished_at: string | null;
  cancelled_by: { user_id: number; display_name: string } | null;
  failure_reason: string | null;
}

function deletion(
  id: number,
  overrides: Partial<DeletionWire> = {},
): DeletionWire {
  return {
    id,
    state: 'APPROVED',
    scope: { work_order_numbers: ['007201'], part_numbers: [] },
    reason: 'Test data entered by mistake',
    requested_by: { user_id: 9, display_name: 'Sam Super' },
    requested_by_operator: null,
    requested_at: '2026-11-18T10:00:00Z',
    work_orders: [
      {
        id: 1,
        work_order_number: '007201',
        internal: false,
        named: true,
        part_numbers: ['A-100'],
        demand_lines: 1,
      },
    ],
    part_numbers: [],
    rows: {
      audit_events: 4,
      part_movements: 12,
      quantity_flow_lineage: 0,
      quantity_flows: 2,
      assigned_route_steps: 6,
      assigned_routes: 2,
      work_order_allocations: 0,
      work_order_demands: 1,
      work_orders: 1,
      part_numbers: 0,
    },
    started_at: null,
    backup_label: null,
    pre_purge_backup: null,
    finished_at: null,
    cancelled_by: null,
    failure_reason: null,
    ...overrides,
  };
}

let policy: Policy;
let runs: RunWire[];
let deletions: DeletionWire[];
let writes: { method: string; url: string; body: unknown }[];
/** A refusal or lost answer per route key, if set (`PUT policy`,
 * `GET preview`, `POST runs`, `POST cancel`, `POST copy`, `GET
 * deletions`, `POST deletion cancel`). */
let failures: Record<string, { status: number; detail: string } | 'network'>;
/** While set, a preview answers only once it resolves. */
let previewHold: Promise<void> | null;
let session: SessionValue;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function handle(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? 'GET';
  const body: unknown = init?.body ? JSON.parse(String(init.body)) : undefined;
  if (method !== 'GET') writes.push({ method, url, body });
  const fail = (key: string): Response | null => {
    const failure = failures[key];
    if (failure === undefined) return null;
    if (failure === 'network') throw new TypeError('Failed to fetch');
    return json({ detail: failure.detail }, failure.status);
  };
  if (url === '/api/policies/data-retention') {
    if (method === 'PUT') {
      const refused = fail('PUT policy');
      if (refused) return refused;
      const changes = { ...(body as Record<string, unknown>) };
      delete changes.reason;
      policy = { ...policy, ...changes } as Policy;
    }
    return json({ ...policy, updated_at: T0 });
  }
  if (url.startsWith('/api/history-archive/preview')) {
    if (previewHold) await previewHold;
    const refused = fail('GET preview');
    if (refused) return refused;
    const cutoff = new URL(url, 'http://x').searchParams.get('cutoff_date');
    return json({
      cutoff_date: cutoff ?? '2024-11-17',
      cutoff: '2024-11-17T08:00:00Z',
      latest_cutoff_date: '2024-11-17',
      site_timezone: 'America/Los_Angeles',
      components: cutoff === '2020-01-01' ? 0 : 120,
      flows: cutoff === '2020-01-01' ? 0 : 310,
      movements: cutoff === '2020-01-01' ? 0 : 4210,
      first_occurred_at:
        cutoff === '2020-01-01' ? null : '2023-01-03T10:00:00Z',
      last_occurred_at: cutoff === '2020-01-01' ? null : '2024-11-16T10:00:00Z',
      estimated_archive_bytes: cutoff === '2020-01-01' ? 0 : 812345,
      duration_ms: 3412,
      time_limit_seconds: policy.archive_preview_seconds,
    });
  }
  if (url === '/api/history-archive/runs' && method === 'GET') {
    return json(runs);
  }
  if (url === '/api/history-archive/runs' && method === 'POST') {
    const failure = failures['POST runs'];
    if (
      failure !== undefined &&
      failure !== 'network' &&
      failure.status < 500
    ) {
      return json({ detail: failure.detail }, failure.status);
    }
    const request = body as { reason: string; cutoff_date: string | null };
    const created = run(runs.length + 1, {
      reason: request.reason,
      cutoff_date: request.cutoff_date ?? '2024-11-17',
    });
    runs = [created, ...runs];
    // The run is created even when its answer is lost.
    const refused = fail('POST runs');
    if (refused) return refused;
    return json(created, 201);
  }
  const action =
    /^\/api\/history-archive\/runs\/(\d+)\/(cancel|copy-confirmation)$/.exec(
      url,
    );
  if (action && method === 'POST') {
    const target = runs.find((r) => r.id === Number(action[1]));
    if (!target)
      return json({ detail: `There is no archival run ${action[1]}.` }, 404);
    const refused = fail(action[2] === 'cancel' ? 'POST cancel' : 'POST copy');
    if (refused) return refused;
    if (action[2] === 'cancel') {
      target.state = 'CANCELLED';
      target.cancelled_by = { user_id: 90, display_name: 'Ada Admin' };
      target.finished_at = T0;
    } else {
      target.copy_confirmed_at = T0;
      target.copy_confirmed_by = { user_id: 90, display_name: 'Ada Admin' };
    }
    return json(target);
  }
  if (url === '/api/permanent-deletion/requests' && method === 'GET') {
    const refused = fail('GET deletions');
    if (refused) return refused;
    return json(deletions);
  }
  const deletionCancel =
    /^\/api\/permanent-deletion\/requests\/(\d+)\/cancel$/.exec(url);
  if (deletionCancel && method === 'POST') {
    const target = deletions.find((d) => d.id === Number(deletionCancel[1]));
    if (!target) {
      return json(
        { detail: `There is no deletion request ${deletionCancel[1]}.` },
        404,
      );
    }
    const refused = fail('POST deletion cancel');
    if (refused) return refused;
    target.state = 'CANCELLED';
    target.cancelled_by = { user_id: 90, display_name: 'Ada Admin' };
    target.finished_at = T0;
    return json(target);
  }
  return json({ detail: `Unhandled fake route: ${method} ${url}` }, 500);
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

function renderSection(status: ConnectivityStatus = 'connected') {
  return render(
    <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
      <SessionContext.Provider value={session}>
        <HistoryArchivalSection />
      </SessionContext.Provider>
    </ConnectivityContext.Provider>,
  );
}

beforeEach(() => {
  policy = {
    retention_period_months: 24,
    shortest_retention_months: 12,
    archive_start_minute: 20,
    archive_preview_seconds: 90,
    archive_max_wait_days: 7,
    archive_purge_batch_movements: 50000,
    archive_purge_batch_part_numbers: 20,
  };
  runs = [];
  deletions = [];
  writes = [];
  failures = {};
  previewHold = null;
  session = sessionWith(['RUN_HISTORY_ARCHIVAL_AND_PURGE']);
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      handle(String(input), init),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function card(title: string): HTMLElement {
  return screen
    .getByRole('heading', { name: title })
    .closest('section') as HTMLElement;
}

function rows(container: ParentNode): Record<string, string> {
  return Object.fromEntries(
    Array.from(container.querySelectorAll('.prow'), (row) => [
      row.querySelector('.k')?.textContent ?? '',
      row.querySelector('.v')?.textContent ?? '',
    ]),
  );
}

async function loaded() {
  await screen.findByText('24 months (2 years)');
  await waitFor(() =>
    expect(screen.queryByRole('status', { name: 'Loading runs' })).toBeNull(),
  );
}

/** The cells of the runs table's row of run `id`, by column. */
function runRow(id: number): Record<string, string> {
  const table = within(card('Runs')).getByRole('table');
  const headers = within(table)
    .getAllByRole('columnheader')
    .map((th) => th.textContent ?? '');
  const row = within(table)
    .getAllByRole('row')
    .find((tr) => tr.querySelector('td')?.textContent === String(id));
  if (!row) throw new Error(`No row for run ${id}`);
  const cells = Array.from(row.querySelectorAll('td'), (td) => td.textContent);
  return Object.fromEntries(headers.map((h, i) => [h, cells[i] ?? '']));
}

test('HA-1: the section shows the settings, the preview and the runs, and says what a run purges', async () => {
  runs = [
    run(3, { state: 'RUNNING', started_at: '2026-11-17T09:20:00Z' }),
    run(2, {
      ...WAITING(2),
      state: 'COMPLETED',
      result: 'EXPORT_ONLY',
      copied_at: '2026-11-17T09:23:00Z',
      finished_at: '2026-11-17T09:23:00Z',
    }),
    run(1, {
      state: 'FAILED',
      failure_reason: 'The archive folder is not usable.',
      finished_at: '2026-11-16T09:20:00Z',
    }),
  ];
  renderSection();
  await loaded();

  expect(rows(card('Retention and schedule'))).toEqual({
    'Retention period': '24 months (2 years)',
    'Shortest allowed period': '12 months (1 year)',
    'Start minute': '20 minutes past each hour',
    'Preview time limit': '90 seconds',
    'Report a waiting run after': '7 days',
    'Movements per purge batch': '50,000',
    'Part Numbers per purge batch': '20',
  });
  expect(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  ).toBeEnabled();
  expect(card('Preview').textContent).toContain(
    'After the off-host copy and a pre-purge backup, a run deletes the archived Movement history from the database. Tracking then shows one “History archived in run N” line per archived flow, and archived commands can no longer be undone.',
  );
  expect(card('Runs').textContent).toContain(
    'Each run exports the Movement history before its cutoff to archive files on the server and, after the off-host copy and a pre-purge backup, purges it from the database.',
  );
  // Said once in the runs card, never per run.
  expect(
    within(card('Runs')).getAllByText(
      'Archived commands can no longer be undone.',
    ),
  ).toHaveLength(1);
  expect(card('Runs').textContent).not.toContain('nothing is deleted');
  expect(screen.queryByText(/View only/)).toBeNull();

  expect(runRow(3)).toMatchObject({
    State: 'Running',
    'Cutoff date': 'Nov 17, 2024',
    Result: '—',
    Archive: '—',
  });
  // No export yet: the preview's numbers, Movements first, marked as such.
  expect(runRow(3).Counts).toBe(
    '4,210 Movements120 components · 310 flowsFrom the preview',
  );
  // Requested and the latest step only; nothing more to fold away.
  expect(runRow(3).Times).toMatch(/^Requested .+Started .+$/);
  expect(runRow(3).Times).not.toContain('All times');
  expect(runRow(3)['Requested by']).toBe('AnnFirst archive');
  expect(runRow(3).Actions).toBe('');
  expect(runRow(2)).toMatchObject({
    State: 'Completed',
    Result: 'Exported to files; nothing deleted.',
    Archive: 'run-2-20261117T092000Z771.5 KB',
  });
  // The export equals the preview: one set of numbers, no skipped line.
  expect(runRow(2).Counts).toBe('4,210 Movements120 components · 310 flows');
  // The latest step is Finished; the whole timeline is under All times.
  const times = within(card('Runs'))
    .getAllByRole('row')
    .find((tr) => tr.querySelector('td')?.textContent === '2')!
    .querySelector('td[data-label="Times"]') as HTMLElement;
  expect(
    Array.from(
      times.querySelectorAll(':scope > .admin-runlines > .admin-runtime'),
      (line) => line.textContent?.split(' ')[0],
    ),
  ).toEqual(['Requested', 'Finished']);
  const details = times.querySelector('details') as HTMLDetailsElement;
  expect(within(details).getByText('All times').tagName).toBe('SUMMARY');
  expect(
    Array.from(
      details.querySelectorAll('.admin-runtime'),
      (line) => line.textContent?.split(' ')[0],
    ),
  ).toEqual([
    'Requested',
    'Started',
    'Exported',
    'Verified',
    'Copied',
    'Finished',
  ]);
  expect(runRow(1)).toMatchObject({
    State: 'Failed',
    Result: 'The archive folder is not usable.',
  });
  expect(writes).toEqual([]);
});

test('HA-1b: a run whose export differs from its preview shows both, and skipped components when there are any', async () => {
  runs = [
    {
      ...WAITING(6),
      exported: {
        components: 118,
        flows: 305,
        movements: 4150,
        lineage_edges: 40,
        assigned_routes: 12,
      },
      skipped: { components: 2, flows: 5 },
    },
  ];
  renderSection();
  await loaded();
  expect(runRow(6).Counts).toBe(
    '4,150 Movements118 components · 305 flowsPreview: 4,210 Movements · 120 components · 310 flowsSkipped (changed during the run): 2 components · 5 flows',
  );
  expect(runRow(6).Times).toMatch(/^Requested .+Verified .+All times/);
});

test('HA-2: without the archival permission the section is view-only: values, runs, no preview and no action', async () => {
  session = sessionWith(['CONFIGURE_SYSTEM_SETTINGS']);
  runs = [WAITING(4)];
  renderSection();
  await loaded();

  expect(
    screen.getByText(
      'View only — changing this needs the Run history archival and purge permission.',
    ),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^Edit / })).toBeNull();
  expect(screen.queryByRole('heading', { name: 'Preview' })).toBeNull();
  expect(screen.queryByRole('columnheader', { name: 'Actions' })).toBeNull();
  expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual([
    'Reload',
  ]);
  expect(runRow(4).State).toBe('Waiting for the off-host copy');
  // The preview needs the permission: it is never asked for.
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([input]) => String(input).includes('/preview')),
  ).toBe(false);
});

test('HA-3: Edit sends only the changed settings and shows the server refusal as given', async () => {
  renderSection();
  await loaded();
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Retention and schedule' });
  expect(
    within(dialog).getByLabelText('Retention period (months)'),
  ).toHaveValue(24);
  fireEvent.change(within(dialog).getByLabelText('Start minute (0–59)'), {
    target: { value: '45' },
  });
  fireEvent.change(
    within(dialog).getByLabelText('Report a waiting run after (days)'),
    {
      target: { value: '14' },
    },
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Retention and schedule' }),
    ).toBeNull(),
  );
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: { archive_start_minute: 45, archive_max_wait_days: 14 },
    },
  ]);
  expect(
    await screen.findByText('45 minutes past each hour'),
  ).toBeInTheDocument();
  expect(screen.getByText('14 days')).toBeInTheDocument();

  // A refusal keeps the dialog and its entries, with the server's text.
  writes = [];
  const REFUSAL =
    'The preview time limit must be a whole number of seconds from 10 to 600.';
  failures['PUT policy'] = { status: 422, detail: REFUSAL };
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const again = screen.getByRole('dialog', { name: 'Retention and schedule' });
  const seconds = within(again).getByLabelText('Preview time limit (seconds)');
  fireEvent.change(seconds, { target: { value: '5' } });
  fireEvent.click(within(again).getByRole('button', { name: 'Save changes' }));
  expect(await within(again).findByRole('alert')).toHaveTextContent(REFUSAL);
  expect(seconds).toHaveValue(5);
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: { archive_preview_seconds: 5 },
    },
  ]);
});

test('HA-4: a change of the shortest allowed period asks for its reason and sends it; no retention period sends null', async () => {
  renderSection();
  await loaded();
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Retention and schedule' });
  expect(within(dialog).queryByLabelText(/Reason for changing/)).toBeNull();
  fireEvent.change(
    within(dialog).getByLabelText('Shortest allowed period (months)'),
    { target: { value: '18' } },
  );
  fireEvent.change(within(dialog).getByLabelText(/Reason for changing/), {
    target: { value: 'Audit asks for 18 months' },
  });
  fireEvent.click(
    within(dialog).getByRole('radio', { name: 'No retention period' }),
  );
  expect(
    within(dialog).queryByLabelText('Retention period (months)'),
  ).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Retention and schedule' }),
    ).toBeNull(),
  );
  expect(writes).toHaveLength(1);
  expect(writes[0].body).toEqual({
    retention_period_months: null,
    shortest_retention_months: 18,
    reason: 'Audit asks for 18 months',
  });
  expect(await screen.findByText('No retention period')).toBeInTheDocument();

  // A field that is not a whole number blocks the save.
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const next = screen.getByRole('dialog', { name: 'Retention and schedule' });
  fireEvent.change(within(next).getByLabelText('Start minute (0–59)'), {
    target: { value: '2.5' },
  });
  expect(within(next).getByRole('alert')).toHaveTextContent(
    'Enter a whole number.',
  );
  expect(
    within(next).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
});

test('HA-5: the preview asks for the latest allowed cutoff, shows its progress and then its numbers', async () => {
  let release!: () => void;
  previewHold = new Promise((resolve) => (release = resolve));
  renderSection();
  await loaded();
  const preview = card('Preview');
  const request = within(preview).getByRole('button', {
    name: 'Request a run…',
  });
  expect(request).toBeDisabled();
  fireEvent.click(within(preview).getByRole('button', { name: 'Run preview' }));

  expect(
    await within(preview).findByText(
      /^Running the preview… \d+ s \(time limit 90 s\)$/,
    ),
  ).toBeInTheDocument();
  expect(
    within(preview).getByRole('button', { name: 'Running preview…' }),
  ).toBeDisabled();
  // The rest of the page stays usable meanwhile.
  expect(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  ).toBeEnabled();
  expect(
    vi
      .mocked(fetch)
      .mock.calls.map(([input]) => String(input))
      .filter((url) => url.includes('/preview')),
  ).toEqual(['/api/history-archive/preview']);

  release();
  await within(preview).findByText('Preview took');
  expect(rows(preview)).toEqual({
    'Cutoff date': 'Nov 17, 2024',
    Components: '120',
    Flows: '310',
    Movements: '4,210',
    'First and last Movement': 'Jan 03, 2023 – Nov 16, 2024',
    'Estimated archive size': '793.3 KB',
    'Preview took': '3.4 s',
  });
  expect(within(preview).getByLabelText('Cutoff date')).toHaveValue(
    '2024-11-17',
  );
  expect(preview.textContent).toContain(
    'The latest date the retention period allows is Nov 17, 2024.',
  );
  expect(request).toBeEnabled();

  // Another date drops the preview until it is run for that date.
  fireEvent.change(within(preview).getByLabelText('Cutoff date'), {
    target: { value: '2020-01-01' },
  });
  expect(within(preview).queryByText('Preview took')).toBeNull();
  expect(request).toBeDisabled();
  fireEvent.click(within(preview).getByRole('button', { name: 'Run preview' }));
  await within(preview).findByText('Preview took');
  expect(preview.textContent).toContain(
    'Nothing is eligible at this cutoff, so there is nothing to request.',
  );
  expect(request).toBeDisabled();
  expect(writes).toEqual([]);
});

test('HA-6: a refused preview shows the server message as given', async () => {
  const NO_PERIOD =
    'No retention period is set, so no history is eligible for archival. Set a retention period first.';
  failures['GET preview'] = { status: 409, detail: NO_PERIOD };
  renderSection();
  await loaded();
  const preview = card('Preview');
  fireEvent.click(within(preview).getByRole('button', { name: 'Run preview' }));
  expect(await within(preview).findByRole('alert')).toHaveTextContent(
    NO_PERIOD,
  );
  expect(
    within(preview).getByRole('button', { name: 'Request a run…' }),
  ).toBeDisabled();
});

async function previewed(): Promise<HTMLElement> {
  const preview = card('Preview');
  fireEvent.click(within(preview).getByRole('button', { name: 'Run preview' }));
  await within(preview).findByText('Preview took');
  fireEvent.click(
    within(preview).getByRole('button', { name: 'Request a run…' }),
  );
  return screen.getByRole('dialog', { name: 'Request an archival run' });
}

test('HA-7: a request shows the preview numbers, posts the reason and the cutoff, and reloads the runs', async () => {
  renderSection();
  await loaded();
  const dialog = await previewed();
  expect(dialog.textContent).toContain(
    '120 components · 310 flows · 4,210 Movements',
  );
  // What the run does, before it is requested: the purge and what follows.
  expect(dialog.textContent).toContain(
    'The run exports the Movement history before Nov 17, 2024 to archive files on the server and, after the off-host copy and a pre-purge backup, deletes it from the database. Tracking then shows one “History archived in run N” line per archived flow, and archived commands can no longer be undone.',
  );
  expect(dialog.textContent).not.toContain('deletes nothing');

  // A definite refusal keeps the dialog and the reason.
  const OPEN =
    'Run 9 is still open (APPROVED). Cancel it or wait until it finishes before requesting a new run.';
  failures['POST runs'] = { status: 409, detail: OPEN };
  const reason = within(dialog).getByLabelText(/Reason/);
  fireEvent.change(reason, { target: { value: 'Yearly archive' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Request run' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(OPEN);
  expect(reason).toHaveValue('Yearly archive');
  expect(runs).toEqual([]);

  delete failures['POST runs'];
  fireEvent.click(within(dialog).getByRole('button', { name: 'Request run' }));
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Request an archival run' }),
    ).toBeNull(),
  );
  expect(writes[writes.length - 1]).toEqual({
    method: 'POST',
    url: '/api/history-archive/runs',
    body: { reason: 'Yearly archive', cutoff_date: '2024-11-17' },
  });
  expect(
    await within(card('Preview')).findByText(
      '✓ Run 1 is requested. The archival job on the server starts it at its next Start minute.',
    ),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(runRow(1).State).toBe('Requested — waiting for the job'),
  );
});

test('HA-8: a request whose answer was lost (524) reloads the runs and asks to check them', async () => {
  renderSection();
  await loaded();
  const dialog = await previewed();
  failures['POST runs'] = { status: 524, detail: '' };
  fireEvent.change(within(dialog).getByLabelText(/Reason/), {
    target: { value: 'Yearly archive' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Request run' }));

  expect(await within(card('Preview')).findByRole('alert')).toHaveTextContent(
    'The server did not answer, so the run may or may not have been requested. Check the runs table before requesting again.',
  );
  expect(
    screen.queryByRole('dialog', { name: 'Request an archival run' }),
  ).toBeNull();
  // The run the server created shows after the reload.
  await waitFor(() =>
    expect(runRow(1).State).toBe('Requested — waiting for the job'),
  );
  expect(writes.filter((w) => w.method === 'POST')).toHaveLength(1);
});

test('HA-9: Cancel asks first, is offered only for a requested or waiting run, and posts once', async () => {
  runs = [WAITING(2), run(1, { state: 'CANCELLED', finished_at: T0 })];
  renderSection();
  await loaded();
  expect(screen.queryByRole('button', { name: 'Cancel run 1…' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel run 2…' }));
  const confirm = screen.getByRole('dialog', { name: 'Cancel run 2?' });
  expect(confirm.textContent).toContain(
    'Its archive folder stays on the server',
  );
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Keep run (Esc)' }),
  );
  expect(writes).toEqual([]);

  fireEvent.click(screen.getByRole('button', { name: 'Cancel run 2…' }));
  fireEvent.click(
    within(screen.getByRole('dialog', { name: 'Cancel run 2?' })).getByRole(
      'button',
      { name: 'Cancel run' },
    ),
  );
  expect(
    await within(card('Runs')).findByText('✓ Run 2 is cancelled.'),
  ).toBeInTheDocument();
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/history-archive/runs/2/cancel',
      body: undefined,
    },
  ]);
  await waitFor(() => expect(runRow(2).State).toBe('Cancelled'));
  expect(runRow(2).Result).toBe('Cancelled by Ada Admin.');
  expect(screen.queryByRole('button', { name: 'Cancel run 2…' })).toBeNull();
});

test('HA-10: a refused cancel shows the server text; a lost answer reloads the runs', async () => {
  runs = [run(5)];
  renderSection();
  await loaded();
  const RUNNING =
    'Run 5 is running on the server now and cannot be cancelled. Wait until it stops.';
  failures['POST cancel'] = { status: 409, detail: RUNNING };
  fireEvent.click(screen.getByRole('button', { name: 'Cancel run 5…' }));
  const confirm = screen.getByRole('dialog', { name: 'Cancel run 5?' });
  fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel run' }));
  expect(await within(confirm).findByRole('alert')).toHaveTextContent(RUNNING);

  failures['POST cancel'] = { status: 502, detail: 'Bad gateway.' };
  fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel run' }));
  expect(await within(card('Runs')).findByRole('alert')).toHaveTextContent(
    'The server did not answer, so run 5 may or may not have been cancelled.',
  );
  expect(screen.queryByRole('dialog', { name: 'Cancel run 5?' })).toBeNull();
});

test('HA-11: Confirm off-host copy shows the archive and its SHA-256, needs the copy ticked and sends the SHA-256', async () => {
  runs = [WAITING(3)];
  renderSection();
  await loaded();
  fireEvent.click(
    screen.getByRole('button', { name: 'Confirm the off-host copy of run 3…' }),
  );
  const dialog = screen.getByRole('dialog', {
    name: 'Confirm the off-host copy of run 3',
  });
  expect(dialog.textContent).toContain('run-3-20261117T092000Z');
  expect(dialog.textContent).toContain(SHA);
  expect(dialog.textContent).toContain('Hyper Backup task');
  expect(dialog.textContent).toContain(
    'At its next start the archival job on the server then takes the pre-purge backup and purges the archived history from the database.',
  );
  const confirm = within(dialog).getByRole('button', { name: 'Confirm copy' });
  expect(confirm).toBeDisabled();
  fireEvent.click(
    within(dialog).getByRole('checkbox', {
      name: 'The archive folder is copied off the server',
    }),
  );
  fireEvent.click(confirm);

  expect(
    await within(card('Runs')).findByText(
      '✓ The off-host copy of run 3 is confirmed. At its next start the archival job on the server takes the pre-purge backup and purges the archived history.',
    ),
  ).toBeInTheDocument();
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/history-archive/runs/3/copy-confirmation',
      body: { manifest_sha256: SHA },
    },
  ]);
  await waitFor(() =>
    expect(runRow(3).State).toBe('Copy confirmed — waiting for the job'),
  );
  expect(
    screen.queryByRole('button', {
      name: 'Confirm the off-host copy of run 3…',
    }),
  ).toBeNull();
  // Still cancellable until the job finishes it.
  expect(screen.getByRole('button', { name: 'Cancel run 3…' })).toBeEnabled();
});

test('HA-12: offline every write is blocked while the values stay readable', async () => {
  runs = [WAITING(3)];
  renderSection('unavailable');
  await loaded();
  expect(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  ).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Run preview' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Cancel run 3…' })).toBeDisabled();
  expect(
    screen.getByRole('button', { name: 'Confirm the off-host copy of run 3…' }),
  ).toBeDisabled();
  expect(runRow(3).Archive).toBe('run-3-20261117T092000Z771.5 KB');
});

test('HA-13: a failed load of the runs offers Retry', async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) =>
    String(input) === '/api/history-archive/runs'
      ? json({ detail: 'Database unavailable.' }, 500)
      : original(input, init),
  );
  renderSection();
  expect(
    await screen.findByText('The archival runs could not be loaded.'),
  ).toBeInTheDocument();
  vi.mocked(fetch).mockImplementation(original);
  runs = [run(1)];
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() =>
    expect(runRow(1).State).toBe('Requested — waiting for the job'),
  );
});

/** The labels of a run's full timeline, under All times. */
function allTimes(id: number): string[] {
  const row = within(card('Runs'))
    .getAllByRole('row')
    .find((tr) => tr.querySelector('td')?.textContent === String(id))!;
  const details = row.querySelector(
    'td[data-label="Times"] details',
  ) as HTMLElement;
  return Array.from(
    details.querySelectorAll('.admin-runtime'),
    (line) => line.firstChild?.textContent?.trim() ?? '',
  );
}

/** The archive cell of a run's row. */
function archiveCell(id: number): HTMLElement {
  const row = within(card('Runs'))
    .getAllByRole('row')
    .find((tr) => tr.querySelector('td')?.textContent === String(id))!;
  return row.querySelector('td[data-label="Archive"]') as HTMLElement;
}

test('HA-14: the runs show the purge — its states and results, the purged and skipped counts, the purge start, the backup and the receipt', async () => {
  runs = [
    PURGING(9),
    PURGED(8),
    {
      ...PURGED(7),
      state: 'COMPLETED_WITH_SKIPS',
      purged: { components: 118, flows: 305, movements: 4190 },
      // The verification's and the purge's skips together.
      skipped: { components: 2, flows: 5 },
    },
    // Every component changed after the export: the verification finished
    // the run, with a receipt and without a copy or a backup.
    {
      ...WAITING(6),
      state: 'COMPLETED_WITH_SKIPS',
      skipped: { components: 120, flows: 310 },
      receipt_name: 'run-6-20261117T092000Z.purged.json',
      receipt_sha256: RECEIPT_SHA,
      finished_at: '2026-11-17T09:23:00Z',
      result: 'NOTHING_PURGED',
    },
    {
      ...PURGING(5),
      state: 'FAILED',
      receipt_name: 'run-5-20261117T092000Z.purged.json',
      receipt_sha256: RECEIPT_SHA,
      failure_reason:
        'A purge batch deleted 9 Movements where its records count 10.',
      finished_at: '2026-11-17T09:30:00Z',
    },
    {
      ...WAITING(4),
      state: 'COMPLETED',
      copied_at: '2026-11-17T09:23:00Z',
      finished_at: '2026-11-17T09:23:00Z',
      result: 'EXPORT_ONLY',
    },
  ];
  renderSection();
  await loaded();

  // Purging: open, its progress so far, never cancellable.
  expect(runRow(9)).toMatchObject({
    State: 'Purging',
    Result: '—',
    Actions: '',
  });
  expect(runRow(9).Counts).toBe(
    '4,210 Movements120 components · 310 flowsPurged so far: 1,400 Movements · 40 components · 100 flows',
  );
  expect(runRow(9).Times).toMatch(/^Requested .+Purge started .+All times/);
  expect(allTimes(9)).toEqual([
    'Requested',
    'Started',
    'Exported',
    'Verified',
    'Copied',
    'Backup started',
    'Purge started',
  ]);
  const purging = archiveCell(9);
  // A non-breaking hyphen keeps `Pre-purge` whole in a narrow column.
  expect(within(purging).getByText(PRE_PURGE).tagName).toBe('SUMMARY');
  expect(purging.textContent).toContain(
    '20261117T092400Z-pre-purge-run-9-20261117T092000Z',
  );
  expect(within(purging).queryByText('Purge receipt')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Cancel run 9…' })).toBeNull();

  // Completed: the result in plain words, the purged counts, the backup
  // and the receipt with its SHA-256.
  expect(runRow(8)).toMatchObject({
    State: 'Completed',
    Result: 'Archived and purged.',
  });
  expect(runRow(8).Counts).toBe(
    '4,210 Movements120 components · 310 flowsPurged: 4,210 Movements · 120 components · 310 flows',
  );
  expect(runRow(8).Times).toMatch(/^Requested .+Finished .+All times/);
  expect(allTimes(8).slice(-3)).toEqual([
    'Backup started',
    'Purge started',
    'Finished',
  ]);
  const purged = archiveCell(8);
  expect(within(purged).getByText('Purge receipt').tagName).toBe('SUMMARY');
  expect(purged.textContent).toContain('run-8-20261117T092000Z.purged.json');
  expect(purged.textContent).toContain(`SHA-256 ${RECEIPT_SHA}`);
  expect(purged.textContent).toContain(
    '20261117T092400Z-pre-purge-run-8-20261117T092000Z',
  );

  expect(runRow(7)).toMatchObject({
    State: 'Completed with skips',
    Result: 'Archived and purged.',
  });
  expect(runRow(7).Counts).toBe(
    '4,210 Movements120 components · 310 flowsPurged: 4,190 Movements · 118 components · 305 flowsSkipped (changed during the run): 2 components · 5 flows',
  );

  expect(runRow(6)).toMatchObject({
    State: 'Completed with skips',
    Result: 'Nothing purged (every component changed).',
  });
  expect(runRow(6).Counts).not.toContain('Purged');
  expect(within(archiveCell(6)).getByText('Purge receipt')).toBeTruthy();
  expect(within(archiveCell(6)).queryByText(PRE_PURGE)).toBeNull();

  // A run that failed while purging keeps the whole components it purged.
  expect(runRow(5)).toMatchObject({
    State: 'Failed',
    Result: 'A purge batch deleted 9 Movements where its records count 10.',
  });
  expect(runRow(5).Counts).toContain(
    'Purged: 1,400 Movements · 40 components · 100 flows',
  );

  // A run that step 1 finished keeps its result.
  expect(runRow(4).Result).toBe('Exported to files; nothing deleted.');
  expect(runRow(4).Counts).not.toContain('Purged');
  expect(writes).toEqual([]);
});

test('HA-15: the purge batch limits are edited in the dialog, sent only when changed, and a refusal is shown as given', async () => {
  renderSection();
  await loaded();
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Retention and schedule' });
  expect(
    within(dialog).getByLabelText('Movements per purge batch'),
  ).toHaveValue(50000);
  expect(
    within(dialog).getByLabelText('Part Numbers per purge batch'),
  ).toHaveValue(20);
  fireEvent.change(
    within(dialog).getByLabelText('Part Numbers per purge batch'),
    { target: { value: '5' } },
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Retention and schedule' }),
    ).toBeNull(),
  );
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: { archive_purge_batch_part_numbers: 5 },
    },
  ]);
  await waitFor(() =>
    expect(
      rows(card('Retention and schedule'))['Part Numbers per purge batch'],
    ).toBe('5'),
  );

  // The server checks the range; its message stays in the dialog.
  writes = [];
  const REFUSAL =
    'The Movements per purge batch must be a whole number from 100 to 1000000.';
  failures['PUT policy'] = { status: 422, detail: REFUSAL };
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit retention and schedule…' }),
  );
  const again = screen.getByRole('dialog', { name: 'Retention and schedule' });
  const movements = within(again).getByLabelText('Movements per purge batch');
  fireEvent.change(movements, { target: { value: '50' } });
  fireEvent.click(within(again).getByRole('button', { name: 'Save changes' }));
  expect(await within(again).findByRole('alert')).toHaveTextContent(REFUSAL);
  expect(movements).toHaveValue(50);
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/data-retention',
      body: { archive_purge_batch_movements: 50 },
    },
  ]);

  // Only a value that is not a whole number is refused in the dialog.
  fireEvent.change(movements, { target: { value: '2.5' } });
  expect(
    within(again)
      .getAllByRole('alert')
      .map((alert) => alert.textContent),
  ).toContain('Enter a whole number.');
  expect(
    within(again).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
});

/* ============ Permanent deletion requests (archival step 3, D17) ============ */

const DELETION_KEY = 'DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY';
const DELETION_BACKUP =
  '20261118T090501Z-pre-purge-deletion-4-20261118T090500Z';
const CHANGED =
  'The data changed after the request; nothing was deleted. Request it again.';

/** One cell of the requests table's row of request `id`. */
function deletionCell(id: number, label: string): HTMLElement {
  const row = within(card('Permanent deletion requests'))
    .getAllByRole('row')
    .find((tr) => tr.querySelector('td')?.textContent === String(id));
  if (!row) throw new Error(`No row for deletion request ${id}`);
  return row.querySelector(`td[data-label="${label}"]`) as HTMLElement;
}

/** What the row of request `id` shows at a glance, by column: the state
 * pill, the lines a cell shows outside its folded disclosures, and the
 * result's text. */
function deletionRow(id: number): Record<string, string> {
  const shown = (label: string) =>
    Array.from(
      deletionCell(id, label).querySelectorAll(
        ':scope > .admin-runlines > :not(details)',
      ),
      (line) => line.textContent,
    ).join(' / ');
  return {
    State:
      deletionCell(id, 'State').querySelector('.pillnav')?.textContent ?? '',
    Deletes: shown('Deletes'),
    'Requested by': shown('Requested by'),
    Times: shown('Times'),
    Result:
      deletionCell(id, 'Result').querySelector('.admin-runresult')
        ?.textContent ?? '',
  };
}

/** The pre-purge backup folded in the result of request `id`, if any. */
function deletionBackup(id: number): string | null {
  const details = deletionCell(id, 'Result').querySelector('details');
  if (details === null) return null;
  expect(details.querySelector('summary')?.textContent).toBe(PRE_PURGE);
  return details.querySelector('.admin-runarchive')?.textContent ?? '';
}

async function deletionsLoaded() {
  await loaded();
  await waitFor(() =>
    expect(
      screen.queryByRole('status', {
        name: 'Loading permanent deletion requests',
      }),
    ).toBeNull(),
  );
}

const deletionReads = () =>
  vi
    .mocked(fetch)
    .mock.calls.filter(([input]) =>
      String(input).startsWith('/api/permanent-deletion'),
    ).length;

test('HA-16: a holder of the deletion key sees the requests below the runs: state, records, rows, requester, times, backup and result', async () => {
  session = sessionWith(['RUN_HISTORY_ARCHIVAL_AND_PURGE', DELETION_KEY]);
  deletions = [
    deletion(5, {
      state: 'RUNNING',
      started_at: '2026-11-18T10:05:00Z',
      backup_label: 'deletion-5-20261118T100500Z',
    }),
    deletion(4, {
      state: 'COMPLETED',
      scope: { work_order_numbers: [], part_numbers: ['PN-A'] },
      work_orders: [
        {
          id: 2,
          work_order_number: null,
          internal: true,
          named: false,
          part_numbers: ['PN-A'],
          demand_lines: 1,
        },
      ],
      part_numbers: [{ part_number: 'PN-A', master: true }],
      requested_by: null,
      requested_by_operator: 'ops-anna',
      requested_at: '2026-11-18T09:00:00Z',
      started_at: '2026-11-18T09:05:00Z',
      backup_label: 'deletion-4-20261118T090500Z',
      pre_purge_backup: {
        name: DELETION_BACKUP,
        dump_started_at: '2026-11-18T09:05:01Z',
      },
      finished_at: '2026-11-18T09:06:00Z',
    }),
    deletion(3, {
      state: 'FAILED',
      started_at: '2026-11-18T08:05:00Z',
      backup_label: 'deletion-3-20261118T080500Z',
      finished_at: '2026-11-18T08:06:00Z',
      failure_reason: CHANGED,
    }),
    deletion(2, {
      state: 'CANCELLED',
      finished_at: '2026-11-18T07:01:00Z',
      cancelled_by: { user_id: 90, display_name: 'Ada Admin' },
    }),
  ];
  renderSection();
  await deletionsLoaded();

  const requestsCard = card('Permanent deletion requests');
  expect(
    card('Runs').compareDocumentPosition(requestsCard) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(requestsCard.textContent).toContain(
    'Each request deletes Work Orders or a Part Number with all their data from the database, as if they had never been entered; the server carries it out after a pre-purge backup.',
  );
  expect(requestsCard.textContent).toContain(
    'Delete permanently… in Work Order Details and in the Part Number dialog of Management → Part Numbers requests a deletion.',
  );
  expect(
    within(requestsCard)
      .getAllByRole('columnheader')
      .map((th) => th.textContent),
  ).toEqual(['Request', 'State', 'Deletes', 'Requested by', 'Times', 'Result']);

  // The open request: Deleting…, nothing to cancel; its rows per table
  // are folded under its records.
  expect(deletionRow(5)).toMatchObject({
    State: 'Deleting…',
    Deletes: 'Work Order 007201 / 28 rows',
    'Requested by': 'Sam Super / Test data entered by mistake',
    Result: '—',
  });
  expect(deletionBackup(5)).toBeNull();
  expect(deletionCell(5, 'State').querySelector('button')).toBeNull();
  // The Work Order Number stays whole: it never wraps inside.
  expect(
    deletionCell(5, 'Deletes').querySelector('.admin-runname')?.textContent,
  ).toBe('007201');
  expect(deletionCell(5, 'Deletes').querySelector('summary')?.textContent).toBe(
    'Rows per table',
  );
  expect(
    Array.from(
      deletionCell(5, 'Deletes').querySelectorAll('details .admin-runsub'),
      (line) => line.textContent,
    ),
  ).toEqual([
    'Work Orders: 1',
    'Demand lines: 1',
    'Allocations: 0',
    'Quantity Flows: 2',
    'Lineage links: 0',
    'Movements: 12',
    'Assigned Routes: 2',
    'Assigned Route steps: 6',
    'Part Number details: 0',
    'Audit rows: 4',
  ]);
  expect(deletionRow(5).Times).toContain(
    `Requested ${formatInstant('2026-11-18T10:00:00Z')}`,
  );
  expect(deletionRow(5).Times).toContain(
    `Started ${formatInstant('2026-11-18T10:05:00Z')}`,
  );

  // An operator's Part Number request that deleted an internal Work
  // Order too, after its pre-purge backup (folded under the result).
  expect(deletionRow(4)).toMatchObject({
    State: 'Deleted',
    Deletes: 'Part Number PN-A / Also 1 internal Work Order / 28 rows',
    Result: 'Deleted from the database.',
  });
  expect(deletionBackup(4)).toBe(DELETION_BACKUP);
  expect(deletionRow(4)['Requested by']).toContain('ops-anna (operator)');
  expect(
    Array.from(
      deletionCell(4, 'Times').querySelectorAll('details .admin-runtime'),
      (line) => line.firstChild?.textContent?.trim(),
    ),
  ).toEqual(['Requested', 'Started', 'Backup started', 'Finished']);

  // A failed request shows the reason as the server gives it.
  expect(deletionRow(3)).toMatchObject({
    State: 'Failed — nothing was deleted',
    Result: CHANGED,
  });
  expect(deletionRow(2)).toMatchObject({
    State: 'Cancelled',
    Result: 'Cancelled by Ada Admin.',
  });
  expect(
    within(requestsCard).queryByRole('button', { name: /^Cancel deletion/ }),
  ).toBeNull();
  expect(writes).toEqual([]);
});

test('HA-17: Cancel asks first, is offered only for a request waiting for the server, and posts once', async () => {
  session = sessionWith(['RUN_HISTORY_ARCHIVAL_AND_PURGE', DELETION_KEY]);
  deletions = [
    deletion(6),
    deletion(2, {
      state: 'CANCELLED',
      finished_at: T0,
      cancelled_by: { user_id: 90, display_name: 'Ada Admin' },
    }),
  ];
  renderSection();
  await deletionsLoaded();
  expect(deletionRow(6).State).toBe('Waiting for the server');
  // Cancel… sits under the state it applies to.
  expect(deletionCell(6, 'State').querySelector('button')).toHaveAccessibleName(
    'Cancel deletion request 6…',
  );
  expect(
    screen.queryByRole('button', { name: 'Cancel deletion request 2…' }),
  ).toBeNull();

  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel deletion request 6…' }),
  );
  const confirm = screen.getByRole('dialog', {
    name: 'Cancel deletion request 6?',
  });
  expect(confirm.textContent).toContain(
    'Nothing is deleted, and the records stay in the database. To delete them later, request the deletion again.',
  );
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Keep request (Esc)' }),
  );
  expect(writes).toEqual([]);

  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel deletion request 6…' }),
  );
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Cancel deletion request 6?' }),
    ).getByRole('button', { name: 'Cancel request' }),
  );
  expect(
    await within(card('Permanent deletion requests')).findByText(
      '✓ Deletion request 6 is cancelled. Nothing was deleted.',
    ),
  ).toBeInTheDocument();
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/permanent-deletion/requests/6/cancel',
      body: undefined,
    },
  ]);
  await waitFor(() => expect(deletionRow(6).State).toBe('Cancelled'));
  expect(deletionRow(6).Result).toBe('Cancelled by Ada Admin.');
  expect(
    screen.queryByRole('button', { name: 'Cancel deletion request 6…' }),
  ).toBeNull();
});

test('HA-18: a refused cancel shows the server text; a lost answer reloads the requests; offline blocks Cancel', async () => {
  session = sessionWith(['RUN_HISTORY_ARCHIVAL_AND_PURGE', DELETION_KEY]);
  deletions = [deletion(7)];
  renderSection();
  await deletionsLoaded();
  const RUNNING =
    'Deletion request 7 is being carried out on the server now and cannot be cancelled. Wait until it finishes.';
  failures['POST deletion cancel'] = { status: 409, detail: RUNNING };
  fireEvent.click(
    screen.getByRole('button', { name: 'Cancel deletion request 7…' }),
  );
  const confirm = screen.getByRole('dialog', {
    name: 'Cancel deletion request 7?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel request' }),
  );
  expect(await within(confirm).findByRole('alert')).toHaveTextContent(RUNNING);

  const reads = deletionReads();
  failures['POST deletion cancel'] = { status: 502, detail: 'Bad gateway.' };
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel request' }),
  );
  expect(
    await within(card('Permanent deletion requests')).findByRole('alert'),
  ).toHaveTextContent(
    'The server did not answer, so deletion request 7 may or may not have been cancelled. Check its state in the table; cancelling it again is safe.',
  );
  expect(
    screen.queryByRole('dialog', { name: 'Cancel deletion request 7?' }),
  ).toBeNull();
  // The lost answer reloads the requests.
  await waitFor(() => expect(deletionReads()).toBeGreaterThan(reads + 1));
  cleanup();

  renderSection('unavailable');
  await deletionsLoaded();
  expect(
    screen.getByRole('button', { name: 'Cancel deletion request 7…' }),
  ).toBeDisabled();
});

test('HA-19: only holders of the deletion key see the card and read the requests; it has its empty and error states', async () => {
  // The archival key alone: no card, the requests never read.
  deletions = [deletion(1)];
  renderSection();
  await loaded();
  expect(
    screen.queryByRole('heading', { name: 'Permanent deletion requests' }),
  ).toBeNull();
  expect(deletionReads()).toBe(0);
  cleanup();

  // No key of the section at all: view-only, no card either.
  session = sessionWith(['CONFIGURE_SYSTEM_SETTINGS']);
  renderSection();
  await loaded();
  expect(
    screen.queryByRole('heading', { name: 'Permanent deletion requests' }),
  ).toBeNull();
  expect(deletionReads()).toBe(0);
  cleanup();

  // The deletion key alone: the card with Cancel, the rest view-only.
  session = sessionWith([DELETION_KEY]);
  renderSection();
  await deletionsLoaded();
  expect(
    screen.getByText(
      'View only — changing this needs the Run history archival and purge permission.',
    ),
  ).toBeInTheDocument();
  expect(deletionRow(1).State).toBe('Waiting for the server');
  expect(
    screen.getByRole('button', { name: 'Cancel deletion request 1…' }),
  ).toBeEnabled();
  cleanup();

  // Empty, then a failed load with Retry.
  deletions = [];
  renderSection();
  await deletionsLoaded();
  expect(
    within(card('Permanent deletion requests')).getByText(
      'No permanent deletion has been requested yet.',
    ),
  ).toBeInTheDocument();
  cleanup();

  failures['GET deletions'] = { status: 500, detail: 'Database unavailable.' };
  renderSection();
  expect(
    await screen.findByText(
      'The permanent deletion requests could not be loaded.',
    ),
  ).toBeInTheDocument();
  delete failures['GET deletions'];
  deletions = [deletion(8)];
  fireEvent.click(
    within(card('Permanent deletion requests')).getByRole('button', {
      name: 'Retry',
    }),
  );
  await waitFor(() =>
    expect(deletionRow(8).State).toBe('Waiting for the server'),
  );
});

test('HA-20: a Work Order Number or Part Number stays whole unless it is longer than 16 characters', async () => {
  session = sessionWith([DELETION_KEY]);
  deletions = [
    deletion(11, {
      state: 'CANCELLED',
      scope: {
        work_order_numbers: ['007099-SUPPLEMENTAL-AMENDMENT'],
        part_numbers: [],
      },
      finished_at: T0,
      cancelled_by: { user_id: 90, display_name: 'Ada Admin' },
    }),
    deletion(10, {
      state: 'CANCELLED',
      scope: { work_order_numbers: [], part_numbers: ['0118-40-0022-07X'] },
      finished_at: T0,
      cancelled_by: { user_id: 90, display_name: 'Ada Admin' },
    }),
  ];
  renderSection();
  await deletionsLoaded();
  const name = (id: number) =>
    deletionCell(id, 'Deletes').querySelector('.admin-runname') as HTMLElement;
  // 16 characters: kept on one line.
  expect(name(10)).toHaveTextContent('0118-40-0022-07X');
  expect(name(10).className).toBe('admin-runname');
  // 29 characters: may wrap anywhere rather than widen the table.
  expect(name(11)).toHaveTextContent('007099-SUPPLEMENTAL-AMENDMENT');
  expect(name(11).className).toBe('admin-runname long');
  expect(deletionRow(11).Deletes).toBe(
    'Work Order 007099-SUPPLEMENTAL-AMENDMENT / 28 rows',
  );
});
