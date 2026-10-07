import { expect, test } from 'vitest';

import type {
  AuditTrailEntry,
  AuditTrailField,
  AuditTrailKind,
  AuditTrailRouteStep,
} from '../../api/audit-trail';
import {
  allocationText,
  changeLines,
  completionText,
  fieldLabel,
  kindLabel,
  priorityText,
  reasonText,
  routeLines,
  subjectText,
  valueText,
} from './audit-trail-text';

// FT-2: the audit trail copy (GUI_DESIGN §7.4; Phase 14 slice 7) —
// every kind label, subject, field label and value in user language
// (no field identifiers), the Hot list cause of a priority change, the
// Work Order completion cause, the allocation facts and the replaced /
// new route steps.

const NO_SUBJECT = {
  workOrderId: null,
  workOrderNumber: null,
  workOrderDemandId: null,
  demandExists: null,
  quantityFlowId: null,
};

function entry(extra: Partial<AuditTrailEntry> = {}): AuditTrailEntry {
  return {
    source: 'AUDIT',
    id: 1,
    occurredAt: '2030-07-23T08:00:00Z',
    kind: 'PART_NUMBER_UPDATED',
    actorUser: null,
    legacyActor: null,
    reason: null,
    subject: NO_SUBJECT,
    changes: [],
    priority: null,
    completionTrigger: null,
    allocation: null,
    route: null,
    ...extra,
  };
}

const LATHE = { id: 2, name: 'Lathe', color: null, isTerminal: false };

function step(extra: Partial<AuditTrailRouteStep> = {}): AuditTrailRouteStep {
  return {
    sequence: 3,
    area: LATHE,
    operation: { id: 21, code: 'OP10', name: null, isExternal: false },
    expectedDuration: null,
    preferredMachine: null,
    instructions: null,
    ...extra,
  };
}

test('every kind has its label', () => {
  const labels: [AuditTrailKind, string][] = [
    ['PART_NUMBER_CREATED', 'Part Number details created'],
    ['PART_NUMBER_UPDATED', 'Part Number details edited'],
    ['PART_NUMBER_DELETED', 'Part Number details deleted'],
    ['WORK_ORDER_CREATED', 'Work Order created'],
    ['WORK_ORDER_UPDATED', 'Work Order edited'],
    ['WORK_ORDER_COMPLETED', 'Work Order completed'],
    ['DEMAND_CREATED', 'Demand line added'],
    ['DEMAND_UPDATED', 'Demand line edited'],
    ['PRIORITY_CHANGED', 'Priority changed'],
    ['ROUTE_ADJUSTED', 'Route adjusted'],
    ['ALLOCATED', 'Allocated from stock'],
    ['ALLOCATION_REVERSED', 'Allocation reversed'],
    ['ALLOCATED_BEYOND_DEMAND', 'Allocated beyond demand — correction'],
    ['CHANGE_RECORDED', 'Change recorded'],
  ];
  for (const [kind, label] of labels) {
    expect(kindLabel(entry({ kind }))).toBe(label);
  }
});

test('an image entry says whether the image was added, replaced or removed — and renders no change line', () => {
  const image = (before: boolean, after: boolean) =>
    entry({
      kind: 'PART_NUMBER_IMAGE_CHANGED',
      changes: [{ field: 'image', before, after }],
    });
  expect(kindLabel(image(false, true))).toBe('Part Number image added');
  expect(kindLabel(image(true, true))).toBe('Part Number image replaced');
  expect(kindLabel(image(true, false))).toBe('Part Number image removed');
  expect(changeLines(image(false, true))).toEqual([]);
});

test('a Work Order completion names its cause; no change line', () => {
  const completed = (completionTrigger: string | null) =>
    entry({ kind: 'WORK_ORDER_COMPLETED', completionTrigger });
  expect(kindLabel(completed('WORK_ORDER_SAVE'))).toBe(
    'Work Order completed (after a Work Order save)',
  );
  expect(kindLabel(completed('DEMAND_LINE_REMOVAL'))).toBe(
    'Work Order completed (after a demand line deletion)',
  );
  expect(changeLines(completed('WORK_ORDER_SAVE'))).toEqual([]);
  expect(completionText(null)).toBe('');
  expect(completionText('WORK_ORDER_SAVE')).toBe(' (after a Work Order save)');
  expect(completionText('DEMAND_LINE_REMOVAL')).toBe(
    ' (after a demand line deletion)',
  );
  expect(completionText('ERP_SYNC')).toBe(' (after another change)');
});

test('the subject names the Work Order, the demand line (deleted since) or the Quantity Flow', () => {
  expect(subjectText(NO_SUBJECT)).toBe('');
  expect(
    subjectText({ ...NO_SUBJECT, workOrderId: 4, workOrderNumber: '007001' }),
  ).toBe('WO 007001');
  expect(subjectText({ ...NO_SUBJECT, workOrderId: 5 })).toBe('WO —');
  const line = {
    ...NO_SUBJECT,
    workOrderId: 4,
    workOrderNumber: '007001',
    workOrderDemandId: 40,
    demandExists: true,
  };
  expect(subjectText(line)).toBe('WO 007001 · demand line');
  expect(subjectText({ ...line, demandExists: false })).toBe(
    'WO 007001 · demand line (since deleted)',
  );
  expect(subjectText({ ...NO_SUBJECT, quantityFlowId: 140 })).toBe(
    'Quantity Flow QF-140',
  );
});

test('every field has its user-language label', () => {
  const labels: [AuditTrailField, string][] = [
    ['name', 'Name'],
    ['currentRevision', 'Revision'],
    ['erpId', 'ERP id'],
    ['image', 'Image'],
    ['workOrderNumber', 'Work Order Number'],
    ['receivedDate', 'Received date'],
    ['dueDate', 'Due date'],
    ['status', 'Status'],
    ['requestType', 'Request Type'],
    ['requestedQuantity', 'Requested quantity'],
    ['jobNumbers', 'Job Numbers'],
    ['requester', 'Requester'],
    ['reason', 'Reason'],
    ['notes', 'Notes'],
    ['priorityRank', 'Hot rank'],
  ];
  for (const [field, label] of labels) expect(fieldLabel(field)).toBe(label);
});

test('values read in user language, nulls included', () => {
  expect(valueText('dueDate', null)).toBe('No due date');
  expect(valueText('priorityRank', null)).toBe('Not listed');
  expect(valueText('name', null)).toBe('—');
  expect(valueText('dueDate', '2030-07-24')).toBe('Jul 24');
  expect(valueText('receivedDate', '2030-07-01')).toBe('Jul 01');
  expect(valueText('requestedQuantity', 12)).toBe('12 pcs');
  expect(valueText('jobNumbers', ['18112', '18113'])).toBe('18112, 18113');
  expect(valueText('jobNumbers', [])).toBe('—');
  expect(valueText('priorityRank', 3)).toBe('#3');
  expect(valueText('image', true)).toBe('custom image');
  expect(valueText('image', false)).toBe('default image');
  expect(valueText('requestType', 'MODIFY')).toBe('MODIFY');
  expect(valueText('status', 'OPEN')).toBe('OPEN');
});

test('change lines: before → after, created values alone, deleted values alone', () => {
  expect(
    changeLines(
      entry({
        kind: 'DEMAND_UPDATED',
        changes: [
          { field: 'requestedQuantity', before: 10, after: 12 },
          { field: 'dueDate', before: null, after: '2030-07-24' },
        ],
      }),
    ),
  ).toEqual([
    'Requested quantity: 10 pcs → 12 pcs',
    'Due date: No due date → Jul 24',
  ]);
  expect(
    changeLines(
      entry({
        kind: 'DEMAND_CREATED',
        changes: [
          { field: 'requestType', before: null, after: 'NEW' },
          { field: 'requestedQuantity', before: null, after: 10 },
        ],
      }),
    ),
  ).toEqual(['Request Type: NEW', 'Requested quantity: 10 pcs']);
  expect(
    changeLines(
      entry({
        kind: 'PART_NUMBER_DELETED',
        changes: [
          { field: 'name', before: 'BRACKET', after: null },
          { field: 'image', before: true, after: null },
        ],
      }),
    ),
  ).toEqual(['Name: BRACKET', 'Image: custom image']);
});

test('the Hot rank line carries the Hot list cause', () => {
  const priority = (
    action: string | null,
    removalReason: string | null,
    trigger: string | null,
  ) => ({ action, removalReason, trigger });
  expect(priorityText(priority('ADD', null, null))).toBe(' · Hot list: added');
  const actions: [string, string][] = [
    ['REMOVE', 'removed'],
    ['MOVE_UP', 'moved up'],
    ['MOVE_DOWN', 'moved down'],
    ['DRAG', 'dragged to a new position'],
    ['UNDO', 'previous ranking restored'],
    ['REDO', 'ranking reapplied'],
    ['AUTO_REMOVE', 'removed automatically'],
    ['LINE_DELETE', 'demand line deleted'],
    ['SOMETHING_NEW', 'changed'],
  ];
  for (const [action, text] of actions) {
    expect(priorityText(priority(action, null, null))).toBe(
      ` · Hot list: ${text}`,
    );
  }
  expect(priorityText(priority(null, null, null))).toBe(' · Hot list: changed');
  expect(
    priorityText(priority('AUTO_REMOVE', 'FULLY_ALLOCATED', 'ALLOCATION')),
  ).toBe(
    ' · Hot list: removed automatically — fully allocated (after an allocation)',
  );
  expect(
    priorityText(
      priority('AUTO_REMOVE', 'WORK_ORDER_COMPLETED', 'WORK_ORDER_SAVE'),
    ),
  ).toBe(
    ' · Hot list: removed automatically — Work Order completed (after a Work Order save)',
  );
  expect(
    priorityText(
      priority('LINE_DELETE', 'LINE_DELETED', 'DEMAND_LINE_REMOVAL'),
    ),
  ).toBe(
    ' · Hot list: demand line deleted — line deleted (after a demand line deletion)',
  );
  expect(priorityText(priority('AUTO_REMOVE', 'OTHER', 'OTHER'))).toBe(
    ' · Hot list: removed automatically — no longer active (after another change)',
  );
  // A shifted line: the cause without a removal reason.
  expect(priorityText(priority('AUTO_REMOVE', null, 'ALLOCATION'))).toBe(
    ' · Hot list: removed automatically (after an allocation)',
  );

  expect(
    changeLines(
      entry({
        kind: 'PRIORITY_CHANGED',
        changes: [{ field: 'priorityRank', before: null, after: 3 }],
        priority: priority('ADD', null, null),
      }),
    ),
  ).toEqual(['Hot rank: Not listed → #3 · Hot list: added']);
});

test('the allocation facts', () => {
  const subject = { ...NO_SUBJECT, workOrderId: 4, workOrderNumber: '007001' };
  expect(
    allocationText(
      {
        quantity: 2,
        source: 'MANAGEMENT',
        isManualOverride: true,
        exceedsDemand: true,
        reversesAllocationId: null,
        stationId: null,
      },
      subject,
    ),
  ).toBe('2 pcs · WO 007001 · beyond demand · manual override');
  expect(
    allocationText(
      {
        quantity: 10,
        source: 'STOCKROOM',
        isManualOverride: false,
        exceedsDemand: false,
        reversesAllocationId: 31,
        stationId: 'STOCK-ST-1',
      },
      { ...subject, workOrderNumber: null },
    ),
  ).toBe('10 pcs · WO — · reverses allocation #31 · STOCK-ST-1');
});

test('route lines list the replaced and the new steps with every recorded part', () => {
  expect(
    routeLines({
      keptThroughSequence: 2,
      beforeSteps: [
        step({
          operation: {
            id: 22,
            code: 'TURN',
            name: 'Turning',
            isExternal: false,
          },
          expectedDuration: 'PT4H',
          preferredMachine: { id: 201, name: 'Lathe 1' },
          instructions: 'Check runout',
        }),
        step({ sequence: 4, operation: null }),
      ],
      afterSteps: [],
    }),
  ).toEqual([
    'Steps after step 2 — before:',
    '3. Lathe · Turning · Est. 4h 00m · Machine Lathe 1 · Instructions: Check runout',
    '4. Lathe',
    'Steps after step 2 — now:',
    'no further steps',
  ]);
});

test('a duration-only adjustment shows the different estimate on each side', () => {
  expect(
    routeLines({
      keptThroughSequence: 2,
      beforeSteps: [step({ expectedDuration: 'PT45M' })],
      afterSteps: [step({ expectedDuration: 'PT1H30M' })],
    }),
  ).toEqual([
    'Steps after step 2 — before:',
    '3. Lathe · OP10 · Est. 45m',
    'Steps after step 2 — now:',
    '3. Lathe · OP10 · Est. 1h 30m',
  ]);
});

test('the reason line', () => {
  expect(reasonText('Lathe 2 down')).toBe('Reason: Lathe 2 down');
});
