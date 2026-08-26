// Machine management API (Phase 3.5 — Management → Machines): listing,
// creation with the server-assigned Asset Tag, the one Save-changes
// edit transaction, the explicit maintenance override, retirement,
// reactivation and the append-only lifecycle history.
//
// Wire responses are the backend's snake_case schemas; this module
// maps them to the camelCase view types. Server-owned fields
// (`asset_tag`, `barcode_value`, `retired_on`, maintenance timestamps)
// appear only in responses — creation carries the previewed
// `expected_asset_tag` as an optimistic precondition only, never as
// the assigned identity.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

/** One physical Machine record as the views render it. */
export interface Machine {
  /** Stable internal identity — never reused by a replacement. */
  id: number;
  areaId: number;
  /** Operator-facing display name; reusable across replacements. */
  name: string;
  /**
   * Required Asset Tag — the stable, human-readable identity of the
   * physical machine, assigned automatically by the server at
   * creation. Unique, never reused (retired Machines keep theirs),
   * never edited, and the source of the Machine barcode.
   */
  assetTag: string;
  /** Server-derived `PF:MACHINE:<asset-tag>` barcode value. */
  barcode: string;
  /**
   * Explicit maintenance override. Entering maintenance never moves,
   * releases, completes, or transfers assigned quantity.
   */
  maintenance?: {
    since: string;
    note?: string;
    expectedReturn?: string;
  };
  /** ISO timestamp of the last operational state change. */
  stateChangedAt: string;
  /**
   * ACTIVE quantity currently assigned to the Machine (server-derived
   * from the production projection, Phase 6). Running is derived from
   * it; retirement is blocked while it is above zero.
   */
  assignedQuantity: number;
  /** Set while retired (`YYYY-MM-DD`); absent = active. */
  retiredOn?: string;
  /* Optional asset metadata — production tracking never depends on
     these fields. */
  description?: string;
  manufacturer?: string;
  model?: string;
  serialNumber?: string;
  installedOn?: string;
  notes?: string;
}

/** One append-only Machine lifecycle event. */
export interface MachineLifecycleEvent {
  id: number;
  event: 'RETIRED' | 'REACTIVATED';
  /** ISO timestamp of the lifecycle change. */
  at: string;
  /** Recording actor — nullable until authentication (Phase 14). */
  actor: string | null;
  reason?: string;
  /** Present as a complete previous → current pair only when the
   * physical machine moved while retired (reactivation). */
  fromAreaId?: number;
  toAreaId?: number;
}

interface MachineWire {
  id: number;
  area_id: number;
  name: string;
  asset_tag: string;
  barcode_value: string;
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
  operational_state: 'MAINTENANCE' | 'RUNNING' | 'IDLE';
  assigned_quantity: number;
}

interface MachineLifecycleEventWire {
  id: number;
  machine_id: number;
  event_type: string;
  occurred_at: string;
  actor: string | null;
  reason: string | null;
  from_area_id: number | null;
  to_area_id: number | null;
}

function toMachine(wire: MachineWire): Machine {
  return {
    id: wire.id,
    areaId: wire.area_id,
    name: wire.name,
    assetTag: wire.asset_tag,
    barcode: wire.barcode_value,
    ...(wire.maintenance_since !== null
      ? {
          maintenance: {
            since: wire.maintenance_since,
            note: wire.maintenance_note ?? undefined,
            expectedReturn: wire.maintenance_expected_return ?? undefined,
          },
        }
      : {}),
    stateChangedAt: wire.state_changed_at,
    assignedQuantity: wire.assigned_quantity,
    retiredOn: wire.retired_on ?? undefined,
    description: wire.description ?? undefined,
    manufacturer: wire.manufacturer ?? undefined,
    model: wire.model ?? undefined,
    serialNumber: wire.serial_number ?? undefined,
    installedOn: wire.installed_on ?? undefined,
    notes: wire.notes ?? undefined,
  };
}

function toLifecycleEvent(
  wire: MachineLifecycleEventWire,
): MachineLifecycleEvent {
  return {
    id: wire.id,
    event: wire.event_type === 'REACTIVATED' ? 'REACTIVATED' : 'RETIRED',
    at: wire.occurred_at,
    actor: wire.actor,
    reason: wire.reason ?? undefined,
    fromAreaId: wire.from_area_id ?? undefined,
    toAreaId: wire.to_area_id ?? undefined,
  };
}

/**
 * One Save-changes draft of the Edit Machine dialog — also the draft
 * shape a retirement may carry as its recorded Save decision. Empty
 * optional fields travel as null (explicit clear); the maintenance
 * context is included only while the override is active.
 */
export interface MachineEditDraft {
  name: string;
  manufacturer: string | null;
  model: string | null;
  serialNumber: string | null;
  installedOn: string | null;
  notes: string | null;
  maintenanceNote?: string | null;
  maintenanceExpectedReturn?: string | null;
}

function draftToWire(draft: MachineEditDraft): Record<string, unknown> {
  return {
    name: draft.name,
    manufacturer: draft.manufacturer,
    model: draft.model,
    serial_number: draft.serialNumber,
    installed_on: draft.installedOn,
    notes: draft.notes,
    ...(draft.maintenanceNote !== undefined
      ? { maintenance_note: draft.maintenanceNote }
      : {}),
    ...(draft.maintenanceExpectedReturn !== undefined
      ? { maintenance_expected_return: draft.maintenanceExpectedReturn }
      : {}),
  };
}

export async function listMachines(): Promise<Machine[]> {
  const wires = await apiRequest<MachineWire[]>('/api/machines');
  return wires.map(toMachine);
}

export async function createMachine(input: {
  areaId: number;
  name: string;
  manufacturer: string | null;
  model: string | null;
  serialNumber: string | null;
  installedOn: string | null;
  notes: string | null;
  /**
   * The exact previewed Asset Tag — an optimistic precondition only.
   * The server still allocates the tag itself; a stale preview is a
   * 409 with nothing consumed.
   */
  expectedAssetTag: string | null;
}): Promise<Machine> {
  const wire = await apiRequest<MachineWire>('/api/machines', {
    method: 'POST',
    body: {
      area_id: input.areaId,
      name: input.name,
      manufacturer: input.manufacturer,
      model: input.model,
      serial_number: input.serialNumber,
      installed_on: input.installedOn,
      notes: input.notes,
      expected_asset_tag: input.expectedAssetTag,
    },
  });
  return toMachine(wire);
}

/** The ONE Save-changes transaction of the Edit Machine dialog. */
export async function updateMachine(
  id: number,
  draft: MachineEditDraft,
): Promise<Machine> {
  const wire = await apiRequest<MachineWire>(`/api/machines/${id}`, {
    method: 'PATCH',
    body: draftToWire(draft),
  });
  return toMachine(wire);
}

export async function startMaintenance(
  id: number,
  input: { note: string | null; expectedReturn: string | null },
): Promise<Machine> {
  const wire = await apiRequest<MachineWire>(
    `/api/machines/${id}/maintenance`,
    {
      method: 'POST',
      body: { note: input.note, expected_return: input.expectedReturn },
    },
  );
  return toMachine(wire);
}

export async function clearMaintenance(id: number): Promise<Machine> {
  const wire = await apiRequest<MachineWire>(
    `/api/machines/${id}/maintenance`,
    {
      method: 'DELETE',
    },
  );
  return toMachine(wire);
}

/**
 * Retire one Machine. `edits` is the recorded Save decision of the
 * retire flow — applied by the server in the same transaction as the
 * retirement and its lifecycle event; a recorded Discard sends none.
 * Actor identity arrives with authentication (Phase 14).
 */
export async function retireMachine(
  id: number,
  input: { edits: MachineEditDraft | null },
): Promise<Machine> {
  const wire = await apiRequest<MachineWire>(`/api/machines/${id}/retire`, {
    method: 'POST',
    body: input.edits !== null ? { edits: draftToWire(input.edits) } : {},
  });
  return toMachine(wire);
}

/** Reactivate the same physical machine (required reason; a changed
 * Area is forward-only for a machine that moved while retired). */
export async function reactivateMachine(
  id: number,
  input: { reason: string; name: string; areaId: number },
): Promise<Machine> {
  const wire = await apiRequest<MachineWire>(`/api/machines/${id}/reactivate`, {
    method: 'POST',
    body: { reason: input.reason, name: input.name, area_id: input.areaId },
  });
  return toMachine(wire);
}

export async function listLifecycleEvents(
  machineId: number,
): Promise<MachineLifecycleEvent[]> {
  const wires = await apiRequest<MachineLifecycleEventWire[]>(
    `/api/machines/${machineId}/lifecycle-events`,
  );
  return wires.map(toLifecycleEvent);
}
