import { expect, test } from 'vitest';

import {
  formatTemporaryWorkOrderNumber,
  generateTemporaryWorkOrderNumber,
} from './work-order-number';

const NOW = new Date(2026, 6, 25, 14, 3, 9); // 2026-07-25 14:03:09 local

test('temporary internal WO Number uses TMP-YYYYMMDD-HHMMSS', () => {
  expect(formatTemporaryWorkOrderNumber(NOW)).toBe('TMP-20260725-140309');
});

test('generation is unique with a deterministic collision suffix', () => {
  expect(generateTemporaryWorkOrderNumber([], NOW)).toBe('TMP-20260725-140309');
  expect(generateTemporaryWorkOrderNumber(['TMP-20260725-140309'], NOW)).toBe(
    'TMP-20260725-140309-2',
  );
  expect(
    generateTemporaryWorkOrderNumber(
      ['TMP-20260725-140309', 'TMP-20260725-140309-2'],
      NOW,
    ),
  ).toBe('TMP-20260725-140309-3');
});
