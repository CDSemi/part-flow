import type { MockPartNumberMaster } from '../views/view-models';

// PartNumber master metadata records (Management → Part Numbers).
// Development-only sample data: the canonical PN string is the
// identity; these records are optional metadata only. The list aligns
// with the PN catalog consumed by Work Orders and the Scan Station
// (mocks/work-orders.ts derives MOCK_PN_CATALOG from it), and with the
// Tracking detail sample (2027-60-8114-00: revision C, ERP-PN-40412).
// TEST-SCRAP-PLATE (mocks/tracking.ts) deliberately has NO record
// here: its junk/test master was hard-deleted — production history
// keeps showing the canonical PN.
export const MOCK_PART_NUMBERS: MockPartNumberMaster[] = [
  {
    pn: '0455-20-0118-03',
    name: 'SHAFT, DRIVE 0.750 DIA X 12.500, 17-4PH H900',
    revision: 'B',
    erpId: 'ERP-PN-38855',
  },
  {
    pn: '78-04-0031',
    name: 'HOUSING, BEARING CAST AL 356-T6, MACHINED',
    revision: 'A',
    erpId: 'ERP-PN-31207',
  },
  {
    pn: '309-127',
    name: 'PIN, DOWEL 1/4 X 1.00 SS',
    erpId: 'ERP-PN-29051',
  },
  {
    // Sparse record: revision and ERP mapping absent — valid data,
    // rendered as `—`.
    pn: '214-406',
    name: 'SPACER, THREADED 10-32, BRASS',
  },
  {
    pn: '118-052',
    name: 'MOTOR, GEAR STEPPER 7.2T',
    revision: 'D',
    erpId: 'ERP-PN-27114',
  },
  {
    pn: '2027-60-8114-00',
    name: 'BRACKET, MOUNTING SS 304, 2.50 X 4.00 X 0.125',
    revision: 'C',
    erpId: 'ERP-PN-40412',
  },
  {
    pn: '142-260',
    name: 'PLATE, TOP COVER ALUM 6061-T6',
    revision: 'A',
  },
];
