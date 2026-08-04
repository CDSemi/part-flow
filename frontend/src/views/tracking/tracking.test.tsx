import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test } from 'vitest';

import { TrackingView } from './TrackingView';

// Tracking detail regressions (GUI_DESIGN §7): route steps and arrows
// as separate sibling flex items, the immutable AREA_COMPLETED history
// entry, and the ready-to-transfer (READY_TO_TRANSFER) presentation
// kept distinct from Stockroom completion.

beforeEach(() => {
  window.history.replaceState({}, '', '/management/tracking');
});

afterEach(cleanup);

// The default selection is the only PN with mock detail data, so the
// detail panel (flows, history, distribution) renders without clicks.
function flowBlock(id: string): Element {
  const block = Array.from(document.querySelectorAll('.qflow')).find(
    (el) => el.querySelector('.qf-id')?.textContent === id,
  );
  expect(block).toBeDefined();
  return block!;
}

test('route steps and arrows are separate siblings in alternating order', () => {
  render(<TrackingView />);

  const routes = Array.from(document.querySelectorAll('.route'));
  expect(routes.length).toBe(2); // one per Quantity Flow

  for (const route of routes) {
    const children = Array.from(route.children);
    // Document order alternates step, arrow, step, … — every child is
    // one of the two, so an arrow is never nested inside a step wrapper.
    children.forEach((el, i) => {
      const expected = i % 2 === 0 ? 'rstep' : 'rarrow';
      expect(el.classList.contains(expected)).toBe(true);
    });
    // Starts and ends with a step: n steps and n-1 arrows.
    expect(children.length % 2).toBe(1);
  }

  const planned = flowBlock('QF-0140');
  expect(planned.querySelectorAll('.route > .rstep').length).toBe(5);
  expect(planned.querySelectorAll('.route > .rarrow').length).toBe(4);
});

test('the Floating trace keeps repeated Areas and the Repair marker', () => {
  render(<TrackingView />);

  const floating = flowBlock('QF-0141');
  const steps = Array.from(floating.querySelectorAll('.rstep'), (el) =>
    el.textContent?.trim(),
  );
  // Cut appears twice — the trace is Movement history, and the second
  // visit is the explicitly marked Repair return.
  expect(steps).toEqual(['Material', 'Cut', 'Lathe', 'Cut ⟲ REPAIR']);
  expect(floating.querySelector('.rstep.repair .repairmark')).not.toBeNull();
});

test('Movement history lists AREA_COMPLETED with the success badge tone', () => {
  render(<TrackingView />);

  const badge = Array.from(document.querySelectorAll('.mv .mtype')).find(
    (el) => el.textContent === 'AREA_COMPLETED',
  );
  expect(badge).toBeDefined();
  expect(badge!.classList.contains('don')).toBe(true);

  const row = badge!.closest('li')!;
  expect(row.textContent).toContain('Lathe 3 → Lathe finished rack');
  expect(row.textContent).toContain('qty 1');
});

test('the finished-rack state never adds a route step', () => {
  render(<TrackingView />);

  // QF-0140 holds the ready-to-transfer piece: its route keeps exactly
  // its five Area steps — completion happens inside the Lathe step.
  const planned = flowBlock('QF-0140');
  const steps = Array.from(
    planned.querySelectorAll('.rstep'),
    (el) => el.textContent ?? '',
  );
  expect(steps).toEqual(['Material', 'Cut', 'Lathe', 'Deburr', 'Stockroom']);

  // No trace anywhere gains a fake finished-rack step.
  for (const route of document.querySelectorAll('.route')) {
    expect(route.textContent).not.toMatch(/rack/i);
  }
});

test('ready-to-transfer is presented distinctly and never as Stocked', () => {
  render(<TrackingView />);

  const done = document.querySelector('.dist .drow.done');
  expect(done).not.toBeNull();
  expect(done!.textContent).toContain('ready to transfer');
  expect(done!.querySelector('.q')?.textContent).toBe('1');
  expect(done!.textContent).not.toMatch(/stocked/i);
  // The Area is the location — the Machine no longer holds the piece.
  expect(done!.querySelector('.nm')?.textContent).not.toContain('Lathe 3');

  const note = document.querySelector('.donenote');
  expect(note?.textContent).toContain(
    'Completed processing at Lathe — ready to transfer',
  );
  expect(note?.textContent).toContain('Completed at Lathe 3');
  expect(note?.textContent).not.toMatch(/stocked/i);
});

test('the Movement history section stays read-only', () => {
  render(<TrackingView />);

  const section = document.querySelector('.mv')!.closest('.tk-sec')!;
  // Immutable audit data: no edit, delete, or any other interactive
  // affordance exists anywhere in the history section.
  expect(section.querySelectorAll('button, a, input, select').length).toBe(0);
});

/* ============ Detail selection toggle and close (GUI v13) ============ */

test('clicking the selected PN row again unselects it and releases the panel column', () => {
  render(<TrackingView />);

  const row = document.querySelector('.tk-table .rowbtn') as HTMLElement;
  expect(row.getAttribute('aria-pressed')).toBe('true');
  expect(document.querySelector('.tk-right')).not.toBeNull();
  expect(document.querySelector('.tk-wrap')?.className).not.toContain(
    'noselect',
  );

  fireEvent.click(row);
  // Unselected: the detail panel is gone entirely — no reserved empty
  // column — and the wrapper switches to the full-width layout.
  expect(row.getAttribute('aria-pressed')).toBe('false');
  expect(document.querySelector('.tk-right')).toBeNull();
  expect(document.querySelector('.tk-wrap')?.className).toContain('noselect');

  // Clicking again re-selects and re-opens the details.
  fireEvent.click(row);
  expect(row.getAttribute('aria-pressed')).toBe('true');
  expect(document.querySelector('.tk-right')).not.toBeNull();
});

test('the detail panel closes through its accessible X button', () => {
  render(<TrackingView />);

  const close = screen.getByRole('button', { name: 'Close details' });
  expect(close.closest('.tk-right')).not.toBeNull();
  fireEvent.click(close);

  expect(document.querySelector('.tk-right')).toBeNull();
  expect(
    document.querySelector('.tk-table .rowbtn')?.getAttribute('aria-pressed'),
  ).toBe('false');
});

test('selecting a different PN keeps the panel open (with its empty-detail state)', () => {
  render(<TrackingView />);

  const rows = document.querySelectorAll('.tk-table .rowbtn');
  expect(rows.length).toBeGreaterThan(1);
  fireEvent.click(rows[1]);

  expect(rows[1].getAttribute('aria-pressed')).toBe('true');
  expect(rows[0].getAttribute('aria-pressed')).toBe('false');
  // The panel stays (this PN has no mock detail data) and still offers
  // the close control.
  expect(document.querySelector('.tk-right')).not.toBeNull();
  expect(
    screen.getByRole('button', { name: 'Close details' }),
  ).toBeInTheDocument();
});

test('the detail panel carries the subtle elevation in the stylesheet', async () => {
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const { dirname, join } = await import('node:path');
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'tracking.css'),
    'utf8',
  );
  expect(css).toMatch(/\.tk-right \{[^}]*box-shadow: var\(--shadow\)/);
  expect(css).toMatch(/\.tk-wrap\.noselect \{[^}]*grid-template-columns/);
});
