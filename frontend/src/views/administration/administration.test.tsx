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
import { prepareImageUpload } from '../../components/image-upload';
import { AdministrationView } from './AdministrationView';

// Administration (GUI_DESIGN §9): the minimum environment setup
// sections — Departments, Areas, Operations, Scan Stations, Barcode
// configuration — Workers, Worker sessions and Correction permissions
// read and write the real /api surface (faked in-memory here with the same routes and
// semantics). Every other section presents itself honestly as not
// available yet.

// Image preparation (sniff, decode, downscale) has its own suite; here
// it passes the chosen file through, or refuses it when a test says so.
const imagePreparation = vi.hoisted(() => ({
  rejectWith: null as string | null,
}));
vi.mock('../../components/image-upload', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../components/image-upload')>();
  return {
    ...actual,
    prepareImageUpload: vi.fn(async (file: File) => {
      if (imagePreparation.rejectWith) {
        throw new actual.ImageUploadError(imagePreparation.rejectWith);
      }
      return file;
    }),
  };
});

interface WorkerRow {
  id: number;
  name: string;
  badge_barcode: string;
  is_active: boolean;
  avatar_updated_at: string | null;
}

/** The fake Worker routes a test can make fail. */
type WorkerRoute =
  'GET list' | 'POST' | 'PATCH' | 'PUT avatar' | 'DELETE avatar';
/** A server answer (`ApiError`) or no answer at all (network failure). */
type FakeFailure = { status: number; detail: string } | 'network';

type WorkerIdMode = 'DISABLED' | 'FIXED' | 'SCANNED';

interface AreaRow {
  id: number;
  department_id: number;
  name: string;
  barcode_value: string | null;
  description: string | null;
  color: string | null;
  icon_url: null;
  is_terminal: boolean;
  is_active: boolean;
  worker_identification_mode: WorkerIdMode;
  fixed_worker_id: number | null;
  worker_session_timeout_minutes: number | null;
}

interface FakeDepartment {
  id: number;
  name: string;
  is_active: boolean;
  /** Department display settings (Production Board rotation timing). */
  board_seconds_per_row: number;
  board_min_page_seconds: number;
}

interface FakeState {
  departments: FakeDepartment[];
  areas: AreaRow[];
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
  workers: WorkerRow[];
  /** `application_policy` default Worker session timeout (minutes). */
  sessionTimeout: number;
  /** `application_policy` badge-confirmation options. */
  badgeConfirm: { done: boolean; queue: boolean; undo: boolean };
  /** `application_policy` Undo reason policy. */
  undoReasonRequired: boolean;
  /** `application_policy` Due Soon warning policy. */
  dueSoon: { min: number; percent: number; max: number };
  nextId: number;
}

const T0 = '2026-08-01T00:00:00.000Z';
const ALEX_AVATAR_AT = '2026-09-01T08:00:00.123456+00:00';

function seedState(): FakeState {
  return {
    departments: [
      {
        id: 1,
        name: 'Machine Shop',
        is_active: true,
        board_seconds_per_row: 3,
        board_min_page_seconds: 6,
      },
    ],
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
        worker_identification_mode: 'DISABLED',
        fixed_worker_id: null,
        worker_session_timeout_minutes: null,
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
        worker_identification_mode: 'DISABLED',
        fixed_worker_id: null,
        worker_session_timeout_minutes: null,
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
    workers: [
      {
        id: 1,
        name: 'Alex Tran',
        badge_barcode: '100482',
        is_active: true,
        avatar_updated_at: ALEX_AVATAR_AT,
      },
      {
        id: 2,
        name: 'Mai',
        badge_barcode: 'B-77',
        is_active: false,
        avatar_updated_at: null,
      },
    ],
    sessionTimeout: 15,
    badgeConfirm: { done: true, queue: true, undo: true },
    undoReasonRequired: false,
    dueSoon: { min: 2, percent: 15, max: 7 },
    nextId: 100,
  };
}

let state: FakeState;
/**
 * Bodies of the write requests the fake API received, oldest first; a
 * raw upload records its `Content-Type` header and byte size instead.
 */
let writes: { method: string; url: string; body: unknown }[];
let workerFailures: Partial<Record<WorkerRoute, FakeFailure>>;
let workerListReads: number;
let avatarVersion: number;
/** A refusal of every `/api/policies/worker-sessions` call, if set. */
let policyFailure: { status: number; detail: string } | null;
/** While set, a policy PUT stays pending until it resolves. */
let policyHold: Promise<void> | null;
/** A refusal of every `/api/policies/correction-permissions` call, if set. */
let correctionFailure: { status: number; detail: string } | null;
/** A refusal of every `/api/policies/due-soon` call, if set. */
let dueSoonFailure: { status: number; detail: string } | null;

const E_B1 = 'Seconds per displayed row must be a whole number from 1 to 60.';
const E_B2 =
  'The minimum page dwell must be a whole number of seconds from 1 to 300.';
const E_D1 = 'Minimum warning days must be a whole number from 0 to 365.';
const E_D2 = 'Maximum warning days must be a whole number from 0 to 365.';
const E_D3 =
  'The lead-time warning percentage must be a whole number from 1 to 100.';
const E_D4 =
  'Minimum warning days cannot be greater than maximum warning days.';

const wholeIn = (value: unknown, min: number, max: number) =>
  typeof value === 'number' &&
  Number.isInteger(value) &&
  value >= min &&
  value <= max;

const AREA_TIMEOUT_REFUSAL =
  "An Area's Worker session timeout must be a whole number of minutes from 1 to 720, or empty to use the default.";

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
  const upload =
    init?.body instanceof Blob
      ? {
          contentType: (init.headers as Record<string, string>)['Content-Type'],
          size: init.body.size,
        }
      : null;
  if (method !== 'GET') writes.push({ method, url, body: upload ?? body });

  if (url === '/api/health') return json({ status: 'ok' });
  if (url === '/api/workers' || url.startsWith('/api/workers/')) {
    return handleWorkers(url, method, body);
  }
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
          operational_state: 'IDLE',
          assigned_quantity: 0,
          assigned_lines: [],
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
    const department = {
      id: state.nextId++,
      name,
      is_active: true,
      board_seconds_per_row: 3,
      board_min_page_seconds: 6,
    };
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
    if (
      'board_seconds_per_row' in body &&
      !wholeIn(body.board_seconds_per_row, 1, 60)
    ) {
      return json({ detail: E_B1 }, 422);
    }
    if (
      'board_min_page_seconds' in body &&
      !wholeIn(body.board_min_page_seconds, 1, 300)
    ) {
      return json({ detail: E_B2 }, 422);
    }
    if (typeof body.name === 'string') department.name = body.name.trim();
    if (typeof body.is_active === 'boolean')
      department.is_active = body.is_active;
    if (typeof body.board_seconds_per_row === 'number') {
      department.board_seconds_per_row = body.board_seconds_per_row;
    }
    if (typeof body.board_min_page_seconds === 'number') {
      department.board_min_page_seconds = body.board_min_page_seconds;
    }
    return json(stamp(department));
  }
  if (url === '/api/areas' && method === 'GET') {
    return json(state.areas.map(stamp));
  }
  if (url === '/api/areas' && method === 'POST') {
    const refusal = areaIdentityRefusal(body, {
      worker_identification_mode: 'DISABLED',
      fixed_worker_id: null,
    });
    if (refusal) return refusal;
    const area: AreaRow = {
      id: state.nextId++,
      department_id: Number(body.department_id),
      name: String(body.name).trim(),
      barcode_value: '',
      description: (body.description as string | null) ?? null,
      color: (body.color as string | null) ?? null,
      icon_url: null,
      is_terminal: Boolean(body.is_terminal),
      is_active: true,
      worker_identification_mode:
        (body.worker_identification_mode as WorkerIdMode | undefined) ??
        'DISABLED',
      fixed_worker_id: (body.fixed_worker_id as number | null) ?? null,
      worker_session_timeout_minutes:
        (body.worker_session_timeout_minutes as number | null) ?? null,
    };
    area.barcode_value = `PF:AREA:${area.id}`;
    state.areas.push(area);
    return json(stamp(area), 201);
  }
  const areaMatch = /^\/api\/areas\/(\d+)$/.exec(url);
  if (areaMatch && method === 'PATCH') {
    const area = state.areas.find((a) => a.id === Number(areaMatch[1]))!;
    const refusal = areaIdentityRefusal(body, area);
    if (refusal) return refusal;
    if (typeof body.worker_identification_mode === 'string') {
      area.worker_identification_mode =
        body.worker_identification_mode as WorkerIdMode;
      // Leaving Fixed Worker mode clears the Fixed Worker server-side.
      if (area.worker_identification_mode !== 'FIXED') {
        area.fixed_worker_id = null;
      }
    }
    if ('fixed_worker_id' in body) {
      area.fixed_worker_id = (body.fixed_worker_id as number | null) ?? null;
    }
    if (typeof body.name === 'string') area.name = body.name.trim();
    if ('description' in body)
      area.description = (body.description as string | null) ?? null;
    if ('color' in body) area.color = (body.color as string | null) ?? null;
    if (typeof body.is_terminal === 'boolean')
      area.is_terminal = body.is_terminal;
    if (typeof body.is_active === 'boolean') area.is_active = body.is_active;
    if ('worker_session_timeout_minutes' in body) {
      const minutes = body.worker_session_timeout_minutes;
      if (
        minutes !== null &&
        (typeof minutes !== 'number' || minutes < 1 || minutes > 720)
      ) {
        return json({ detail: AREA_TIMEOUT_REFUSAL }, 422);
      }
      area.worker_session_timeout_minutes = minutes;
    }
    return json(stamp(area));
  }
  if (url === '/api/policies/worker-sessions') {
    if (policyFailure) {
      return json({ detail: policyFailure.detail }, policyFailure.status);
    }
    if (method === 'PUT') {
      if (policyHold) await policyHold;
      // Partial merge: every field absent from the body keeps its value.
      if ('worker_session_timeout_minutes' in body) {
        state.sessionTimeout = Number(body.worker_session_timeout_minutes);
      }
      if ('badge_confirm_done' in body) {
        state.badgeConfirm.done = body.badge_confirm_done as boolean;
      }
      if ('badge_confirm_queue' in body) {
        state.badgeConfirm.queue = body.badge_confirm_queue as boolean;
      }
      if ('badge_confirm_undo' in body) {
        state.badgeConfirm.undo = body.badge_confirm_undo as boolean;
      }
    }
    return json({
      worker_session_timeout_minutes: state.sessionTimeout,
      badge_confirm_done: state.badgeConfirm.done,
      badge_confirm_queue: state.badgeConfirm.queue,
      badge_confirm_undo: state.badgeConfirm.undo,
      updated_at: T0,
    });
  }
  if (url === '/api/policies/due-soon') {
    if (dueSoonFailure) {
      return json({ detail: dueSoonFailure.detail }, dueSoonFailure.status);
    }
    if (method === 'PUT') {
      const keys = [
        'due_soon_min_days',
        'due_soon_lead_time_percent',
        'due_soon_max_days',
      ];
      if (Object.keys(body).sort().join() !== [...keys].sort().join()) {
        return json({ detail: 'Invalid request.' }, 422);
      }
      const min = body.due_soon_min_days;
      const percent = body.due_soon_lead_time_percent;
      const max = body.due_soon_max_days;
      if (!wholeIn(min, 0, 365)) return json({ detail: E_D1 }, 422);
      if (!wholeIn(max, 0, 365)) return json({ detail: E_D2 }, 422);
      if (!wholeIn(percent, 1, 100)) return json({ detail: E_D3 }, 422);
      if ((min as number) > (max as number)) {
        return json({ detail: E_D4 }, 422);
      }
      state.dueSoon = {
        min: min as number,
        percent: percent as number,
        max: max as number,
      };
    }
    return json({
      due_soon_min_days: state.dueSoon.min,
      due_soon_lead_time_percent: state.dueSoon.percent,
      due_soon_max_days: state.dueSoon.max,
      updated_at: T0,
    });
  }
  if (url === '/api/policies/correction-permissions') {
    if (correctionFailure) {
      return json(
        { detail: correctionFailure.detail },
        correctionFailure.status,
      );
    }
    if (method === 'PUT') {
      if (policyHold) await policyHold;
      // Exactly one required boolean field (no partial merge).
      if (
        Object.keys(body).length !== 1 ||
        typeof body.undo_reason_required !== 'boolean'
      ) {
        return json({ detail: 'Invalid request.' }, 422);
      }
      state.undoReasonRequired = body.undo_reason_required;
    }
    return json({
      undo_reason_required: state.undoReasonRequired,
      updated_at: T0,
    });
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

/** The configured failure of one Worker route, if any. */
function workerFailure(route: WorkerRoute): Response | null {
  const failure = workerFailures[route];
  if (!failure) return null;
  if (failure === 'network') throw new TypeError('Failed to fetch');
  return json({ detail: failure.detail }, failure.status);
}

/** Server-side badge canonical form (trim, uppercase). */
function canonicalBadge(value: unknown): string {
  return String(value).trim().toUpperCase();
}

function duplicateBadge(badge: string, exceptId?: number): Response | null {
  const holder = state.workers.find(
    (w) => w.badge_barcode === badge && w.id !== exceptId,
  );
  if (!holder) return null;
  const suffix = holder.is_active ? '' : ' (inactive)';
  return json(
    {
      detail: `This badge barcode is already assigned to ${holder.name}${suffix}.`,
    },
    409,
  );
}

/** The server's Worker ID mode refusal the editor renders in place: a
 * newly chosen Fixed Worker that is inactive (an unchanged configuration
 * is never re-judged). */
function areaIdentityRefusal(
  body: Record<string, unknown>,
  current: Pick<AreaRow, 'worker_identification_mode' | 'fixed_worker_id'>,
): Response | null {
  const worker = state.workers.find((w) => w.id === body.fixed_worker_id);
  const changed =
    body.worker_identification_mode !== current.worker_identification_mode ||
    body.fixed_worker_id !== current.fixed_worker_id;
  if (
    body.worker_identification_mode === 'FIXED' &&
    changed &&
    worker &&
    !worker.is_active
  ) {
    return json(
      {
        detail: `Worker '${worker.name}' is inactive and cannot be the Fixed Worker of an Area. Choose an active Worker.`,
      },
      409,
    );
  }
  return null;
}

function handleWorkers(
  url: string,
  method: string,
  body: Record<string, unknown>,
): Response {
  if (url === '/api/workers' && method === 'GET') {
    workerListReads += 1;
    const ordered = [...state.workers].sort(
      (a, b) => a.name.localeCompare(b.name) || a.id - b.id,
    );
    return workerFailure('GET list') ?? json(ordered.map(stamp));
  }
  if (url === '/api/workers' && method === 'POST') {
    const failure = workerFailure('POST');
    if (failure) return failure;
    const badge = canonicalBadge(body.badge_barcode);
    const duplicate = duplicateBadge(badge);
    if (duplicate) return duplicate;
    const worker: WorkerRow = {
      id: state.nextId++,
      name: String(body.name).trim(),
      badge_barcode: badge,
      is_active: true,
      avatar_updated_at: null,
    };
    state.workers.push(worker);
    return json(stamp(worker), 201);
  }
  const match = /^\/api\/workers\/(\d+)(\/avatar)?$/.exec(url);
  const worker = state.workers.find((w) => w.id === Number(match?.[1]));
  if (!match || !worker) {
    return json({ detail: `Worker ${match?.[1]} does not exist.` }, 404);
  }
  if (!match[2] && method === 'PATCH') {
    const failure = workerFailure('PATCH');
    if (failure) return failure;
    if (typeof body.badge_barcode === 'string') {
      const badge = canonicalBadge(body.badge_barcode);
      const duplicate = duplicateBadge(badge, worker.id);
      if (duplicate) return duplicate;
      worker.badge_barcode = badge;
    }
    const fixedIn = state.areas.filter((a) => a.fixed_worker_id === worker.id);
    if (body.is_active === false && worker.is_active && fixedIn.length > 0) {
      return json(
        {
          detail: `Worker '${worker.name}' is the Fixed Worker of Area '${fixedIn[0].name}'. Choose another Fixed Worker or Worker ID mode for that Area in Administration → Areas before deactivating this Worker.`,
        },
        409,
      );
    }
    if (typeof body.name === 'string') worker.name = body.name.trim();
    if (typeof body.is_active === 'boolean') worker.is_active = body.is_active;
    return json(stamp(worker));
  }
  if (match[2] && method === 'PUT') {
    const failure = workerFailure('PUT avatar');
    if (failure) return failure;
    avatarVersion += 1;
    worker.avatar_updated_at = `2026-10-04T10:00:00.00000${avatarVersion}+00:00`;
    return json(stamp(worker));
  }
  if (match[2] && method === 'DELETE') {
    const failure = workerFailure('DELETE avatar');
    if (failure) return failure;
    worker.avatar_updated_at = null;
    return json(stamp(worker));
  }
  return json({ detail: `Unhandled fake route: ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.history.replaceState({}, '', '/administration');
  state = seedState();
  writes = [];
  workerFailures = {};
  workerListReads = 0;
  avatarVersion = 0;
  policyFailure = null;
  policyHold = null;
  correctionFailure = null;
  dueSoonFailure = null;
  imagePreparation.rejectWith = null;
  // jsdom has no object URLs; the staged avatar preview needs one.
  URL.createObjectURL = vi.fn(() => 'blob:staged-avatar');
  URL.revokeObjectURL = vi.fn();
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
    worker_identification_mode: 'DISABLED',
    fixed_worker_id: null,
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
    // A new Area defaults to Disabled (no Worker recorded).
    worker_identification_mode: 'DISABLED',
    fixed_worker_id: null,
  });
});

/* ============ Areas — Worker ID mode ============ */

function workerIdModeCell(areaName: string): string | null | undefined {
  return screen
    .getByRole('button', { name: `Edit ${areaName}` })
    .closest('tr')
    ?.querySelector('td[data-label="Worker ID mode"]')?.textContent;
}

test('the Worker ID mode column renders the configured mode of every Area', async () => {
  state.areas[0].worker_identification_mode = 'FIXED';
  state.areas[0].fixed_worker_id = 1;
  state.areas.push({
    ...state.areas[1],
    id: 3,
    name: 'Plating',
    barcode_value: 'PF:AREA:3',
    is_terminal: false,
    worker_identification_mode: 'SCANNED',
  });
  renderAdmin();

  await screen.findByRole('button', { name: 'Edit Lathe' });
  expect(workerIdModeCell('Lathe')).toBe('Fixed Worker');
  expect(workerIdModeCell('Stockroom')).toBe('Disabled');
  expect(workerIdModeCell('Plating')).toBe('Scanned session');
});

test('Fixed Worker mode lists active Workers only, requires a choice and sends the mode with the Worker', async () => {
  renderAdmin();

  fireEvent.click(await screen.findByRole('button', { name: 'Edit Lathe' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Area' });
  const mode = within(dialog).getByLabelText('Worker ID mode');
  expect(mode).toHaveValue('DISABLED');
  expect(dialog.textContent).toContain(
    "No Worker is recorded for this Area's production activity.",
  );
  expect(within(dialog).queryByLabelText('Fixed Worker')).toBeNull();

  fireEvent.change(mode, { target: { value: 'FIXED' } });
  expect(dialog.textContent).toContain(
    "Every production action at this Area's Scan Stations records the Fixed Worker.",
  );
  const worker = within(dialog).getByLabelText(
    'Fixed Worker',
  ) as HTMLSelectElement;
  // Active Workers only, named with their badge (names are not unique).
  expect(Array.from(worker.options, (option) => option.textContent)).toEqual([
    'Choose a Worker',
    'Alex Tran · 100482',
  ]);

  // Saving without a chosen Worker is refused in place; nothing is sent.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'Choose the Fixed Worker.',
  );
  expect(writes).toEqual([]);

  fireEvent.change(worker, { target: { value: '1' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[0].body).toMatchObject({
    worker_identification_mode: 'FIXED',
    fixed_worker_id: 1,
  });
  await waitFor(() => expect(workerIdModeCell('Lathe')).toBe('Fixed Worker'));

  // Back to Disabled: the Worker select disappears and null is sent.
  fireEvent.click(screen.getByRole('button', { name: 'Edit Lathe' }));
  const edit = screen.getByRole('dialog', { name: 'Edit Area' });
  expect(within(edit).getByLabelText('Fixed Worker')).toHaveValue('1');
  fireEvent.change(within(edit).getByLabelText('Worker ID mode'), {
    target: { value: 'DISABLED' },
  });
  expect(within(edit).queryByLabelText('Fixed Worker')).toBeNull();
  fireEvent.click(within(edit).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[1].body).toMatchObject({
    worker_identification_mode: 'DISABLED',
    fixed_worker_id: null,
  });
  await waitFor(() => expect(workerIdModeCell('Lathe')).toBe('Disabled'));
});

test('Scanned session is selectable with its help; without active Workers the Fixed Worker select is disabled with guidance', async () => {
  state.workers = state.workers.map((w) => ({ ...w, is_active: false }));
  renderAdmin();

  fireEvent.click(await screen.findByRole('button', { name: '+ New Area' }));
  const dialog = screen.getByRole('dialog', { name: 'New Area' });
  const mode = within(dialog).getByLabelText(
    'Worker ID mode',
  ) as HTMLSelectElement;
  expect(mode).toHaveValue('DISABLED');
  const scanned = Array.from(mode.options).find((o) => o.value === 'SCANNED')!;
  expect(scanned.textContent).toBe('Scanned session');
  expect(scanned.disabled).toBe(false);
  fireEvent.change(mode, { target: { value: 'SCANNED' } });
  expect(dialog.textContent).toContain(
    "Workers sign in at this Area's Scan Stations by scanning their badge. Every production action records the signed-in Worker.",
  );
  expect(within(dialog).queryByLabelText('Fixed Worker')).toBeNull();

  fireEvent.change(mode, { target: { value: 'FIXED' } });
  expect(within(dialog).getByLabelText('Fixed Worker')).toBeDisabled();
  expect(dialog.textContent).toContain(
    'No active Workers — add one in Administration → Workers.',
  );
});

test('an Area already in Scanned session keeps that option and saves it unchanged', async () => {
  state.areas[0].worker_identification_mode = 'SCANNED';
  renderAdmin();

  fireEvent.click(await screen.findByRole('button', { name: 'Edit Lathe' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Area' });
  const mode = within(dialog).getByLabelText(
    'Worker ID mode',
  ) as HTMLSelectElement;
  expect(mode).toHaveValue('SCANNED');
  expect(
    Array.from(mode.options).find((o) => o.value === 'SCANNED')!.disabled,
  ).toBe(false);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[0].body).toMatchObject({
    worker_identification_mode: 'SCANNED',
  });
  expect(writes[0].body).not.toHaveProperty('fixed_worker_id');
});

test('an inactive current Fixed Worker stays visible as a disabled option', async () => {
  // An inactive current Fixed Worker exists only from old fixtures.
  state.areas[0].worker_identification_mode = 'FIXED';
  state.areas[0].fixed_worker_id = 2; // Mai — inactive
  renderAdmin();

  fireEvent.click(await screen.findByRole('button', { name: 'Edit Lathe' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Area' });
  const worker = within(dialog).getByLabelText(
    'Fixed Worker',
  ) as HTMLSelectElement;
  const mai = Array.from(worker.options).find((o) => o.value === '2')!;
  expect(mai.textContent).toBe('Mai (inactive)');
  expect(mai.disabled).toBe(true);
  expect(worker).toHaveValue('2');
});

test('a Fixed Worker the server finds inactive is refused in place with the draft kept', async () => {
  renderAdmin();

  fireEvent.click(await screen.findByRole('button', { name: 'Edit Lathe' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Area' });
  fireEvent.change(within(dialog).getByLabelText('Worker ID mode'), {
    target: { value: 'FIXED' },
  });
  fireEvent.change(within(dialog).getByLabelText('Fixed Worker'), {
    target: { value: '1' },
  });
  // Deactivated elsewhere after the editor loaded its Worker list.
  state.workers[0].is_active = false;
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    "Worker 'Alex Tran' is inactive and cannot be the Fixed Worker of an Area. Choose an active Worker.",
  );
  expect(screen.getByRole('dialog', { name: 'Edit Area' })).toBe(dialog);
  expect(within(dialog).getByLabelText('Worker ID mode')).toHaveValue('FIXED');
  expect(within(dialog).getByLabelText('Fixed Worker')).toHaveValue('1');
  expect(state.areas[0].worker_identification_mode).toBe('DISABLED');
});

test('an Area moves to Scanned session without a Fixed Worker in the PATCH; the table shows the mode', async () => {
  // From Fixed Worker: the server clears the Fixed Worker itself.
  state.areas[0].worker_identification_mode = 'FIXED';
  state.areas[0].fixed_worker_id = 1;
  renderAdmin();

  fireEvent.click(await screen.findByRole('button', { name: 'Edit Lathe' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Area' });
  fireEvent.change(within(dialog).getByLabelText('Worker ID mode'), {
    target: { value: 'SCANNED' },
  });
  expect(within(dialog).queryByLabelText('Fixed Worker')).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ method: 'PATCH', url: '/api/areas/1' });
  expect(writes[0].body).toMatchObject({
    worker_identification_mode: 'SCANNED',
  });
  expect(writes[0].body).not.toHaveProperty('fixed_worker_id');
  expect(state.areas[0].fixed_worker_id).toBeNull();
  await waitFor(() =>
    expect(workerIdModeCell('Lathe')).toBe('Scanned session'),
  );
});

test('a Workers load failure is the Area data error with Retry; offline keeps Save disabled', async () => {
  workerFailures['GET list'] = { status: 500, detail: 'Workers unavailable' };
  renderAdmin();

  expect(
    await screen.findByText('Area data could not be loaded.'),
  ).toBeInTheDocument();
  workerFailures = {};
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(
    await screen.findByRole('button', { name: 'Edit Lathe' }),
  ).toBeInTheDocument();
  cleanup();

  renderAdmin('unavailable');
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Lathe' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Area' });
  fireEvent.change(within(dialog).getByLabelText('Worker ID mode'), {
    target: { value: 'FIXED' },
  });
  fireEvent.change(within(dialog).getByLabelText('Fixed Worker'), {
    target: { value: '1' },
  });
  expect(
    within(dialog).getByRole('button', { name: 'Save changes' }),
  ).toBeDisabled();
  expect(writes).toEqual([]);
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

test('a fractional expected duration is rejected in place and never written', async () => {
  renderAdmin();
  openSection('Operations');
  await screen.findByRole('button', { name: 'Edit Turning' });

  fireEvent.click(screen.getByRole('button', { name: '+ New Operation' }));
  const dialog = screen.getByRole('dialog', { name: 'New Operation' });
  fireEvent.change(within(dialog).getByLabelText('Code'), {
    target: { value: 'GRIND' },
  });
  // A fractional value must be rejected as entered — never silently
  // truncated to 1 minute.
  fireEvent.change(
    within(dialog).getByLabelText(/Expected duration in minutes/),
    { target: { value: '1.5' } },
  );
  expect(within(dialog).getByRole('alert').textContent).toContain(
    'whole number of minutes above zero',
  );
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Operation' }),
  );
  // The dialog stays open and no write reached the API.
  expect(
    screen.getByRole('dialog', { name: 'New Operation' }),
  ).toBeInTheDocument();
  expect(writes).toHaveLength(0);

  // Zero and negative values are rejected the same way.
  fireEvent.change(
    within(dialog).getByLabelText(/Expected duration in minutes/),
    { target: { value: '0' } },
  );
  expect(within(dialog).getByRole('alert').textContent).toContain(
    'whole number of minutes above zero',
  );
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Operation' }),
  );
  expect(writes).toHaveLength(0);

  // A whole minute count saves and travels as the ISO 8601 value.
  fireEvent.change(
    within(dialog).getByLabelText(/Expected duration in minutes/),
    { target: { value: '30' } },
  );
  expect(within(dialog).queryByRole('alert')).toBeNull();
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add Operation' }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toHaveLength(1);
  expect(writes[0].method).toBe('POST');
  expect(writes[0].body).toMatchObject({
    code: 'GRIND',
    default_expected_duration: 'PT30M',
  });
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

test('invalid Asset Tag digits are rejected in place — no clamping, no PUT', async () => {
  renderAdmin();
  openSection('Barcode configuration');
  const digits = await screen.findByLabelText('Number length (digits)');

  // Out of range: never clamped to 8.
  fireEvent.change(digits, { target: { value: '12' } });
  expect(
    screen.getByText('The number length must be a whole number from 1 to 8.'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save format' })).toBeDisabled();
  // No preview renders for an invalid format — no substituted value.
  expect(screen.queryByText(/^CD-\d/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Save format' }));
  expect(writes).toHaveLength(0);

  // Fractional: never rounded or truncated.
  fireEvent.change(digits, { target: { value: '2.5' } });
  expect(
    screen.getByText('The number length must be a whole number from 1 to 8.'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save format' })).toBeDisabled();

  // Blank: no silent fallback to the saved value.
  fireEvent.change(digits, { target: { value: '' } });
  expect(
    screen.getByText('The number length must be a whole number from 1 to 8.'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save format' })).toBeDisabled();
  expect(writes).toHaveLength(0);

  // A valid whole number from 1 through 8 clears the error and saves
  // exactly the entered value.
  fireEvent.change(digits, { target: { value: '6' } });
  expect(
    screen.queryByText('The number length must be a whole number from 1 to 8.'),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Save format' }));
  await screen.findByText('✓ Format saved.');
  expect(writes).toHaveLength(1);
  expect(writes[0].body).toEqual({ prefix: 'CD-', digits: 6 });
});

test('a prefix containing whitespace is invalid and is never trimmed into validity', async () => {
  renderAdmin();
  openSection('Barcode configuration');
  const prefix = await screen.findByLabelText('Prefix');

  // Trailing whitespace is part of the entered value — invalid, not
  // trimmed away to make the entry valid.
  fireEvent.change(prefix, { target: { value: 'CD- ' } });
  expect(
    screen.getByText('The prefix cannot contain spaces or “:”.'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save format' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Save format' }));
  expect(writes).toHaveLength(0);

  // An empty prefix is valid — tags are the bare zero-padded number.
  fireEvent.change(prefix, { target: { value: '' } });
  expect(
    screen.queryByText('The prefix cannot contain spaces or “:”.'),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Save format' }));
  await screen.findByText('✓ Format saved.');
  expect(writes).toHaveLength(1);
  expect(writes[0].body).toEqual({ prefix: '', digits: 4 });
});

/* ============ Workers ============ */

const UNKNOWN_OUTCOME =
  'The server did not answer — this change may or may not have been saved. Close this window to refresh the list, then check the Worker before trying again.';

async function openWorkers(status: 'connected' | 'unavailable' = 'connected') {
  renderAdmin(status);
  // The initial Areas section also reads the Workers (its Fixed Worker
  // select); count only the Workers section's own reads.
  await screen.findByRole('button', { name: 'Edit Lathe' });
  workerListReads = 0;
  openSection('Workers');
  await screen.findByRole('button', { name: 'Edit Alex Tran' });
}

function pngFile(): File {
  return new File(
    [new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2])],
    'badge-photo.png',
    { type: 'image/png' },
  );
}

/** Choose an avatar image through the editor's file input. */
function chooseAvatar(dialog: HTMLElement, file: File = pngFile()) {
  fireEvent.change(within(dialog).getByLabelText('Avatar image file'), {
    target: { files: [file] },
  });
}

function fillWorker(dialog: HTMLElement, name: string, badge: string) {
  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: name },
  });
  fireEvent.change(within(dialog).getByLabelText('Badge barcode'), {
    target: { value: badge },
  });
}

const writeSummary = () => writes.map((w) => `${w.method} ${w.url}`);

test('Workers lists active and inactive Workers with badge, status and avatar', async () => {
  await openWorkers();

  const alexRow = screen
    .getByRole('button', { name: 'Edit Alex Tran' })
    .closest('tr') as HTMLElement;
  const alexBadge = within(alexRow).getByText('100482');
  expect(alexBadge).toHaveClass('mono');
  expect(alexBadge).toHaveAttribute('data-label', 'Badge barcode');
  expect(within(alexRow).getByText('Active')).toBeInTheDocument();
  expect(alexRow.querySelector('img')?.getAttribute('src')).toBe(
    `/api/workers/1/avatar?v=${encodeURIComponent(ALEX_AVATAR_AT)}`,
  );

  // Inactive Workers stay listed; no avatar → initials.
  const maiRow = screen
    .getByRole('button', { name: 'Edit Mai' })
    .closest('tr') as HTMLElement;
  expect(within(maiRow).getByText('B-77')).toBeInTheDocument();
  expect(within(maiRow).getByText('Inactive')).toBeInTheDocument();
  expect(maiRow.querySelector('img')).toBeNull();
  expect(maiRow.querySelector('.worker-avatar')?.textContent).toBe('M');

  expect(
    screen.getByText(/separate from application Users/),
  ).toBeInTheDocument();
});

test('a new Worker posts the trimmed name and the canonical badge, previewing the stored form', async () => {
  await openWorkers();

  fireEvent.click(screen.getByRole('button', { name: '+ New Worker' }));
  const dialog = screen.getByRole('dialog', { name: 'New Worker' });
  fillWorker(dialog, '  Linh Pham ', 'ABC1');
  // Already canonical: no preview line.
  expect(dialog.textContent).not.toContain('Saved as:');
  fireEvent.change(within(dialog).getByLabelText('Badge barcode'), {
    target: { value: 'abc1' },
  });
  expect(dialog.textContent).toContain('Saved as: ABC1');
  fireEvent.change(within(dialog).getByLabelText('Badge barcode'), {
    target: { value: ' abc1 ' },
  });
  // The field keeps what was typed; only the preview is canonical.
  expect(within(dialog).getByLabelText('Badge barcode')).toHaveValue(' abc1 ');
  expect(dialog.textContent).toContain('Saved as: ABC1');

  fireEvent.click(within(dialog).getByRole('button', { name: 'Add Worker' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/workers',
      body: { name: 'Linh Pham', badge_barcode: 'ABC1' },
    },
  ]);
  const row = (
    await screen.findByRole('button', { name: 'Edit Linh Pham' })
  ).closest('tr') as HTMLElement;
  expect(within(row).getByText('ABC1')).toBeInTheDocument();
  expect(workerListReads).toBe(2);
});

test('invalid Worker input is refused in place and never written', async () => {
  await openWorkers();

  fireEvent.click(screen.getByRole('button', { name: '+ New Worker' }));
  const dialog = screen.getByRole('dialog', { name: 'New Worker' });
  const add = within(dialog).getByRole('button', { name: 'Add Worker' });
  const alerts = () =>
    within(dialog)
      .queryAllByRole('alert')
      .map((alert) => alert.textContent);

  fireEvent.click(add);
  expect(alerts()).toEqual([
    'A name is required.',
    'A badge barcode is required.',
  ]);

  // The length counts the canonical form: 127 + "ß" → 129 ("SS").
  for (const badge of ['b'.repeat(129), 'a'.repeat(127) + 'ß']) {
    fillWorker(dialog, 'Linh Pham', badge);
    fireEvent.click(add);
    expect(alerts()).toEqual([
      'A badge barcode must be at most 128 characters.',
    ]);
  }

  // The PF: namespace is refused in any letter case.
  fillWorker(dialog, 'Linh Pham', ' pf:worker:7');
  fireEvent.click(add);
  expect(alerts()).toEqual([
    'A badge barcode cannot start with PF: — scan the barcode printed on the employee badge.',
  ]);

  expect(writes).toEqual([]);
  expect(screen.getByRole('dialog', { name: 'New Worker' })).toBeTruthy();
});

test('a duplicate badge is answered by the server in place, with no further write', async () => {
  await openWorkers();

  fireEvent.click(screen.getByRole('button', { name: '+ New Worker' }));
  const dialog = screen.getByRole('dialog', { name: 'New Worker' });
  fillWorker(dialog, 'Linh Pham', ' 100482 ');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add Worker' }));

  const alert = await within(dialog).findByRole('alert');
  expect(alert.textContent).toBe(
    'This badge barcode is already assigned to Alex Tran.',
  );
  expect(screen.getByRole('dialog', { name: 'New Worker' })).toBe(dialog);
  expect(writeSummary()).toEqual(['POST /api/workers']);
});

test('editing a Worker sends the full profile with the canonical badge', async () => {
  await openWorkers();

  fireEvent.click(screen.getByRole('button', { name: 'Edit Alex Tran' }));
  let dialog = screen.getByRole('dialog', { name: 'Edit Worker' });
  expect(within(dialog).getByLabelText('Name')).toHaveValue('Alex Tran');
  fireEvent.change(within(dialog).getByLabelText('Badge barcode'), {
    target: { value: 'x-100482 ' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[0]).toEqual({
    method: 'PATCH',
    url: '/api/workers/1',
    body: { name: 'Alex Tran', badge_barcode: 'X-100482', is_active: true },
  });
  expect(await screen.findByText('X-100482')).toBeInTheDocument();

  // Deactivation travels in the same full-profile body.
  fireEvent.click(screen.getByRole('button', { name: 'Edit Alex Tran' }));
  dialog = screen.getByRole('dialog', { name: 'Edit Worker' });
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Active' }));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[1]).toEqual({
    method: 'PATCH',
    url: '/api/workers/1',
    body: { name: 'Alex Tran', badge_barcode: 'X-100482', is_active: false },
  });
  const row = (
    await screen.findByRole('button', { name: 'Edit Alex Tran' })
  ).closest('tr') as HTMLElement;
  await waitFor(() =>
    expect(within(row).getByText('Inactive')).toBeInTheDocument(),
  );
});

test('deactivating a Worker who is an Area Fixed Worker is refused in place', async () => {
  state.areas[0].worker_identification_mode = 'FIXED';
  state.areas[0].fixed_worker_id = 1;
  await openWorkers();

  fireEvent.click(screen.getByRole('button', { name: 'Edit Alex Tran' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Worker' });
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Active' }));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    "Worker 'Alex Tran' is the Fixed Worker of Area 'Lathe'. Choose another Fixed Worker or Worker ID mode for that Area in Administration → Areas before deactivating this Worker.",
  );
  expect(screen.getByRole('dialog', { name: 'Edit Worker' })).toBe(dialog);
  expect(state.workers[0].is_active).toBe(true);
});

test('a chosen avatar is uploaded after the profile, labelled with its type', async () => {
  await openWorkers();

  fireEvent.click(screen.getByRole('button', { name: 'Edit Mai' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Worker' });
  const file = pngFile();
  chooseAvatar(dialog, file);
  // The staged image previews in the editor before anything is sent.
  await waitFor(() =>
    expect(dialog.querySelector('img')?.getAttribute('src')).toBe(
      'blob:staged-avatar',
    ),
  );
  expect(prepareImageUpload).toHaveBeenCalledWith(file);
  expect(writes).toEqual([]);

  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual([
    'PATCH /api/workers/2',
    'PUT /api/workers/2/avatar',
  ]);
  expect(writes[1].body).toEqual({
    contentType: 'image/png',
    size: file.size,
  });
  const row = (await screen.findByRole('button', { name: 'Edit Mai' })).closest(
    'tr',
  ) as HTMLElement;
  await waitFor(() =>
    expect(row.querySelector('img')?.getAttribute('src')).toBe(
      `/api/workers/2/avatar?v=${encodeURIComponent('2026-10-04T10:00:00.000001+00:00')}`,
    ),
  );
});

test('removing the avatar sends DELETE after the profile', async () => {
  await openWorkers();

  fireEvent.click(screen.getByRole('button', { name: 'Edit Alex Tran' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Worker' });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Remove avatar' }),
  );
  // Staged removal: initials preview, no Remove action left.
  expect(dialog.querySelector('img')).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: 'Remove avatar' }),
  ).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual([
    'PATCH /api/workers/1',
    'DELETE /api/workers/1/avatar',
  ]);
  const row = (
    await screen.findByRole('button', { name: 'Edit Alex Tran' })
  ).closest('tr') as HTMLElement;
  await waitFor(() => expect(row.querySelector('img')).toBeNull());
  expect(row.querySelector('.worker-avatar')?.textContent).toBe('AT');
});

test('Cancel, Escape and the backdrop are ignored while a save is in flight', async () => {
  await openWorkers();
  // Hold the PATCH before the fake server applies it: the profile write
  // is in flight and not committed yet.
  let releasePatch = () => {};
  const patchHeld = new Promise<void>((resolve) => {
    releasePatch = resolve;
  });
  let patchRequested = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        patchRequested = true;
        await patchHeld;
      }
      return handle(String(input), init);
    }),
  );

  fireEvent.click(screen.getByRole('button', { name: 'Edit Alex Tran' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Worker' });
  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: 'Alex T. Tran' },
  });
  chooseAvatar(dialog);
  await waitFor(() =>
    expect(dialog.querySelector('img')?.getAttribute('src')).toBe(
      'blob:staged-avatar',
    ),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(patchRequested).toBe(true));

  const cancel = within(dialog).getByRole('button', { name: 'Cancel (Esc)' });
  expect((cancel as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(cancel);
  fireEvent.keyDown(dialog, { key: 'Escape' });
  fireEvent.mouseDown(dialog.parentElement as HTMLElement);
  // The editor stays open and the list is not reloaded under the write.
  expect(screen.getByRole('dialog', { name: 'Edit Worker' })).toBe(dialog);
  expect(workerListReads).toBe(1);

  releasePatch();
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual([
    'PATCH /api/workers/1',
    'PUT /api/workers/1/avatar',
  ]);
  // The list reloads once, after both writes committed.
  const row = (
    await screen.findByRole('button', { name: 'Edit Alex T. Tran' })
  ).closest('tr') as HTMLElement;
  expect(workerListReads).toBe(2);
  await waitFor(() =>
    expect(row.querySelector('img')?.getAttribute('src')).toBe(
      `/api/workers/1/avatar?v=${encodeURIComponent('2026-10-04T10:00:00.000001+00:00')}`,
    ),
  );
});

test('a refused image file shows its reason inline and stages nothing', async () => {
  await openWorkers();
  imagePreparation.rejectWith = 'Choose a PNG, JPEG or WebP image.';

  fireEvent.click(screen.getByRole('button', { name: 'Edit Mai' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Worker' });
  chooseAvatar(dialog);
  const alert = await within(dialog).findByRole('alert');
  expect(alert.textContent).toBe('Choose a PNG, JPEG or WebP image.');
  expect(dialog.querySelector('img')).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: 'Remove avatar' }),
  ).toBeNull();

  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual(['PATCH /api/workers/2']);
});

test('a new Worker whose avatar is refused becomes an edit of the saved Worker', async () => {
  await openWorkers();
  workerFailures['PUT avatar'] = {
    status: 415,
    detail: 'The file is not a PNG, JPEG or WebP image.',
  };

  fireEvent.click(screen.getByRole('button', { name: '+ New Worker' }));
  let dialog = screen.getByRole('dialog', { name: 'New Worker' });
  fillWorker(dialog, 'Linh Pham', 'l-1');
  chooseAvatar(dialog);
  await waitFor(() => expect(dialog.querySelector('img')).not.toBeNull());
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add Worker' }));

  dialog = await screen.findByRole('dialog', { name: 'Edit Worker' });
  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    'The Worker was saved, but the avatar could not be updated: The file is not a PNG, JPEG or WebP image.',
  );
  // The staged avatar is kept, and the list is not reloaded while the
  // editor is open.
  expect(dialog.querySelector('img')?.getAttribute('src')).toBe(
    'blob:staged-avatar',
  );
  expect(workerListReads).toBe(1);
  expect(writeSummary()).toEqual([
    'POST /api/workers',
    'PUT /api/workers/100/avatar',
  ]);

  // Saving again edits the saved Worker: PATCH, then PUT.
  delete workerFailures['PUT avatar'];
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual([
    'POST /api/workers',
    'PUT /api/workers/100/avatar',
    'PATCH /api/workers/100',
    'PUT /api/workers/100/avatar',
  ]);
  expect(writes[2].body).toEqual({
    name: 'Linh Pham',
    badge_barcode: 'L-1',
    is_active: true,
  });
  // Closing reloads the table.
  expect(
    await screen.findByRole('button', { name: 'Edit Linh Pham' }),
  ).toBeInTheDocument();
  expect(workerListReads).toBe(2);
});

test('an avatar-only change that the server refuses names the avatar alone', async () => {
  await openWorkers();
  workerFailures['PUT avatar'] = {
    status: 413,
    detail: 'The image is larger than 2 MB. Choose a smaller image.',
  };

  fireEvent.click(screen.getByRole('button', { name: 'Edit Mai' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Worker' });
  chooseAvatar(dialog);
  await waitFor(() => expect(dialog.querySelector('img')).not.toBeNull());
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));

  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    'The avatar could not be updated: The image is larger than 2 MB. Choose a smaller image.',
  );
  expect(dialog.querySelector('img')?.getAttribute('src')).toBe(
    'blob:staged-avatar',
  );
});

test('an unanswered avatar upload keeps the editor open with an outcome-neutral note', async () => {
  await openWorkers();
  // From now on the server stops answering the upload and the list.
  workerFailures['PUT avatar'] = 'network';
  workerFailures['GET list'] = 'network';

  fireEvent.click(screen.getByRole('button', { name: '+ New Worker' }));
  const dialog = screen.getByRole('dialog', { name: 'New Worker' });
  fillWorker(dialog, 'Linh Pham', 'L-1');
  chooseAvatar(dialog);
  await waitFor(() => expect(dialog.querySelector('img')).not.toBeNull());
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add Worker' }));

  const alert = await within(dialog).findByRole('alert');
  expect(alert.textContent).toBe(UNKNOWN_OUTCOME);
  expect(alert.textContent).not.toContain('Nothing was changed.');
  // Still mounted, staged avatar kept, Save available, no reload yet.
  expect(screen.getByRole('dialog', { name: 'Edit Worker' })).toBe(dialog);
  expect(dialog.querySelector('img')?.getAttribute('src')).toBe(
    'blob:staged-avatar',
  );
  expect(
    within(dialog).getByRole('button', { name: 'Save changes' }),
  ).toBeEnabled();
  expect(workerListReads).toBe(1);

  // Closing refreshes the list — which now fails into the error state.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(
    await screen.findByText('Worker data could not be loaded.'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(workerListReads).toBe(2);
});

test('offline disables every Worker write control; the table still renders', async () => {
  await openWorkers('unavailable');

  expect(screen.getByRole('button', { name: '+ New Worker' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Edit Alex Tran' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Worker' });
  for (const name of ['Save changes', 'Choose image…', 'Remove avatar']) {
    expect(within(dialog).getByRole('button', { name })).toBeDisabled();
  }
});

test('the Workers section and its editor show no phase numbers', async () => {
  await openWorkers();
  expect(document.body.textContent).not.toMatch(/Phase \d/);

  fireEvent.click(screen.getByRole('button', { name: '+ New Worker' }));
  screen.getByRole('dialog', { name: 'New Worker' });
  expect(document.body.textContent).not.toMatch(/Phase \d/);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));

  fireEvent.click(screen.getByRole('button', { name: 'Edit Alex Tran' }));
  screen.getByRole('dialog', { name: 'Edit Worker' });
  expect(document.body.textContent).not.toMatch(/Phase \d/);
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

  // The Undo reason switch reads the stored value but cannot be saved.
  openSection('Correction permissions');
  const undoReason = await screen.findByRole('switch', {
    name: UNDO_REASON_SWITCH,
  });
  expect(undoReason).toHaveAttribute('aria-checked', 'false');
  expect(undoReason).toBeDisabled();
  fireEvent.click(undoReason);
  expect(writes).toEqual([]);
});

/* ============ Worker sessions (Phase 13 — real timeout policy) ============ */

async function openWorkerSessions(
  status: 'connected' | 'unavailable' = 'connected',
) {
  renderAdmin(status);
  openSection('Worker sessions');
  return (await screen.findByLabelText(
    'Default timeout (minutes)',
  )) as HTMLInputElement;
}

function sessionTimeoutCell(areaName: string): string | null | undefined {
  return screen
    .getByRole('button', { name: `Edit session timeout — ${areaName}` })
    .closest('tr')
    ?.querySelector('td[data-label="Session timeout"]')?.textContent;
}

const BADGE_SWITCHES = [
  'Require badge scan — DONE — Complete Area processing',
  'Require badge scan — QUEUE — Return unfinished quantity to queue',
  'Require badge scan — UNDO — Reverse the last action',
];

test('Worker sessions loads the policy and the Areas, with the three real badge-confirmation switches', async () => {
  state.badgeConfirm.queue = false;
  const field = await openWorkerSessions();

  expect(field).toHaveValue(15);
  expect(screen.getByText('Sliding inactivity timeout')).toBeInTheDocument();
  expect(
    screen.getByText('Badge confirmation for sensitive actions'),
  ).toBeInTheDocument();
  expect(document.body.textContent).toContain(
    'Every sensitive action always ends in a final confirmation question restating the key facts. Each option below upgrades that final step to a required Worker badge scan in Areas with scanned Worker Sessions — the badge records the confirming Worker and completes the action. Areas with a fixed or disabled Worker always keep the question; no badge exists there.',
  );
  expect(document.body.textContent).not.toContain('not available yet');
  const switches = screen.getAllByRole('switch');
  expect(switches.map((item) => item.getAttribute('aria-label'))).toEqual(
    BADGE_SWITCHES,
  );
  expect(switches.map((item) => item.getAttribute('aria-checked'))).toEqual([
    'true',
    'false',
    'true',
  ]);
  expect(
    switches.map((item) => item.querySelector('.swstate')?.textContent),
  ).toEqual(['On', 'Off', 'On']);
  expect(switches[0]).toHaveTextContent(
    'Require a Worker badge scan as the final step of every completion.',
  );
  expect(switches[1]).toHaveTextContent(
    'Require a Worker badge scan as the final step of every queue return.',
  );
  expect(switches[2]).toHaveTextContent(
    'Require a Worker badge scan as the final step of every reversal.',
  );
  // No development notice, no entry action.
  expect(screen.queryByRole('note')).toBeNull();
  expect(screen.queryByRole('button', { name: /New entry/ })).toBeNull();
  expect(document.body.textContent).not.toMatch(/Phase \d/);
  // Unchanged → Save disabled.
  expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
});

test('a badge-confirmation switch saves only its own option, disables the policy controls in flight and re-reads', async () => {
  const field = await openWorkerSessions();
  // A typed but unsaved timeout draft is never sent by a switch.
  fireEvent.change(field, { target: { value: '45' } });
  const save = screen.getByRole('button', { name: 'Save' });
  expect(save).toBeEnabled();
  // Another administrator turned QUEUE off after this page read it.
  state.badgeConfirm.queue = false;

  let release: () => void = () => undefined;
  policyHold = new Promise<void>((resolve) => {
    release = resolve;
  });
  const undo = screen.getByRole('switch', { name: BADGE_SWITCHES[2] });
  fireEvent.click(undo);
  await waitFor(() => expect(undo).toBeDisabled());
  for (const item of screen.getAllByRole('switch')) {
    expect(item).toBeDisabled();
  }
  expect(save).toBeDisabled();
  release();
  policyHold = null;

  await waitFor(() => expect(undo).toHaveAttribute('aria-checked', 'false'));
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/worker-sessions',
      body: { badge_confirm_undo: false },
    },
  ]);
  // The re-read shows every server value: the other administrator's
  // QUEUE change survives and is displayed; the draft stays a draft.
  expect(
    screen.getByRole('switch', { name: BADGE_SWITCHES[1] }),
  ).toHaveAttribute('aria-checked', 'false');
  expect(state.sessionTimeout).toBe(15);
  expect(state.badgeConfirm).toEqual({ done: true, queue: false, undo: false });
  expect(field).toHaveValue(45);
  await waitFor(() => expect(undo).toBeEnabled());
  for (const item of screen.getAllByRole('switch')) {
    expect(item).toBeEnabled();
  }

  // Back on: again exactly one field.
  fireEvent.click(undo);
  await waitFor(() => expect(undo).toHaveAttribute('aria-checked', 'true'));
  expect(writes[1].body).toEqual({ badge_confirm_undo: true });
});

test('a refused badge-confirmation switch keeps its state with the reason; offline every switch is disabled', async () => {
  await openWorkerSessions();
  policyFailure = {
    status: 422,
    detail: 'Each badge-confirmation option must be On or Off.',
  };
  const done = screen.getByRole('switch', { name: BADGE_SWITCHES[0] });
  fireEvent.click(done);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Each badge-confirmation option must be On or Off.',
  );
  expect(done).toHaveAttribute('aria-checked', 'true');
  expect(done).toBeEnabled();
  expect(state.badgeConfirm.done).toBe(true);
  cleanup();

  policyFailure = null;
  writes = [];
  await openWorkerSessions('unavailable');
  for (const item of screen.getAllByRole('switch')) {
    expect(item).toBeDisabled();
    fireEvent.click(item);
  }
  expect(writes).toEqual([]);
});

test('Worker sessions saves a whole-minute default and refuses anything else in place', async () => {
  const field = await openWorkerSessions();
  const save = screen.getByRole('button', { name: 'Save' });

  for (const value of ['0', '721', '', '1.5']) {
    fireEvent.change(field, { target: { value } });
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Enter a whole number of minutes from 1 to 720.',
    );
    expect(save).toBeDisabled();
  }
  expect(writes).toEqual([]);

  fireEvent.change(field, { target: { value: '30' } });
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.click(save);
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Default timeout saved.',
  );
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/worker-sessions',
      body: { worker_session_timeout_minutes: 30 },
    },
  ]);
  // The re-read shows the stored default in the overrides table.
  await waitFor(() =>
    expect(sessionTimeoutCell('Lathe')).toBe('Default · 30 min'),
  );
});

test('the per-Area overrides table edits, clears and cancels an Area override', async () => {
  state.areas[1].worker_session_timeout_minutes = 5;
  state.areas[1].is_active = false;
  await openWorkerSessions();

  expect(sessionTimeoutCell('Lathe')).toBe('Default · 15 min');
  expect(sessionTimeoutCell('Stockroom')).toBe('5 min');
  const stockroomRow = screen
    .getByRole('button', { name: 'Edit session timeout — Stockroom' })
    .closest('tr')!;
  expect(stockroomRow.textContent).toContain('Stockroom (inactive)');
  expect(stockroomRow.textContent).toContain('Disabled');

  // Override Lathe with 20 minutes.
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit session timeout — Lathe' }),
  );
  let dialog = screen.getByRole('dialog', { name: 'Session timeout — Lathe' });
  expect(
    within(dialog).getByRole('radio', {
      name: 'Use the default (15 minutes)',
    }),
  ).toBeChecked();
  fireEvent.click(within(dialog).getByRole('radio', { name: 'Override' }));
  const minutes = within(dialog).getByLabelText('Timeout (minutes)');
  fireEvent.change(minutes, { target: { value: '0' } });
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'Enter a whole number of minutes from 1 to 720.',
  );
  expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled();
  fireEvent.change(minutes, { target: { value: '20' } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[0]).toEqual({
    method: 'PATCH',
    url: '/api/areas/1',
    body: { worker_session_timeout_minutes: 20 },
  });
  await waitFor(() => expect(sessionTimeoutCell('Lathe')).toBe('20 min'));

  // Back to the default: null clears the override.
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit session timeout — Stockroom' }),
  );
  dialog = screen.getByRole('dialog', { name: 'Session timeout — Stockroom' });
  expect(within(dialog).getByRole('radio', { name: 'Override' })).toBeChecked();
  expect(within(dialog).getByLabelText('Timeout (minutes)')).toHaveValue(5);
  fireEvent.click(
    within(dialog).getByRole('radio', { name: 'Use the default (15 minutes)' }),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[1]).toEqual({
    method: 'PATCH',
    url: '/api/areas/2',
    body: { worker_session_timeout_minutes: null },
  });
  await waitFor(() =>
    expect(sessionTimeoutCell('Stockroom')).toBe('Default · 15 min'),
  );

  // Cancel (Esc) sends nothing.
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit session timeout — Lathe' }),
  );
  dialog = screen.getByRole('dialog', { name: 'Session timeout — Lathe' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(writes).toHaveLength(2);
});

test('Worker sessions renders server refusals in place, a failed load with Retry, and blocks saving offline', async () => {
  // A server refusal of an override keeps the dialog and the draft.
  const field = await openWorkerSessions();
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit session timeout — Lathe' }),
  );
  const dialog = screen.getByRole('dialog', {
    name: 'Session timeout — Lathe',
  });
  fireEvent.click(within(dialog).getByRole('radio', { name: 'Override' }));
  fireEvent.change(within(dialog).getByLabelText('Timeout (minutes)'), {
    target: { value: '45' },
  });
  vi.mocked(fetch).mockImplementationOnce(async () =>
    json({ detail: AREA_TIMEOUT_REFUSAL }, 422),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    AREA_TIMEOUT_REFUSAL,
  );
  expect(within(dialog).getByLabelText('Timeout (minutes)')).toHaveValue(45);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));

  // A server refusal of the default keeps the panel and the draft.
  policyFailure = {
    status: 422,
    detail:
      'The Worker session timeout must be a whole number of minutes from 1 to 720.',
  };
  fireEvent.change(field, { target: { value: '60' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'The Worker session timeout must be a whole number of minutes from 1 to 720.',
  );
  expect(field).toHaveValue(60);
  cleanup();

  // Load failure → error state with Retry, which recovers.
  policyFailure = { status: 500, detail: 'Database unavailable.' };
  renderAdmin();
  openSection('Worker sessions');
  expect(
    await screen.findByText('Worker session settings could not be loaded.'),
  ).toBeInTheDocument();
  policyFailure = null;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByLabelText('Default timeout (minutes)')).toHaveValue(
    15,
  );
  cleanup();

  // Offline: reading works, every Save is disabled.
  const offlineField = await openWorkerSessions('unavailable');
  fireEvent.change(offlineField, { target: { value: '30' } });
  expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit session timeout — Lathe' }),
  );
  expect(
    within(
      screen.getByRole('dialog', { name: 'Session timeout — Lathe' }),
    ).getByRole('button', { name: 'Save' }),
  ).toBeDisabled();
});

/* ============ Correction permissions (Phase 13 — Undo reason policy) ============ */

const UNDO_REASON_SWITCH = 'Require a reason for every Undo';

async function openCorrectionPermissions() {
  renderAdmin();
  openSection('Correction permissions');
  return screen.findByRole('switch', { name: UNDO_REASON_SWITCH });
}

test('Correction permissions shows the real Undo reason switch and states the role-based part honestly', async () => {
  const toggle = await openCorrectionPermissions();

  expect(toggle).toHaveAttribute('aria-checked', 'false');
  expect(toggle.querySelector('.swstate')).toHaveTextContent('Off');
  expect(toggle).toHaveTextContent(
    'Applies to every Area and every Scan Station.',
  );
  expect(screen.getByRole('heading', { name: 'Undo reason' })).toBeVisible();
  expect(document.body.textContent).toContain(
    'When On, every Undo at a Scan Station asks for a reason before the reversal can be confirmed, and a reversal without a reason is refused. The reason is recorded with the reversal and shown in Tracking. When Off, Undo asks for no reason.',
  );
  expect(
    screen.getByRole('heading', { name: 'Who may undo or correct' }),
  ).toBeVisible();
  expect(
    screen.getByText(
      'Role-based correction permissions are not configurable yet.',
    ),
  ).toBeInTheDocument();
  expect(screen.getAllByRole('switch')).toHaveLength(1);
  expect(screen.queryByRole('button', { name: /New entry/ })).toBeNull();
  expect(screen.queryByRole('note')).toBeNull();
  const main = document.querySelector('.ad-main')!;
  expect(main.textContent).not.toContain('not available yet');
  expect(main.textContent).not.toContain('Roles & permissions');
  expect(main.textContent).not.toMatch(/sign-in/i);
  expect(document.body.textContent).not.toMatch(/Phase \d/);
});

test('the Undo reason switch PUTs exactly its toggled value, is disabled in flight and re-reads', async () => {
  const toggle = await openCorrectionPermissions();

  let release: () => void = () => undefined;
  policyHold = new Promise<void>((resolve) => {
    release = resolve;
  });
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toBeDisabled());
  release();
  policyHold = null;

  await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'));
  expect(toggle.querySelector('.swstate')).toHaveTextContent('On');
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/correction-permissions',
      body: { undo_reason_required: true },
    },
  ]);
  expect(state.undoReasonRequired).toBe(true);
  await waitFor(() => expect(toggle).toBeEnabled());

  // Back off: again exactly the one field.
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'false'));
  expect(writes[1].body).toEqual({ undo_reason_required: false });
  // The Worker sessions policy is never written by this section.
  expect(writes.every((item) => !item.url.includes('worker-sessions'))).toBe(
    true,
  );
});

test('a refused Undo reason switch keeps the stored value with the reason; a failed load offers Retry', async () => {
  const toggle = await openCorrectionPermissions();
  correctionFailure = {
    status: 422,
    detail: 'The Undo reason setting must be On or Off.',
  };
  fireEvent.click(toggle);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'The Undo reason setting must be On or Off.',
  );
  expect(toggle).toHaveAttribute('aria-checked', 'false');
  expect(toggle).toBeEnabled();
  expect(state.undoReasonRequired).toBe(false);
  cleanup();

  correctionFailure = { status: 500, detail: 'Database unavailable.' };
  renderAdmin();
  openSection('Correction permissions');
  expect(
    await screen.findByText(
      'Correction permission settings could not be loaded.',
    ),
  ).toBeInTheDocument();
  correctionFailure = null;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(
    await screen.findByRole('switch', { name: UNDO_REASON_SWITCH }),
  ).toHaveAttribute('aria-checked', 'false');
});

/* ============ Department display settings (Production Board rotation) ============ */

async function openDepartmentDisplay(
  status: 'connected' | 'unavailable' = 'connected',
) {
  renderAdmin(status);
  openSection('Department display settings');
  return screen.findByRole('button', {
    name: 'Edit rotation timing — Machine Shop',
  });
}

function rotationRow(name: string): string[] {
  const row = screen
    .getByRole('button', { name: `Edit rotation timing — ${name}` })
    .closest('tr') as HTMLElement;
  return Array.from(row.querySelectorAll('td'), (td) => td.textContent ?? '');
}

test('AD-1: Department display settings lists every Department with its rotation timing', async () => {
  state.departments.push({
    id: 2,
    name: 'Finishing',
    is_active: false,
    board_seconds_per_row: 4,
    board_min_page_seconds: 20,
  });
  await openDepartmentDisplay();

  expect(
    screen.getByRole('heading', { name: 'Department display settings' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('heading', { name: 'Production Board rotation' }),
  ).toBeInTheDocument();
  expect(rotationRow('Machine Shop').slice(0, 3)).toEqual([
    'Machine Shop',
    '3 s',
    '6 s',
  ]);
  expect(rotationRow('Finishing').slice(0, 3)).toEqual([
    'Finishing (inactive)',
    '4 s',
    '20 s',
  ]);
  expect(screen.queryByRole('button', { name: '+ New entry' })).toBeNull();
  expect(document.body.textContent).not.toMatch(/Phase \d/);
});

test('AD-2: the rotation editor validates in place, previews, and PATCHes only the changed fields', async () => {
  const edit = await openDepartmentDisplay();
  edit.focus();
  fireEvent.click(edit);
  const dialog = screen.getByRole('dialog', {
    name: 'Production Board rotation — Machine Shop',
  });
  const seconds = within(dialog).getByLabelText('Seconds per displayed row');
  const dwell = within(dialog).getByLabelText('Minimum page dwell (seconds)');
  await waitFor(() => expect(document.activeElement).toBe(seconds));
  const save = within(dialog).getByRole('button', { name: 'Save' });
  // Unchanged: nothing to save.
  expect(save).toBeDisabled();

  fireEvent.change(seconds, { target: { value: '0' } });
  expect(within(dialog).getByRole('alert')).toHaveTextContent(E_B1);
  expect(save).toBeDisabled();
  fireEvent.change(seconds, { target: { value: '2' } });
  fireEvent.change(dwell, { target: { value: '301' } });
  expect(within(dialog).getByRole('alert')).toHaveTextContent(E_B2);
  expect(save).toBeDisabled();
  fireEvent.change(dwell, { target: { value: '10' } });
  expect(within(dialog).queryByRole('alert')).toBeNull();
  expect(dialog).toHaveTextContent(
    'A page showing 1 row stays 10 s; a page showing 10 rows stays 20 s.',
  );
  expect(save).toBeEnabled();
  fireEvent.click(save);

  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toEqual([
    {
      method: 'PATCH',
      url: '/api/departments/1',
      body: { board_seconds_per_row: 2, board_min_page_seconds: 10 },
    },
  ]);
  await waitFor(() =>
    expect(rotationRow('Machine Shop').slice(1, 3)).toEqual(['2 s', '10 s']),
  );
  expect(document.activeElement).toBe(
    screen.getByRole('button', { name: 'Edit rotation timing — Machine Shop' }),
  );

  // Only the dwell changes: only the dwell is sent.
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit rotation timing — Machine Shop' }),
  );
  const again = screen.getByRole('dialog', {
    name: 'Production Board rotation — Machine Shop',
  });
  const againSave = within(again).getByRole('button', { name: 'Save' });
  const againSeconds = within(again).getByLabelText(
    'Seconds per displayed row',
  );
  // A typed value restored to its opened value is no change.
  fireEvent.change(againSeconds, { target: { value: '5' } });
  expect(againSave).toBeEnabled();
  fireEvent.change(againSeconds, { target: { value: '2' } });
  expect(againSave).toBeDisabled();
  fireEvent.change(
    within(again).getByLabelText('Minimum page dwell (seconds)'),
    { target: { value: '9' } },
  );
  fireEvent.click(againSave);
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[1]).toEqual({
    method: 'PATCH',
    url: '/api/departments/1',
    body: { board_min_page_seconds: 9 },
  });
  expect(state.departments[0]).toMatchObject({
    board_seconds_per_row: 2,
    board_min_page_seconds: 9,
  });
});

test('AD-3: offline the rotation editor cannot save; the table still reads', async () => {
  const edit = await openDepartmentDisplay('unavailable');
  expect(rotationRow('Machine Shop').slice(1, 3)).toEqual(['3 s', '6 s']);
  fireEvent.click(edit);
  const dialog = screen.getByRole('dialog', {
    name: 'Production Board rotation — Machine Shop',
  });
  fireEvent.change(within(dialog).getByLabelText('Seconds per displayed row'), {
    target: { value: '4' },
  });
  expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled();
});

test('AD-4: a server refusal stays in place in the rotation editor with the typed values', async () => {
  fireEvent.click(await openDepartmentDisplay());
  const dialog = screen.getByRole('dialog', {
    name: 'Production Board rotation — Machine Shop',
  });
  fireEvent.change(within(dialog).getByLabelText('Seconds per displayed row'), {
    target: { value: '4' },
  });
  vi.mocked(fetch).mockImplementationOnce(async () =>
    json({ detail: E_B1 }, 422),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(E_B1);
  expect(
    within(dialog).getByLabelText('Seconds per displayed row'),
  ).toHaveValue(4);
  expect(screen.getByRole('dialog')).toBe(dialog);
  expect(state.departments[0].board_seconds_per_row).toBe(3);
});

const ROTATION_UNKNOWN_OUTCOME =
  'The server did not answer — this change may or may not have been saved. Close this window to refresh the table, then check the timing before trying again.';

test('AD-4: Cancel, Escape and the backdrop are ignored while a rotation save is in flight', async () => {
  const edit = await openDepartmentDisplay();
  // Hold the PATCH before the fake server applies it.
  let releasePatch = () => {};
  const patchHeld = new Promise<void>((resolve) => {
    releasePatch = resolve;
  });
  let patchRequested = false;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if (init?.method === 'PATCH') {
      patchRequested = true;
      await patchHeld;
    }
    return handle(String(input), init);
  });

  fireEvent.click(edit);
  const dialog = screen.getByRole('dialog', {
    name: 'Production Board rotation — Machine Shop',
  });
  fireEvent.change(within(dialog).getByLabelText('Seconds per displayed row'), {
    target: { value: '5' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(patchRequested).toBe(true));

  const cancel = within(dialog).getByRole('button', { name: 'Cancel (Esc)' });
  expect(cancel).toBeDisabled();
  fireEvent.click(cancel);
  fireEvent.keyDown(dialog, { key: 'Escape' });
  fireEvent.mouseDown(dialog.parentElement as HTMLElement);
  expect(screen.getByRole('dialog')).toBe(dialog);

  releasePatch();
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  await waitFor(() =>
    expect(rotationRow('Machine Shop').slice(1, 3)).toEqual(['5 s', '6 s']),
  );
});

test('AD-4: a rotation save whose answer is lost is an unknown outcome, and closing re-reads the table', async () => {
  fireEvent.click(await openDepartmentDisplay());
  const dialog = screen.getByRole('dialog', {
    name: 'Production Board rotation — Machine Shop',
  });
  fireEvent.change(within(dialog).getByLabelText('Seconds per displayed row'), {
    target: { value: '5' },
  });
  // The server commits the PATCH; the answer never arrives.
  vi.mocked(fetch).mockImplementationOnce(async (input, init) => {
    await handle(String(input), init);
    throw new TypeError('Failed to fetch');
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    ROTATION_UNKNOWN_OUTCOME,
  );
  expect(dialog).not.toHaveTextContent('Nothing was changed');
  expect(state.departments[0].board_seconds_per_row).toBe(5);

  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  await waitFor(() =>
    expect(rotationRow('Machine Shop').slice(1, 3)).toEqual(['5 s', '6 s']),
  );
});

test('Department display settings: a failed load offers Retry', async () => {
  vi.mocked(fetch).mockImplementation(async (input, init) =>
    String(input) === '/api/departments'
      ? json({ detail: 'Database unavailable.' }, 500)
      : handle(String(input), init),
  );
  renderAdmin();
  openSection('Department display settings');
  expect(
    await screen.findByText('Department display settings could not be loaded.'),
  ).toBeInTheDocument();
  vi.mocked(fetch).mockImplementation(async (input, init) =>
    handle(String(input), init),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(
    await screen.findByRole('button', {
      name: 'Edit rotation timing — Machine Shop',
    }),
  ).toBeInTheDocument();
});

/* ============ Settings (Due Soon warning) ============ */

async function openSettings(status: 'connected' | 'unavailable' = 'connected') {
  renderAdmin(status);
  openSection('Settings');
  return screen.findByLabelText('Minimum warning days');
}

test('AD-5: the Due Soon warning panel loads, explains, validates and PUTs exactly the three fields', async () => {
  const min = await openSettings();
  const percent = screen.getByLabelText('Lead-time warning percentage (%)');
  const max = screen.getByLabelText('Maximum warning days');
  expect(min).toHaveValue(2);
  expect(percent).toHaveValue(15);
  expect(max).toHaveValue(7);
  expect(
    screen.getByRole('heading', { name: 'Due Soon warning' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText('10-day lead → warns 2 days ahead'),
  ).toBeInTheDocument();
  expect(
    screen.getByText('30-day lead → warns 5 days ahead'),
  ).toBeInTheDocument();
  expect(
    screen.getByText('90-day lead → warns 7 days ahead'),
  ).toBeInTheDocument();
  expect(
    screen.getByText('Other application settings are not available yet.'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '+ New entry' })).toBeNull();
  expect(document.body.textContent).not.toMatch(/Phase \d/);
  const save = screen.getByRole('button', { name: 'Save' });
  expect(save).toBeDisabled();

  // Range errors under the failing field; the order rule once both
  // clamps are valid.
  fireEvent.change(min, { target: { value: '-1' } });
  expect(screen.getByRole('alert')).toHaveTextContent(E_D1);
  fireEvent.change(min, { target: { value: '2' } });
  fireEvent.change(percent, { target: { value: '0' } });
  expect(screen.getByRole('alert')).toHaveTextContent(E_D3);
  fireEvent.change(percent, { target: { value: '15' } });
  fireEvent.change(max, { target: { value: '366' } });
  expect(screen.getByRole('alert')).toHaveTextContent(E_D2);
  fireEvent.change(min, { target: { value: '6' } });
  fireEvent.change(max, { target: { value: '5' } });
  expect(screen.getByRole('alert')).toHaveTextContent(E_D4);
  expect(screen.queryByText(/-day lead → warns/)).toBeNull();
  expect(save).toBeDisabled();

  fireEvent.change(min, { target: { value: '1' } });
  fireEvent.change(percent, { target: { value: '20' } });
  fireEvent.change(max, { target: { value: '5' } });
  expect(screen.queryByRole('alert')).toBeNull();
  expect(
    screen.getByText('10-day lead → warns 2 days ahead'),
  ).toBeInTheDocument();
  expect(
    screen.getByText('30-day lead → warns 5 days ahead'),
  ).toBeInTheDocument();
  fireEvent.click(save);
  expect(await screen.findByRole('status')).toHaveTextContent(
    '✓ Due Soon warning saved.',
  );
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/due-soon',
      body: {
        due_soon_min_days: 1,
        due_soon_lead_time_percent: 20,
        due_soon_max_days: 5,
      },
    },
  ]);
  expect(state.dueSoon).toEqual({ min: 1, percent: 20, max: 5 });
  await waitFor(() => expect(save).toBeDisabled());
});

test('AD-5: a Due Soon save whose answer is lost is an unknown outcome and re-reads the stored policy', async () => {
  const min = await openSettings();
  fireEvent.change(min, { target: { value: '3' } });
  const save = screen.getByRole('button', { name: 'Save' });
  // The server commits the PUT; the answer never arrives.
  vi.mocked(fetch).mockImplementationOnce(async (input, init) => {
    await handle(String(input), init);
    throw new TypeError('Failed to fetch');
  });
  fireEvent.click(save);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'The server did not answer — this change may or may not have been saved. Check the Due Soon warning before trying again; saving the same values again is safe.',
  );
  expect(document.body.textContent).not.toMatch(/Nothing was changed/);
  expect(screen.queryByRole('status')).toBeNull();
  expect(state.dueSoon).toEqual({ min: 3, percent: 15, max: 7 });
  // The re-read stored policy now equals the typed values: nothing left
  // to save, and the notice stays.
  await waitFor(() => expect(save).toBeDisabled());
  expect(screen.getByRole('alert')).toHaveTextContent(
    'may or may not have been saved',
  );
  expect(min).toHaveValue(3);
});

test('AD-5: offline the Due Soon warning cannot be saved; a failed load offers Retry', async () => {
  const min = await openSettings('unavailable');
  fireEvent.change(min, { target: { value: '3' } });
  expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  cleanup();

  dueSoonFailure = { status: 500, detail: 'Database unavailable.' };
  renderAdmin();
  openSection('Settings');
  expect(
    await screen.findByText('Due Soon warning settings could not be loaded.'),
  ).toBeInTheDocument();
  dueSoonFailure = null;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByLabelText('Minimum warning days')).toHaveValue(2);
});
