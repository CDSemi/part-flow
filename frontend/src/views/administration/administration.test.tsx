import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import type { SessionUser } from '../../api/session';
import { ConnectivityContext } from '../../app/connectivity-context';
import { SessionContext, hasPermission } from '../../app/session-context';
import type { SessionValue } from '../../app/session-context';
import { prepareImageUpload } from '../../components/image-upload';
import { AdministrationView } from './AdministrationView';

// Administration (GUI_DESIGN §9): the minimum environment setup
// sections — Departments, Areas, Operations, Scan Stations, Barcode
// configuration — Workers, Users, Roles & permissions, Worker sessions,
// Correction permissions, Department display settings, Settings and
// History archival & purge read and write the real /api surface (faked
// in-memory here with the same routes and semantics). Machine
// assignment is a read-only statement; Scan behavior presents itself
// honestly as not available yet.

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

interface RoleRow {
  id: number;
  name: string;
  permissions: string[];
}

interface UserRow {
  id: number;
  login_name: string;
  display_name: string;
  role_id: number;
  is_active: boolean;
  avatar_updated_at: string | null;
  /** How the user can sign in (default: no password). */
  sign_in_state?:
    'NO_PASSWORD' | 'TEMPORARY_PASSWORD' | 'PASSWORD_SET' | 'LOCKED';
}

/** The fake Roles and Users routes a test can make fail. */
type RoleRoute = 'GET list' | 'POST' | 'PATCH';
type UserRoute =
  | 'GET list'
  | 'POST'
  | 'PATCH'
  | 'PUT avatar'
  | 'DELETE avatar'
  | 'PUT password';

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
  /** Named roles with their grants (the three seeded §20 roles). */
  roles: RoleRow[];
  /** Application Users (none seeded). */
  users: UserRow[];
  /** `application_policy` default Worker session timeout (minutes). */
  sessionTimeout: number;
  /** `application_policy` badge-confirmation options. */
  badgeConfirm: { done: boolean; queue: boolean; undo: boolean };
  /** `application_policy` Undo reason policy. */
  undoReasonRequired: boolean;
  /** `application_policy` Due Soon warning policy. */
  dueSoon: { min: number; percent: number; max: number };
  /** `application_policy` Movement-history retention period (months;
   * null = no retention period). */
  retentionMonths: number | null;
  nextId: number;
}

const T0 = '2026-08-01T00:00:00.000Z';
const ALEX_AVATAR_AT = '2026-09-01T08:00:00.123456+00:00';

// The seeded roles: exactly the grants PROJECT_PROFILE §20 states.
const ADMINISTRATOR_GRANTS = [
  'MANAGE_DEPARTMENTS',
  'MANAGE_AREAS',
  'MANAGE_OPERATIONS',
  'MANAGE_MACHINES',
  'MANAGE_WORKERS',
  'MANAGE_USERS_AND_ROLES',
  'MANAGE_SCAN_STATIONS',
  'MANAGE_BARCODE_CONFIGURATION',
  'MANAGE_ROUTE_TEMPLATES',
  'MANAGE_PART_NUMBER_MASTER',
  'MANAGE_SCAN_BEHAVIOR',
  'MANAGE_WORKER_SESSION_POLICIES',
  'MANAGE_CORRECTION_PERMISSIONS',
  'EDIT_WORK_ORDER_DEMAND',
  'EDIT_WORK_ORDER_ALLOCATION',
  'PERFORM_HISTORICAL_CORRECTIONS',
  'CONFIGURE_SYSTEM_SETTINGS',
];
const MANAGER_GRANTS = [
  'VIEW_PRODUCTION_DATA',
  'MANAGE_WORK_ORDERS',
  'EDIT_WORK_ORDER_DEMAND',
  'SET_DEMAND_PRIORITY',
  'REORDER_HOT_ITEMS',
  'ASSIGN_ROUTES',
  'PERFORM_QUANTITY_CORRECTIONS',
  'EDIT_WORK_ORDER_ALLOCATION',
  'RESOLVE_EXCEPTIONAL_SITUATIONS',
  'EXPORT_REPORTS',
];
const OPERATOR_GRANTS = [
  'SCAN_PN_BARCODES',
  'SCAN_MACHINE_BARCODES',
  'SCAN_WORKER_BARCODES',
  'RECEIVE_QUANTITY',
  'ASSIGN_QUANTITY_TO_MACHINE',
  'CONFIRM_QUANTITY',
  'COMPLETE_INTO_STOCKROOM',
  'CONFIRM_SUGGESTED_ALLOCATION',
  'ADJUST_SUGGESTED_ALLOCATION',
  'UNDO_RECENT_SCANS',
];
const ADMINISTRATOR_ID = 1;
const MANAGER_ID = 2;
const OPERATOR_ID = 3;

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
    roles: [
      {
        id: ADMINISTRATOR_ID,
        name: 'Administrator',
        permissions: [...ADMINISTRATOR_GRANTS],
      },
      { id: MANAGER_ID, name: 'Manager', permissions: [...MANAGER_GRANTS] },
      { id: OPERATOR_ID, name: 'Operator', permissions: [...OPERATOR_GRANTS] },
    ],
    users: [],
    sessionTimeout: 15,
    badgeConfirm: { done: true, queue: true, undo: true },
    undoReasonRequired: false,
    dueSoon: { min: 2, percent: 15, max: 7 },
    retentionMonths: null,
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
/** A refusal of every `/api/policies/data-retention` call, if set. */
let retentionFailure: { status: number; detail: string } | null;
let roleFailures: Partial<Record<RoleRoute, FakeFailure>>;
let userFailures: Partial<Record<UserRoute, FakeFailure>>;
/** While set, a role PATCH stays pending until it resolves. */
let roleHold: Promise<void> | null;
/** While set, a user password PUT stays pending until it resolves. */
let userHold: Promise<void> | null;

interface SignInPolicyRow {
  user_session_expires: boolean;
  user_session_days: number;
  sign_in_lockout_attempts: number;
  sign_in_lockout_minutes: number;
  require_password_change: boolean;
}

const DEFAULT_SIGN_IN_POLICY: SignInPolicyRow = {
  user_session_expires: true,
  user_session_days: 30,
  sign_in_lockout_attempts: 10,
  sign_in_lockout_minutes: 15,
  require_password_change: true,
};
let signInPolicy: SignInPolicyRow;
/** A refusal (or no answer) of every `/api/policies/sign-in` call. */
let signInPolicyFailure: FakeFailure | null;

const E_B1 = 'Seconds per displayed row must be a whole number from 1 to 60.';
const E_B2 =
  'The minimum page dwell must be a whole number of seconds from 1 to 300.';
const E_D1 = 'Minimum warning days must be a whole number from 0 to 365.';
const E_D2 = 'Maximum warning days must be a whole number from 0 to 365.';
const E_D3 =
  'The lead-time warning percentage must be a whole number from 1 to 100.';
const E_D4 =
  'Minimum warning days cannot be greater than maximum warning days.';
const E_T1 =
  'The retention period must be a whole number of months from 12 to 1200, or no retention period.';
const E_T2 = 'Enter a whole number of months from 12 to 1200.';

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
  if (url === '/api/roles' || url.startsWith('/api/roles/')) {
    return handleRoles(url, method, body);
  }
  if (url === '/api/users' || url.startsWith('/api/users/')) {
    return handleUsers(url, method, body);
  }
  if (url === '/api/policies/sign-in') {
    return handleSignInPolicy(method, body);
  }
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
  if (url === '/api/policies/data-retention') {
    if (retentionFailure) {
      return json({ detail: retentionFailure.detail }, retentionFailure.status);
    }
    if (method === 'PUT') {
      // Exactly one required field: whole months, or null (no period).
      const months = body.retention_period_months;
      if (
        Object.keys(body).length !== 1 ||
        !('retention_period_months' in body) ||
        (months !== null &&
          (typeof months !== 'number' || !Number.isInteger(months)))
      ) {
        return json({ detail: 'Invalid request.' }, 422);
      }
      if (months !== null && !wholeIn(months, 12, 1200)) {
        return json({ detail: E_T1 }, 422);
      }
      state.retentionMonths = months as number | null;
    }
    return json({
      retention_period_months: state.retentionMonths,
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
  if (match[2] === '/avatar' && method === 'DELETE') {
    const failure = workerFailure('DELETE avatar');
    if (failure) return failure;
    worker.avatar_updated_at = null;
    return json(stamp(worker));
  }
  return json({ detail: `Unhandled fake route: ${method} ${url}` }, 500);
}

/** The configured failure of one fake route, if any. */
function routeFailure(failure: FakeFailure | undefined): Response | null {
  if (!failure) return null;
  if (failure === 'network') throw new TypeError('Failed to fetch');
  return json({ detail: failure.detail }, failure.status);
}

function roleWire(role: RoleRow) {
  return {
    ...stamp({ id: role.id, name: role.name }),
    permissions: [...role.permissions].sort(),
    user_count: state.users.filter((u) => u.role_id === role.id).length,
  };
}

const E_R2 = 'A role with this name already exists.';

async function handleRoles(
  url: string,
  method: string,
  body: Record<string, unknown>,
): Promise<Response> {
  if (url === '/api/roles' && method === 'GET') {
    const ordered = [...state.roles].sort(
      (a, b) => a.name.localeCompare(b.name) || a.id - b.id,
    );
    return (
      routeFailure(roleFailures['GET list']) ?? json(ordered.map(roleWire))
    );
  }
  if (url === '/api/roles' && method === 'POST') {
    const failure = routeFailure(roleFailures.POST);
    if (failure) return failure;
    const name = String(body.name).trim();
    if (state.roles.some((r) => r.name === name)) {
      return json({ detail: E_R2 }, 409);
    }
    const role: RoleRow = {
      id: state.nextId++,
      name,
      permissions: [...new Set((body.permissions as string[]) ?? [])],
    };
    state.roles.push(role);
    return json(roleWire(role), 201);
  }
  const match = /^\/api\/roles\/(\d+)$/.exec(url);
  const role = state.roles.find((r) => r.id === Number(match?.[1]));
  if (!match || !role || method !== 'PATCH') {
    return json({ detail: `Role ${match?.[1]} does not exist.` }, 404);
  }
  if (roleHold) await roleHold;
  const failure = routeFailure(roleFailures.PATCH);
  if (failure) return failure;
  if (typeof body.name === 'string') {
    const name = body.name.trim();
    if (state.roles.some((r) => r.name === name && r.id !== role.id)) {
      return json({ detail: E_R2 }, 409);
    }
    role.name = name;
  }
  // Delta semantics: granting a held key or revoking a missing one is
  // a no-op.
  const grant = (body.grant_permissions as string[] | undefined) ?? [];
  const revoke = (body.revoke_permissions as string[] | undefined) ?? [];
  role.permissions = [...new Set([...role.permissions, ...grant])].filter(
    (key) => !revoke.includes(key),
  );
  return json(roleWire(role));
}

/** The server's two user response shapes: `sign_in_state` only for a
 * caller who may manage users and roles, absent for everyone else. */
function userWire(user: UserRow) {
  const { sign_in_state, ...profile } = user;
  return {
    ...stamp(profile),
    role_name: state.roles.find((r) => r.id === user.role_id)!.name,
    ...(hasPermission(session.user, 'MANAGE_USERS_AND_ROLES')
      ? { sign_in_state: sign_in_state ?? 'NO_PASSWORD' }
      : {}),
  };
}

/** `GET`/`PUT /api/policies/sign-in` (partial merge, server ranges). */
function handleSignInPolicy(
  method: string,
  body: Record<string, unknown>,
): Response {
  const failure = routeFailure(signInPolicyFailure ?? undefined);
  if (failure) return failure;
  if (method === 'PUT') {
    const ranges: Record<string, [number, number]> = {
      user_session_days: [1, 365],
      sign_in_lockout_attempts: [3, 100],
      sign_in_lockout_minutes: [1, 1440],
    };
    for (const [key, value] of Object.entries(body)) {
      if (key in ranges) {
        const [min, max] = ranges[key];
        if (!wholeIn(value, min, max)) {
          return json({ detail: 'Out of range.' }, 422);
        }
      } else if (typeof value !== 'boolean' || !(key in signInPolicy)) {
        return json({ detail: 'Invalid request.' }, 422);
      }
    }
    signInPolicy = { ...signInPolicy, ...body };
  }
  return json({ ...signInPolicy, updated_at: T0 });
}

function duplicateLogin(login: string, exceptId?: number): Response | null {
  const holder = state.users.find(
    (u) => u.login_name === login && u.id !== exceptId,
  );
  if (!holder) return null;
  const suffix = holder.is_active ? '' : ' (inactive)';
  return json(
    {
      detail: `This login name is already used by ${holder.display_name}${suffix}.`,
    },
    409,
  );
}

async function handleUsers(
  url: string,
  method: string,
  body: Record<string, unknown>,
): Promise<Response> {
  if (url === '/api/users' && method === 'GET') {
    const ordered = [...state.users].sort(
      (a, b) => a.display_name.localeCompare(b.display_name) || a.id - b.id,
    );
    return (
      routeFailure(userFailures['GET list']) ?? json(ordered.map(userWire))
    );
  }
  if (url === '/api/users' && method === 'POST') {
    const failure = routeFailure(userFailures.POST);
    if (failure) return failure;
    const login = String(body.login_name).trim().toLowerCase();
    const duplicate = duplicateLogin(login);
    if (duplicate) return duplicate;
    const user: UserRow = {
      id: state.nextId++,
      login_name: login,
      display_name: String(body.display_name).trim(),
      role_id: Number(body.role_id),
      is_active: true,
      avatar_updated_at: null,
    };
    state.users.push(user);
    return json(userWire(user), 201);
  }
  const match = /^\/api\/users\/(\d+)(\/avatar|\/password)?$/.exec(url);
  const user = state.users.find((u) => u.id === Number(match?.[1]));
  if (!match || !user) {
    return json({ detail: `User ${match?.[1]} does not exist.` }, 404);
  }
  if (!match[2] && method === 'PATCH') {
    const failure = routeFailure(userFailures.PATCH);
    if (failure) return failure;
    if (typeof body.login_name === 'string') {
      const login = body.login_name.trim().toLowerCase();
      const duplicate = duplicateLogin(login, user.id);
      if (duplicate) return duplicate;
      user.login_name = login;
    }
    if (typeof body.display_name === 'string') {
      user.display_name = body.display_name.trim();
    }
    if (typeof body.role_id === 'number') user.role_id = body.role_id;
    if (typeof body.is_active === 'boolean') user.is_active = body.is_active;
    return json(userWire(user));
  }
  if (match[2] === '/password' && method === 'PUT') {
    if (userHold) await userHold;
    const failure = routeFailure(userFailures['PUT password']);
    if (failure) return failure;
    user.sign_in_state = 'TEMPORARY_PASSWORD';
    return json(userWire(user));
  }
  if (match[2] === '/avatar' && method === 'PUT') {
    const failure = routeFailure(userFailures['PUT avatar']);
    if (failure) return failure;
    avatarVersion += 1;
    user.avatar_updated_at = `2026-10-05T10:00:00.00000${avatarVersion}+00:00`;
    return json(userWire(user));
  }
  if (match[2] && method === 'DELETE') {
    const failure = routeFailure(userFailures['DELETE avatar']);
    if (failure) return failure;
    user.avatar_updated_at = null;
    return json(userWire(user));
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
  retentionFailure = null;
  roleFailures = {};
  userFailures = {};
  roleHold = null;
  signInPolicy = { ...DEFAULT_SIGN_IN_POLICY };
  signInPolicyFailure = null;
  userHold = null;
  session = sessionValue();
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

/**
 * An explicit user sign-in for the sections that read it (Users, the
 * Settings → User sign-in panel): signed out unless a test signs a user
 * in. The session provider has its own suite.
 */
function sessionValue(user: SessionUser | null = null): SessionValue {
  return {
    status: user ? 'signed-in' : 'signed-out',
    user,
    setupOpen: false,
    can: (permission) => hasPermission(user, permission),
    openSignIn: vi.fn(),
    openSetup: vi.fn(),
    openChangePassword: vi.fn(),
    signOut: vi.fn(async () => {}),
    refresh: vi.fn(async () => {}),
  };
}

/** The signed-in user of a test: a User of the fake with these keys. */
function signedInUser(
  permissions: SessionUser['permissions'],
  overrides: Partial<SessionUser> = {},
): SessionUser {
  return {
    id: 90,
    loginName: 'admin',
    displayName: 'Ada Admin',
    roleId: ADMINISTRATOR_ID,
    roleName: 'Administrator',
    avatarUpdatedAt: null,
    permissions,
    mustChangePassword: false,
    sessionExpiresAt: null,
    ...overrides,
  };
}

let session: SessionValue;

function renderWithSession(
  ui: React.ReactElement,
  status: 'connected' | 'unavailable' = 'connected',
) {
  return render(
    <ConnectivityContext.Provider value={{ status, retry: vi.fn() }}>
      <SessionContext.Provider value={session}>{ui}</SessionContext.Provider>
    </ConnectivityContext.Provider>,
  );
}

function renderAdmin(status: 'connected' | 'unavailable' = 'connected') {
  return renderWithSession(<AdministrationView />, status);
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

test('editing a Worker sends only the changed fields, with the canonical badge', async () => {
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
    body: { badge_barcode: 'X-100482' },
  });
  expect(await screen.findByText('X-100482')).toBeInTheDocument();

  // Deactivation alone sends the active flag alone.
  fireEvent.click(screen.getByRole('button', { name: 'Edit Alex Tran' }));
  dialog = screen.getByRole('dialog', { name: 'Edit Worker' });
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Active' }));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[1]).toEqual({
    method: 'PATCH',
    url: '/api/workers/1',
    body: { is_active: false },
  });
  const row = (
    await screen.findByRole('button', { name: 'Edit Alex Tran' })
  ).closest('tr') as HTMLElement;
  await waitFor(() =>
    expect(within(row).getByText('Inactive')).toBeInTheDocument(),
  );
});

test('a stale Worker editor never reverts another administrator’s change (S12-F7)', async () => {
  await openWorkers();

  fireEvent.click(screen.getByRole('button', { name: 'Edit Alex Tran' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit Worker' });
  // Another administrator deactivates the Worker and corrects the badge
  // while this editor is open.
  state.workers[0].is_active = false;
  state.workers[0].badge_barcode = 'X-200000';
  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: 'Alex T. Tran' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toEqual([
    {
      method: 'PATCH',
      url: '/api/workers/1',
      body: { name: 'Alex T. Tran' },
    },
  ]);
  expect(state.workers[0].is_active).toBe(false);
  expect(state.workers[0].badge_barcode).toBe('X-200000');
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

test('a chosen avatar alone is uploaded without a profile write, labelled with its type', async () => {
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
  expect(writeSummary()).toEqual(['PUT /api/workers/2/avatar']);
  expect(writes[0].body).toEqual({
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

test('removing the avatar alone sends only the DELETE', async () => {
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
  expect(writeSummary()).toEqual(['DELETE /api/workers/1/avatar']);
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
  // Nothing changed: the editor closes without a write.
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual([]);
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

  // Saving again edits the saved Worker: its profile is unchanged, so
  // only the avatar is sent again.
  delete workerFailures['PUT avatar'];
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual([
    'POST /api/workers',
    'PUT /api/workers/100/avatar',
    'PUT /api/workers/100/avatar',
  ]);
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

test('FA-S1: Scan behavior is not available yet and promises no phase', async () => {
  renderAdmin();
  await screen.findByRole('button', { name: 'Edit Lathe' });
  openSection('Scan behavior');

  const main = document.querySelector('.ad-main') as HTMLElement;
  expect(main.textContent).toContain(
    'The Scan behavior configuration is not available yet.',
  );
  expect(main.textContent).toContain('Its settings have not been defined.');
  expect(main.textContent).not.toContain('full Administration');
  expect(main.textContent).not.toContain('table + editor');
  expect(screen.getByRole('button', { name: '+ New entry' })).toBeDisabled();
  expect(document.body.textContent).not.toMatch(/Phase \d/);
});

const MACHINE_ASSIGNMENT_SUBTITLE =
  "Two Area modes that follow from the Area's Machines: no Machines → direct processing; one or more Machines → queue and one-shot assignment (one Machine behaves like several) — never a per-Area setting";

test('FA-M1: Machine assignment is a read-only statement of the two Area modes', async () => {
  renderAdmin();
  await screen.findByRole('button', { name: 'Edit Lathe' });
  const fetchCalls = vi.mocked(fetch).mock.calls.length;
  openSection('Machine assignment');

  expect(
    screen.getByRole('heading', {
      name: "Machine assignment follows from the Area's Machines",
    }),
  ).toBeInTheDocument();
  expect(screen.getByText(MACHINE_ASSIGNMENT_SUBTITLE)).toBeInTheDocument();
  const rows = within(screen.getByRole('table')).getAllByRole('row');
  expect(
    rows.map((row) => Array.from(row.children).map((cell) => cell.textContent)),
  ).toEqual([
    ['Area has', 'Mode', 'At the Scan Station'],
    [
      'No Machines',
      'Direct processing (no Machines)',
      'Quantity scanned into the Area is processed there directly; no Machine is recorded.',
    ],
    [
      'One or more Machines',
      'Queue → assign (one-shot)',
      'Quantity enters the Area queue and is assigned to a Machine through an explicit one-shot assignment — never automatically, even when the Area has a single Machine.',
    ],
  ]);
  expect(
    Array.from(rows[1].querySelectorAll('td')).map((cell) =>
      cell.getAttribute('data-label'),
    ),
  ).toEqual(['Area has', 'Mode', 'At the Scan Station']);
  const main = document.querySelector('.ad-main') as HTMLElement;
  expect(main.textContent).toContain(
    'Machine assignment is not configured per Area. Each Area works in one of two modes, decided only by whether it has Machines.',
  );
  expect(main.textContent).toContain(
    "Machines are managed in Management → Machines; an Area's mode changes only when its Machines change. The Areas section shows each Area's current mode.",
  );
  expect(
    main.querySelectorAll('button, input, select, textarea, a'),
  ).toHaveLength(0);
  expect(screen.queryByRole('button', { name: '+ New entry' })).toBeNull();
  await Promise.resolve();
  expect(vi.mocked(fetch).mock.calls.length).toBe(fetchCalls);
  expect(document.body.textContent).not.toMatch(/Phase \d/);
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

/** The correction table as text: header, then ✓ / - per checkbox. */
function correctionMatrix(): string[][] {
  return within(screen.getByRole('table'))
    .getAllByRole('row')
    .map((row) =>
      Array.from(row.children).map((cell) => {
        const box = cell.querySelector('input[type="checkbox"]');
        if (box) return (box as HTMLInputElement).checked ? '✓' : '-';
        return cell.textContent ?? '';
      }),
    );
}

async function openCorrectionPermissions() {
  renderAdmin();
  openSection('Correction permissions');
  return screen.findByRole('switch', { name: UNDO_REASON_SWITCH });
}

test('FA-C5: Correction permissions shows the real Undo reason switch first, then the role × correction-permission table', async () => {
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
    await screen.findByRole('checkbox', {
      name: 'Undo recent eligible scans — Operator',
    }),
  ).toBeChecked();
  expect(document.body.textContent).not.toContain(
    'Role-based correction permissions are not configurable yet.',
  );
  // The Undo reason switch comes first, the table after it.
  const table = screen.getByRole('table');
  expect(
    toggle.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(correctionMatrix()).toEqual([
    [
      'Role',
      'Undo recent eligible scans',
      'Perform quantity corrections',
      'Edit Work Order Allocation',
      'Perform authorized historical corrections',
    ],
    ['Administrator', '-', '-', '✓', '✓'],
    ['Manager', '-', '✓', '✓', '-'],
    ['Operator', '✓', '-', '-', '-'],
  ]);
  const operatorUndo = screen.getByRole('checkbox', {
    name: 'Undo recent eligible scans — Operator',
  });
  expect(operatorUndo.closest('td')).toHaveAttribute(
    'data-label',
    'Undo recent eligible scans',
  );
  expect(document.body.textContent).toContain(
    'Choose which roles hold each correction permission. These permissions are recorded for each role and are not enforced yet.',
  );
  expect(document.body.textContent).toContain(
    "Undo recent eligible scans covers exactly the actions the Scan Station's Undo offers — there is no extra time limit.",
  );
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

/* ============ History archival & purge (retention period) ============ */

const RETENTION_PATH = '/api/policies/data-retention';
const NO_PERIOD = 'No retention period';
const KEEP_PERIOD = 'Keep a set period of history';
const RETENTION_UNKNOWN_OUTCOME =
  'The server did not answer — this change may or may not have been saved. Check the retention period before trying again; saving the same value again is safe.';

async function openHistoryArchival(
  status: 'connected' | 'unavailable' = 'connected',
) {
  renderAdmin(status);
  openSection('History archival & purge');
  return (await screen.findByRole('radio', {
    name: NO_PERIOD,
  })) as HTMLInputElement;
}

function retentionField(): HTMLInputElement {
  return screen.getByLabelText('Retention period (months)') as HTMLInputElement;
}

test('FA-R1: History archival & purge loads no retention period and states that runs are not available', async () => {
  const none = await openHistoryArchival();

  expect(none).toBeChecked();
  expect(screen.getByRole('radio', { name: KEEP_PERIOD })).not.toBeChecked();
  expect(screen.queryByLabelText('Retention period (months)')).toBeNull();
  expect(
    screen.getByRole('heading', { name: 'Retention period' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('heading', { name: 'Archival and purge runs' }),
  ).toBeInTheDocument();
  expect(document.body.textContent).toContain(
    'How long Movement history stays in the PartFlow database before archival maintenance may move it to archive files. Saving the period archives or deletes nothing, and it never affects production scanning.',
  );
  expect(document.body.textContent).toContain(
    'Archival and purge runs — by retention period, data-size threshold or manual request — are not available yet.',
  );
  expect(document.body.textContent).toContain(
    'Nothing is archived or purged: all Movement history stays in the database.',
  );
  expect(screen.queryByRole('button', { name: '+ New entry' })).toBeNull();
  expect(screen.queryByRole('switch')).toBeNull();
  expect(screen.queryByRole('note')).toBeNull();
  expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  expect(document.body.textContent).not.toMatch(/Phase \d/);
});

test('FA-R2: a set period PUTs exactly its months, states it in years and re-reads', async () => {
  await openHistoryArchival();
  fireEvent.click(screen.getByRole('radio', { name: KEEP_PERIOD }));

  const field = retentionField();
  expect(field).toHaveValue(null);
  expect(screen.getByRole('alert')).toHaveTextContent(E_T2);
  const save = screen.getByRole('button', { name: 'Save' });
  expect(save).toBeDisabled();

  fireEvent.change(field, { target: { value: '120' } });
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByText('Retention period: 10 years.')).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/Keeps the most recent/);
  expect(save).toBeEnabled();
  fireEvent.click(save);

  expect(await screen.findByRole('status')).toHaveTextContent(
    '✓ Retention period saved.',
  );
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: RETENTION_PATH,
      body: { retention_period_months: 120 },
    },
  ]);
  expect(state.retentionMonths).toBe(120);
  await waitFor(() => expect(save).toBeDisabled());

  // Editing again clears the saved note.
  fireEvent.change(field, { target: { value: '121' } });
  expect(screen.queryByRole('status')).toBeNull();
  cleanup();

  // A fresh read shows the stored period.
  await openHistoryArchival();
  expect(screen.getByRole('radio', { name: KEEP_PERIOD })).toBeChecked();
  expect(retentionField()).toHaveValue(120);
  expect(screen.getByText('Retention period: 10 years.')).toBeInTheDocument();
});

test('FA-R3: an entry outside whole months 12 to 1200 cannot be saved', async () => {
  await openHistoryArchival();
  fireEvent.click(screen.getByRole('radio', { name: KEEP_PERIOD }));
  const field = retentionField();
  const save = screen.getByRole('button', { name: 'Save' });

  for (const value of ['11', '1201', '12.5', '']) {
    fireEvent.change(field, { target: { value } });
    expect(screen.getByRole('alert')).toHaveTextContent(E_T2);
    expect(screen.queryByText(/^Retention period: /)).toBeNull();
    expect(save).toBeDisabled();
  }
  fireEvent.change(field, { target: { value: '12' } });
  expect(screen.getByText('Retention period: 1 year.')).toBeInTheDocument();
  fireEvent.change(field, { target: { value: '18' } });
  expect(
    screen.getByText('Retention period: 1 year 6 months.'),
  ).toBeInTheDocument();
  expect(writes).toEqual([]);
});

test('FA-R4: choosing no retention period clears the stored period with null', async () => {
  state.retentionMonths = 120;
  await openHistoryArchival();
  expect(screen.getByRole('radio', { name: KEEP_PERIOD })).toBeChecked();
  expect(retentionField()).toHaveValue(120);
  const save = screen.getByRole('button', { name: 'Save' });
  expect(save).toBeDisabled();

  fireEvent.click(screen.getByRole('radio', { name: NO_PERIOD }));
  expect(screen.queryByLabelText('Retention period (months)')).toBeNull();
  expect(save).toBeEnabled();
  fireEvent.click(save);

  expect(await screen.findByRole('status')).toHaveTextContent(
    '✓ Retention period saved.',
  );
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: RETENTION_PATH,
      body: { retention_period_months: null },
    },
  ]);
  expect(state.retentionMonths).toBeNull();
  await waitFor(() => expect(save).toBeDisabled());
});

test('FA-R5: a refused save keeps the entry with the reason; an unanswered save is an unknown outcome', async () => {
  await openHistoryArchival();
  fireEvent.click(screen.getByRole('radio', { name: KEEP_PERIOD }));
  const field = retentionField();
  fireEvent.change(field, { target: { value: '120' } });
  const save = screen.getByRole('button', { name: 'Save' });

  retentionFailure = { status: 422, detail: E_T1 };
  fireEvent.click(save);
  expect(await screen.findByRole('alert')).toHaveTextContent(E_T1);
  expect(field).toHaveValue(120);
  expect(screen.queryByRole('status')).toBeNull();
  expect(state.retentionMonths).toBeNull();
  await waitFor(() => expect(save).toBeEnabled());

  // A 5xx may have committed: the section says so instead of claiming
  // nothing changed, and keeps the entry.
  retentionFailure = { status: 500, detail: 'Database unavailable.' };
  fireEvent.click(save);
  await waitFor(() =>
    expect(screen.getByRole('alert')).toHaveTextContent(
      RETENTION_UNKNOWN_OUTCOME,
    ),
  );
  expect(field).toHaveValue(120);
  expect(screen.queryByRole('status')).toBeNull();
  retentionFailure = null;
  cleanup();

  // The server commits the PUT; the answer never arrives. The stored
  // period is re-read, so nothing is left to save and the notice stays.
  await openHistoryArchival();
  fireEvent.click(screen.getByRole('radio', { name: KEEP_PERIOD }));
  fireEvent.change(retentionField(), { target: { value: '36' } });
  vi.mocked(fetch).mockImplementationOnce(async (input, init) => {
    await handle(String(input), init);
    throw new TypeError('Failed to fetch');
  });
  const retry = screen.getByRole('button', { name: 'Save' });
  fireEvent.click(retry);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    RETENTION_UNKNOWN_OUTCOME,
  );
  expect(document.body.textContent).not.toMatch(/Nothing was changed/);
  expect(state.retentionMonths).toBe(36);
  await waitFor(() => expect(retry).toBeDisabled());
  expect(screen.getByRole('alert')).toHaveTextContent(
    'may or may not have been saved',
  );
  expect(retentionField()).toHaveValue(36);
});

test('FA-R6: offline the stored period stays visible and cannot be saved', async () => {
  state.retentionMonths = 120;
  await openHistoryArchival('unavailable');
  const field = retentionField();
  expect(field).toHaveValue(120);
  fireEvent.change(field, { target: { value: '240' } });
  expect(screen.getByText('Retention period: 20 years.')).toBeInTheDocument();
  const save = screen.getByRole('button', { name: 'Save' });
  expect(save).toBeDisabled();
  fireEvent.click(save);
  expect(writes).toEqual([]);
});

test('FA-R7: a failed load of the retention period offers Retry', async () => {
  retentionFailure = { status: 500, detail: 'Database unavailable.' };
  renderAdmin();
  openSection('History archival & purge');
  expect(
    await screen.findByText('History retention settings could not be loaded.'),
  ).toBeInTheDocument();
  retentionFailure = null;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByRole('radio', { name: NO_PERIOD })).toBeChecked();
});

/* ============ Users (Phase 13 — application accounts) ============ */

const USERS_NOTE =
  'Users sign in with their login name and a password. Use Set password… to give a user a password. PartFlow checks permissions only for setting passwords and changing user sign-in settings so far; every other screen stays open to anyone who can reach PartFlow. Workers who scan at the Scan Stations are managed in Workers, not here. Users are deactivated, never deleted; deactivating a user signs them out.';
const USER_UNKNOWN_OUTCOME =
  'The server did not answer — this change may or may not have been saved. Close this window to refresh the list, then check the user before trying again.';
const E_U2B =
  'A login name may contain only letters (a–z), digits and . _ @ + -, with no spaces, and at most 128 characters.';

function seedJane(overrides: Partial<UserRow> = {}): UserRow {
  const user: UserRow = {
    id: 50,
    login_name: 'jdoe',
    display_name: 'Jane Doe',
    role_id: MANAGER_ID,
    is_active: true,
    avatar_updated_at: null,
    ...overrides,
  };
  state.users.push(user);
  return user;
}

async function openUsers(status: 'connected' | 'unavailable' = 'connected') {
  renderAdmin(status);
  await screen.findByRole('button', { name: 'Edit Lathe' });
  openSection('Users');
  await screen.findByRole('heading', { name: 'Users' });
  await waitFor(() =>
    expect(screen.queryByRole('status', { name: 'Loading users' })).toBeNull(),
  );
}

function fillUser(dialog: HTMLElement, name: string, login: string) {
  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: name },
  });
  fireEvent.change(within(dialog).getByLabelText('Login name'), {
    target: { value: login },
  });
}

test('FA-U1: Users is real — empty state, the honest note after it, an enabled entry action', async () => {
  await openUsers();

  const main = document.querySelector('.ad-main') as HTMLElement;
  expect(screen.getByText('No users configured yet.')).toBeInTheDocument();
  const note = main.querySelector('.ad-notice') as HTMLElement;
  expect(note.textContent?.replace(/\s+/g, ' ').trim()).toBe(USERS_NOTE);
  expect(
    screen.getByText('No users configured yet.').compareDocumentPosition(note) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(screen.getByRole('button', { name: '+ New user' })).toBeEnabled();
  expect(main.textContent).toContain(
    'Application accounts — name, login name, role, avatar, active status; separate from Workers',
  );
  expect(document.body.textContent).not.toMatch(/Phase \d/);
  expect(main.textContent).not.toContain('is not available yet');
  expect(screen.queryByRole('button', { name: '+ New entry' })).toBeNull();
});

test('FA-U1: the Users table lists name, login name, role and status, with the note after it', async () => {
  seedJane({ avatar_updated_at: ALEX_AVATAR_AT });
  seedJane({
    id: 51,
    login_name: 'tlam',
    display_name: 'Tuan Lam',
    role_id: OPERATOR_ID,
    is_active: false,
  });
  await openUsers();

  const janeRow = screen
    .getByRole('button', { name: 'Edit Jane Doe' })
    .closest('tr') as HTMLElement;
  expect(within(janeRow).getByText('jdoe')).toHaveClass('mono');
  expect(within(janeRow).getByText('jdoe')).toHaveAttribute(
    'data-label',
    'Login name',
  );
  expect(within(janeRow).getByText('Manager')).toHaveAttribute(
    'data-label',
    'Role',
  );
  expect(within(janeRow).getByText('Active')).toBeInTheDocument();
  expect(janeRow.querySelector('img')?.getAttribute('src')).toBe(
    `/api/users/50/avatar?v=${encodeURIComponent(ALEX_AVATAR_AT)}`,
  );
  const tuanRow = screen
    .getByRole('button', { name: 'Edit Tuan Lam' })
    .closest('tr') as HTMLElement;
  expect(within(tuanRow).getByText('Operator')).toBeInTheDocument();
  expect(within(tuanRow).getByText('Inactive')).toBeInTheDocument();
  expect(tuanRow.querySelector('.worker-avatar')?.textContent).toBe('TL');

  const note = document.querySelector('.ad-main .ad-notice') as HTMLElement;
  expect(
    screen.getByRole('table').compareDocumentPosition(note) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

test('FA-U2: a new user posts the trimmed name, the canonical login name and the chosen role', async () => {
  await openUsers();

  fireEvent.click(screen.getByRole('button', { name: '+ New user' }));
  const dialog = screen.getByRole('dialog', { name: 'New user' });
  expect(within(dialog).getByLabelText('Name')).toHaveFocus();
  fillUser(dialog, ' Jane Doe ', 'jdoe');
  expect(dialog.textContent).not.toContain('Saved as:');
  fireEvent.change(within(dialog).getByLabelText('Login name'), {
    target: { value: 'JDoe' },
  });
  expect(dialog.textContent).toContain('Saved as: jdoe');
  expect(dialog.textContent).toContain(
    'Saved in small letters — letter case does not matter.',
  );
  // No role preselected: the choice is required.
  const role = within(dialog).getByLabelText('Role') as HTMLSelectElement;
  expect(role.value).toBe('');
  expect(
    within(role).getByRole('option', { name: 'Choose a role…' }),
  ).toBeDisabled();
  expect(
    within(role)
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toEqual(['Choose a role…', 'Administrator', 'Manager', 'Operator']);
  expect(within(dialog).queryByRole('checkbox', { name: 'Active' })).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add user' }));
  expect(within(dialog).getByRole('alert')).toHaveTextContent('Choose a role.');
  expect(writes).toEqual([]);

  fireEvent.change(role, { target: { value: String(MANAGER_ID) } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add user' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/users',
      body: {
        login_name: 'jdoe',
        display_name: 'Jane Doe',
        role_id: MANAGER_ID,
      },
    },
  ]);
  const row = (
    await screen.findByRole('button', { name: 'Edit Jane Doe' })
  ).closest('tr') as HTMLElement;
  expect(row.querySelector('.worker-avatar')?.textContent).toBe('JD');
  expect(within(row).getByText('jdoe')).toBeInTheDocument();
  expect(within(row).getByText('Manager')).toBeInTheDocument();
  expect(within(row).getByText('Active')).toBeInTheDocument();
});

test('FA-U3: invalid login names are refused in place; a server duplicate keeps the entry', async () => {
  seedJane();
  await openUsers();

  fireEvent.click(screen.getByRole('button', { name: '+ New user' }));
  const dialog = screen.getByRole('dialog', { name: 'New user' });
  fireEvent.change(within(dialog).getByLabelText('Role'), {
    target: { value: String(OPERATOR_ID) },
  });
  for (const login of ['j doe', 'jdoé', 'a'.repeat(129)]) {
    fillUser(dialog, 'John Doe', login);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add user' }));
    expect(within(dialog).getByRole('alert')).toHaveTextContent(E_U2B);
  }
  fillUser(dialog, 'John Doe', '');
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'A login name is required.',
  );
  expect(writes).toEqual([]);

  fillUser(dialog, 'John Doe', 'JDOE');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add user' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    'This login name is already used by Jane Doe.',
  );
  expect(screen.getByRole('dialog', { name: 'New user' })).toBe(dialog);
  expect(within(dialog).getByLabelText('Login name')).toHaveValue('JDOE');
  expect(state.users).toHaveLength(1);
});

test('FA-U4: an edit sends only the changed fields; nothing changed sends nothing', async () => {
  seedJane();
  await openUsers();

  // Role + deactivate: exactly those two fields.
  fireEvent.click(screen.getByRole('button', { name: 'Edit Jane Doe' }));
  let dialog = screen.getByRole('dialog', { name: 'Edit user' });
  expect(within(dialog).getByLabelText('Name')).toHaveFocus();
  expect(within(dialog).getByLabelText('Login name')).toHaveValue('jdoe');
  fireEvent.change(within(dialog).getByLabelText('Role'), {
    target: { value: String(OPERATOR_ID) },
  });
  fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Active' }));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toEqual([
    {
      method: 'PATCH',
      url: '/api/users/50',
      body: { role_id: OPERATOR_ID, is_active: false },
    },
  ]);
  await waitFor(() =>
    expect(
      within(
        screen
          .getByRole('button', { name: 'Edit Jane Doe' })
          .closest('tr') as HTMLElement,
      ).getByText('Inactive'),
    ).toBeInTheDocument(),
  );

  // Rename only: the name alone.
  fireEvent.click(screen.getByRole('button', { name: 'Edit Jane Doe' }));
  dialog = screen.getByRole('dialog', { name: 'Edit user' });
  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: ' Jane D. Doe ' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[1]).toEqual({
    method: 'PATCH',
    url: '/api/users/50',
    body: { display_name: 'Jane D. Doe' },
  });
  expect(state.users[0].is_active).toBe(false);

  // A case or whitespace variant of the stored login is no change.
  await screen.findByRole('button', { name: 'Edit Jane D. Doe' });
  fireEvent.click(screen.getByRole('button', { name: 'Edit Jane D. Doe' }));
  dialog = screen.getByRole('dialog', { name: 'Edit user' });
  fireEvent.change(within(dialog).getByLabelText('Login name'), {
    target: { value: ' JDOE ' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toHaveLength(2);
});

test('FA-U4: a staged avatar alone is uploaded without a PATCH; after a changed profile it is the second write', async () => {
  seedJane();
  await openUsers();

  fireEvent.click(screen.getByRole('button', { name: 'Edit Jane Doe' }));
  let dialog = screen.getByRole('dialog', { name: 'Edit user' });
  const file = pngFile();
  chooseAvatar(dialog, file);
  await waitFor(() =>
    expect(dialog.querySelector('img')?.getAttribute('src')).toBe(
      'blob:staged-avatar',
    ),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary()).toEqual(['PUT /api/users/50/avatar']);
  expect(writes[0].body).toEqual({ contentType: 'image/png', size: file.size });
  const row = (
    await screen.findByRole('button', { name: 'Edit Jane Doe' })
  ).closest('tr') as HTMLElement;
  await waitFor(() =>
    expect(row.querySelector('img')?.getAttribute('src')).toBe(
      `/api/users/50/avatar?v=${encodeURIComponent('2026-10-05T10:00:00.000001+00:00')}`,
    ),
  );

  // Profile change + staged avatar: PATCH, then PUT.
  fireEvent.click(screen.getByRole('button', { name: 'Edit Jane Doe' }));
  dialog = screen.getByRole('dialog', { name: 'Edit user' });
  fireEvent.change(within(dialog).getByLabelText('Role'), {
    target: { value: String(ADMINISTRATOR_ID) },
  });
  chooseAvatar(dialog);
  await waitFor(() =>
    expect(dialog.querySelector('img')?.getAttribute('src')).toBe(
      'blob:staged-avatar',
    ),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary().slice(1)).toEqual([
    'PATCH /api/users/50',
    'PUT /api/users/50/avatar',
  ]);
  expect(writes[1].body).toEqual({ role_id: ADMINISTRATOR_ID });

  // Removing the stored avatar sends DELETE alone.
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Jane Doe' }));
  dialog = screen.getByRole('dialog', { name: 'Edit user' });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Remove avatar' }),
  );
  expect(dialog.querySelector('img')).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writeSummary().slice(3)).toEqual(['DELETE /api/users/50/avatar']);
});

test('FA-U4: an avatar refused after a changed profile names the saved profile', async () => {
  seedJane();
  await openUsers();
  userFailures['PUT avatar'] = {
    status: 413,
    detail: 'The image is larger than 2 MiB.',
  };

  fireEvent.click(screen.getByRole('button', { name: 'Edit Jane Doe' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit user' });
  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: 'Jane Q. Doe' },
  });
  chooseAvatar(dialog);
  await waitFor(() => expect(dialog.querySelector('img')).not.toBeNull());
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    'The user was saved, but the avatar could not be updated: The image is larger than 2 MiB.',
  );
  expect(writeSummary()).toEqual([
    'PATCH /api/users/50',
    'PUT /api/users/50/avatar',
  ]);

  // Retrying sends only the avatar (the profile is saved).
  userFailures['PUT avatar'] = {
    status: 413,
    detail: 'The image is larger than 2 MiB.',
  };
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(writes[2].url).toBe('/api/users/50/avatar');
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    'The avatar could not be updated: The image is larger than 2 MiB.',
  );
});

test('FA-U5: an unanswered save is an unknown outcome; offline blocks writes; a failed load offers Retry', async () => {
  seedJane();
  await openUsers();
  userFailures.PATCH = 'network';

  fireEvent.click(screen.getByRole('button', { name: 'Edit Jane Doe' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit user' });
  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: 'Jane Q. Doe' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    USER_UNKNOWN_OUTCOME,
  );
  expect(screen.getByRole('dialog', { name: 'Edit user' })).toBe(dialog);
  cleanup();

  userFailures = {};
  await openUsers('unavailable');
  expect(screen.getByRole('button', { name: '+ New user' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Edit Jane Doe' }));
  const offline = screen.getByRole('dialog', { name: 'Edit user' });
  for (const name of ['Save changes', 'Choose image…']) {
    expect(within(offline).getByRole('button', { name })).toBeDisabled();
  }
  cleanup();

  userFailures['GET list'] = { status: 500, detail: 'Database unavailable.' };
  renderAdmin();
  openSection('Users');
  expect(
    await screen.findByText('User data could not be loaded.'),
  ).toBeInTheDocument();
  expect(screen.getByText('Database unavailable.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '+ New user' })).toBeDisabled();
  userFailures = {};
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(
    await screen.findByRole('button', { name: 'Edit Jane Doe' }),
  ).toBeInTheDocument();
});

/* ============ Roles & permissions (Phase 13 — named roles) ============ */

const ROLES_NOTE =
  'Each user holds one role. PartFlow checks permissions only for setting passwords and changing user sign-in settings so far; the other permissions are recorded here and are not checked yet. Correction permissions are set in Policies → Correction permissions. Roles are renamed, never deleted.';

async function openRoles(status: 'connected' | 'unavailable' = 'connected') {
  renderAdmin(status);
  await screen.findByRole('button', { name: 'Edit Lathe' });
  openSection('Roles & permissions');
  await screen.findByRole('button', { name: 'Edit Manager' });
}

function roleRow(name: string): string[] {
  const row = screen
    .getByRole('button', { name: `Edit ${name}` })
    .closest('tr') as HTMLElement;
  return Array.from(row.querySelectorAll('td'), (td) => td.textContent ?? '');
}

test('FA-RO1: Roles & permissions lists the seeded roles with permission and user counts', async () => {
  seedJane();
  await openRoles();

  expect(roleRow('Administrator')).toEqual(['Administrator', '17 of 35', '0']);
  expect(roleRow('Manager')).toEqual(['Manager', '10 of 35', '1']);
  expect(roleRow('Operator')).toEqual(['Operator', '10 of 35', '0']);
  const managerRow = screen
    .getByRole('button', { name: 'Edit Manager' })
    .closest('tr') as HTMLElement;
  expect(
    Array.from(managerRow.querySelectorAll('td[data-label]'), (td) =>
      td.getAttribute('data-label'),
    ),
  ).toEqual(['Permissions', 'Users']);
  const note = document.querySelector('.ad-main .ad-notice') as HTMLElement;
  expect(note.textContent?.replace(/\s+/g, ' ').trim()).toBe(ROLES_NOTE);
  expect(
    screen.getByRole('table').compareDocumentPosition(note) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  const main = document.querySelector('.ad-main') as HTMLElement;
  expect(main.textContent).toContain(
    'Named roles and the permissions each one grants',
  );
  expect(main.textContent).not.toContain('Phase');
  expect(screen.getByRole('button', { name: '+ New role' })).toBeEnabled();
});

test('FA-RO2: editing a role sends one delta over the four editable groups only', async () => {
  await openRoles();

  fireEvent.click(screen.getByRole('button', { name: 'Edit Manager' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit role' });
  expect(within(dialog).getByLabelText('Name')).toHaveFocus();
  expect(
    within(dialog)
      .getAllByRole('group')
      .map((group) => group.querySelector('legend')?.textContent),
  ).toEqual([
    'Administration',
    'Production master data',
    'Work Orders, priority and reports',
    'Scan Station',
  ]);
  expect(within(dialog).getAllByRole('checkbox')).toHaveLength(31);
  for (const label of [
    'Undo recent eligible scans',
    'Perform quantity corrections',
    'Edit Work Order Allocation',
    'Perform authorized historical corrections',
  ]) {
    expect(within(dialog).queryByRole('checkbox', { name: label })).toBeNull();
  }
  expect(dialog.textContent).toContain(
    'Correction permissions: Perform quantity corrections, Edit Work Order Allocation — set in Policies → Correction permissions.',
  );
  expect(
    within(dialog).getByRole('checkbox', { name: 'Export and print reports' }),
  ).toBeChecked();
  expect(
    within(dialog).getByRole('checkbox', { name: 'Manage Machines' }),
  ).not.toBeChecked();

  // Unchanged Save: closes without a request.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toEqual([]);

  fireEvent.click(screen.getByRole('button', { name: 'Edit Manager' }));
  const edit = screen.getByRole('dialog', { name: 'Edit role' });
  fireEvent.click(
    within(edit).getByRole('checkbox', { name: 'Export and print reports' }),
  );
  fireEvent.click(
    within(edit).getByRole('checkbox', { name: 'Manage Machines' }),
  );
  fireEvent.change(within(edit).getByLabelText('Name'), {
    target: { value: ' Production Manager ' },
  });
  fireEvent.click(within(edit).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toEqual([
    {
      method: 'PATCH',
      url: `/api/roles/${MANAGER_ID}`,
      body: {
        name: 'Production Manager',
        grant_permissions: ['MANAGE_MACHINES'],
        revoke_permissions: ['EXPORT_REPORTS'],
      },
    },
  ]);
  expect(
    await screen.findByRole('button', { name: 'Edit Production Manager' }),
  ).toBeInTheDocument();
  // The correction permissions the role holds are untouched.
  expect(state.roles[1].permissions).toContain('PERFORM_QUANTITY_CORRECTIONS');
  expect(state.roles[1].permissions).toContain('EDIT_WORK_ORDER_ALLOCATION');
});

test('FA-RO3: a new role posts its name and checked permissions; a duplicate keeps the entry; offline blocks writes', async () => {
  await openRoles();

  fireEvent.click(screen.getByRole('button', { name: '+ New role' }));
  let dialog = screen.getByRole('dialog', { name: 'New role' });
  expect(within(dialog).getByLabelText('Name')).toHaveFocus();
  expect(dialog.textContent).not.toContain('Correction permissions:');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add role' }));
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'A role name is required.',
  );
  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: ' Process Engineer ' },
  });
  fireEvent.click(
    within(dialog).getByRole('checkbox', { name: 'Manage Planned Routes' }),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add role' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toEqual([
    {
      method: 'POST',
      url: '/api/roles',
      body: {
        name: 'Process Engineer',
        permissions: ['MANAGE_ROUTE_TEMPLATES'],
      },
    },
  ]);
  expect(await roleRowWhenShown('Process Engineer')).toEqual([
    'Process Engineer',
    '1 of 35',
    '0',
  ]);

  fireEvent.click(screen.getByRole('button', { name: '+ New role' }));
  dialog = screen.getByRole('dialog', { name: 'New role' });
  fireEvent.change(within(dialog).getByLabelText('Name'), {
    target: { value: 'Manager' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add role' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    'A role with this name already exists.',
  );
  expect(within(dialog).getByLabelText('Name')).toHaveValue('Manager');
  cleanup();

  await openRoles('unavailable');
  expect(screen.getByRole('button', { name: '+ New role' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Edit Operator' }));
  expect(
    within(screen.getByRole('dialog', { name: 'Edit role' })).getByRole(
      'button',
      { name: 'Save changes' },
    ),
  ).toBeDisabled();
});

async function roleRowWhenShown(name: string): Promise<string[]> {
  await screen.findByRole('button', { name: `Edit ${name}` });
  return roleRow(name);
}

test('FA-RO: an unanswered role save is an unknown outcome; a failed load offers Retry', async () => {
  await openRoles();
  roleFailures.PATCH = 'network';
  fireEvent.click(screen.getByRole('button', { name: 'Edit Operator' }));
  const dialog = screen.getByRole('dialog', { name: 'Edit role' });
  fireEvent.click(
    within(dialog).getByRole('checkbox', { name: 'Confirm quantity' }),
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    'The server did not answer — this change may or may not have been saved. Close this window to refresh the list, then check the role before trying again.',
  );
  cleanup();

  roleFailures = {
    'GET list': { status: 500, detail: 'Database unavailable.' },
  };
  renderAdmin();
  openSection('Roles & permissions');
  expect(
    await screen.findByText('Role data could not be loaded.'),
  ).toBeInTheDocument();
  roleFailures = {};
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(
    await screen.findByRole('button', { name: 'Edit Operator' }),
  ).toBeInTheDocument();
});

/* ============ Correction permissions — role table ============ */

test('FA-C6: a click grants or revokes exactly that permission of that role, disabled in flight, then re-reads', async () => {
  await openCorrectionPermissions();
  const managerUndo = await screen.findByRole('checkbox', {
    name: 'Undo recent eligible scans — Manager',
  });
  expect(managerUndo).not.toBeChecked();

  let release: () => void = () => undefined;
  roleHold = new Promise<void>((resolve) => {
    release = resolve;
  });
  fireEvent.click(managerUndo);
  await waitFor(() => expect(managerUndo).toBeDisabled());
  for (const box of within(screen.getByRole('table')).getAllByRole(
    'checkbox',
  )) {
    expect(box).toBeDisabled();
  }
  release();
  roleHold = null;
  await waitFor(() =>
    expect(
      screen.getByRole('checkbox', {
        name: 'Undo recent eligible scans — Manager',
      }),
    ).toBeChecked(),
  );
  expect(writes).toEqual([
    {
      method: 'PATCH',
      url: `/api/roles/${MANAGER_ID}`,
      body: { grant_permissions: ['UNDO_RECENT_SCANS'] },
    },
  ]);
  await waitFor(() =>
    expect(
      screen.getByRole('checkbox', {
        name: 'Undo recent eligible scans — Manager',
      }),
    ).toBeEnabled(),
  );

  fireEvent.click(
    screen.getByRole('checkbox', {
      name: 'Undo recent eligible scans — Manager',
    }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole('checkbox', {
        name: 'Undo recent eligible scans — Manager',
      }),
    ).not.toBeChecked(),
  );
  expect(writes[1]).toEqual({
    method: 'PATCH',
    url: `/api/roles/${MANAGER_ID}`,
    body: { revoke_permissions: ['UNDO_RECENT_SCANS'] },
  });
  // The Undo reason policy is never written by the table.
  expect(writes.every((item) => item.url.startsWith('/api/roles/'))).toBe(true);
});

test('FA-C6: a refused grant shows the reason and the stored value; offline disables the table', async () => {
  await openCorrectionPermissions();
  roleFailures.PATCH = { status: 404, detail: 'Role 2 does not exist.' };
  const box = await screen.findByRole('checkbox', {
    name: 'Perform authorized historical corrections — Manager',
  });
  fireEvent.click(box);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Role 2 does not exist.',
  );
  await waitFor(() =>
    expect(
      screen.getByRole('checkbox', {
        name: 'Perform authorized historical corrections — Manager',
      }),
    ).toBeEnabled(),
  );
  expect(
    screen.getByRole('checkbox', {
      name: 'Perform authorized historical corrections — Manager',
    }),
  ).not.toBeChecked();
  expect(state.roles[1].permissions).not.toContain(
    'PERFORM_HISTORICAL_CORRECTIONS',
  );
  cleanup();

  roleFailures = {};
  writes = [];
  renderAdmin('unavailable');
  openSection('Correction permissions');
  await screen.findByRole('checkbox', {
    name: 'Undo recent eligible scans — Operator',
  });
  const boxes = within(screen.getByRole('table')).getAllByRole('checkbox');
  expect(boxes).toHaveLength(12);
  for (const item of boxes) expect(item).toBeDisabled();
  fireEvent.click(boxes[0]);
  expect(writes).toEqual([]);
});

test('FA-C6: an unanswered grant is an unknown outcome; a failed re-read keeps the table and the note', async () => {
  const unknown =
    'The server did not answer — this change may or may not have been saved. The table shows the stored permissions once they can be read again; check them before trying again.';
  await openCorrectionPermissions();
  const name = 'Undo recent eligible scans — Manager';
  expect(await screen.findByRole('checkbox', { name })).not.toBeChecked();

  // The server stores the grant, then the answer is lost.
  state.roles
    .find((role) => role.id === MANAGER_ID)!
    .permissions.push('UNDO_RECENT_SCANS');
  roleFailures.PATCH = 'network';
  fireEvent.click(screen.getByRole('checkbox', { name }));
  expect((await screen.findByRole('alert')).textContent).toBe(unknown);
  await waitFor(() =>
    expect(screen.getByRole('checkbox', { name })).toBeChecked(),
  );
  expect(document.body.textContent).not.toContain('Nothing was changed');

  // The re-read fails too: the last table and the note stay on screen.
  roleFailures = {
    PATCH: 'network',
    'GET list': { status: 500, detail: 'Database unavailable.' },
  };
  const other = 'Perform quantity corrections — Operator';
  fireEvent.click(screen.getByRole('checkbox', { name: other }));
  await waitFor(() =>
    expect(screen.getByRole('checkbox', { name: other })).toBeEnabled(),
  );
  expect(screen.getByRole('alert').textContent).toBe(unknown);
  expect(screen.getByRole('checkbox', { name })).toBeChecked();
  expect(
    screen.queryByText('Role permissions could not be loaded.'),
  ).toBeNull();
});

test('an unanswered Undo reason switch is an unknown outcome and keeps the switch', async () => {
  const toggle = await openCorrectionPermissions();
  correctionFailure = { status: 503, detail: 'Service unavailable.' };
  fireEvent.click(toggle);
  expect((await screen.findByRole('alert')).textContent).toBe(
    'The server did not answer — this change may or may not have been saved. The switch shows the stored setting once it can be read again; check it before trying again.',
  );
  await waitFor(() => expect(toggle).toBeEnabled());
  expect(
    screen.getByRole('switch', { name: UNDO_REASON_SWITCH }),
  ).toHaveAttribute('aria-checked', 'false');
  expect(
    screen.queryByText('Correction permission settings could not be loaded.'),
  ).toBeNull();
});

test('FA-C6: a failed roles load offers Retry while the Undo reason switch keeps working', async () => {
  roleFailures['GET list'] = { status: 500, detail: 'Database unavailable.' };
  const toggle = await openCorrectionPermissions();
  expect(
    await screen.findByText('Role permissions could not be loaded.'),
  ).toBeInTheDocument();

  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'));
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/correction-permissions',
      body: { undo_reason_required: true },
    },
  ]);

  roleFailures = {};
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(
    await screen.findByRole('checkbox', {
      name: 'Undo recent eligible scans — Operator',
    }),
  ).toBeChecked();
});

/* ============ Users — sign-in states and Set password… (Phase 14) ============ */

const SET_PASSWORD_TEXT =
  'This replaces the password and signs Jane Doe out everywhere. Give the new password to them in person. If Settings → User sign-in requires it, they choose their own password at their next sign-in. A lock on the account is cleared.';
const SET_PASSWORD_UNKNOWN =
  'The server did not answer — the password may or may not have been set. Set it again to be sure.';

/** Signed in as user 90 holding `MANAGE_USERS_AND_ROLES`, listed too. */
function signInUserAdministrator() {
  session = sessionValue(signedInUser(['MANAGE_USERS_AND_ROLES']));
  seedJane({
    id: 90,
    login_name: 'admin',
    display_name: 'Ada Admin',
    role_id: ADMINISTRATOR_ID,
    sign_in_state: 'PASSWORD_SET',
  });
}

function signInCell(name: string): string | null | undefined {
  const row = screen
    .getByRole('button', { name: `Edit ${name}` })
    .closest('tr') as HTMLElement;
  return row.querySelector('td[data-label="Sign-in"]')?.textContent;
}

test('FA-U6: user administrators see every sign-in state and Set password… on every other user', async () => {
  signInUserAdministrator();
  seedJane({ sign_in_state: 'PASSWORD_SET' });
  seedJane({
    id: 51,
    login_name: 'tlam',
    display_name: 'Tuan Lam',
    sign_in_state: 'TEMPORARY_PASSWORD',
  });
  seedJane({
    id: 52,
    login_name: 'mnguyen',
    display_name: 'Mai Nguyen',
    sign_in_state: 'LOCKED',
  });
  seedJane({ id: 53, login_name: 'bkim', display_name: 'Bo Kim' });
  await openUsers();

  expect(
    screen.getAllByRole('columnheader').map((th) => th.textContent),
  ).toEqual(['User', 'Login name', 'Role', 'Sign-in', 'Status', '']);
  expect(signInCell('Jane Doe')).toBe('Password set');
  expect(signInCell('Tuan Lam')).toBe('Temporary password');
  expect(signInCell('Mai Nguyen')).toBe('Locked');
  expect(signInCell('Bo Kim')).toBe('No password');
  for (const name of ['Jane Doe', 'Tuan Lam', 'Mai Nguyen', 'Bo Kim']) {
    expect(
      screen.getByRole('button', { name: `Set password for ${name}` }),
    ).toBeInTheDocument();
  }
  // Never on the signed-in user's own row (Change password is theirs).
  expect(signInCell('Ada Admin')).toBe('Password set');
  expect(
    screen.queryByRole('button', { name: 'Set password for Ada Admin' }),
  ).toBeNull();
});

test('FA-U6: without the permission, or signed out, neither the Sign-in column nor Set password… exists', async () => {
  seedJane({ sign_in_state: 'LOCKED' });
  // Signed out: the server omits the state; the table is the S12 table.
  await openUsers();
  expect(
    screen.getAllByRole('columnheader').map((th) => th.textContent),
  ).toEqual(['User', 'Login name', 'Role', 'Status']);
  expect(screen.queryByRole('button', { name: /^Set password/ })).toBeNull();
  const note = document.querySelector('.ad-main .ad-notice') as HTMLElement;
  expect(note.textContent?.replace(/\s+/g, ' ').trim()).toBe(USERS_NOTE);
  expect(document.body.textContent).not.toContain('Users cannot sign in yet');
  cleanup();

  // Signed in without the permission: the same table.
  session = sessionValue(signedInUser(['CONFIGURE_SYSTEM_SETTINGS']));
  await openUsers();
  expect(
    screen.getAllByRole('columnheader').map((th) => th.textContent),
  ).toEqual(['User', 'Login name', 'Role', 'Status']);
  expect(screen.queryByText('Locked')).toBeNull();
  expect(screen.queryByRole('button', { name: /^Set password/ })).toBeNull();
});

test('FA-U7: Set password… sends exactly the new password; a mismatch or a short password sends nothing', async () => {
  signInUserAdministrator();
  seedJane({ sign_in_state: 'LOCKED' });
  await openUsers();

  fireEvent.click(
    screen.getByRole('button', { name: 'Set password for Jane Doe' }),
  );
  const dialog = screen.getByRole('dialog', {
    name: 'Set password for Jane Doe',
  });
  // The row underneath never opened its editor.
  expect(screen.queryByRole('dialog', { name: 'Edit user' })).toBeNull();
  expect(
    within(dialog).getByText(
      (_, element) =>
        element?.tagName === 'P' &&
        element.textContent?.replace(/\s+/g, ' ').trim() === SET_PASSWORD_TEXT,
    ),
  ).toBeInTheDocument();
  const next = within(dialog).getByLabelText('New password');
  const repeat = within(dialog).getByLabelText('Repeat password');
  expect(next).toHaveFocus();
  const submit = within(dialog).getByRole('button', { name: 'Set password' });

  fireEvent.change(next, { target: { value: 'short-pass' } });
  fireEvent.change(repeat, { target: { value: 'short-pass' } });
  fireEvent.click(submit);
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'At least 12 characters.',
  );
  fireEvent.change(next, { target: { value: 'correct horse battery' } });
  fireEvent.change(repeat, { target: { value: 'correct horse battery!' } });
  fireEvent.click(submit);
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'The new passwords do not match.',
  );
  expect(writes).toEqual([]);

  fireEvent.change(repeat, { target: { value: 'correct horse battery' } });
  fireEvent.click(submit);
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Password set for Jane Doe.',
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/users/50/password',
      body: { new_password: 'correct horse battery' },
    },
  ]);
  // The list reloads: the lock is cleared, the password is temporary.
  await waitFor(() =>
    expect(signInCell('Jane Doe')).toBe('Temporary password'),
  );
});

test('FA-U7: Set password… closes without a request, ignores closing in flight, blocks offline and states an unknown outcome', async () => {
  signInUserAdministrator();
  seedJane();
  await openUsers();
  const open = () => {
    fireEvent.click(
      screen.getByRole('button', { name: 'Set password for Jane Doe' }),
    );
    return screen.getByRole('dialog', { name: 'Set password for Jane Doe' });
  };
  const fill = (dialog: HTMLElement) => {
    for (const label of ['New password', 'Repeat password']) {
      fireEvent.change(within(dialog).getByLabelText(label), {
        target: { value: 'correct horse battery' },
      });
    }
  };

  // Cancel, Escape and the backdrop close it; nothing is sent.
  fireEvent.click(within(open()).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.keyDown(open(), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.mouseDown(open().parentElement as HTMLElement);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(writes).toEqual([]);

  // In flight, closing is ignored.
  let release = () => {};
  userHold = new Promise<void>((resolve) => {
    release = resolve;
  });
  const held = open();
  fill(held);
  fireEvent.click(within(held).getByRole('button', { name: 'Set password' }));
  await waitFor(() => expect(writes).toHaveLength(1));
  const cancel = within(held).getByRole('button', { name: 'Cancel (Esc)' });
  expect(cancel).toBeDisabled();
  fireEvent.click(cancel);
  fireEvent.keyDown(held, { key: 'Escape' });
  fireEvent.mouseDown(held.parentElement as HTMLElement);
  expect(screen.getByRole('dialog')).toBe(held);
  release();
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  userHold = null;

  // No answer: the outcome is unknown and setting it again is offered.
  userFailures['PUT password'] = 'network';
  const unanswered = open();
  fill(unanswered);
  fireEvent.click(
    within(unanswered).getByRole('button', { name: 'Set password' }),
  );
  expect((await within(unanswered).findByRole('alert')).textContent).toBe(
    SET_PASSWORD_UNKNOWN,
  );
  expect(
    within(unanswered).getByRole('button', { name: 'Set password' }),
  ).toBeEnabled();
  // A busy password check is a definite refusal with its own text.
  const busy =
    'PartFlow is busy checking other passwords. Try again in a moment.';
  vi.mocked(fetch).mockImplementationOnce(async () =>
    json({ detail: busy, password_check_busy: true }, 503),
  );
  fireEvent.click(
    within(unanswered).getByRole('button', { name: 'Set password' }),
  );
  await waitFor(() =>
    expect(within(unanswered).getByRole('alert').textContent).toBe(busy),
  );
  expect(
    within(unanswered).getByRole('button', { name: 'Set password' }),
  ).toBeEnabled();
  cleanup();

  userFailures = {};
  await openUsers('unavailable');
  const offline = open();
  fill(offline);
  expect(
    within(offline).getByRole('button', { name: 'Set password' }),
  ).toBeDisabled();
});

/* ============ Settings → User sign-in (Phase 14) ============ */

function signInPolicyRows(): Record<string, string> {
  return Object.fromEntries(
    Array.from(document.querySelectorAll('.ad-configpreview .prow'), (row) => [
      row.querySelector('.k')?.textContent ?? '',
      row.querySelector('.v')?.textContent ?? '',
    ]),
  );
}

function signInPolicyReads(): number {
  return vi
    .mocked(fetch)
    .mock.calls.filter(([input]) => String(input) === '/api/policies/sign-in')
    .length;
}

async function openSignInSettings(
  status: 'connected' | 'unavailable' = 'connected',
) {
  await openSettings(status);
  await screen.findByRole('heading', { name: 'User sign-in' });
}

test('FA-S1: signed out, the User sign-in panel asks to sign in and reads nothing', async () => {
  await openSignInSettings();

  expect(
    screen.getByText(
      "How long a user's sign-in lasts, when repeated failed sign-ins lock a user's account, and whether users must replace a password an administrator set.",
    ),
  ).toBeInTheDocument();
  expect(
    screen.getByText('Sign in to see the user sign-in settings.'),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(session.openSignIn).toHaveBeenCalledTimes(1);
  expect(signInPolicyReads()).toBe(0);
  // The Due Soon panel comes first, Other settings stays last.
  expect(
    screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent),
  ).toEqual(['Due Soon warning', 'User sign-in', 'Other settings']);
});

test('FA-S2: signed in without the permission, the settings read without an edit control', async () => {
  session = sessionValue(signedInUser(['MANAGE_USERS_AND_ROLES']));
  await openSignInSettings();

  await waitFor(() =>
    expect(signInPolicyRows()).toEqual({
      'User sign-ins expire': 'After 30 days',
      'Failed sign-ins before a lock': '10',
      'Lock duration': '15 minutes',
      'New password at first sign-in': 'Required',
    }),
  );
  expect(
    screen.getByText(
      'Only users whose role may configure system settings can change these.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Edit user sign-in settings…' }),
  ).toBeNull();
  // User sign-in never says "sessions" (those are Worker Sessions).
  expect(document.querySelector('.ad-config')?.textContent).not.toMatch(
    /session/i,
  );
});

test('FA-S3: the editor saves only the changed setting and re-reads', async () => {
  session = sessionValue(signedInUser(['CONFIGURE_SYSTEM_SETTINGS']));
  await openSignInSettings();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Edit user sign-in settings…' }),
  );
  let dialog = screen.getByRole('dialog', { name: 'User sign-in settings' });
  expect(dialog.textContent).not.toMatch(/session/i);
  fireEvent.change(within(dialog).getByLabelText('Expire after (days)'), {
    target: { value: '7' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/sign-in',
      body: { user_session_days: 7 },
    },
  ]);
  await waitFor(() =>
    expect(signInPolicyRows()['User sign-ins expire']).toBe('After 7 days'),
  );

  // Expiry Off: the days stay (disabled) and only the switch is sent.
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit user sign-in settings…' }),
  );
  dialog = screen.getByRole('dialog', { name: 'User sign-in settings' });
  const expires = within(dialog).getByRole('switch', {
    name: 'User sign-ins expire',
  });
  expect(expires).toHaveAttribute('aria-checked', 'true');
  fireEvent.click(expires);
  expect(expires).toHaveAttribute('aria-checked', 'false');
  const days = within(dialog).getByLabelText('Expire after (days)');
  expect(days).toBeDisabled();
  expect(days).toHaveValue(7);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes[1]).toEqual({
    method: 'PUT',
    url: '/api/policies/sign-in',
    body: { user_session_expires: false },
  });
  await waitFor(() =>
    expect(signInPolicyRows()['User sign-ins expire']).toBe('Never'),
  );
  expect(signInPolicy.user_session_days).toBe(7);

  // Nothing changed: Save closes without a request.
  fireEvent.click(
    screen.getByRole('button', { name: 'Edit user sign-in settings…' }),
  );
  dialog = screen.getByRole('dialog', { name: 'User sign-in settings' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(writes).toHaveLength(2);
});

test('FA-S3b: an invalid day count never blocks turning expiry Off and is not sent', async () => {
  session = sessionValue(signedInUser(['CONFIGURE_SYSTEM_SETTINGS']));
  await openSignInSettings();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Edit user sign-in settings…' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'User sign-in settings' });
  const save = within(dialog).getByRole('button', { name: 'Save changes' });
  const days = within(dialog).getByLabelText('Expire after (days)');
  fireEvent.change(days, { target: { value: '' } });
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'User sign-ins must expire after a whole number of days from 1 to 365.',
  );
  expect(save).toBeDisabled();

  fireEvent.click(
    within(dialog).getByRole('switch', { name: 'User sign-ins expire' }),
  );
  expect(days).toBeDisabled();
  expect(within(dialog).queryByRole('alert')).toBeNull();
  expect(save).toBeEnabled();
  fireEvent.click(save);
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(writes).toEqual([
    {
      method: 'PUT',
      url: '/api/policies/sign-in',
      body: { user_session_expires: false },
    },
  ]);
  expect(signInPolicy.user_session_days).toBe(30);
});

test('FA-S4: invalid values are refused in place, offline blocks editing and a failed read offers Retry', async () => {
  session = sessionValue(signedInUser(['CONFIGURE_SYSTEM_SETTINGS']));
  await openSignInSettings();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Edit user sign-in settings…' }),
  );
  const dialog = screen.getByRole('dialog', { name: 'User sign-in settings' });
  const save = within(dialog).getByRole('button', { name: 'Save changes' });
  for (const [label, value, message] of [
    [
      'Expire after (days)',
      '366',
      'User sign-ins must expire after a whole number of days from 1 to 365.',
    ],
    [
      'Failed sign-ins before a lock',
      '2',
      'The number of failed sign-ins before a lock must be a whole number from 3 to 100.',
    ],
    [
      'Lock duration (minutes)',
      '1441',
      'The lock duration must be a whole number of minutes from 1 to 1440.',
    ],
  ] as const) {
    const field = within(dialog).getByLabelText(label);
    const before = (field as HTMLInputElement).value;
    fireEvent.change(field, { target: { value } });
    expect(within(dialog).getByRole('alert')).toHaveTextContent(message);
    expect(save).toBeDisabled();
    fireEvent.change(field, { target: { value: before } });
    expect(within(dialog).queryByRole('alert')).toBeNull();
  }
  // An unanswered save is an unknown outcome; closing re-reads.
  signInPolicyFailure = 'network';
  fireEvent.click(
    within(dialog).getByRole('switch', {
      name: 'Require a new password at first sign-in',
    }),
  );
  fireEvent.click(save);
  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    'The server did not answer — the settings may or may not have been saved. Close this window to reload them before trying again.',
  );
  signInPolicyFailure = null;
  const readsBefore = signInPolicyReads();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  await waitFor(() => expect(signInPolicyReads()).toBe(readsBefore + 1));
  cleanup();

  await openSignInSettings('unavailable');
  expect(
    await screen.findByRole('button', { name: 'Edit user sign-in settings…' }),
  ).toBeDisabled();
  cleanup();

  signInPolicyFailure = { status: 500, detail: 'Database unavailable.' };
  await openSignInSettings();
  expect(
    await screen.findByText('The user sign-in settings could not be loaded.'),
  ).toBeInTheDocument();
  signInPolicyFailure = null;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() =>
    expect(signInPolicyRows()['Lock duration']).toBe('15 minutes'),
  );
});
