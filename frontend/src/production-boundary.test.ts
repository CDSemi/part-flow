import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from 'vitest';

import { DEV_MOCK_VIEWS } from './app/dev-views';
import { REAL_VIEWS } from './app/real-views';

// The production mock boundary has three parts:
//  1. scripts/check-production-boundary.mjs fails `npm run build` when a
//     mock sentinel appears in the generated production assets;
//  2. this suite keeps that sentinel list honest — every sentinel must
//     exist in the development mock sources, so the build check can
//     never silently rot into scanning for values that no longer exist;
//  3. the real modules (the API layer, Management → Machines,
//     Administration since Phase 3.5, Management → Work Orders since
//     Phase 4) ship in production builds, so this suite verifies at
//     the source level that none of them imports from src/mocks/ —
//     the deliberate exceptions are the development-only previews
//     (the Worker sessions policy preview and the Completed Work
//     Orders visual preview), each reachable only through an
//     `import.meta.env.DEV`-guarded lazy import.

const srcDir = dirname(fileURLToPath(import.meta.url));
const scriptPath = join(
  srcDir,
  '..',
  'scripts',
  'check-production-boundary.mjs',
);

function readSentinels(): string[] {
  const script = readFileSync(scriptPath, 'utf8');
  const listMatch = /MOCK_SENTINELS = \[([^\]]+)]/.exec(script);
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

test('development builds expose the remaining mock views through the dev-only registry', () => {
  // Vitest runs with import.meta.env.DEV === true, so the registry must
  // exist and cover every view that is still a Phase 2 mock view;
  // production builds compile the registry to null and render the
  // not-connected state for these routes instead. Machines and
  // Administration left this registry with Phase 3.5 — they are real
  // views now.
  expect(DEV_MOCK_VIEWS).not.toBeNull();
  expect(Object.keys(DEV_MOCK_VIEWS!).sort()).toEqual(
    [
      'area-board',
      'part-numbers',
      'planned-routes',
      'priority',
      'production-board',
      'scan-station',
      'tracking',
    ].sort(),
  );
});

test('the real views ship in every build', () => {
  // Management → Machines, Administration (Phase 3.5) and Management →
  // Work Orders (Phase 4) read real server state — they live in the
  // always-available registry, never behind the development-only
  // boundary.
  expect(Object.keys(REAL_VIEWS).sort()).toEqual(
    ['administration', 'machines', 'work-orders'].sort(),
  );
});

/** Production modules (shipped in every build) that must never import
 * from src/mocks/. */
const PRODUCTION_MODULE_DIRS = [
  'api',
  join('views', 'machines'),
  join('views', 'administration'),
  join('views', 'work-orders'),
];

/** The development-only modules inside production view folders —
 * each reachable only through an `import.meta.env.DEV`-guarded lazy
 * import, so production builds drop them from the module graph. */
const DEV_ONLY_MODULES = new Set([
  join('views', 'administration', 'WorkerSessionsPreview.tsx'),
  join('views', 'work-orders', 'CompletedWorkOrdersView.tsx'),
]);

test('production modules do not import from src/mocks/', () => {
  const offenders: string[] = [];
  for (const dir of PRODUCTION_MODULE_DIRS) {
    for (const file of readdirSync(join(srcDir, dir))) {
      if (!/\.(ts|tsx)$/.test(file) || /\.test\.tsx?$/.test(file)) continue;
      const path = join(srcDir, dir, file);
      const relativePath = relative(srcDir, path);
      if (DEV_ONLY_MODULES.has(relativePath)) continue;
      const source = readFileSync(path, 'utf8');
      if (/from '(\.\.\/)+mocks\//.test(source)) {
        offenders.push(relativePath);
      }
    }
  }
  expect(offenders).toEqual([]);
});

test('the dev-only Worker sessions preview stays behind the DEV boundary', () => {
  // The preview module itself is allowed to import mock data, but the
  // real Administration view may reach it only through the guarded
  // lazy import — never through a static import that would pull the
  // mock datasets into the production module graph.
  const adminView = readFileSync(
    join(srcDir, 'views', 'administration', 'AdministrationView.tsx'),
    'utf8',
  );
  expect(adminView).not.toMatch(/^import .*WorkerSessionsPreview/m);
  expect(adminView).toContain('import.meta.env.DEV');
  expect(adminView).toContain("import('./WorkerSessionsPreview')");
});

test('the dev-only Completed Work Orders preview stays behind the DEV boundary', () => {
  // §11.5 has no backend yet (completion = full allocation, Phase 10):
  // production builds render the honest unavailable page, and the mock
  // visual preview is reachable only through the guarded lazy import —
  // never through a static import that would pull the mock completed
  // history into the production module graph.
  const workOrdersView = readFileSync(
    join(srcDir, 'views', 'work-orders', 'WorkOrdersView.tsx'),
    'utf8',
  );
  expect(workOrdersView).not.toMatch(/^import .*CompletedWorkOrdersView'/m);
  expect(workOrdersView).toContain('import.meta.env.DEV');
  expect(workOrdersView).toContain("import('./CompletedWorkOrdersView')");
});
