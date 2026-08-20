import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';
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
//  3. this suite walks the production module graph transitively from
//     the real entry point and verifies at the source level that
//     nothing it reaches imports from src/mocks/ — the deliberate
//     exceptions are the development-only previews (the Worker
//     sessions policy preview and the Completed Work Orders visual
//     preview), each reachable only through an
//     `import.meta.env.DEV`-guarded lazy import that a production
//     build compiles away. The walk replaces an earlier fixed list of
//     view folders, which stopped covering the shared helper modules
//     the real views started importing.

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

/** Resolve one relative import specifier to a file under src/, or null
 * when it is a bare package / asset import. */
function resolveModule(fromFile: string, specifier: string): string | null {
  if (!specifier.startsWith('.')) return null;
  const base = join(dirname(fromFile), specifier);
  for (const candidate of [
    `${base}.ts`,
    `${base}.tsx`,
    join(base, 'index.ts'),
    join(base, 'index.tsx'),
    base,
  ]) {
    if (
      /\.tsx?$/.test(candidate) &&
      existsSync(candidate) &&
      statSync(candidate).isFile()
    ) {
      return candidate;
    }
  }
  return null;
}

function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
}

/**
 * Every module a production build actually reaches, walked transitively
 * from the real entry point.
 *
 * Static imports are always followed. A DYNAMIC import inside a module
 * that tests `import.meta.env.DEV` is a cut edge: Vite replaces that
 * constant statically, so in a production build the branch is dead
 * code and its lazy chunk is never emitted — this is exactly how the
 * mock views, the Worker sessions preview and the Completed Work
 * Orders preview leave the graph. Walking instead of listing folders
 * is the point: shared helpers that started life in the mock views
 * (dates, barcode parsing, the toast hook) are production modules
 * today, and a fixed directory list silently stopped covering them.
 */
function productionModuleGraph(): string[] {
  const entry = join(srcDir, 'main.tsx');
  const seen = new Set<string>();
  const queue = [entry];
  while (queue.length > 0) {
    const file = queue.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    // Comments are stripped first: a module that only MENTIONS the DEV
    // boundary in prose still ships its dynamic imports.
    const source = stripComments(readFileSync(file, 'utf8'));
    const devGuarded = source.includes('import.meta.env.DEV');
    const specifiers = [
      // Static: `import x from '…'`, `export … from '…'`, `import '…'`.
      ...Array.from(source.matchAll(/\bfrom\s+'([^']+)'/g), (m) => m[1]),
      ...Array.from(source.matchAll(/^\s*import\s+'([^']+)'/gm), (m) => m[1]),
      // Dynamic, and only from modules with no DEV guard.
      ...(devGuarded
        ? []
        : Array.from(source.matchAll(/\bimport\(\s*'([^']+)'/g), (m) => m[1])),
    ];
    for (const specifier of specifiers) {
      const resolved = resolveModule(file, specifier);
      if (resolved) queue.push(resolved);
    }
  }
  return [...seen].map((file) => relative(srcDir, file)).sort();
}

test('no production module reaches src/mocks/', () => {
  const graph = productionModuleGraph();
  // The walk is meaningful only if it actually reached the real views.
  expect(graph).toContain(join('app', 'real-views.ts'));
  expect(graph).toContain(join('views', 'work-orders', 'WorkOrdersView.tsx'));
  expect(graph.length).toBeGreaterThan(30);

  const offenders = graph.filter(
    (module) => module === 'mocks' || module.startsWith(`mocks${sep}`),
  );
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
