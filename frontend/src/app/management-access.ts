// Management access presentation (Phase 14 slice 3): which permission
// keys open each Management sub view and which keys its changes need.
// The server checks every read and write itself (a sub view's reads
// need View production data or a key whose action the view hosts); these
// maps only decide what the UI renders — the sub views listed, the
// access panel, and the hidden write controls with their view-only note.
//
// Pure: no React, no framework imports.

import type { Permission } from '../api/roles';
import { MANAGEMENT_SUBVIEWS } from './router-core';
import type { ManagementSubview } from './router-core';

/** The keys any one of which opens a sub view (its reads). */
export const MANAGEMENT_READ_ACCESS: Readonly<
  Record<ManagementSubview, readonly Permission[]>
> = {
  'area-board': ['VIEW_PRODUCTION_DATA'],
  'work-orders': [
    'VIEW_PRODUCTION_DATA',
    'MANAGE_WORK_ORDERS',
    'EDIT_WORK_ORDER_DEMAND',
    'EDIT_WORK_ORDER_ALLOCATION',
  ],
  tracking: [
    'VIEW_PRODUCTION_DATA',
    'EDIT_WORK_ORDER_ALLOCATION',
    'ASSIGN_ROUTES',
  ],
  priority: [
    'VIEW_PRODUCTION_DATA',
    'SET_DEMAND_PRIORITY',
    'REORDER_HOT_ITEMS',
  ],
  'planned-routes': ['VIEW_PRODUCTION_DATA', 'MANAGE_ROUTE_TEMPLATES'],
  'part-numbers': ['VIEW_PRODUCTION_DATA', 'MANAGE_PART_NUMBER_MASTER'],
  machines: ['VIEW_PRODUCTION_DATA', 'MANAGE_MACHINES'],
};

/** The keys of the changes a sub view hosts (none: a read-only view). */
export const MANAGEMENT_WRITE_ACCESS: Readonly<
  Record<ManagementSubview, readonly Permission[]>
> = {
  'area-board': [],
  'work-orders': [
    'MANAGE_WORK_ORDERS',
    'EDIT_WORK_ORDER_DEMAND',
    'EDIT_WORK_ORDER_ALLOCATION',
  ],
  tracking: ['EDIT_WORK_ORDER_ALLOCATION'],
  priority: ['SET_DEMAND_PRIORITY', 'REORDER_HOT_ITEMS'],
  'planned-routes': ['MANAGE_ROUTE_TEMPLATES'],
  'part-numbers': ['MANAGE_PART_NUMBER_MASTER'],
  machines: ['MANAGE_MACHINES'],
};

type Can = (permission: Permission) => boolean;

/** Whether the user may open the sub view (any key of its read set). */
export function canReadManagementView(
  can: Can,
  subview: ManagementSubview,
): boolean {
  return MANAGEMENT_READ_ACCESS[subview].some((key) => can(key));
}

/** The sub views the user may open. */
export function readableManagementSubviews(
  can: Can,
): ReadonlySet<ManagementSubview> {
  return new Set(
    MANAGEMENT_SUBVIEWS.filter((subview) =>
      canReadManagementView(can, subview),
    ),
  );
}
