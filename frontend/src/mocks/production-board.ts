import type { MockBoardRow } from '../views/view-models';

export const MOCK_BOARD_ROWS: MockBoardRow[] = [
  {
    pn: '2027-60-8114-00',
    name: 'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125, LASER CUT W/ CSK HOLES · rev C',
    hotRank: 1,
    blink: true,
    locations: [
      { area: 'cut', label: 'Cut', qty: 4, time: '3h 40m' },
      {
        area: 'lathe',
        label: 'Lathe 3',
        qty: 4,
        tag: 'machine',
        time: '2h 05m',
      },
      { area: 'lathe', label: 'Lathe', qty: 2, tag: 'queue', time: '1h 10m' },
    ],
    total: 10,
    jobs: [
      { job: 'ERP-88112', meta: '· PO-1001 · 10 pcs' },
      { job: 'ERP-88240', meta: '· PO-1008 · 5 pcs queued' },
    ],
    due: 'Jul 24',
    dueNote: '2 days left',
    dueClass: 'soon',
    totalDays: '10 d',
  },
  {
    pn: '142-260',
    name: 'PLATE, TOP COVER ALUM 6061-T6',
    hotRank: 2,
    blink: true,
    locations: [
      {
        area: 'external',
        label: 'External — Plating',
        qty: 20,
        time: '4d 02h',
        timeLong: true,
      },
    ],
    total: 20,
    jobs: [{ job: 'ERP-88031', meta: '· PO-1005 · 20 pcs' }],
    due: 'Jul 16',
    dueNote: 'overdue 6 days',
    dueClass: 'late',
    totalDays: '18 d',
  },
  {
    pn: '0123-40-0007-22',
    name: 'CATCH CUP INSERT, COATER 3-5, VENDOR',
    locations: [
      { area: 'external', label: 'External — Vendor', qty: 12, time: '1d 06h' },
    ],
    total: 12,
    jobs: [{ job: 'ERP-88377', meta: '· PO-1007 · 12 pcs' }],
    due: 'Aug 03',
    dueNote: '9 days left',
    dueClass: 'ok',
    totalDays: '3 d',
  },
  {
    pn: '0455-20-0118-03',
    name: 'SHAFT, DRIVE 0.750 DIA X 12.500, 17-4PH H900, GRIND 32 RA',
    locations: [
      { area: 'material', label: 'Material', qty: 8, time: '2d 01h' },
      {
        area: 'lathe',
        label: 'Lathe 2',
        qty: 4,
        tag: 'machine',
        time: '1h 05m',
      },
    ],
    total: 12,
    jobs: [{ job: 'ERP-88190', meta: '· PO-1003 · 12 pcs' }],
    due: 'Jul 31',
    dueNote: '9 days left',
    dueClass: 'ok',
    totalDays: '6 d',
  },
  {
    pn: '78-04-0031',
    name: 'HOUSING, BEARING CAST AL 356-T6, MACHINED',
    locations: [
      { area: 'mill', label: 'Mill 1', qty: 3, tag: 'machine', time: '45m' },
      { area: 'deburr', label: 'Deburr', qty: 3, time: '30m' },
    ],
    total: 6,
    jobs: [
      { job: 'ERP-88102', meta: '· PO-1002 · 6 pcs' },
      { job: '—', meta: '· TMP-20260718-0910-REWORK · 1 pc' },
    ],
    due: 'Aug 07',
    dueNote: '16 days left',
    dueClass: 'ok',
    totalDays: '4 d',
  },
  {
    pn: '309-127',
    name: 'PIN, DOWEL 1/4 X 1.00 SS',
    locations: [{ area: 'stockroom', label: 'Stockroom', qty: 50, time: '—' }],
    total: 50,
    totalStocked: true,
    jobs: [{ job: 'ERP-87740', meta: '· PO-0996 · allocated 50/50' }],
    due: 'Jul 10',
    dueNote: '✓ stocked',
    dueClass: 'none',
    totalDays: '12 d',
  },
];

// Long-data preview rows: over-long identifiers and many rows so the
// board's single-line PN rule and dense-table behavior can be verified.
export const MOCK_BOARD_ROWS_LONG: MockBoardRow[] = [
  {
    pn: '0118-40-0022-07-0455-88-REV-C',
    name: 'MANIFOLD ASSY, 6-PORT ANODIZED, W/ FITTINGS 1/4 NPT, VENDOR SUB-ASSY — long identifier sample',
    hotRank: 1,
    blink: true,
    locations: [
      { area: 'mill', label: 'Mill 2', qty: 2, tag: 'machine', time: '1h 20m' },
      {
        area: 'deburr',
        label: 'Deburr',
        qty: 6,
        time: '5d 11h',
        timeLong: true,
      },
    ],
    total: 8,
    jobs: [
      {
        job: 'ERP-88455-CUSTOMER-REF-2026-000147',
        meta: '· PO-1008-SUPPLEMENTAL-B · 8 pcs',
      },
    ],
    due: 'Jul 25',
    dueNote: '1 day left',
    dueClass: 'soon',
    totalDays: '21 d',
  },
  ...Array.from({ length: 24 }, (_, i): MockBoardRow => {
    const n = i + 1;
    return {
      pn: `0114-60-${String(100 + n).padStart(4, '0')}-00`,
      name: `Spacer, sample lot ${n}, ALUM 6061-T6`,
      locations: [
        {
          area: n % 2 === 0 ? 'lathe' : 'mill',
          label: n % 2 === 0 ? 'Lathe' : 'Mill',
          qty: (n % 7) + 1,
          tag: 'queue',
          time: `${(n % 9) + 1}h 0${n % 6}m`,
        },
      ],
      total: (n % 7) + 1,
      jobs: [
        { job: `ERP-9${String(1000 + n)}`, meta: `· PO-2${String(100 + n)}` },
      ],
      due: 'Aug 15',
      dueNote: `${(n % 20) + 4} days left`,
      dueClass: 'ok',
      totalDays: `${(n % 12) + 1} d`,
    };
  }),
];
