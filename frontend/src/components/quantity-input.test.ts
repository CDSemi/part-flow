import { expect, test } from 'vitest';

import {
  applyQuantityKey,
  deleteQuantityBackward,
  insertQuantityDigit,
  sanitizeQuantity,
} from './quantity-input';

/* ============ Cursor-aware shared transitions ============ */

test('a digit replaces the selected range at its exact position', () => {
  // Fully selected default value: "4" selected, enter 2 → "2".
  expect(insertQuantityDigit('4', '2', { start: 0, end: 1 })).toEqual({
    value: '2',
    caret: 1,
  });
  // Partial selection: "1234" with "23" selected, enter 9 → "194".
  expect(insertQuantityDigit('1234', '9', { start: 1, end: 3 })).toEqual({
    value: '194',
    caret: 2,
  });
});

test('a digit inserts at a collapsed caret position', () => {
  // "23" with the caret between 2 and 3, enter 4 → "243".
  expect(insertQuantityDigit('23', '4', { start: 1, end: 1 })).toEqual({
    value: '243',
    caret: 2,
  });
  expect(insertQuantityDigit('23', '4', { start: 0, end: 0 })).toEqual({
    value: '423',
    caret: 1,
  });
});

test('a null selection (unfocused input) appends to the end', () => {
  expect(insertQuantityDigit('23', '4', null)).toEqual({
    value: '234',
    caret: 3,
  });
  expect(insertQuantityDigit('', '4', null)).toEqual({ value: '4', caret: 1 });
});

test('the maximum digit count is enforced for every insertion path', () => {
  // Collapsed caret in a full value: rejected, value unchanged.
  expect(insertQuantityDigit('1234', '5', { start: 2, end: 2 })).toEqual({
    value: '1234',
    caret: 2,
  });
  expect(insertQuantityDigit('1234', '5', null)).toEqual({
    value: '1234',
    caret: 4,
  });
  // Replacing a selection stays within the limit and is accepted.
  expect(insertQuantityDigit('1234', '5', { start: 0, end: 4 })).toEqual({
    value: '5',
    caret: 1,
  });
  // After replacing, further digits append again up to the limit.
  expect(insertQuantityDigit('5', '6', { start: 1, end: 1 })).toEqual({
    value: '56',
    caret: 2,
  });
});

test('non-digits never edit the value', () => {
  expect(insertQuantityDigit('12', 'a', { start: 1, end: 1 })).toEqual({
    value: '12',
    caret: 1,
  });
});

test('Backspace removes the selection, or the digit before the caret', () => {
  expect(deleteQuantityBackward('1234', { start: 1, end: 3 })).toEqual({
    value: '14',
    caret: 1,
  });
  expect(deleteQuantityBackward('123', { start: 2, end: 2 })).toEqual({
    value: '13',
    caret: 1,
  });
  expect(deleteQuantityBackward('123', { start: 0, end: 0 })).toEqual({
    value: '123',
    caret: 0,
  });
  // Unfocused fallback: the last digit.
  expect(deleteQuantityBackward('123', null)).toEqual({
    value: '12',
    caret: 2,
  });
  expect(deleteQuantityBackward('', null)).toEqual({ value: '', caret: 0 });
});

/* ============ Unfocused fallback key mapping ============ */

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
