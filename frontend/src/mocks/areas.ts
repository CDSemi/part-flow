import type { MockArea } from './types';

// Area identity colors are display properties defined in Administration;
// the palette below mirrors GUI_DESIGN §2.2 and is stable in both themes.
export const MOCK_AREAS: MockArea[] = [
  {
    key: 'material',
    name: 'Material',
    colorVar: 'var(--a-material)',
    description: 'Incoming raw material staging',
    operations: ['Receiving'],
  },
  {
    key: 'cut',
    name: 'Cut',
    colorVar: 'var(--a-cut)',
    description: 'Saw cutting to length',
    operations: ['Cutting'],
  },
  {
    key: 'lathe',
    name: 'Lathe',
    colorVar: 'var(--a-lathe)',
    description: 'Turning cell · Lathe 1–4',
    operations: ['Turning'],
  },
  {
    key: 'mill',
    name: 'Mill',
    colorVar: 'var(--a-mill)',
    description: 'Milling cell · Mill 1–2',
    operations: ['Milling'],
  },
  {
    key: 'manual',
    name: 'Manual',
    colorVar: 'var(--a-manual)',
    description: 'Manual machining bench',
    operations: ['Manual work'],
  },
  {
    key: 'deburr',
    name: 'Deburr',
    colorVar: 'var(--a-deburr)',
    description: 'Hand finishing bench',
    operations: ['Deburring'],
  },
  {
    key: 'external',
    name: 'External',
    colorVar: 'var(--a-external)',
    description: 'Outside processing vendors',
    operations: ['Plating', 'Painting', 'Testing'],
  },
  {
    key: 'stockroom',
    name: 'Stockroom',
    colorVar: 'var(--a-stockroom)',
    description: 'Terminal Area — completed parts',
    operations: ['Receiving'],
    terminal: true,
  },
];

export function areaByKey(key: string): MockArea | undefined {
  return MOCK_AREAS.find((a) => a.key === key);
}
