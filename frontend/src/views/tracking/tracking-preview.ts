// Development-only long-data preview of PN Tracking (`?state=long`).
//
// A deterministic list page with an over-long Part Number, an over-long
// Work Order Number and many rows — enough to verify the single-line PN
// column, the stacked narrow layout and the paging summary without a
// busy shop. Authored in the API model the real feed delivers.
//
// `import.meta.env.DEV` is replaced statically by Vite, so the whole
// fixture is dead code in a production build and never ships (verified
// by src/production-boundary.test.ts). It is NOT a mock view: the real
// view reads the real feed in every build.

import type {
  TrackingAreaRef,
  TrackingPage,
  TrackingRow,
} from '../../api/tracking';

const isoDateIn = (days: number) => {
  const date = new Date();
  date.setDate(date.getDate() + days);
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
};

const LATHE: TrackingAreaRef = {
  id: 3,
  name: 'Lathe',
  color: 'var(--a-lathe)',
  isTerminal: false,
};
const MILL: TrackingAreaRef = {
  id: 4,
  name: 'Mill',
  color: 'var(--a-mill)',
  isTerminal: false,
};

function row(
  pn: string,
  workOrderNumber: string,
  quantity: number,
  area: TrackingAreaRef,
  dueInDays: number,
  hotRank: number | null = null,
): TrackingRow {
  return {
    pn,
    hasMaster: true,
    barcodeValue: `PF:PN:${pn}`,
    hotRank,
    demands: [
      {
        workOrderId: 1,
        workOrderNumber,
        workOrderDemandId: 1,
        requestType: 'NEW',
        requestedQuantity: quantity,
        allocatedQuantity: 0,
        jobNumbers: [],
        dueDate: isoDateIn(dueInDays),
        priorityRank: hotRank,
      },
    ],
    distribution: [{ area, quantity, stocked: false }],
    activeQuantity: quantity,
    stockedQuantity: 0,
    scrappedQuantity: 0,
    nextDueDate: isoDateIn(dueInDays),
    status: 'ACTIVE',
  };
}

function longPreviewPage(): TrackingPage {
  const rows: TrackingRow[] = [
    row(
      '0118-40-0022-07-0455-88-REV-C',
      '007008-SUPPLEMENTAL-B',
      8,
      MILL,
      12,
      1,
    ),
    // 34 generated rows: the long-data preview renders 30+ rows (§2.3).
    ...Array.from({ length: 34 }, (_, i) => {
      const n = i + 1;
      return row(
        `0114-60-${String(100 + n).padStart(4, '0')}-00`,
        String(7200 + n).padStart(6, '0'),
        (n % 9) + 1,
        n % 2 === 0 ? LATHE : MILL,
        20 + n,
      );
    }),
  ];
  return {
    rows,
    total: rows.length,
    offset: 0,
    limit: rows.length,
    hasMore: false,
  };
}

export const LONG_PREVIEW_PAGE: TrackingPage | null = import.meta.env.DEV
  ? longPreviewPage()
  : null;
