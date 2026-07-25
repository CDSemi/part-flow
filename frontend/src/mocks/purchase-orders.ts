import type { MockPo } from '../views/view-models';

// Scanner-first New PO demo catalog: PN barcode → PartNumber (mock only).
export const MOCK_PN_BARCODES: Record<string, string> = {
  'PF:PN:1014': '0455-20-0118-03',
  'PF:PN:1021': '78-04-0031',
  'PF:PN:1102': '309-127',
  'PF:PN:1033': '214-406',
};

// Editable dates are ISO `YYYY-MM-DD`; the view formats them for display.
export const MOCK_PO_LIST: MockPo[] = [
  {
    po: 'PO-1010',
    received: '2026-07-22',
    due: '2026-08-12',
    dueClass: '',
    status: 'Open',
    preview: '0455-20-0118-03 · 52-09-0114 · 2 more',
    lines: [
      {
        pn: '0455-20-0118-03',
        barcode: 'existing PN · barcode PF:PN:1014',
        type: 'NEW',
        qty: 12,
        due: '2026-08-12',
        job: 'ERP-88411',
        status: 'Saved',
        statusClass: 'saved',
        releasable: true,
      },
      {
        pn: '52-09-0114',
        barcode: 'new PN — barcode created with PN master: PF:PN:1201',
        type: 'NEW',
        qty: 8,
        due: '2026-08-20',
        job: 'ERP-88420',
        status: 'Saved',
        statusClass: 'saved',
        releasable: true,
      },
      {
        // Released production quantity exists for this PoDemand: the line
        // can no longer be removed from Purchase Orders (PROJECT_PROFILE
        // §13 — corrections go through correction/production workflows).
        pn: '2027-60-8114-00',
        barcode: 'existing PN · barcode PF:PN:1044',
        type: 'NEW',
        qty: 6,
        due: '2026-08-05',
        job: 'ERP-88395',
        status: 'Released · QF-0161',
        statusClass: 'released',
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
    received: '2026-07-12',
    due: '2026-07-31',
    dueClass: '',
    status: 'Released',
    preview: '0455-20-0118-03',
    lines: [
      {
        pn: '0455-20-0118-03',
        barcode: 'barcode PF:PN:1014',
        type: 'NEW',
        qty: 12,
        due: '2026-07-31',
        job: 'ERP-88190',
        status: 'Released · QF-0148',
        statusClass: 'released',
      },
    ],
  },
  {
    po: 'PO-1005',
    received: '2026-07-06',
    due: '2026-07-16',
    dueClass: 'late',
    status: 'Released',
    preview: '142-260',
    lines: [
      {
        pn: '142-260',
        barcode: 'barcode PF:PN:1007',
        type: 'NEW',
        qty: 20,
        due: '2026-07-16',
        job: 'ERP-88031',
        status: 'Released · QF-0152',
        statusClass: 'released',
      },
    ],
  },
  {
    po: 'TMP-20260721-0940-REWORK',
    received: '2026-07-21',
    due: '2026-07-21',
    dueClass: 'late',
    status: 'Released',
    internal: true,
    preview: '214-406',
    lines: [
      {
        pn: '214-406',
        barcode: 'barcode PF:PN:1033',
        type: 'REWORK',
        qty: 2,
        due: '2026-07-21',
        job: '— (internal)',
        status: 'Released · QF-0158',
        statusClass: 'released',
      },
    ],
  },
  {
    po: 'PO-0996',
    received: '2026-06-18',
    due: '2026-07-10',
    dueClass: '',
    status: 'Complete',
    preview: '309-127',
    lines: [
      {
        pn: '309-127',
        barcode: 'barcode PF:PN:1102',
        type: 'NEW',
        qty: 50,
        due: '2026-07-10',
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
  '0455-20-0118-03': {
    requested: 12,
    routes: ['Shaft std v2', 'Shaft short v1'],
    activeDistribution: 'Material 8 · Lathe 2 × 4',
  },
  '52-09-0114': {
    requested: 8,
    routes: ['Gear std v1'],
    activeDistribution: null,
  },
};
