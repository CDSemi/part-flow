import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from 'vitest';

import { DEV_MOCK_VIEWS } from './app/dev-views';

// The production mock boundary has two halves:
//  1. scripts/check-production-boundary.mjs fails `npm run build` when a
//     mock sentinel appears in the generated production assets;
//  2. this suite keeps that sentinel list honest — every sentinel must
//     exist in the development mock sources, so the build check can
//     never silently rot into scanning for values that no longer exist.

const srcDir = dirname(fileURLToPath(import.meta.url));
const scriptPath = join(
  srcDir,
  '..',
  'scripts',
  'check-production-boundary.mjs',
);

function readSentinels(): string[] {
  const script = readFileSync(scriptPath, 'utf8');
  const listMatch = /MOCK_SENTINELS = \[([^\]]+)\]/.exec(script);
  if (!listMatch) return [];
  return Array.from(listMatch[1].matchAll(/'([^']+)'/g), (m) => m[1]);
}

function readMockAndViewSources(): string {
  const parts: string[] = [];
  const mocksDir = join(srcDir, 'mocks');
  for (const file of readdirSync(mocksDir)) {
    parts.push(readFileSync(join(mocksDir, file), 'utf8'));
  }
  const viewsDir = join(srcDir, 'views');
  for (const entry of readdirSync(viewsDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    for (const file of readdirSync(join(viewsDir, entry.name))) {
      if (/\.(ts|tsx)$/.test(file) && !file.endsWith('.test.tsx')) {
        parts.push(readFileSync(join(viewsDir, entry.name, file), 'utf8'));
      }
    }
  }
  return parts.join('\n');
}

test('the sentinel list is non-empty and every sentinel exists in mock sources', () => {
  const sentinels = readSentinels();
  expect(sentinels.length).toBeGreaterThanOrEqual(5);

  const sources = readMockAndViewSources();
  for (const sentinel of sentinels) {
    expect(sources, `sentinel "${sentinel}" no longer exists`).toContain(
      sentinel,
    );
  }
});

test('development builds expose the mock views through the dev-only registry', () => {
  // Vitest runs with import.meta.env.DEV === true, so the registry must
  // exist and cover every application view; production builds compile
  // the registry to null and render the not-connected state instead.
  expect(DEV_MOCK_VIEWS).not.toBeNull();
  // The ten approved GUI views (dev-views.ts) — Machines and Planned
  // Routes joined the registry with the v14/v15 Management screens,
  // Part Numbers with the post-v18 master-metadata screen.
  expect(Object.keys(DEV_MOCK_VIEWS!).sort()).toEqual(
    [
      'administration',
      'area-board',
      'machines',
      'part-numbers',
      'planned-routes',
      'priority',
      'production-board',
      'work-orders',
      'scan-station',
      'tracking',
    ].sort(),
  );
});
