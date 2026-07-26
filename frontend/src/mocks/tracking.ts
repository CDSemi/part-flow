import type { MockTrackingRow } from '../views/view-models';

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
    nextDue: 'Jul 31',
    status: 'Active',
  },
  {
    pn: '78-04-0031',
    name: 'HOUSING, BEARING CAST AL 356-T6, MACHINED',
    demand: [
      { workOrder: '007002', qty: 6, type: 'NEW' },
      { workOrder: 'TMP-…-0910', qty: 1, type: 'REWORK' },
    ],
    distribution: [
      { area: 'mill', label: 'Mill', qty: 3 },
      { area: 'deburr', label: 'Deburr', qty: 3 },
    ],
    activeQty: 6,
    stockedQty: 0,
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
    nextDue: '—',
    status: 'Completed',
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
      nextDue: 'Aug 15',
      status: 'Active',
    };
  }),
];

// Detail-panel sample for the selected PN (mirrors mockup v5, which
// shows the full detail panel for 2027-60-8114-00).
export const MOCK_TRACKING_DETAIL = {
  pn: '2027-60-8114-00',
  name: 'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125, LASER CUT W/ CSK HOLES, DEBURR ALL EDGES',
  revision: 'C',
  barcode: 'PF:PN:1001',
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
  distribution: [
    { area: 'cut' as const, name: 'Cut', sub: 'Saw 1', pct: 40, qty: 4 },
    {
      area: 'lathe' as const,
      name: 'Lathe 3',
      sub: 'machine',
      pct: 40,
      qty: 4,
    },
    {
      area: 'lathe' as const,
      name: 'Lathe',
      sub: 'queue',
      pct: 20,
      qty: 2,
      queued: true,
    },
  ],
  flows: [
    {
      id: 'QF-0140',
      qty: 6,
      position: 'Lathe (4 on Lathe 3 · 2 queued) · 2h 05m in Area',
      route: [
        { step: 'Material', state: 'done' },
        { step: 'Cut', state: 'done' },
        { step: 'Lathe', state: 'cur' },
        { step: 'Deburr', state: 'future' },
        { step: 'Stockroom', state: 'future' },
      ],
    },
    {
      id: 'QF-0141',
      qty: 4,
      position: 'Cut · Saw 1 · 3h 40m in Area · SPLIT from QF-0140',
      route: [
        { step: 'Material', state: 'done' },
        { step: 'Cut', state: 'cur' },
        { step: 'Lathe', state: 'future' },
        {
          step: 'External ⚠',
          state: 'dev',
          title: 'Deviation — External inserted Jul 20 by T. Le',
        },
        { step: 'Deburr', state: 'future' },
        { step: 'Stockroom', state: 'future' },
      ],
      deviationNote:
        '⚠ ROUTE_ADJUSTED — “External · Plating” inserted Jul 20 by T. Le (Manager). Reason: plating added per customer. Previous route preserved in audit.',
    },
  ],
  movements: [
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
      description: 'Cut → Lathe (queue) · qty 6 · QF-0140 · W: H. Nguyen',
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
        'QF-0140 (10) → QF-0140 (6) + QF-0141 (4) · at Cut · W: V. Tran',
    },
    {
      time: 'Jul 20 08:12',
      type: 'TRANSFERRED',
      typeClass: 'tra',
      description: 'Material → Cut · qty 10 · QF-0140 · W: V. Tran · CUT-ST-01',
    },
    {
      time: 'Jul 12 08:02',
      type: 'RECEIVED',
      typeClass: 'rec',
      description:
        'Received into Material · qty 10 · QF-0140 · WO 007001 release · Route “Bracket std v3” assigned',
    },
  ],
  stockedNote:
    'No STOCKED quantity yet for this PN. Allocation suggestions will follow priority → earliest due date (§18).',
};
