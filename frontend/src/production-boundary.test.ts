import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
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
  // Administration left this registry with Phase 3.5, Work Orders with
  // Phase 4, the Scan Station with Phase 5 and the Production Board
  // with Phase 11 — they are real views now.
  expect(DEV_MOCK_VIEWS).not.toBeNull();
  expect(Object.keys(DEV_MOCK_VIEWS!).sort()).toEqual(
    [
      'area-board',
      'part-numbers',
      'planned-routes',
      'priority',
      'tracking',
    ].sort(),
  );
});

test('the real views ship in every build', () => {
  // Management → Machines, Administration (Phase 3.5), Management →
  // Work Orders (Phase 4), the Scan Station (Phase 5) and the
  // Production Board (Phase 11) read real server state — they live in
  // the always-available registry, never behind the development-only
  // boundary.
  expect(Object.keys(REAL_VIEWS).sort()).toEqual(
    [
      'administration',
      'machines',
      'production-board',
      'scan-station',
      'work-orders',
    ].sort(),
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
 * The character ranges of a module that a production build compiles
 * away, as `[start, end)` pairs.
 *
 * The codebase expresses its DEV boundary one way — a conditional whose
 * test is `import.meta.env.DEV`, whose consequent holds the lazy
 * import:
 *
 *     export const X = import.meta.env.DEV ? lazy(() => import('…')) : null;
 *
 * Vite substitutes the constant, so that consequent is dead code and
 * its chunk is never emitted. This finds exactly those consequents by
 * matching the `?` that follows the guard to its `:` at the same
 * bracket depth — enough to model the one construct in use, and no
 * parser or new dependency. Anything else (a guard written some other
 * way, a dynamic import outside a guard) is deliberately NOT treated as
 * cut: the walk then follows the import, and a mock reached that way
 * fails the suite instead of passing quietly.
 */
function devOnlyRanges(source: string): [number, number][] {
  const ranges: [number, number][] = [];
  const guard = /import\.meta\.env\.DEV\s*\?/g;
  for (const match of source.matchAll(guard)) {
    let depth = 0;
    const start = match.index + match[0].length;
    for (let index = start; index < source.length; index += 1) {
      const char = source[index];
      if ('([{'.includes(char)) depth += 1;
      else if (')]}'.includes(char)) {
        // The conditional ended without its own `:` — stop rather than
        // swallow the rest of the file.
        if (depth === 0) break;
        depth -= 1;
      } else if (char === ':' && depth === 0) {
        ranges.push([start, index]);
        break;
      }
    }
  }
  return ranges;
}

/**
 * Every module a production build actually reaches, walked transitively
 * from an entry point.
 *
 * Static imports are always followed. A dynamic import is followed too
 * — unless it sits inside a DEV-only range, which is how the mock
 * views, the Worker sessions preview and the Completed Work Orders
 * preview leave the graph. Walking instead of listing folders is the
 * point: shared helpers that started life in the mock views (dates,
 * barcode parsing, the toast hook) are production modules today, and a
 * fixed directory list silently stopped covering them.
 *
 * Parameterized on the root so the rules themselves can be tested
 * against fixtures rather than only against the live tree.
 */
function moduleGraph(entry: string, rootDir: string): string[] {
  const seen = new Set<string>();
  const queue = [entry];
  while (queue.length > 0) {
    const file = queue.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    // Comments are stripped first: a module that only MENTIONS the DEV
    // boundary in prose still ships its dynamic imports.
    const source = stripComments(readFileSync(file, 'utf8'));
    const devOnly = devOnlyRanges(source);
    // BOTH quote styles. Prettier normalizes this tree to single
    // quotes, but a walk that silently skipped `import("…")` would let
    // a mock import leave the graph unnoticed — exactly the failure
    // this suite exists to prevent, and it must not depend on a
    // formatting convention holding.
    const specifiers: string[] = [
      // Static: `import x from '…'`, `export … from '…'`, `import '…'`.
      ...Array.from(source.matchAll(/\bfrom\s+['"]([^'"]+)['"]/g), (m) => m[1]),
      ...Array.from(
        source.matchAll(/^\s*import\s+['"]([^'"]+)['"]/gm),
        (m) => m[1],
      ),
    ];
    for (const match of source.matchAll(/\bimport\(\s*['"]([^'"]+)['"]/g)) {
      const cut = devOnly.some(
        ([start, end]) => match.index >= start && match.index < end,
      );
      if (!cut) specifiers.push(match[1]);
    }
    for (const specifier of specifiers) {
      const resolved = resolveModule(file, specifier);
      if (resolved) queue.push(resolved);
    }
  }
  return [...seen].map((file) => relative(rootDir, file)).sort();
}

function mockOffenders(graph: readonly string[]): string[] {
  return graph.filter(
    (module) => module === 'mocks' || module.startsWith(`mocks${sep}`),
  );
}

test('no production module reaches src/mocks/', () => {
  const graph = moduleGraph(join(srcDir, 'main.tsx'), srcDir);
  // The walk is meaningful only if it actually reached the real views.
  expect(graph).toContain(join('app', 'real-views.ts'));
  expect(graph).toContain(join('views', 'work-orders', 'WorkOrdersView.tsx'));
  expect(graph).toContain(join('views', 'scan-station', 'ScanStationView.tsx'));
  expect(graph).toContain(join('api', 'scan-station.ts'));
  expect(graph.length).toBeGreaterThan(30);
  // ...and only if the DEV boundary really cut the mock views away —
  // including the mock Scan Station preview reachable from the real
  // Scan Station view only through its DEV-guarded lazy import.
  expect(graph).toContain(join('app', 'dev-views.ts')); // statically imported
  expect(graph).not.toContain(
    join('views', 'scan-station', 'ScanStationMockView.tsx'),
  );
  expect(graph).not.toContain(
    join('views', 'scan-station', 'mock-area-state.ts'),
  );
  // A still-mock view (the Area Board) stays cut away…
  expect(graph).not.toContain(join('views', 'area-board', 'AreaBoardView.tsx'));
  // …while the Production Board is a REAL view since Phase 11: it
  // ships in every build on `/api/production-board` and imports
  // nothing from src/mocks/ (its long-data preview is inline behind
  // the DEV boundary).
  expect(graph).toContain(
    join('views', 'production-board', 'ProductionBoardView.tsx'),
  );
  expect(graph).toContain(join('api', 'production-board.ts'));
  // The Completed Work Orders page is a REAL view since Phase 10: it
  // ships in every build and imports nothing from src/mocks/.
  expect(graph).toContain(
    join('views', 'work-orders', 'CompletedWorkOrdersView.tsx'),
  );
  expect(graph).toContain(
    join('views', 'scan-station', 'scan-station-allocation-dialog.tsx'),
  );

  expect(mockOffenders(graph)).toEqual([]);
});

/** Write one fixture module, creating parent folders as needed. */
function writeFixture(root: string, relativePath: string, source: string) {
  const path = join(root, relativePath);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, source, 'utf8');
}

function withFixtureTree(
  build: (root: string) => void,
  assert: (root: string) => void,
) {
  const root = mkdtempSync(join(tmpdir(), 'partflow-boundary-'));
  try {
    build(root);
    assert(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test('an ordinary production dynamic import is still walked', () => {
  // The DEV cut must be narrow: a module may hold a DEV-guarded lazy
  // import AND an ordinary code-split import, and only the guarded one
  // leaves the production graph.
  withFixtureTree(
    (root) => {
      writeFixture(root, 'mocks/data.ts', 'export const MOCK = 1;\n');
      writeFixture(root, 'shared.ts', 'export const shared = 2;\n');
      writeFixture(
        root,
        'preview.ts',
        "import { MOCK } from './mocks/data';\nexport const preview = MOCK;\n",
      );
      writeFixture(
        root,
        'feature.ts',
        [
          'export const lazyPreview = import.meta.env.DEV',
          "  ? () => import('./preview')",
          '  : null;',
          "export const split = () => import('./shared');",
          '',
        ].join('\n'),
      );
      writeFixture(root, 'main.tsx', "import './feature';\n");
    },
    (root) => {
      const graph = moduleGraph(join(root, 'main.tsx'), root);
      expect(graph).toContain('feature.ts');
      // Followed: a plain production dynamic import.
      expect(graph).toContain('shared.ts');
      // Not followed: the DEV-guarded lazy import, and what it pulls.
      expect(graph).not.toContain('preview.ts');
      expect(mockOffenders(graph)).toEqual([]);
    },
  );
});

test('a DEV-only lazy import does not drag its mocks into the graph', () => {
  withFixtureTree(
    (root) => {
      writeFixture(root, 'mocks/data.ts', 'export const MOCK = 1;\n');
      writeFixture(
        root,
        'dev-view.ts',
        "import { MOCK } from './mocks/data';\nexport const view = MOCK;\n",
      );
      writeFixture(
        root,
        'registry.ts',
        [
          'export const REGISTRY = import.meta.env.DEV',
          "  ? { view: () => import('./dev-view') }",
          '  : null;',
          '',
        ].join('\n'),
      );
      writeFixture(
        root,
        'main.tsx',
        "import { REGISTRY } from './registry';\n",
      );
    },
    (root) => {
      const graph = moduleGraph(join(root, 'main.tsx'), root);
      expect(graph).toContain('registry.ts');
      expect(graph).not.toContain('dev-view.ts');
      expect(mockOffenders(graph)).toEqual([]);
    },
  );
});

test('double-quoted specifiers are walked like single-quoted ones', () => {
  // Prettier keeps this tree on single quotes, so the walk must not
  // quietly depend on that: a double-quoted static or dynamic import
  // reaching src/mocks/ has to fail exactly the same way.
  withFixtureTree(
    (root) => {
      writeFixture(root, 'mocks/data.ts', 'export const MOCK = 1;\n');
      writeFixture(
        root,
        'helper.ts',
        'import { MOCK } from "./mocks/data";\nexport const helper = MOCK;\n',
      );
      writeFixture(root, 'split.ts', 'export const split = 3;\n');
      writeFixture(
        root,
        'view.ts',
        [
          'import { helper } from "./helper";',
          'export const lazy = () => import("./split");',
          'export const view = helper;',
          '',
        ].join('\n'),
      );
      writeFixture(root, 'main.tsx', 'import "./view";\n');
    },
    (root) => {
      const graph = moduleGraph(join(root, 'main.tsx'), root);
      expect(graph).toContain('view.ts');
      expect(graph).toContain('helper.ts');
      expect(graph).toContain('split.ts');
      expect(mockOffenders(graph)).toEqual([join('mocks', 'data.ts')]);
    },
  );
});

test('a mock reached through a shared transitive module is caught', () => {
  // The failure the folder-list scan used to miss: no production VIEW
  // imports src/mocks/ directly — a shared helper two hops away does.
  withFixtureTree(
    (root) => {
      writeFixture(root, 'mocks/data.ts', 'export const MOCK = 1;\n');
      writeFixture(
        root,
        'helper.ts',
        "import { MOCK } from './mocks/data';\nexport const helper = MOCK;\n",
      );
      writeFixture(
        root,
        'view.ts',
        "import { helper } from './helper';\nexport const view = helper;\n",
      );
      writeFixture(root, 'main.tsx', "import './view';\n");
    },
    (root) => {
      const graph = moduleGraph(join(root, 'main.tsx'), root);
      expect(graph).toContain('helper.ts');
      expect(mockOffenders(graph)).toEqual([join('mocks', 'data.ts')]);
    },
  );
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

test('the Completed Work Orders page is a real view with no mock history', () => {
  // §11.5 has its backend since Phase 10 (completion = full
  // allocation): the real page reads `/api/work-orders/completed` and
  // is imported statically — never a DEV-gated preview over mock data,
  // and never anything from src/mocks/.
  const workOrdersView = readFileSync(
    join(srcDir, 'views', 'work-orders', 'WorkOrdersView.tsx'),
    'utf8',
  );
  expect(workOrdersView).toMatch(/^import .*CompletedWorkOrdersView'/m);
  expect(workOrdersView).not.toContain("import('./CompletedWorkOrdersView')");
  const completedView = readFileSync(
    join(srcDir, 'views', 'work-orders', 'CompletedWorkOrdersView.tsx'),
    'utf8',
  );
  expect(completedView).not.toMatch(/from '\.\.\/\.\.\/mocks\//);
  expect(completedView).toContain('listCompletedWorkOrders');
});

test('the dev-only mock Scan Station preview stays behind the DEV boundary', () => {
  // The Phase 6+ workflows (Machine assignment, DONE / QUEUE, Repair,
  // Scrap, Undo, Worker sessions) exist only as the mock preview: the
  // real Scan Station view may reach it only through the guarded lazy
  // import — never through a static import that would pull the mock
  // Area state and datasets into the production module graph.
  const scanStationView = readFileSync(
    join(srcDir, 'views', 'scan-station', 'ScanStationView.tsx'),
    'utf8',
  );
  expect(scanStationView).not.toMatch(/^import .*ScanStationMockView/m);
  expect(scanStationView).not.toMatch(/^import .*mock-area-state/m);
  expect(scanStationView).toContain('import.meta.env.DEV');
  expect(scanStationView).toContain("import('./ScanStationMockView')");
});
