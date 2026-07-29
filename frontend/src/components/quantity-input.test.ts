import { expect, test } from 'vitest';

import { applyQuantityKey, sanitizeQuantity } from './quantity-input';

test('digits append up to the maximum length', () => {
  expect(applyQuantityKey('', '4')).toBe('4');
  expect(applyQuantityKey('4', '2')).toBe('42');
  expect(applyQuantityKey('1234', '5')).toBe('1234'); // capped
});

test('Backspace removes one digit; Delete/Clear clears the value', () => {
  expect(applyQuantityKey('42', 'Backspace')).toBe('4');
  expect(applyQuantityKey('', 'Backspace')).toBe('');
  expect(applyQuantityKey('42', 'Delete')).toBe('');
  expect(applyQuantityKey('42', 'Clear')).toBe('');
});

test('non-quantity keys are left to the dialog (null)', () => {
  expect(applyQuantityKey('42', 'Enter')).toBeNull();
  expect(applyQuantityKey('42', 'Escape')).toBeNull();
  expect(applyQuantityKey('42', 'a')).toBeNull();
  expect(applyQuantityKey('42', 'Tab')).toBeNull();
});

test('Space is consumed but ignored — the value never changes', () => {
  expect(applyQuantityKey('42', ' ')).toBe('42');
  expect(applyQuantityKey('', ' ')).toBe('');
});

test('sanitizeQuantity keeps digits only, capped at the maximum length', () => {
  expect(sanitizeQuantity('4a2 ')).toBe('42');
  expect(sanitizeQuantity('123456')).toBe('1234');
  expect(sanitizeQuantity('')).toBe('');
});
