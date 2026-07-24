import type { MockBoardRow } from './types';

export const MOCK_BOARD_ROWS: MockBoardRow[] = [
  {
    pn: 'PF-BRACKET-001',
    name: 'Mounting bracket, alum 6061 · rev C',
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
    pn: 'PF-PLATE-007',
    name: 'Base plate 200×140',
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
    pn: 'PF-SHAFT-014',
    name: 'Drive shaft Ø25, 4140',
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
    pn: 'PF-HOUSING-021',
    name: 'Gearbox housing',
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
    pn: 'PF-PIN-102',
    name: 'Locating pin Ø8',
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
    pn: 'PF-MANIFOLD-ASSY-00847-REV-C-EXTENDED-VALIDATION',
    name: 'Hydraulic manifold assembly, long identifier sample',
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
      pn: `PF-LONGRUN-${String(n).padStart(3, '0')}`,
      name: `Long-list sample part ${n}`,
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
