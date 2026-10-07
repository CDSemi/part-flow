import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from 'vitest';

import { PERMISSIONS } from '../../api/roles';
import type { Role } from '../../api/roles';
import {
  CORRECTION_PERMISSIONS,
  INERT_PERMISSIONS,
  PERMISSION_LABELS,
  PROTECTED_PERMISSIONS,
  ROLE_PERMISSION_GROUPS,
  permissionChoiceLabel,
  roleHoldsProtected,
} from './permissions';
import { ADMIN_SECTIONS } from './sections';

// The permission vocabulary and its presentation (Administration →
// Roles & permissions and → Correction permissions), the protected and
// inert keys and the section → permission map, and the guards that only
// Administration and the user sign-in modules read a user, a role or a
// permission, and that only the session UI, the Administration sections
// and the development-only demo badges ask whether the signed-in user
// holds a permission: the server checks the Administration permissions
// (Phase 14 slice 2). The Scan Station never reads a user, a role or the
// permission vocabulary: it hides actions from its own context's station
// permissions (`stationCan`, slice 4), and the device list of
// Administration → Scan Stations reads the keys enrolling needs.

/** The server's vocabulary, in order (literal copy of the contract). */
const CONTRACT_KEYS = [
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
];

test('PERMISSIONS is the 35-key server vocabulary, in order', () => {
  expect([...PERMISSIONS]).toEqual(CONTRACT_KEYS);
  expect(new Set(PERMISSIONS).size).toBe(35);
});

test('every permission sits in exactly one editable group or the correction subset', () => {
  const placed = [
    ...ROLE_PERMISSION_GROUPS.flatMap((group) => group.permissions),
    ...CORRECTION_PERMISSIONS,
  ];
  expect(placed).toHaveLength(35);
  expect([...placed].sort()).toEqual([...CONTRACT_KEYS].sort());
  expect(ROLE_PERMISSION_GROUPS.map((group) => group.label)).toEqual([
    'Administration',
    'Production master data',
    'Work Orders, priority and reports',
    'Scan Station',
  ]);
  expect(CORRECTION_PERMISSIONS).toEqual([
    'UNDO_RECENT_SCANS',
    'PERFORM_QUANTITY_CORRECTIONS',
    'EDIT_WORK_ORDER_ALLOCATION',
    'PERFORM_HISTORICAL_CORRECTIONS',
  ]);
});

test('labels are unique and non-empty, with the GUI-vocabulary renames', () => {
  const labels = PERMISSIONS.map((key) => PERMISSION_LABELS[key]);
  expect(labels.every((label) => label.trim().length > 0)).toBe(true);
  expect(new Set(labels).size).toBe(labels.length);
  expect(PERMISSION_LABELS.ADJUST_SUGGESTED_ALLOCATION).toBe(
    'Review and adjust suggested completion allocation',
  );
  expect(PERMISSION_LABELS.SCAN_WORKER_BARCODES).toBe('Scan Worker badges');
  expect(PERMISSION_LABELS.MANAGE_ROUTE_TEMPLATES).toBe(
    'Manage Planned Routes',
  );
  expect(PERMISSION_LABELS.MANAGE_PART_NUMBER_MASTER).toBe(
    'Manage Part Numbers, including hard deletion',
  );
  expect(PERMISSION_LABELS.UNDO_RECENT_SCANS).toBe(
    'Undo recent eligible scans',
  );
});

test('the protected and inert permissions are the contract literals', () => {
  expect(PROTECTED_PERMISSIONS).toEqual([
    'UNDO_RECENT_SCANS',
    'PERFORM_QUANTITY_CORRECTIONS',
    'EDIT_WORK_ORDER_ALLOCATION',
    'PERFORM_HISTORICAL_CORRECTIONS',
    'MANAGE_CORRECTION_PERMISSIONS',
  ]);
  expect(INERT_PERMISSIONS).toEqual([
    'MANAGE_SCAN_BEHAVIOR',
    'RESOLVE_EXCEPTIONAL_SITUATIONS',
    'EXPORT_REPORTS',
    'PERFORM_QUANTITY_CORRECTIONS',
    'PERFORM_HISTORICAL_CORRECTIONS',
  ]);
  // Exactly the inert keys are marked where a permission is chosen.
  expect(
    PERMISSIONS.filter((key) =>
      permissionChoiceLabel(key).endsWith(' — grants nothing yet'),
    ),
  ).toEqual([
    'MANAGE_SCAN_BEHAVIOR',
    'RESOLVE_EXCEPTIONAL_SITUATIONS',
    'EXPORT_REPORTS',
    'PERFORM_QUANTITY_CORRECTIONS',
    'PERFORM_HISTORICAL_CORRECTIONS',
  ]);
  expect(permissionChoiceLabel('EXPORT_REPORTS')).toBe(
    'Export and print reports — grants nothing yet',
  );
  expect(permissionChoiceLabel('MANAGE_MACHINES')).toBe('Manage Machines');
});

test('a role is protected when it holds a correction permission or the permission to manage them', () => {
  const role = (permissions: Role['permissions']): Role => ({
    id: 1,
    name: 'R',
    permissions,
    userCount: 0,
    appliesAtScanStations: false,
  });
  expect(roleHoldsProtected(role([]))).toBe(false);
  expect(roleHoldsProtected(role(['MANAGE_USERS_AND_ROLES']))).toBe(false);
  expect(roleHoldsProtected(role(['UNDO_RECENT_SCANS']))).toBe(true);
  expect(roleHoldsProtected(role(['MANAGE_CORRECTION_PERMISSIONS']))).toBe(
    true,
  );
  expect(roleHoldsProtected(role(['PERFORM_HISTORICAL_CORRECTIONS']))).toBe(
    true,
  );
});

test('each Administration section names the permission its changes need', () => {
  expect(
    Object.fromEntries(
      ADMIN_SECTIONS.map((section) => [
        section.id,
        section.writePermission ?? null,
      ]),
    ),
  ).toEqual({
    departments: 'MANAGE_DEPARTMENTS',
    areas: 'MANAGE_AREAS',
    operations: 'MANAGE_OPERATIONS',
    workers: 'MANAGE_WORKERS',
    'scan-stations': 'MANAGE_SCAN_STATIONS',
    'barcode-configuration': 'MANAGE_BARCODE_CONFIGURATION',
    'scan-behavior': null,
    users: 'MANAGE_USERS_AND_ROLES',
    roles: 'MANAGE_USERS_AND_ROLES',
    'worker-sessions': 'MANAGE_WORKER_SESSION_POLICIES',
    'machine-assignment': null,
    'correction-permissions': 'MANAGE_CORRECTION_PERMISSIONS',
    'data-retention': 'CONFIGURE_SYSTEM_SETTINGS',
    'department-display': 'MANAGE_DEPARTMENTS',
    settings: 'CONFIGURE_SYSTEM_SETTINGS',
  });
});

const srcDir = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

function walk(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walk(path));
    else files.push(path);
  }
  return files;
}

/** Non-test sources (relative, `/`-separated) whose text matches. */
function sourcesMatching(pattern: RegExp): string[] {
  return walk(srcDir)
    .filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f))
    .filter((f) => pattern.test(readFileSync(f, 'utf8')))
    .map((f) => relative(srcDir, f).split('\\').join('/'))
    .sort();
}

test('only Administration, the Management access presentation and the session modules use the users and roles API', () => {
  expect(sourcesMatching(/from '[./]*api\/(users|roles)'/)).toEqual([
    'app/management-access.ts',
    'app/session-context.ts',
    'components/AccountChip.tsx',
    'components/AllocationAdjustmentDialog.tsx',
    'components/ViewOnlyPageNote.tsx',
    'views/administration/CorrectionPermissionsSection.tsx',
    'views/administration/RolesSection.tsx',
    'views/administration/SetPasswordDialog.tsx',
    'views/administration/StationDevicesDialog.tsx',
    'views/administration/UsersSection.tsx',
    'views/administration/permissions.ts',
    'views/administration/section-widgets.tsx',
    'views/administration/sections.ts',
    'views/machines/MachinesView.tsx',
    'views/tracking/AuditTrailDialog.tsx',
  ]);
});

test('only Administration, the Management access presentation and the session modules read a user, a role or a permission', () => {
  expect(
    sourcesMatching(
      /\b(Permission|PERMISSIONS|PERMISSION_LABELS|CORRECTION_PERMISSIONS|listRoles|listUsers|roleId|roleName)\b/,
    ),
  ).toEqual([
    'App.tsx',
    'api/roles.ts',
    'api/session.ts',
    'api/setup.ts',
    'api/station-devices.ts',
    'api/users.ts',
    'app/management-access.ts',
    'app/session-context.ts',
    'components/AccountChip.tsx',
    'components/FirstRunSetupDialog.tsx',
    'components/ViewOnlyPageNote.tsx',
    'views/administration/CorrectionPermissionsSection.tsx',
    'views/administration/RolesSection.tsx',
    'views/administration/StationDevicesDialog.tsx',
    'views/administration/UsersSection.tsx',
    'views/administration/permissions.ts',
    'views/administration/section-widgets.tsx',
    'views/administration/sections.ts',
  ]);
});

test('only the session UI, the sign-in gate, the Administration sections, the Management views and the DEV demo badges ask for a permission', () => {
  expect(sourcesMatching(/\bcan\(|\bhasPermission\b/)).toEqual([
    'App.tsx',
    'app/SignInGate.tsx',
    'app/management-access.ts',
    'app/session-context.ts',
    'app/session-provider.tsx',
    'views/administration/AreasSection.tsx',
    'views/administration/BarcodeConfigurationSection.tsx',
    'views/administration/CorrectionPermissionsSection.tsx',
    'views/administration/DepartmentDisplaySection.tsx',
    'views/administration/DepartmentsSection.tsx',
    'views/administration/HistoryArchivalSection.tsx',
    'views/administration/OperationsSection.tsx',
    'views/administration/RolesSection.tsx',
    'views/administration/ScanStationsSection.tsx',
    'views/administration/SettingsSection.tsx',
    'views/administration/SignInSettingsPanel.tsx',
    'views/administration/StationDevicesDialog.tsx',
    'views/administration/UsersSection.tsx',
    'views/administration/WorkerSessionsSection.tsx',
    'views/administration/WorkersSection.tsx',
    'views/machines/MachinesView.tsx',
    'views/part-numbers/PartNumbersView.tsx',
    'views/planned-routes/PlannedRoutesView.tsx',
    'views/priority/PriorityView.tsx',
    'views/scan-station/scan-station-dev-badges.tsx',
    'views/tracking/TrackingView.tsx',
    'views/work-orders/NewWorkOrderDialog.tsx',
    'views/work-orders/WorkOrderDetailPanel.tsx',
    'views/work-orders/WorkOrdersView.tsx',
  ]);
});
