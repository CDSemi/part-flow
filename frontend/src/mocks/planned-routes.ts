import type { MockRouteTemplate } from '../views/view-models';

// Planned Route templates (development mock). `Bracket std v3` is the
// template behind the QF-0140 Assigned Route snapshot shown in
// Tracking — editing the template later never rewrites that snapshot.
// `Legacy plating route` demonstrates an archived, previously used
// template (visible in historical context, never offered for new
// assignments), and `Lathe + mill combo (trial)` a never-used draft
// that may still be deleted outright.
export const MOCK_ROUTE_TEMPLATES: MockRouteTemplate[] = [
  {
    id: 'RT-011',
    name: 'Bracket std v3',
    description: 'Standard turned bracket — saw cut, turn, hand finish.',
    steps: [
      { area: 'material', operation: 'Receiving' },
      {
        area: 'cut',
        operation: 'Cutting',
        expectedDuration: '2h',
        preferredMachine: 'Saw 1',
      },
      {
        area: 'lathe',
        operation: 'Turning',
        expectedDuration: '6h',
        instructions: 'Face and turn per drawing; check shoulder depth.',
      },
      { area: 'deburr', operation: 'Deburring', expectedDuration: '1h' },
      { area: 'stockroom', operation: 'Receiving' },
    ],
    usedBy: [
      { flow: 'QF-0140', pn: '2027-60-8114-00', releasedOn: '2026-07-31' },
      { flow: 'QF-0118', pn: '2027-60-8114-00', releasedOn: '2026-06-19' },
    ],
    createdOn: '2026-03-02',
    updatedOn: '2026-06-15',
  },
  {
    id: 'RT-014',
    name: 'Milled housing + plating',
    description: 'Milled housing with outside plating before finish.',
    steps: [
      { area: 'material', operation: 'Receiving' },
      {
        area: 'cut',
        operation: 'Cutting',
        expectedDuration: '2h',
        preferredMachine: 'Saw 1',
      },
      {
        area: 'mill',
        operation: 'Milling',
        expectedDuration: '8h',
        preferredMachine: 'Mill 2',
        instructions: 'Rough and finish mill; verify bore location.',
      },
      { area: 'external', operation: 'Plating', expectedDuration: '3d' },
      { area: 'deburr', operation: 'Deburring', expectedDuration: '1h' },
      { area: 'stockroom', operation: 'Receiving' },
    ],
    usedBy: [{ flow: 'QF-0131', pn: '78-04-0031', releasedOn: '2026-07-14' }],
    createdOn: '2026-04-10',
    updatedOn: '2026-07-01',
  },
  {
    id: 'RT-017',
    name: 'Lathe + mill combo (trial)',
    description: 'Trial variant — turning first, then milled flats.',
    steps: [
      { area: 'material', operation: 'Receiving' },
      { area: 'lathe', operation: 'Turning', expectedDuration: '4h' },
      { area: 'mill', operation: 'Milling', expectedDuration: '3h' },
      { area: 'deburr', operation: 'Deburring' },
      { area: 'stockroom', operation: 'Receiving' },
    ],
    usedBy: [],
    createdOn: '2026-07-28',
    updatedOn: '2026-07-28',
  },
  {
    id: 'RT-006',
    name: 'Legacy plating route',
    description: 'Superseded by “Milled housing + plating”.',
    steps: [
      { area: 'material', operation: 'Receiving' },
      { area: 'mill', operation: 'Milling', expectedDuration: '8h' },
      { area: 'external', operation: 'Plating', expectedDuration: '4d' },
      { area: 'stockroom', operation: 'Receiving' },
    ],
    archivedOn: '2026-04-12',
    usedBy: [
      { flow: 'QF-0072', pn: '78-04-0031', releasedOn: '2025-11-03' },
      { flow: 'QF-0084', pn: '142-260', releasedOn: '2026-01-22' },
    ],
    createdOn: '2025-09-14',
    updatedOn: '2026-04-12',
  },
];
