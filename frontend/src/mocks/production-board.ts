import type { MockBoardRow } from '../views/view-models';

// Due dates are ISO `YYYY-MM-DD` or null (a WO Demand may have no due
// date); `received` is the parent Work Order received date that orders
// undated demands. The view sorts rows with the canonical demand order
// (Hot rank → earliest due date → undated by received date).
export const MOCK_BOARD_ROWS: MockBoardRow[] = [
  {
    pn: '2027-60-8114-00',
    name: 'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125, LASER CUT W/ CSK HOLES · rev C',
    hotRank: 1,
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
      { job: '18112', meta: '· WO 007001 · 10 pcs' },
      { job: '18240', meta: '· WO 007008 · 5 pcs queued' },
    ],
    due: '2026-07-24',
    dueNote: '2 days left',
    dueClass: 'soon',
    totalDays: '10 d',
    received: '2026-07-12',
  },
  {
    pn: '142-260',
    name: 'PLATE, TOP COVER ALUM 6061-T6',
    hotRank: 2,
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
    jobs: [{ job: '18031', meta: '· WO 007005 · 20 pcs' }],
    due: '2026-07-16',
    dueNote: 'overdue 6 days',
    dueClass: 'late',
    totalDays: '18 d',
    received: '2026-07-06',
  },
  {
    pn: '0123-40-0007-22',
    name: 'CATCH CUP INSERT, COATER 3-5, VENDOR',
    locations: [
      { area: 'external', label: 'External — Vendor', qty: 12, time: '1d 06h' },
    ],
    total: 12,
    jobs: [{ job: '18377', meta: '· WO 007007 · 12 pcs' }],
    due: '2026-08-03',
    dueNote: '9 days left',
    dueClass: 'ok',
    totalDays: '3 d',
    received: '2026-07-18',
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
    jobs: [{ job: '18190', meta: '· WO 007003 · 12 pcs' }],
    due: '2026-07-31',
    dueNote: '9 days left',
    dueClass: 'ok',
    totalDays: '6 d',
    received: '2026-07-12',
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
      { job: '18102', meta: '· WO 007002 · 6 pcs' },
      { job: '—', meta: '· TMP-20260718-0910-REWORK · 1 pc' },
    ],
    due: '2026-08-07',
    dueNote: '16 days left',
    dueClass: 'ok',
    totalDays: '4 d',
    received: '2026-07-14',
  },
  {
    // WO Demand without a due date: valid data — sorts after all dated
    // demands, ordered by the parent Work Order received date.
    pn: '118-052',
    name: 'MOTOR, GEAR STEPPER 7.2T',
    locations: [{ area: 'manual', label: 'Manual', qty: 4, time: '5h 20m' }],
    total: 4,
    jobs: [{ job: '18520', meta: '· WO 007011 · 4 pcs' }],
    due: null,
    dueNote: 'No due date',
    dueClass: 'none',
    totalDays: '2 d',
    received: '2026-07-19',
  },
  {
    pn: '309-127',
    name: 'PIN, DOWEL 1/4 X 1.00 SS',
    locations: [{ area: 'stockroom', label: 'Stockroom', qty: 50, time: '—' }],
    total: 50,
    totalStocked: true,
    jobs: [{ job: '17740', meta: '· WO 006996 · allocated 50/50' }],
    due: '2026-07-10',
    dueNote: '✓ stocked',
    dueClass: 'none',
    totalDays: '12 d',
    received: '2026-06-18',
  },
];

// Long-data preview rows: over-long identifiers and many rows so the
// board's single-line PN rule and dense-table behavior can be verified.
export const MOCK_BOARD_ROWS_LONG: MockBoardRow[] = [
  {
    pn: '0118-40-0022-07-0455-88-REV-C',
    name: 'MANIFOLD ASSY, 6-PORT ANODIZED, W/ FITTINGS 1/4 NPT, VENDOR SUB-ASSY — long identifier sample',
    hotRank: 1,
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
        job: '18455-CUSTOMER-REF-2026-000147',
        meta: '· WO 007008-SUPPLEMENTAL-B · 8 pcs',
      },
    ],
    due: '2026-07-25',
    dueNote: '1 day left',
    dueClass: 'soon',
    totalDays: '21 d',
    received: '2026-07-04',
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
        {
          job: String(19000 + n),
          meta: `· WO ${String(7200 + n).padStart(6, '0')}`,
        },
      ],
      // Every fourth generated demand has no due date so the long list
      // also exercises the dated-first / undated-by-received ordering.
      due:
        n % 4 === 0
          ? null
          : `2026-08-${String((n % 14) + 15).padStart(2, '0')}`,
      dueNote: n % 4 === 0 ? 'No due date' : `${(n % 20) + 4} days left`,
      dueClass: n % 4 === 0 ? 'none' : 'ok',
      totalDays: `${(n % 12) + 1} d`,
      received: `2026-07-${String((n % 20) + 1).padStart(2, '0')}`,
    };
  }),
];
