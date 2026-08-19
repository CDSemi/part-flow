// Environment configuration API (Phase 3.5 minimum environment setup):
// Departments, Areas, Operations, Scan Stations, and the Machine Asset
// Tag format (Administration → Barcode configuration).
//
// Wire responses are the backend's snake_case schemas; this module maps
// them to the camelCase application types the views render, and back.
// Server-owned fields (Area `barcode_value`, the Asset Tag
// `next_sequence` counter) are read-only here — the backend rejects
// any attempt to submit them.
//
// Production-safe: no mock data, no framework imports.

import { ApiError, apiRequest } from './client';

// ---------------------------------------------------------------------------
// Departments
// ---------------------------------------------------------------------------

export interface Department {
  id: number;
  name: string;
  isActive: boolean;
}

interface DepartmentWire {
  id: number;
  name: string;
  is_active: boolean;
}

function toDepartment(wire: DepartmentWire): Department {
  return { id: wire.id, name: wire.name, isActive: wire.is_active };
}

export async function listDepartments(): Promise<Department[]> {
  const wires = await apiRequest<DepartmentWire[]>('/api/departments');
  return wires.map(toDepartment);
}

export async function createDepartment(input: {
  name: string;
}): Promise<Department> {
  const wire = await apiRequest<DepartmentWire>('/api/departments', {
    method: 'POST',
    body: { name: input.name },
  });
  return toDepartment(wire);
}

export async function updateDepartment(
  id: number,
  patch: { name?: string; isActive?: boolean },
): Promise<Department> {
  const wire = await apiRequest<DepartmentWire>(`/api/departments/${id}`, {
    method: 'PATCH',
    body: {
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.isActive !== undefined ? { is_active: patch.isActive } : {}),
    },
  });
  return toDepartment(wire);
}

// ---------------------------------------------------------------------------
// Areas
// ---------------------------------------------------------------------------

export interface Area {
  id: number;
  departmentId: number;
  name: string;
  /** Server-assigned `PF:AREA:<id>` value — stable forever. */
  barcodeValue: string | null;
  description: string | null;
  color: string | null;
  isTerminal: boolean;
  isActive: boolean;
}

interface AreaWire {
  id: number;
  department_id: number;
  name: string;
  barcode_value: string | null;
  description: string | null;
  color: string | null;
  icon_url: string | null;
  is_terminal: boolean;
  is_active: boolean;
}

function toArea(wire: AreaWire): Area {
  return {
    id: wire.id,
    departmentId: wire.department_id,
    name: wire.name,
    barcodeValue: wire.barcode_value,
    description: wire.description,
    color: wire.color,
    isTerminal: wire.is_terminal,
    isActive: wire.is_active,
  };
}

/** CSS color of one Area (fallback for Areas without a color yet). */
export function areaColor(area: Pick<Area, 'color'> | undefined): string {
  return area?.color ?? 'var(--faint)';
}

export async function listAreas(): Promise<Area[]> {
  const wires = await apiRequest<AreaWire[]>('/api/areas');
  return wires.map(toArea);
}

export async function createArea(input: {
  departmentId: number;
  name: string;
  description: string | null;
  color: string | null;
  isTerminal: boolean;
}): Promise<Area> {
  const wire = await apiRequest<AreaWire>('/api/areas', {
    method: 'POST',
    body: {
      department_id: input.departmentId,
      name: input.name,
      description: input.description,
      color: input.color,
      is_terminal: input.isTerminal,
    },
  });
  return toArea(wire);
}

export async function updateArea(
  id: number,
  patch: {
    name?: string;
    description?: string | null;
    color?: string | null;
    isTerminal?: boolean;
    isActive?: boolean;
  },
): Promise<Area> {
  const wire = await apiRequest<AreaWire>(`/api/areas/${id}`, {
    method: 'PATCH',
    body: {
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.description !== undefined
        ? { description: patch.description }
        : {}),
      ...(patch.color !== undefined ? { color: patch.color } : {}),
      ...(patch.isTerminal !== undefined
        ? { is_terminal: patch.isTerminal }
        : {}),
      ...(patch.isActive !== undefined ? { is_active: patch.isActive } : {}),
    },
  });
  return toArea(wire);
}

// ---------------------------------------------------------------------------
// Operations
// ---------------------------------------------------------------------------

export interface Operation {
  id: number;
  areaId: number;
  code: string;
  name: string | null;
  description: string | null;
  /** ISO 8601 duration (`PT30M`) as delivered by the API, or null. */
  defaultExpectedDuration: string | null;
  isExternal: boolean;
  isActive: boolean;
}

interface OperationWire {
  id: number;
  area_id: number;
  code: string;
  name: string | null;
  description: string | null;
  default_expected_duration: string | null;
  is_external: boolean;
  is_active: boolean;
}

function toOperation(wire: OperationWire): Operation {
  return {
    id: wire.id,
    areaId: wire.area_id,
    code: wire.code,
    name: wire.name,
    description: wire.description,
    defaultExpectedDuration: wire.default_expected_duration,
    isExternal: wire.is_external,
    isActive: wire.is_active,
  };
}

export async function listOperations(): Promise<Operation[]> {
  const wires = await apiRequest<OperationWire[]>('/api/operations');
  return wires.map(toOperation);
}

export async function createOperation(input: {
  areaId: number;
  code: string;
  name: string | null;
  description: string | null;
  /** ISO 8601 duration or null. */
  defaultExpectedDuration: string | null;
  isExternal: boolean;
}): Promise<Operation> {
  const wire = await apiRequest<OperationWire>('/api/operations', {
    method: 'POST',
    body: {
      area_id: input.areaId,
      code: input.code,
      name: input.name,
      description: input.description,
      default_expected_duration: input.defaultExpectedDuration,
      is_external: input.isExternal,
    },
  });
  return toOperation(wire);
}

export async function updateOperation(
  id: number,
  patch: {
    code?: string;
    name?: string | null;
    description?: string | null;
    defaultExpectedDuration?: string | null;
    isExternal?: boolean;
    isActive?: boolean;
  },
): Promise<Operation> {
  const wire = await apiRequest<OperationWire>(`/api/operations/${id}`, {
    method: 'PATCH',
    body: {
      ...(patch.code !== undefined ? { code: patch.code } : {}),
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.description !== undefined
        ? { description: patch.description }
        : {}),
      ...(patch.defaultExpectedDuration !== undefined
        ? { default_expected_duration: patch.defaultExpectedDuration }
        : {}),
      ...(patch.isExternal !== undefined
        ? { is_external: patch.isExternal }
        : {}),
      ...(patch.isActive !== undefined ? { is_active: patch.isActive } : {}),
    },
  });
  return toOperation(wire);
}

// ---------------------------------------------------------------------------
// Scan Stations
// ---------------------------------------------------------------------------

export interface ScanStation {
  /** Stable Station ID — the natural identity, never renamed. */
  stationId: string;
  areaId: number;
  isActive: boolean;
}

interface ScanStationWire {
  station_id: string;
  area_id: number;
  is_active: boolean;
}

function toScanStation(wire: ScanStationWire): ScanStation {
  return {
    stationId: wire.station_id,
    areaId: wire.area_id,
    isActive: wire.is_active,
  };
}

export async function listScanStations(): Promise<ScanStation[]> {
  const wires = await apiRequest<ScanStationWire[]>('/api/scan-stations');
  return wires.map(toScanStation);
}

export async function createScanStation(input: {
  stationId: string;
  areaId: number;
  isActive: boolean;
}): Promise<ScanStation> {
  const wire = await apiRequest<ScanStationWire>('/api/scan-stations', {
    method: 'POST',
    body: {
      station_id: input.stationId,
      area_id: input.areaId,
      is_active: input.isActive,
    },
  });
  return toScanStation(wire);
}

export async function updateScanStation(
  stationId: string,
  patch: { areaId?: number; isActive?: boolean },
): Promise<ScanStation> {
  const wire = await apiRequest<ScanStationWire>(
    `/api/scan-stations/${encodeURIComponent(stationId)}`,
    {
      method: 'PATCH',
      body: {
        ...(patch.areaId !== undefined ? { area_id: patch.areaId } : {}),
        ...(patch.isActive !== undefined ? { is_active: patch.isActive } : {}),
      },
    },
  );
  return toScanStation(wire);
}

// ---------------------------------------------------------------------------
// Barcode configuration — Machine Asset Tag format
// ---------------------------------------------------------------------------

export interface MachineAssetTagFormatConfig {
  prefix: string;
  digits: number;
  /**
   * Read-only view of the persisted never-reuse counter — the source
   * of the "Next Asset Tag" preview. Allocation is owned by Machine
   * creation, never by this configuration surface.
   */
  nextSequence: number;
}

interface MachineAssetTagFormatWire {
  prefix: string;
  digits: number;
  next_sequence: number;
}

function toFormatConfig(
  wire: MachineAssetTagFormatWire,
): MachineAssetTagFormatConfig {
  return {
    prefix: wire.prefix,
    digits: wire.digits,
    nextSequence: wire.next_sequence,
  };
}

/** The configured format, or null while none has been saved yet. */
export async function getMachineAssetTagFormat(): Promise<MachineAssetTagFormatConfig | null> {
  try {
    const wire = await apiRequest<MachineAssetTagFormatWire>(
      '/api/barcode-configuration/machine-asset-tag-format',
    );
    return toFormatConfig(wire);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function putMachineAssetTagFormat(input: {
  prefix: string;
  digits: number;
}): Promise<MachineAssetTagFormatConfig> {
  const wire = await apiRequest<MachineAssetTagFormatWire>(
    '/api/barcode-configuration/machine-asset-tag-format',
    { method: 'PUT', body: { prefix: input.prefix, digits: input.digits } },
  );
  return toFormatConfig(wire);
}
