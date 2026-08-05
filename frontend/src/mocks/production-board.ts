import type { MockBoardRow } from '../views/view-models';
import { isoDateIn, minutesAgoIso } from './mock-time';

// Due dates are ISO `YYYY-MM-DD` or null (a WO Demand may have no due
// date); `received` is the parent Work Order received date that orders
// undated demands. The view sorts rows with the canonical demand order
// (Hot rank → earliest due date → undated by received date). Dates and
// position-entry timestamps are authored as relative offsets
// (mock-time.ts) resolved once at load into fixed data; the countdown
// text, `Total Days`, and per-location durations are DERIVED at render
// through the shared UI clock — never stored.
//
// Location rows use the explicit presentation model: `label` is always
// the Area name alone (`External`, never `External — Plating`),
// `machine` is a separate field (executor for `state: 'machine'`,
// completion context for `state: 'done'`), `activity` carries the
// External processing activity for its state chip, and `state` is
// never encoded into the display string. Where a PN also
// appears on the Area Board mocks, the distribution mirrors that state.
export const MOCK_BOARD_ROWS: MockBoardRow[] = [
  {
    pn: '2027-60-8114-00',
    name: 'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125, LASER CUT W/ CSK HOLES · rev C',
    hotRank: 1,
    locations: [
      {
        area: 'cut',
        label: 'Cut',
        machine: 'Saw 1',
        qty: 4,
        state: 'machine',
        since: minutesAgoIso(220),
      },
      // Lathe mirror of the Area Board card: 3 pcs turning on Lathe 3,
      // 2 pcs queued, 1 pc finished at the Area (completed by Lathe 3,
      // waiting on the finished rack — no current Machine ownership).
      {
        area: 'lathe',
        label: 'Lathe',
        machine: 'Lathe 3',
        qty: 3,
        state: 'machine',
        since: minutesAgoIso(125),
      },
      {
        area: 'lathe',
        label: 'Lathe',
        qty: 2,
        state: 'queue',
        since: minutesAgoIso(70),
      },
      {
        area: 'lathe',
        label: 'Lathe',
        machine: 'Lathe 3',
        qty: 1,
        state: 'done',
        since: minutesAgoIso(25),
      },
    ],
    total: 10,
    scrapped: 1,
    jobs: [
      { job: '18112', meta: '· WO 007001 · 10 pcs' },
      { job: '18240', meta: '· WO 007008 · 5 pcs queued' },
    ],
    due: isoDateIn(2),
    received: isoDateIn(-10),
  },
  {
    pn: '142-260',
    name: 'PLATE, TOP COVER ALUM 6061-T6',
    hotRank: 2,
    locations: [
      {
        area: 'external',
        label: 'External',
        activity: 'plating',
        qty: 20,
        state: 'processing',
        since: minutesAgoIso(5880),
      },
    ],
    total: 20,
    scrapped: 2,
    jobs: [{ job: '18031', meta: '· WO 007005 · 20 pcs' }],
    due: isoDateIn(-6),
    received: isoDateIn(-18),
  },
  {
    pn: '0123-40-0007-22',
    name: 'CATCH CUP INSERT, COATER 3-5, VENDOR',
    locations: [
      {
        area: 'external',
        label: 'External',
        activity: 'vendor',
        qty: 12,
        state: 'processing',
        since: minutesAgoIso(1800),
      },
    ],
    total: 12,
    jobs: [{ job: '18377', meta: '· WO 007007 · 12 pcs' }],
    due: isoDateIn(9),
    received: isoDateIn(-3),
  },
  {
    pn: '0455-20-0118-03',
    name: 'SHAFT, DRIVE 0.750 DIA X 12.500, 17-4PH H900, GRIND 32 RA',
    locations: [
      {
        area: 'material',
        label: 'Material',
        qty: 8,
        state: 'processing',
        since: minutesAgoIso(2941),
      },
      {
        area: 'lathe',
        label: 'Lathe',
        machine: 'Lathe 2',
        qty: 4,
        state: 'machine',
        since: minutesAgoIso(65),
      },
    ],
    total: 12,
    jobs: [{ job: '18190', meta: '· WO 007003 · 12 pcs' }],
    due: isoDateIn(9),
    received: isoDateIn(-6),
  },
  {
    pn: '78-04-0031',
    name: 'HOUSING, BEARING CAST AL 356-T6, MACHINED',
    locations: [
      {
        area: 'mill',
        label: 'Mill',
        machine: 'Mill 1',
        qty: 3,
        state: 'machine',
        since: minutesAgoIso(45),
      },
      // Finished in a no-Machine Area: deburring completed for the
      // whole portion — waiting on the finished rack, no `machine`.
      {
        area: 'deburr',
        label: 'Deburr',
        qty: 3,
        state: 'done',
        since: minutesAgoIso(30),
      },
    ],
    total: 6,
    jobs: [
      { job: '18102', meta: '· WO 007002 · 6 pcs' },
      // Internal MODIFY demand without an external WO Number → `—`.
      { job: '—', meta: '· WO — · MODIFY · 1 pc' },
    ],
    due: isoDateIn(16),
    received: isoDateIn(-4),
  },
  {
    // WO Demand without a due date: valid data — sorts after all dated
    // demands, ordered by the parent Work Order received date.
    pn: '118-052',
    name: 'MOTOR, GEAR STEPPER 7.2T',
    locations: [
      {
        area: 'manual',
        label: 'Manual',
        qty: 4,
        state: 'processing',
        since: minutesAgoIso(320),
      },
    ],
    total: 4,
    jobs: [{ job: '18520', meta: '· WO 007011 · 4 pcs' }],
    due: null,
    received: isoDateIn(-2),
  },
  {
    pn: '309-127',
    name: 'PIN, DOWEL 1/4 X 1.00 SS',
    locations: [
      {
        area: 'stockroom',
        label: 'Stockroom',
        qty: 50,
        state: 'stocked',
        since: null,
      },
    ],
    total: 50,
    totalStocked: true,
    jobs: [{ job: '17740', meta: '· WO 006996 · allocated 50/50' }],
    due: isoDateIn(-12),
    received: isoDateIn(-34),
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
      {
        // Long Machine name reference case (see MOCK_AREA_MACHINES):
        // the chip truncates with an ellipsis + title tooltip and never
        // pushes the quantity, state, or time out of alignment.
        area: 'mill',
        label: 'Mill',
        machine: 'Mill 3 — Horizontal Boring',
        qty: 2,
        state: 'machine',
        since: minutesAgoIso(80),
      },
      {
        area: 'deburr',
        label: 'Deburr',
        qty: 6,
        state: 'processing',
        since: minutesAgoIso(7860),
      },
    ],
    total: 8,
    jobs: [
      {
        job: '18455-CUSTOMER-REF-2026-000147',
        meta: '· WO 007008-SUPPLEMENTAL-B · 8 pcs',
      },
    ],
    due: isoDateIn(1),
    received: isoDateIn(-21),
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
          state: 'queue',
          since: minutesAgoIso(((n % 9) + 1) * 60 + (n % 6)),
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
      due: n % 4 === 0 ? null : isoDateIn((n % 20) + 4),
      received: isoDateIn(-((n % 12) + 1 + (n % 20))),
    };
  }),
];
