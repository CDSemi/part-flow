import type { MockHotEntry } from './types';

export const MOCK_HOT_LIST: MockHotEntry[] = [
  {
    pn: 'PF-BRACKET-001',
    po: 'PO-1001 · ERP-88112',
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
    pn: 'PF-PLATE-007',
    po: 'PO-1005 · ERP-88031',
    type: 'NEW',
    figures: ['requested 20', 'allocated 0', 'shortage 20', 'at External 20'],
    due: 'Jul 16',
    dueNote: 'overdue 6 days',
    dueClass: 'late',
  },
  {
    pn: 'PF-PIN-102',
    po: 'PO-1006 · ERP-88355',
    type: 'REWORK',
    figures: ['requested 12', 'allocated 0', 'shortage 12', 'not yet released'],
    due: 'Jul 25',
    dueNote: '3 days left',
    dueClass: 'soon',
  },
];

export const MOCK_HOT_CANDIDATES: MockHotEntry[] = [
  {
    pn: 'PF-SHAFT-014',
    po: 'PO-1003 · ERP-88190',
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
    pn: 'PF-HOUSING-021',
    po: 'PO-1002 · ERP-88102',
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
    pn: 'PF-VALVE-033',
    po: 'TMP-20260721-0940-REWORK',
    type: 'REWORK',
    figures: ['requested 2', 'allocated 0', 'shortage 2', 'at Lathe queue 2'],
    due: 'Jul 21',
    dueNote: 'overdue 1 day',
    dueClass: 'late',
    barcode: 'PF:PN:1033',
  },
];
