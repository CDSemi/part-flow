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
//     exceptions are the development-only conveniences (the
//     `?preview=mock` Scan Station mock view and the Scan Station demo
//     badges), each reachable only through an
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

test('every approved view is a real view that ships in every build', () => {
  // Management → Machines, Administration (Phase 3.5), Management →
  // Work Orders (Phase 4), the Scan Station (Phase 5), the Production
  // Board, Area Board and PN Tracking (Phase 11), Management →
  // Priority (Phase 12), Management → Part Numbers and Management →
  // Planned Routes (Phase 13) read real server state — all ten live in
  // the always-available registry; no development-only view registry
  // and no not-connected placeholder remain.
  expect(Object.keys(REAL_VIEWS).sort()).toEqual(
    [
      'administration',
      'area-board',
      'machines',
      'part-numbers',
      'planned-routes',
      'priority',
      'production-board',
      'scan-station',
      'tracking',
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
 * views, the Scan Station demo badges and the Completed Work Orders
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
  // the mock Scan Station preview is reachable from the real Scan
  // Station view only through its DEV-guarded lazy import.
  expect(graph).not.toContain(
    join('views', 'scan-station', 'ScanStationMockView.tsx'),
  );
  expect(graph).not.toContain(
    join('views', 'scan-station', 'mock-area-state.ts'),
  );
  // Management → Planned Routes is a REAL view since Phase 13 on
  // `/api/route-templates`: it ships in every build and imports nothing
  // from src/mocks/ (its long-data preview is built at runtime behind
  // the DEV boundary).
  expect(graph).toContain(
    join('views', 'planned-routes', 'PlannedRoutesView.tsx'),
  );
  expect(graph).toContain(join('api', 'route-templates.ts'));
  // Priority Management is a REAL view since Phase 12: it ships in
  // every build on `/api/hot-list` and imports nothing from
  // src/mocks/.
  expect(graph).toContain(join('views', 'priority', 'PriorityView.tsx'));
  expect(graph).toContain(join('api', 'hot-list.ts'));
  // Management → Part Numbers is a REAL view since Phase 13 on
  // `/api/part-numbers`, with the shared `Edit Part Number` dialog —
  // neither imports anything from src/mocks/ (the long-data preview
  // sits behind the DEV boundary).
  expect(graph).toContain(join('views', 'part-numbers', 'PartNumbersView.tsx'));
  expect(graph).toContain(join('components', 'EditPartNumberDialog.tsx'));
  // So are the Production Board, the Area Board and PN Tracking —
  // REAL views since Phase 11: they ship in every build on
  // `/api/production-board`, `/api/area-board` and `/api/tracking` and
  // import nothing from src/mocks/ (their long-data previews sit behind
  // the DEV boundary).
  expect(graph).toContain(
    join('views', 'production-board', 'ProductionBoardView.tsx'),
  );
  expect(graph).toContain(join('api', 'production-board.ts'));
  expect(graph).toContain(join('views', 'area-board', 'AreaBoardView.tsx'));
  expect(graph).toContain(join('api', 'area-board.ts'));
  expect(graph).toContain(join('views', 'tracking', 'TrackingView.tsx'));
  expect(graph).toContain(join('api', 'tracking.ts'));
  // The PN audit trail and the `Change priority` hand-off (Phase 14
  // slice 7) ship with Tracking and import nothing from src/mocks/.
  expect(graph).toContain(join('views', 'tracking', 'AuditTrailDialog.tsx'));
  expect(graph).toContain(join('views', 'tracking', 'audit-trail-text.ts'));
  expect(graph).toContain(join('api', 'audit-trail.ts'));
  expect(graph).toContain(join('views', 'priority', 'priority-focus.ts'));
  // Both views render the ONE shared Area monitoring model.
  expect(graph).toContain(join('api', 'area-inventory.ts'));
  expect(graph).toContain(join('views', 'area-presentation.ts'));
  // The Completed Work Orders page is a REAL view since Phase 10: it
  // ships in every build and imports nothing from src/mocks/.
  expect(graph).toContain(
    join('views', 'work-orders', 'CompletedWorkOrdersView.tsx'),
  );
  expect(graph).toContain(
    join('views', 'scan-station', 'scan-station-allocation-dialog.tsx'),
  );
  // User sign-in (Phase 14): the session provider, the account chip
  // and its dialogs, and the Administration password and sign-in
  // settings controls ship in every build on `/api/session`,
  // `/api/setup`, `/api/users` and `/api/policies` and import nothing
  // from src/mocks/.
  for (const module of [
    join('app', 'session-provider.tsx'),
    join('api', 'session.ts'),
    join('api', 'setup.ts'),
    join('components', 'AccountChip.tsx'),
    join('components', 'SignInDialog.tsx'),
    join('components', 'ChangePasswordDialog.tsx'),
    join('components', 'FirstRunSetupDialog.tsx'),
    join('views', 'administration', 'SetPasswordDialog.tsx'),
    join('views', 'administration', 'SignInSettingsPanel.tsx'),
  ]) {
    expect(graph).toContain(module);
  }

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

test('Worker sessions is a real section and the demo badges stay behind the DEV boundary', () => {
  // Phase 13 replaced the development-only Worker sessions preview with
  // the real section: the preview module is gone and Administration
  // reaches no mock data at all.
  const adminDir = join(srcDir, 'views', 'administration');
  expect(existsSync(join(adminDir, 'WorkerSessionsPreview.tsx'))).toBe(false);
  const adminView = readFileSync(
    join(adminDir, 'AdministrationView.tsx'),
    'utf8',
  );
  expect(adminView).not.toContain('WorkerSessionsPreview');
  expect(adminView).not.toContain('mocks/');
  // The sign-in modal and the badge-confirmation gate reach the
  // development-only demo badges ONLY through the one slot module and
  // its import.meta.env.DEV-guarded lazy import — never a static import
  // that would put them into the production graph.
  const scanStationDir = join(srcDir, 'views', 'scan-station');
  const slot = readFileSync(
    join(scanStationDir, 'scan-station-dev-badges-slot.tsx'),
    'utf8',
  );
  expect(slot).not.toMatch(/^import .*scan-station-dev-badges'/m);
  expect(slot).toMatch(
    /import\.meta\.env\.DEV\s*\?\s*lazy\(\(\) =>\s*import\('\.\/scan-station-dev-badges'\)/,
  );
  const importers = readdirSync(scanStationDir).filter((name) =>
    readFileSync(join(scanStationDir, name), 'utf8').includes(
      "'./scan-station-dev-badges'",
    ),
  );
  expect(importers).toEqual(['scan-station-dev-badges-slot.tsx']);
  for (const name of [
    'scan-station-sign-in-dialog.tsx',
    'scan-station-badge-gate.tsx',
  ]) {
    const source = readFileSync(join(scanStationDir, name), 'utf8');
    expect(source).toMatch(
      /^import \{ DevBadgesSlot \} from '\.\/scan-station-dev-badges-slot';$/m,
    );
    expect(source).not.toMatch(/mocks\//);
    expect(source).not.toContain('ScanStationMockView');
  }
  expect(
    readFileSync(join(scanStationDir, 'scan-station-dev-badges.tsx'), 'utf8'),
  ).toContain('Demo badges');
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
  // Every approved Scan Station workflow is real; the mock view is only
  // a retained `?preview=mock` development preview of the approved
  // design. The real Scan Station view may reach it only through the
  // guarded lazy import — never through a static import that would
  // pull the mock Area state and datasets into the production module
  // graph.
  const scanStationView = readFileSync(
    join(srcDir, 'views', 'scan-station', 'ScanStationView.tsx'),
    'utf8',
  );
  expect(scanStationView).not.toMatch(/^import .*ScanStationMockView/m);
  expect(scanStationView).not.toMatch(/^import .*mock-area-state/m);
  expect(scanStationView).toContain('import.meta.env.DEV');
  expect(scanStationView).toContain("import('./ScanStationMockView')");
});

/* ============ No hard-coded display settings (Phase 13 slice 9) ============ */

// The Due Soon warning policy and the Production Board rotation timing
// are server configuration (Administration → Settings, Administration →
// Department display settings). No production source may restate them:
// the only non-test sources allowed to hold such literals are the mocks
// and the DEV-only preview values of the `?state=` previews.

const PREVIEW_MODULE = join(srcDir, 'views', 'display-settings-preview.ts');

/** Every non-test production source under src/ (mocks and the DEV-only
 * preview module excluded). */
function productionSources(dir = srcDir): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (path === join(srcDir, 'mocks')) continue;
      files.push(...productionSources(path));
    } else if (
      /\.tsx?$/.test(entry.name) &&
      !/\.test\.tsx?$/.test(entry.name) &&
      entry.name !== 'setupTests.ts' &&
      path !== PREVIEW_MODULE
    ) {
      files.push(path);
    }
  }
  return files;
}

/** A policy or timing property written with a numeric literal (an
 * interface member typed `: number` never matches). */
const SETTING_LITERAL =
  /\b(minDays|leadTimePercent|maxDays|secondsPerRow|minPageSeconds|ratio)\s*:\s*-?\d/;

test('the setting-literal pattern catches a planted policy value', () => {
  expect(SETTING_LITERAL.test('const policy = { minDays: 2 };')).toBe(true);
  expect(SETTING_LITERAL.test('{ secondsPerRow: 3, x: 1 }')).toBe(true);
  expect(SETTING_LITERAL.test('  minDays: number;')).toBe(false);
});

test('no production source hard-codes the Due Soon policy or the rotation timing', () => {
  const sources = productionSources();
  expect(sources.length).toBeGreaterThan(50);
  const offenders: string[] = [];
  for (const file of sources) {
    const source = readFileSync(file, 'utf8');
    const name = relative(srcDir, file).split(sep).join('/');
    for (const banned of [
      'DEFAULT_DUE_SOON_POLICY',
      'ROTATE_MS_PER_ROW',
      'ROTATE_MS_MIN',
    ]) {
      if (source.includes(banned)) offenders.push(`${name}: ${banned}`);
    }
    if (SETTING_LITERAL.test(source)) offenders.push(`${name}: literal`);
  }
  expect(offenders).toEqual([]);

  const boardLogic = readFileSync(
    join(srcDir, 'views', 'production-board', 'board-logic.ts'),
    'utf8',
  );
  expect(boardLogic).not.toMatch(/\b3_000\b/);
  expect(boardLogic).not.toMatch(/\b6_000\b/);
});

test('the display-settings preview module exports only DEV-guarded values', () => {
  const source = stripComments(readFileSync(PREVIEW_MODULE, 'utf8'));
  const exports = Array.from(source.matchAll(/^export\b.*$/gm), (m) => m[0]);
  expect(exports.length).toBeGreaterThan(0);
  for (const statement of exports) {
    expect(statement).toMatch(
      /^export const \w+(:[^=]+)?= import\.meta\.env\.DEV$/,
    );
  }
});
