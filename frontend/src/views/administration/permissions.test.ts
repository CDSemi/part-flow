import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from 'vitest';

import { PERMISSIONS } from '../../api/roles';
import {
  CORRECTION_PERMISSIONS,
  PERMISSION_LABELS,
  ROLE_PERMISSION_GROUPS,
} from './permissions';

// The permission vocabulary and its presentation (Administration →
// Roles & permissions and → Correction permissions), and the guard that
// nothing outside Administration reads a user, a role or a permission:
// they are configuration only — no screen is hidden or refused by role
// before users can sign in.

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

test('only the Administration users, roles and correction sections use the users and roles API', () => {
  expect(sourcesMatching(/from '[./]*api\/(users|roles)'/)).toEqual([
    'views/administration/CorrectionPermissionsSection.tsx',
    'views/administration/RolesSection.tsx',
    'views/administration/UsersSection.tsx',
    'views/administration/permissions.ts',
  ]);
});

test('nothing outside Administration reads a user, a role or a permission', () => {
  expect(
    sourcesMatching(
      /\b(Permission|PERMISSIONS|PERMISSION_LABELS|CORRECTION_PERMISSIONS|listRoles|listUsers|roleId|roleName)\b/,
    ),
  ).toEqual([
    'api/roles.ts',
    'api/users.ts',
    'views/administration/CorrectionPermissionsSection.tsx',
    'views/administration/RolesSection.tsx',
    'views/administration/UsersSection.tsx',
    'views/administration/permissions.ts',
  ]);
});
