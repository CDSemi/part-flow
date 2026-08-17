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

test('the active Machines list sheds columns, then compacts to single-line wrapping rows', () => {
  const sheet = css('views/machines/machines.css');
  // Column shedding: Asset first, then Assigned now — each inside its
  // own max-width block.
  expect(sheet).toMatch(
    /@media \(max-width: \d+px\) \{[^@]*\.mg-table \.mg-metacol \{\s*display: none;/s,
  );
  expect(sheet).toMatch(
    /@media \(max-width: \d+px\) \{[^@]*\.mg-table \.mg-assignedcol \{\s*display: none;/s,
  );
  // Compact mode: one flex row per Machine that wraps only when
  // needed — never the permanently stacked block presentation (that
  // stays scoped to the retired table).
  expect(sheet).toMatch(
    /\.mg-table\.mg-active tbody tr \{[^}]*display: flex;[^}]*flex-wrap: wrap;/s,
  );
  expect(sheet).toMatch(/\.mg-retired \.mg-table thead \{\s*display: none;/s);
  // The toolbar keeps the primary action on the search row: the row
  // never wraps, the search field shrinks instead.
  const toolbar = /\.mg-toolbar \{[^}]*}/s.exec(sheet)![0];
  expect(toolbar).toContain('flex-wrap: nowrap');
});

test('the Management sub-navigation is a sticky frosted-glass bar on its own panel tone', () => {
  const shell = css('app/shell.css');
  const nav = /\.mgmtnav \{[^}]*}/s.exec(shell)![0];
  // Its own translucent panel surface — deliberately NOT the top
  // navigation's --navglass — plus the backdrop blur, and sticky so
  // scrolled content actually passes beneath it.
  expect(nav).toContain('background: var(--panelglass)');
  expect(nav).not.toContain('--navglass');
  expect(nav).toContain('backdrop-filter: blur');
  expect(nav).toContain('position: sticky');
  // Inside the scrolling <main> flex column the bar must keep its
  // natural height — the phone swipe mode's overflow-x drops the
  // automatic flex minimum to 0 and tall content would crush the row.
  expect(nav).toContain('flex: none');
  // Scroll-direction condensing: a shrunk variant with reduced chip
  // height exists (the class itself is driven by AppShell).
  expect(shell).toMatch(/\.mgmtnav\.shrunk \{[^}]*padding/s);
  expect(shell).toMatch(/\.mgmtnav\.shrunk \.subbtn \{[^}]*min-height/s);
});

test('the narrow Area Board stacks the overview (Summary on) or pages the details with snap', () => {
  const board = css('views/area-board/area-board.css');
  // Summary ON: the stacked full-width All Areas column list —
  // vertical scrolling only.
  const stack =
    /@media \(max-width: \d+px\) \{[^@]*\.ms-scroll\.wrap \{[^}]*flex-wrap: wrap;[^}]*overflow-x: visible;/s.exec(
      board,
    );
  expect(stack).not.toBeNull();
  expect(board).toMatch(/\.ms-scroll\.wrap \.ms-col \{\s*flex: 1 1 100%;/s);
  // Summary OFF (post-v18, the default): a swipeable one-detail-per-
  // page carousel — mandatory horizontal snap, no visible scrollbar,
  // and near-full-width pages so the neighbors peek in at the edges.
  expect(board).toMatch(
    /@media \(max-width: \d+px\) \{[^@]*\.abd-scroll \{[^}]*scroll-snap-type: x mandatory;[^}]*scrollbar-width: none;/s,
  );
  expect(board).toMatch(
    /\.abd-scroll \.abd-page \{[^}]*scroll-snap-align: center/s,
  );
});
