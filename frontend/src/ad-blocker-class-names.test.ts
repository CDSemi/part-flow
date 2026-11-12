// Ad blockers hide page elements by class name on every site. EasyList,
// the default list of uBlock Origin, AdBlock, AdBlock Plus, AdGuard and
// Brave, holds generic rules such as `##.ad-wrap:not(#…)` and
// `##.ad-main`: an Administration view whose classes were named `ad-*`
// (short for "administration") rendered as an empty page for every user
// with an ad blocker. No class name may therefore contain `ad` or `ads`
// as a separate word (`ad`, `ad-wrap`, `top-ad`, `ads_box`). The rule is
// verified against the stylesheets and the literal `className` values,
// the established presentation-contract pattern of responsive-layout.test.ts.

import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from 'vitest';

const srcDir = dirname(fileURLToPath(import.meta.url));

function filesUnder(dir: string, extension: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return filesUnder(path, extension);
    return entry.name.endsWith(extension) ? [path] : [];
  });
}

/** True for a class name that has `ad` or `ads` as a word of its own. */
function looksLikeAnAdClass(name: string): boolean {
  return /(^|[-_])ads?($|[-_])/i.test(name);
}

function classNamesInUse(): Map<string, Set<string>> {
  const used = new Map<string, Set<string>>();
  const add = (name: string, file: string) => {
    const files = used.get(name) ?? new Set<string>();
    files.add(relative(srcDir, file));
    used.set(name, files);
  };
  for (const file of filesUnder(srcDir, '.css')) {
    for (const match of readFileSync(file, 'utf8').matchAll(
      /\.([A-Za-z_][\w-]*)/g,
    )) {
      add(match[1], file);
    }
  }
  for (const file of filesUnder(srcDir, '.tsx')) {
    for (const match of readFileSync(file, 'utf8').matchAll(
      /className="([^"]*)"/g,
    )) {
      for (const name of match[1].split(/\s+/).filter(Boolean)) {
        add(name, file);
      }
    }
  }
  return used;
}

test('the rule recognizes the class names that ad blockers hide', () => {
  for (const name of ['ad', 'ads', 'ad-wrap', 'ad-main', 'top-ad', 'ads_box']) {
    expect(looksLikeAnAdClass(name), name).toBe(true);
  }
  for (const name of ['admin', 'admin-wrap', 'head', 'thread', 'load-pad']) {
    expect(looksLikeAnAdClass(name), name).toBe(false);
  }
});

test('no class name in use looks like an ad to an ad blocker', () => {
  const used = classNamesInUse();
  expect(used.size).toBeGreaterThan(100);
  const offending = [...used]
    .filter(([name]) => looksLikeAnAdClass(name))
    .map(([name, files]) => `.${name} (${[...files].join(', ')})`);
  expect(offending).toEqual([]);
});
