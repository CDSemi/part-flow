// Roles API (Administration → Roles & permissions and → Correction
// permissions): named, editable roles and the permissions each one
// grants. Every application User holds exactly one role.
//
// The server checks the Administration permissions of the signed-in
// user's role (Phase 14 slice 2); the Management and Scan Station
// permissions are recorded and checked by later slices. The client only
// hides what the role does not allow — the server decides. Workers who
// scan at the Scan Stations hold no role.
//
// Wire responses are the backend's snake_case schema; this module maps
// them to the camelCase application type. Permission edits travel as
// deltas (`grant_permissions` / `revoke_permissions`), so two editors
// changing different permissions never revert each other.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

/**
 * The permission vocabulary: one stable key per capability
 * PROJECT_PROFILE §20 lists, in the server's order. Keys are never
 * renamed; a later capability is added.
 */
export const PERMISSIONS = [
  'MANAGE_DEPARTMENTS',
  'MANAGE_AREAS',
  'MANAGE_OPERATIONS',
  'MANAGE_WORKERS',
  'MANAGE_USERS_AND_ROLES',
  'MANAGE_SCAN_STATIONS',
  'MANAGE_BARCODE_CONFIGURATION',
  'MANAGE_SCAN_BEHAVIOR',
  'MANAGE_WORKER_SESSION_POLICIES',
  'MANAGE_CORRECTION_PERMISSIONS',
  'CONFIGURE_SYSTEM_SETTINGS',
  'MANAGE_MACHINES',
  'MANAGE_ROUTE_TEMPLATES',
  'MANAGE_PART_NUMBER_MASTER',
  'VIEW_PRODUCTION_DATA',
  'MANAGE_WORK_ORDERS',
  'EDIT_WORK_ORDER_DEMAND',
  'SET_DEMAND_PRIORITY',
  'REORDER_HOT_ITEMS',
  'ASSIGN_ROUTES',
  'RESOLVE_EXCEPTIONAL_SITUATIONS',
  'EXPORT_REPORTS',
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
  'PERFORM_QUANTITY_CORRECTIONS',
  'EDIT_WORK_ORDER_ALLOCATION',
  'PERFORM_HISTORICAL_CORRECTIONS',
] as const;

export type Permission = (typeof PERMISSIONS)[number];

export interface Role {
  id: number;
  name: string;
  /** Granted permissions, sorted by key. */
  permissions: Permission[];
  /** Users holding the role, active and inactive. */
  userCount: number;
}

interface RoleWire {
  id: number;
  name: string;
  permissions: string[];
  user_count: number;
  created_at: string;
  updated_at: string;
}

const KNOWN_PERMISSIONS: ReadonlySet<string> = new Set(PERMISSIONS);

function isPermission(value: string): value is Permission {
  return KNOWN_PERMISSIONS.has(value);
}

function toRole(wire: RoleWire): Role {
  return {
    id: wire.id,
    name: wire.name,
    permissions: wire.permissions.map((key) => {
      // The server stores only known keys; anything else means this
      // client is out of date — fail loudly rather than drop a grant.
      if (!isPermission(key)) {
        throw new Error(`Unknown permission key from the server: ${key}`);
      }
      return key;
    }),
    userCount: wire.user_count,
  };
}

/** Every role, ordered by name. */
export async function listRoles(): Promise<Role[]> {
  const wires = await apiRequest<RoleWire[]>('/api/roles');
  return wires.map(toRole);
}

export async function createRole(input: {
  name: string;
  permissions: Permission[];
}): Promise<Role> {
  const wire = await apiRequest<RoleWire>('/api/roles', {
    method: 'POST',
    body: { name: input.name, permissions: input.permissions },
  });
  return toRole(wire);
}

/**
 * Rename a role and/or grant and revoke permissions in one audited
 * change. Only the provided parts are sent (an empty list is omitted);
 * granting a held permission or revoking a missing one changes nothing.
 */
export async function updateRole(
  id: number,
  patch: {
    name?: string;
    grantPermissions?: Permission[];
    revokePermissions?: Permission[];
  },
): Promise<Role> {
  const wire = await apiRequest<RoleWire>(`/api/roles/${id}`, {
    method: 'PATCH',
    body: {
      ...(patch.name !== undefined ? { name: patch.name } : {}),
      ...(patch.grantPermissions?.length
        ? { grant_permissions: patch.grantPermissions }
        : {}),
      ...(patch.revokePermissions?.length
        ? { revoke_permissions: patch.revokePermissions }
        : {}),
    },
  });
  return toRole(wire);
}
