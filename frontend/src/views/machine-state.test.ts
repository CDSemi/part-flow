import { expect, test } from 'vitest';

import {
  effectiveMachineStatus,
  formatStateAge,
  machineAssignedQty,
  machineAssignments,
  toAreaMachine,
} from './machine-state';
import type { MockAreaCard, MockMachine } from './view-models';

// Machine operational-state rules (§5 Machine lifecycle/state):
// maintenance is an explicit override; otherwise running/idle are
// DERIVED from the assigned quantity — never chosen by a user.

const CARDS: MockAreaCard[] = [
  {
    area: 'lathe',
    pn: 'PN-1',
    workOrder: 'WO 1 · Turning',
    job: '1',
    qty: 6,
    machines: [
      ['Lathe 2', 3],
      ['queue', 2],
    ],
    finished: [{ qty: 1, completedBy: 'Lathe 2' }],
    due: null,
    enteredAreaAt: '2026-08-01T06:00:00.000Z',
    received: '2026-08-01',
  },
  {
    area: 'lathe',
    pn: 'PN-2',
    workOrder: 'WO 2 · Turning',
    job: '2',
    qty: 2,
    machines: [['Lathe 2', 2]],
    due: null,
    enteredAreaAt: '2026-08-01T06:00:00.000Z',
    received: '2026-08-01',
  },
  {
    // Same Machine display name in ANOTHER Area must never count.
    area: 'mill',
    pn: 'PN-3',
    workOrder: 'WO 3 · Milling',
    job: '3',
    qty: 4,
    machines: [['Lathe 2', 4]],
    due: null,
    enteredAreaAt: '2026-08-01T06:00:00.000Z',
    received: '2026-08-01',
  },
];

const machine = (over: Partial<MockMachine>): MockMachine => ({
  id: 'MC-1',
  area: 'lathe',
  name: 'Lathe 2',
  barcode: 'CD-0105',
  assetTag: 'CD-0105',
  stateChangedAt: '2026-08-04T10:00:00.000Z',
  ...over,
});

test('assigned quantity derives from active Machine portions in the Machine Area only', () => {
  expect(machineAssignedQty(CARDS, machine({}))).toBe(5);
  expect(machineAssignments(CARDS, machine({}))).toEqual([
    { pn: 'PN-1', qty: 3 },
    { pn: 'PN-2', qty: 2 },
  ]);
  // Queue, vendor and finished portions never count as assigned.
  expect(machineAssignedQty(CARDS, machine({ name: 'Lathe 9' }))).toBe(0);
});

test('running and idle are derived; maintenance is an explicit override', () => {
  expect(effectiveMachineStatus(machine({}), 5)).toBe('running');
  expect(effectiveMachineStatus(machine({}), 0)).toBe('idle');
  // Maintenance wins even while quantity stays assigned — starting
  // maintenance never moves or releases quantity.
  expect(
    effectiveMachineStatus(
      machine({ maintenance: { since: '2026-08-04T08:00:00.000Z' } }),
      5,
    ),
  ).toBe('maintenance');
});

test('state age formats from the shared timestamp in compact duration language', () => {
  const now = Date.parse('2026-08-04T12:00:00.000Z');
  const at = (iso: string) => formatStateAge(iso, now);
  expect(at('2026-08-04T11:42:00.000Z')).toBe('18m');
  expect(at('2026-08-04T10:36:00.000Z')).toBe('1h 24m');
  expect(at('2026-08-02T09:00:00.000Z')).toBe('2d 03h');
  // Sub-minute and future timestamps clamp instead of going negative.
  expect(at('2026-08-04T11:59:30.000Z')).toBe('<1m');
  expect(at('2026-08-04T12:05:00.000Z')).toBe('<1m');
});

test('the monitoring-card projection carries state, age source and maintenance context', () => {
  const projected = toAreaMachine(
    machine({
      maintenance: {
        since: '2026-08-04T08:00:00.000Z',
        note: 'Spindle bearing replacement',
        expectedReturn: '2026-08-06',
      },
    }),
    0,
  );
  expect(projected).toEqual({
    name: 'Lathe 2',
    status: 'maintenance',
    stateChangedAt: '2026-08-04T10:00:00.000Z',
    maintenanceNote: 'Spindle bearing replacement',
    expectedReturn: '2026-08-06',
  });
});
