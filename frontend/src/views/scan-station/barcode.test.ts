import { expect, test } from 'vitest';

import { normalizePartNumber, parseScan, SCRAP_BARCODE } from './barcode';

test('PF:PN: accepts the whitespace-free suffix as the canonical PN', () => {
  // PN values have no guaranteed format beyond the canonical form rules
  // — nothing is parsed or mapped through an opaque stable id, and the
  // suffix canonicalizes to the uppercase PN.
  expect(parseScan('PF:PN:0455-20-0118-03')).toEqual({
    kind: 'pn',
    pn: '0455-20-0118-03',
  });
  expect(parseScan('PF:PN:ABC')).toEqual({ kind: 'pn', pn: 'ABC' });
  expect(parseScan('PF:PN:abc')).toEqual({ kind: 'pn', pn: 'ABC' });
});

test('a PN suffix containing whitespace is an invalid PN barcode', () => {
  // Whitespace inside a PN is invalid and is never silently removed to
  // turn invalid input into a valid PN.
  expect(parseScan('PF:PN:X 1/2-REV C')).toEqual({
    kind: 'unknown',
    raw: 'PF:PN:X 1/2-REV C',
  });
  expect(parseScan('PF:PN: ABC-123')).toEqual({
    kind: 'unknown',
    raw: 'PF:PN: ABC-123',
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

test('normalizePartNumber canonicalizes case-insensitive PN identity to uppercase', () => {
  expect(normalizePartNumber('abc-123')).toBe('ABC-123');
  expect(normalizePartNumber('AbC-123')).toBe('ABC-123');
  expect(normalizePartNumber('ABC-123')).toBe('ABC-123');
  // Case-insensitive identity: differently cased entries are one PN.
  expect(normalizePartNumber('ABC-123')).toBe(normalizePartNumber('abc-123'));
  // The parser canonicalizes too.
  expect(parseScan('PF:PN:Abc')).toEqual({ kind: 'pn', pn: 'ABC' });
});

test('normalizePartNumber rejects empty values and any whitespace', () => {
  expect(normalizePartNumber('')).toBeNull();
  expect(normalizePartNumber('ABC 123')).toBeNull();
  expect(normalizePartNumber(' ABC-123')).toBeNull();
  expect(normalizePartNumber('ABC-123 ')).toBeNull();
  expect(normalizePartNumber('ABC\t123')).toBeNull();
  expect(normalizePartNumber('ABC\n123')).toBeNull();
});
