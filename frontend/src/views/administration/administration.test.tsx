import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityContext } from '../../app/connectivity-context';
import {
  MOCK_BADGE_CONFIRM_POLICY,
  setBadgeConfirmRequirement,
} from '../../mocks/scan-station';
import { AdministrationView } from './AdministrationView';

// Administration (GUI_DESIGN §9, Phase 3.5): the minimum environment
// setup sections — Departments, Areas, Operations, Scan Stations,
// Barcode configuration — read and write the real /api surface (faked
// in-memory here with the same routes and semantics). Every other
// section presents itself honestly as not available yet; the Worker
// sessions policy preview stays a development-only panel behind the
// DEV build boundary.

interface FakeState {
  departments: { id: number; name: string; is_active: boolean }[];
  areas: {
    id: number;
    department_id: number;
    name: string;
    barcode_value: string | null;
    description: string | null;
    color: string | null;
    icon_url: null;
    is_terminal: boolean;
    is_active: boolean;
  }[];
  operations: {
    id: number;
    area_id: number;
    code: string;
    name: string | null;
    description: string | null;
    default_expected_duration: string | null;
    is_external: boolean;
    is_active: boolean;
  }[];
  stations: { station_id: string; area_id: number; is_active: boolean }[];
  format: { prefix: string; digits: number; next_sequence: number } | null;
  machines: {
    id: number;
    area_id: number;
    name: string;
    retired_on: string | null;
  }[];
  nextId: number;
}

const T0 = '2026-08-01T00:00:00.000Z';

function seedState(): FakeState {
  return {
    departments: [{ id: 1, name: 'Machine Shop', is_active: true }],
    areas: [
      {
        id: 1,
        department_id: 1,
        name: 'Lathe',
        barcode_value: 'PF:AREA:1',
        description: 'Turning cell',
        color: '#b06fe0',
        icon_url: null,
        is_terminal: false,
        is_active: true,
      },
      {
        id: 2,
        department_id: 1,
        name: 'Stockroom',
        barcode_value: 'PF:AREA:2',
        description: null,
        color: null,
        icon_url: null,
        is_terminal: true,
        is_active: true,
      },
    ],
    operations: [
      {
        id: 1,
        area_id: 1,
        code: 'TURN',
        name: 'Turning',
        description: null,
        default_expected_duration: 'PT45M',
        is_external: false,
        is_active: true,
      },
    ],
    stations: [{ station_id: 'LATHE-ST-77', area_id: 1, is_active: true }],
    format: { prefix: 'CD-', digits: 4, next_sequence: 513 },
    machines: [
      { id: 512, area_id: 1, name: 'Lathe 1', retired_on: null },
      { id: 104, area_id: 1, name: 'Old Lathe 1', retired_on: '2026-02-14' },
    ],
    nextId: 100,
  };
}

let state: FakeState;
/** Bodies of the write requests the fake API received, oldest first. */
let writes: { method: string; url: string; body: unknown }[];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stamp<T extends object>(row: T) {
  return { ...row, created_at: T0, updated_at: T0 };
}

async function handle(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? 'GET';
  const body =
    typeof init?.body === 'string'
      ? (JSON.parse(init.body) as Record<string, unknown>)
      : {};
  if (method !== 'GET') writes.push({ method, url, body });

  if (url === '/api/health') return json({ status: 'ok' });
  if (url === '/api/machines') {
    return json(
      state.machines.map((machine) =>
        stamp({
          ...machine,
          asset_tag: `CD-${machine.id}`,
          barcode_value: `PF:MACHINE:CD-${machine.id}`,
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
        }),
      ),
    );
  }
  if (url === '/api/departments' && method === 'GET') {
    return json(state.departments.map(stamp));
  }
  if (url === '/api/departments' && method === 'POST') {
    const name = String(body.name).trim();
    if (state.departments.some((d) => d.name === name)) {
      return json(
        { detail: `A Department named “${name}” already exists.` },
        409,
      );
    }
    const department = { id: state.nextId++, name, is_active: true };
    state.departments.push(department);
    return json(stamp(department), 201);
  }
  const departmentMatch = /^\/api\/departments\/(\d+)$/.exec(url);
  if (departmentMatch && method === 'PATCH') {
    const department = state.departments.find(
      (d) => d.id === Number(departmentMatch[1]),
    )!;
    if (
      body.is_active === false &&
      state.areas.some((a) => a.department_id === department.id && a.is_active)
    ) {
      return json(
        {
          detail:
            'The Department still has active Areas. Deactivate its Areas first.',
        },
        409,
      );
    }
    if (typeof body.name === 'string') department.name = body.name.trim();
    if (typeof body.is_active === 'boolean')
      department.is_active = body.is_active;
    return json(stamp(department));
  }
  if (url === '/api/areas' && method === 'GET') {
    return json(state.areas.map(stamp));
  }
  if (url === '/api/areas' && method === 'POST') {
    const area = {
      id: state.nextId++,
      department_id: Number(body.department_id),
      name: String(body.name).trim(),
      barcode_value: '',
      description: (body.description as string | null) ?? null,
      color: (body.color as string | null) ?? null,
      icon_url: null,
      is_terminal: Boolean(body.is_terminal),
      is_active: true,
    };
    area.barcode_value = `PF:AREA:${area.id}`;
    state.areas.push(area);
    return json(stamp(area), 201);
  }
  const areaMatch = /^\/api\/areas\/(\d+)$/.exec(url);
  if (areaMatch && method === 'PATCH') {
    const area = state.areas.find((a) => a.id === Number(areaMatch[1]))!;
    if (typeof body.name === 'string') area.name = body.name.trim();
    if ('description' in body)
      area.description = (body.description as string | null) ?? null;
    if ('color' in body) area.color = (body.color as string | null) ?? null;
    if (typeof body.is_terminal === 'boolean')
      area.is_terminal = body.is_terminal;
    if (typeof body.is_active === 'boolean') area.is_active = body.is_active;
    return json(stamp(area));
  }
  if (url === '/api/operations' && method === 'GET') {
    return json(state.operations.map(stamp));
  }
  if (url === '/api/operations' && method === 'POST') {
    const operation = {
      id: state.nextId++,
      area_id: Number(body.area_id),
      code: String(body.code).trim(),
      name: (body.name as string | null) ?? null,
      description: (body.description as string | null) ?? null,
      default_expected_duration:
        (body.default_expected_duration as string | null) ?? null,
      is_external: Boolean(body.is_external),
      is_active: true,
    };
    state.operations.push(operation);
    return json(stamp(operation), 201);
  }
  const operationMatch = /^\/api\/operations\/(\d+)$/.exec(url);
  if (operationMatch && method === 'PATCH') {
    const operation = state.operations.find(
      (o) => o.id === Number(operationMatch[1]),
    )!;
    Object.assign(operation, {
      ...(typeof body.code === 'string' ? { code: body.code.trim() } : {}),
      ...('name' in body ? { name: body.name ?? null } : {}),
      ...('description' in body
        ? { description: body.description ?? null }
        : {}),
      ...('default_expected_duration' in body
        ? { default_expected_duration: body.default_expected_duration ?? null }
        : {}),
      ...(typeof body.is_external === 'boolean'
        ? { is_external: body.is_external }
        : {}),
      ...(typeof body.is_active === 'boolean'
        ? { is_active: body.is_active }
        : {}),
    });
    return json(stamp(operation));
  }
  if (url === '/api/scan-stations' && method === 'GET') {
    return json(state.stations.map(stamp));
  }
  if (url === '/api/scan-stations' && method === 'POST') {
    const station = {
      station_id: String(body.station_id),
      area_id: Number(body.area_id),
      is_active: body.is_active !== false,
    };
    if (state.stations.some((s) => s.station_id === station.station_id)) {
      return json(
        { detail: `Scan Station “${station.station_id}” already exists.` },
        409,
      );
    }
    state.stations.push(station);
    return json(stamp(station), 201);
  }
  const stationMatch = /^\/api\/scan-stations\/([^/]+)$/.exec(url);
  if (stationMatch && method === 'PATCH') {
    const station = state.stations.find(
      (s) => s.station_id === decodeURIComponent(stationMatch[1]),
    )!;
    if (body.area_id !== undefined) station.area_id = Number(body.area_id);
    if (typeof body.is_active === 'boolean') station.is_active = body.is_active;
    return json(stamp(station));
  }
  if (url === '/api/barcode-configuration/machine-asset-tag-format') {
    if (method === 'GET') {
      return state.format === null
        ? json(
            { detail: 'The Machine Asset Tag format is not configured.' },
            404,
          )
        : json(stamp(state.format));
    }
    if (method === 'PUT') {
      state.format = {
        prefix: String(body.prefix),
        digits: Number(body.digits),
        next_sequence: state.format?.next_sequence ?? 1,
      };
      return json(stamp(state.format));
    }
  }
  return json({ detail: `Unhandled fake route: ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.history.replaceState({}, '', '/administration');
  state = seedState();
  writes = [];
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
  setBadgeConfirmRequirement('done', true);
  setBadgeConfirmRequirement('queue', true);
  setBadgeConfirmRequirement('undo', true);
});

function renderAdmin(status: 'connected' | 'unavailable' = 'connected') {
  return render(
    <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
      <AdministrationView />
    </ConnectivityContext.Provider>,
  );
}

function openSection(label: string) {
  fireEvent.click(
    within(
      screen.getByRole('navigation', { name: 'Administration sections' }),
    ).getByRole('button', { name: label }),
  );
}

/* ============ Areas (reference table) ============ */

test('the Areas table renders the real environment with derived Machine columns', async () => {
  renderAdmin();

  // Areas is the initial section; the table loads from the API.
  const latheRow = (
    await screen.findByRole('button', { name: 'Edit Lathe' })
  ).closest('tr') as HTMLElement;
  // Operations of the Area (active ones, by name).
  expect(latheRow.textContent).toContain('Turning');
  // The Machine-assignment mode follows from the Area's active
  // Machines — retired Machines never count.
  expect(latheRow.textContent).toContain('Queue → assign (one-shot)');
  expect(latheRow.textContent).toContain('Lathe 1');
  expect(latheRow.textContent).not.toContain('Old Lathe 1');
  expect(within(latheRow).getByText('Active')).toBeInTheDocument();

  const stockroomRow = screen
    .getByRole('button', { name: 'Edit Stockroom' })
    .closest('tr') as HTMLElement;
  expect(stockroomRow.textContent).toContain('Direct processing (no Machines)');
  expect(within(stockroomRow).getByText('Terminal')).toBeInTheDocument();

  // The stable-identity explanation stays under the table.
  expect(
    screen.getByText(/identity and barcode are stable/),
  ).toBeInTheDocument();
});

test('editing an Area shows its stable identity and saves through the API', async () => {
  renderAdmin();

  fireEvent.click(await screen.findByRole('button', { name: 'Edit Lathe' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Area' });
  // Identity panel: server-assigned barcode, read-only; no barcode
  // input exists anywhere in the dialog.
  expect(dialog.textContent).toContain('PF:AREA:1');
  expect(within(dialog).queryByLabelText(/Barcode/)).toBeNull();
  expect(dialog.textContent).toContain('Area identity and barcode are stable');

  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: 'Lathe Cell' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(
    await screen.findByRole('button', { name: 'Edit Lathe Cell' }),
  ).toBeInTheDocument();
  // The untouched color input was NOT resubmitted (an untouched
  // preview never overwrites the stored color).
  const patch = writes.find((w) => w.method === 'PATCH');
  expect(patch?.url).toBe('/api/areas/1');
  expect(patch?.body).toEqual({
    name: 'Lathe Cell',
    description: 'Turning cell',
    is_terminal: false,
    is_active: true,
  });
});

test('a new Area posts the entered values to the API', async () => {
  renderAdmin();

  fireEvent.click(await screen.findByRole('button', { name: '+ New Area' }));
  const dialog = screen.getByRole('dialog', { name: 'New Area' });
  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: 'Mill' },
  });
  fireEvent.change(within(dialog).getByLabelText(/Description/), {
    target: { value: 'Milling cell' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add Area' }));

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(
    await screen.findByRole('button', { name: 'Edit Mill' }),
  ).toBeInTheDocument();
  const post = writes.find((w) => w.method === 'POST');
  expect(post?.url).toBe('/api/areas');
  expect(post?.body).toEqual({
    department_id: 1,
    name: 'Mill',
    description: 'Milling cell',
    color: null,
    is_terminal: false,
  });
});

/* ============ Departments ============ */

test('Departments lists, creates and surfaces the server hierarchy rule', async () => {
  renderAdmin();
  openSection('Departments');

  const row = (
    await screen.findByRole('button', { name: 'Edit Machine Shop' })
  ).closest('tr') as HTMLElement;
  // Area count of the Department.
  expect(within(row).getByRole('cell', { name: '2' })).toBeInTheDocument();

  // Create a new Department.
  fireEvent.click(screen.getByRole('button', { name: '+ New Department' }));
  const dialog = screen.getByRole('dialog', { name: 'New Department' });
  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: 'Sheet Metal' },
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Department' }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(
    await screen.findByRole('button', { name: 'Edit Sheet Metal' }),
  ).toBeInTheDocument();

  // Deactivating a Department with active Areas is rejected by the
  // server — the dialog stays open with the explanation, nothing is
  // silently confirmed through.
  fireEvent.click(screen.getByRole('button', { name: 'Edit Machine Shop' }));
  const edit = screen.getByRole('dialog', { name: 'Edit Department' });
  fireEvent.click(within(edit).getByRole('checkbox'));
  fireEvent.click(within(edit).getByRole('button', { name: 'Save changes' }));
  const alert = await within(
    screen.getByRole('dialog', { name: 'Edit Department' }),
  ).findByRole('alert');
  expect(alert.textContent).toContain('still has active Areas');
  expect(
    state.departments.find((d) => d.name === 'Machine Shop')?.is_active,
  ).toBe(true);
});

/* ============ Operations ============ */

test('Operations edit durations as minutes and send the ISO 8601 wire value', async () => {
  renderAdmin();
  openSection('Operations');

  // The seeded PT45M renders as whole minutes.
  const row = (
    await screen.findByRole('button', { name: 'Edit Turning' })
  ).closest('tr') as HTMLElement;
  expect(row.textContent).toContain('45 min');

  fireEvent.click(screen.getByRole('button', { name: '+ New Operation' }));
  const dialog = screen.getByRole('dialog', { name: 'New Operation' });
  fireEvent.change(within(dialog).getByLabelText('Code'), {
    target: { value: 'POLISH' },
  });
  fireEvent.change(
    within(dialog).getByLabelText(/Expected duration in minutes/),
    { target: { value: '30' } },
  );
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Operation' }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

  const post = writes.find((w) => w.method === 'POST');
  expect(post?.url).toBe('/api/operations');
  expect(post?.body).toMatchObject({
    area_id: 1,
    code: 'POLISH',
    default_expected_duration: 'PT30M',
    is_external: false,
  });
  expect(
    await screen.findByRole('button', { name: 'Edit POLISH' }),
  ).toBeInTheDocument();

  // The Area binding is fixed on edit — no Area select, the identity
  // panel explains why.
  fireEvent.click(screen.getByRole('button', { name: 'Edit Turning' }));
  const edit = screen.getByRole('dialog', { name: 'Edit Operation' });
  expect(within(edit).queryByRole('combobox')).toBeNull();
  expect(edit.textContent).toContain('The Area binding is fixed');
});

/* ============ Scan Stations ============ */

test('Scan Stations validate the canonical Station ID and create through the API', async () => {
  renderAdmin();
  openSection('Scan Stations');

  expect(await screen.findByText('LATHE-ST-77')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '+ New Scan Station' }));
  const dialog = screen.getByRole('dialog', { name: 'New Scan Station' });
  // The Station ID is one URL path segment — the canonical shape is
  // validated in place.
  fireEvent.change(within(dialog).getByLabelText('Station ID'), {
    target: { value: 'ST/1' },
  });
  expect(within(dialog).getByRole('alert').textContent).toContain(
    "letters, digits, '.', '_' and '-'",
  );
  fireEvent.change(within(dialog).getByLabelText('Station ID'), {
    target: { value: 'LATHE-ST-78' },
  });
  expect(within(dialog).queryByRole('alert')).toBeNull();
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Scan Station' }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(await screen.findByText('LATHE-ST-78')).toBeInTheDocument();
  const post = writes.find((w) => w.method === 'POST');
  expect(post?.body).toEqual({
    station_id: 'LATHE-ST-78',
    area_id: 1,
    is_active: true,
  });

  // The Station ID is the stable identity — the edit dialog offers no
  // rename.
  fireEvent.click(screen.getByRole('button', { name: 'Edit LATHE-ST-77' }));
  const edit = screen.getByRole('dialog', { name: 'Edit Scan Station' });
  expect(within(edit).queryByLabelText('Station ID')).toBeNull();
  expect(edit.textContent).toContain('never renamed');
});

/* ============ Barcode configuration ============ */

test('Barcode configuration reads the persisted format and previews the server counter', async () => {
  renderAdmin();
  openSection('Barcode configuration');

  const prefix = await screen.findByLabelText('Prefix');
  expect(prefix).toHaveValue('CD-');
  // The Next Asset Tag preview reads the server's persisted counter.
  expect(screen.getByText('CD-0513')).toBeInTheDocument();
  expect(screen.getByText('PF:MACHINE:CD-0513')).toBeInTheDocument();
  // A settings form, not an entry table — no entry action.
  expect(screen.queryByRole('button', { name: /New entry/ })).toBeNull();

  // The prefix rejects whitespace and ':' in place.
  fireEvent.change(prefix, { target: { value: 'CD:' } });
  expect(screen.getByRole('alert').textContent).toContain(
    'cannot contain spaces or “:”',
  );

  // A valid change saves through PUT; the counter is never sent.
  fireEvent.change(prefix, { target: { value: 'MX-' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save format' }));
  await screen.findByText('✓ Format saved.');
  const put = writes.find((w) => w.method === 'PUT');
  expect(put?.body).toEqual({ prefix: 'MX-', digits: 4 });
  // A format change never resets the sequence — the preview follows
  // the same persisted counter under the new prefix.
  expect(screen.getByText('MX-0513')).toBeInTheDocument();
});

test('an unconfigured Asset Tag format states that Machines cannot be created yet', async () => {
  state.format = null;
  renderAdmin();
  openSection('Barcode configuration');

  expect(
    await screen.findByText(/has not been configured yet/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Machines cannot be created until it is saved/),
  ).toBeInTheDocument();
});

/* ============ Later-phase sections stay honest ============ */

test('a full-Administration section presents itself as not available yet', async () => {
  renderAdmin();
  await screen.findByRole('button', { name: 'Edit Lathe' });
  openSection('Users');

  expect(
    screen.getByText(/is not available yet/, { exact: false }),
  ).toBeInTheDocument();
  expect(screen.getByText(/full Administration/)).toBeInTheDocument();
  const entry = screen.getByRole('button', { name: '+ New entry' });
  expect(entry).toBeDisabled();
});

/* ============ Offline write-block ============ */

test('offline disables the configuration entry actions; reading stays available', async () => {
  renderAdmin('unavailable');

  // The table still loads and renders (read-only stays available)…
  expect(
    await screen.findByRole('button', { name: 'Edit Lathe' }),
  ).toBeInTheDocument();
  // …but the write entry action gates on connectivity.
  expect(screen.getByRole('button', { name: '+ New Area' })).toBeDisabled();

  openSection('Departments');
  expect(
    await screen.findByRole('button', { name: 'Edit Machine Shop' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: '+ New Department' }),
  ).toBeDisabled();

  // Save inside an editor dialog gates too.
  fireEvent.click(screen.getByRole('button', { name: 'Edit Machine Shop' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Department' });
  expect(
    within(dialog).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
});

/* ====== Worker sessions (development-only preview) ====== */

async function openWorkerSessions() {
  renderAdmin();
  openSection('Worker sessions');
  // The preview is a development-only lazy module — wait for it.
  await screen.findByText('Default timeout');
}

test('Worker sessions shows the timeout values and the three badge-confirmation switches', async () => {
  await openWorkerSessions();

  // Timeout preview: the default plus the per-Area override.
  expect(screen.getByText('Default timeout')).toBeInTheDocument();
  expect(screen.getByText('15 minutes')).toBeInTheDocument();
  expect(screen.getByText('Lathe override')).toBeInTheDocument();
  expect(screen.getByText('20 minutes')).toBeInTheDocument();

  // Three slide switches — one per sensitive action, default ON.
  const switches = screen.getAllByRole('switch');
  expect(switches).toHaveLength(3);
  for (const sw of switches) {
    expect(sw.getAttribute('aria-checked')).toBe('true');
    expect(sw.textContent).toContain('On');
  }
  for (const name of [
    'Require badge scan — DONE — Complete Area processing',
    'Require badge scan — QUEUE — Return unfinished quantity to queue',
    'Require badge scan — UNDO — Reverse the last action',
  ]) {
    expect(screen.getByRole('switch', { name })).toBeInTheDocument();
  }
  // A settings panel, not an entry table — no entry action renders.
  expect(screen.queryByRole('button', { name: /New entry/ })).toBeNull();
});

test('toggling a badge-confirmation switch updates the shared policy per action', async () => {
  await openWorkerSessions();

  const undoSwitch = screen.getByRole('switch', {
    name: 'Require badge scan — UNDO — Reverse the last action',
  });
  fireEvent.click(undoSwitch);
  expect(undoSwitch.getAttribute('aria-checked')).toBe('false');
  expect(undoSwitch.textContent).toContain('Off');
  expect(MOCK_BADGE_CONFIRM_POLICY.undo).toBe(false);
  // The other actions stay independent.
  expect(MOCK_BADGE_CONFIRM_POLICY.done).toBe(true);
  expect(MOCK_BADGE_CONFIRM_POLICY.queue).toBe(true);

  fireEvent.click(undoSwitch);
  expect(undoSwitch.getAttribute('aria-checked')).toBe('true');
  expect(MOCK_BADGE_CONFIRM_POLICY.undo).toBe(true);
});
