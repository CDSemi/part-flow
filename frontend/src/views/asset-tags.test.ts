import { expect, test } from 'vitest';

import { formatAssetTag, machineBarcode, nextAssetTag } from './asset-tags';

// Asset Tag generation (PROJECT_PROFILE §8.6, §10): prefix +
// zero-padded numeric sequence, assigned automatically at Machine
// creation, never reused (retired Machines keep theirs) and never
// regenerated after a format change.

const FORMAT = { prefix: 'CD-', digits: 4 };

test('formats the sequence zero-padded behind the prefix', () => {
  expect(formatAssetTag(FORMAT, 1)).toBe('CD-0001');
  expect(formatAssetTag(FORMAT, 512)).toBe('CD-0512');
  expect(formatAssetTag({ prefix: 'MX', digits: 6 }, 42)).toBe('MX000042');
});

test('the digit count is a minimum width, never a truncation', () => {
  expect(formatAssetTag(FORMAT, 12045)).toBe('CD-12045');
});

test('the next tag is one past the highest existing sequence — retired included', () => {
  expect(nextAssetTag(FORMAT, ['CD-0201', 'CD-0512', 'CD-0104'])).toBe(
    'CD-0513',
  );
  // No existing tags → the sequence starts at 1.
  expect(nextAssetTag(FORMAT, [])).toBe('CD-0001');
});

test('tags of another prefix or shape never influence the sequence', () => {
  // Older-format tags stay untouched and are simply skipped.
  expect(nextAssetTag(FORMAT, ['MX-9999', 'CD-0007', 'CD-EXTRA'])).toBe(
    'CD-0008',
  );
});

test('the Machine barcode is the Asset Tag in the PF:MACHINE: namespace', () => {
  expect(machineBarcode('CD-0512')).toBe('PF:MACHINE:CD-0512');
});
