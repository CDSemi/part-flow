import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from 'vitest';

// Rendered-copy guard (GUI_DESIGN §3.10): normal user-facing copy never
// exposes developer-oriented wording — implementation constants, mock/
// persistence explanations, or architecture notes. The guard scans the
// renderable frontend sources (comments stripped, so engineering notes
// stay allowed) and the current interactive mockup for banned phrases.
//
// Canonical Movement type names (RECEIVED, AREA_COMPLETED, SCRAPPED, …)
// are deliberately NOT banned: they are legitimate audit/history data in
// the Movement history, type badges, and confirmation `Recorded event`
// rows. The ban list targets developer phrasing only; scoped exceptions
// go into ALLOWED below instead of weakening the list.

const srcDir = dirname(fileURLToPath(import.meta.url));
const mockupsDir = join(srcDir, '..', '..', 'docs', 'mockups');

/** Developer-facing wording that must never render in normal UI. */
const BANNED_PHRASES: string[] = [
  '(mock)',
  'presentation only',
  'presentation-only',
  'nothing persisted',
  'persisted to the backend',
  'Development note',
  'Development mock',
  'local mock data',
  'local mock state',
  'mock data',
  'mock state',
  'Phase 2 preview',
  'no Quantity Flow',
  'no Movement,',
  'priority_rank',
  'movement_reason',
  'derived state',
  'deterministic tie-breaker',
  'derived from Movement',
  'separate from Movement',
  'immutable — corrections',
  '🔥#n before the PN',
  'Hot Part is a label',
];

/**
 * Scoped exceptions: audit/history surfaces (or equivalent) where a
 * listed phrase is legitimate data rather than developer commentary.
 * Keep this list explicit and short — never widen it casually.
 */
const ALLOWED: { file: RegExp; phrase: string }[] = [];

/**
 * Strip comments from a TS/TSX/JS source so engineering notes never
 * trip the guard. String literals are preserved (a banned phrase inside
 * a rendered string still fails); block comments, line comments and
 * HTML comments are removed with a small quote-aware scanner.
 */
function stripComments(source: string): string {
  let out = '';
  let i = 0;
  let quote: string | null = null;
  while (i < source.length) {
    const ch = source[i];
    const next = source[i + 1];
    if (quote) {
      out += ch;
      if (ch === '\\') {
        out += next ?? '';
        i += 2;
        continue;
      }
      // Ordinary quotes never span lines; ending the state at a
      // newline keeps prose apostrophes (in HTML text) from poisoning
      // the parser state. Template literals (`) may span lines.
      if (ch === quote || (ch === '\n' && quote !== '`')) quote = null;
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') {
      quote = ch;
      out += ch;
      i += 1;
      continue;
    }
    if (ch === '/' && next === '*') {
      const end = source.indexOf('*/', i + 2);
      i = end === -1 ? source.length : end + 2;
      continue;
    }
    if (ch === '/' && next === '/') {
      const end = source.indexOf('\n', i);
      i = end === -1 ? source.length : end;
      continue;
    }
    if (ch === '<' && source.startsWith('<!--', i)) {
      const end = source.indexOf('-->', i + 4);
      i = end === -1 ? source.length : end + 3;
      continue;
    }
    out += ch;
    i += 1;
  }
  return out;
}

function walk(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walk(path));
    else files.push(path);
  }
  return files;
}

/** Renderable frontend sources: view/component/mock/app code, no tests. */
function renderableSources(): string[] {
  return ['views', 'components', 'mocks', 'app']
    .flatMap((sub) => walk(join(srcDir, sub)))
    .filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f));
}

function currentMockups(): string[] {
  return readdirSync(mockupsDir)
    .filter((f) => f.endsWith('.html'))
    .map((f) => join(mockupsDir, f));
}

test('normal rendered UI copy contains no developer-facing wording', () => {
  const offenders: string[] = [];
  for (const file of [...renderableSources(), ...currentMockups()]) {
    const content = stripComments(readFileSync(file, 'utf8'));
    const rel = relative(srcDir, file);
    for (const phrase of BANNED_PHRASES) {
      if (!content.includes(phrase)) continue;
      if (ALLOWED.some((a) => a.file.test(rel) && a.phrase === phrase)) {
        continue;
      }
      offenders.push(`${rel}: "${phrase}"`);
    }
  }
  expect(offenders, offenders.join('\n')).toEqual([]);
});

test('the copy audit really covers the current mockup', () => {
  // Exactly one current mockup lives in docs/mockups (previous versions
  // are archived); the guard must scan it, so its presence is asserted.
  expect(currentMockups().length).toBe(1);
});

test('canonical Movement names remain allowed as audit/history data', () => {
  // The guard must never rot into banning legitimate audit vocabulary:
  // the Movement history mock still carries canonical type names.
  const tracking = readFileSync(join(srcDir, 'mocks', 'tracking.ts'), 'utf8');
  for (const name of ['AREA_COMPLETED', 'TRANSFERRED', 'SCRAPPED']) {
    expect(tracking).toContain(name);
  }
});
