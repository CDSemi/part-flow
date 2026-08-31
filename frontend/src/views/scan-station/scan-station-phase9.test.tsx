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

// Real Scan Station (Phase 9 — Undo, corrections and auditable quantity
// events) against a fake in-memory `/api` with the backend's Phase 9
// semantics: the transfer carries the explicit Repair intent (`repair`
// + mandatory `repair_reason`, refused for a destination the quantity
// never visited; `repair_available` marks the eligible candidates),
// `POST …/scraps` records ONE auditable SCRAPPED operation per
// confirmation (partial via the server's split), `POST
// …/quantity-additions` introduces a NEW flow as `QUANTITY_ADJUSTED ·
// INCREASE` (mandatory reason, no MAX, no default), and the
// command-level Undo reads `GET …/undo-preview/{id}` for the summary
// confirmation and reverses the complete command through `POST
// …/undos` under the Undo's own device_event_id. Covers: the Undo
// summary/final-question/confirmation with history preserved and the
// Last Scanned PN advancing, the ineligible preview, Repair full and
// partial with the plain transfer kept available, Scrap counting
// (PF:SCRAP only inside the workflow, −1/reset, one write for the
// total, cancel writes nothing), the quantity addition, offline
// write-blocking, the lost-response retry under the same
// device_event_id, the server refusal with the Area re-read, the
// post-write refresh and focus restoration.

type State = 'QUEUED' | 'PROCESSING' | 'ON_MACHINE' | 'READY_TO_TRANSFER';

interface Flow {
  id: number;
  pn: string;
  qty: number;
  areaId: number;
  state: State;
  machineId: number | null;
  /** Areas this quantity previously visited (repair eligibility). */
  visited?: number[];
}

interface CommandRecord {
  kind: string;
  pn: string;
  qty: number;
  movements: {
    movement_id: number;
    movement_type: string;
    movement_reason: string | null;
    quantity: number;
    from_area: unknown;
    to_area: unknown;
    machine_id: number | null;
    operation_id: number;
  }[];
  restored: {
    quantity_flow_id: number;
    quantity: number;
    status: 'ACTIVE' | 'REVERSED';
    area: unknown;
    machine_id: number | null;
    processing_state: State | null;
  }[];
  /** The complete flow state BEFORE the command — undo restores it. */
  snapshot: Flow[];
  reversed: boolean;
}

const AREAS = [
  { id: 2, name: 'Lathe', color: '#3366ff' },
  { id: 6, name: 'Plating', color: '#aa33aa' },
];
const OPERATIONS = [
  { id: 20, area_id: 2, code: 'TURNING', name: 'Turning', is_external: false },
  { id: 60, area_id: 6, code: 'PLATE', name: 'Plating', is_external: false },
];
const STATIONS = [
  { station_id: 'LATHE-ST-01', area_id: 2 },
  { station_id: 'PLATING-ST-01', area_id: 6 },
];
const MACHINES = [
  { id: 1, name: 'Lathe 1', tag: 'CD-0001', areaId: 2 },
  { id: 2, name: 'Lathe 2', tag: 'CD-0002', areaId: 2 },
];

let flows: Flow[];
let committed: Map<string, unknown>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let requests: { url: string; method: string; body: any }[];
let nextMovementId: number;
let nextFlowId: number;
let writeFailure:
  null | 'network' | 'lost-response' | { status: number; body: unknown };
// The committed commands of this fake session, oldest first: only the
// newest is Undo-eligible (the backend's "most recent operation" rule).
let commandLog: string[];
let records: Map<string, CommandRecord>;
// Test seam: force the preview verdict regardless of the log.
let forceIneligibleReason: string | null;

function areaRef(areaId: number) {
  const area = AREAS.find((a) => a.id === areaId)!;
  return {
    id: area.id,
    name: area.name,
    color: area.color,
    description: null,
    is_terminal: false,
  };
}

function operationsOf(areaId: number) {
  return OPERATIONS.filter((o) => o.area_id === areaId);
}

function machineRef(machine: (typeof MACHINES)[number]) {
  const assigned = flows
    .filter((f) => f.state === 'ON_MACHINE' && f.machineId === machine.id)
    .reduce((s, f) => s + f.qty, 0);
  return {
    id: machine.id,
    name: machine.name,
    asset_tag: machine.tag,
    barcode_value: `PF:MACHINE:${machine.tag}`,
    operational_state: assigned > 0 ? 'RUNNING' : 'IDLE',
    state_changed_at: '2026-08-31T10:00:00Z',
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
    operation: {
      ...OPERATIONS.find((o) => o.area_id === flow.areaId)!,
      is_active: true,
    },
    processing_state: flow.state,
    machine_id: flow.machineId,
    available_actions: actionsOf(flow.state),
    work_order: {
      work_order_id: 1,
      work_order_number: '007003',
      work_order_demand_id: 11,
      request_type: 'NEW',
    },
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

function stationOf(url: string): { station_id: string; area_id: number } {
  const id = decodeURIComponent(/\/scan-stations\/([^/]+)\//.exec(url)![1]);
  return STATIONS.find((s) => s.station_id === id)!;
}

function hasMachines(areaId: number) {
  return MACHINES.some((m) => m.areaId === areaId);
}

function inventory(areaId: number) {
  const here = flows.filter((f) => f.areaId === areaId);
  const byState = (state: State) => here.filter((f) => f.state === state);
  const sum = (items: Flow[]) => items.reduce((s, f) => s + f.qty, 0);
  const all = lines(here);
  return json({
    area: areaRef(areaId),
    has_machines: hasMachines(areaId),
    lines: all,
    total_part_numbers: all.length,
    total_quantity: sum(here),
    queued: lines(byState('QUEUED')),
    queued_quantity: sum(byState('QUEUED')),
    machines: MACHINES.filter((m) => m.areaId === areaId).map((m) => {
      const held = here.filter(
        (f) => f.state === 'ON_MACHINE' && f.machineId === m.id,
      );
      return {
        machine: machineRef(m),
        lines: lines(held),
        total_quantity: sum(held),
      };
    }),
    on_machine_quantity: sum(byState('ON_MACHINE')),
    processing: lines(byState('PROCESSING')),
    processing_quantity: sum(byState('PROCESSING')),
    finished: lines(byState('READY_TO_TRANSFER')),
    finished_quantity: sum(byState('READY_TO_TRANSFER')),
  });
}

function failWrite(): Response | null {
  if (writeFailure === 'network') {
    writeFailure = null;
    throw new TypeError('Failed to fetch');
  }
  if (writeFailure && typeof writeFailure === 'object') {
    const failure = writeFailure;
    writeFailure = null;
    return json(failure.body, failure.status);
  }
  return null;
}

function commit(deviceEventId: string, result: unknown): Response {
  committed.set(deviceEventId, result);
  if (writeFailure === 'lost-response') {
    writeFailure = null;
    throw new TypeError('Failed to fetch');
  }
  return json(result, 201);
}

function snapshotFlows(): Flow[] {
  return flows.map((f) => ({ ...f, visited: f.visited && [...f.visited] }));
}

function recordCommand(
  deviceEventId: string,
  record: Omit<CommandRecord, 'reversed'>,
) {
  records.set(deviceEventId, { ...record, reversed: false });
  commandLog.push(deviceEventId);
}

function splitIfPartial(
  flow: Flow,
  quantity: number,
): { acted: Flow; split: Record<string, number | null> } {
  if (quantity === flow.qty) {
    return {
      acted: flow,
      split: {
        source_quantity_flow_id: null,
        remainder_quantity_flow_id: null,
        remainder_quantity: null,
      },
    };
  }
  const selected: Flow = { ...flow, id: nextFlowId++, qty: quantity };
  const remainder: Flow = {
    ...flow,
    id: nextFlowId++,
    qty: flow.qty - quantity,
  };
  flows = flows.filter((f) => f.id !== flow.id).concat([selected, remainder]);
  return {
    acted: selected,
    split: {
      source_quantity_flow_id: flow.id,
      remainder_quantity_flow_id: remainder.id,
      remainder_quantity: remainder.qty,
    },
  };
}

function handle(url: string, method: string, body: unknown): Response {
  if (url === '/api/health') return json({ status: 'ok' });
  if (url === '/api/scan-stations') {
    return json(STATIONS.map((s) => ({ ...s, is_active: true })));
  }
  if (url === '/api/areas') {
    return json(
      AREAS.map((a) => ({
        ...areaRef(a.id),
        department_id: 1,
        barcode_value: `PF:AREA:${a.id}`,
        icon_url: null,
        is_active: true,
      })),
    );
  }
  if (url === '/api/departments') {
    return json([{ id: 1, name: 'Finishing', is_active: true }]);
  }
  if (url === '/api/operations') {
    return json(
      OPERATIONS.map((o) => ({
        ...o,
        description: null,
        default_expected_duration: null,
        is_active: true,
        created_at: 't',
        updated_at: 't',
      })),
    );
  }
  if (url === '/api/machines') return json([]);
  if (/\/context$/.test(url)) {
    const station = stationOf(url);
    return json({
      station_id: station.station_id,
      department: { id: 1, name: 'Finishing' },
      area: areaRef(station.area_id),
      operations: operationsOf(station.area_id),
      has_machines: hasMachines(station.area_id),
    });
  }
  const inv = /^\/api\/areas\/(\d+)\/inventory$/.exec(url);
  if (inv) return inventory(Number(inv[1]));

  if (/\/scans\/resolve$/.test(url)) {
    const station = stationOf(url);
    const input = body as { barcode?: string; part_number?: string };
    const scanned = input.barcode ?? `PF:PN:${input.part_number}`;
    const pn = scanned.slice('PF:PN:'.length).toUpperCase();
    const mine = flows.filter((f) => f.pn === pn);
    const inArea = mine.filter((f) => f.areaId === station.area_id);
    const candidates = mine.filter((f) => f.areaId !== station.area_id);
    return json({
      part_number: pn,
      station_id: station.station_id,
      area: areaRef(station.area_id),
      resolution: inArea.length
        ? 'ALREADY_IN_AREA'
        : candidates.length
          ? 'TRANSFER_SOURCE_AVAILABLE'
          : 'NO_TRANSFERABLE_QUANTITY',
      in_area: inArea.map(flowWire),
      candidates: candidates.map((flow) => ({
        ...flowWire(flow),
        current_area: areaRef(flow.areaId),
        route_status: 'FLOATING',
        expected_next_area: null,
        expected_operation_id: null,
        suggested_operation_id: operationsOf(station.area_id)[0].id,
        // The SERVER's judgement of repair eligibility: this quantity
        // previously visited the station's Area.
        repair_available: flow.visited?.includes(station.area_id) ?? false,
      })),
      operations: operationsOf(station.area_id),
      has_active_demand: true,
      transfer_blocked_reason: null,
      requires_selection:
        inArea.length > 1 || (inArea.length === 0 && candidates.length > 1),
      combine_groups: [],
      scrapped_quantity: 0,
    });
  }

  const inArea =
    /\/(machine-assignments|machine-releases|area-completions)$/.exec(url);
  if (inArea && method === 'POST') {
    const station = stationOf(url);
    const request = body as {
      part_number: string;
      quantity_flow_id: number;
      machine_id?: number;
      quantity: number;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    const failed = failWrite();
    if (failed) return failed;
    const source = flows.find((f) => f.id === request.quantity_flow_id);
    if (!source) return json({ detail: 'no longer active.' }, 409);
    if (request.quantity > source.qty) {
      return json({ detail: 'exceeds' }, 422);
    }
    const snapshot = snapshotFlows();
    const pre = { ...source };
    const { acted, split } = splitIfPartial(source, request.quantity);
    const kind = inArea[1];
    let movementType: string;
    let commandKind: string;
    if (kind === 'machine-assignments') {
      acted.state = 'ON_MACHINE';
      acted.machineId = request.machine_id!;
      movementType = 'ASSIGNED_TO_MACHINE';
      commandKind = 'ASSIGN';
    } else if (kind === 'machine-releases') {
      acted.state = 'QUEUED';
      acted.machineId = null;
      movementType = 'RELEASED_FROM_MACHINE';
      commandKind = 'QUEUE';
    } else {
      acted.state = 'READY_TO_TRANSFER';
      acted.machineId = null;
      movementType = 'AREA_COMPLETED';
      commandKind = 'DONE';
    }
    const movementId = nextMovementId++;
    recordCommand(request.device_event_id, {
      kind: commandKind,
      pn: acted.pn,
      qty: acted.qty,
      movements: [
        {
          movement_id: movementId,
          movement_type: movementType,
          movement_reason: null,
          quantity: acted.qty,
          from_area: areaRef(acted.areaId),
          to_area: areaRef(acted.areaId),
          machine_id: request.machine_id ?? pre.machineId,
          operation_id: operationsOf(acted.areaId)[0].id,
        },
      ],
      restored: [
        {
          quantity_flow_id: pre.id,
          quantity: pre.qty,
          status: 'ACTIVE',
          area: areaRef(pre.areaId),
          machine_id: pre.machineId,
          processing_state: pre.state,
        },
      ],
      snapshot,
    });
    return commit(request.device_event_id, {
      movement_id: movementId,
      movement_type: movementType,
      quantity_flow_id: acted.id,
      part_number: acted.pn,
      quantity: acted.qty,
      area_id: acted.areaId,
      machine_id: request.machine_id ?? null,
      operation_id: operationsOf(acted.areaId)[0].id,
      station_id: station.station_id,
      processing_state: acted.state,
      ...split,
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-31T12:00:00Z',
    });
  }

  if (/\/transfers$/.test(url) && method === 'POST') {
    const station = stationOf(url);
    const request = body as {
      quantity_flow_id: number;
      source_area_id: number;
      quantity: number;
      operation_id: number | null;
      repair: boolean;
      repair_reason: string | null;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    const failed = failWrite();
    if (failed) return failed;
    const source = flows.find((f) => f.id === request.quantity_flow_id)!;
    if (request.quantity > source.qty) {
      return json({ detail: 'exceeds' }, 422);
    }
    if (request.repair && !request.repair_reason?.trim()) {
      return json({ detail: 'A repair needs its reason.' }, 422);
    }
    if (request.repair && !source.visited?.includes(station.area_id)) {
      return json(
        { detail: 'never previously visited. Nothing was recorded.' },
        409,
      );
    }
    const snapshot = snapshotFlows();
    const pre = { ...source };
    const { acted, split } = splitIfPartial(source, request.quantity);
    acted.areaId = station.area_id;
    acted.state = hasMachines(station.area_id) ? 'QUEUED' : 'PROCESSING';
    acted.machineId = null;
    const movementId = nextMovementId++;
    recordCommand(request.device_event_id, {
      kind: 'TRANSFER',
      pn: acted.pn,
      qty: acted.qty,
      movements: [
        {
          movement_id: movementId,
          movement_type: 'TRANSFERRED',
          movement_reason: request.repair ? 'REPAIR' : null,
          quantity: acted.qty,
          from_area: areaRef(request.source_area_id),
          to_area: areaRef(station.area_id),
          machine_id: null,
          operation_id:
            request.operation_id ?? operationsOf(station.area_id)[0].id,
        },
      ],
      restored: [
        {
          quantity_flow_id: pre.id,
          quantity: pre.qty,
          status: 'ACTIVE',
          area: areaRef(pre.areaId),
          machine_id: pre.machineId,
          processing_state: pre.state,
        },
      ],
      snapshot,
    });
    return commit(request.device_event_id, {
      movement_id: movementId,
      quantity_flow_id: acted.id,
      part_number: acted.pn,
      quantity: acted.qty,
      from_area_id: request.source_area_id,
      to_area_id: station.area_id,
      operation_id: request.operation_id ?? operationsOf(station.area_id)[0].id,
      station_id: station.station_id,
      assigned_route_step_id: null,
      route_deviation: null,
      completed_movement_id: null,
      completed_machine_id: null,
      ...split,
      movement_reason: request.repair ? 'REPAIR' : null,
      reason: request.repair ? request.repair_reason : null,
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-31T12:00:00Z',
    });
  }

  if (/\/scraps$/.test(url) && method === 'POST') {
    const station = stationOf(url);
    const request = body as {
      part_number: string;
      quantity_flow_id: number;
      quantity: number;
      reason: string;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    const failed = failWrite();
    if (failed) return failed;
    const source = flows.find((f) => f.id === request.quantity_flow_id);
    if (!source) return json({ detail: 'no longer active.' }, 409);
    if (request.quantity > source.qty) {
      return json({ detail: 'exceeds' }, 422);
    }
    const snapshot = snapshotFlows();
    const pre = { ...source };
    const { acted, split } = splitIfPartial(source, request.quantity);
    const machineId = acted.state === 'ON_MACHINE' ? acted.machineId : null;
    // The scrapped flow closes and leaves active production.
    flows = flows.filter((f) => f.id !== acted.id);
    const movementId = nextMovementId++;
    recordCommand(request.device_event_id, {
      kind: 'SCRAP',
      pn: pre.pn,
      qty: request.quantity,
      movements: [
        {
          movement_id: movementId,
          movement_type: 'SCRAPPED',
          movement_reason: null,
          quantity: request.quantity,
          from_area: areaRef(pre.areaId),
          to_area: areaRef(pre.areaId),
          machine_id: machineId,
          operation_id: operationsOf(pre.areaId)[0].id,
        },
      ],
      restored: [
        {
          quantity_flow_id: pre.id,
          quantity: pre.qty,
          status: 'ACTIVE',
          area: areaRef(pre.areaId),
          machine_id: pre.machineId,
          processing_state: pre.state,
        },
      ],
      snapshot,
    });
    return commit(request.device_event_id, {
      movement_id: movementId,
      quantity_flow_id: acted.id,
      part_number: pre.pn,
      quantity: request.quantity,
      area_id: pre.areaId,
      machine_id: machineId,
      reason: request.reason,
      station_id: station.station_id,
      ...split,
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-31T12:00:00Z',
    });
  }

  if (/\/quantity-additions$/.test(url) && method === 'POST') {
    const station = stationOf(url);
    const request = body as {
      part_number: string;
      quantity: number;
      reason: string;
      operation_id?: number;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    const failed = failWrite();
    if (failed) return failed;
    const pn = request.part_number.toUpperCase();
    if (!flows.some((f) => f.pn === pn && f.areaId === station.area_id)) {
      return json(
        { detail: 'no active quantity in this Area. Nothing was recorded.' },
        409,
      );
    }
    const snapshot = snapshotFlows();
    const created: Flow = {
      id: nextFlowId++,
      pn,
      qty: request.quantity,
      areaId: station.area_id,
      state: hasMachines(station.area_id) ? 'QUEUED' : 'PROCESSING',
      machineId: null,
    };
    flows = [...flows, created];
    const movementId = nextMovementId++;
    recordCommand(request.device_event_id, {
      kind: 'ADD',
      pn,
      qty: request.quantity,
      movements: [
        {
          movement_id: movementId,
          movement_type: 'QUANTITY_ADJUSTED',
          movement_reason: null,
          quantity: request.quantity,
          from_area: null,
          to_area: areaRef(station.area_id),
          machine_id: null,
          operation_id:
            request.operation_id ?? operationsOf(station.area_id)[0].id,
        },
      ],
      restored: [
        {
          quantity_flow_id: created.id,
          quantity: created.qty,
          status: 'REVERSED',
          area: null,
          machine_id: null,
          processing_state: null,
        },
      ],
      snapshot,
    });
    return commit(request.device_event_id, {
      movement_id: movementId,
      quantity_flow_id: created.id,
      part_number: pn,
      quantity: created.qty,
      area_id: station.area_id,
      operation_id: request.operation_id ?? operationsOf(station.area_id)[0].id,
      processing_state: created.state,
      reason: request.reason,
      station_id: station.station_id,
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-31T12:00:00Z',
    });
  }

  const preview = /\/undo-preview\/([^/]+)$/.exec(url);
  if (preview && method === 'GET') {
    const station = stationOf(url);
    const eventId = decodeURIComponent(preview[1]);
    const record = records.get(eventId);
    if (!record) {
      return json({ detail: `No production event under '${eventId}'.` }, 404);
    }
    const reason =
      forceIneligibleReason ??
      (record.reversed
        ? 'This action has already been reversed.'
        : commandLog[commandLog.length - 1] !== eventId
          ? 'Later activity exists for this quantity: the action is no longer the most recent recorded operation and cannot be undone.'
          : null);
    return json({
      reverses_device_event_id: eventId,
      station_id: station.station_id,
      kind: record.kind,
      part_number: record.pn,
      quantity: record.qty,
      occurred_at: '2026-08-31T12:00:00Z',
      eligible: reason === null,
      ineligible_reason: reason,
      movements: record.movements,
      restored: reason === null ? record.restored : [],
    });
  }

  if (/\/undos$/.test(url) && method === 'POST') {
    const station = stationOf(url);
    const request = body as {
      part_number: string;
      reverses_device_event_id: string;
      device_event_id: string;
    };
    const replay = committed.get(request.device_event_id);
    if (replay) return json(replay, 200);
    const failed = failWrite();
    if (failed) return failed;
    const record = records.get(request.reverses_device_event_id);
    if (!record) {
      return json({ detail: 'No production event was recorded.' }, 422);
    }
    if (record.reversed) {
      return json(
        {
          detail:
            'This action has already been reversed. Nothing was reversed.',
        },
        409,
      );
    }
    if (
      commandLog[commandLog.length - 1] !== request.reverses_device_event_id
    ) {
      return json(
        {
          detail:
            'Later activity exists for this quantity. Nothing was reversed.',
        },
        409,
      );
    }
    // Restore the complete pre-command state (the reversal-aware
    // derivation of the real backend).
    flows = record.snapshot.map((f) => ({
      ...f,
      visited: f.visited && [...f.visited],
    }));
    record.reversed = true;
    commandLog.pop();
    return commit(request.device_event_id, {
      reverses_device_event_id: request.reverses_device_event_id,
      reversed_kind: record.kind,
      part_number: record.pn,
      station_id: station.station_id,
      movements: record.movements.map((item) => ({
        movement_id: nextMovementId++,
        reverses_movement_id: item.movement_id,
        original_movement_type: item.movement_type,
      })),
      flows: record.restored.map((item) => ({
        quantity_flow_id: item.quantity_flow_id,
        quantity: item.quantity,
        status: item.status,
        current_area_id:
          item.status === 'ACTIVE'
            ? ((item.area as { id: number } | null)?.id ?? null)
            : null,
        current_machine_id: item.machine_id,
      })),
      device_event_id: request.device_event_id,
      occurred_at: '2026-08-31T12:05:00Z',
    });
  }
  return json({ detail: `Unhandled ${method} ${url}` }, 500);
}

beforeEach(() => {
  window.sessionStorage.removeItem('partflow.dev.mock-preview');
  flows = [
    // Lathe (Machines): a queued PN with a repair-eligible source at
    // Plating, and one PN on a Machine with a non-eligible source.
    {
      id: 300,
      pn: 'PN-A',
      qty: 12,
      areaId: 2,
      state: 'QUEUED',
      machineId: null,
    },
    {
      id: 350,
      pn: 'PN-A',
      qty: 8,
      areaId: 6,
      state: 'READY_TO_TRANSFER',
      machineId: null,
      visited: [2, 6],
    },
    {
      id: 301,
      pn: 'PN-B',
      qty: 10,
      areaId: 2,
      state: 'ON_MACHINE',
      machineId: 1,
    },
    {
      id: 351,
      pn: 'PN-B',
      qty: 4,
      areaId: 6,
      state: 'READY_TO_TRANSFER',
      machineId: null,
    },
  ];
  committed = new Map();
  requests = [];
  nextMovementId = 500;
  nextFlowId = 900;
  writeFailure = null;
  commandLog = [];
  records = new Map();
  forceIneligibleReason = null;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      if (!url.endsWith('/api/health')) requests.push({ url, method, body });
      return Promise.resolve().then(() => handle(url, method, body));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderStation(stationId: string) {
  window.history.replaceState({}, '', `/scan-station/${stationId}`);
  render(<App />);
  const input = await screen.findByLabelText('Scan barcode');
  await screen.findByText('Total PNs');
  return input;
}

function scan(barcode: string) {
  const input = screen.getByLabelText('Scan barcode');
  fireEvent.change(input, { target: { value: barcode } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

function dialog() {
  return screen.getByRole('dialog');
}

async function notice() {
  return waitFor(() => {
    const toast = document.querySelector('.ss-toast');
    if (!toast) throw new Error('no notice');
    return toast as HTMLElement;
  });
}

function writes() {
  return requests.filter(
    (r) =>
      r.method === 'POST' &&
      /\/(area-completions|machine-assignments|machine-releases|transfers|merges|scraps|quantity-additions|undos)$/.test(
        r.url,
      ),
  );
}

function reads(pattern: RegExp) {
  return requests.filter((r) => r.method === 'GET' && pattern.test(r.url));
}

function machineCard(name: string): HTMLElement {
  const heading = screen.getByText(name, { selector: '.mname' });
  return heading.closest('.abd-machine') as HTMLElement;
}

function quantityInput(box: HTMLElement) {
  return within(box).getByLabelText(/^Quantity: /);
}

function summaryValue(box: HTMLElement, term: string): string {
  const dt = within(box).getByText(term, { selector: 'dt' });
  return dt.nextElementSibling?.textContent ?? '';
}

function lastActionBlock() {
  return document.querySelector('.ss-lastpn') as HTMLElement;
}

function undoButton() {
  return document.querySelector('button.ss-undo') as HTMLButtonElement;
}

function scrapScan(box: HTMLElement, value: string) {
  const input = within(box).getByLabelText('Scrap barcode input');
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

/** Complete a Machine DONE on PN-B (Lathe 1) — a reversible command. */
async function completeDoneOnPnB() {
  fireEvent.click(
    within(machineCard('Lathe 1')).getByRole('button', {
      name: 'Complete Area processing',
    }),
  );
  const box = await screen.findByRole('dialog', {
    name: 'Complete Area processing',
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm completion' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Confirm finished quantity?',
  });
  fireEvent.click(within(gate).getByRole('button', { name: 'Yes — finished' }));
  await notice();
}

/** Scrap `count` pcs of PN-A (queued at Lathe) through the workflow. */
async function completeScrapOnPnA(count: number) {
  scan('PF:PN:PN-A');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', { name: /Scrap damaged quantity/ }),
  );
  const box = await screen.findByRole('dialog', {
    name: 'Scrap damaged quantity',
  });
  for (let i = 0; i < count; i += 1) scrapScan(box, 'PF:SCRAP');
  fireEvent.change(within(box).getByLabelText(/Scrap reason/), {
    target: { value: 'tool crash — gouged face' },
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm scrap' }));
  await notice();
}

/* ============ Undo ============ */

test('UNDO is disabled with no completed action, opens the server preview summary after one, asks the final warning question, and reverses under its own device_event_id', async () => {
  const input = await renderStation('LATHE-ST-01');
  expect(undoButton()).toBeDisabled();

  await completeDoneOnPnB();
  expect(undoButton()).toBeEnabled();
  const doneEventId = writes()[0].body.device_event_id as string;

  fireEvent.click(undoButton());
  const box = await screen.findByRole('dialog', {
    name: 'Reverse the last Part Number action?',
  });
  // The structured §4.5 summary from the server's preview.
  expect(reads(new RegExp(`/undo-preview/${doneEventId}$`))).toHaveLength(1);
  expect(summaryValue(box, 'Original action')).toBe('AREA_COMPLETED');
  expect(summaryValue(box, 'Quantity')).toBe('10 pcs');
  expect(summaryValue(box, 'Machine')).toBe('Lathe 1');
  expect(summaryValue(box, 'Result after reversal')).toBe(
    '10 pcs return to Lathe — on Lathe 1.',
  );
  expect(writes()).toHaveLength(1);

  // The final warning question — never skipped; Cancel returns.
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm reversal' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Reverse this action?',
  });
  fireEvent.click(within(gate).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(writes()).toHaveLength(1);
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm reversal' }),
  );
  const gate2 = await screen.findByRole('dialog', {
    name: 'Reverse this action?',
  });
  fireEvent.click(
    within(gate2).getByRole('button', { name: 'Yes — reverse it' }),
  );

  const toast = await notice();
  expect(toast).toHaveTextContent('PN-B — action reversed');
  expect(toast).toHaveTextContent('original history stays recorded');
  const undo = writes().find((r) => /\/undos$/.test(r.url))!;
  expect(undo.body.reverses_device_event_id).toBe(doneEventId);
  expect(undo.body.device_event_id).not.toBe(doneEventId);
  // The quantity is back ON its Machine after the re-read.
  await waitFor(() =>
    expect(machineCard('Lathe 1')).toHaveTextContent('10 pcs assigned'),
  );
  // Nothing left to reverse: the block returns to its empty state.
  expect(lastActionBlock()).toHaveTextContent('No Part Number actions yet');
  expect(undoButton()).toBeDisabled();
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('after a confirmed Undo the Last Scanned PN advances to the previous completed operation', async () => {
  await renderStation('LATHE-ST-01');
  await completeDoneOnPnB();
  await completeScrapOnPnA(2);
  expect(lastActionBlock()).toHaveTextContent('SCRAPPED');

  fireEvent.click(undoButton());
  const box = await screen.findByRole('dialog', {
    name: 'Reverse the last Part Number action?',
  });
  expect(summaryValue(box, 'Original action')).toBe('SCRAPPED');
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm reversal' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Reverse this action?',
  });
  fireEvent.click(
    within(gate).getByRole('button', { name: 'Yes — reverse it' }),
  );
  await notice();
  // The previous operation (the DONE on PN-B) is the Last Scanned PN
  // again, and Undo stays available for it.
  expect(lastActionBlock()).toHaveTextContent('PN-B');
  expect(lastActionBlock()).toHaveTextContent('AREA_COMPLETED');
  expect(undoButton()).toBeEnabled();
});

test('an ineligible command shows the server reason with no way to confirm', async () => {
  await renderStation('LATHE-ST-01');
  await completeDoneOnPnB();
  forceIneligibleReason =
    'Later activity exists for this quantity: the action is no longer the most recent recorded operation and cannot be undone.';

  fireEvent.click(undoButton());
  const box = await screen.findByRole('dialog', {
    name: 'Reverse the last Part Number action?',
  });
  expect(box).toHaveTextContent('Later activity exists for this quantity');
  expect(
    within(box).queryByRole('button', { name: 'Confirm reversal' }),
  ).toBeNull();
  fireEvent.click(within(box).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(writes()).toHaveLength(1); // the DONE only — nothing reversed
});

test('a refused Undo keeps the server reason in the dialog, re-reads the Area and pops nothing', async () => {
  await renderStation('LATHE-ST-01');
  await completeDoneOnPnB();
  const inventoryReads = reads(/\/inventory$/).length;

  fireEvent.click(undoButton());
  const box = await screen.findByRole('dialog', {
    name: 'Reverse the last Part Number action?',
  });
  writeFailure = {
    status: 409,
    body: {
      detail: 'This action has already been reversed. Nothing was reversed.',
    },
  };
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm reversal' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Reverse this action?',
  });
  fireEvent.click(
    within(gate).getByRole('button', { name: 'Yes — reverse it' }),
  );
  await waitFor(() =>
    expect(box).toHaveTextContent('This action has already been reversed.'),
  );
  // The rejection re-read the Area; the entry stays on the stack.
  await waitFor(() =>
    expect(reads(/\/inventory$/).length).toBeGreaterThan(inventoryReads),
  );
  expect(
    within(box).getByRole('button', { name: 'Retry reversal' }),
  ).toBeInTheDocument();
  fireEvent.click(within(box).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(lastActionBlock()).toHaveTextContent('AREA_COMPLETED');
  expect(undoButton()).toBeEnabled();
});

test('a lost Undo response freezes the intent and the retry replays the same device_event_id', async () => {
  await renderStation('LATHE-ST-01');
  await completeDoneOnPnB();

  fireEvent.click(undoButton());
  const box = await screen.findByRole('dialog', {
    name: 'Reverse the last Part Number action?',
  });
  writeFailure = 'lost-response';
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm reversal' }),
  );
  const gate = await screen.findByRole('dialog', {
    name: 'Reverse this action?',
  });
  fireEvent.click(
    within(gate).getByRole('button', { name: 'Yes — reverse it' }),
  );
  await waitFor(() =>
    expect(box).toHaveTextContent(
      'this reversal may or may not have been recorded',
    ),
  );
  // The retry is the exact same request — no second question asked.
  fireEvent.click(
    within(box).getByRole('button', { name: 'Retry the same reversal' }),
  );
  const toast = await notice();
  expect(toast).toHaveTextContent('nothing was reversed twice');
  const undos = writes().filter((r) => /\/undos$/.test(r.url));
  expect(undos).toHaveLength(2);
  expect(undos[1].body.device_event_id).toBe(undos[0].body.device_event_id);
  expect(lastActionBlock()).toHaveTextContent('No Part Number actions yet');
});

/* ============ Repair ============ */

test('Return quantity for repair is offered only for server-marked sources, requires the reason, and records the explicit Repair intent', async () => {
  const input = await renderStation('LATHE-ST-01');
  scan('PF:PN:PN-A');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', { name: /Return quantity for repair/ }),
  );
  const box = await screen.findByRole('dialog', {
    name: 'Return quantity for repair',
  });
  expect(box).toHaveTextContent('so earlier work can be corrected');
  expect(quantityInput(box)).toHaveValue('8');
  // The mandatory reason gates Next.
  expect(within(box).getByRole('button', { name: 'Next' })).toBeDisabled();
  fireEvent.change(within(box).getByLabelText(/Reason/), {
    target: { value: 'Thread depth out of spec' },
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));

  const summary = dialog();
  expect(summaryValue(summary, 'Action')).toBe('Return quantity for repair');
  expect(summaryValue(summary, 'Reason')).toBe('Thread depth out of spec');
  expect(summaryValue(summary, 'Recorded event')).toBe(
    'TRANSFERRED · REPAIR intent',
  );
  expect(writes()).toHaveLength(0);
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm repair' }),
  );

  const toast = await notice();
  expect(toast).toHaveTextContent('PN-A × 8');
  expect(toast).toHaveTextContent('for repair');
  expect(writes()).toHaveLength(1);
  expect(writes()[0].body).toMatchObject({
    quantity_flow_id: 350,
    quantity: 8,
    repair: true,
    repair_reason: 'Thread depth out of spec',
  });
  const last = lastActionBlock();
  expect(last).toHaveTextContent('TRANSFERRED · REPAIR');
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('a partial repair sends only the chosen quantity; the server splits and the remainder is reported', async () => {
  await renderStation('LATHE-ST-01');
  scan('PF:PN:PN-A');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', { name: /Return quantity for repair/ }),
  );
  const box = await screen.findByRole('dialog', {
    name: 'Return quantity for repair',
  });
  fireEvent.change(quantityInput(box), { target: { value: '3' } });
  fireEvent.change(within(box).getByLabelText(/Reason/), {
    target: { value: 'Rework the chamfer' },
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  const summary = dialog();
  expect(summaryValue(summary, 'Quantity')).toBe('3 pcs of 8');
  fireEvent.click(
    within(summary).getByRole('button', { name: 'Confirm repair' }),
  );
  const toast = await notice();
  expect(toast).toHaveTextContent('SPLIT · 5 pcs remain at Plating');
  expect(writes()[0].body).toMatchObject({ quantity: 3, repair: true });
});

test('the normal transfer to a previously visited Area stays available and never turns into a Repair by itself', async () => {
  await renderStation('LATHE-ST-01');
  scan('PF:PN:PN-A');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', {
      name: /Receive more quantity from another Area/,
    }),
  );
  const box = await screen.findByRole('dialog', {
    name: 'Receive from another Area',
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  fireEvent.click(
    within(dialog()).getByRole('button', { name: 'Confirm transfer' }),
  );
  await notice();
  expect(writes()[0].body).toMatchObject({
    repair: false,
    repair_reason: null,
  });
});

test('Repair is not offered when no source is repair-eligible', async () => {
  await renderStation('LATHE-ST-01');
  scan('PF:PN:PN-B');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  expect(actions).not.toHaveTextContent('Return quantity for repair');
});

/* ============ Scrap ============ */

test('PF:SCRAP counts only inside the Scrap workflow: counting, −1, reset, one server operation for the total, and the refresh afterwards', async () => {
  const input = await renderStation('LATHE-ST-01');
  scan('PF:PN:PN-A');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', { name: /Scrap damaged quantity/ }),
  );
  const box = await screen.findByRole('dialog', {
    name: 'Scrap damaged quantity',
  });
  expect(box).toHaveTextContent('Scan PF:SCRAP once for each damaged piece');

  // Counting: each PF:SCRAP scan adds one; anything else is refused in
  // place without touching the count.
  scrapScan(box, 'PF:SCRAP');
  scrapScan(box, 'PF:SCRAP');
  scrapScan(box, 'PF:SCRAP');
  expect(within(box).getByRole('status')).toHaveTextContent('3');
  scrapScan(box, 'PF:PN:PN-A');
  expect(box).toHaveTextContent('is not a valid scrap barcode');
  expect(within(box).getByRole('status')).toHaveTextContent('3');
  fireEvent.click(within(box).getByRole('button', { name: 'Remove one' }));
  expect(within(box).getByRole('status')).toHaveTextContent('2');
  fireEvent.click(within(box).getByRole('button', { name: 'Reset' }));
  expect(within(box).getByRole('status')).toHaveTextContent('0');
  expect(within(box).getByRole('button', { name: 'Next' })).toBeDisabled();
  scrapScan(box, 'PF:SCRAP');
  scrapScan(box, 'PF:SCRAP');
  expect(box).toHaveTextContent('remaining after scrap 10 pcs');
  // The common reason is mandatory.
  expect(within(box).getByRole('button', { name: 'Next' })).toBeDisabled();
  fireEvent.change(within(box).getByLabelText(/Scrap reason/), {
    target: { value: 'tool crash — gouged face' },
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));

  expect(summaryValue(box, 'Scrap quantity')).toBe('2 pcs');
  expect(summaryValue(box, 'Available')).toBe('12 pcs');
  expect(summaryValue(box, 'Remaining active quantity')).toBe(
    '10 pcs — stays in the Area queue',
  );
  expect(summaryValue(box, 'Recorded event')).toBe('SCRAPPED');
  expect(writes()).toHaveLength(0);
  fireEvent.click(within(box).getByRole('button', { name: 'Confirm scrap' }));

  const toast = await notice();
  expect(toast).toHaveTextContent('PN-A × 2 scrapped at Lathe');
  // Exactly ONE server operation for the counted total.
  expect(writes()).toHaveLength(1);
  expect(writes()[0].url).toMatch(/\/scraps$/);
  expect(writes()[0].body).toMatchObject({
    part_number: 'PN-A',
    quantity_flow_id: 300,
    quantity: 2,
    reason: 'tool crash — gouged face',
  });
  // Inventory re-read: the remainder is what is left in the queue.
  await waitFor(() =>
    expect(screen.getByLabelText('Area statistics')).toHaveTextContent('10'),
  );
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('cancelling the Scrap workflow discards the pending count with no write', async () => {
  await renderStation('LATHE-ST-01');
  scan('PF:PN:PN-A');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', { name: /Scrap damaged quantity/ }),
  );
  const box = await screen.findByRole('dialog', {
    name: 'Scrap damaged quantity',
  });
  scrapScan(box, 'PF:SCRAP');
  scrapScan(box, 'PF:SCRAP');
  fireEvent.click(within(box).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(writes()).toHaveLength(0);
  expect(await notice()).toHaveTextContent(
    'Cancelled. No changes were recorded.',
  );
});

test('a lost Scrap response is an unknown outcome; the retry keeps the device_event_id and nothing is recorded twice', async () => {
  await renderStation('LATHE-ST-01');
  writeFailure = 'lost-response';
  await (async () => {
    scan('PF:PN:PN-A');
    const actions = await screen.findByRole('dialog', {
      name: 'Select an action',
    });
    fireEvent.click(
      within(actions).getByRole('button', { name: /Scrap damaged quantity/ }),
    );
    const box = await screen.findByRole('dialog', {
      name: 'Scrap damaged quantity',
    });
    scrapScan(box, 'PF:SCRAP');
    fireEvent.change(within(box).getByLabelText(/Scrap reason/), {
      target: { value: 'cracked' },
    });
    fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
    fireEvent.click(within(box).getByRole('button', { name: 'Confirm scrap' }));
    await waitFor(() =>
      expect(box).toHaveTextContent(
        'this scrap may or may not have been recorded',
      ),
    );
    expect(within(box).queryByRole('button', { name: '‹ Back' })).toBeNull();
    fireEvent.click(
      within(box).getByRole('button', { name: 'Retry the same scrap' }),
    );
  })();
  const toast = await notice();
  expect(toast).toHaveTextContent('nothing was recorded twice');
  const scraps = writes().filter((r) => /\/scraps$/.test(r.url));
  expect(scraps).toHaveLength(2);
  expect(scraps[1].body.device_event_id).toBe(scraps[0].body.device_event_id);
  // The flow shrank exactly once: 12 − 1 = 11 remain.
  expect(flows.filter((f) => f.pn === 'PN-A' && f.areaId === 2)).toHaveLength(
    1,
  );
  expect(flows.find((f) => f.pn === 'PN-A' && f.areaId === 2)!.qty).toBe(11);
});

test('the main scan input keeps rejecting PF:SCRAP with nothing recorded', async () => {
  await renderStation('LATHE-ST-01');
  scan('PF:SCRAP');
  const toast = await notice();
  expect(toast).toHaveTextContent('Scrap barcode cannot be used here');
  expect(toast).toHaveTextContent('select “Scrap damaged quantity,”');
  expect(writes()).toHaveLength(0);
  expect(screen.queryByRole('dialog')).toBeNull();
});

/* ============ Add more quantity ============ */

test('Add more quantity has no default and no MAX, requires the reason, confirms QUANTITY_ADJUSTED · INCREASE and refreshes', async () => {
  const input = await renderStation('LATHE-ST-01');
  scan('PF:PN:PN-A');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', { name: /Add more quantity/ }),
  );
  const box = await screen.findByRole('dialog', { name: 'Add more quantity' });
  // No default quantity and no MAX shortcut (GUI_DESIGN §4.7).
  expect(quantityInput(box)).toHaveValue('');
  expect(within(box).queryByRole('button', { name: /^MAX/ })).toBeNull();
  expect(within(box).getByRole('button', { name: 'Next' })).toBeDisabled();
  fireEvent.change(quantityInput(box), { target: { value: '3' } });
  expect(within(box).getByRole('button', { name: 'Next' })).toBeDisabled();
  fireEvent.change(within(box).getByLabelText(/Reason/), {
    target: { value: 'found 3 additional blanks with the lot' },
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));

  expect(summaryValue(box, 'Action')).toBe('Add physical quantity');
  expect(summaryValue(box, 'Quantity')).toBe('+3 pcs');
  expect(summaryValue(box, 'Recorded event')).toBe(
    'QUANTITY_ADJUSTED · INCREASE',
  );
  expect(writes()).toHaveLength(0);
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm addition' }),
  );

  const toast = await notice();
  expect(toast).toHaveTextContent('PN-A +3 pcs at Lathe');
  expect(toast).toHaveTextContent('waiting in the Area queue');
  expect(writes()).toHaveLength(1);
  expect(writes()[0].url).toMatch(/\/quantity-additions$/);
  expect(writes()[0].body).toMatchObject({
    part_number: 'PN-A',
    quantity: 3,
    reason: 'found 3 additional blanks with the lot',
    operation_id: 20,
  });
  expect(lastActionBlock()).toHaveTextContent(
    'QUANTITY_ADJUSTED · INCREASE · +3 pcs at Lathe',
  );
  // The added quantity is a NEW server flow in the re-read inventory.
  await waitFor(() =>
    expect(screen.getByLabelText('Area statistics')).toHaveTextContent('15'),
  );
  await waitFor(() => expect(document.activeElement).toBe(input));
});

test('a server refusal of the addition stays in the dialog with the reason and re-reads the Area', async () => {
  await renderStation('LATHE-ST-01');
  const inventoryReads = reads(/\/inventory$/).length;
  scan('PF:PN:PN-A');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', { name: /Add more quantity/ }),
  );
  const box = await screen.findByRole('dialog', { name: 'Add more quantity' });
  fireEvent.change(quantityInput(box), { target: { value: '2' } });
  fireEvent.change(within(box).getByLabelText(/Reason/), {
    target: { value: 'found parts' },
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));
  writeFailure = {
    status: 409,
    body: { detail: 'no active quantity in this Area. Nothing was recorded.' },
  };
  fireEvent.click(
    within(box).getByRole('button', { name: 'Confirm addition' }),
  );
  await waitFor(() =>
    expect(box).toHaveTextContent('no active quantity in this Area'),
  );
  // Back is withdrawn after a refusal; only Retry / Cancel remain, and
  // the Area was re-read so nothing stale survives the dialog.
  expect(within(box).queryByRole('button', { name: '‹ Back' })).toBeNull();
  expect(
    within(box).getByRole('button', { name: 'Retry addition' }),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(reads(/\/inventory$/).length).toBeGreaterThan(inventoryReads),
  );
  expect(lastActionBlock()).toHaveTextContent('No Part Number actions yet');
});

/* ============ Offline write-blocking ============ */

test('going offline disables the pending confirmation and the UNDO action in place', async () => {
  await renderStation('LATHE-ST-01');
  await completeDoneOnPnB();
  expect(undoButton()).toBeEnabled();

  scan('PF:PN:PN-A');
  const actions = await screen.findByRole('dialog', {
    name: 'Select an action',
  });
  fireEvent.click(
    within(actions).getByRole('button', { name: /Add more quantity/ }),
  );
  const box = await screen.findByRole('dialog', { name: 'Add more quantity' });
  fireEvent.change(quantityInput(box), { target: { value: '2' } });
  fireEvent.change(within(box).getByLabelText(/Reason/), {
    target: { value: 'found parts' },
  });
  fireEvent.click(within(box).getByRole('button', { name: 'Next' }));

  act(() => {
    window.dispatchEvent(new Event('offline'));
  });
  await waitFor(() =>
    expect(
      within(box).getByRole('button', { name: 'Confirm addition' }),
    ).toBeDisabled(),
  );
  expect(box).toHaveTextContent(
    'Disconnected — the addition cannot be recorded',
  );
  expect(
    writes().filter((r) => /\/quantity-additions$/.test(r.url)),
  ).toHaveLength(0);
  fireEvent.click(within(box).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(undoButton()).toBeDisabled();
});
