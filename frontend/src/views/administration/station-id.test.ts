import { expect, test } from 'vitest';

import { stationIdPrefix, suggestStationId } from './station-id';

// The Station ID proposed for a new Scan Station: the Area name as a
// capital-letter prefix, `-ST-` and the next two-digit number.

const STATION_ID = /^[A-Za-z0-9._-]+$/;

test('the prefix is the Area name in capitals, other characters joined by one dash', () => {
  expect(stationIdPrefix('Cut')).toBe('CUT');
  expect(stationIdPrefix('Machine Shop')).toBe('MACHINE-SHOP');
  expect(stationIdPrefix('  Heat treat / Plating #2 ')).toBe(
    'HEAT-TREAT-PLATING-2',
  );
  // Accents are folded, including the Vietnamese Đ.
  expect(stationIdPrefix('Cắt Đột')).toBe('CAT-DOT');
  expect(stationIdPrefix('Ébavurage')).toBe('EBAVURAGE');
  // Nothing usable.
  expect(stationIdPrefix('—')).toBe('');
});

test('the first station of an Area is number 01', () => {
  expect(suggestStationId({ id: 3, name: 'Cut' }, [])).toBe('CUT-ST-01');
  expect(
    suggestStationId({ id: 3, name: 'Cut' }, ['LATHE-ST-01', 'CUTTER-ST-04']),
  ).toBe('CUT-ST-01');
});

test('the number follows the highest one already used under the prefix', () => {
  expect(suggestStationId({ id: 3, name: 'Cut' }, ['CUT-ST-01'])).toBe(
    'CUT-ST-02',
  );
  // One above the highest, never filling a gap; unpadded and
  // differently cased IDs count too.
  expect(
    suggestStationId({ id: 3, name: 'Cut' }, [
      'CUT-ST-01',
      'cut-st-7',
      'CUT-ST-03',
    ]),
  ).toBe('CUT-ST-08');
  expect(suggestStationId({ id: 3, name: 'Cut' }, ['CUT-ST-99'])).toBe(
    'CUT-ST-100',
  );
  // Other shapes under the same prefix are someone's own naming.
  expect(
    suggestStationId({ id: 3, name: 'Cut' }, [
      'CUT-ST-02B',
      'CUT-ST-',
      'CUT-ST-1234567890',
      'XCUT-ST-05',
    ]),
  ).toBe('CUT-ST-01');
});

test('an Area name without usable characters falls back to its id', () => {
  expect(suggestStationId({ id: 12, name: '—' }, ['AREA-12-ST-01'])).toBe(
    'AREA-12-ST-02',
  );
});

test('every proposal is a valid Station ID', () => {
  for (const name of ['Cut', 'Machine Shop', 'Cắt Đột', '—', 'A/B.C_D']) {
    expect(suggestStationId({ id: 1, name }, [])).toMatch(STATION_ID);
  }
});
