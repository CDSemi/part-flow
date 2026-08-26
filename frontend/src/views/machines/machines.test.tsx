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
import { MachinesView } from './MachinesView';

// Management → Machines (GUI_DESIGN §12, Phase 3.5): operational
// monitoring plus permission-based configuration against the REAL
// /api/machines surface — lifecycle (active/retired) stays separate
// from the derived operational state, maintenance never moves
// quantity, and every write round-trips through the API. These tests
// run the view against an in-memory fake of the backend API with the
// same route surface and semantics; Machine assignments (and with
// them the Running state and the assigned-quantity retire blocker)
// arrive with the Phase 6 production workflows, so every active
// Machine is Idle unless Maintenance overrides it.

interface FakeMachine {
  id: number;
  area_id: number;
  name: string;
  asset_tag: string;
  description: string | null;
  manufacturer: string | null;
  model: string | null;
  serial_number: string | null;
  installed_on: string | null;
  notes: string | null;
  maintenance_since: string | null;
  maintenance_note: string | null;
  maintenance_expected_return: string | null;
  state_changed_at: string;
  retired_on: string | null;
  operational_state?: 'MAINTENANCE' | 'RUNNING' | 'IDLE';
  assigned_quantity?: number;
}

interface FakeEvent {
  id: number;
  machine_id: number;
  event_type: 'RETIRED' | 'REACTIVATED';
  occurred_at: string;
  actor: string | null;
  reason: string | null;
  from_area_id: number | null;
  to_area_id: number | null;
}

interface FakeState {
  areas: {
    id: number;
    department_id: number;
    name: string;
    barcode_value: string;
    description: string | null;
    color: string | null;
    icon_url: null;
    is_terminal: boolean;
    is_active: boolean;
    created_at: string;
    updated_at: string;
  }[];
  machines: FakeMachine[];
  events: FakeEvent[];
  format: { prefix: string; digits: number; next_sequence: number };
  nextMachineId: number;
  nextEventId: number;
}

const T0 = '2026-08-01T00:00:00.000Z';
const NOW = '2026-08-19T00:00:00.000Z';
const TODAY = '2026-08-19';

function fakeArea(
  id: number,
  name: string,
  extra?: { is_terminal?: boolean; is_active?: boolean },
) {
  return {
    id,
    department_id: 1,
    name,
    barcode_value: `PF:AREA:${id}`,
    description: null,
    color: '#4f8cff',
    icon_url: null,
    is_terminal: extra?.is_terminal ?? false,
    is_active: extra?.is_active ?? true,
    created_at: T0,
    updated_at: T0,
  };
}

function fakeMachine(
  id: number,
  area_id: number,
  name: string,
  asset_tag: string,
  extra?: Partial<FakeMachine>,
): FakeMachine {
  return {
    id,
    area_id,
    name,
    asset_tag,
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
    retired_on: null,
    assigned_quantity: 0,
    ...extra,
  };
}

/** The seeded environment mirrors the familiar shop-floor sample. */
function seedState(): FakeState {
  return {
    areas: [
      fakeArea(1, 'Cut'),
      fakeArea(2, 'Lathe'),
      fakeArea(3, 'Mill'),
      fakeArea(4, 'Stockroom', { is_terminal: true }),
    ],
    machines: [
      fakeMachine(201, 1, 'Saw 1', 'CD-0201', {
        manufacturer: 'Amada',
        model: 'HFA-250W',
        serial_number: 'HF25-33017',
        installed_on: '2018-03-12',
      }),
      fakeMachine(512, 2, 'Lathe 1', 'CD-0512', {
        manufacturer: 'Mazak',
        model: 'QT-250',
        serial_number: 'Q25-90412',
        installed_on: '2026-02-16',
        // Phase 6: the server reports the assigned ACTIVE quantity.
        assigned_quantity: 40,
      }),
      fakeMachine(105, 2, 'Lathe 2', 'CD-0105', {
        manufacturer: 'Mazak',
        model: 'QT-15',
        serial_number: 'Q15-88472',
        installed_on: '2014-09-20',
      }),
      fakeMachine(106, 2, 'Lathe 3', 'CD-0106', {
        manufacturer: 'Haas',
        model: 'ST-20',
        serial_number: 'ST20-51230',
        installed_on: '2019-05-02',
      }),
      fakeMachine(107, 2, 'Lathe 4', 'CD-0107', {
        manufacturer: 'Haas',
        model: 'ST-20',
        serial_number: 'ST20-51301',
        installed_on: '2019-05-02',
        maintenance_since: '2026-07-28T00:00:00.000Z',
        maintenance_note: 'Spindle bearing replacement',
        maintenance_expected_return: '2026-08-06',
      }),
      fakeMachine(301, 3, 'Mill 1', 'CD-0301', {
        manufacturer: 'Haas',
        model: 'VF-2',
        serial_number: 'VF2-77841',
      }),
      fakeMachine(302, 3, 'Mill 2', 'CD-0302', {
        manufacturer: 'Haas',
        model: 'VF-4',
        serial_number: 'VF4-80233',
      }),
      fakeMachine(303, 3, 'Mill 3 — Horizontal Boring', 'CD-0303', {
        manufacturer: 'Toshiba',
        model: 'BTD-110',
        serial_number: 'BT11-40518',
      }),
      // Retired predecessor of machine 512 — same display name,
      // different physical asset with its own untouched identity.
      fakeMachine(104, 2, 'Lathe 1', 'CD-0104', {
        manufacturer: 'Mazak',
        model: 'QT-10',
        serial_number: 'Q10-61208',
        installed_on: '2012-06-01',
        notes:
          'Replaced by asset CD-0512 — display name reused for the floor position.',
        retired_on: '2026-02-14',
        state_changed_at: '2026-02-14T16:00:00.000Z',
      }),
      fakeMachine(202, 1, 'Saw 2', 'CD-0202', {
        manufacturer: 'Behringer',
        model: 'HBP-263A',
        installed_on: '2011-04-08',
        notes: 'Kept in storage — may return to service after overhaul.',
        retired_on: '2025-11-03',
        state_changed_at: '2025-11-03T09:30:00.000Z',
      }),
    ],
    events: [
      {
        id: 1,
        machine_id: 104,
        event_type: 'RETIRED',
        occurred_at: '2026-02-14T16:00:00.000Z',
        actor: 'M. Chen (Production Manager)',
        reason: 'Replaced by asset CD-0512',
        from_area_id: null,
        to_area_id: null,
      },
      {
        id: 2,
        machine_id: 202,
        event_type: 'RETIRED',
        occurred_at: '2025-11-03T09:30:00.000Z',
        actor: 'M. Chen (Production Manager)',
        reason: 'Gearbox failure — not economical to repair',
        from_area_id: null,
        to_area_id: null,
      },
    ],
    // Highest seeded sequence is CD-0512 → the counter stands at 513.
    format: { prefix: 'CD-', digits: 4, next_sequence: 513 },
    nextMachineId: 1000,
    nextEventId: 100,
  };
}

let state: FakeState;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function detail(message: string, status: number): Response {
  return json({ detail: message }, status);
}

function nextTag(): string {
  return `${state.format.prefix}${String(state.format.next_sequence).padStart(
    state.format.digits,
    '0',
  )}`;
}

function applyEdits(machine: FakeMachine, edits: Record<string, unknown>) {
  const text = (value: unknown) =>
    typeof value === 'string' && value.trim() ? value.trim() : null;
  if ('name' in edits) machine.name = String(edits.name).trim();
  if ('manufacturer' in edits) machine.manufacturer = text(edits.manufacturer);
  if ('model' in edits) machine.model = text(edits.model);
  if ('serial_number' in edits)
    machine.serial_number = text(edits.serial_number);
  if ('installed_on' in edits) machine.installed_on = text(edits.installed_on);
  if ('notes' in edits) machine.notes = text(edits.notes);
  if ('maintenance_note' in edits) {
    machine.maintenance_note = text(edits.maintenance_note);
  }
  if ('maintenance_expected_return' in edits) {
    machine.maintenance_expected_return = text(
      edits.maintenance_expected_return,
    );
  }
}

/** The server derives the operational state: maintenance > assigned
 * ACTIVE quantity = RUNNING > IDLE (PROJECT_PROFILE §8.6). */
function operationalState(
  machine: FakeMachine,
): 'MAINTENANCE' | 'RUNNING' | 'IDLE' {
  if (machine.operational_state !== undefined) return machine.operational_state;
  if (machine.maintenance_since !== null) return 'MAINTENANCE';
  return (machine.assigned_quantity ?? 0) > 0 ? 'RUNNING' : 'IDLE';
}

function machineWire(machine: FakeMachine) {
  return {
    ...machine,
    operational_state: operationalState(machine),
    barcode_value: `PF:MACHINE:${machine.asset_tag}`,
    created_at: T0,
    updated_at: T0,
  };
}

function nameCollision(machine: FakeMachine): boolean {
  return state.machines.some(
    (other) =>
      other.id !== machine.id &&
      other.retired_on === null &&
      other.area_id === machine.area_id &&
      other.name === machine.name,
  );
}

/** In-memory fake of the /api surface the Machines view uses. */
async function handle(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? 'GET';
  const body =
    typeof init?.body === 'string'
      ? (JSON.parse(init.body) as Record<string, unknown>)
      : {};
  if (url === '/api/health') return json({ status: 'ok' });
  if (url === '/api/areas') return json(state.areas);
  if (url === '/api/barcode-configuration/machine-asset-tag-format') {
    return json({ ...state.format, created_at: T0, updated_at: T0 });
  }
  if (url === '/api/machines' && method === 'GET') {
    return json(state.machines.map(machineWire));
  }
  if (url === '/api/machines' && method === 'POST') {
    const expected = body.expected_asset_tag;
    if (typeof expected === 'string' && expected !== nextTag()) {
      return detail(
        `The previewed Asset Tag is out of date — the next Asset Tag is now ${nextTag()}.`,
        409,
      );
    }
    const machine = fakeMachine(
      state.nextMachineId++,
      Number(body.area_id),
      String(body.name).trim(),
      nextTag(),
      { state_changed_at: NOW },
    );
    applyEdits(machine, body);
    if (nameCollision(machine)) {
      return detail(
        `An active Machine named “${machine.name}” already exists in this Area.`,
        409,
      );
    }
    state.format.next_sequence += 1;
    state.machines.push(machine);
    return json(machineWire(machine), 201);
  }
  const machineMatch = /^\/api\/machines\/(\d+)(\/.*)?$/.exec(url);
  if (machineMatch) {
    const machine = state.machines.find(
      (m) => m.id === Number(machineMatch[1]),
    );
    if (!machine) return detail('Machine not found.', 404);
    const rest = machineMatch[2] ?? '';
    if (rest === '' && method === 'PATCH') {
      const before = { ...machine };
      applyEdits(machine, body);
      if (nameCollision(machine)) {
        Object.assign(machine, before);
        return detail(
          `An active Machine named “${String(body.name)}” already exists in this Area.`,
          409,
        );
      }
      return json(machineWire(machine));
    }
    if (rest === '/maintenance' && method === 'POST') {
      machine.maintenance_since = NOW;
      machine.maintenance_note =
        typeof body.note === 'string' && body.note ? body.note : null;
      machine.maintenance_expected_return =
        typeof body.expected_return === 'string' && body.expected_return
          ? body.expected_return
          : null;
      machine.state_changed_at = NOW;
      return json(machineWire(machine), 201);
    }
    if (rest === '/maintenance' && method === 'DELETE') {
      machine.maintenance_since = null;
      machine.maintenance_note = null;
      machine.maintenance_expected_return = null;
      machine.state_changed_at = NOW;
      return json(machineWire(machine));
    }
    if (rest === '/retire' && method === 'POST') {
      if (body.edits && typeof body.edits === 'object') {
        applyEdits(machine, body.edits as Record<string, unknown>);
      }
      machine.retired_on = TODAY;
      machine.maintenance_since = null;
      machine.maintenance_note = null;
      machine.maintenance_expected_return = null;
      machine.state_changed_at = NOW;
      state.events.push({
        id: state.nextEventId++,
        machine_id: machine.id,
        event_type: 'RETIRED',
        occurred_at: NOW,
        actor: null,
        reason: null,
        from_area_id: null,
        to_area_id: null,
      });
      return json(machineWire(machine));
    }
    if (rest === '/reactivate' && method === 'POST') {
      const fromArea = machine.area_id;
      const toArea =
        body.area_id !== undefined && body.area_id !== null
          ? Number(body.area_id)
          : machine.area_id;
      machine.retired_on = null;
      machine.area_id = toArea;
      if (typeof body.name === 'string') machine.name = body.name.trim();
      machine.maintenance_since = null;
      machine.maintenance_note = null;
      machine.maintenance_expected_return = null;
      machine.state_changed_at = NOW;
      state.events.push({
        id: state.nextEventId++,
        machine_id: machine.id,
        event_type: 'REACTIVATED',
        occurred_at: NOW,
        actor: null,
        reason: typeof body.reason === 'string' ? body.reason : null,
        from_area_id: fromArea !== toArea ? fromArea : null,
        to_area_id: fromArea !== toArea ? toArea : null,
      });
      return json(machineWire(machine));
    }
    if (rest === '/lifecycle-events' && method === 'GET') {
      return json(state.events.filter((e) => e.machine_id === machine.id));
    }
  }
  return detail(`Unhandled fake route: ${method} ${url}`, 500);
}

beforeEach(() => {
  window.history.replaceState({}, '', '/management/machines');
  state = seedState();
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

/** Render Machines with a fixed connectivity status and wait for the
 * server data to arrive. Defaults to `connected` so the behavioral
 * tests exercise a fully-enabled view; offline-specific tests pass
 * `'unavailable'` explicitly (reading stays available offline). */
async function renderMachines(
  status: 'connected' | 'unavailable' = 'connected',
) {
  const utils = render(
    <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
      <MachinesView />
    </ConnectivityContext.Provider>,
  );
  await screen.findByText('Saw 1');
  return utils;
}

/** The active-Machines table row whose first cell names the Machine. */
function activeRow(name: string): HTMLElement {
  const table = document.querySelectorAll('.mg-table')[0];
  const row = Array.from(table.querySelectorAll('tbody tr')).find(
    (tr) => tr.querySelector('.mgname')?.textContent === name,
  );
  expect(row).toBeDefined();
  return row as HTMLElement;
}

/** The Retired Machines table (renders below the active table). */
function retiredTable(): HTMLElement {
  const table = document.querySelectorAll('.mg-table')[1];
  expect(table).toBeDefined();
  return table as HTMLElement;
}

function maintenanceSwitch(name: string): HTMLElement {
  return within(activeRow(name)).getByRole('switch', {
    name: `Maintenance — ${name}`,
  });
}

/** Open Edit Machine through the whole-row click (v15). */
function openEdit(name: string): HTMLElement {
  fireEvent.click(activeRow(name));
  return screen.getByRole('dialog', { name: 'Edit Machine' });
}

test('active Machines list the derived state with the time in state', async () => {
  await renderMachines();

  // The state is derived, never chosen: no assigned quantity and no
  // maintenance override → Idle, with the age derived from the shared
  // stateChangedAt timestamp.
  const lathe2 = activeRow('Lathe 2');
  expect(lathe2.querySelector('.mg-state')?.textContent).toMatch(/^Idle · /);
  expect(within(lathe2).getByRole('cell', { name: '—' })).toBeInTheDocument();

  // Assigned ACTIVE quantity reported by the server (Phase 6) → Running,
  // with the total shown in the Assigned now column.
  const lathe1 = activeRow('Lathe 1');
  expect(lathe1.querySelector('.mg-state')?.textContent).toMatch(/^Running · /);
  expect(lathe1.textContent).toContain('40 pcs assigned');

  // Explicit maintenance override with its note and expected return.
  const lathe4 = activeRow('Lathe 4');
  expect(lathe4.querySelector('.mg-state')?.textContent).toMatch(
    /^Maintenance · /,
  );
  expect(lathe4.textContent).toContain('Spindle bearing replacement');
  expect(lathe4.textContent).toContain('Expected back 2026-08-06');
});

test('the state column shows the state the SERVER derived, never a local re-derivation', async () => {
  // The server is the single derivation of the operational state (the
  // same value the Scan Station Machine cards show): a Machine the
  // server reports as RUNNING renders Running even when the quantity
  // total alone would not say so, and vice versa.
  const lathe2 = state.machines.find((m) => m.name === 'Lathe 2')!;
  lathe2.operational_state = 'RUNNING';
  const lathe3 = state.machines.find((m) => m.name === 'Lathe 3')!;
  lathe3.assigned_quantity = 12;
  lathe3.operational_state = 'IDLE';
  await renderMachines();

  expect(activeRow('Lathe 2').querySelector('.mg-state')?.textContent).toMatch(
    /^Running · /,
  );
  const row3 = activeRow('Lathe 3');
  expect(row3.querySelector('.mg-state')?.textContent).toMatch(/^Idle · /);
  expect(row3.textContent).toContain('12 pcs assigned');
});

test('the replacement pair stays distinguishable: retired records keep their identity', async () => {
  await renderMachines();

  // The active `Lathe 1` is the replacement asset…
  expect(activeRow('Lathe 1').textContent).toContain('CD-0512');

  // …the retired predecessor keeps the SAME display name but its own
  // asset identity, retirement date and explanatory note.
  const retired = retiredTable();
  expect(retired.querySelectorAll('tbody tr')).toHaveLength(2);
  const retiredRow = within(retired)
    .getByText('Retired on 2026-02-14')
    .closest('tr') as HTMLElement;
  expect(retiredRow.querySelector('.mgname')?.textContent).toBe('Lathe 1');
  expect(retiredRow.textContent).toContain('CD-0104');
  expect(retiredRow.textContent).toContain('Replaced by asset CD-0512');
  // Historical display with exactly one entry point per row: the
  // read-only details dialog (whole row / name-cell button), which
  // carries the Reactivate entry — no row action, no edit, no delete.
  expect(within(retiredRow).getAllByRole('button')).toHaveLength(1);
  expect(
    within(retiredRow).getByRole('button', {
      name: 'Machine details — Lathe 1',
    }),
  ).toBeInTheDocument();

  // Second retired record: retired Machines keep their Asset Tag
  // forever — the tag is never reused by a later Machine.
  const saw2Row = within(retired)
    .getByText('Retired on 2025-11-03')
    .closest('tr') as HTMLElement;
  expect(saw2Row.querySelector('.mgname')?.textContent).toBe('Saw 2');
  expect(saw2Row.querySelector('.mg-assetline')?.textContent).toContain(
    'CD-0202',
  );
  expect(saw2Row.textContent).toContain(
    'Kept in storage — may return to service after overhaul.',
  );
});

test('the whole active row opens Edit Machine with the Area fixed', async () => {
  await renderMachines();

  const dialog = openEdit('Lathe 2');
  expect(within(dialog).getByLabelText('Display name')).toHaveValue('Lathe 2');
  // The identity header leads the dialog: Asset Tag and its derived
  // barcode as read-only values — there is no input for either — plus
  // the barcode-label entry.
  expect(within(dialog).queryByLabelText('Barcode value')).toBeNull();
  expect(within(dialog).queryByLabelText(/Asset tag/)).toBeNull();
  const identity = dialog.querySelector('.mg-idhead') as HTMLElement;
  expect(identity.textContent).toContain('CD-0105');
  expect(identity.textContent).toContain('PF:MACHINE:CD-0105');
  expect(
    within(identity).getByRole('button', { name: 'Barcode label…' }),
  ).toBeInTheDocument();
  // A Machine belongs to exactly one Area — no Area select on edit;
  // the fixed Area lives in the identity header with its plain-language
  // explanation.
  expect(within(dialog).queryByRole('combobox')).toBeNull();
  expect(identity.textContent).toContain(
    'The Area cannot be changed while the Machine is active.',
  );

  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('the Maintenance switch mirrors the real state and only opens the dialogs', async () => {
  await renderMachines();

  // aria-checked reflects the real state only.
  expect(maintenanceSwitch('Lathe 2')).toHaveAttribute('aria-checked', 'false');
  expect(maintenanceSwitch('Lathe 4')).toHaveAttribute('aria-checked', 'true');

  // Toggling opens Start Maintenance — the Maintenance cell stops
  // propagation, so the row's Edit dialog never opens with it.
  fireEvent.click(maintenanceSwitch('Lathe 2'));
  expect(screen.queryByRole('dialog', { name: 'Edit Machine' })).toBeNull();
  const dialog = screen.getByRole('dialog', { name: 'Start maintenance' });

  // A cancelled dialog leaves the switch (the real state) untouched.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(maintenanceSwitch('Lathe 2')).toHaveAttribute('aria-checked', 'false');
});

test('starting maintenance persists the override with its note', async () => {
  await renderMachines();

  fireEvent.click(maintenanceSwitch('Lathe 2'));
  const dialog = screen.getByRole('dialog', { name: 'Start maintenance' });
  // No production quantity exists before Phase 6 — the dialog states
  // the current truth (nothing is assigned, nothing moves).
  expect(dialog.textContent).toContain(
    'No quantity is currently assigned to it.',
  );
  fireEvent.change(within(dialog).getByLabelText(/Reason \/ note/), {
    target: { value: 'Coolant leak' },
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Start maintenance' }),
  );

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() => {
    const row = activeRow('Lathe 2');
    expect(row.querySelector('.mg-state')?.textContent).toMatch(
      /^Maintenance · /,
    );
    expect(row.textContent).toContain('Coolant leak');
  });
  expect(maintenanceSwitch('Lathe 2')).toHaveAttribute('aria-checked', 'true');
});

test('clearing maintenance returns the Machine to Idle', async () => {
  await renderMachines();

  fireEvent.click(maintenanceSwitch('Lathe 4'));
  const dialog = screen.getByRole('dialog', { name: 'Clear maintenance' });
  expect(dialog.textContent).toContain('Idle');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Clear maintenance' }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() =>
    expect(
      activeRow('Lathe 4').querySelector('.mg-state')?.textContent,
    ).toMatch(/^Idle · /),
  );
  expect(maintenanceSwitch('Lathe 4')).toHaveAttribute('aria-checked', 'false');
});

test('an idle Machine retires after typing its Asset Tag and a final summary — never deleted', async () => {
  await renderMachines();

  const edit = openEdit('Mill 3 — Horizontal Boring');
  // Nothing assigned: the Danger Zone shows the consequences in its
  // soft error reading tone and Retire is enabled.
  expect(edit.querySelector('.dz-live')?.textContent).toContain(
    'removes it from every assignment choice',
  );
  expect(within(edit).getByRole('button', { name: 'Retire…' })).toBeEnabled();
  fireEvent.click(within(edit).getByRole('button', { name: 'Retire…' }));

  const confirm = screen.getByRole('dialog', { name: 'Retire Machine' });
  expect(confirm.textContent).toContain(
    'It disappears from Machine assignment choices.',
  );
  expect(confirm.textContent).toContain('no longer accepts assignment scans');
  expect(confirm.textContent).toContain('nothing is deleted');
  expect(confirm.textContent).toContain('The record moves to Retired Machines');

  // Continue stays disabled until the Asset Tag is typed (trim +
  // case-insensitive deliberate acknowledgement).
  const continueButton = within(confirm).getByRole('button', {
    name: 'Continue',
  });
  const gate = within(confirm).getByLabelText(/to confirm$/);
  // The typed gate is the dialog's task — it receives initial focus.
  expect(gate).toHaveFocus();
  expect(gate).toHaveAttribute('placeholder', 'CD-0303');
  expect(continueButton).toBeDisabled();
  fireEvent.change(gate, { target: { value: 'CD-0304' } });
  expect(continueButton).toBeDisabled();
  fireEvent.change(gate, { target: { value: '  cd-0303 ' } });
  expect(continueButton).toBeEnabled();
  fireEvent.click(continueButton);

  // The typed confirmation leads to a final summary (v17) — nothing
  // has been retired yet.
  const summary = screen.getByRole('dialog', { name: 'Confirm retirement' });
  expect(summary.textContent).toContain('Mill 3 — Horizontal Boring');
  expect(summary.textContent).toContain('CD-0303');
  expect(summary.textContent).toContain('nothing has changed yet');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Retire Machine' }),
  );

  // The summary's action asks one last explicit question — the
  // retirement is permanently recorded in Machine history.
  const ask = screen.getByRole('dialog', { name: 'Retire this Machine?' });
  expect(ask.textContent).toContain('permanently recorded in Machine history');
  fireEvent.click(within(ask).getByRole('button', { name: 'Retire Machine' }));

  // Gone from the active table…
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() => {
    const activeTable = document.querySelectorAll('.mg-table')[0];
    expect(activeTable.textContent).not.toContain('Mill 3 — Horizontal Boring');
  });
  // …and present under Retired Machines with its asset metadata.
  const retired = retiredTable();
  expect(retired.textContent).toContain('Mill 3 — Horizontal Boring');
  expect(retired.textContent).toContain('CD-0303');
});

test('the retire edits decision is recorded, not applied — cancelling later keeps the edits', async () => {
  await renderMachines();

  const edit = openEdit('Mill 3 — Horizontal Boring');
  fireEvent.change(within(edit).getByLabelText('Notes (optional)'), {
    target: { value: 'Pending disposal review' },
  });
  expect(edit.textContent).toContain('● Unsaved changes');
  fireEvent.click(within(edit).getByRole('button', { name: 'Retire…' }));

  // Discard is a DECISION for the retirement, not an immediate reset.
  const unsaved = screen.getByRole('dialog', { name: 'Unsaved changes' });
  expect(
    within(unsaved).getByRole('button', { name: 'Save changes' }),
  ).toBeInTheDocument();
  fireEvent.click(
    within(unsaved).getByRole('button', { name: 'Discard changes' }),
  );

  // Cancelling the typed confirmation returns to the form with the
  // edits still in place.
  const confirm = screen.getByRole('dialog', { name: 'Retire Machine' });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  const editAgain = screen.getByRole('dialog', { name: 'Edit Machine' });
  expect(within(editAgain).getByLabelText('Notes (optional)')).toHaveValue(
    'Pending disposal review',
  );
  expect(editAgain.textContent).toContain('● Unsaved changes');
  // Nothing reached the server.
  expect(state.machines.find((m) => m.id === 303)?.notes).toBeNull();
});

test('a recorded Save decision travels with the retirement and commits with it', async () => {
  await renderMachines();

  const edit = openEdit('Mill 3 — Horizontal Boring');
  fireEvent.change(within(edit).getByLabelText('Notes (optional)'), {
    target: { value: 'Sold for scrap' },
  });
  fireEvent.click(within(edit).getByRole('button', { name: 'Retire…' }));
  const unsaved = screen.getByRole('dialog', { name: 'Unsaved changes' });
  fireEvent.click(
    within(unsaved).getByRole('button', { name: 'Save changes' }),
  );

  const confirm = screen.getByRole('dialog', { name: 'Retire Machine' });
  fireEvent.change(within(confirm).getByLabelText(/to confirm$/), {
    target: { value: 'CD-0303' },
  });
  fireEvent.click(within(confirm).getByRole('button', { name: 'Continue' }));

  // The summary names the recorded decision before anything happens.
  const summary = screen.getByRole('dialog', { name: 'Confirm retirement' });
  expect(summary.textContent).toContain('Saved with the retirement');
  // The edits are still only recorded — not sent.
  expect(state.machines.find((m) => m.id === 303)?.notes).toBeNull();
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Retire Machine' }),
  );
  const ask = screen.getByRole('dialog', { name: 'Retire this Machine?' });
  fireEvent.click(within(ask).getByRole('button', { name: 'Retire Machine' }));

  // The edits were applied together with the retirement (one request).
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() =>
    expect(retiredTable().textContent).toContain('Sold for scrap'),
  );
  const mill3 = state.machines.find((m) => m.id === 303)!;
  expect(mill3.notes).toBe('Sold for scrap');
  expect(mill3.retired_on).not.toBeNull();
});

test('reactivation blocks on a name collision until a rename, then returns the Machine as Idle', async () => {
  await renderMachines();

  // Reactivate is entered through the Retired Machine Details dialog.
  const lathe1Row = within(retiredTable())
    .getByText('Retired on 2026-02-14')
    .closest('tr') as HTMLElement;
  fireEvent.click(lathe1Row);
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Retired Machine Details' }),
    ).getByRole('button', { name: 'Reactivate' }),
  );

  const dialog = screen.getByRole('dialog', { name: 'Reactivate Machine' });
  // The identity header leads the dialog (like New/Edit Machine): the
  // untouched Asset Tag, its derived barcode and the asset metadata.
  const identity = dialog.querySelector('.mg-idhead') as HTMLElement;
  expect(identity.textContent).toContain('CD-0104');
  expect(identity.textContent).toContain('PF:MACHINE:CD-0104');
  expect(identity.textContent).toContain('Mazak');
  expect(identity.textContent).toContain('QT-10');
  expect(identity.textContent).toContain('S/N Q10-61208');
  // The same-physical acknowledgement sits on the warning Important
  // panel — a be-sure marker, not an error.
  expect(
    dialog.querySelector('.mg-confirmpanel .cp-head')?.textContent,
  ).toContain('Important');
  // The Display name takes initial focus — it is the field most
  // likely to need attention.
  expect(within(dialog).getByLabelText('Display name')).toHaveFocus();
  // The reused floor-position name collides with the active replacement
  // `Lathe 1` in the same Area — the error sits in the name column,
  // marked with the ✕ glyph.
  expect(dialog.textContent).toContain('✕ “Lathe 1” already exists in Lathe');

  // A required reason alone is not enough while the collision stands —
  // Continue stays on the form; the collision message remains the ONE
  // name-column error (no duplicate catch-all block).
  fireEvent.change(within(dialog).getByLabelText('Reason (required)'), {
    target: { value: 'Returned from overhaul' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  expect(
    screen.getByRole('dialog', { name: 'Reactivate Machine' }),
  ).toBeInTheDocument();
  expect(dialog.querySelectorAll('.err')).toHaveLength(2);
  expect(dialog.textContent).toContain('✕ “Lathe 1” already exists in Lathe');

  // Renaming inside the dialog resolves the collision — the name
  // column switches to the availability confirmation…
  fireEvent.change(within(dialog).getByLabelText('Display name'), {
    target: { value: 'Lathe 1B' },
  });
  expect(dialog.textContent).not.toContain('already exists in Lathe');
  expect(dialog.textContent).toContain('“Lathe 1B” is available in Lathe');
  // The Return Area select sits beside the name with its move note.
  expect(within(dialog).getByLabelText('Return Area')).toBeInTheDocument();
  expect(dialog.textContent).toContain(
    'Change only if the physical Machine moved while retired.',
  );
  // …and the same-physical-machine confirmation stays required.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  expect(dialog.textContent).toContain(
    'Confirm that this is the same physical machine.',
  );
  fireEvent.click(within(dialog).getByRole('checkbox'));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));

  // The form leads to a final summary (v17) — reactivation happens
  // only on its confirmation.
  const summary = screen.getByRole('dialog', { name: 'Confirm reactivation' });
  expect(summary.textContent).toContain('Lathe 1B');
  expect(summary.textContent).toContain('Returned from overhaul');
  expect(summary.textContent).toContain('Same physical machine confirmed');
  expect(summary.textContent).toContain('Idle');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Reactivate Machine' }),
  );

  // One last explicit question — reactivation is permanently recorded
  // in Machine history.
  const ask = screen.getByRole('dialog', { name: 'Reactivate this Machine?' });
  expect(ask.textContent).toContain('permanently recorded in Machine history');
  fireEvent.click(
    within(ask).getByRole('button', { name: 'Reactivate Machine' }),
  );

  // The Machine returns as Idle (running stays derived).
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() => {
    const row = activeRow('Lathe 1B');
    expect(row.querySelector('.mg-state')?.textContent).toMatch(/^Idle · /);
  });

  // The lifecycle audit keeps both events, append-only — presented in
  // the shared timeline style, in its compact one-line-per-event
  // variant inside Edit Machine. The reactivation reason is recorded;
  // actor identity arrives with authentication (Phase 14).
  fireEvent.click(activeRow('Lathe 1B'));
  const edit = screen.getByRole('dialog', { name: 'Edit Machine' });
  await waitFor(() =>
    expect(edit.querySelectorAll('.mg-tlevent').length).toBe(2),
  );
  expect(edit.querySelector('.mg-timeline')).toHaveClass('compact');
  const events = edit.querySelectorAll('.mg-tlevent');
  expect(events[0].textContent).toContain('Retired');
  expect(events[0].textContent).toContain('M. Chen (Production Manager)');
  expect(events[1].textContent).toContain('Reactivated');
  expect(events[1].textContent).toContain('Returned from overhaul');
});

test('a reactivated Machine keeps its Asset Tag and confirms retirement with it', async () => {
  await renderMachines();

  // Reactivate Saw 2 first (no identity conflicts, no name collision)
  // — entered through the Retired Machine Details dialog.
  const saw2Row = within(retiredTable())
    .getByText('Retired on 2025-11-03')
    .closest('tr') as HTMLElement;
  fireEvent.click(saw2Row);
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Retired Machine Details' }),
    ).getByRole('button', { name: 'Reactivate' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Reactivate Machine' });
  expect(dialog.querySelector('.mg-blockers')).toBeNull();
  fireEvent.change(within(dialog).getByLabelText('Reason (required)'), {
    target: { value: 'Back from gearbox overhaul' },
  });
  fireEvent.click(within(dialog).getByRole('checkbox'));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  const reactSummary = screen.getByRole('dialog', {
    name: 'Confirm reactivation',
  });
  // The summary recaps the untouched identity: Asset Tag and the
  // barcode derived from it.
  expect(reactSummary.textContent).toContain('CD-0202');
  expect(reactSummary.textContent).toContain('PF:MACHINE:CD-0202');
  fireEvent.click(
    within(reactSummary).getByRole('button', { name: 'Reactivate Machine' }),
  );
  const reactAsk = screen.getByRole('dialog', {
    name: 'Reactivate this Machine?',
  });
  fireEvent.click(
    within(reactAsk).getByRole('button', { name: 'Reactivate Machine' }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() =>
    expect(activeRow('Saw 2').querySelector('.mg-state')?.textContent).toMatch(
      /^Idle · /,
    ),
  );

  // Retire it again: the typed confirmation is always the Asset Tag —
  // never the reusable display name.
  const edit = openEdit('Saw 2');
  fireEvent.click(within(edit).getByRole('button', { name: 'Retire…' }));
  const confirm = screen.getByRole('dialog', { name: 'Retire Machine' });
  expect(confirm.textContent).toContain('(Asset Tag) to confirm');
  const gate = within(confirm).getByLabelText(/to confirm$/);
  expect(gate).toHaveAttribute('placeholder', 'CD-0202');
  const continueButton = within(confirm).getByRole('button', {
    name: 'Continue',
  });
  expect(continueButton).toBeDisabled();
  fireEvent.change(gate, { target: { value: 'cd-0202' } });
  expect(continueButton).toBeEnabled();
  fireEvent.click(continueButton);
  const summary = screen.getByRole('dialog', { name: 'Confirm retirement' });
  expect(summary.textContent).toContain('CD-0202');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Retire Machine' }),
  );
  const retireAsk = screen.getByRole('dialog', {
    name: 'Retire this Machine?',
  });
  fireEvent.click(
    within(retireAsk).getByRole('button', { name: 'Retire Machine' }),
  );

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() => expect(retiredTable().textContent).toContain('Saw 2'));
});

test('a new Machine previews the next Asset Tag and is added only after summary + confirmation', async () => {
  await renderMachines();

  fireEvent.click(screen.getByRole('button', { name: '+ New Machine' }));
  const dialog = screen.getByRole('dialog', { name: 'New Machine' });
  // No manual identity entry exists: the preview shows the next tag of
  // the configured format (the server-persisted counter stands at 513)
  // and the barcode derives from it. The identity header leads the
  // dialog.
  expect(within(dialog).queryByLabelText('Barcode value')).toBeNull();
  expect(within(dialog).queryByLabelText(/Asset tag/)).toBeNull();
  const identity = dialog.querySelector('.mg-idhead') as HTMLElement;
  expect(identity.textContent).toContain('CD-0513');
  expect(identity.textContent).toContain('PF:MACHINE:CD-0513');
  // A new Machine has no barcode label yet — the label entry is for
  // existing Machines only.
  expect(
    within(dialog).queryByRole('button', { name: 'Barcode label…' }),
  ).toBeNull();
  // The Area select shares the form with the Display name.
  expect(within(dialog).getByRole('combobox')).toBeInTheDocument();

  fireEvent.change(within(dialog).getByLabelText('Display name'), {
    target: { value: 'Lathe 5' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));

  // Continue leads to a summary — nothing has been added yet.
  const summary = screen.getByRole('dialog', { name: 'Confirm new Machine' });
  expect(summary.textContent).toContain('nothing has been added yet');
  expect(summary.textContent).toContain('Lathe 5');
  expect(summary.textContent).toContain('CD-0513');
  expect(summary.textContent).toContain('PF:MACHINE:CD-0513');
  fireEvent.click(within(summary).getByRole('button', { name: 'Add Machine' }));

  // The summary's action asks one last explicit question — a Machine
  // record can never be deleted, only retired.
  const ask = screen.getByRole('dialog', { name: 'Add this Machine?' });
  expect(ask.textContent).toContain('can only be retired later');
  fireEvent.click(within(ask).getByRole('button', { name: 'Add Machine' }));

  // The new Machine starts Idle and carries the server-assigned tag —
  // the previewed value travelled as the optimistic precondition.
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() => {
    const row = activeRow('Lathe 5');
    expect(row.querySelector('.mg-state')?.textContent).toMatch(/^Idle · /);
    expect(row.querySelector('.mg-assetline')?.textContent).toContain(
      'CD-0513',
    );
  });
  // The counter advanced server-side.
  expect(state.format.next_sequence).toBe(514);
});

test('cancelling the add confirmation returns to the summary, then the form — nothing added', async () => {
  await renderMachines();

  fireEvent.click(screen.getByRole('button', { name: '+ New Machine' }));
  const dialog = screen.getByRole('dialog', { name: 'New Machine' });
  fireEvent.change(within(dialog).getByLabelText('Display name'), {
    target: { value: 'Lathe 5' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  const summary = screen.getByRole('dialog', { name: 'Confirm new Machine' });
  fireEvent.click(within(summary).getByRole('button', { name: 'Add Machine' }));
  const ask = screen.getByRole('dialog', { name: 'Add this Machine?' });
  fireEvent.click(within(ask).getByRole('button', { name: 'Cancel (Esc)' }));

  // Back on the summary; Back returns to the form with its input kept.
  const summaryAgain = screen.getByRole('dialog', {
    name: 'Confirm new Machine',
  });
  fireEvent.click(within(summaryAgain).getByRole('button', { name: 'Back' }));
  expect(
    within(screen.getByRole('dialog', { name: 'New Machine' })).getByLabelText(
      'Display name',
    ),
  ).toHaveValue('Lathe 5');
  // Nothing was added.
  const activeTable = document.querySelectorAll('.mg-table')[0];
  expect(activeTable.textContent).not.toContain('Lathe 5');
  expect(state.machines.some((m) => m.name === 'Lathe 5')).toBe(false);
});

test('the New Machine dialog focuses Display name; Edit Machine claims no field', async () => {
  await renderMachines();

  fireEvent.click(screen.getByRole('button', { name: '+ New Machine' }));
  const dialog = screen.getByRole('dialog', { name: 'New Machine' });
  expect(within(dialog).getByLabelText('Display name')).toHaveFocus();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));

  const edit = openEdit('Lathe 2');
  expect(within(edit).getByLabelText('Display name')).not.toHaveFocus();
  fireEvent.click(within(edit).getByRole('button', { name: 'Cancel (Esc)' }));
});

test('cancelling Edit Machine with unsaved edits asks to save first', async () => {
  await renderMachines();

  const edit = openEdit('Mill 3 — Horizontal Boring');
  fireEvent.change(within(edit).getByLabelText('Notes (optional)'), {
    target: { value: 'Coolant flushed 2026' },
  });
  fireEvent.click(within(edit).getByRole('button', { name: 'Cancel (Esc)' }));

  // The decision comes first; Cancel returns with the edits intact.
  const ask = screen.getByRole('dialog', { name: 'Unsaved changes' });
  fireEvent.click(within(ask).getByRole('button', { name: 'Cancel (Esc)' }));
  const editAgain = screen.getByRole('dialog', { name: 'Edit Machine' });
  expect(within(editAgain).getByLabelText('Notes (optional)')).toHaveValue(
    'Coolant flushed 2026',
  );

  // Save changes saves through the API and closes; reopening shows the
  // persisted value.
  fireEvent.click(
    within(editAgain).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  fireEvent.click(
    within(screen.getByRole('dialog', { name: 'Unsaved changes' })).getByRole(
      'button',
      { name: 'Save changes' },
    ),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(state.machines.find((m) => m.id === 303)?.notes).toBe(
    'Coolant flushed 2026',
  );
  await waitFor(() =>
    expect(activeRow('Mill 3 — Horizontal Boring')).toBeTruthy(),
  );
  const reopened = openEdit('Mill 3 — Horizontal Boring');
  expect(within(reopened).getByLabelText('Notes (optional)')).toHaveValue(
    'Coolant flushed 2026',
  );
});

test('discarding on cancel never saves; a dirty New Machine confirms the discard', async () => {
  await renderMachines();

  const edit = openEdit('Mill 3 — Horizontal Boring');
  fireEvent.change(within(edit).getByLabelText('Notes (optional)'), {
    target: { value: 'Never saved' },
  });
  fireEvent.click(within(edit).getByRole('button', { name: 'Cancel (Esc)' }));
  fireEvent.click(
    within(screen.getByRole('dialog', { name: 'Unsaved changes' })).getByRole(
      'button',
      { name: 'Discard changes' },
    ),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(state.machines.find((m) => m.id === 303)?.notes).toBeNull();
  const reopened = openEdit('Mill 3 — Horizontal Boring');
  expect(within(reopened).getByLabelText('Notes (optional)')).toHaveValue('');
  fireEvent.click(
    within(reopened).getByRole('button', { name: 'Cancel (Esc)' }),
  );

  // New Machine with entered input: closing asks before discarding.
  fireEvent.click(screen.getByRole('button', { name: '+ New Machine' }));
  const dialog = screen.getByRole('dialog', { name: 'New Machine' });
  fireEvent.change(within(dialog).getByLabelText('Display name'), {
    target: { value: 'Lathe 9' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  const discardAsk = screen.getByRole('dialog', {
    name: 'Discard new Machine?',
  });
  expect(discardAsk.textContent).toContain(
    'Your entered information will not be saved',
  );
  // Keep editing returns with the input intact…
  fireEvent.click(
    within(discardAsk).getByRole('button', { name: 'Keep editing' }),
  );
  expect(
    within(screen.getByRole('dialog', { name: 'New Machine' })).getByLabelText(
      'Display name',
    ),
  ).toHaveValue('Lathe 9');
  // …Discard input closes without adding anything.
  fireEvent.click(
    within(screen.getByRole('dialog', { name: 'New Machine' })).getByRole(
      'button',
      { name: 'Cancel (Esc)' },
    ),
  );
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Discard new Machine?' }),
    ).getByRole('button', { name: 'Discard input' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  const activeTable = document.querySelectorAll('.mg-table')[0];
  expect(activeTable.textContent).not.toContain('Lathe 9');
  expect(state.machines.some((m) => m.name === 'Lathe 9')).toBe(false);
});

test('a new Machine cannot reuse an active display name of the same Area', async () => {
  await renderMachines();

  fireEvent.click(screen.getByRole('button', { name: '+ New Machine' }));
  const dialog = screen.getByRole('dialog', { name: 'New Machine' });
  // The default Area is Cut (first active non-terminal Area) — pick
  // Lathe, where an active `Lathe 2` exists.
  fireEvent.change(within(dialog).getByRole('combobox'), {
    target: { value: '2' },
  });
  fireEvent.change(within(dialog).getByLabelText('Display name'), {
    target: { value: 'Lathe 2' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  expect(within(dialog).getByRole('alert').textContent).toContain(
    'already exists in Lathe',
  );
  expect(
    screen.queryByRole('dialog', { name: 'Confirm new Machine' }),
  ).toBeNull();
});

test('a stale Asset Tag preview is rejected by the server and consumes nothing', async () => {
  await renderMachines();

  fireEvent.click(screen.getByRole('button', { name: '+ New Machine' }));
  const dialog = screen.getByRole('dialog', { name: 'New Machine' });
  expect(dialog.textContent).toContain('CD-0513');

  // Another client consumes the previewed tag while the dialog is open.
  state.format.next_sequence = 514;

  fireEvent.change(within(dialog).getByLabelText('Display name'), {
    target: { value: 'Lathe 5' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
  const summary = screen.getByRole('dialog', { name: 'Confirm new Machine' });
  fireEvent.click(within(summary).getByRole('button', { name: 'Add Machine' }));
  fireEvent.click(
    within(screen.getByRole('dialog', { name: 'Add this Machine?' })).getByRole(
      'button',
      { name: 'Add Machine' },
    ),
  );

  // The server rejects the stale precondition — the summary stays open
  // with the server's message, and nothing was created or consumed.
  const message = await within(
    screen.getByRole('dialog', { name: 'Confirm new Machine' }),
  ).findByRole('alert');
  expect(message.textContent).toContain('out of date');
  expect(state.machines.some((m) => m.name === 'Lathe 5')).toBe(false);
  expect(state.format.next_sequence).toBe(514);
});

/** Display names of the active table, in row order. */
function activeNames(): (string | null)[] {
  const table = document.querySelectorAll('.mg-table')[0];
  return Array.from(table.querySelectorAll('tbody .mgname')).map(
    (cell) => cell.textContent,
  );
}

test('column headers sort the active table and cycle ascending → descending → unsorted', async () => {
  await renderMachines();

  const original = [
    'Saw 1',
    'Lathe 1',
    'Lathe 2',
    'Lathe 3',
    'Lathe 4',
    'Mill 1',
    'Mill 2',
    'Mill 3 — Horizontal Boring',
  ];
  expect(activeNames()).toEqual(original);

  const byMachine = screen.getByRole('button', { name: 'Sort by Machine' });
  // Unsorted headers carry the neutral both-ways arrow, no emphasis.
  expect(byMachine).not.toHaveClass('on');
  expect(byMachine.textContent).toContain('↕');

  fireEvent.click(byMachine);
  expect(activeNames()).toEqual([
    'Lathe 1',
    'Lathe 2',
    'Lathe 3',
    'Lathe 4',
    'Mill 1',
    'Mill 2',
    'Mill 3 — Horizontal Boring',
    'Saw 1',
  ]);
  expect(byMachine).toHaveClass('on');
  expect(byMachine.textContent).toContain('↑');
  expect(byMachine.closest('th')).toHaveAttribute('aria-sort', 'ascending');

  fireEvent.click(byMachine);
  expect(activeNames()).toEqual([
    'Saw 1',
    'Mill 3 — Horizontal Boring',
    'Mill 2',
    'Mill 1',
    'Lathe 4',
    'Lathe 3',
    'Lathe 2',
    'Lathe 1',
  ]);
  expect(byMachine.textContent).toContain('↓');
  expect(byMachine.closest('th')).toHaveAttribute('aria-sort', 'descending');

  // Third click returns to the unsorted registry order.
  fireEvent.click(byMachine);
  expect(activeNames()).toEqual(original);
  expect(byMachine).not.toHaveClass('on');
  expect(byMachine.closest('th')).not.toHaveAttribute('aria-sort');
});

test('State sorts the derived state — working machines first, Maintenance last, ties in name order', async () => {
  await renderMachines();

  // Running (Lathe 1 holds assigned quantity) first, then the Idle
  // machines by name, the Maintenance override last.
  fireEvent.click(screen.getByRole('button', { name: 'Sort by State' }));
  expect(activeNames()).toEqual([
    'Lathe 1',
    'Lathe 2',
    'Lathe 3',
    'Mill 1',
    'Mill 2',
    'Mill 3 — Horizontal Boring',
    'Saw 1',
    'Lathe 4',
  ]);
  fireEvent.click(screen.getByRole('button', { name: 'Sort by State' }));
  expect(activeNames()).toEqual([
    'Lathe 4',
    'Lathe 2',
    'Lathe 3',
    'Mill 1',
    'Mill 2',
    'Mill 3 — Horizontal Boring',
    'Saw 1',
    'Lathe 1',
  ]);
});

test('the Retired Machines table sorts independently through its own headers', async () => {
  await renderMachines();

  /** Display names of the retired table, in row order. */
  const retiredNames = () =>
    Array.from(retiredTable().querySelectorAll('tbody .mgname')).map(
      (cell) => cell.textContent,
    );
  // Registry order: the retired Lathe 1 (2026-02-14), then Saw 2
  // (2025-11-03).
  expect(retiredNames()).toEqual(['Lathe 1', 'Saw 2']);

  // Ascending by retirement date puts the older retirement first.
  const byRetired = screen.getByRole('button', {
    name: 'Sort Retired Machines by Retired',
  });
  fireEvent.click(byRetired);
  expect(retiredNames()).toEqual(['Saw 2', 'Lathe 1']);
  expect(byRetired).toHaveClass('on');
  expect(byRetired.closest('th')).toHaveAttribute('aria-sort', 'ascending');

  fireEvent.click(byRetired);
  expect(retiredNames()).toEqual(['Lathe 1', 'Saw 2']);
  expect(byRetired.closest('th')).toHaveAttribute('aria-sort', 'descending');

  // Third click returns to the registry order; the active table's own
  // sort state is untouched throughout.
  fireEvent.click(byRetired);
  expect(retiredNames()).toEqual(['Lathe 1', 'Saw 2']);
  expect(byRetired).not.toHaveClass('on');
  expect(
    screen.getByRole('button', { name: 'Sort by Machine' }),
  ).not.toHaveClass('on');
});

test('the Retired Machines columns order Machine, Retired, Asset, Notes — no action column', async () => {
  await renderMachines();

  const headers = Array.from(
    retiredTable().querySelectorAll('thead th'),
    (th) => th.textContent?.replace(/[↕↑↓]/g, '').trim(),
  );
  expect(headers).toEqual(['Machine', 'Retired', 'Asset', 'Notes']);
});

test('a retired row opens the read-only Retired Machine Details dialog with the lifecycle', async () => {
  await renderMachines();

  const lathe1Row = within(retiredTable())
    .getByText('Retired on 2026-02-14')
    .closest('tr') as HTMLElement;
  fireEvent.click(lathe1Row);

  const dialog = screen.getByRole('dialog', {
    name: 'Retired Machine Details',
  });
  // Identity and the untouched asset metadata of the record.
  expect(dialog.textContent).toContain('Lathe 1');
  expect(dialog.textContent).toContain('Retired on 2026-02-14');
  expect(dialog.textContent).toContain('CD-0104');
  expect(dialog.textContent).toContain('PF:MACHINE:CD-0104');
  expect(dialog.textContent).toContain('Mazak');
  expect(dialog.textContent).toContain('QT-10');
  expect(dialog.textContent).toContain('Q10-61208');
  expect(dialog.textContent).toContain(
    'display name reused for the floor position',
  );
  // The lifecycle timeline presents the append-only audit events,
  // loaded from the lifecycle-history endpoint.
  await waitFor(() =>
    expect(dialog.querySelectorAll('.mg-tlevent').length).toBe(1),
  );
  const events = dialog.querySelectorAll('.mg-tlevent');
  expect(events[0].textContent).toContain('Retired');
  expect(events[0].textContent).toContain('2026-02-14');
  expect(events[0].textContent).toContain('M. Chen (Production Manager)');
  expect(events[0].textContent).toContain('Replaced by asset CD-0512');
  // Read-only: no inputs — Close and the Reactivate entry only.
  expect(dialog.querySelector('input')).toBeNull();

  // Close changes nothing.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(retiredTable().textContent).toContain('Retired on 2026-02-14');
});

test('the details dialog leads to Reactivate and ‹ Back returns to the details', async () => {
  await renderMachines();

  const saw2Row = within(retiredTable())
    .getByText('Retired on 2025-11-03')
    .closest('tr') as HTMLElement;
  fireEvent.click(saw2Row);
  const details = screen.getByRole('dialog', {
    name: 'Retired Machine Details',
  });
  fireEvent.click(within(details).getByRole('button', { name: 'Reactivate' }));

  // Entered from the details dialog, leaving the workflow is ‹ Back…
  const reactivate = screen.getByRole('dialog', { name: 'Reactivate Machine' });
  fireEvent.click(within(reactivate).getByRole('button', { name: '‹ Back' }));

  // …returning to the details dialog instead of closing everything.
  expect(
    screen.getByRole('dialog', { name: 'Retired Machine Details' }),
  ).toBeInTheDocument();
});

test('the maintenance note and expected return date are editable from Edit Machine', async () => {
  await renderMachines();

  // A Machine that is NOT under maintenance shows no maintenance
  // fields in the Edit dialog.
  const lathe2 = openEdit('Lathe 2');
  expect(within(lathe2).queryByLabelText(/Reason \/ note/)).toBeNull();
  fireEvent.click(within(lathe2).getByRole('button', { name: 'Cancel (Esc)' }));

  // Lathe 4 is under maintenance — the context is editable in place.
  const edit = openEdit('Lathe 4');
  expect(within(edit).getByLabelText(/Reason \/ note/)).toHaveValue(
    'Spindle bearing replacement',
  );
  expect(within(edit).getByLabelText(/Expected return date/)).toHaveValue(
    '2026-08-06',
  );
  fireEvent.change(within(edit).getByLabelText(/Reason \/ note/), {
    target: { value: 'Spindle rebuilt — waiting on parts' },
  });
  fireEvent.change(within(edit).getByLabelText(/Expected return date/), {
    target: { value: '2026-08-20' },
  });
  fireEvent.click(within(edit).getByRole('button', { name: 'Save changes' }));

  // The row shows the updated context; the state itself is untouched.
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() => {
    const row = activeRow('Lathe 4');
    expect(row.querySelector('.mg-state')?.textContent).toMatch(
      /^Maintenance · /,
    );
    expect(row.textContent).toContain('Spindle rebuilt — waiting on parts');
    expect(row.textContent).toContain('Expected back 2026-08-20');
  });
  // The maintenance start time was NOT touched by the context edit.
  expect(state.machines.find((m) => m.id === 107)?.maintenance_since).toBe(
    '2026-07-28T00:00:00.000Z',
  );
});

test('the barcode label dialog renders the scannable Asset Tag barcode', async () => {
  await renderMachines();

  const dialog = openEdit('Lathe 2');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Barcode label…' }),
  );
  const label = screen.getByRole('dialog', { name: 'Machine barcode label' });
  expect(label.textContent).toContain('Lathe 2');
  expect(label.textContent).toContain('CD-0105');
  expect(label.textContent).toContain('PF:MACHINE:CD-0105');
  // The Code 128 rendering is a real SVG barcode of the scanned value.
  const svg = label.querySelector('svg.lbarcode') as SVGElement;
  expect(svg).not.toBeNull();
  expect(svg.getAttribute('aria-label')).toBe('Barcode PF:MACHINE:CD-0105');
  expect(svg.querySelectorAll('rect').length).toBeGreaterThan(20);
  expect(
    within(label).getByRole('button', { name: 'Print Label' }),
  ).toBeInTheDocument();

  // Cancel returns to the Edit dialog.
  fireEvent.click(within(label).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(
    screen.getByRole('dialog', { name: 'Edit Machine' }),
  ).toBeInTheDocument();
});

test('a rejected save keeps the Edit dialog open with the server message', async () => {
  await renderMachines();

  // Rename Lathe 3 to collide with the active Lathe 2 — the live field
  // feedback catches this locally, so simulate the authoritative
  // server rejection instead through a name that only the server
  // refuses (a concurrent rename): force the fake to reject.
  const edit = openEdit('Lathe 3');
  fireEvent.change(within(edit).getByLabelText('Notes (optional)'), {
    target: { value: 'Updated notes' },
  });
  // Another client renames Lathe 2 → the collision only exists
  // server-side.
  state.machines.find((m) => m.id === 105)!.name = 'Lathe 3X';
  fireEvent.change(within(edit).getByLabelText('Display name'), {
    target: { value: 'Lathe 3X' },
  });
  fireEvent.click(within(edit).getByRole('button', { name: 'Save changes' }));

  const message = await within(
    screen.getByRole('dialog', { name: 'Edit Machine' }),
  ).findAllByRole('alert');
  expect(message.some((m) => m.textContent?.includes('already exists'))).toBe(
    true,
  );
  // Nothing was persisted.
  expect(state.machines.find((m) => m.id === 106)?.notes).toBeNull();
});

/* ============ Offline write-block ============ */

test('offline disables New Machine and the Maintenance switch; reading stays available', async () => {
  await renderMachines('unavailable');

  expect(screen.getByRole('button', { name: '+ New Machine' })).toBeDisabled();
  expect(maintenanceSwitch('Lathe 2')).toBeDisabled();

  // Read-only/search/navigation stay available offline: the row still
  // opens Edit Machine.
  const edit = openEdit('Lathe 2');
  expect(edit).toBeInTheDocument();
});

test('offline disables Save and Retire… inside Edit Machine', async () => {
  await renderMachines('unavailable');

  const edit = openEdit('Mill 3 — Horizontal Boring');
  expect(
    within(edit).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
  expect(within(edit).getByRole('button', { name: 'Retire…' })).toBeDisabled();
});

test('offline disables Reactivate inside Retired Machine Details', async () => {
  await renderMachines('unavailable');

  const lathe1Row = within(retiredTable())
    .getByText('Retired on 2026-02-14')
    .closest('tr') as HTMLElement;
  fireEvent.click(lathe1Row);
  const details = screen.getByRole('dialog', {
    name: 'Retired Machine Details',
  });
  expect(
    within(details).getByRole('button', { name: 'Reactivate' }),
  ).toBeDisabled();
});

test('reconnecting re-enables the write actions', async () => {
  const { rerender } = await renderMachines('unavailable');
  expect(screen.getByRole('button', { name: '+ New Machine' })).toBeDisabled();

  rerender(
    <ConnectivityContext.Provider
      value={{ status: 'connected', retry: vi.fn() }}
    >
      <MachinesView />
    </ConnectivityContext.Provider>,
  );
  expect(screen.getByRole('button', { name: '+ New Machine' })).toBeEnabled();
});

test('offline mid-flow disables the Retire workflow’s typed-confirm Continue and the final question — nothing retires', async () => {
  // The realistic sequence: open the workflow while CONNECTED, then
  // lose connectivity with it already open. Entry-point blocking alone
  // (Retire… disabled) is not enough — every write-capable step
  // already reachable inside the open dialog must gate too.
  const { rerender } = await renderMachines('connected');
  const reconnectAs = (status: 'connected' | 'unavailable') =>
    rerender(
      <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
        <MachinesView />
      </ConnectivityContext.Provider>,
    );

  const edit = openEdit('Mill 3 — Horizontal Boring');
  fireEvent.click(within(edit).getByRole('button', { name: 'Retire…' }));
  const confirm = screen.getByRole('dialog', { name: 'Retire Machine' });
  fireEvent.change(within(confirm).getByLabelText(/to confirm$/), {
    target: { value: 'CD-0303' },
  });
  const continueButton = within(confirm).getByRole('button', {
    name: 'Continue',
  });
  expect(continueButton).toBeEnabled();

  // Connectivity drops with the typed-confirm dialog already open and
  // already satisfied — the shared TypedConfirmDialog must still gate
  // its own confirming action on the external writeBlocked prop.
  reconnectAs('unavailable');
  expect(
    within(screen.getByRole('dialog', { name: 'Retire Machine' })).getByRole(
      'button',
      { name: 'Continue' },
    ),
  ).toBeDisabled();

  // Reconnecting restores it — the flow can proceed to the final
  // explicit question.
  reconnectAs('connected');
  fireEvent.click(
    within(screen.getByRole('dialog', { name: 'Retire Machine' })).getByRole(
      'button',
      { name: 'Continue' },
    ),
  );
  const summary = screen.getByRole('dialog', { name: 'Confirm retirement' });
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Retire Machine' }),
  );
  const ask = screen.getByRole('dialog', { name: 'Retire this Machine?' });
  expect(
    within(ask).getByRole('button', { name: 'Retire Machine' }),
  ).toBeEnabled();

  // Connectivity drops again with the FINAL confirmation already open
  // — this is the exact gap the offline write-block must close.
  reconnectAs('unavailable');
  const finalButton = within(
    screen.getByRole('dialog', { name: 'Retire this Machine?' }),
  ).getByRole('button', { name: 'Retire Machine' });
  expect(finalButton).toBeDisabled();
  fireEvent.click(finalButton);

  // Nothing mutated: the Machine is still active, not retired.
  expect(
    screen.getByRole('dialog', { name: 'Retire this Machine?' }),
  ).toBeInTheDocument();
  const activeTable = document.querySelectorAll('.mg-table')[0];
  expect(activeTable.textContent).toContain('Mill 3 — Horizontal Boring');
  expect(retiredTable().textContent).not.toContain(
    'Mill 3 — Horizontal Boring',
  );
  expect(state.machines.find((m) => m.id === 303)?.retired_on).toBeNull();
});

test('offline mid-flow disables the Start maintenance confirm — the switch stays off', async () => {
  const { rerender } = await renderMachines('connected');
  const reconnectAs = (status: 'connected' | 'unavailable') =>
    rerender(
      <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
        <MachinesView />
      </ConnectivityContext.Provider>,
    );

  fireEvent.click(maintenanceSwitch('Lathe 2'));
  const dialog = screen.getByRole('dialog', { name: 'Start maintenance' });
  const startButton = within(dialog).getByRole('button', {
    name: 'Start maintenance',
  });
  expect(startButton).toBeEnabled();

  reconnectAs('unavailable');
  expect(
    within(screen.getByRole('dialog', { name: 'Start maintenance' })).getByRole(
      'button',
      { name: 'Start maintenance' },
    ),
  ).toBeDisabled();
  fireEvent.click(
    within(screen.getByRole('dialog', { name: 'Start maintenance' })).getByRole(
      'button',
      { name: 'Start maintenance' },
    ),
  );

  // Still open, and the Machine never actually entered Maintenance.
  expect(
    screen.getByRole('dialog', { name: 'Start maintenance' }),
  ).toBeInTheDocument();
  expect(
    state.machines.find((m) => m.id === 105)?.maintenance_since,
  ).toBeNull();
});

test('offline mid-flow disables the unsaved-edits Save changes but keeps Discard changes available — nothing is saved', async () => {
  const { rerender } = await renderMachines('connected');
  const reconnectAs = (status: 'connected' | 'unavailable') =>
    rerender(
      <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
        <MachinesView />
      </ConnectivityContext.Provider>,
    );

  const edit = openEdit('Mill 3 — Horizontal Boring');
  fireEvent.change(within(edit).getByLabelText('Notes (optional)'), {
    target: { value: 'Pending disposal review' },
  });
  fireEvent.click(within(edit).getByRole('button', { name: 'Cancel (Esc)' }));
  const unsaved = screen.getByRole('dialog', { name: 'Unsaved changes' });
  expect(
    within(unsaved).getByRole('button', { name: 'Save changes' }),
  ).toBeEnabled();

  reconnectAs('unavailable');
  const stillUnsaved = screen.getByRole('dialog', { name: 'Unsaved changes' });
  // Save writes immediately here (it calls the API directly) — it must
  // gate on writeBlocked. Discard never persists anything — it must
  // keep working offline (§ read-only/close actions stay available).
  expect(
    within(stillUnsaved).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
  expect(
    within(stillUnsaved).getByRole('button', { name: 'Discard changes' }),
  ).toBeEnabled();

  // Discard still works offline — it only closes, never writes.
  fireEvent.click(
    within(stillUnsaved).getByRole('button', { name: 'Discard changes' }),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  // Notes were never saved.
  expect(state.machines.find((m) => m.id === 303)?.notes).toBeNull();
});

/* ============ ?state=long ============ */

test('?state=long renders many long-identifier Machines alongside the server data', async () => {
  window.history.replaceState({}, '', '/management/machines?state=long');
  await renderMachines();

  // Server data is still present…
  expect(activeRow('Lathe 2')).toBeTruthy();
  // …plus the long-preview rows, including the over-long display name
  // and asset metadata.
  const activeTable = document.querySelectorAll('.mg-table')[0];
  expect(activeTable.textContent).toContain(
    'Supplemental long-preview Machine — extended display name for dense-table layout testing only',
  );
  expect(activeTable.textContent).toContain('CD-LONG-SUPPLEMENTAL');
  expect(activeTable.textContent).toContain(
    'Long preview Machine 1 — extended qualification cell',
  );
});
