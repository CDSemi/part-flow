// Small-screen layout contracts (GUI_DESIGN §2.5): every view must be
// browsable by vertical scrolling alone on phone-width viewports — the
// navigation wraps instead of widening the document, the wide
// Management tables collapse to stacked rows with inline column
// captions, and the Area Board All Areas board stacks its columns.
// jsdom applies no media queries, so these contracts are verified
// against the stylesheets directly (the established presentation-
// contract pattern of scan-station.test.tsx).

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));
const css = (relative: string) => readFileSync(join(here, relative), 'utf8');

test('the Management sub-navigation wraps instead of widening the document', () => {
  const shell = css('app/shell.css');
  const nav = /\.mgmtnav \{[^}]*}/s.exec(shell)![0];
  expect(nav).toContain('flex-wrap: wrap');
});

test('the wide Management tables collapse to stacked rows with inline captions', () => {
  const tables = [
    ['views/machines/machines.css', 'mg-table'],
    ['views/tracking/tracking.css', 'tk-table'],
    ['views/work-orders/work-orders.css', 'wolist'],
    ['views/planned-routes/planned-routes.css', 'rt-table'],
    ['views/administration/administration.css', 'ad-table'],
  ] as const;
  for (const [file, table] of tables) {
    const sheet = css(file);
    // One max-width collapse block: header row hidden, table displayed
    // as stacked blocks, data-label rendered as the inline caption.
    const collapse = new RegExp(
      `@media \\(max-width: \\d+px\\) \\{[^@]*?\\.${table} thead \\{\\s*display: none;`,
      's',
    );
    expect(sheet).toMatch(collapse);
    expect(sheet).toMatch(
      new RegExp(`\\.${table} td\\[data-label]::before`, 's'),
    );
    expect(sheet).toContain('content: attr(data-label)');
  }
});

test('the All Areas board stacks its Area columns at phone widths', () => {
  const board = css('views/area-board/area-board.css');
  const stack =
    /@media \(max-width: \d+px\) \{[^@]*\.ms-scroll \{[^}]*flex-wrap: wrap;[^}]*overflow-x: visible;/s.exec(
      board,
    );
  expect(stack).not.toBeNull();
  expect(board).toMatch(/\.ms-col \{\s*flex: 1 1 100%;/s);
});
