import type { MockWorkOrder } from '../views/view-models';

import { isoDateIn } from './mock-time';

// Existing PartNumber master-metadata catalog for the Add Part search
// step and the optional barcode entry path (mock only). PN barcodes
// carry the PN itself — `PF:PN:<part-number>` — never an opaque stable
// id; catalog PNs are canonical (uppercase, whitespace-free), so the
// PN string itself is the identity and lookup is direct equality.
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

/** Catalog lookup by canonical PN (the PN string is the identity). */
export function catalogPartNumber(
  pn: string,
): { pn: string; name: string; barcode: string } | undefined {
  return MOCK_PN_CATALOG.find((entry) => entry.pn === pn);
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
        barcode: 'new PN — barcode PF:PN:52-09-0114',
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
];

// ====================================================================
// Completed Work Orders — the permanent read-only history behind
// `/management/work-orders/completed` (GUI_DESIGN §11.5). A completed
// Work Order (every demand fully allocated) never appears in the
// active list above. `done` is the ISO done-date stand-in for
// `completed_at` (PROJECT_PROFILE §8.2). The dataset is deliberately
// LARGE (curated examples + a deterministic generated tail) so the
// page's server-side contract — done-range default, keyset-style
// `Show more` paging, search across years of history — is really
// exercised in development.
// ====================================================================

/** Deterministic pseudo-random stream (LCG) — the generated history
 * must be identical on every load and in every test run. */
function lcg(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0xffffffff;
  };
}

const COMPLETED_CURATED: MockWorkOrder[] = [
  {
    // Recently completed, on time (done before due) — the §11.5
    // `✓ On time` outcome presentation.
    id: 'wo-006996',
    workOrderNumber: '006996',
    received: isoDateIn(-56),
    due: isoDateIn(-34),
    status: 'Complete',
    done: isoDateIn(-36),
    preview: '309-127',
    lines: [
      {
        pn: '309-127',
        barcode: 'barcode PF:PN:309-127',
        type: 'NEW',
        qty: 50,
        due: isoDateIn(-34),
        job: '17740',
        status: 'Allocated 50 / 50',
        statusClass: 'released',
      },
    ],
  },
  {
    // Completed late — the `✕ N days late` outcome.
    id: 'wo-006990',
    workOrderNumber: '006990',
    received: isoDateIn(-80),
    due: isoDateIn(-45),
    status: 'Complete',
    done: isoDateIn(-40),
    preview: '142-260',
    lines: [
      {
        pn: '142-260',
        barcode: 'barcode PF:PN:142-260',
        type: 'NEW',
        qty: 16,
        due: isoDateIn(-45),
        job: '17561',
        status: 'Allocated 16 / 16',
        statusClass: 'released',
      },
    ],
  },
  {
    // Completed internal MODIFY Work Order without an external number
    // and without a due date (`—` outcome).
    id: 'wo-int-0003',
    workOrderNumber: null,
    received: isoDateIn(-20),
    due: null,
    status: 'Complete',
    done: isoDateIn(-12),
    internal: true,
    preview: '214-406',
    lines: [
      {
        pn: '214-406',
        barcode: 'barcode PF:PN:214-406',
        type: 'MODIFY',
        qty: 3,
        due: null,
        job: '— (internal)',
        status: 'Allocated 3 / 3',
        statusClass: 'released',
      },
    ],
  },
];

/** Generated completed history: ~180 Work Orders whose done dates
 * spread from days to roughly two years back, with a realistic mix of
 * on-time / late / no-due-date outcomes. */
function generateCompletedHistory(): MockWorkOrder[] {
  const random = lcg(0x9e3779b9);
  const catalog = MOCK_PN_CATALOG;
  return Array.from({ length: 180 }, (_, i): MockWorkOrder => {
    const doneDaysAgo = 2 + i * 4 + Math.floor(random() * 4);
    const leadDays = 10 + Math.floor(random() * 40);
    const hasDue = random() > 1 / 6;
    // Done vs due mix: mostly on time (due after done), a visible
    // late share (due before done).
    const dueOffset =
      random() < 0.3
        ? 2 + Math.floor(random() * 12)
        : -(1 + Math.floor(random() * 9));
    const entry = catalog[i % catalog.length];
    const qty = 1 + Math.floor(random() * 60);
    const number = String(6900 - i).padStart(6, '0');
    const done = isoDateIn(-doneDaysAgo);
    const received = isoDateIn(-(doneDaysAgo + leadDays));
    const due = hasDue ? isoDateIn(-(doneDaysAgo + dueOffset)) : null;
    return {
      id: `wo-hist-${number}`,
      workOrderNumber: number,
      received,
      due,
      status: 'Complete',
      done,
      preview: entry.pn,
      lines: [
        {
          pn: entry.pn,
          barcode: `barcode ${entry.barcode}`,
          type: 'NEW',
          qty,
          due,
          job: String(17500 - i * 3),
          status: `Allocated ${qty} / ${qty}`,
          statusClass: 'released',
        },
      ],
    };
  });
}

export const MOCK_COMPLETED_WORK_ORDERS: MockWorkOrder[] = [
  ...COMPLETED_CURATED,
  ...generateCompletedHistory(),
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
