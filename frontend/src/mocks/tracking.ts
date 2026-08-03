import type { MockTrackingRow } from '../views/view-models';

// `demand.workOrder: '—'` marks internal demand without an external
// Work Order Number (the placeholder is display-only, never persisted).
// `scrappedQty` is the cumulative SCRAPPED quantity of the PN.
export const MOCK_TRACKING_ROWS: MockTrackingRow[] = [
  {
    pn: '2027-60-8114-00',
    name: 'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125, LASER CUT W/ CSK HOLES, DEBURR ALL EDGES',
    hotRank: 1,
    demand: [
      { workOrder: '007001', qty: 10, type: 'NEW' },
      { workOrder: '007008', qty: 5, type: 'NEW' },
    ],
    distribution: [
      { area: 'cut', label: 'Cut', qty: 4 },
      { area: 'lathe', label: 'Lathe', qty: 6 },
    ],
    activeQty: 10,
    stockedQty: 0,
    scrappedQty: 1,
    nextDue: 'Jul 24',
    status: 'Active',
  },
  {
    pn: '142-260',
    name: 'PLATE, TOP COVER ALUM 6061-T6',
    hotRank: 2,
    demand: [{ workOrder: '007005', qty: 20, type: 'NEW' }],
    distribution: [{ area: 'external', label: 'External', qty: 20 }],
    activeQty: 20,
    stockedQty: 0,
    scrappedQty: 2,
    nextDue: 'Jul 16',
    status: 'Active',
  },
  {
    pn: '0455-20-0118-03',
    name: 'SHAFT, DRIVE 0.750 DIA X 12.500, 17-4PH H900, GRIND 32 RA',
    demand: [{ workOrder: '007003', qty: 12, type: 'NEW' }],
    distribution: [
      { area: 'material', label: 'Material', qty: 8 },
      { area: 'lathe', label: 'Lathe', qty: 4 },
    ],
    activeQty: 12,
    stockedQty: 0,
    scrappedQty: 0,
    nextDue: 'Jul 31',
    status: 'Active',
  },
  {
    // Internal MODIFY demand without an external Work Order Number:
    // the blank number renders as `—` everywhere. The 3 pcs at Deburr
    // finished processing there (AREA_COMPLETED → READY_TO_TRANSFER on
    // the finished rack, aligned with the Area Board mock); the compact
    // distribution column shows location only, so no state appears here.
    pn: '78-04-0031',
    name: 'HOUSING, BEARING CAST AL 356-T6, MACHINED',
    demand: [
      { workOrder: '007002', qty: 6, type: 'NEW' },
      { workOrder: '—', qty: 1, type: 'MODIFY' },
    ],
    distribution: [
      { area: 'mill', label: 'Mill', qty: 3 },
      { area: 'deburr', label: 'Deburr', qty: 3 },
    ],
    activeQty: 6,
    stockedQty: 0,
    scrappedQty: 0,
    nextDue: 'Aug 07',
    status: 'Active',
  },
  {
    // WO Demand without a due date: `—` in Due (next) means the demand
    // has no due date — valid data, not missing data.
    pn: '118-052',
    name: 'MOTOR, GEAR STEPPER 7.2T',
    demand: [{ workOrder: '007011', qty: 4, type: 'NEW' }],
    distribution: [{ area: 'manual', label: 'Manual', qty: 4 }],
    activeQty: 4,
    stockedQty: 0,
    scrappedQty: 0,
    nextDue: '—',
    status: 'Active',
  },
  {
    pn: '309-127',
    name: 'PIN, DOWEL 1/4 X 1.00 SS',
    demand: [{ workOrder: '006996', qty: 50, type: 'NEW' }],
    distribution: [{ area: 'stockroom', label: 'Stockroom', qty: 50 }],
    activeQty: 0,
    stockedQty: 50,
    scrappedQty: 0,
    nextDue: '—',
    status: 'Completed',
  },
  {
    // Archived PN: junk/test record soft-deleted by an Administrator.
    // It disappears from active lookup and intake; history keeps the
    // original PN text with an explicit `(archived)` marker.
    pn: 'TEST-SCRAP-PLATE',
    name: 'TEST PLATE — created during scanner testing',
    demand: [],
    distribution: [],
    activeQty: 0,
    stockedQty: 0,
    scrappedQty: 3,
    nextDue: '—',
    status: 'Completed',
    archived: true,
  },
];

export const MOCK_TRACKING_ROWS_LONG: MockTrackingRow[] = [
  {
    pn: '0118-40-0022-07-0455-88-REV-C',
    name: 'MANIFOLD ASSY, 6-PORT ANODIZED, W/ FITTINGS 1/4 NPT, VENDOR SUB-ASSY — long identifier sample',
    demand: [{ workOrder: '007008-SUPPLEMENTAL-B', qty: 8, type: 'NEW' }],
    distribution: [{ area: 'mill', label: 'Mill', qty: 8 }],
    activeQty: 8,
    stockedQty: 0,
    scrappedQty: 0,
    nextDue: 'Jul 25',
    status: 'Active',
  },
  ...MOCK_TRACKING_ROWS,
  // 28 generated rows: the long-data preview renders 30+ rows (§2.3).
  ...Array.from({ length: 28 }, (_, i): MockTrackingRow => {
    const n = i + 1;
    return {
      pn: `0114-60-${String(100 + n).padStart(4, '0')}-00`,
      name: `Spacer, sample lot ${n}, ALUM 6061-T6`,
      demand: [
        {
          workOrder: String(7200 + n).padStart(6, '0'),
          qty: (n % 9) + 1,
          type: 'NEW',
        },
      ],
      distribution: [
        {
          area: n % 2 === 0 ? 'lathe' : 'mill',
          label: n % 2 === 0 ? 'Lathe' : 'Mill',
          qty: (n % 9) + 1,
        },
      ],
      activeQty: (n % 9) + 1,
      stockedQty: 0,
      scrappedQty: 0,
      nextDue: 'Aug 15',
      status: 'Active',
    };
  }),
];

// Detail-panel sample for the selected PN. QF-0140 demonstrates a
// PLANNED route (AssignedRoute snapshot as guidance); QF-0141 is a
// FLOATING route — no AssignedRoute, the actual route trace is derived
// from immutable Movement history, repeated Areas preserved and Repair
// transfers marked explicitly. AREA_COMPLETED never adds a route step:
// DONE is completion inside the source Area (the finished rack is not
// an Area) — only TRANSFERRED extends a trace.
export const MOCK_TRACKING_DETAIL = {
  pn: '2027-60-8114-00',
  name: 'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125, LASER CUT W/ CSK HOLES, DEBURR ALL EDGES',
  revision: 'C',
  barcode: 'PF:PN:2027-60-8114-00',
  erpId: 'ERP-PN-40412',
  demand: [
    {
      workOrder: '007001',
      type: 'NEW' as const,
      requested: 10,
      allocated: 0,
      shortage: 10,
      due: 'Jul 24',
      priority: '🔥#1',
    },
    {
      workOrder: '007008',
      type: 'NEW' as const,
      requested: 5,
      allocated: 0,
      shortage: 5,
      due: 'Aug 02',
      priority: '—',
    },
  ],
  allocationNote: 'Allocated 0 / 15 requested — nothing stocked yet',
  // Current holding state per portion: `machine` = actively assigned,
  // `queue` = waiting for a Machine, `done` = Area completion
  // (AREA_COMPLETED → READY_TO_TRANSFER): the finished piece left
  // Lathe 3 for the finished rack but is still located in the Lathe
  // Area — never Stocked, and no Machine holds it anymore.
  distribution: [
    {
      area: 'cut' as const,
      name: 'Cut',
      sub: 'Saw 1',
      state: 'machine' as const,
      pct: 40,
      qty: 4,
    },
    {
      area: 'lathe' as const,
      name: 'Lathe 3',
      sub: 'machine',
      state: 'machine' as const,
      pct: 30,
      qty: 3,
    },
    {
      area: 'lathe' as const,
      name: 'Lathe',
      sub: 'queue',
      state: 'queue' as const,
      pct: 20,
      qty: 2,
    },
    {
      area: 'lathe' as const,
      name: 'Lathe',
      sub: 'ready to transfer',
      state: 'done' as const,
      pct: 10,
      qty: 1,
    },
  ],
  readyNote:
    'Completed processing at Lathe — ready to transfer. Completed at Lathe 3; the piece waits on the finished rack in Lathe until it is transferred.',
  flows: [
    {
      id: 'QF-0140',
      qty: 6,
      routeMode: 'PLANNED' as const,
      position:
        'Lathe (3 on Lathe 3 · 2 queued · 1 ready to transfer) · 2h 05m in Area',
      route: [
        { step: 'Material', state: 'done' },
        { step: 'Cut', state: 'done' },
        { step: 'Lathe', state: 'cur' },
        { step: 'Deburr', state: 'future' },
        { step: 'Stockroom', state: 'future' },
      ],
      routeNote:
        'Planned Route “Bracket std v3” (snapshot) — guidance only; actual Movement history stays authoritative.',
    },
    {
      id: 'QF-0141',
      qty: 4,
      routeMode: 'FLOATING' as const,
      position: 'Cut · Saw 1 · 3h 40m in Area · SPLIT from QF-0140',
      // Actual route trace derived from Movement history: the repeated
      // Cut visit is a confirmed Repair return, preserved in order.
      route: [
        { step: 'Material', state: 'done' },
        { step: 'Cut', state: 'done' },
        { step: 'Lathe', state: 'done' },
        { step: 'Cut', state: 'cur', repair: true },
      ],
      routeNote:
        'Floating Route — the trace above is the actual recorded history (repeated Areas preserved). ⟲ REPAIR marks the explicit Repair return to Cut.',
    },
  ],
  movements: [
    {
      // DONE at the Area: the piece left Lathe 3 for the finished rack
      // (READY_TO_TRANSFER) and stays located in Lathe until it is
      // transferred — Area completion, never Stockroom completion.
      time: 'Jul 22 15:05',
      type: 'AREA_COMPLETED',
      typeClass: 'don',
      description:
        'Lathe 3 → Lathe finished rack · qty 1 · QF-0140 · W: H. Nguyen · LATHE-ST-01',
    },
    {
      time: 'Jul 22 14:10',
      type: 'SCRAPPED',
      typeClass: 'scr',
      description:
        'Scrapped 1 at Lathe · QF-0140 · reason: tool crash — gouged face · W: H. Nguyen · LATHE-ST-01',
    },
    {
      time: 'Jul 22 13:40',
      type: 'TRANSFERRED',
      typeClass: 'tra',
      description:
        'Lathe → Cut · qty 4 · QF-0141 · Repair — reason: shoulder cut short — recut required · W: H. Nguyen · LATHE-ST-01',
      repair: true,
    },
    {
      time: 'Jul 22 13:05',
      type: 'ASSIGNED_TO_MACHINE',
      typeClass: 'asg',
      description:
        'Lathe queue → Lathe 3 · qty 4 · QF-0140 · W: H. Nguyen · LATHE-ST-01',
    },
    {
      time: 'Jul 22 11:20',
      type: 'TRANSFERRED',
      typeClass: 'tra',
      description: 'Cut → Lathe (queue) · qty 7 · QF-0140 · W: H. Nguyen',
    },
    {
      time: 'Jul 21 10:15',
      type: 'TRANSFERRED',
      typeClass: 'tra',
      description: 'Cut → Lathe · qty 4 · QF-0141 · W: V. Tran · CUT-ST-01',
    },
    {
      time: 'Jul 21 09:41',
      type: 'REVERSED',
      typeClass: 'rev',
      description:
        'Compensates 09:38 ASSIGNED_TO_MACHINE · qty 2 · reason: wrong Machine scanned · W: H. Nguyen',
    },
    {
      time: 'Jul 21 09:38',
      type: 'ASSIGNED_TO_MACHINE',
      typeClass: 'asg',
      description: 'Lathe queue → Lathe 1 · qty 2 · QF-0140 · W: H. Nguyen',
    },
    {
      time: 'Jul 20 15:22',
      type: 'SPLIT',
      typeClass: 'spl',
      description:
        'QF-0140 (11) → QF-0140 (7) + QF-0141 (4) · at Cut · W: V. Tran',
    },
    {
      time: 'Jul 20 08:12',
      type: 'TRANSFERRED',
      typeClass: 'tra',
      description: 'Material → Cut · qty 11 · QF-0140 · W: V. Tran · CUT-ST-01',
    },
    {
      time: 'Jul 12 08:02',
      type: 'RECEIVED',
      typeClass: 'rec',
      description:
        'Received into Material · qty 11 · QF-0140 · WO 007001 release · Planned Route “Bracket std v3” assigned',
    },
  ],
  scrapNote:
    'Cumulative scrapped: 1 pc — recorded in the Movement history above; scrap is auditable and never reduces the WO Demand requested quantity. Reconciliation: introduced = active + stocked + scrapped.',
  stockedNote:
    'Nothing stocked yet for this PN. Allocation suggestions will follow priority first, then the earliest due date.',
};
