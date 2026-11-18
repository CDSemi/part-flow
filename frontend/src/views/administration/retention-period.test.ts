import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from 'vitest';

import { formatRetentionPeriod } from './retention-period';

// The Movement-history retention period wording (Administration →
// History archival & purge) and the guard that only the settings API and
// that section read the stored period: production workflows never do
// (PROJECT_PROFILE §28). The ranges are the server's.

test('RP-2: formatRetentionPeriod states the months in years and months', () => {
  expect(formatRetentionPeriod(1)).toBe('1 month');
  expect(formatRetentionPeriod(6)).toBe('6 months');
  expect(formatRetentionPeriod(12)).toBe('1 year');
  expect(formatRetentionPeriod(13)).toBe('1 year 1 month');
  expect(formatRetentionPeriod(18)).toBe('1 year 6 months');
  expect(formatRetentionPeriod(24)).toBe('2 years');
  expect(formatRetentionPeriod(120)).toBe('10 years');
  expect(formatRetentionPeriod(1200)).toBe('100 years');
});

const srcDir = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

function walk(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walk(path));
    else files.push(path);
  }
  return files;
}

/** Non-test sources (relative, `/`-separated) whose text matches. */
function sourcesMatching(pattern: RegExp): string[] {
  return walk(srcDir)
    .filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f))
    .filter((f) => pattern.test(readFileSync(f, 'utf8')))
    .map((f) => relative(srcDir, f).split('\\').join('/'))
    .sort();
}

test('RP-3: only the policy API and History archival & purge read the retention period', () => {
  expect(sourcesMatching(/retention_period_months/)).toEqual([
    'api/policies.ts',
  ]);
  expect(sourcesMatching(/RetentionPolicy|retentionPeriodMonths/)).toEqual([
    'api/policies.ts',
    'views/administration/HistoryArchivalSection.tsx',
  ]);
});
