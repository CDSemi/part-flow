import type { MockTrackingRow } from './types';

export const MOCK_TRACKING_ROWS: MockTrackingRow[] = [
  {
    pn: 'PF-BRACKET-001',
    name: 'Mounting bracket, alum 6061',
    hotRank: 1,
    demand: [
      { po: 'PO-1001', qty: 10, type: 'NEW' },
      { po: 'PO-1008', qty: 5, type: 'NEW' },
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
    pn: 'PF-PLATE-007',
    name: 'Base plate 200×140',
    hotRank: 2,
    demand: [{ po: 'PO-1005', qty: 20, type: 'NEW' }],
    distribution: [{ area: 'external', label: 'External', qty: 20 }],
    activeQty: 20,
    stockedQty: 0,
    nextDue: 'Jul 16',
    status: 'Active',
  },
  {
    pn: 'PF-SHAFT-014',
    name: 'Drive shaft Ø25, 4140',
    demand: [{ po: 'PO-1003', qty: 12, type: 'NEW' }],
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
    pn: 'PF-HOUSING-021',
    name: 'Gearbox housing',
    demand: [
      { po: 'PO-1002', qty: 6, type: 'NEW' },
      { po: 'TMP-…-0910', qty: 1, type: 'REWORK' },
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
    pn: 'PF-PIN-102',
    name: 'Locating pin Ø8',
    demand: [{ po: 'PO-0996', qty: 50, type: 'NEW' }],
    distribution: [{ area: 'stockroom', label: 'Stockroom', qty: 50 }],
    activeQty: 0,
    stockedQty: 50,
    nextDue: '—',
    status: 'Completed',
  },
];

export const MOCK_TRACKING_ROWS_LONG: MockTrackingRow[] = [
  {
    pn: 'PF-MANIFOLD-ASSY-00847-REV-C-EXTENDED-VALIDATION',
    name: 'Hydraulic manifold assembly, long identifier sample',
    demand: [{ po: 'PO-1008-SUPPLEMENTAL-B', qty: 8, type: 'NEW' }],
    distribution: [{ area: 'mill', label: 'Mill', qty: 8 }],
    activeQty: 8,
    stockedQty: 0,
    nextDue: 'Jul 25',
    status: 'Active',
  },
  ...MOCK_TRACKING_ROWS,
  ...Array.from({ length: 20 }, (_, i): MockTrackingRow => {
    const n = i + 1;
    return {
      pn: `PF-LONGRUN-${String(n).padStart(3, '0')}`,
      name: `Long-list sample part ${n}`,
      demand: [{ po: `PO-2${String(100 + n)}`, qty: (n % 9) + 1, type: 'NEW' }],
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
// shows the full detail panel for PF-BRACKET-001).
export const MOCK_TRACKING_DETAIL = {
  pn: 'PF-BRACKET-001',
  name: 'Mounting bracket, alum 6061',
  revision: 'C',
  barcode: 'PF:PN:1001',
  erpId: 'ERP-PN-40412',
  demand: [
    {
      po: 'PO-1001',
      type: 'NEW' as const,
      requested: 10,
      allocated: 0,
      shortage: 10,
      due: 'Jul 24',
      priority: '🔥 Hot #1',
    },
    {
      po: 'PO-1008',
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
        'Received into Material · qty 10 · QF-0140 · PO-1001 release · Route “Bracket std v3” assigned',
    },
  ],
  stockedNote:
    'No STOCKED quantity yet for this PN. Allocation suggestions will follow priority → earliest due date (§18).',
};
