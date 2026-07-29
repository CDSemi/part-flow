import type { MockWorkOrder } from '../views/view-models';

// Existing PartNumber catalog for the Add Part search step and the
// optional barcode entry path (mock only). PN barcodes carry the PN
// itself — `PF:PN:<part-number>` — never an opaque stable id; PN
// lookup is case-insensitive while the stored casing is preserved.
export const MOCK_PN_CATALOG: { pn: string; name: string; barcode: string }[] =
  [
    {
      pn: '0455-20-0118-03',
      name: 'SHAFT, DRIVE 0.750 DIA X 12.500, 17-4PH H900',
      barcode: 'PF:PN:0455-20-0118-03',
    },
    {
      pn: '78-04-0031',
      name: 'HOUSING, BEARING CAST AL 356-T6, MACHINED',
      barcode: 'PF:PN:78-04-0031',
    },
    {
      pn: '309-127',
      name: 'PIN, DOWEL 1/4 X 1.00 SS',
      barcode: 'PF:PN:309-127',
    },
    {
      pn: '214-406',
      name: 'SPACER, THREADED 10-32, BRASS',
      barcode: 'PF:PN:214-406',
    },
    {
      pn: '118-052',
      name: 'MOTOR, GEAR STEPPER 7.2T',
      barcode: 'PF:PN:118-052',
    },
    {
      pn: '2027-60-8114-00',
      name: 'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125',
      barcode: 'PF:PN:2027-60-8114-00',
    },
    {
      pn: '142-260',
      name: 'PLATE, TOP COVER ALUM 6061-T6',
      barcode: 'PF:PN:142-260',
    },
  ];

/** Case-insensitive catalog lookup — display casing stays as stored. */
export function catalogPartNumber(
  pn: string,
): { pn: string; name: string; barcode: string } | undefined {
  const key = pn.trim().toLowerCase();
  return MOCK_PN_CATALOG.find((entry) => entry.pn.toLowerCase() === key);
}

// Editable dates are ISO `YYYY-MM-DD`; the view formats them for
// display. `due: null` is valid — both a Work Order and its demand
// lines may have no due date. `workOrderNumber: null` is valid too:
// an internal Work Order without an external number displays as `—`
// (the placeholder is never persisted); `id` is the stable internal
// identity. Entered Work Order Numbers are opaque external strings —
// typically numeric-looking (e.g. `007010`), never parsed or padded.
export const MOCK_WORK_ORDER_LIST: MockWorkOrder[] = [
  {
    id: 'wo-007010',
    workOrderNumber: '007010',
    received: '2026-07-22',
    due: '2026-08-12',
    dueClass: '',
    status: 'Open',
    preview: '0455-20-0118-03 · 52-09-0114 · 2 more',
    lines: [
      {
        pn: '0455-20-0118-03',
        barcode: 'existing PN · barcode PF:PN:0455-20-0118-03',
        type: 'NEW',
        qty: 12,
        due: '2026-08-12',
        job: '18411',
        status: 'Saved',
        statusClass: 'saved',
        releasable: true,
      },
      {
        pn: '52-09-0114',
        barcode: 'new PN — barcode created with PN master: PF:PN:52-09-0114',
        type: 'NEW',
        qty: 8,
        due: '2026-08-20',
        job: '18420',
        status: 'Saved',
        statusClass: 'saved',
        releasable: true,
      },
      {
        // Released production quantity exists for this WorkOrderDemand:
        // the line can no longer be removed from Work Orders
        // (PROJECT_PROFILE §13 — corrections go through correction/
        // production workflows).
        pn: '2027-60-8114-00',
        barcode: 'existing PN · barcode PF:PN:2027-60-8114-00',
        type: 'NEW',
        qty: 6,
        due: '2026-08-05',
        job: '18395',
        status: 'Released · QF-0161',
        statusClass: 'released',
      },
      {
        pn: '',
        barcode: 'PN lookup or inline create required',
        type: 'MODIFY',
        qty: 0,
        due: null,
        job: '',
        status: 'Row invalid',
        statusClass: 'invalid',
      },
    ],
  },
  {
    id: 'wo-007003',
    workOrderNumber: '007003',
    received: '2026-07-12',
    due: '2026-07-31',
    dueClass: '',
    status: 'Released',
    preview: '0455-20-0118-03',
    lines: [
      {
        pn: '0455-20-0118-03',
        barcode: 'barcode PF:PN:0455-20-0118-03',
        type: 'NEW',
        qty: 12,
        due: '2026-07-31',
        job: '18190',
        status: 'Released · QF-0148',
        statusClass: 'released',
      },
    ],
  },
  {
    id: 'wo-007005',
    workOrderNumber: '007005',
    received: '2026-07-06',
    due: '2026-07-16',
    dueClass: 'late',
    status: 'Released',
    preview: '142-260',
    lines: [
      {
        pn: '142-260',
        barcode: 'barcode PF:PN:142-260',
        type: 'NEW',
        qty: 20,
        due: '2026-07-16',
        job: '18031',
        status: 'Released · QF-0152',
        statusClass: 'released',
      },
    ],
  },
  {
    // Work Order without a due date: it stays unscheduled until one is
    // added; its undated demand orders by the received date.
    id: 'wo-007011',
    workOrderNumber: '007011',
    received: '2026-07-19',
    due: null,
    dueClass: '',
    status: 'Released',
    preview: '118-052',
    lines: [
      {
        pn: '118-052',
        barcode: 'barcode PF:PN:118-052',
        type: 'NEW',
        qty: 4,
        due: null,
        job: '18520',
        status: 'Released · QF-0163',
        statusClass: 'released',
      },
    ],
  },
  {
    // Internal MODIFY Work Order without an external number: created by
    // a Scan Station MODIFY intake. The blank number displays as `—`;
    // a real external number may be added later through an audited
    // edit. No temporary number is ever generated.
    id: 'wo-int-0007',
    workOrderNumber: null,
    received: '2026-07-21',
    due: '2026-07-21',
    dueClass: 'late',
    status: 'Released',
    internal: true,
    preview: '214-406',
    lines: [
      {
        pn: '214-406',
        barcode: 'barcode PF:PN:214-406',
        type: 'MODIFY',
        qty: 2,
        due: '2026-07-21',
        job: '— (internal)',
        status: 'Released · QF-0158',
        statusClass: 'released',
      },
    ],
  },
  {
    id: 'wo-006996',
    workOrderNumber: '006996',
    received: '2026-06-18',
    due: '2026-07-10',
    dueClass: '',
    status: 'Complete',
    preview: '309-127',
    lines: [
      {
        pn: '309-127',
        barcode: 'barcode PF:PN:309-127',
        type: 'NEW',
        qty: 50,
        due: '2026-07-10',
        job: '17740',
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
    routes: ['Floating Route (default)', 'Shaft std v2', 'Shaft short v1'],
    activeDistribution: 'Material 8 · Lathe 2 × 4',
  },
  '52-09-0114': {
    requested: 8,
    routes: ['Floating Route (default)', 'Gear std v1'],
    activeDistribution: null,
  },
};
