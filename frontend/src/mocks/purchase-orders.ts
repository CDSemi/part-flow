import type { MockPo } from './types';

// Scanner-first New PO demo catalog: PN barcode → PartNumber (mock only).
export const MOCK_PN_BARCODES: Record<string, string> = {
  'PF:PN:1014': 'PF-SHAFT-014',
  'PF:PN:1021': 'PF-HOUSING-021',
  'PF:PN:1102': 'PF-PIN-102',
  'PF:PN:1033': 'PF-VALVE-033',
};

export const MOCK_PO_LIST: MockPo[] = [
  {
    po: 'PO-1010',
    received: 'Jul 22, 2026',
    due: 'Aug 12, 2026',
    dueClass: '',
    status: 'Open',
    preview: 'PF-SHAFT-014 · PF-GEAR-201 · 1 more',
    lines: [
      {
        pn: 'PF-SHAFT-014',
        barcode: 'existing PN · barcode PF:PN:1014',
        type: 'NEW',
        qty: 12,
        due: 'Aug 12',
        job: 'ERP-88411',
        status: 'Saved',
        statusClass: 'saved',
        releasable: true,
      },
      {
        pn: 'PF-GEAR-201',
        barcode: 'new PN — barcode created with PN master: PF:PN:1201',
        type: 'NEW',
        qty: 8,
        due: 'Aug 20',
        job: 'ERP-88420',
        status: 'Saved',
        statusClass: 'saved',
        releasable: true,
      },
      {
        pn: '',
        barcode: 'PN lookup or inline create required',
        type: 'REWORK',
        qty: 0,
        due: '',
        job: '',
        status: 'Row invalid',
        statusClass: 'invalid',
      },
    ],
  },
  {
    po: 'PO-1003',
    received: 'Jul 12, 2026',
    due: 'Jul 31, 2026',
    dueClass: '',
    status: 'Released',
    preview: 'PF-SHAFT-014',
    lines: [
      {
        pn: 'PF-SHAFT-014',
        barcode: 'barcode PF:PN:1014',
        type: 'NEW',
        qty: 12,
        due: 'Jul 31, 2026',
        job: 'ERP-88190',
        status: 'Released · QF-0148',
        statusClass: 'released',
      },
    ],
  },
  {
    po: 'PO-1005',
    received: 'Jul 06, 2026',
    due: 'Jul 16, 2026',
    dueClass: 'late',
    status: 'Released',
    preview: 'PF-PLATE-007',
    lines: [
      {
        pn: 'PF-PLATE-007',
        barcode: 'barcode PF:PN:1007',
        type: 'NEW',
        qty: 20,
        due: 'Jul 16, 2026',
        job: 'ERP-88031',
        status: 'Released · QF-0152',
        statusClass: 'released',
      },
    ],
  },
  {
    po: 'TMP-20260721-0940-REWORK',
    received: 'Jul 21, 2026',
    due: 'Jul 21, 2026',
    dueClass: 'late',
    status: 'Released',
    internal: true,
    preview: 'PF-VALVE-033',
    lines: [
      {
        pn: 'PF-VALVE-033',
        barcode: 'barcode PF:PN:1033',
        type: 'REWORK',
        qty: 2,
        due: 'Jul 21, 2026',
        job: '— (internal)',
        status: 'Released · QF-0158',
        statusClass: 'released',
      },
    ],
  },
  {
    po: 'PO-0996',
    received: 'Jun 18, 2026',
    due: 'Jul 10, 2026',
    dueClass: '',
    status: 'Complete',
    preview: 'PF-PIN-102',
    lines: [
      {
        pn: 'PF-PIN-102',
        barcode: 'barcode PF:PN:1102',
        type: 'NEW',
        qty: 50,
        due: 'Jul 10, 2026',
        job: 'ERP-87740',
        status: 'Allocated 50 / 50',
        statusClass: 'released',
      },
    ],
  },
];

// Release-dialog demo data (presentation only — no release is performed).
export const MOCK_RELEASE_DATA: Record<
  string,
  { requested: number; routes: string[]; activeDistribution: string | null }
> = {
  'PF-SHAFT-014': {
    requested: 12,
    routes: ['Shaft std v2', 'Shaft short v1'],
    activeDistribution: 'Material 8 · Lathe 2 × 4',
  },
  'PF-GEAR-201': {
    requested: 8,
    routes: ['Gear std v1'],
    activeDistribution: null,
  },
};
