// Small-screen layout contracts (GUI_DESIGN §2.5): every view must be
// browsable by vertical scrolling alone on phone-width viewports — the
// navigation adapts instead of widening the document, the wide
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

test('the Management sub-navigation keeps ONE swipeable row at every width', () => {
  const shell = css('app/shell.css');
  const nav = /\.mgmtnav \{[^}]*}/s.exec(shell)![0];
  // Post-v18: the links never flow onto a second row at intermediate
  // widths — wherever the row does not fit it pans horizontally inside
  // its own scroll region with no visible scrollbar.
  expect(nav).toContain('flex-wrap: nowrap');
  expect(nav).toContain('overflow-x: auto');
  expect(nav).toContain('scrollbar-width: none');
  expect(nav).not.toContain('flex-wrap: wrap');
  expect(shell).toMatch(/\.mgmtnav::-webkit-scrollbar \{\s*display: none;/s);
  // The links and group labels stay unshrinkable one-line items.
  expect(shell).toMatch(
    /\.mgmtnav \.subbtn,\s*\.mgmtnav \.subgrp \{[^}]*flex: none;[^}]*white-space: nowrap;/s,
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
    ['views/work-orders/work-orders.css', 'wo-import-table'],
    ['views/planned-routes/planned-routes.css', 'rt-table'],
    ['views/administration/administration.css', 'admin-table'],
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
});

test('the Machines and Part Numbers toolbars move the search to its own row, then shorten the button labels', () => {
  for (const [file, toolbar] of [
    ['views/machines/machines.css', 'mg-toolbar'],
    ['views/part-numbers/part-numbers.css', 'pnm-toolbar'],
  ] as const) {
    const sheet = css(file);
    // The toolbar is its own size container.
    const block = new RegExp(`\\.${toolbar} \\{[^}]*}`, 's').exec(sheet)![0];
    expect(block).toContain('container-type: inline-size');
    // Below the one-row width: a full-width search row, then the two
    // buttons in equal halves.
    expect(sheet).toMatch(
      new RegExp(
        `@container \\(max-width: \\d+px\\) \\{[^@]*\\.${toolbar} input\\[type='search'\\] \\{\\s*flex: 1 0 100%;[^@]*\\.${toolbar} \\.btn \\{\\s*flex: 1 1 0;`,
        's',
      ),
    );
    // Narrower still: the short labels replace the full ones.
    expect(sheet).toMatch(
      new RegExp(
        `@container \\(max-width: \\d+px\\) \\{[^@]*\\.${toolbar} \\.lbl-full \\{\\s*display: none;[^@]*\\.${toolbar} \\.lbl-short \\{\\s*display: inline;`,
        's',
      ),
    );
  }
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

test('a code in a history row or a flow note never breaks at its hyphen in a narrow panel', () => {
  // Tracking's detail panel and the archive viewer share the rule: the
  // codes of a line (`QF-44`, `ST-PACK`) are `wholecode` spans.
  expect(css('views/tracking/tracking.css')).toMatch(
    /\.wholecode \{\s*white-space: nowrap;\s*\}/s,
  );
});
