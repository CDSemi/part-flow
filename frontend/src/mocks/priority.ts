import type { MockHotEntry } from '../views/view-models';

export const MOCK_HOT_LIST: MockHotEntry[] = [
  {
    pn: '2027-60-8114-00',
    workOrder: 'WO 007001 · Job 18112',
    type: 'NEW',
    figures: [
      'requested 10',
      'allocated 0',
      'shortage 10',
      'at Cut 4 · Lathe 6',
    ],
    due: 'Jul 24',
    dueNote: '2 days left',
    dueClass: 'soon',
  },
  {
    pn: '142-260',
    workOrder: 'WO 007005 · Job 18031',
    type: 'NEW',
    figures: ['requested 20', 'allocated 0', 'shortage 20', 'at External 20'],
    due: 'Jul 16',
    dueNote: 'overdue 6 days',
    dueClass: 'late',
  },
  {
    pn: '309-127',
    workOrder: 'WO 007006 · Job 18355',
    type: 'REWORK',
    figures: ['requested 12', 'allocated 0', 'shortage 12', 'not yet released'],
    due: 'Jul 25',
    dueNote: '3 days left',
    dueClass: 'soon',
  },
];

export const MOCK_HOT_CANDIDATES: MockHotEntry[] = [
  {
    pn: '0455-20-0118-03',
    workOrder: 'WO 007003 · Job 18190',
    type: 'NEW',
    figures: [
      'requested 12',
      'allocated 0',
      'shortage 12',
      'at Material 8 · Lathe 4',
    ],
    due: 'Jul 31',
    dueNote: '9 days left',
    dueClass: 'ok',
    barcode: 'PF:PN:1014',
  },
  {
    pn: '78-04-0031',
    workOrder: 'WO 007002 · Job 18102',
    type: 'NEW',
    figures: [
      'requested 6',
      'allocated 0',
      'shortage 6',
      'at Mill 3 · Deburr 3',
    ],
    due: 'Aug 07',
    dueNote: '16 days left',
    dueClass: 'ok',
    barcode: 'PF:PN:1021',
  },
  {
    pn: '214-406',
    workOrder: 'TMP-20260721-0940-REWORK',
    type: 'REWORK',
    figures: ['requested 2', 'allocated 0', 'shortage 2', 'at Lathe queue 2'],
    due: 'Jul 21',
    dueNote: 'overdue 1 day',
    dueClass: 'late',
    barcode: 'PF:PN:1033',
  },
];
