// Permission presentation of Administration → Roles & permissions and
// → Correction permissions: one operator-facing label per permission
// key (the PROJECT_PROFILE §20 capabilities in GUI vocabulary — the
// Worker barcode is the badge, Route Templates are Planned Routes, the
// PartNumber master is Part Numbers), the four groups the Roles editor
// shows, the correction subset edited only in Correction permissions,
// the protected keys whose holders only a user who may manage correction
// permissions changes, and the keys that grant nothing yet. Presentation
// only: the server checks every permission itself, and nothing here
// allows or refuses anything.

import type { Permission, Role } from '../../api/roles';

export interface PermissionGroup {
  label: string;
  permissions: readonly Permission[];
}

export const PERMISSION_LABELS: Record<Permission, string> = {
  MANAGE_DEPARTMENTS: 'Manage Departments',
  MANAGE_AREAS: 'Manage Areas',
  MANAGE_OPERATIONS: 'Manage Operations',
  MANAGE_WORKERS: 'Manage Workers',
  MANAGE_USERS_AND_ROLES: 'Manage users and roles',
  MANAGE_SCAN_STATIONS: 'Manage Scan Stations',
  MANAGE_BARCODE_CONFIGURATION: 'Manage barcode configuration',
  MANAGE_SCAN_BEHAVIOR: 'Manage scan behavior',
  MANAGE_WORKER_SESSION_POLICIES: 'Manage Worker session policies',
  MANAGE_CORRECTION_PERMISSIONS: 'Manage correction permissions',
  CONFIGURE_SYSTEM_SETTINGS: 'Configure system settings',
  MANAGE_MACHINES: 'Manage Machines',
  MANAGE_ROUTE_TEMPLATES: 'Manage Planned Routes',
  MANAGE_PART_NUMBER_MASTER: 'Manage Part Numbers, including hard deletion',
  VIEW_PRODUCTION_DATA: 'View all current and historical production data',
  MANAGE_WORK_ORDERS: 'Create and edit Work Orders',
  EDIT_WORK_ORDER_DEMAND: 'Edit Work Order Demand',
  SET_DEMAND_PRIORITY: 'Set Work Order Demand priority',
  REORDER_HOT_ITEMS: 'Reorder Hot items',
  ASSIGN_ROUTES: 'Assign and edit Routes',
  RESOLVE_EXCEPTIONAL_SITUATIONS: 'Resolve exceptional production situations',
  EXPORT_REPORTS: 'Export and print reports',
  SCAN_PN_BARCODES: 'Scan PN barcodes',
  SCAN_MACHINE_BARCODES: 'Scan Machine barcodes',
  SCAN_WORKER_BARCODES: 'Scan Worker badges',
  RECEIVE_QUANTITY: 'Receive quantity into an Area',
  ASSIGN_QUANTITY_TO_MACHINE: 'Assign quantity to a Machine',
  CONFIRM_QUANTITY: 'Confirm quantity',
  COMPLETE_INTO_STOCKROOM: 'Complete production into Stockroom',
  CONFIRM_SUGGESTED_ALLOCATION: 'Confirm suggested allocation',
  ADJUST_SUGGESTED_ALLOCATION:
    'Review and adjust suggested completion allocation',
  UNDO_RECENT_SCANS: 'Undo recent eligible scans',
  PERFORM_QUANTITY_CORRECTIONS: 'Perform quantity corrections',
  EDIT_WORK_ORDER_ALLOCATION: 'Edit Work Order Allocation',
  PERFORM_HISTORICAL_CORRECTIONS: 'Perform authorized historical corrections',
};

/** The four groups the Roles & permissions editor edits. */
export const ROLE_PERMISSION_GROUPS: readonly PermissionGroup[] = [
  {
    label: 'Administration',
    permissions: [
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
    ],
  },
  {
    label: 'Production master data',
    permissions: [
      'MANAGE_MACHINES',
      'MANAGE_ROUTE_TEMPLATES',
      'MANAGE_PART_NUMBER_MASTER',
    ],
  },
  {
    label: 'Work Orders, priority and reports',
    permissions: [
      'VIEW_PRODUCTION_DATA',
      'MANAGE_WORK_ORDERS',
      'EDIT_WORK_ORDER_DEMAND',
      'SET_DEMAND_PRIORITY',
      'REORDER_HOT_ITEMS',
      'ASSIGN_ROUTES',
      'RESOLVE_EXCEPTIONAL_SITUATIONS',
      'EXPORT_REPORTS',
    ],
  },
  {
    label: 'Scan Station',
    permissions: [
      'SCAN_PN_BARCODES',
      'SCAN_MACHINE_BARCODES',
      'SCAN_WORKER_BARCODES',
      'RECEIVE_QUANTITY',
      'ASSIGN_QUANTITY_TO_MACHINE',
      'CONFIRM_QUANTITY',
      'COMPLETE_INTO_STOCKROOM',
      'CONFIRM_SUGGESTED_ALLOCATION',
      'ADJUST_SUGGESTED_ALLOCATION',
    ],
  },
];

/**
 * The correction permissions, edited only in Policies → Correction
 * permissions (the Roles editor shows them read-only and never sends
 * them, so the two editors never revert each other).
 */
export const CORRECTION_PERMISSIONS: readonly Permission[] = [
  'UNDO_RECENT_SCANS',
  'PERFORM_QUANTITY_CORRECTIONS',
  'EDIT_WORK_ORDER_ALLOCATION',
  'PERFORM_HISTORICAL_CORRECTIONS',
];

/**
 * The correction permissions and the permission to manage them: changing
 * who holds one (a role's grants, or a user's role, activity or password)
 * needs Manage correction permissions (the server's permission-management
 * guard).
 */
export const PROTECTED_PERMISSIONS: readonly Permission[] = [
  ...CORRECTION_PERMISSIONS,
  'MANAGE_CORRECTION_PERMISSIONS',
];

/** Permissions no PartFlow action requires yet; marked as granting
 * nothing yet wherever they are chosen. */
export const INERT_PERMISSIONS: readonly Permission[] = [
  'MANAGE_SCAN_BEHAVIOR',
  'RESOLVE_EXCEPTIONAL_SITUATIONS',
  'EXPORT_REPORTS',
  'PERFORM_QUANTITY_CORRECTIONS',
  'PERFORM_HISTORICAL_CORRECTIONS',
];

/** Whether the role holds a correction permission or the permission to
 * manage them. */
export function roleHoldsProtected(role: Role): boolean {
  return role.permissions.some((key) => PROTECTED_PERMISSIONS.includes(key));
}

/** The operator-facing label of a permission where it is chosen: the
 * label, marked when the permission grants nothing yet. */
export function permissionChoiceLabel(key: Permission): string {
  return INERT_PERMISSIONS.includes(key)
    ? `${PERMISSION_LABELS[key]} — grants nothing yet`
    : PERMISSION_LABELS[key];
}
