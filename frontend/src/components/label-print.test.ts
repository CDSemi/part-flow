// The print contract of the ONE label print sheet (LabelPrintSheet,
// GUI_DESIGN §12.6). jsdom applies no print styles, so the contract is
// verified against the stylesheets directly (the presentation-contract
// pattern of responsive-layout.test.ts).

import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, '..');

function cssFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return cssFiles(path);
    return entry.name.endsWith('.css') ? [path] : [];
  });
}

test('a label print never gains a blank page', () => {
  const sheet = readFileSync(join(here, 'label-print.css'), 'utf8');
  // No named page: a change of page name forces a page break, which
  // printed a blank last page whenever any element followed the sheet
  // (such as one a browser extension adds after <body>).
  expect(sheet).not.toMatch(/^\s*page\s*:/m);
  expect(sheet).not.toContain('@page');
  // While a sheet exists everything else leaves the layout — the
  // application under <body> and anything next to <body>.
  expect(sheet).toMatch(
    /html:has\(> body > \.pbx-sheet\) > :not\(body\),\s*body:has\(> \.pbx-sheet\) > :not\(\.pbx-sheet\) \{\s*display: none !important;/s,
  );
});

test('the label print sheet owns the only print styles', () => {
  // The former single-label prints hid `body *` for every print once
  // their stylesheet had loaded; every label now prints through the
  // sheet, and no other stylesheet changes what a print shows.
  const others = cssFiles(src)
    .filter((file) => !file.endsWith('label-print.css'))
    .filter((file) => /@media print|@page/.test(readFileSync(file, 'utf8')))
    .map((file) => relative(src, file));
  expect(others).toEqual([]);
});
