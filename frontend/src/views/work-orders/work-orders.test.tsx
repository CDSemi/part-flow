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

import { App } from '../../App';
import { PERMISSIONS } from '../../api/roles';
import type { Permission } from '../../api/roles';

// Work Orders regression tests (Phase 4): the view runs against the
// REAL /api/work-orders surface — these tests exercise it against an
// in-memory fake of the backend API with the same route surface and
// semantics. Covered: the Work Order Details modal dialog over the
// always-mounted list (no URL change), the New Work Order modal
// workflow (optional WO Number — a blank number saves a NULL number
// rendered as `—` — and optional due dates, missing-information Save
// Demand confirmation, duplicate numbers resolved on the server and
// opened instead of duplicated), the multi-step Add Part dialog with
// real PN lookup and create-on-first-use, PN-carrying barcodes,
// one-transaction saves that keep the draft on failure, the explicit
// §11.4 release flow (FLOATING / PLANNED, active-quantity
// confirmation, device_event_id idempotency), server-enforced demand
// removal, offline write blocking, and unsaved-change protection.

interface FakeDemand {
  id: number;
  work_order_id: number;
  part_number: string;
  request_type: 'NEW' | 'MODIFY';
  requested_quantity: number;
  allocated_quantity: number;
  due_date: string | null;
  priority_rank: number | null;
  job_numbers: string[];
  requester: string | null;
  reason: string | null;
  notes: string | null;
  // Any allocation — active or reversed — ever referenced the line.
  has_allocation_history: boolean;
}

interface FakeWorkOrder {
  id: number;
  work_order_number: string | null;
  received_date: string;
  due_date: string | null;
  status: string;
  demands: FakeDemand[];
}

interface ReleaseCommit {
  body: Record<string, unknown>;
  wire: Record<string, unknown>;
}

interface FakeState {
  workOrders: FakeWorkOrder[];
  /** PNs with saved details (create-on-first-use or explicit create). */
  partNumbers: string[];
  /** Saved Name / Description per PN (absent = null). */
  partNumberNames: Record<string, string>;
  /** Released quantity per demand id (server knowledge — derived from
   * Movement history on the real backend). A demand may be released in
   * several parts, so this is a running total, never a flag. */
  releasedQuantities: Map<number, number>;
  /** PN → existing ACTIVE distribution (confirmation payload). */
  activeDistribution: Record<
    string,
    {
      quantity_flow_id: number;
      quantity: number;
      route_mode: string;
      current_area_id: number;
      current_area_name: string;
    }[]
  >;
  committedReleases: Map<string, ReleaseCommit>;
  /** Every release body the client sent, in order — the idempotency
   * key of each attempt is observable from here. */
  releaseAttempts: Record<string, unknown>[];
  /** Reject the next release with a server error, committing nothing. */
  failNextRelease: boolean;
  nextWorkOrderId: number;
  nextDemandId: number;
  nextFlowId: number;
  nextMovementId: number;
  nextSnapshotId: number;
  healthDown: boolean;
  failNextWorkOrderWrite: boolean;
  /** Commit the release but drop the response (transport loss). */
  dropNextReleaseResponse: boolean;
  failNextList: boolean;
  /** Hold every PN lookup until the test releases it — lets a test
   * observe the UI while a lookup is genuinely in flight. */
  holdPartNumbers: Promise<void> | null;
  /** Same seam for the active Work Order list read. */
  holdWorkOrderList: Promise<void> | null;
  /** The Due Soon warning policy (`GET /api/policies/due-soon` wire). */
  policy: Record<string, unknown>;
  /** Answer every policy read with a server error while set. */
  failPolicy: boolean;
  /** Hold every policy read until the test releases it. */
  holdPolicy: Promise<void> | null;
  calls: string[];
}

function policyWire(minDays: number, percent: number, maxDays: number) {
  return {
    due_soon_min_days: minDays,
    due_soon_lead_time_percent: percent,
    due_soon_max_days: maxDays,
    updated_at: '2026-10-01T08:00:00Z',
  };
}

const T0 = '2026-08-01T00:00:00.000Z';

function demand(
  id: number,
  work_order_id: number,
  part_number: string,
  requested_quantity: number,
  extra?: Partial<FakeDemand>,
): FakeDemand {
  return {
    id,
    work_order_id,
    part_number,
    request_type: 'NEW',
    requested_quantity,
    allocated_quantity: 0,
    due_date: null,
    priority_rank: null,
    job_numbers: [],
    requester: null,
    reason: null,
    notes: null,
    has_allocation_history: false,
    ...extra,
  };
}

function seedState(): FakeState {
  return {
    workOrders: [
      {
        id: 1,
        work_order_number: '007201',
        received_date: '2026-08-01',
        due_date: '2026-09-10',
        status: 'OPEN',
        demands: [
          demand(101, 1, 'A-100', 25, {
            due_date: '2026-09-10',
            job_numbers: ['18112'],
          }),
          demand(102, 1, 'B-200', 10, { due_date: '2026-09-10' }),
          demand(103, 1, 'E-500', 7, { due_date: '2026-09-10' }),
        ],
      },
      {
        id: 2,
        work_order_number: null,
        received_date: '2026-08-05',
        due_date: null,
        status: 'OPEN',
        demands: [demand(201, 2, 'C-300', 5)],
      },
      // Every demand of this Work Order has release evidence — the
      // derived read status is RELEASED.
      {
        id: 3,
        work_order_number: '007300',
        received_date: '2026-07-25',
        due_date: '2026-08-30',
        status: 'OPEN',
        demands: [demand(301, 3, 'D-400', 8, { due_date: '2026-08-30' })],
      },
    ],
    partNumbers: ['309-127', 'A-100', 'B-200', 'C-300', 'D-400', 'E-500'],
    partNumberNames: {},
    // Demands 102 (10 pcs) and 301 (8 pcs) are fully released.
    releasedQuantities: new Map([
      [102, 10],
      [301, 8],
    ]),
    activeDistribution: {
      'A-100': [
        {
          quantity_flow_id: 71,
          quantity: 40,
          route_mode: 'FLOATING',
          current_area_id: 2,
          current_area_name: 'Lathe',
        },
      ],
    },
    committedReleases: new Map(),
    releaseAttempts: [],
    failNextRelease: false,
    nextWorkOrderId: 100,
    nextDemandId: 1000,
    nextFlowId: 500,
    nextMovementId: 9000,
    nextSnapshotId: 300,
    healthDown: false,
    failNextWorkOrderWrite: false,
    dropNextReleaseResponse: false,
    failNextList: false,
    holdPartNumbers: null,
    holdWorkOrderList: null,
    policy: policyWire(2, 15, 7),
    failPolicy: false,
    holdPolicy: null,
    calls: [],
  };
}

let state: FakeState;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function detailResponse(message: string, status: number): Response {
  return json({ detail: message }, status);
}

function releasedOf(demandId: number): number {
  return state.releasedQuantities.get(demandId) ?? 0;
}

function remainingOf(demand: FakeDemand): number {
  return Math.max(demand.requested_quantity - releasedOf(demand.id), 0);
}

/** Take one demand off the Hot list and close the gap densely — the
 * server's removal (a confirmed line deletion or an automatic one). */
function leaveHotList(target: FakeDemand) {
  const rank = target.priority_rank;
  if (rank === null) return;
  target.priority_rank = null;
  for (const wo of state.workOrders) {
    for (const d of wo.demands) {
      if (d.priority_rank !== null && d.priority_rank > rank) {
        d.priority_rank -= 1;
      }
    }
  }
}

/** Server-derived read status: RELEASED once EVERY current demand is
 * fully released; the stored column stays OPEN. A partly released
 * line keeps the Work Order Open — its remainder is still releasable. */
function derivedStatus(wo: FakeWorkOrder): string {
  return wo.demands.length > 0 && wo.demands.every((d) => remainingOf(d) === 0)
    ? 'RELEASED'
    : wo.status;
}

function summaryWire(wo: FakeWorkOrder) {
  return {
    id: wo.id,
    work_order_number: wo.work_order_number,
    received_date: wo.received_date,
    due_date: wo.due_date,
    status: derivedStatus(wo),
    demand_line_count: wo.demands.length,
    part_numbers: wo.demands.map((d) => d.part_number),
  };
}

/** The server's active-list page: the contains-match over the Work
 * Order Number runs HERE (GUI_DESIGN §11.1 — never over PNs, and never
 * in the browser), and the result is bounded exactly as the real
 * backend bounds it (`work_orders.LIST_RESULT_LIMIT`). */
const FAKE_LIST_LIMIT = 100;

function listPage(search: string | null) {
  const term = (search ?? '').trim().toLowerCase();
  return state.workOrders
    .filter(
      (w) => !term || (w.work_order_number ?? '').toLowerCase().includes(term),
    )
    .slice(0, FAKE_LIST_LIMIT)
    .map(summaryWire);
}

function detailWire(wo: FakeWorkOrder) {
  return {
    id: wo.id,
    work_order_number: wo.work_order_number,
    received_date: wo.received_date,
    due_date: wo.due_date,
    status: derivedStatus(wo),
    created_at: T0,
    updated_at: T0,
    demands: wo.demands.map((d) => ({
      ...d,
      // Server-derived release evidence — never a client-session flag.
      has_released_quantity: releasedOf(d.id) > 0,
      released_quantity: releasedOf(d.id),
      remaining_quantity: remainingOf(d),
    })),
  };
}

const AREAS = [
  {
    id: 1,
    department_id: 1,
    name: 'Material',
    barcode_value: 'PF:AREA:1',
    description: null,
    color: '#4f8cff',
    icon_url: null,
    is_terminal: false,
    is_active: true,
  },
  {
    id: 2,
    department_id: 1,
    name: 'Lathe',
    barcode_value: 'PF:AREA:2',
    description: null,
    color: '#f2a44a',
    icon_url: null,
    is_terminal: false,
    is_active: true,
  },
  {
    id: 3,
    department_id: 1,
    name: 'Stockroom',
    barcode_value: 'PF:AREA:3',
    description: null,
    color: '#7bd88f',
    icon_url: null,
    is_terminal: true,
    is_active: true,
  },
];

const OPERATIONS = [
  {
    id: 11,
    area_id: 1,
    code: 'RECV',
    name: 'Receiving',
    description: null,
    default_expected_duration: null,
    is_external: false,
    is_active: true,
  },
  {
    id: 12,
    area_id: 1,
    code: 'INSP',
    name: 'Inspection',
    description: null,
    default_expected_duration: null,
    is_external: false,
    is_active: true,
  },
  {
    id: 21,
    area_id: 2,
    code: 'TURN',
    name: 'Turning',
    description: null,
    default_expected_duration: null,
    is_external: false,
    is_active: true,
  },
];

const ROUTE_TEMPLATES = [
  {
    id: 1,
    name: 'Standard Flow',
    description: 'Material → Lathe',
    created_at: T0,
    updated_at: T0,
    steps: [
      { id: 1, sequence: 10, area_id: 1, operation_id: 11, instructions: null },
      {
        id: 2,
        sequence: 20,
        area_id: 2,
        operation_id: null,
        instructions: null,
      },
    ],
  },
];

function releaseWire(
  body: Record<string, unknown>,
  flowId: number,
  movementId: number,
  snapshotId: number | null,
) {
  return {
    quantity_flow_id: flowId,
    part_number: body.part_number,
    quantity: body.quantity,
    route_mode: body.route_mode,
    assigned_route_id: snapshotId,
    starting_area_id: body.starting_area_id,
    operation_id: body.operation_id,
    movement_id: movementId,
    device_event_id: body.device_event_id,
    occurred_at: '2026-08-19T12:00:00.000Z',
  };
}

/**
 * Management needs a signed-in user (Phase 14 slice 3): the fake signs
 * in a user holding every permission unless a test grants fewer.
 */
let sessionPermissions: readonly Permission[] = PERMISSIONS;

/** Refuse the next release before anything is written (sign-in,
 * permission, another user's request id). */
let nextReleaseRefusal: {
  status: number;
  body: Record<string, unknown>;
} | null = null;

/**
 * Management allocation (Phase 14 slice 5): the stocked quantity of a
 * PN not allocated yet, the active allocation rows of each demand line,
 * and every allocation command the client posted.
 */
let availableStock: Record<string, number> = {};
let allocationRows: {
  id: number;
  demandId: number;
  quantity: number;
  exceedsDemand: boolean;
  reason: string | null;
}[] = [];
let allocationPosts: { url: string; body: Record<string, unknown> }[] = [];

function sessionResponse(): Response {
  return new Response(
    JSON.stringify({
      user: {
        id: 90,
        login_name: 'mia',
        display_name: 'Mia Manager',
        role_id: 2,
        role_name: 'Manager',
        avatar_updated_at: null,
        permissions: sessionPermissions,
        must_change_password: false,
        session_expires_at: null,
      },
      setup_open: false,
    }),
    { status: 200 },
  );
}

async function handle(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? 'GET';
  if (url === '/api/session') return sessionResponse();
  state.calls.push(`${method} ${url}`);
  const body =
    typeof init?.body === 'string'
      ? (JSON.parse(init.body) as Record<string, unknown>)
      : {};

  if (url === '/api/health') {
    return state.healthDown
      ? detailResponse('Service unavailable.', 503)
      : json({ status: 'ok' });
  }
  if (url === '/api/areas') return json(AREAS);
  if (url === '/api/operations') return json(OPERATIONS);
  if (url === '/api/policies/due-soon') {
    if (state.holdPolicy) await state.holdPolicy;
    return state.failPolicy
      ? detailResponse('The policy store is unavailable.', 500)
      : json(state.policy);
  }
  if (url === '/api/route-templates') return json(ROUTE_TEMPLATES);
  if (url.startsWith('/api/part-numbers')) {
    if (state.holdPartNumbers) await state.holdPartNumbers;
    const params = new URLSearchParams(url.split('?')[1] ?? '');
    const number = params.get('number');
    // The §4.1 PartNumberResponse shape.
    const pnWire = (pn: string) => ({
      part_number: pn,
      barcode_value: `PF:PN:${pn}`,
      name: state.partNumberNames[pn] ?? null,
      current_revision: null,
      erp_id: null,
      image_updated_at: null,
      created_at: T0,
      updated_at: T0,
    });
    if (method === 'POST') {
      const pn = String(body.part_number).trim().toUpperCase();
      if (state.partNumbers.includes(pn)) {
        return detailResponse(
          `Part Number “${pn}” already has saved details.`,
          409,
        );
      }
      state.partNumbers.push(pn);
      if (typeof body.name === 'string') state.partNumberNames[pn] = body.name;
      return json(pnWire(pn), 201);
    }
    if (method === 'PATCH' || method === 'DELETE') {
      const pn = String(number).trim().toUpperCase();
      if (!state.partNumbers.includes(pn)) {
        return detailResponse(`Part Number ${pn} has no saved details.`, 404);
      }
      if (method === 'DELETE') {
        state.partNumbers = state.partNumbers.filter((p) => p !== pn);
        return new Response(null, { status: 204 });
      }
      if ('name' in body) {
        if (typeof body.name === 'string') {
          state.partNumberNames[pn] = body.name;
        } else delete state.partNumberNames[pn];
      }
      return json(pnWire(pn));
    }
    if (number !== null) {
      const canonical = number.trim().toUpperCase();
      return json(
        state.partNumbers.filter((pn) => pn === canonical).map(pnWire),
      );
    }
    // The server search matches the PN or the saved name.
    const search = (params.get('search') ?? '').toUpperCase();
    return json(
      state.partNumbers
        .filter(
          (pn) =>
            !search ||
            pn.toUpperCase().includes(search) ||
            (state.partNumberNames[pn] ?? '').toUpperCase().includes(search),
        )
        .sort()
        // The real backend bounds the listing in the query
        // (part_numbers.SEARCH_RESULT_LIMIT) — mirrored so the fake
        // cannot promise the client an unbounded catalog.
        .slice(0, 50)
        .map(pnWire),
    );
  }

  // ---- Release --------------------------------------------------------
  const releaseMatch =
    /^\/api\/work-orders\/(\d+)\/demands\/(\d+)\/release$/.exec(url);
  if (releaseMatch && method === 'POST') {
    const deviceEventId = String(body.device_event_id);
    state.releaseAttempts.push(body);
    if (nextReleaseRefusal) {
      const refusal = nextReleaseRefusal;
      nextReleaseRefusal = null;
      return json(refusal.body, refusal.status);
    }
    const replay = state.committedReleases.get(deviceEventId);
    if (replay) return json(replay.wire, 200);
    if (state.failNextRelease) {
      state.failNextRelease = false;
      return detailResponse('Release rejected by the server.', 409);
    }
    const pn = String(body.part_number);
    const distribution = state.activeDistribution[pn];
    if (distribution && body.confirm_active_quantity !== true) {
      return json(
        {
          detail: `Part Number '${pn}' already has active production quantity. Review the existing distribution and confirm the intent to release a separate Quantity Flow — existing quantity is never merged.`,
          confirmation_required: true,
          existing_active_quantity: distribution,
        },
        409,
      );
    }
    const snapshotId =
      body.route_mode === 'PLANNED' ? state.nextSnapshotId++ : null;
    const wire = releaseWire(
      body,
      state.nextFlowId++,
      state.nextMovementId++,
      snapshotId,
    );
    const releasedDemandId = Number(releaseMatch[2]);
    state.committedReleases.set(deviceEventId, { body, wire });
    state.releasedQuantities.set(
      releasedDemandId,
      releasedOf(releasedDemandId) + Number(body.quantity),
    );
    if (state.dropNextReleaseResponse) {
      state.dropNextReleaseResponse = false;
      throw new TypeError('Failed to fetch');
    }
    return json(wire, 201);
  }

  // ---- Demand removal -------------------------------------------------
  const demandMatch =
    /^\/api\/work-orders\/(\d+)\/demands\/(\d+)(?:\?(.*))?$/.exec(url);
  if (demandMatch && method === 'DELETE') {
    const wo = state.workOrders.find((w) => w.id === Number(demandMatch[1]));
    if (!wo) return detailResponse('Work Order not found.', 404);
    const demandId = Number(demandMatch[2]);
    const confirmHotRemoval =
      new URLSearchParams(demandMatch[3] ?? '').get('confirm_hot_removal') ===
      'true';
    const removed = wo.demands.find((d) => d.id === demandId);
    if (!removed) return detailResponse('Work Order Demand not found.', 404);
    // The backend order: every other rule answers its own 409 BEFORE
    // the Hot confirmation is asked for.
    if (removed.allocated_quantity > 0) {
      return detailResponse(
        'Cannot remove: stocked quantity has already been allocated to this demand line.',
        409,
      );
    }
    if (removed.has_allocation_history) {
      return detailResponse(
        'Cannot remove: stocked quantity has been allocated to this demand line before and the allocation history stays with it.',
        409,
      );
    }
    if (releasedOf(demandId) > 0) {
      return detailResponse(
        'Cannot remove: production quantity has already been released.',
        409,
      );
    }
    if (wo.demands.length <= 1) {
      return detailResponse(
        'Cannot remove the last demand line — a Work Order contains one or more demand records.',
        409,
      );
    }
    const rank = removed.priority_rank;
    if (rank !== null) {
      if (!confirmHotRemoval) {
        return json(
          {
            detail: `${removed.part_number} on Work Order ${wo.work_order_number ?? '— (internal)'} is on the Hot list at #${rank}. Removing this demand line also removes it from the Hot list, and every entry below it moves up one rank. Confirm the removal to continue. Nothing was removed.`,
            confirmation_required: true,
            hot_list_entry: {
              work_order_demand_id: removed.id,
              part_number: removed.part_number,
              rank,
            },
          },
          409,
        );
      }
      leaveHotList(removed);
    }
    wo.demands = wo.demands.filter((d) => d.id !== demandId);
    return new Response(null, { status: 204 });
  }

  // ---- Management allocation (Phase 14 slice 5) -----------------------
  if (url.startsWith('/api/allocations/management/context?')) {
    const id = Number(
      new URLSearchParams(url.split('?')[1]).get('work_order_demand_id'),
    );
    const wo = state.workOrders.find((w) => w.demands.some((d) => d.id === id));
    const target = wo?.demands.find((d) => d.id === id);
    if (!wo || !target) {
      return detailResponse(`Demand line ${id} does not exist.`, 404);
    }
    const available = availableStock[target.part_number] ?? 0;
    return json({
      part_number: target.part_number,
      stocked_quantity: available + target.allocated_quantity,
      active_allocated_quantity: target.allocated_quantity,
      available_stocked_quantity: available,
      lines: [
        {
          work_order_id: wo.id,
          work_order_number: wo.work_order_number,
          work_order_completed: false,
          received_date: wo.received_date,
          work_order_demand_id: id,
          request_type: target.request_type,
          due_date: target.due_date,
          priority_rank: target.priority_rank,
          requested_quantity: target.requested_quantity,
          allocated_quantity: target.allocated_quantity,
          remaining_shortage: Math.max(
            target.requested_quantity - target.allocated_quantity,
            0,
          ),
          beyond_demand_quantity: Math.max(
            target.allocated_quantity - target.requested_quantity,
            0,
          ),
          active_allocations: allocationRows
            .filter((row) => row.demandId === id)
            .map((row) => ({
              allocation_id: row.id,
              quantity: row.quantity,
              source: 'MANAGEMENT',
              is_manual_override: row.exceedsDemand,
              exceeds_demand: row.exceedsDemand,
              allocation_reason: row.reason,
              station_id: null,
              allocated_at: T0,
              actor_user: {
                id: 90,
                display_name: 'Mia Manager',
                avatar_updated_at: null,
              },
            })),
        },
      ],
    });
  }
  if (url === '/api/allocations/corrections' && method === 'POST') {
    allocationPosts.push({ url, body });
    const id = Number(body.work_order_demand_id);
    const target = state.workOrders
      .flatMap((w) => w.demands)
      .find((d) => d.id === id);
    if (!target) {
      return detailResponse(`Demand line ${id} does not exist.`, 422);
    }
    const quantity = Number(body.quantity);
    target.allocated_quantity += quantity;
    availableStock[target.part_number] =
      (availableStock[target.part_number] ?? 0) - quantity;
    allocationRows.push({
      id: 500 + allocationRows.length,
      demandId: id,
      quantity,
      exceedsDemand: true,
      reason: String(body.reason),
    });
    return json(
      {
        kind: 'ALLOCATE_BEYOND_DEMAND',
        part_number: target.part_number,
        allocation_quantity: quantity,
        rows: [],
        completed_work_order_ids: [],
        reopened_work_order_ids: [],
        device_event_id: body.device_event_id,
      },
      201,
    );
  }

  // ---- Work Orders ----------------------------------------------------
  if (url.startsWith('/api/work-orders?')) {
    const params = new URLSearchParams(url.split('?')[1]);
    const number = params.get('number');
    if (number !== null) {
      // Exact resolution answers "does this number already exist?", so
      // it is never bounded away (mirrors the real backend).
      return json(
        state.workOrders
          .filter((w) => w.work_order_number === number)
          .map(summaryWire),
      );
    }
    if (state.holdWorkOrderList) await state.holdWorkOrderList;
    return json(listPage(params.get('search')));
  }
  if (url === '/api/work-orders' && method === 'GET') {
    if (state.failNextList) {
      state.failNextList = false;
      return detailResponse('The database is unavailable.', 500);
    }
    if (state.holdWorkOrderList) await state.holdWorkOrderList;
    return json(listPage(null));
  }
  if (url === '/api/work-orders' && method === 'POST') {
    if (state.failNextWorkOrderWrite) {
      state.failNextWorkOrderWrite = false;
      return detailResponse('The save failed on the server.', 500);
    }
    const number = body.work_order_number as string | null;
    if (
      number !== null &&
      state.workOrders.some((w) => w.work_order_number === number)
    ) {
      return detailResponse(
        `Work Order Number '${number}' already exists.`,
        409,
      );
    }
    const wo: FakeWorkOrder = {
      id: state.nextWorkOrderId++,
      work_order_number: number,
      received_date: String(body.received_date),
      due_date: (body.due_date as string | null) ?? null,
      status: 'OPEN',
      demands: [],
    };
    for (const line of body.lines as Record<string, unknown>[]) {
      const pn = String(line.part_number).trim().toUpperCase();
      if (!state.partNumbers.includes(pn)) state.partNumbers.push(pn);
      wo.demands.push(
        demand(
          state.nextDemandId++,
          wo.id,
          pn,
          Number(line.requested_quantity),
          {
            request_type: (line.request_type as 'NEW' | 'MODIFY') ?? 'NEW',
            due_date: (line.due_date as string | null) ?? null,
            job_numbers: (line.job_numbers as string[]) ?? [],
            notes: (line.notes as string | null) ?? null,
          },
        ),
      );
    }
    state.workOrders.unshift(wo);
    return json(detailWire(wo), 201);
  }
  const workOrderMatch = /^\/api\/work-orders\/(\d+)$/.exec(url);
  if (workOrderMatch) {
    const wo = state.workOrders.find((w) => w.id === Number(workOrderMatch[1]));
    if (!wo) return detailResponse('Work Order not found.', 404);
    if (method === 'GET') return json(detailWire(wo));
    if (method === 'PATCH') {
      if (state.failNextWorkOrderWrite) {
        state.failNextWorkOrderWrite = false;
        return detailResponse('The save failed on the server.', 500);
      }
      if ('work_order_number' in body) {
        // Verbatim semantics: the entered string is stored exactly;
        // uniqueness spans non-null numbers.
        const nextNumber = body.work_order_number as string | null;
        if (
          nextNumber !== null &&
          nextNumber !== wo.work_order_number &&
          state.workOrders.some((w) => w.work_order_number === nextNumber)
        ) {
          return detailResponse(
            `Work Order '${nextNumber}' already exists. Open the existing Work Order instead of creating a duplicate.`,
            409,
          );
        }
        wo.work_order_number = nextNumber;
      }
      if ('due_date' in body) wo.due_date = body.due_date as string | null;
      for (const edit of (body.line_edits ?? []) as Record<string, unknown>[]) {
        const target = wo.demands.find((d) => d.id === Number(edit.id));
        if (!target) return detailResponse('Demand not found.', 404);
        // Mirror of the backend restricted-edit rule for a released
        // line (PROJECT_PROFILE §13): the identity of the work and the
        // intake metadata are locked, and the quantity may not fall
        // below what production has already committed.
        const releasedQuantity = releasedOf(target.id);
        if (releasedQuantity > 0) {
          const locked = (
            [
              ['request_type', 'Request Type'],
              ['requester', 'Requester'],
              ['reason', 'Reason'],
              ['notes', 'Notes'],
            ] as const
          ).find(([field]) => field in edit && edit[field] !== target[field]);
          if (locked) {
            return detailResponse(
              `Cannot change ${locked[1]} for Part Number '${target.part_number}': production quantity has already been released for this demand line. Qty, Due date and Job Numbers stay editable.`,
              409,
            );
          }
          const committed = Math.max(
            releasedQuantity,
            target.allocated_quantity,
          );
          if (
            'requested_quantity' in edit &&
            Number(edit.requested_quantity) < committed
          ) {
            return detailResponse(
              `Cannot lower Qty to ${Number(edit.requested_quantity)} pcs for Part Number '${target.part_number}': ${releasedQuantity} pcs are already released. Enter ${committed} pcs or more.`,
              409,
            );
          }
        }
        if ('request_type' in edit) {
          if (edit.request_type === null) {
            return detailResponse('request_type must not be null.', 422);
          }
          target.request_type = edit.request_type as 'NEW' | 'MODIFY';
        }
        if ('requested_quantity' in edit) {
          target.requested_quantity = Number(edit.requested_quantity);
          // A ranked line the save leaves fully allocated leaves the
          // Hot list automatically, in the same save.
          if (target.requested_quantity <= target.allocated_quantity) {
            leaveHotList(target);
          }
        }
        if ('due_date' in edit)
          target.due_date = edit.due_date as string | null;
        if ('job_numbers' in edit) {
          target.job_numbers = edit.job_numbers as string[];
        }
        if ('notes' in edit) target.notes = edit.notes as string | null;
      }
      for (const line of (body.new_lines ?? []) as Record<string, unknown>[]) {
        const pn = String(line.part_number).trim().toUpperCase();
        if (!state.partNumbers.includes(pn)) state.partNumbers.push(pn);
        wo.demands.push(
          demand(
            state.nextDemandId++,
            wo.id,
            pn,
            Number(line.requested_quantity),
            {
              request_type: (line.request_type as 'NEW' | 'MODIFY') ?? 'NEW',
              due_date: (line.due_date as string | null) ?? null,
              job_numbers: (line.job_numbers as string[]) ?? [],
              notes: (line.notes as string | null) ?? null,
            },
          ),
        );
      }
      return json(detailWire(wo));
    }
  }

  return detailResponse(`Unhandled fake route: ${method} ${url}`, 500);
}

beforeEach(() => {
  state = seedState();
  sessionPermissions = PERMISSIONS;
  nextReleaseRefusal = null;
  availableStock = {};
  allocationRows = [];
  allocationPosts = [];
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
  vi.restoreAllMocks();
});

async function renderWorkOrders() {
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);
  await screen.findByRole('heading', { name: 'Work Orders' });
  await screen.findByText('007201');
}

function openNewWorkOrderDialog() {
  const button = screen.getByRole('button', { name: '＋ New Work Order' });
  // A real click focuses the button first; jsdom needs this explicitly
  // so focus restoration on close can be asserted.
  button.focus();
  fireEvent.click(button);
  return screen.getByRole('dialog', { name: 'New Work Order' });
}

function scanBarcode(barcode: string) {
  const scan = screen.getByLabelText('Scan PN barcode');
  fireEvent.change(scan, { target: { value: barcode } });
  fireEvent.keyDown(scan, { key: 'Enter' });
  return scan;
}

function workOrderRow(workOrderNumber: string) {
  return screen.getByRole('button', { name: new RegExp(workOrderNumber) });
}

async function openWorkOrderDetail(workOrderNumber: string, waitForPn: string) {
  const row = workOrderRow(workOrderNumber);
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  // The dialog loads the real Work Order — wait for its demand lines.
  await within(dialog).findByText(waitForPn);
  return dialog;
}

function releaseCalls(): string[] {
  return state.calls.filter((call) => call.includes('/release'));
}

/* ============ Work Order Details modal ============ */

test('clicking a Work Order row opens the Work Order Details dialog over the list', async () => {
  await renderWorkOrders();

  const dialog = await openWorkOrderDetail('007201', 'A-100');

  expect(dialog).toHaveAttribute('aria-modal', 'true');
  expect(dialog).toHaveTextContent('007201');
  expect(window.location.pathname).toBe('/management/work-orders');
  // The list stays mounted behind the overlay.
  expect(
    screen.getByRole('heading', { name: 'Work Orders' }),
  ).toBeInTheDocument();
});

test('a clean Work Order Details dialog closes directly and focus returns to its row', async () => {
  await renderWorkOrders();
  const row = workOrderRow('007201');
  await openWorkOrderDetail('007201', 'A-100');

  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(document.activeElement).toBe(row);
});

test('closing a dirty Work Order Details dialog requires explicit discard confirmation', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  fireEvent.change(within(dialog).getByLabelText('Quantity for A-100'), {
    target: { value: '30' },
  });
  expect(
    within(dialog).getAllByText('● Unsaved changes').length,
  ).toBeGreaterThan(0);
  fireEvent.keyDown(dialog, { key: 'Escape' });

  const confirm = screen.getByRole('dialog', {
    name: 'Discard unsaved demand changes?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Keep editing' }),
  );
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Quantity for A-100')).toHaveValue('30');

  fireEvent.keyDown(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
    { key: 'Escape' },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('Escape and backdrop on the Add Part child dialog close only the child', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007201', 'A-100');

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));
  const addPart = await screen.findByRole('dialog', {
    name: /Add Part — step 1/,
  });

  fireEvent.keyDown(addPart, { key: 'Escape' });
  expect(screen.queryByRole('dialog', { name: /Add Part/ })).toBeNull();
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
});

test('the detail dialog shows a real error state with Retry when the load fails', async () => {
  await renderWorkOrders();
  state.failNextList = false;
  // Fail the detail GET once: temporarily remove the Work Order.
  const [wo] = state.workOrders;
  state.workOrders = state.workOrders.filter((w) => w.id !== wo.id);
  const row = workOrderRow('007201');
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('The Work Order could not be loaded.');

  state.workOrders = [wo, ...state.workOrders];
  fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }));
  await within(dialog).findByText('A-100');
});

/* ============ New Work Order dialog ============ */

test('＋ New Work Order opens a dialog over the Work Order list without changing the URL', async () => {
  await renderWorkOrders();

  const dialog = openNewWorkOrderDialog();

  expect(dialog).toHaveAttribute('aria-modal', 'true');
  expect(window.location.pathname).toBe('/management/work-orders');
  expect(
    screen.getByRole('heading', { name: 'Work Orders' }),
  ).toBeInTheDocument();
  expect(screen.getByText('007201')).toBeInTheDocument();
});

test('Cancel (Esc) closes a clean dialog and focus returns to ＋ New Work Order', async () => {
  await renderWorkOrders();
  const newWorkOrderButton = screen.getByRole('button', {
    name: '＋ New Work Order',
  });
  openNewWorkOrderDialog();

  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(document.activeElement).toBe(newWorkOrderButton);
});

test('Escape and backdrop close a clean dialog', async () => {
  await renderWorkOrders();
  const dialog = openNewWorkOrderDialog();

  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  const dialog2 = openNewWorkOrderDialog();
  fireEvent.mouseDown(dialog2.parentElement!);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('closing a dirty New Work Order form requires confirmation and preserves entries', async () => {
  await renderWorkOrders();
  const dialog = openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007482' },
  });
  fireEvent.keyDown(dialog, { key: 'Escape' });

  // Nothing is silently discarded: an explicit confirmation appears.
  expect(
    screen.getByRole('dialog', { name: 'Discard this New Work Order?' }),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText(/^WO Number/)).toHaveValue('007482');

  fireEvent.keyDown(screen.getByRole('dialog', { name: 'New Work Order' }), {
    key: 'Escape',
  });
  fireEvent.click(screen.getByRole('button', { name: 'Discard entries' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('a complete save flow (number + due entered) commits ONE POST and reloads from the server', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007482' },
  });
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });

  // Barcode scanning remains available as a secondary method (the PN
  // resolution is a real server lookup, so the line arrives async).
  scanBarcode('PF:PN:78-04-0031');
  const qty = await screen.findByLabelText('Quantity for 78-04-0031');
  expect(document.activeElement).toBe(qty);
  fireEvent.change(qty, { target: { value: '5' } });
  fireEvent.keyDown(qty, { key: 'Enter' });
  expect(document.activeElement).toBe(screen.getByLabelText('Scan PN barcode'));

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  // The toast states the business rule; the dialog closed only after
  // the server committed, and the list shows the server's row.
  await screen.findByText(/007482 saved — business demand only/);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(await screen.findByText('007482')).toBeInTheDocument();
  expect(window.location.pathname).toBe('/management/work-orders');

  const saved = state.workOrders.find((w) => w.work_order_number === '007482');
  expect(saved).toBeDefined();
  expect(saved!.due_date).toBe('2026-09-01');
  expect(saved!.demands).toHaveLength(1);
  expect(saved!.demands[0].part_number).toBe('78-04-0031');
  expect(saved!.demands[0].requested_quantity).toBe(5);
  // The line inherited the WO due date.
  expect(saved!.demands[0].due_date).toBe('2026-09-01');
  // Save demand touched only the intake surface — never the release
  // endpoint.
  expect(releaseCalls()).toEqual([]);
  expect(
    state.calls.filter((call) => call === 'POST /api/work-orders'),
  ).toHaveLength(1);
});

test('a failed New Work Order save keeps the whole draft and every entered value', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007483' },
  });
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:78-04-0031');
  const qty = await screen.findByLabelText('Quantity for 78-04-0031');
  fireEvent.change(qty, { target: { value: '5' } });

  state.failNextWorkOrderWrite = true;
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  // No success is shown before the server commit; the draft is kept.
  await screen.findByText(/The save failed on the server/);
  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText(/^WO Number/)).toHaveValue('007483');
  expect(screen.getByLabelText('Quantity for 78-04-0031')).toHaveValue('5');
  expect(state.workOrders.some((w) => w.work_order_number === '007483')).toBe(
    false,
  );

  // The retry with the intact draft succeeds.
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/007483 saved — business demand only/);
  expect(state.workOrders.some((w) => w.work_order_number === '007483')).toBe(
    true,
  );
});

/* ============ Barcodes and PN identity ============ */

test('a non-PN barcode is rejected and adds nothing', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:MACHINE:L2');

  expect(
    screen.getByText(/Unknown barcode “PF:MACHINE:L2” — nothing added/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/No demand lines yet — add the first Part/),
  ).toBeInTheDocument();
});

test('a PN barcode carries the PN itself; an unknown PN is created on first use', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  // Arbitrary non-empty suffix — no format, no opaque id mapping. The
  // PN has no master yet, so the line is marked as a new PN.
  scanBarcode('PF:PN:NEW-PLATE-9');

  expect(await screen.findByText('NEW-PLATE-9')).toBeInTheDocument();
  expect(
    screen.getByText(/NEW-PLATE-9 added — Request Type NEW/),
  ).toBeInTheDocument();

  // Case-insensitive identity: a re-scan in different casing focuses
  // the existing line instead of duplicating it.
  scanBarcode('PF:PN:new-plate-9');
  await waitFor(() =>
    expect(screen.getAllByLabelText('Quantity for NEW-PLATE-9')).toHaveLength(
      1,
    ),
  );
});

test('scanned lines reflect the real master lookup — existing PN reuse vs. create-on-first-use', async () => {
  await renderWorkOrders();
  // Every line carries the same PN control; only the `new PN`
  // marker distinguishes a PN that has no saved details yet.
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:309-127');
  expect(
    await within(dialog).findByRole('button', {
      name: 'Edit Part Number 309-127',
    }),
  ).toHaveTextContent('309-127');

  scanBarcode('PF:PN:NEW-PLATE-9');
  const newLine = (
    await within(dialog).findByRole('button', {
      name: 'Edit Part Number NEW-PLATE-9',
    })
  ).closest('td') as HTMLElement;
  // The `new PN` marker sits on the PN's own line — no extra row.
  expect(newLine.querySelector('.pncell')).not.toBeNull();
  expect(within(newLine).getByText('new PN')).toBeInTheDocument();

  // The barcode is never a sentence in the row any more.
  expect(within(dialog).queryByText(/existing PN · barcode/)).toBeNull();
  expect(within(dialog).queryByText(/new PN — barcode/)).toBeNull();
});

test('scanning a duplicate PN focuses the existing line instead of adding one', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:78-04-0031');
  await screen.findByLabelText('Quantity for 78-04-0031');
  scanBarcode('PF:PN:78-04-0031');

  const qtyFields = screen.getAllByLabelText('Quantity for 78-04-0031');
  expect(qtyFields).toHaveLength(1);
  await waitFor(() => expect(document.activeElement).toBe(qtyFields[0]));
});

test('search is a server request over the Work Order Number, not a local filter', async () => {
  await renderWorkOrders();

  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: '007201' },
  });
  // The entry travels to the server (after the debounce) — the client
  // never downloads the whole active list to filter it itself.
  await waitFor(() =>
    expect(state.calls).toContain('GET /api/work-orders?search=007201'),
  );
  await waitFor(() =>
    expect(screen.queryByText('007300')).not.toBeInTheDocument(),
  );
  expect(screen.getByText('007201')).toBeInTheDocument();

  // §11.1 scopes the search to the WO Number. A PN is not a WO Number,
  // so the server answers with nothing and the view shows that answer —
  // it does not fall back to matching the row's PN preview text, which
  // could only ever have matched the first two PNs of a Work Order.
  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: 'A-100' },
  });
  await waitFor(() =>
    expect(state.calls).toContain('GET /api/work-orders?search=A-100'),
  );
  await waitFor(() =>
    expect(screen.queryByText('007201')).not.toBeInTheDocument(),
  );
  expect(screen.getByText(/No active Work Order matches/)).toBeInTheDocument();
});

test('the active list is bounded by the server and says so', async () => {
  // More Work Orders than one page: nothing leaves the active list
  // before allocation-derived completion (Phase 10), so the read must
  // be bounded and the view must say what it is showing.
  for (let index = 0; index < 120; index += 1) {
    state.workOrders.push({
      id: 5000 + index,
      work_order_number: `BULK-${String(index).padStart(4, '0')}`,
      received_date: '2026-08-02',
      due_date: null,
      status: 'OPEN',
      demands: [demand(9000 + index, 5000 + index, 'Z-900', 1)],
    });
  }
  await renderWorkOrders();

  await waitFor(() =>
    expect(
      screen.getByText(/Showing the first 100 Work Orders/),
    ).toBeInTheDocument(),
  );
  expect(document.querySelectorAll('.wolist tbody tr')).toHaveLength(100);

  // A Work Order beyond the bound is still reachable by searching for
  // it — the WHERE runs before the LIMIT on the server.
  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: 'BULK-0119' },
  });
  await waitFor(() =>
    expect(screen.getByText('BULK-0119')).toBeInTheDocument(),
  );
  expect(
    screen.queryByText(/Showing the first 100 Work Orders/),
  ).not.toBeInTheDocument();
});

test('a search in flight never presents the previous page as its answer', async () => {
  // A full previous page and a search still on the wire: the bound
  // belongs to the OLD query, so it must not be shown as this search's
  // state, and the rows on screen must not be read as its answer.
  for (let index = 0; index < 120; index += 1) {
    state.workOrders.push({
      id: 6000 + index,
      work_order_number: `PEND-${String(index).padStart(4, '0')}`,
      received_date: '2026-08-03',
      due_date: null,
      status: 'OPEN',
      demands: [demand(9500 + index, 6000 + index, 'Z-900', 1)],
    });
  }
  await renderWorkOrders();
  await waitFor(() =>
    expect(
      screen.getByText(/Showing the first 100 Work Orders/),
    ).toBeInTheDocument(),
  );

  let release = () => {};
  state.holdWorkOrderList = new Promise<void>((resolve) => {
    release = resolve;
  });

  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: 'PEND-0119' },
  });
  // The debounce fires and the request is genuinely in flight.
  await waitFor(() =>
    expect(state.calls).toContain('GET /api/work-orders?search=PEND-0119'),
  );

  // Unanswered: the view says so, keeps the previous rows visible so the
  // list does not flicker, and concludes NOTHING about the new search.
  expect(screen.getByText('Searching Work Orders…')).toBeInTheDocument();
  expect(screen.getByText('007201')).toBeInTheDocument();
  expect(
    screen.queryByText(/Showing the first 100 Work Orders/),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/No active Work Order matches/),
  ).not.toBeInTheDocument();

  release();
  await waitFor(() =>
    expect(screen.getByText('PEND-0119')).toBeInTheDocument(),
  );
  expect(screen.queryByText('Searching Work Orders…')).not.toBeInTheDocument();
  expect(screen.queryByText('007201')).not.toBeInTheDocument();
  expect(
    screen.queryByText(/Showing the first 100 Work Orders/),
  ).not.toBeInTheDocument();
});

test("a stale empty page is never presented as the next search's answer", async () => {
  await renderWorkOrders();

  // First search: a real miss, answered.
  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: 'ZZZZ' },
  });
  await waitFor(() =>
    expect(
      screen.getByText(/No active Work Order matches/),
    ).toBeInTheDocument(),
  );

  let release = () => {};
  state.holdWorkOrderList = new Promise<void>((resolve) => {
    release = resolve;
  });

  // Second search, still in flight: the previous page was empty, but
  // "nothing matches" is a claim about THIS entry and has no answer yet.
  fireEvent.change(screen.getByLabelText('Search WO Number'), {
    target: { value: '007201' },
  });
  await waitFor(() =>
    expect(state.calls).toContain('GET /api/work-orders?search=007201'),
  );
  expect(screen.getByText('Searching Work Orders…')).toBeInTheDocument();
  expect(
    screen.queryByText(/No active Work Order matches/),
  ).not.toBeInTheDocument();

  release();
  await waitFor(() => expect(screen.getByText('007201')).toBeInTheDocument());
  expect(screen.queryByText('Searching Work Orders…')).not.toBeInTheDocument();
});

test('an internal Work Order without an external number renders as —', async () => {
  await renderWorkOrders();
  const internalRow = screen
    .getByText('internal Work Order — no external number yet')
    .closest('tr');
  expect(internalRow?.querySelector('.wo')?.textContent).toBe('—');
});

/* ============ Optional header + Save Demand confirmation ============ */

test('missing WO Number and due dates open a confirmation and save NULLs, never placeholders', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:NEW-PLATE-9');
  const qty = await screen.findByLabelText('Quantity for NEW-PLATE-9');
  fireEvent.change(qty, { target: { value: '3' } });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  // Omissions are summarized and explicitly confirmed — they are never
  // validation errors and nothing is saved yet.
  const confirm = await screen.findByRole('dialog', {
    name: 'Save demand with missing information?',
  });
  expect(confirm).toHaveTextContent(/internal Work Order without an external/);
  expect(confirm).toHaveTextContent(/remains unscheduled/);
  expect(confirm).toHaveTextContent(/1.*demand line.*has.*no.*due date/s);
  expect(state.workOrders).toHaveLength(3);

  fireEvent.click(screen.getByRole('button', { name: 'Confirm and save' }));

  await screen.findByText(/WO — saved — business demand only/);
  const saved = state.workOrders[0];
  // Nullable fields persist NULL — no temporary number, no fake date.
  expect(saved.work_order_number).toBeNull();
  expect(saved.due_date).toBeNull();
  expect(saved.demands[0].due_date).toBeNull();
  // The internal Work Order renders `—` plus its label in the list.
  expect(
    screen.getAllByText('internal Work Order — no external number yet').length,
  ).toBeGreaterThanOrEqual(2);
});

test('an invalid quantity still blocks save and keeps entered values', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  scanBarcode('PF:PN:NEW-PLATE-9');
  const qty = await screen.findByLabelText('Quantity for NEW-PLATE-9');
  fireEvent.change(qty, { target: { value: '0' } });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  expect(
    await screen.findByText('quantity must be a positive whole number'),
  ).toBeInTheDocument();
  expect(document.activeElement).toBe(qty);
  expect(qty).toHaveValue('0');
  // Nothing traveled: no create POST happened.
  expect(
    state.calls.filter((call) => call === 'POST /api/work-orders'),
  ).toHaveLength(0);
});

test('an existing WO Number is opened instead of duplicated', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  // Without entered lines the existing Work Order opens directly.
  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007201' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  expect(dialog).toHaveTextContent('007201');
  expect(
    screen.getByText(/007201 already exists — opening the existing Work Order/),
  ).toBeInTheDocument();
  // Nothing was created.
  expect(state.workOrders).toHaveLength(3);
  expect(
    state.calls.filter((call) => call === 'POST /api/work-orders'),
  ).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel (Esc)' }));

  // With entered lines, opening the existing Work Order (discarding
  // them) is confirmed explicitly first.
  openNewWorkOrderDialog();
  scanBarcode('PF:PN:NEW-PLATE-9');
  await screen.findByLabelText('Quantity for NEW-PLATE-9');
  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007201' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));

  const confirm = await screen.findByRole('dialog', {
    name: '007201 already exists',
  });
  expect(confirm).toHaveTextContent(/never duplicated/);
  fireEvent.click(
    screen.getByRole('button', { name: 'Open existing Work Order' }),
  );
  expect(
    await screen.findByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
  expect(state.workOrders).toHaveLength(3);
});

/* ============ Add Part flow ============ */

test('a one-character PN is never called new while its lookup is unresolved', async () => {
  // `X` is a valid canonical PN and its master EXISTS. The contains
  // search needs 2 characters, so a single character resolves through
  // the exact lookup — and until that lookup has answered for exactly
  // what is in the field, the dialog may not claim the PN is new.
  state.partNumbers.push('X');
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));

  let release = () => {};
  state.holdPartNumbers = new Promise<void>((resolve) => {
    release = resolve;
  });

  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: 'X' },
  });
  // Immediately: still inside the debounce, nothing was even asked.
  expect(
    screen.queryByRole('button', { name: /Create new PN/ }),
  ).not.toBeInTheDocument();

  // The debounce elapses and the exact lookup is genuinely in flight —
  // the answer is unknown, so the create offer stays absent.
  await waitFor(() =>
    expect(
      state.calls.some((call) => call.includes('/api/part-numbers?number=X')),
    ).toBe(true),
  );
  expect(
    screen.queryByRole('button', { name: /Create new PN/ }),
  ).not.toBeInTheDocument();

  // Resolved: the existing PN is offered for selection, and it is
  // still never offered as new.
  release();
  const dialog = screen.getByRole('dialog', { name: /Add Part/ });
  await within(dialog).findByRole('button', { name: /PF:PN:X/ });
  expect(
    screen.queryByRole('button', { name: /Create new PN/ }),
  ).not.toBeInTheDocument();
  // The short entry is not treated as "too short to be a PN" either.
  expect(
    within(dialog).queryByText(/Type at least 2 characters/),
  ).not.toBeInTheDocument();
});

test('an unknown one-character PN is offered for creation once the lookup confirms the miss', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));

  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: 'q' },
  });
  // Not before the answer.
  expect(
    screen.queryByRole('button', { name: /Create new PN/ }),
  ).not.toBeInTheDocument();

  // After it: the canonical form is offered for creation.
  const create = await screen.findByRole('button', {
    name: '＋ Create new PN “Q”',
  });
  fireEvent.click(create);
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();
});

test('the Add Part flow steps through PN, quantity, due date and metadata', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));
  const dialog = screen.getByRole('dialog', { name: /Add Part — step 1/ });

  // Step 1: search and select an existing PN (a real server search).
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '309' },
  });
  fireEvent.click(await screen.findByRole('button', { name: /309-127/ }));
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();

  // Step 2: same keypad + physical-keyboard interaction as Scan Station
  // (a real focusable numeric input, focused on entry).
  fireEvent.keyDown(dialog, { key: '5' });
  expect(screen.getByLabelText('Quantity: 5')).toBeInTheDocument();

  // Back preserves entered values.
  fireEvent.click(screen.getByRole('button', { name: '‹ Back' }));
  expect(screen.getByLabelText('Search PartNumber')).toHaveValue('309');
  fireEvent.click(await screen.findByRole('button', { name: /309-127/ }));
  expect(screen.getByLabelText('Quantity: 5')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next ›' }));

  // Step 3: `No due date` is an explicit, valid choice.
  fireEvent.click(screen.getByRole('radio', { name: /No due date/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Next ›' }));

  // Step 4: optional metadata, then finish.
  fireEvent.change(screen.getByLabelText('Job Numbers'), {
    target: { value: '18777' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add Part' }));

  // The flow created an editable draft row — nothing saved yet.
  expect(screen.queryByRole('dialog', { name: /Add Part/ })).toBeNull();
  expect(
    screen.getByRole('dialog', { name: 'New Work Order' }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Quantity for 309-127')).toHaveValue('5');
  const lineDue = screen.getByLabelText('Due date for 309-127');
  expect(lineDue).toHaveValue('');
  expect(state.calls.filter((call) => call.startsWith('POST'))).toHaveLength(0);

  // The explicit `No due date` line never inherits a later WO due date…
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-15' },
  });
  expect(lineDue).toHaveValue('');
});

test('a one-character PN that already exists is found, not offered as new', async () => {
  // The Add Part minimum search length bounds the contains-SEARCH; it
  // is not a rule about what a Part Number may be. A short existing PN
  // must resolve exactly, or the operator would be told to create a PN
  // that is already in the master.
  state.partNumbers.push('X');
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));

  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: 'x' },
  });

  // Offered as the existing master, with its derived barcode...
  const match = await screen.findByRole('button', { name: /PF:PN:X/ });
  expect(match).toBeInTheDocument();
  // ...and never as a new PN.
  expect(
    screen.queryByRole('button', { name: /Create new PN/ }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText(/Type at least 2 characters/),
  ).not.toBeInTheDocument();

  fireEvent.click(match);
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();
});

test('a one-character PN that does not exist is still creatable', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));

  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: 'q' },
  });

  const create = await screen.findByRole('button', {
    name: /Create new PN “Q”/,
  });
  expect(create).toBeInTheDocument();
});

test('Add Part canonicalizes manual entry like the backend — trim + uppercase, internal whitespace rejected', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));

  // Internal whitespace is invalid — never silently removed.
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: 'PL 77' },
  });
  expect(
    await screen.findByText(/cannot contain spaces or other whitespace/),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Create new PN/ })).toBeNull();

  // Surrounding whitespace trims; the canonical PN is UPPERCASE.
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '  pl-77  ' },
  });
  const create = await screen.findByRole('button', {
    name: '＋ Create new PN “PL-77”',
  });
  fireEvent.click(create);
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();
  expect(screen.getByText('new Part Number')).toBeInTheDocument();
});

test('the Add Part flow rejects a PN already on the Work Order', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();
  scanBarcode('PF:PN:78-04-0031');
  await screen.findByLabelText('Quantity for 78-04-0031');

  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '78-04-0031' },
  });
  // Scope to the Add Part dialog: the scanned demand line behind it
  // exposes its own control whose accessible name carries the same PN.
  const addPartDialog = screen.getByRole('dialog', { name: /Add Part/ });
  fireEvent.click(
    await within(addPartDialog).findByRole('button', {
      name: '＋ Create new PN “78-04-0031”',
    }),
  );

  expect(screen.queryByRole('dialog', { name: /Add Part/ })).toBeNull();
  expect(
    screen.getByText(/78-04-0031 is already on this Work Order/),
  ).toBeInTheDocument();
  expect(screen.getAllByLabelText('Quantity for 78-04-0031')).toHaveLength(1);
});

/* ============ Dates ============ */

test('editable date fields are calendar inputs (type="date")', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  expect(screen.getByLabelText('Received date')).toHaveAttribute(
    'type',
    'date',
  );
  expect(screen.getByLabelText(/^WO due date/)).toHaveAttribute('type', 'date');

  scanBarcode('PF:PN:78-04-0031');
  expect(
    await screen.findByLabelText('Due date for 78-04-0031'),
  ).toHaveAttribute('type', 'date');
});

test('new lines inherit the WO due date; edited lines keep their own date', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:AAA-1');
  await screen.findByLabelText('Due date for AAA-1');
  scanBarcode('PF:PN:BBB-2');
  await screen.findByLabelText('Due date for BBB-2');

  expect(screen.getByLabelText('Due date for AAA-1')).toHaveValue('2026-09-01');

  // Edit one line's date — it becomes user-owned.
  fireEvent.change(screen.getByLabelText('Due date for BBB-2'), {
    target: { value: '2026-09-20' },
  });
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-10-01' },
  });

  expect(screen.getByLabelText('Due date for AAA-1')).toHaveValue('2026-10-01');
  expect(screen.getByLabelText('Due date for BBB-2')).toHaveValue('2026-09-20');
});

test('a line whose due date is cleared is user-edited and stops inheriting', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:AAA-1');
  const lineDue = await screen.findByLabelText('Due date for AAA-1');
  fireEvent.change(lineDue, { target: { value: '' } });

  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-10-01' },
  });
  expect(lineDue).toHaveValue('');
});

test('a Work Order without a due date displays cleanly in list and detail', async () => {
  await renderWorkOrders();

  // The internal seeded Work Order has no due date: the list shows `—`
  // for both the number and the due date.
  const internalRow = screen
    .getByText('internal Work Order — no external number yet')
    .closest('tr')!;
  expect(
    within(internalRow as HTMLElement).getAllByText('—').length,
  ).toBeGreaterThanOrEqual(2);

  const row = screen.getByRole('button', { name: 'Open Work Order —' });
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('C-300');
  expect(within(dialog).getByText('no due date')).toBeInTheDocument();
});

/* ============ OPEN Work Order editing ============ */

test('scanning a new PN on an OPEN Work Order adds a draft line and marks unsaved', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:EXTRA-77');
  await within(dialog).findByText('EXTRA-77');

  expect(
    within(dialog).getAllByText('● Unsaved changes').length,
  ).toBeGreaterThan(0);
  expect(within(dialog).getByText('Draft (unsaved)')).toBeInTheDocument();
});

test('a duplicate PN on an OPEN Work Order focuses the existing line', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:A-100');

  expect(
    await screen.findByText(/A-100 is already on this Work Order/),
  ).toBeInTheDocument();
  const qty = within(dialog).getByLabelText('Quantity for A-100');
  await waitFor(() => expect(document.activeElement).toBe(qty));
});

test('an unsaved draft line is removed immediately without a confirmation dialog', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:EXTRA-77');
  await within(dialog).findByText('EXTRA-77');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Remove line EXTRA-77' }),
  );

  expect(screen.queryByRole('dialog', { name: /Remove/ })).toBeNull();
  expect(within(dialog).queryByText('EXTRA-77')).toBeNull();
  expect(
    screen.getByText('✕ Draft line removed — it had never been saved.'),
  ).toBeInTheDocument();
  // No server call happened for a draft-only removal.
  expect(state.calls.filter((call) => call.startsWith('DELETE'))).toHaveLength(
    0,
  );
});

test('removing a saved unreleased line requires confirmation and commits on the server', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Remove line A-100' }),
  );
  const confirm = screen.getByRole('dialog', {
    name: 'Remove A-100 from 007201?',
  });
  expect(confirm).toHaveTextContent(/never deletes the PartNumber master/);

  fireEvent.click(within(confirm).getByRole('button', { name: 'Remove line' }));

  await screen.findByText('✕ A-100 removed from 007201.');
  expect(within(dialog).queryByText('A-100')).toBeNull();
  expect(state.workOrders[0].demands.map((d) => d.id)).toEqual([102, 103]);
  expect(
    state.calls.filter((call) =>
      call.startsWith('DELETE /api/work-orders/1/demands/101'),
    ),
  ).toHaveLength(1);
});

/* ---- Removing a demand line on the Hot list (owner decision OD3) ---- */

const HOT_REMOVE_DIALOG = 'Remove a demand line on the Hot list?';

/** Hot list: A-100 #1 and E-500 #2 (WO 007201), C-300 #3 (internal). */
function rankHotLines() {
  state.workOrders[0].demands[0].priority_rank = 1;
  state.workOrders[0].demands[2].priority_rank = 2;
  state.workOrders[1].demands[0].priority_rank = 3;
}

function deleteCalls(): string[] {
  return state.calls.filter((call) => call.startsWith('DELETE'));
}

async function openHotRemoval(dialog: HTMLElement, pn: string) {
  const remove = within(dialog).getByRole('button', {
    name: `Remove line ${pn}`,
  });
  await waitFor(() => expect(remove).toBeEnabled());
  fireEvent.click(remove);
  return screen.getByRole('dialog', { name: HOT_REMOVE_DIALOG });
}

test('a Hot demand line’s ✕ opens the typed confirmation naming the PN and its rank; Cancel sends nothing', async () => {
  rankHotLines();
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'E-500');

  const remove = within(dialog).getByRole('button', {
    name: 'Remove line E-500',
  });
  await waitFor(() => expect(remove).toBeEnabled());
  expect(remove).toHaveAttribute(
    'title',
    'Remove line — it is on the Hot list (🔥#2); asks for typed confirmation',
  );
  // No standing refusal under the button any more.
  expect(within(dialog).queryByText(/Cannot remove: this demand line/)).toBe(
    null,
  );

  const typed = await openHotRemoval(dialog, 'E-500');
  expect(typed).toHaveTextContent(
    '⚠ E-500 · 007201 is on the Hot list at 🔥#2. Removing the line also takes it off the Hot list, and every Hot entry below it moves up one rank. Undo in Priority cannot bring it back — the demand line will no longer exist.',
  );
  expect(typed).toHaveTextContent(/never deletes the PartNumber master/);
  // The plain confirmation is not used for a Hot line.
  expect(screen.queryByRole('dialog', { name: /Remove E-500 from/ })).toBe(
    null,
  );

  // Confirm stays disabled until the PN is typed (trimmed,
  // case-insensitive).
  const confirm = within(typed).getByRole('button', {
    name: 'Remove line and Hot entry',
  });
  const input = within(typed).getByRole('textbox');
  expect(confirm).toBeDisabled();
  fireEvent.change(input, { target: { value: 'E-50' } });
  expect(confirm).toBeDisabled();
  fireEvent.change(input, { target: { value: '  e-500 ' } });
  expect(confirm).toBeEnabled();

  fireEvent.click(within(typed).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog', { name: HOT_REMOVE_DIALOG })).toBeNull();
  expect(deleteCalls()).toEqual([]);
  expect(state.workOrders[0].demands).toHaveLength(3);
  expect(state.workOrders[0].demands[2].priority_rank).toBe(2);
});

test('confirming a Hot line removal sends one flagged DELETE and removes the line and its Hot entry', async () => {
  rankHotLines();
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'E-500');

  const typed = await openHotRemoval(dialog, 'E-500');
  fireEvent.change(within(typed).getByRole('textbox'), {
    target: { value: 'E-500' },
  });
  fireEvent.click(
    within(typed).getByRole('button', { name: 'Remove line and Hot entry' }),
  );

  await screen.findByText(
    '✕ E-500 removed from 007201; it is no longer on the Hot list.',
  );
  expect(deleteCalls()).toEqual([
    'DELETE /api/work-orders/1/demands/103?confirm_hot_removal=true',
  ]);
  expect(within(dialog).queryByText('E-500')).toBeNull();
  expect(state.workOrders[0].demands.map((d) => d.id)).toEqual([101, 102]);
  // The Hot entry below moved up one rank; the one above is untouched.
  expect(state.workOrders[0].demands[0].priority_rank).toBe(1);
  expect(state.workOrders[1].demands[0].priority_rank).toBe(2);
});

test('unsaved edits disable a Hot line’s ✕, and offline disables the typed Confirm', async () => {
  rankHotLines();
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'E-500');
  const remove = within(dialog).getByRole('button', {
    name: 'Remove line E-500',
  });
  await waitFor(() => expect(remove).toBeEnabled());

  const qty = within(dialog).getByLabelText('Quantity for A-100');
  fireEvent.change(qty, { target: { value: '30' } });
  expect(remove).toBeDisabled();
  expect(remove).toHaveAttribute(
    'title',
    'Save or discard demand changes before removing a saved line.',
  );
  fireEvent.change(qty, { target: { value: '25' } });
  expect(remove).toBeEnabled();

  const typed = await openHotRemoval(dialog, 'E-500');
  fireEvent.change(within(typed).getByRole('textbox'), {
    target: { value: 'E-500' },
  });
  const confirm = within(typed).getByRole('button', {
    name: 'Remove line and Hot entry',
  });
  expect(confirm).toBeEnabled();

  state.healthDown = true;
  act(() => {
    window.dispatchEvent(new Event('offline'));
  });
  await waitFor(() => expect(confirm).toBeDisabled());
  fireEvent.click(confirm);
  expect(deleteCalls()).toEqual([]);
});

test('a Hot line with allocated quantity gets the plain confirmation and the allocated refusal', async () => {
  state.workOrders[0].demands[2].priority_rank = 2;
  state.workOrders[0].demands[2].allocated_quantity = 3;
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'E-500');

  const remove = within(dialog).getByRole('button', {
    name: 'Remove line E-500',
  });
  await waitFor(() => expect(remove).toBeEnabled());
  expect(remove).toHaveAttribute(
    'title',
    'Remove line (asks for confirmation)',
  );
  fireEvent.click(remove);
  const confirm = screen.getByRole('dialog', {
    name: 'Remove E-500 from 007201?',
  });
  fireEvent.click(within(confirm).getByRole('button', { name: 'Remove line' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Cannot remove: stocked quantity has already been allocated to this demand line.',
  );
  expect(screen.queryByRole('dialog', { name: HOT_REMOVE_DIALOG })).toBeNull();
  expect(deleteCalls()).toEqual(['DELETE /api/work-orders/1/demands/103']);
  expect(within(dialog).getByText('E-500')).toBeInTheDocument();
  expect(state.workOrders[0].demands).toHaveLength(3);
});

test('a Hot line with reversed allocation history gets the plain confirmation, never the typed one', async () => {
  // Allocated 3, then reversed: no allocated quantity left, but the
  // allocation history keeps the line unremovable.
  state.workOrders[0].demands[2].priority_rank = 2;
  state.workOrders[0].demands[2].has_allocation_history = true;
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'E-500');

  const remove = within(dialog).getByRole('button', {
    name: 'Remove line E-500',
  });
  await waitFor(() => expect(remove).toBeEnabled());
  expect(remove).toHaveAttribute(
    'title',
    'Remove line (asks for confirmation)',
  );
  fireEvent.click(remove);
  expect(screen.queryByRole('dialog', { name: HOT_REMOVE_DIALOG })).toBeNull();
  const confirm = screen.getByRole('dialog', {
    name: 'Remove E-500 from 007201?',
  });
  fireEvent.click(within(confirm).getByRole('button', { name: 'Remove line' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Cannot remove: stocked quantity has been allocated to this demand line before and the allocation history stays with it.',
  );
  expect(screen.queryByRole('dialog', { name: HOT_REMOVE_DIALOG })).toBeNull();
  expect(deleteCalls()).toEqual(['DELETE /api/work-orders/1/demands/103']);
  expect(state.workOrders[0].demands).toHaveLength(3);
  expect(state.workOrders[0].demands[2].priority_rank).toBe(2);
});

test('a line ranked after the view loaded upgrades the plain confirmation to the typed one with the server’s rank', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'E-500');

  // Elsewhere: E-500 was added to the Hot list at #4 after this
  // dialog loaded it unranked.
  state.workOrders[0].demands[2].priority_rank = 4;
  const remove = within(dialog).getByRole('button', {
    name: 'Remove line E-500',
  });
  await waitFor(() => expect(remove).toBeEnabled());
  fireEvent.click(remove);
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Remove E-500 from 007201?' }),
    ).getByRole('button', { name: 'Remove line' }),
  );

  const typed = await screen.findByRole('dialog', { name: HOT_REMOVE_DIALOG });
  expect(typed).toHaveTextContent('E-500 · 007201 is on the Hot list at 🔥#4.');
  // Nothing was removed, so nothing is reported as an error.
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByText(/Confirm the removal to continue/)).toBeNull();
  expect(
    screen.queryByRole('dialog', { name: 'Remove E-500 from 007201?' }),
  ).toBeNull();
  expect(state.workOrders[0].demands).toHaveLength(3);
  expect(deleteCalls()).toEqual(['DELETE /api/work-orders/1/demands/103']);

  fireEvent.change(within(typed).getByRole('textbox'), {
    target: { value: 'E-500' },
  });
  fireEvent.click(
    within(typed).getByRole('button', { name: 'Remove line and Hot entry' }),
  );
  await screen.findByText(
    '✕ E-500 removed from 007201; it is no longer on the Hot list.',
  );
  expect(deleteCalls()).toEqual([
    'DELETE /api/work-orders/1/demands/103',
    'DELETE /api/work-orders/1/demands/103?confirm_hot_removal=true',
  ]);
  expect(state.workOrders[0].demands.map((d) => d.id)).toEqual([101, 102]);
});

test('a confirmed Hot line removal refused as released shows the error and keeps the line', async () => {
  rankHotLines();
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'E-500');

  const typed = await openHotRemoval(dialog, 'E-500');
  // Elsewhere: E-500 was released after this dialog loaded.
  state.releasedQuantities.set(103, 7);
  fireEvent.change(within(typed).getByRole('textbox'), {
    target: { value: 'E-500' },
  });
  fireEvent.click(
    within(typed).getByRole('button', { name: 'Remove line and Hot entry' }),
  );

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Cannot remove: production quantity has already been released.',
  );
  expect(screen.queryByRole('dialog', { name: HOT_REMOVE_DIALOG })).toBeNull();
  expect(deleteCalls()).toEqual([
    'DELETE /api/work-orders/1/demands/103?confirm_hot_removal=true',
  ]);
  expect(within(dialog).getByText('E-500')).toBeInTheDocument();
  expect(state.workOrders[0].demands).toHaveLength(3);
  expect(state.workOrders[0].demands[2].priority_rank).toBe(2);
});

test('a save that leaves a Hot line fully allocated says it left the Hot list', async () => {
  rankHotLines();
  state.workOrders[0].demands[2].allocated_quantity = 3;
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'E-500');

  fireEvent.change(within(dialog).getByLabelText('Quantity for E-500'), {
    target: { value: '3' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));

  expect(
    await screen.findByText(
      /007201 demand updated — business demand only\. 🔥 E-500 left the Hot list \(was #2\) — the line is now fully allocated\./,
    ),
  ).toBeInTheDocument();
  expect(state.workOrders[0].demands[2].priority_rank).toBeNull();
  expect(state.workOrders[1].demands[0].priority_rank).toBe(2);
});

test('a release committed after the view loaded still blocks removal — server 409 with the explanation', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  // The stale-view race: A-100 released elsewhere AFTER this dialog
  // loaded. The UI still offers removal, but the backend rule is the
  // authority — the 409 removes nothing and explains why.
  state.releasedQuantities.set(101, 25);
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Remove line A-100' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove line' }));

  expect(
    await screen.findByText(
      /Cannot remove: production quantity has already been released\./,
    ),
  ).toBeInTheDocument();
  // Nothing was removed.
  expect(within(dialog).getByText('A-100')).toBeInTheDocument();
  expect(state.workOrders[0].demands).toHaveLength(3);
});

test('the last demand line of a Work Order cannot be removed', async () => {
  await renderWorkOrders();
  const row = screen.getByRole('button', { name: 'Open Work Order —' });
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('C-300');

  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Remove line C-300' }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove line' }));

  expect(
    await screen.findByText(/Cannot remove the last demand line/),
  ).toBeInTheDocument();
  expect(state.workOrders[1].demands).toHaveLength(1);
});

test('Save demand PATCHes only the diff, refreshes from the server, and never releases', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  fireEvent.change(within(dialog).getByLabelText('Quantity for A-100'), {
    target: { value: '30' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));

  await screen.findByText(/007201 demand updated — business demand only/);
  // Server state took the edit; the other line traveled no edit.
  const wo = state.workOrders.find((w) => w.id === 1)!;
  expect(wo.demands.find((d) => d.id === 101)!.requested_quantity).toBe(30);
  const patchCalls = state.calls.filter(
    (call) => call === 'PATCH /api/work-orders/1',
  );
  expect(patchCalls).toHaveLength(1);
  // Saving demand never touches the release surface.
  expect(releaseCalls()).toEqual([]);
  // The dialog refreshed from server state and is clean again.
  expect(within(dialog).queryByText('● Unsaved changes')).toBeNull();
});

test('a failed Save demand keeps the draft and shows the server message', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  fireEvent.change(within(dialog).getByLabelText('Quantity for A-100'), {
    target: { value: '30' },
  });
  state.failNextWorkOrderWrite = true;
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));

  await screen.findByText(/The save failed on the server/);
  // The draft is intact, still dirty, and nothing changed server-side.
  expect(within(dialog).getByLabelText('Quantity for A-100')).toHaveValue('30');
  expect(
    within(dialog).getAllByText('● Unsaved changes').length,
  ).toBeGreaterThan(0);
  expect(
    state.workOrders[0].demands.find((d) => d.id === 101)!.requested_quantity,
  ).toBe(25);
});

test('saving an OPEN Work Order with an incomplete row is blocked, not filtered', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:EXTRA-77');
  await within(dialog).findByText('EXTRA-77');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));

  expect(
    await screen.findByText('quantity must be a positive whole number'),
  ).toBeInTheDocument();
  // The incomplete row is still there — never silently dropped — and
  // nothing traveled.
  expect(within(dialog).getByText('EXTRA-77')).toBeInTheDocument();
  expect(state.calls.filter((call) => call.startsWith('PATCH'))).toHaveLength(
    0,
  );
});

/* ============ Release to production (§11.4) ============ */

async function openRelease(pn: string, woNumber = '007201') {
  const dialog = await openWorkOrderDetail(woNumber, pn);
  const row = within(dialog).getByText(pn).closest('tr')!;
  const opener = within(row as HTMLElement).getByRole('button', {
    name: 'Release to production…',
  });
  // Activated from the keyboard: the opener holds focus.
  opener.focus();
  fireEvent.click(opener);
  const release = await screen.findByRole('dialog', {
    name: 'Release to production — explicit action',
  });
  // The release choices load from the real environment surfaces.
  await within(release).findByLabelText('Release quantity');
  return release;
}

test('release FLOATING confirms quantity, Area and Operation, and reports the committed result', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500');

  // The quantity defaults to the COMMITTED requested demand quantity;
  // FLOATING is the default Route Mode and needs no Route.
  expect(within(release).getByLabelText('Release quantity')).toHaveValue('7');
  expect(within(release).getByLabelText('Route Mode')).toHaveValue('FLOATING');

  // The explicit starting Area + Operation confirmation.
  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });
  expect(release).toHaveTextContent(
    /starts in Material \(Operation Receiving\)/,
  );

  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );

  // The committed result: Quantity Flow id, route mode, Area, quantity
  // and the appended RECEIVED Movement.
  const result = await screen.findByRole('dialog', {
    name: 'Release committed',
  });
  expect(result).toHaveTextContent('Quantity Flow');
  expect(result).toHaveTextContent('#500');
  expect(result).toHaveTextContent('× 7 pcs');
  expect(result).toHaveTextContent(/FLOATING — actual route derives/);
  expect(result).toHaveTextContent('Material');
  expect(result).toHaveTextContent('RECEIVED · movement #9000');

  fireEvent.click(within(result).getByRole('button', { name: 'Done' }));
  await screen.findByText(/E-500 released to production × 7/);

  // The fake committed exactly one FLOATING release for the demand.
  expect(state.committedReleases.size).toBe(1);
  const commit = [...state.committedReleases.values()][0];
  expect(commit.body.route_mode).toBe('FLOATING');
  expect(commit.body.route_template_id).toBeNull();
  expect(commit.body.starting_area_id).toBe(1);
  expect(commit.body.operation_id).toBe(11);

  // The released state comes back from the SERVER (the dialog
  // reloaded the demand lines): Released status, release and removal
  // disabled with the explanation. The mixed Work Order stays OPEN.
  const detailDialog = screen.getByRole('dialog', {
    name: 'Work Order Details',
  });
  const row = await waitFor(() => {
    const releasedRow = within(detailDialog)
      .getByText('E-500')
      .closest('tr') as HTMLElement;
    expect(within(releasedRow).getByText('Released')).toBeInTheDocument();
    return releasedRow;
  });
  expect(
    within(row).getByRole('button', { name: 'Release to production…' }),
  ).toBeDisabled();
  // The now-disabled opener cannot hold focus: it stays in the dialog.
  expect(document.activeElement).toBe(detailDialog);
  expect(
    within(row).getByRole('button', { name: 'Remove line E-500' }),
  ).toBeDisabled();
  expect(
    within(detailDialog).getAllByText(
      'Cannot remove: production quantity has already been released.',
    ).length,
  ).toBeGreaterThan(0);
  expect(within(detailDialog).getByText('Open')).toBeInTheDocument();
});

test('the release quantity field states what is available and MAX fills it', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500'); // demand 103 — remaining 7
  const qty = within(release).getByLabelText('Release quantity');

  expect(within(release).getByText('available 7 pcs')).toBeInTheDocument();

  fireEvent.change(qty, { target: { value: '3' } });
  expect(qty).toHaveValue('3');
  fireEvent.click(within(release).getByRole('button', { name: 'MAX' }));
  expect(qty).toHaveValue('7');
  expect(release).toHaveTextContent(/× 7 pcs as a new, separate Quantity Flow/);
});

test('an Area with a single active Operation preselects it', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500');

  // Material has two active Operations — the choice stays open.
  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  expect(within(release).getByLabelText('Operation')).toHaveValue('');
  expect(
    within(release).getByRole('button', { name: 'Confirm release' }),
  ).toBeDisabled();

  // Lathe has exactly one — nothing left to choose, so it is selected.
  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '2' },
  });
  expect(within(release).getByLabelText('Operation')).toHaveValue('21');
  expect(release).toHaveTextContent(/starts in Lathe \(Operation Turning\)/);
  expect(
    within(release).getByRole('button', { name: 'Confirm release' }),
  ).toBeEnabled();

  // Switching back re-opens the choice — the preselection is derived.
  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  expect(within(release).getByLabelText('Operation')).toHaveValue('');
});

test('release PLANNED requires an existing active Planned Route and fixes the starting step', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500');

  fireEvent.change(within(release).getByLabelText('Route Mode'), {
    target: { value: 'PLANNED' },
  });
  fireEvent.change(within(release).getByLabelText('Planned Route'), {
    target: { value: '1' },
  });

  // The Route's first step fixes the starting Area and its Operation —
  // never a silent adjustment.
  expect(release).toHaveTextContent(
    /Material — fixed by the Route's first step/,
  );
  expect(release).toHaveTextContent(
    /Receiving — fixed by the Route's first step/,
  );
  expect(release).toHaveTextContent(/snapshot is taken on commit/);

  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );

  const result = await screen.findByRole('dialog', {
    name: 'Release committed',
  });
  expect(result).toHaveTextContent(/PLANNED — route snapshot/);
  expect(result).toHaveTextContent('#300');

  const commit = [...state.committedReleases.values()][0];
  expect(commit.body.route_mode).toBe('PLANNED');
  expect(commit.body.route_template_id).toBe(1);
  expect(commit.body.starting_area_id).toBe(1);
  expect(commit.body.operation_id).toBe(11);
});

test('existing active quantity demands explicit confirmation before a separate flow is created', async () => {
  await renderWorkOrders();
  const release = await openRelease('A-100');

  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );

  // The backend answered 409 + distribution — nothing was created.
  expect(
    await within(release).findByText(/already has active quantity/),
  ).toBeInTheDocument();
  expect(release).toHaveTextContent(/Lathe × 40 \(flow #71\)/);
  expect(release).toHaveTextContent(/never automatically adds to or merges/);
  expect(state.committedReleases.size).toBe(0);

  // The confirm action stays disabled until the intent is explicit.
  const confirmButton = within(release).getByRole('button', {
    name: 'Confirm release',
  });
  expect(confirmButton).toBeDisabled();
  fireEvent.click(
    within(release).getByRole('checkbox', {
      name: /Release a separate Quantity Flow anyway/,
    }),
  );
  fireEvent.click(confirmButton);

  await screen.findByRole('dialog', { name: 'Release committed' });
  expect(state.committedReleases.size).toBe(1);

  // Both submissions used the SAME device_event_id — the confirmation
  // resubmission continues the submission, it never starts a new one.
  const releaseBodies = state.calls.filter((call) => call.includes('/release'));
  expect(releaseBodies).toHaveLength(2);
  const commit = [...state.committedReleases.values()][0];
  expect(commit.body.confirm_active_quantity).toBe(true);
});

test('a transport retry reuses the device_event_id and can only replay the committed release', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500');

  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });

  // The commit succeeds server-side but the response is lost.
  state.dropNextReleaseResponse = true;
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );

  expect(
    await within(release).findByText(
      /The PartFlow server could not be reached/,
    ),
  ).toBeInTheDocument();
  expect(release).toHaveTextContent(
    /a retry of this submission can never create a second Quantity Flow/i,
  );
  expect(state.committedReleases.size).toBe(1);

  // Retry: the SAME device_event_id replays the original result.
  fireEvent.click(
    within(release).getByRole('button', { name: 'Retry release' }),
  );
  const result = await screen.findByRole('dialog', {
    name: 'Release committed',
  });
  expect(result).toHaveTextContent(/already committed/);
  expect(result).toHaveTextContent('#500');
  // Still exactly ONE committed flow.
  expect(state.committedReleases.size).toBe(1);
  expect(releaseCalls()).toHaveLength(2);
});

test('a demand released in parts keeps offering the remaining quantity', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500'); // demand 103, requested 7

  // The quantity defaults to what is LEFT to release (all of it here).
  expect(within(release).getByLabelText('Release quantity')).toHaveValue('7');
  fireEvent.change(within(release).getByLabelText('Release quantity'), {
    target: { value: '3' },
  });
  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );
  const result = await screen.findByRole('dialog', {
    name: 'Release committed',
  });
  expect(result).toHaveTextContent('× 3 pcs');
  fireEvent.click(within(result).getByRole('button', { name: 'Done' }));

  // The line states what is actually released, the Work Order stays
  // Open, and the release action is still available for the remainder.
  const detailDialog = screen.getByRole('dialog', {
    name: 'Work Order Details',
  });
  const row = await waitFor(() => {
    const partial = within(detailDialog)
      .getByText('E-500')
      .closest('tr') as HTMLElement;
    expect(within(partial).getByText('Released 3/7')).toBeInTheDocument();
    return partial;
  });
  expect(
    within(row).getByRole('button', { name: 'Release to production…' }),
  ).toBeEnabled();
  expect(within(detailDialog).getByText('Open')).toBeInTheDocument();
  // Removal stays blocked — quantity has been released (§13).
  expect(
    within(row).getByRole('button', { name: 'Remove line E-500' }),
  ).toBeDisabled();

  // Reopening offers exactly the remainder and refuses more than that.
  fireEvent.click(
    within(row).getByRole('button', { name: 'Release to production…' }),
  );
  const second = await screen.findByRole('dialog', {
    name: 'Release to production — explicit action',
  });
  await within(second).findByLabelText('Release quantity');
  expect(within(second).getByLabelText('Release quantity')).toHaveValue('4');
  expect(second).toHaveTextContent(/already released 3/);
  expect(second).toHaveTextContent(/remaining 4/);

  fireEvent.change(within(second).getByLabelText('Release quantity'), {
    target: { value: '5' },
  });
  expect(second).toHaveTextContent(/Only 4 pcs remain to release/);
  expect(
    within(second).getByRole('button', { name: 'Confirm release' }),
  ).toBeDisabled();
});

test('a fully released demand line closes its release action', async () => {
  await renderWorkOrders();
  // Demand 102 (B-200) is fully released in the seeded server state.
  const detailDialog = await openWorkOrderDetail('007201', 'B-200');
  const row = within(detailDialog).getByText('B-200').closest('tr')!;

  expect(within(row as HTMLElement).getByText('Released')).toBeInTheDocument();
  expect(
    within(row as HTMLElement).getByRole('button', {
      name: 'Release to production…',
    }),
  ).toBeDisabled();
});

test('a changed release intent gets a new device_event_id; a plain retry keeps it', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500');
  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });

  state.failNextRelease = true;
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );
  await within(release).findByText(/Release rejected by the server\./);

  // An unchanged retry is the SAME submission — same key.
  state.failNextRelease = true;
  fireEvent.click(
    within(release).getByRole('button', { name: 'Retry release' }),
  );
  await waitFor(() => expect(state.releaseAttempts).toHaveLength(2));
  expect(state.releaseAttempts[1].device_event_id).toBe(
    state.releaseAttempts[0].device_event_id,
  );

  // Editing a field the server fingerprint covers is a NEW intent: it
  // gets a fresh key, so the corrected release is never rejected as an
  // idempotency conflict (SLICE1 §14).
  fireEvent.change(within(release).getByLabelText('Release quantity'), {
    target: { value: '4' },
  });
  expect(
    within(release).getByRole('button', { name: 'Confirm release' }),
  ).toBeEnabled();
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );
  await screen.findByRole('dialog', { name: 'Release committed' });
  expect(state.releaseAttempts).toHaveLength(3);
  expect(state.releaseAttempts[2].quantity).toBe(4);
  expect(state.releaseAttempts[2].device_event_id).not.toBe(
    state.releaseAttempts[0].device_event_id,
  );
});

test('cancelling the release creates nothing', async () => {
  await renderWorkOrders();
  const release = await openRelease('C-300', '—');

  fireEvent.click(
    within(release).getByRole('button', { name: 'Cancel (Esc)' }),
  );

  expect(
    await screen.findByText('✕ Release cancelled — nothing was created.'),
  ).toBeInTheDocument();
  expect(releaseCalls()).toEqual([]);
  expect(state.committedReleases.size).toBe(0);
});

/* ============ View states ============ */

test('the list shows a real error state with Retry', async () => {
  state.failNextList = true;
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);

  await screen.findByText('Work Orders could not be loaded.');
  expect(screen.getByText('The database is unavailable.')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('007201')).toBeInTheDocument();
});

test('an empty server list shows the empty state', async () => {
  state.workOrders = [];
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);

  expect(
    await screen.findByText(
      'No Work Orders yet — create the first one with ＋ New Work Order.',
    ),
  ).toBeInTheDocument();
});

test('the development ?state=long preview appends the dense-table fixture', async () => {
  window.history.replaceState({}, '', '/management/work-orders?state=long');
  render(<App />);

  expect(
    await screen.findByText('007099-SUPPLEMENTAL-AMENDMENT-2026-REV-B'),
  ).toBeInTheDocument();
  // The real server rows render too.
  expect(screen.getByText('007201')).toBeInTheDocument();
});

test('offline blocks every write action while reading stays available', async () => {
  state.healthDown = true;
  await renderWorkOrders();

  // Reading works: the list rendered. Open the details dialog.
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  await waitFor(() =>
    expect(
      within(dialog).getByRole('button', { name: 'Save demand' }),
    ).toBeDisabled(),
  );
  expect(
    within(dialog).getByRole('button', { name: '＋ Add Part manually' }),
  ).toBeDisabled();
  expect(within(dialog).getByLabelText('Scan PN barcode')).toBeDisabled();
  const row = within(dialog).getByText('A-100').closest('tr')!;
  expect(
    within(row as HTMLElement).getByRole('button', {
      name: 'Release to production…',
    }),
  ).toBeDisabled();
  // A saved line's removal is a server write — blocked offline too.
  expect(
    within(row as HTMLElement).getByRole('button', {
      name: 'Remove line A-100',
    }),
  ).toBeDisabled();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));

  // The New Work Order dialog blocks its save as well.
  openNewWorkOrderDialog();
  expect(screen.getByRole('button', { name: 'Save demand' })).toBeDisabled();
});

/* ============ Unsaved-change protection ============ */

async function makeDetailDirty() {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  fireEvent.change(within(dialog).getByLabelText('Quantity for A-100'), {
    target: { value: '30' },
  });
  return dialog;
}

test('cancelling the discard confirmation keeps the user on the dirty Work Order', async () => {
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(confirmSpy).toHaveBeenCalledWith(
    'Work Orders has unsaved changes. Discard them and leave this view?',
  );
  expect(window.location.pathname).toBe('/management/work-orders');
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
});

test('confirming the discard allows top-level navigation away', async () => {
  await makeDetailDirty();
  vi.spyOn(window, 'confirm').mockReturnValue(true);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(window.location.pathname).toBe('/scan-station');
});

test('Management sub-navigation is guarded too', async () => {
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'PN Tracking' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/work-orders');
});

test('browser back is guarded while the Work Order detail is dirty', async () => {
  await makeDetailDirty();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.popState(window);

  expect(confirmSpy).toHaveBeenCalled();
});

test('reload and tab close are guarded through beforeunload while dirty', async () => {
  await makeDetailDirty();

  const event = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(event);

  expect(event.defaultPrevented).toBe(true);

  // A clean view never blocks unload.
  fireEvent.keyDown(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
    { key: 'Escape' },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
  const cleanEvent = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(cleanEvent);
  expect(cleanEvent.defaultPrevented).toBe(false);
});

test('a dirty New Work Order dialog also guards top-level navigation', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '007999' },
  });
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));

  expect(confirmSpy).toHaveBeenCalled();
  expect(window.location.pathname).toBe('/management/work-orders');
});

/* ============ Server-derived released state ============ */

test('a demand released in an earlier session loads Released and restricted before any local action', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  // B-200's release evidence comes from the SERVER response — no
  // release ever happened in this browser session.
  const row = within(dialog).getByText('B-200').closest('tr') as HTMLElement;
  expect(within(row).getByText('Released')).toBeInTheDocument();
  expect(
    within(row).getByRole('button', { name: 'Release to production…' }),
  ).toBeDisabled();
  expect(
    within(row).getByRole('button', { name: 'Remove line B-200' }),
  ).toBeDisabled();
  expect(
    within(row).getByText(
      'Cannot remove: production quantity has already been released.',
    ),
  ).toBeInTheDocument();

  // Restricted, not frozen: Qty, due date and Job Numbers stay
  // editable — with no standing note, the field speaks only when the
  // entry is actually wrong…
  expect(within(row).getByLabelText('Quantity for B-200')).toHaveValue('10');
  expect(within(row).getByLabelText('Quantity for B-200')).not.toHaveAttribute(
    'aria-invalid',
  );
  expect(within(row).getByLabelText('Due date for B-200')).toBeInTheDocument();
  expect(
    within(row).getByLabelText('Job Numbers for B-200'),
  ).toBeInTheDocument();
  // …while the PN and the Request Type are fixed.
  expect(within(row).queryByLabelText('Request Type for B-200')).toBeNull();
  expect(within(row).getByText('NEW')).toBeInTheDocument();

  // A duplicate scan of the released PN points at the existing line.
  scanBarcode('PF:PN:B-200');
  expect(
    await screen.findByText(
      /B-200 is already on this Work Order and its production quantity is released — edit its quantity, due date or Job Numbers instead of adding a duplicate line\./,
    ),
  ).toBeInTheDocument();
  // Still exactly one B-200 line — nothing was added.
  expect(within(dialog).getAllByText('B-200')).toHaveLength(1);
});

test('a released line saves its Qty, due date and Job Numbers as a normal audited edit', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  const row = within(dialog).getByText('B-200').closest('tr') as HTMLElement;

  fireEvent.change(within(row).getByLabelText('Quantity for B-200'), {
    target: { value: '18' },
  });
  fireEvent.change(within(row).getByLabelText('Due date for B-200'), {
    target: { value: '2026-10-02' },
  });
  fireEvent.change(within(row).getByLabelText('Job Numbers for B-200'), {
    target: { value: '18112, 18113' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/007201 demand updated — business demand only/);

  // Exactly the restricted fields travelled for the released line.
  const patch = state.calls.filter((call) =>
    call.startsWith('PATCH /api/work-orders/1'),
  );
  expect(patch).toHaveLength(1);
  const saved = state.workOrders.find((w) => w.id === 1)!.demands[1];
  expect(saved.requested_quantity).toBe(18);
  expect(saved.due_date).toBe('2026-10-02');
  expect(saved.job_numbers).toEqual(['18112', '18113']);
  expect(saved.request_type).toBe('NEW');
  // The release evidence is untouched — the remainder simply grew.
  expect(state.releasedQuantities.get(102)).toBe(10);
  await waitFor(() =>
    expect(within(dialog).getByText('Released 10/18')).toBeInTheDocument(),
  );
});

test('a released line marks a Qty below the released quantity while it is typed', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  const row = within(dialog).getByText('B-200').closest('tr') as HTMLElement;
  const qty = within(row).getByLabelText('Quantity for B-200');

  // The error appears on entry — no Save needed, and nothing travels.
  fireEvent.change(qty, { target: { value: '9' } });
  expect(within(row).getByText('≥ 10 pcs released')).toBeInTheDocument();
  expect(qty).toHaveAttribute('aria-invalid', 'true');
  expect(
    state.calls.filter((call) => call.startsWith('PATCH /api/work-orders/1')),
  ).toHaveLength(0);

  // Saving with it stays blocked, and the value is preserved.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  expect(await within(row).findByText('≥ 10 pcs released')).toBeInTheDocument();
  expect(
    state.calls.filter((call) => call.startsWith('PATCH /api/work-orders/1')),
  ).toHaveLength(0);
  expect(
    state.workOrders.find((w) => w.id === 1)!.demands[1].requested_quantity,
  ).toBe(10);

  // Down to exactly the released quantity is valid — and correcting the
  // entry clears the error immediately.
  fireEvent.change(qty, { target: { value: '10' } });
  expect(within(row).queryByText('≥ 10 pcs released')).toBeNull();
  expect(qty).not.toHaveAttribute('aria-invalid');
  fireEvent.change(within(row).getByLabelText('Due date for B-200'), {
    target: { value: '2026-10-03' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/007201 demand updated — business demand only/);
  expect(state.workOrders.find((w) => w.id === 1)!.demands[1].due_date).toBe(
    '2026-10-03',
  );
});

test('the released state survives a full page reload', async () => {
  await renderWorkOrders();
  let dialog = await openWorkOrderDetail('007201', 'A-100');
  let row = within(dialog).getByText('B-200').closest('tr') as HTMLElement;
  expect(within(row).getByText('Released')).toBeInTheDocument();

  // Full reload: a brand-new application instance, same server state.
  cleanup();
  await renderWorkOrders();
  dialog = await openWorkOrderDetail('007201', 'A-100');
  row = within(dialog).getByText('B-200').closest('tr') as HTMLElement;
  expect(within(row).getByText('Released')).toBeInTheDocument();
  expect(
    within(row).getByRole('button', { name: 'Release to production…' }),
  ).toBeDisabled();
});

test('the WO list derives Open for a mixed Work Order and Released for a fully released one', async () => {
  await renderWorkOrders();

  // 007201 holds released AND unreleased demand — canonical OPEN.
  const mixedRow = workOrderRow('007201').closest('tr') as HTMLElement;
  expect(within(mixedRow).getByText('Open')).toBeInTheDocument();
  // 007300's every demand carries release evidence — RELEASED.
  const releasedRow = workOrderRow('007300').closest('tr') as HTMLElement;
  expect(within(releasedRow).getByText('Released')).toBeInTheDocument();
});

test('a fully released Work Order keeps the restricted line edit', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007300', 'D-400');

  expect(dialog).toHaveTextContent('every demand line is fully released');
  expect(within(dialog).getAllByText('Released').length).toBeGreaterThan(0);
  // Qty, due date and Job Numbers stay editable even here…
  expect(within(dialog).getByLabelText('Quantity for D-400')).toHaveValue('8');
  expect(
    within(dialog).getByLabelText('Due date for D-400'),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByLabelText('Job Numbers for D-400'),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole('button', { name: 'Save demand' }),
  ).toBeEnabled();
  // …while the OPEN-only scope stays closed: no new lines, no release
  // action and no removal while nothing remains to release.
  expect(within(dialog).queryByLabelText('Request Type for D-400')).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: 'Release to production…' }),
  ).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: '＋ Add Part manually' }),
  ).toBeNull();
  expect(within(dialog).queryByLabelText('WO due date')).toBeNull();
});

test('raising the Qty of a fully released Work Order makes it Open with quantity to release again', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007300', 'D-400');

  fireEvent.change(within(dialog).getByLabelText('Quantity for D-400'), {
    target: { value: '12' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/007300 demand updated — business demand only/);

  // The Work Order derives back to Open, the line states what is
  // actually released, and the release action returns for the rest.
  await waitFor(() =>
    expect(within(dialog).getByText('Released 8/12')).toBeInTheDocument(),
  );
  expect(within(dialog).getByText('Open')).toBeInTheDocument();
  const row = within(dialog).getByText('D-400').closest('tr') as HTMLElement;
  expect(
    within(row).getByRole('button', { name: 'Release to production…' }),
  ).toBeEnabled();

  // The remainder is what the release dialog offers.
  fireEvent.click(
    within(row).getByRole('button', { name: 'Release to production…' }),
  );
  const release = await screen.findByRole('dialog', {
    name: 'Release to production — explicit action',
  });
  expect(await within(release).findByLabelText('Release quantity')).toHaveValue(
    '4',
  );
});

/* ============ Release vs. unsaved demand edits ============ */

const releaseButtonIn = (dialog: HTMLElement, pn: string) =>
  within(within(dialog).getByText(pn).closest('tr') as HTMLElement).getByRole(
    'button',
    { name: 'Release to production…' },
  );

test('releasing with unsaved changes asks to save first, then uses the committed quantity', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  // Edit E-500's requested quantity — the draft is now dirty.
  fireEvent.change(within(dialog).getByLabelText('Quantity for E-500'), {
    target: { value: '9' },
  });
  // The action stays available; it announces that it settles the draft.
  expect(releaseButtonIn(dialog, 'E-500')).toBeEnabled();
  expect(releaseButtonIn(dialog, 'E-500')).toHaveAttribute(
    'title',
    'Releasing asks to save or discard demand changes first.',
  );
  expect(dialog).toHaveTextContent(
    'Releasing asks to save or discard demand changes first.',
  );

  fireEvent.click(releaseButtonIn(dialog, 'E-500'));
  const choice = await screen.findByRole('dialog', { name: 'Unsaved changes' });
  expect(choice).toHaveTextContent('Releasing always uses the saved demand');
  // Nothing is saved or released by opening the decision.
  expect(
    state.calls.filter((call) => call.startsWith('PATCH /api/work-orders/1')),
  ).toHaveLength(0);

  fireEvent.click(
    within(choice).getByRole('button', { name: 'Save demand, then release…' }),
  );
  await screen.findByText(/007201 demand updated — business demand only/);

  // The release flow opens on exactly what was just committed.
  const release = await screen.findByRole('dialog', {
    name: 'Release to production — explicit action',
  });
  await within(release).findByLabelText('Release quantity');
  expect(within(release).getByLabelText('Release quantity')).toHaveValue('9');
  expect(release).toHaveTextContent(/requested 9/);
  expect(
    state.workOrders.find((w) => w.id === 1)!.demands[2].requested_quantity,
  ).toBe(9);
});

test('the unsaved decision marks Discard as the destructive choice', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  fireEvent.change(within(dialog).getByLabelText('Quantity for E-500'), {
    target: { value: '9' },
  });
  fireEvent.click(releaseButtonIn(dialog, 'E-500'));
  const choice = await screen.findByRole('dialog', { name: 'Unsaved changes' });

  expect(
    within(choice).getByRole('button', {
      name: 'Discard changes, then release…',
    }),
  ).toHaveClass('danger');
  expect(
    within(choice).getByRole('button', { name: 'Save demand, then release…' }),
  ).toHaveClass('primary');
});

test('discarding unsaved changes releases the saved demand and restores the draft', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  fireEvent.change(within(dialog).getByLabelText('Quantity for E-500'), {
    target: { value: '9' },
  });
  fireEvent.click(releaseButtonIn(dialog, 'E-500'));
  const choice = await screen.findByRole('dialog', { name: 'Unsaved changes' });
  fireEvent.click(
    within(choice).getByRole('button', {
      name: 'Discard changes, then release…',
    }),
  );

  // The edit is gone (never saved) and the release uses the saved 7.
  const release = await screen.findByRole('dialog', {
    name: 'Release to production — explicit action',
  });
  expect(await within(release).findByLabelText('Release quantity')).toHaveValue(
    '7',
  );
  expect(
    state.calls.filter((call) => call.startsWith('PATCH /api/work-orders/1')),
  ).toHaveLength(0);
  expect(within(dialog).getByLabelText('Quantity for E-500')).toHaveValue('7');
  expect(within(dialog).queryAllByText('● Unsaved changes')).toHaveLength(0);
});

test('cancelling the unsaved decision keeps the draft and releases nothing', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  fireEvent.change(within(dialog).getByLabelText('Quantity for E-500'), {
    target: { value: '9' },
  });
  fireEvent.click(releaseButtonIn(dialog, 'E-500'));
  const choice = await screen.findByRole('dialog', { name: 'Unsaved changes' });
  fireEvent.click(within(choice).getByRole('button', { name: 'Cancel (Esc)' }));

  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Unsaved changes' }),
    ).toBeNull(),
  );
  expect(
    screen.queryByRole('dialog', {
      name: 'Release to production — explicit action',
    }),
  ).toBeNull();
  expect(within(dialog).getByLabelText('Quantity for E-500')).toHaveValue('9');
  expect(
    state.calls.filter((call) => call.startsWith('PATCH /api/work-orders/1')),
  ).toHaveLength(0);
});

test('an invalid draft cannot be saved from the release decision', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  // A quantity below what B-200 already released is invalid.
  fireEvent.change(within(dialog).getByLabelText('Quantity for B-200'), {
    target: { value: '9' },
  });
  fireEvent.click(releaseButtonIn(dialog, 'E-500'));
  const choice = await screen.findByRole('dialog', { name: 'Unsaved changes' });
  expect(
    within(choice).getByRole('button', { name: 'Save demand, then release…' }),
  ).toBeDisabled();
  expect(choice).toHaveTextContent(
    'The demand cannot be saved yet: the Work Order has invalid demand lines.',
  );
  // Discarding the invalid draft is still a way forward.
  fireEvent.click(
    within(choice).getByRole('button', {
      name: 'Discard changes, then release…',
    }),
  );
  const release = await screen.findByRole('dialog', {
    name: 'Release to production — explicit action',
  });
  expect(await within(release).findByLabelText('Release quantity')).toHaveValue(
    '7',
  );
});

test('undoing an edit clears the unsaved state — it is derived, not sticky', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  const releasedRow = within(dialog)
    .getByText('B-200')
    .closest('tr') as HTMLElement;
  const qty = within(releasedRow).getByLabelText('Quantity for B-200');
  const removeButton = () =>
    within(dialog).getByRole('button', { name: 'Remove line A-100' });

  expect(removeButton()).toBeEnabled();
  expect(releaseButtonIn(dialog, 'E-500')).not.toHaveAttribute('title');

  // A rejected entry makes the draft dirty: saved-line removal is
  // blocked and Release announces that it settles the draft first.
  fireEvent.change(qty, { target: { value: '9' } });
  expect(
    within(releasedRow).getByText('≥ 10 pcs released'),
  ).toBeInTheDocument();
  expect(removeButton()).toBeDisabled();
  expect(releaseButtonIn(dialog, 'E-500')).toHaveAttribute(
    'title',
    'Releasing asks to save or discard demand changes first.',
  );
  expect(
    within(dialog).getAllByText('● Unsaved changes').length,
  ).toBeGreaterThan(0);

  // Typing the committed value back leaves nothing to save, so the
  // error and every dirty gate go — without a save or a reload.
  fireEvent.change(qty, { target: { value: '10' } });
  expect(within(releasedRow).queryByText('≥ 10 pcs released')).toBeNull();
  expect(removeButton()).toBeEnabled();
  expect(releaseButtonIn(dialog, 'E-500')).not.toHaveAttribute('title');
  expect(within(dialog).queryAllByText('● Unsaved changes')).toHaveLength(0);
  expect(
    state.calls.filter((call) => call.startsWith('PATCH /api/work-orders/1')),
  ).toHaveLength(0);

  // The same holds for a draft line added and removed again.
  scanBarcode('PF:PN:TEMP-9');
  await within(dialog).findByText('TEMP-9');
  expect(removeButton()).toBeDisabled();
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Remove line TEMP-9' }),
  );
  await screen.findByText('✕ Draft line removed — it had never been saved.');
  expect(removeButton()).toBeEnabled();
  expect(within(dialog).queryAllByText('● Unsaved changes')).toHaveLength(0);
});

/* ============ Work Order Number verbatim ============ */

test('an entered WO Number travels and resolves verbatim — surrounding whitespace included', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '  WO-77  ' },
  });
  fireEvent.change(screen.getByLabelText(/^WO due date/), {
    target: { value: '2026-09-01' },
  });
  scanBarcode('PF:PN:NEW-PLATE-9');
  const qty = await screen.findByLabelText('Quantity for NEW-PLATE-9');
  fireEvent.change(qty, { target: { value: '3' } });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/saved — business demand only/);

  // The duplicate lookup used the ORIGINAL string, never a trimmed one.
  expect(
    state.calls.filter(
      (call) =>
        call ===
        `GET /api/work-orders?number=${encodeURIComponent('  WO-77  ')}`,
    ),
  ).toHaveLength(1);
  // The stored number is byte-for-byte the entered value.
  expect(
    state.workOrders.some((w) => w.work_order_number === '  WO-77  '),
  ).toBe(true);
  expect(state.workOrders.some((w) => w.work_order_number === 'WO-77')).toBe(
    false,
  );
});

test('a whitespace-only WO Number saves NULL — trimming only DETECTS blank', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();

  fireEvent.change(screen.getByLabelText(/^WO Number/), {
    target: { value: '   ' },
  });
  scanBarcode('PF:PN:NEW-PLATE-9');
  const qty = await screen.findByLabelText('Quantity for NEW-PLATE-9');
  fireEvent.change(qty, { target: { value: '3' } });

  fireEvent.click(screen.getByRole('button', { name: 'Save demand' }));
  // Blank counts as "no number entered": no duplicate lookup runs and
  // the omission confirmation appears.
  const confirm = await screen.findByRole('dialog', {
    name: 'Save demand with missing information?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Confirm and save' }),
  );
  await screen.findByText(/WO — saved — business demand only/);

  expect(state.workOrders[0].work_order_number).toBeNull();
  expect(
    state.calls.some((call) => call.includes('/api/work-orders?number=')),
  ).toBe(false);
});

/* ============ Audited external-number edit (internal WO) ============ */

test('an internal Work Order receives its external number through the audited edit and it persists', async () => {
  await renderWorkOrders();
  const row = screen.getByRole('button', { name: 'Open Work Order —' });
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('C-300');

  // Verbatim: the entered value keeps its surrounding whitespace.
  fireEvent.change(within(dialog).getByLabelText(/External WO Number/), {
    target: { value: ' WO-500 ' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  // The Work Order has no due date — the omission confirmation stays.
  const confirm = await screen.findByRole('dialog', {
    name: 'Save demand with missing information?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Confirm and save' }),
  );
  await screen.findByText(/demand updated — business demand only/);

  expect(state.workOrders[1].work_order_number).toBe(' WO-500 ');
  expect(dialog).toHaveTextContent('WO-500');
  // The entry disappeared — the Work Order is no longer internal.
  expect(within(dialog).queryByLabelText(/External WO Number/)).toBeNull();

  // Full reload: the exact entered number persists on the server.
  cleanup();
  await renderWorkOrders();
  expect(screen.getByText('WO-500')).toBeInTheDocument();
  expect(
    screen.queryByText('internal Work Order — no external number yet'),
  ).toBeNull();
});

test('an external-number conflict keeps the draft and shows the server message', async () => {
  await renderWorkOrders();
  const row = screen.getByRole('button', { name: 'Open Work Order —' });
  row.focus();
  fireEvent.click(row);
  const dialog = await screen.findByRole('dialog', {
    name: 'Work Order Details',
  });
  await within(dialog).findByText('C-300');

  fireEvent.change(within(dialog).getByLabelText(/External WO Number/), {
    target: { value: '007201' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  const confirm = await screen.findByRole('dialog', {
    name: 'Save demand with missing information?',
  });
  fireEvent.click(
    within(confirm).getByRole('button', { name: 'Confirm and save' }),
  );

  // 409: nothing overwritten, the draft and the message stay.
  expect(
    await screen.findByText(/Work Order '007201' already exists/),
  ).toBeInTheDocument();
  expect(within(dialog).getByLabelText(/External WO Number/)).toHaveValue(
    '007201',
  );
  expect(state.workOrders[1].work_order_number).toBeNull();
});

/* ============ Printable PN barcode label ============ */

test('the Add Part intake offers a printable Code 128 label for a new canonical PN', async () => {
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));

  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: '  pl-88  ' },
  });
  fireEvent.click(
    await screen.findByRole('button', { name: '＋ Create new PN “PL-88”' }),
  );
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();

  // The label derives from the canonical PN identity — no master
  // record exists for PL-88 and none is needed.
  fireEvent.click(screen.getByRole('button', { name: 'Barcode label…' }));
  const label = await screen.findByRole('dialog', {
    name: 'Part Number barcode label',
  });
  expect(
    within(label).getByRole('img', { name: 'Barcode PF:PN:PL-88' }),
  ).toBeInTheDocument();
  expect(label.querySelector('.lpn')?.textContent).toBe('PL-88');
  expect(label.querySelector('.lvalue')?.textContent).toBe('PF:PN:PL-88');
  expect(
    within(label).getByRole('button', { name: 'Print Label' }),
  ).toBeInTheDocument();

  // Closing the label returns to the Add Part flow, values intact.
  fireEvent.click(within(label).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(
    screen.getByRole('dialog', { name: /step 2 of 4: Quantity/ }),
  ).toBeInTheDocument();
});

/** Open the shared Edit Part Number dialog from a demand line's PN and
 * wait for its saved details to load. */
async function openPnDialog(pn: string, title: string): Promise<HTMLElement> {
  fireEvent.click(
    screen.getByRole('button', { name: `Edit Part Number ${pn}` }),
  );
  const dialog = screen.getByRole('dialog', { name: 'Edit Part Number' });
  await within(dialog).findByLabelText(/Name \/ Description/);
  expect(dialog).toHaveAccessibleName(title);
  return dialog;
}

/** The `new PN` marker of a demand line, or null. */
function newPnMarker(pn: string): HTMLElement | null {
  const cell = screen
    .getByRole('button', { name: `Edit Part Number ${pn}` })
    .closest('td') as HTMLElement;
  return within(cell).queryByText('new PN');
}

/** The Save demand button's disabled state, or null when not offered. */
function saveDemandState(dialog: HTMLElement): boolean | null {
  const button = within(dialog).queryByRole('button', { name: 'Save demand' });
  return button === null ? null : (button as HTMLButtonElement).disabled;
}

test('a New Work Order demand line opens Edit Part Number through the pencil control', async () => {
  await renderWorkOrders();
  const dialog = openNewWorkOrderDialog();

  scanBarcode('PF:PN:309-127');
  await screen.findByLabelText('Quantity for 309-127');

  // The PN itself is the ONE entry, rendered identically to Work Order
  // Details: the PN followed by the pencil glyph.
  expect(dialog.querySelector('.pn-labellink')).toBeNull();
  const pnControl = within(dialog).getByRole('button', {
    name: 'Edit Part Number 309-127',
  });
  expect(pnControl).toHaveClass('pnb-pnbtn');
  expect(pnControl).toHaveAttribute('title', '309-127');
  expect(pnControl).toHaveTextContent('309-127✎');
  expect(pnControl.querySelector('.pnb-pnicon')).toHaveAttribute(
    'aria-hidden',
    'true',
  );

  const pnDialog = await openPnDialog('309-127', 'Edit Part Number');
  expect(state.calls).toContain('GET /api/part-numbers?number=309-127');
  // The printable label is reachable from inside the dialog.
  fireEvent.click(
    within(pnDialog).getByRole('button', { name: 'Barcode label…' }),
  );
  const label = await screen.findByRole('dialog', {
    name: 'Part Number barcode label',
  });
  expect(
    within(label).getByRole('img', { name: 'Barcode PF:PN:309-127' }),
  ).toBeInTheDocument();
  expect(label.querySelector('.lpn')?.textContent).toBe('309-127');
  expect(label.querySelector('.lvalue')?.textContent).toBe('PF:PN:309-127');

  // Closing returns to the New Work Order draft, untouched.
  fireEvent.click(within(label).getByRole('button', { name: 'Cancel (Esc)' }));
  fireEvent.click(
    within(pnDialog).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  expect(screen.getByRole('dialog', { name: 'New Work Order' })).toBe(dialog);
  expect(screen.getByLabelText('Quantity for 309-127')).toBeInTheDocument();
});

test('a saved Work Order Details line opens Edit Part Number without touching the draft', async () => {
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  // No barcode sentence and no extra entry row in the PN column — the
  // PN itself carries the affordance at the PN typography (no extra
  // row height).
  expect(within(dialog).queryByText(/existing PN · barcode/)).toBeNull();
  expect(dialog.querySelector('.pn-labellink')).toBeNull();
  const pnControl = within(dialog).getByRole('button', {
    name: 'Edit Part Number A-100',
  });
  expect(pnControl).toHaveAttribute('type', 'button');
  expect(pnControl).toHaveClass('pnb-pnbtn');
  expect(pnControl.closest('.pncell')).not.toBeNull();
  expect(within(dialog).queryByText('● Unsaved changes')).toBeNull();
  const saveBefore = saveDemandState(dialog);

  const pnDialog = await openPnDialog('A-100', 'Edit Part Number');
  // Opening it never marks the Work Order draft dirty.
  expect(within(dialog).queryByText('● Unsaved changes')).toBeNull();
  fireEvent.click(
    within(pnDialog).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  expect(
    screen.getByRole('dialog', { name: 'Work Order Details' }),
  ).toBeInTheDocument();
  expect(within(dialog).queryByText('● Unsaved changes')).toBeNull();
  expect(saveDemandState(dialog)).toBe(saveBefore);
  // A saved line never shows the `new PN` marker.
  expect(newPnMarker('A-100')).toBeNull();
});

test('a draft line for a new PN opens New Part Number with the PN fixed; Add Part Number clears the marker', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:NEW-PLATE-9');
  await screen.findByRole('button', { name: 'Edit Part Number NEW-PLATE-9' });
  expect(newPnMarker('NEW-PLATE-9')).not.toBeNull();

  const pnDialog = await openPnDialog('NEW-PLATE-9', 'New Part Number');
  expect(pnDialog.querySelector('.pnm-idhead')?.textContent).toContain(
    'PF:PN:NEW-PLATE-9',
  );
  expect(pnDialog.querySelector('.pnm-dangerzone')).toBeNull();
  // The label derives from the PN alone — no saved details needed.
  fireEvent.click(
    within(pnDialog).getByRole('button', { name: 'Barcode label…' }),
  );
  const label = await screen.findByRole('dialog', {
    name: 'Part Number barcode label',
  });
  expect(label.querySelector('.lvalue')?.textContent).toBe('PF:PN:NEW-PLATE-9');
  fireEvent.click(within(label).getByRole('button', { name: 'Cancel (Esc)' }));

  fireEvent.change(within(pnDialog).getByLabelText(/Name \/ Description/), {
    target: { value: 'PLATE, NEW' },
  });
  fireEvent.click(
    within(pnDialog).getByRole('button', { name: 'Add Part Number' }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'New Part Number' }),
    ).toBeNull(),
  );
  expect(state.calls).toContain('POST /api/part-numbers');
  expect(state.partNumbers).toContain('NEW-PLATE-9');
  expect(newPnMarker('NEW-PLATE-9')).toBeNull();
  // The demand draft itself is untouched: the line is still unsaved.
  expect(screen.getByLabelText('Quantity for NEW-PLATE-9')).toBeInTheDocument();
  expect(
    state.calls.filter((call) => call.startsWith('PATCH /api/work-orders')),
  ).toEqual([]);
});

test('a draft line marked new whose PN gained saved details loses the marker once the pencil observes them', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:LATE-PLATE');
  await screen.findByRole('button', { name: 'Edit Part Number LATE-PLATE' });
  expect(newPnMarker('LATE-PLATE')).not.toBeNull();

  // Saved details appear elsewhere; opening the pencil observes them
  // through its load alone — closing without saving clears the marker.
  state.partNumbers.push('LATE-PLATE');
  const pnDialog = await openPnDialog('LATE-PLATE', 'Edit Part Number');
  fireEvent.click(
    within(pnDialog).getByRole('button', { name: 'Cancel (Esc)' }),
  );
  expect(newPnMarker('LATE-PLATE')).toBeNull();
  expect(
    state.calls.filter((call) => call.startsWith('POST /api/part-numbers')),
  ).toEqual([]);
});

test('a fixed-PN create answered 409 reloads into Edit keeping the entered values', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:RACE-PLATE');
  await screen.findByRole('button', { name: 'Edit Part Number RACE-PLATE' });
  const pnDialog = await openPnDialog('RACE-PLATE', 'New Part Number');
  fireEvent.change(within(pnDialog).getByLabelText(/Name \/ Description/), {
    target: { value: 'PLATE, RACED' },
  });
  // Someone saves details for the same PN meanwhile.
  state.partNumbers.push('RACE-PLATE');
  state.partNumberNames['RACE-PLATE'] = 'PLATE, FIRST';
  fireEvent.click(
    within(pnDialog).getByRole('button', { name: 'Add Part Number' }),
  );

  expect(
    await within(pnDialog).findByText(
      'Part Number “RACE-PLATE” already has saved details.',
    ),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(
      within(pnDialog).getByRole('button', { name: 'Save changes' }),
    ).toBeEnabled(),
  );
  expect(pnDialog).toHaveAccessibleName('Edit Part Number');
  expect(within(pnDialog).getByLabelText(/Name \/ Description/)).toHaveValue(
    'PLATE, RACED',
  );
  expect(within(pnDialog).getByText('● Unsaved changes')).toBeInTheDocument();

  fireEvent.click(
    within(pnDialog).getByRole('button', { name: 'Save changes' }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Edit Part Number' }),
    ).toBeNull(),
  );
  expect(state.calls).toContain('PATCH /api/part-numbers?number=RACE-PLATE');
  expect(state.partNumberNames['RACE-PLATE']).toBe('PLATE, RACED');
  expect(newPnMarker('RACE-PLATE')).toBeNull();
});

test('deleting the details from a draft line shows the new PN marker', async () => {
  await renderWorkOrders();
  await openWorkOrderDetail('007201', 'A-100');

  scanBarcode('PF:PN:309-127');
  await screen.findByRole('button', { name: 'Edit Part Number 309-127' });
  expect(newPnMarker('309-127')).toBeNull();

  const pnDialog = await openPnDialog('309-127', 'Edit Part Number');
  fireEvent.click(
    within(pnDialog).getByRole('button', { name: 'Delete details…' }),
  );
  fireEvent.click(
    within(
      screen.getByRole('dialog', { name: 'Delete Part Number details?' }),
    ).getByRole('button', { name: 'Delete details' }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole('dialog', { name: 'Edit Part Number' }),
    ).toBeNull(),
  );
  expect(state.calls).toContain('DELETE /api/part-numbers?number=309-127');
  expect(newPnMarker('309-127')).not.toBeNull();
});

test('a released line still offers the pencil; offline it opens with the label but writes blocked', async () => {
  await renderWorkOrders();
  // 007201: B-200 is released (server evidence).
  const dialog = await openWorkOrderDetail('007201', 'B-200');
  expect(
    within(dialog).getByRole('button', { name: 'Edit Part Number B-200' }),
  ).toBeInTheDocument();

  state.healthDown = true;
  act(() => {
    window.dispatchEvent(new Event('offline'));
  });
  const pnDialog = await openPnDialog('B-200', 'Edit Part Number');
  await waitFor(() =>
    expect(
      within(pnDialog).getByRole('button', { name: 'Save changes' }),
    ).toBeDisabled(),
  );
  expect(
    within(pnDialog).getByRole('button', { name: 'Delete details…' }),
  ).toBeDisabled();
  fireEvent.click(
    within(pnDialog).getByRole('button', { name: 'Barcode label…' }),
  );
  expect(
    await screen.findByRole('dialog', { name: 'Part Number barcode label' }),
  ).toBeInTheDocument();
});

test('Add Part results show the saved name, or the barcode when none is saved', async () => {
  state.partNumberNames['309-127'] = 'PLATE, MOUNTING 309';
  await renderWorkOrders();
  openNewWorkOrderDialog();
  fireEvent.click(screen.getByRole('button', { name: '＋ Add Part manually' }));

  // The server matches the saved name too; the typed term travels as is.
  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: 'mounting' },
  });
  const named = await screen.findByRole('button', { name: /309-127/ });
  expect(state.calls).toContain('GET /api/part-numbers?search=mounting');
  const name = named.querySelector('.ap-name') as HTMLElement;
  expect(name.textContent).toBe('PLATE, MOUNTING 309');
  expect(name).not.toHaveClass('mono');

  fireEvent.change(screen.getByLabelText('Search PartNumber'), {
    target: { value: 'A-1' },
  });
  const unnamed = await screen.findByRole('button', { name: /A-100/ });
  const barcode = unnamed.querySelector('.ap-name') as HTMLElement;
  expect(barcode.textContent).toBe('PF:PN:A-100');
  expect(barcode).toHaveClass('mono');

  // The picked-PN header still shows the barcode, not the name.
  fireEvent.click(unnamed);
  expect(
    document.querySelector('.ap-pnhead .ap-pninfo')?.textContent,
  ).toContain('PF:PN:A-100');
});

/* ============ Saved-line removal vs. unsaved demand edits ============ */

test('unsaved demand changes disable saved-line removal; after Save the removals commit and the WO derives RELEASED', async () => {
  await renderWorkOrders();
  // 007201: B-200 released (server evidence), A-100 and E-500 saved
  // and unreleased.
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  const removeButtonOf = (pn: string) =>
    within(dialog).getByRole('button', { name: `Remove line ${pn}` });

  // Make the draft dirty with a header edit (the WO due date).
  fireEvent.change(within(dialog).getByLabelText('WO due date'), {
    target: { value: '2026-09-20' },
  });

  // Saved-line removal is a committed server action — blocked while
  // unsaved edits are in flight, with the stated explanation.
  expect(removeButtonOf('A-100')).toBeDisabled();
  expect(removeButtonOf('A-100')).toHaveAttribute(
    'title',
    'Save or discard demand changes before removing a saved line.',
  );
  expect(dialog).toHaveTextContent(
    'Save or discard demand changes before removing a saved line.',
  );
  // An unsaved draft line stays removable locally — it IS the draft.
  scanBarcode('PF:PN:TEMP-1');
  await within(dialog).findByText('TEMP-1');
  fireEvent.click(removeButtonOf('TEMP-1'));
  expect(
    await screen.findByText('✕ Draft line removed — it had never been saved.'),
  ).toBeInTheDocument();
  expect(within(dialog).queryByText('TEMP-1')).toBeNull();
  expect(removeButtonOf('A-100')).toBeDisabled();

  // Save the draft — nothing was auto-saved or auto-discarded; the
  // saved-line removal re-enables.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/007201 demand updated — business demand only/);
  expect(state.workOrders[0].due_date).toBe('2026-09-20');
  const enabledRemove = await waitFor(() => {
    const button = removeButtonOf('A-100');
    expect(button).not.toBeDisabled();
    return button;
  });

  // Confirmed removal of both unreleased lines: afterwards every
  // remaining persisted demand carries release evidence, so the Work
  // Order derives RELEASED — with no unsaved draft stranded or lost.
  fireEvent.click(enabledRemove);
  fireEvent.click(screen.getByRole('button', { name: 'Remove line' }));
  await screen.findByText('✕ A-100 removed from 007201.');
  fireEvent.click(removeButtonOf('E-500'));
  fireEvent.click(screen.getByRole('button', { name: 'Remove line' }));
  await screen.findByText('✕ E-500 removed from 007201.');

  expect(state.workOrders[0].demands.map((d) => d.id)).toEqual([102]);
  // The reloaded server detail derives RELEASED: the OPEN-only scope
  // closes (no Add Part, no release, no removal) while the remaining
  // released line keeps its restricted edit — and no unsaved marker is
  // stranded.
  await waitFor(() =>
    expect(dialog).toHaveTextContent('every demand line is fully released'),
  );
  expect(within(dialog).queryByText('● Unsaved changes')).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: '＋ Add Part manually' }),
  ).toBeNull();
  expect(within(dialog).getByLabelText('Quantity for B-200')).toHaveValue('10');

  // The list derives RELEASED for the Work Order too.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  const listRow = workOrderRow('007201').closest('tr') as HTMLElement;
  await waitFor(() =>
    expect(within(listRow).getByText('Released')).toBeInTheDocument(),
  );
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

test('WO-1: a failing policy replaces only the table; the toolbar stays usable and Retry recovers', async () => {
  // Due in 6 days with a 40-day lead: 15 % → a 6-day window (soon).
  state.workOrders.push({
    id: 4,
    work_order_number: '007400',
    received_date: isoDateIn(-34),
    due_date: isoDateIn(6),
    status: 'OPEN',
    demands: [demand(401, 4, 'A-100', 2, { due_date: isoDateIn(6) })],
  });
  state.failPolicy = true;
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);
  await screen.findByRole('heading', { name: 'Work Orders' });
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(
    'The Due Soon warning settings could not be loaded.',
  );
  expect(alert).toHaveTextContent('The policy store is unavailable.');
  expect(document.querySelector('table.wolist')).toBeNull();
  expect(screen.getByLabelText('Search WO Number')).toBeEnabled();
  expect(
    screen.getByRole('link', { name: 'Completed Work Orders ›' }),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: '＋ New Work Order' }),
    ).toBeEnabled(),
  );
  const dialog = openNewWorkOrderDialog();
  expect(dialog).toBeInTheDocument();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  await waitFor(() =>
    expect(screen.queryByRole('dialog', { name: 'New Work Order' })).toBeNull(),
  );

  state.failPolicy = false;
  fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('007400')).toBeInTheDocument();
  const tone = (number: string) =>
    screen.getByText(number).closest('tr')?.querySelector('.duetxt')
      ?.className ?? '';
  expect(tone('007400')).toContain('soon');
});

test('WO-1: due tones follow the served policy, and its first read gates only the table', async () => {
  state.workOrders.push({
    id: 4,
    work_order_number: '007400',
    received_date: isoDateIn(-34),
    due_date: isoDateIn(6),
    status: 'OPEN',
    demands: [demand(401, 4, 'A-100', 2, { due_date: isoDateIn(6) })],
  });
  state.policy = policyWire(2, 15, 5);
  let releasePolicy: () => void = () => {};
  state.holdPolicy = new Promise<void>((resolve) => {
    releasePolicy = resolve;
  });
  window.history.replaceState({}, '', '/management/work-orders');
  render(<App />);
  await screen.findByRole('heading', { name: 'Work Orders' });
  expect(
    await screen.findByRole('status', {
      name: 'Loading Due Soon warning settings',
    }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText('Search WO Number')).toBeInTheDocument();
  expect(document.querySelector('table.wolist')).toBeNull();

  await act(async () => {
    releasePolicy();
  });
  expect(await screen.findByText('007400')).toBeInTheDocument();
  const row = screen.getByText('007400').closest('tr');
  const className = row?.querySelector('.duetxt')?.className ?? '';
  expect(className).toContain('ok');
  expect(className).not.toContain('soon');
});

/* ============ Phase 14 slice 3 — Work Order permissions ============ */

function patchBodies(): Record<string, unknown>[] {
  return vi
    .mocked(fetch)
    .mock.calls.filter(([, init]) => init?.method === 'PATCH')
    .map(
      ([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>,
    );
}

test('FM-4: with Edit Work Order Demand only, the header reads as text and no release is offered; the lines stay editable', async () => {
  sessionPermissions = ['EDIT_WORK_ORDER_DEMAND', 'MANAGE_PART_NUMBER_MASTER'];
  await renderWorkOrders();
  expect(
    screen.queryByRole('button', { name: '＋ New Work Order' }),
  ).toBeNull();
  expect(screen.queryByText(/^View only — /)).toBeNull();

  const dialog = await openWorkOrderDetail('007201', 'A-100');
  expect(within(dialog).queryByLabelText(/^WO due date/)).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: 'Release to production…' }),
  ).toBeNull();
  expect(within(dialog).getByLabelText('Quantity for A-100')).toBeEnabled();
  expect(
    within(dialog).getByRole('button', { name: '＋ Add Part manually' }),
  ).toBeInTheDocument();
  expect(within(dialog).getByLabelText('Scan PN barcode')).toBeInTheDocument();
  expect(
    within(dialog).getByRole('button', { name: 'Remove line A-100' }),
  ).toBeInTheDocument();

  // The internal Work Order offers no external-number entry either.
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  const internal = await openWorkOrderDetail('—', 'C-300');
  expect(within(internal).queryByLabelText(/^External WO Number/)).toBeNull();
});

test('FM-4: with Edit Work Order Demand only, an unchanged Save demand sends nothing — never a request the server refuses', async () => {
  sessionPermissions = ['EDIT_WORK_ORDER_DEMAND', 'MANAGE_PART_NUMBER_MASTER'];
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  const save = within(dialog).getByRole('button', { name: 'Save demand' });

  fireEvent.click(save);
  expect(
    await screen.findByText('Nothing to save — the Work Order is unchanged.'),
  ).toBeInTheDocument();
  expect(patchBodies()).toEqual([]);

  // An edit typed back to the saved value leaves nothing to save too.
  const qty = within(dialog).getByLabelText('Quantity for A-100');
  const saved = (qty as HTMLInputElement).value;
  fireEvent.change(qty, { target: { value: '999' } });
  fireEvent.change(qty, { target: { value: saved } });
  fireEvent.click(save);
  expect(patchBodies()).toEqual([]);
  expect(within(dialog).queryByText(/do not have permission/)).toBeNull();
});

test('FM-4: with Create and edit Work Orders only, the lines read as text — no draft line can be created — and a save sends only the header', async () => {
  sessionPermissions = ['MANAGE_WORK_ORDERS', 'MANAGE_PART_NUMBER_MASTER'];
  await renderWorkOrders();
  expect(
    screen.getByRole('button', { name: '＋ New Work Order' }),
  ).toBeInTheDocument();

  const dialog = await openWorkOrderDetail('007201', 'A-100');
  expect(within(dialog).queryByLabelText('Quantity for A-100')).toBeNull();
  expect(within(dialog).queryByLabelText('Job Numbers for A-100')).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: '＋ Add Part manually' }),
  ).toBeNull();
  expect(within(dialog).queryByLabelText('Scan PN barcode')).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: 'Remove line A-100' }),
  ).toBeNull();
  expect(
    within(dialog).getAllByRole('button', { name: 'Release to production…' }),
  ).toHaveLength(3);

  // A new WO due date travels alone — the lines are not the user's to
  // change, so they do not follow it, and the dialog says so.
  expect(
    within(dialog).queryByText(
      'Line due dates stay unchanged — you may not edit demand lines.',
    ),
  ).toBeNull();
  fireEvent.change(within(dialog).getByLabelText(/^WO due date/), {
    target: { value: '2026-09-20' },
  });
  expect(
    within(dialog).getByText(
      'Line due dates stay unchanged — you may not edit demand lines.',
    ),
  ).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/007201 demand updated — business demand only/);
  expect(patchBodies()).toEqual([
    { due_date: '2026-09-20', line_edits: [], new_lines: [] },
  ]);
});

test('FM-4: a user who may change no Work Order only reads — no Save demand, the view-only note names the three permissions', async () => {
  sessionPermissions = ['VIEW_PRODUCTION_DATA'];
  await renderWorkOrders();

  expect(
    screen.getByText(
      'View only — changing this needs one of these permissions: Create and edit Work Orders, Edit Work Order Demand, Edit Work Order Allocation.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: '＋ New Work Order' }),
  ).toBeNull();
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  expect(
    within(dialog).queryByRole('button', { name: 'Save demand' }),
  ).toBeNull();
  expect(dialog.querySelector('input')).toBeNull();
  expect(within(dialog).queryByText(/Saving stores/)).toBeNull();
});

test('FM-4: without Manage Part Numbers the PN of a demand line opens the read-only Part Number details with its barcode label', async () => {
  sessionPermissions = ['VIEW_PRODUCTION_DATA', 'EDIT_WORK_ORDER_DEMAND'];
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');

  expect(
    within(dialog).queryByRole('button', { name: 'Edit Part Number A-100' }),
  ).toBeNull();
  const pn = within(dialog).getByRole('button', {
    name: 'Part Number A-100 details',
  });
  expect(pn.textContent).toBe('A-100');
  fireEvent.click(pn);
  const details = screen.getByRole('dialog', { name: 'Part Number details' });
  expect(
    within(details).getByRole('button', { name: 'Barcode label…' }),
  ).toBeInTheDocument();
  expect(
    within(details).queryByRole('button', { name: /Save|Add Part Number/ }),
  ).toBeNull();
  expect(
    await within(details).findByRole('button', { name: 'Close (Esc)' }),
  ).toBeInTheDocument();
});

/* ============ Phase 14 slice 3 — release retry copy ============ */

test('FM-6: a release refused because the sign-in ended keeps the dialog and its key; submitting again after signing in records it once', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500');
  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });
  const A1 =
    'You are not signed in, or your sign-in has ended. Sign in to continue.';
  nextReleaseRefusal = {
    status: 401,
    body: { detail: A1, authentication_required: true },
  };
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );
  expect(
    await within(release).findByText(
      `${A1} Sign in again, then submit again. PartFlow records this release only once.`,
    ),
  ).toBeInTheDocument();

  // The sign-in provider asks for the sign-in; the same user signs in.
  const signIn = await screen.findByRole('dialog', { name: 'Sign in' });
  fireEvent.change(within(signIn).getByLabelText('Login name'), {
    target: { value: 'mia' },
  });
  fireEvent.change(within(signIn).getByLabelText('Password'), {
    target: { value: 'secret-password' },
  });
  fireEvent.click(within(signIn).getByRole('button', { name: 'Sign in' }));
  await waitFor(() =>
    expect(screen.queryByRole('dialog', { name: 'Sign in' })).toBeNull(),
  );

  // The release dialog and its inputs are still there; the same key goes.
  fireEvent.click(
    within(
      screen.getByRole('dialog', {
        name: 'Release to production — explicit action',
      }),
    ).getByRole('button', { name: 'Retry release' }),
  );
  await screen.findByRole('dialog', { name: 'Release committed' });
  expect(state.releaseAttempts).toHaveLength(2);
  expect(state.releaseAttempts[1].device_event_id).toBe(
    state.releaseAttempts[0].device_event_id,
  );
  expect(state.committedReleases.size).toBe(1);
});

test('FM-6: a release recorded by another user shows the server detail and keeps the key', async () => {
  await renderWorkOrders();
  const release = await openRelease('E-500');
  fireEvent.change(within(release).getByLabelText('Starting Area'), {
    target: { value: '1' },
  });
  fireEvent.change(within(release).getByLabelText('Operation'), {
    target: { value: '11' },
  });
  const R1 =
    'This request was already recorded by another user. Nothing more was recorded — reload to see the current state.';
  nextReleaseRefusal = {
    status: 409,
    body: { detail: R1, recorded_by_another_user: true },
  };
  fireEvent.click(
    within(release).getByRole('button', { name: 'Confirm release' }),
  );
  expect(await within(release).findByText(R1)).toBeInTheDocument();
  expect(release).not.toHaveTextContent('You can retry');
  nextReleaseRefusal = {
    status: 409,
    body: { detail: R1, recorded_by_another_user: true },
  };
  fireEvent.click(
    within(release).getByRole('button', { name: 'Retry release' }),
  );
  await waitFor(() => expect(state.releaseAttempts).toHaveLength(2));
  expect(state.releaseAttempts[1].device_event_id).toBe(
    state.releaseAttempts[0].device_event_id,
  );
  expect(state.committedReleases.size).toBe(0);
});

/* ============ Phase 14 slice 5 — allocation in Work Order Details ============ */

const WITHOUT_ALLOCATION = PERMISSIONS.filter(
  (key) => key !== 'EDIT_WORK_ORDER_ALLOCATION',
);

function lineRow(dialog: HTMLElement, pn: string): HTMLElement {
  return within(dialog).getByText(pn).closest('tr') as HTMLElement;
}

test('FC-7: the allocation actions are offered only to Edit Work Order Allocation holders', async () => {
  state.workOrders[0].demands[0].allocated_quantity = 4;
  sessionPermissions = WITHOUT_ALLOCATION;
  await renderWorkOrders();
  let dialog = await openWorkOrderDetail('007201', 'A-100');
  expect(
    within(dialog).queryByRole('button', { name: /^Allocate stocked/ }),
  ).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: /^Reverse an allocation/ }),
  ).toBeNull();
  cleanup();

  sessionPermissions = PERMISSIONS;
  await renderWorkOrders();
  dialog = await openWorkOrderDetail('007201', 'A-100');
  const a100 = lineRow(dialog, 'A-100');
  expect(
    within(a100).getByRole('button', {
      name: 'Allocate stocked A-100 to this line',
    }),
  ).toHaveTextContent('Allocate from stock…');
  expect(
    within(a100).getByRole('button', {
      name: 'Reverse an allocation of A-100 on this line',
    }),
  ).toHaveTextContent('Reverse…');
  // Reverse… only where something is allocated.
  const e500 = lineRow(dialog, 'E-500');
  expect(
    within(e500).getByRole('button', {
      name: 'Allocate stocked E-500 to this line',
    }),
  ).toBeInTheDocument();
  expect(
    within(e500).queryByRole('button', { name: /^Reverse an allocation/ }),
  ).toBeNull();
});

test('FC-7: a Released Work Order shows the actions column only to allocation holders; an allocation-only user reads no View only note', async () => {
  sessionPermissions = ['VIEW_PRODUCTION_DATA', 'EDIT_WORK_ORDER_ALLOCATION'];
  await renderWorkOrders();
  expect(screen.queryByText(/^View only — /)).toBeNull();
  let dialog = await openWorkOrderDetail('007300', 'D-400');
  expect(dialog.querySelectorAll('.wo-table thead th')).toHaveLength(7);
  const d400 = lineRow(dialog, 'D-400');
  expect(
    within(d400).getByRole('button', {
      name: 'Allocate stocked D-400 to this line',
    }),
  ).toBeEnabled();
  expect(
    within(d400).queryByRole('button', { name: 'Release to production…' }),
  ).toBeNull();
  expect(
    within(d400).queryByRole('button', { name: 'Remove line D-400' }),
  ).toBeNull();
  cleanup();

  sessionPermissions = ['VIEW_PRODUCTION_DATA'];
  await renderWorkOrders();
  dialog = await openWorkOrderDetail('007300', 'D-400');
  expect(dialog.querySelectorAll('.wo-table thead th')).toHaveLength(6);
  expect(
    within(dialog).queryByRole('button', { name: /^Allocate stocked/ }),
  ).toBeNull();
});

test('FC-7: allocation waits for the saved demand and for the connection', async () => {
  state.workOrders[0].demands[0].allocated_quantity = 4;
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  const allocate = within(dialog).getByRole('button', {
    name: 'Allocate stocked A-100 to this line',
  });
  const reverse = within(dialog).getByRole('button', {
    name: 'Reverse an allocation of A-100 on this line',
  });
  await waitFor(() => expect(allocate).toBeEnabled());

  const qty = within(dialog).getByLabelText('Quantity for A-100');
  fireEvent.change(qty, { target: { value: '30' } });
  for (const button of [allocate, reverse]) {
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute(
      'title',
      'Save or discard the demand changes first — allocation works on the saved demand.',
    );
  }
  fireEvent.change(qty, { target: { value: '25' } });
  expect(allocate).toBeEnabled();

  state.healthDown = true;
  act(() => {
    window.dispatchEvent(new Event('offline'));
  });
  await waitFor(() => expect(allocate).toBeDisabled());
  expect(reverse).toBeDisabled();
});

test('FC-7: a beyond-demand correction from Work Order Details reloads the details once and the list, and names the result', async () => {
  // B-200 is fully allocated (10 of 10) and 2 pcs wait in stock.
  state.workOrders[0].demands[1].allocated_quantity = 10;
  allocationRows = [
    {
      id: 400,
      demandId: 102,
      quantity: 10,
      exceedsDemand: false,
      reason: null,
    },
  ];
  availableStock = { 'B-200': 2 };
  await renderWorkOrders();
  const details = await openWorkOrderDetail('007201', 'B-200');
  expect(lineRow(details, 'B-200')).toHaveTextContent('Allocated 10/10');
  expect(lineRow(details, 'B-200')).not.toHaveTextContent('beyond demand');

  const opener = within(details).getByRole('button', {
    name: 'Allocate stocked B-200 to this line',
  });
  opener.focus();
  fireEvent.click(opener);
  const allocation = await screen.findByRole('dialog', {
    name: 'Allocate from stock',
  });
  await within(allocation).findByText(
    'This demand line is fully allocated. Allocating more is a correction — use Allocate beyond demand….',
  );
  fireEvent.click(
    within(allocation).getByRole('button', {
      name: 'Allocate beyond demand…',
    }),
  );
  fireEvent.change(within(allocation).getByLabelText(/^Quantity to allocate/), {
    target: { value: '2' },
  });
  fireEvent.change(within(allocation).getByLabelText(/^Reason/), {
    target: { value: 'customer accepted overage' },
  });
  const detailReads = () =>
    state.calls.filter((call) => call === 'GET /api/work-orders/1').length;
  const listReads = () =>
    state.calls.filter((call) => call === 'GET /api/work-orders').length;
  const [detailBefore, listBefore] = [detailReads(), listReads()];
  fireEvent.click(
    within(allocation).getByRole('button', { name: 'Record correction' }),
  );

  expect(
    await screen.findByText(
      '✓ 2 pcs of B-200 allocated beyond demand to Work Order 007201 — correction recorded.',
    ),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('dialog', { name: /Allocate beyond demand/ }),
  ).toBeNull();
  await waitFor(() =>
    expect(lineRow(details, 'B-200')).toHaveTextContent(
      'Allocated 12/10 · +2 beyond demand',
    ),
  );
  // The reloaded row keeps its identity: focus returns to the opener.
  expect(document.activeElement).toBe(
    within(details).getByRole('button', {
      name: 'Allocate stocked B-200 to this line',
    }),
  );
  expect(detailReads()).toBe(detailBefore + 1);
  expect(listReads()).toBe(listBefore + 1);
  expect(allocationPosts).toHaveLength(1);
  expect(allocationPosts[0].body).toMatchObject({
    part_number: 'B-200',
    work_order_demand_id: 102,
    quantity: 2,
    reason: 'customer accepted overage',
  });
});

test('FC-8: an over-allocated line saves its other fields — its unchanged Qty is never judged', async () => {
  // A-100: 25 requested, 27 allocated by an authorized correction.
  state.workOrders[0].demands[0].allocated_quantity = 27;
  await renderWorkOrders();
  const dialog = await openWorkOrderDetail('007201', 'A-100');
  expect(lineRow(dialog, 'A-100')).toHaveTextContent(
    'Allocated 27/25 · +2 beyond demand',
  );
  fireEvent.change(within(dialog).getByLabelText('Job Numbers for A-100'), {
    target: { value: '18112, 18113' },
  });
  // A changed Qty below the allocated quantity still errors as typed.
  const qty = within(dialog).getByLabelText('Quantity for A-100');
  fireEvent.change(qty, { target: { value: '26' } });
  expect(within(dialog).getByText('≥ 27 pcs allocated')).toBeInTheDocument();
  fireEvent.change(qty, { target: { value: '25' } });
  expect(within(dialog).queryByText('≥ 27 pcs allocated')).toBeNull();

  fireEvent.click(within(dialog).getByRole('button', { name: 'Save demand' }));
  await screen.findByText(/007201 demand updated — business demand only/);
  expect(patchBodies()).toEqual([
    {
      line_edits: [{ id: 101, job_numbers: ['18112', '18113'] }],
      new_lines: [],
    },
  ]);
});
