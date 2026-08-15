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

test('the Management sub-navigation wraps on wide viewports and pans on phone widths', () => {
  const shell = css('app/shell.css');
  const nav = /\.mgmtnav \{[^}]*}/s.exec(shell)![0];
  expect(nav).toContain('flex-wrap: wrap');
  // Phone: ONE swipeable row inside its own scroll region — no
  // visible scrollbar, and never a widened document.
  expect(shell).toMatch(
    /@media \(max-width: \d+px\) \{[^@]*\.mgmtnav \{[^}]*flex-wrap: nowrap;[^}]*overflow-x: auto;[^}]*scrollbar-width: none;/s,
  );
});

test('the top navigation collapses behind a menu button on phone widths', () => {
  const shell = css('app/shell.css');
  // Wide viewports: the button is hidden and the links wrapper is
  // layout-transparent — the links stay direct flex items of the nav.
  expect(shell).toMatch(/\.menubtn \{\s*display: none;/s);
  expect(shell).toMatch(/\.appnav-links \{\s*display: contents;/s);
  // Phone: links hidden until the menu opens as a vertical panel; the
  // development preview tag disappears and the Dark/Light control
  // keeps only its icon (mode word visually hidden, never removed).
  expect(shell).toMatch(
    /@media \(max-width: \d+px\) \{[^@]*\.appnav-links \{\s*display: none;/s,
  );
  expect(shell).toMatch(/\.appnav-links\.open \{[^}]*flex-direction: column;/s);
  expect(shell).toMatch(/\.appnav \.mock-tag \{\s*display: none;/s);
  expect(shell).toMatch(/\.appnav \.tlabel \{[^}]*clip: rect/s);
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
