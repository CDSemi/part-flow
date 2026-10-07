import { expect, test } from 'vitest';

import type { Permission } from '../api/roles';
import {
  MANAGEMENT_READ_ACCESS,
  MANAGEMENT_WRITE_ACCESS,
  canReadManagementView,
  readableManagementSubviews,
} from './management-access';
import {
  MANAGEMENT_NAV_ORDER,
  MANAGEMENT_SUBVIEWS,
  managementEntrySubview,
  resolvePath,
} from './router-core';
import type { ManagementSubview } from './router-core';

const holding =
  (...keys: Permission[]) =>
  (key: Permission) =>
    keys.includes(key);

test('FM-3: each sub view opens for View production data or a key its actions use (the server read sets)', () => {
  // The same literals as the backend registry test (RA-8).
  expect(MANAGEMENT_READ_ACCESS).toEqual({
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
  });
  for (const subview of MANAGEMENT_SUBVIEWS) {
    expect(MANAGEMENT_READ_ACCESS[subview]).toContain('VIEW_PRODUCTION_DATA');
  }
});

test('FM-3: each sub view names the keys of the changes it hosts', () => {
  expect(MANAGEMENT_WRITE_ACCESS).toEqual({
    'area-board': [],
    'work-orders': [
      'MANAGE_WORK_ORDERS',
      'EDIT_WORK_ORDER_DEMAND',
      'EDIT_WORK_ORDER_ALLOCATION',
    ],
    tracking: ['ASSIGN_ROUTES', 'EDIT_WORK_ORDER_ALLOCATION'],
    priority: ['SET_DEMAND_PRIORITY', 'REORDER_HOT_ITEMS'],
    'planned-routes': ['MANAGE_ROUTE_TEMPLATES'],
    'part-numbers': ['MANAGE_PART_NUMBER_MASTER'],
    machines: ['MANAGE_MACHINES'],
  });
});

test('a sub view opens for any one key of its read set', () => {
  expect(canReadManagementView(holding(), 'work-orders')).toBe(false);
  expect(
    canReadManagementView(holding('EDIT_WORK_ORDER_ALLOCATION'), 'work-orders'),
  ).toBe(true);
  expect(
    canReadManagementView(holding('MANAGE_PART_NUMBER_MASTER'), 'work-orders'),
  ).toBe(false);
  // The seeded Administrator's Management keys (Phase 13 seed).
  const administrator = holding(
    'EDIT_WORK_ORDER_DEMAND',
    'EDIT_WORK_ORDER_ALLOCATION',
    'MANAGE_MACHINES',
    'MANAGE_ROUTE_TEMPLATES',
    'MANAGE_PART_NUMBER_MASTER',
  );
  expect([...readableManagementSubviews(administrator)].sort()).toEqual([
    'machines',
    'part-numbers',
    'planned-routes',
    'tracking',
    'work-orders',
  ]);
  expect(readableManagementSubviews(holding()).size).toBe(0);
});

test('FM-2: the bar order is the approved sub-view order', () => {
  expect(MANAGEMENT_NAV_ORDER).toEqual([
    'area-board',
    'work-orders',
    'tracking',
    'priority',
    'planned-routes',
    'part-numbers',
    'machines',
  ]);
});

test('FM-2: the Management entry is the last-used sub view when it may be opened, else the first one in the bar', () => {
  const set = (...subviews: ManagementSubview[]) => new Set(subviews);
  // Nobody known to be signed in: the last-used one, as before.
  expect(managementEntrySubview('priority', null)).toBe('priority');
  // The last-used one may be opened.
  expect(managementEntrySubview('priority', set('machines', 'priority'))).toBe(
    'priority',
  );
  // Otherwise the first one in bar order (not the set's order).
  expect(
    managementEntrySubview('area-board', set('machines', 'work-orders')),
  ).toBe('work-orders');
  // None may be opened: the last-used one (its access panel shows).
  expect(managementEntrySubview('tracking', set())).toBe('tracking');
});

test('FM-2: only bare /management follows the readable set — a deep link never redirects', () => {
  const readable = new Set<ManagementSubview>(['work-orders', 'machines']);
  expect(resolvePath('/management', 'area-board')).toEqual({
    redirect: '/management/area-board',
  });
  expect(resolvePath('/management', 'area-board', null)).toEqual({
    redirect: '/management/area-board',
  });
  expect(resolvePath('/management', 'area-board', readable)).toEqual({
    redirect: '/management/work-orders',
  });
  expect(resolvePath('/management/area-board', 'area-board', readable)).toEqual(
    { view: 'management', subview: 'area-board' },
  );
  expect(
    resolvePath('/management/work-orders/completed', 'machines', new Set()),
  ).toEqual({ view: 'management', subview: 'work-orders', page: 'completed' });
});
