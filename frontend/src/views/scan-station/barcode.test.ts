import { expect, test } from 'vitest';

import { parseScan, pnKey, SCRAP_BARCODE } from './barcode';

test('PF:PN: accepts the entire arbitrary non-empty suffix as the PN', () => {
  // PN values have no guaranteed format — nothing is parsed or mapped
  // through an opaque stable id.
  expect(parseScan('PF:PN:0455-20-0118-03')).toEqual({
    kind: 'pn',
    pn: '0455-20-0118-03',
  });
  expect(parseScan('PF:PN:ABC')).toEqual({ kind: 'pn', pn: 'ABC' });
  expect(parseScan('PF:PN:abc')).toEqual({ kind: 'pn', pn: 'abc' });
  expect(parseScan('PF:PN:X 1/2-REV C')).toEqual({
    kind: 'pn',
    pn: 'X 1/2-REV C',
  });
});

test('scanner terminators and surrounding whitespace are trimmed', () => {
  expect(parseScan('  PF:PN:214-406\r\n')).toEqual({
    kind: 'pn',
    pn: '214-406',
  });
});

test('an empty PN suffix is not a valid PN barcode', () => {
  expect(parseScan('PF:PN:')).toEqual({ kind: 'unknown', raw: 'PF:PN:' });
  expect(parseScan('PF:PN:   ')).toEqual({ kind: 'unknown', raw: 'PF:PN:' });
});

test('entity prefixes classify Machine, Worker and Area barcodes', () => {
  expect(parseScan('PF:MACHINE:CD-0105')).toEqual({
    kind: 'machine',
    id: 'CD-0105',
  });
  expect(parseScan('PF:WORKER:88')).toEqual({ kind: 'worker', id: '88' });
  expect(parseScan('PF:AREA:LATHE')).toEqual({ kind: 'area', id: 'LATHE' });
});

test('PF:SCRAP is its own dedicated barcode kind', () => {
  expect(parseScan(SCRAP_BARCODE)).toEqual({ kind: 'scrap' });
});

test('unknown values are rejected — raw PN text is never a barcode', () => {
  expect(parseScan('0455-20-0118-03')).toEqual({
    kind: 'unknown',
    raw: '0455-20-0118-03',
  });
  expect(parseScan('VENDOR-123')).toEqual({
    kind: 'unknown',
    raw: 'VENDOR-123',
  });
  // Action barcodes were removed: intent comes from explicit dialogs.
  // (Concatenated so the stale-vocabulary guard stays strict.)
  expect(parseScan('PF:' + 'ACTION:MODIFY').kind).toBe('unknown');
});

test('PN identity is case-insensitive; display casing is preserved', () => {
  expect(pnKey('abc')).toBe(pnKey('ABC'));
  expect(pnKey('Abc')).toBe(pnKey('ABC'));
  expect(pnKey(' 0455-20-0118-03 ')).toBe(pnKey('0455-20-0118-03'));
  // The parser itself never re-cases the PN.
  expect(parseScan('PF:PN:Abc')).toEqual({ kind: 'pn', pn: 'Abc' });
});
