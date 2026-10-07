import { afterEach, expect, test } from 'vitest';

import {
  clearPriorityFocus,
  peekPriorityFocus,
  requestPriorityFocus,
} from './priority-focus';

// FT-7: the one-shot hand-off from Tracking's `Change priority` to
// Priority (Phase 14 slice 7).

afterEach(() => {
  clearPriorityFocus();
});

test('nothing is pending until a PN is requested; a peek never consumes it', () => {
  expect(peekPriorityFocus()).toBeNull();
  requestPriorityFocus('2027-60-8114-00');
  expect(peekPriorityFocus()).toBe('2027-60-8114-00');
  expect(peekPriorityFocus()).toBe('2027-60-8114-00');
});

test('clear drops the pending PN', () => {
  requestPriorityFocus('A-100');
  clearPriorityFocus();
  expect(peekPriorityFocus()).toBeNull();
});

test('a new request replaces the pending PN', () => {
  requestPriorityFocus('A-100');
  requestPriorityFocus('B-200');
  expect(peekPriorityFocus()).toBe('B-200');
});
