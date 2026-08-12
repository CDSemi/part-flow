import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../App';

// Development-only state previews (?state=…) drive the deterministic
// loading / empty / error / long-data representations. The override is
// compiled away outside development builds; vitest runs in DEV mode.

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ status: 'ok' }), { status: 200 }),
      ),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderAt(path: string) {
  window.history.replaceState({}, '', path);
  return render(<App />);
}

test('a view renders its loading state', async () => {
  renderAt('/management/tracking?state=loading');

  expect(
    await screen.findByRole('status', { name: 'Loading PN Tracking' }),
  ).toBeInTheDocument();
});

test('a view renders its empty state', async () => {
  renderAt('/management/tracking?state=empty');

  expect(
    await screen.findByText(/No PNs match the current filters/),
  ).toBeInTheDocument();
});

test('a view renders its error state', async () => {
  renderAt('/management/area-board?state=error');

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Area Board data could not be loaded.',
  );
});

test('the Production Board renders long data without wrapping the PN', async () => {
  renderAt('/production-board?state=long');

  // Over-long identifier from the long-data mock set (page 1). Every
  // row also renders once more inside the hidden measurement copy
  // (v15 height-aware pagination), so the PN legitimately appears
  // twice — the visible instance is the first.
  const [longPn] = await screen.findAllByText(/0118-40-0022-07-0455-88-REV-C/);
  expect(longPn).toBeInTheDocument();
  // The long description renders on its own secondary line and never
  // displaces the quantity / due / total columns of the row.
  expect(
    screen.getAllByText(/MANIFOLD ASSY, 6-PORT ANODIZED, W\/ FITTINGS 1\/4 NPT/)
      .length,
  ).toBeGreaterThanOrEqual(1);
  // The long list paginates (25 rows → 3 fallback pages in jsdom) and
  // honestly reports its rotation: the per-page countdown indicator
  // exists only while more than one page really rotates (v15 replaced
  // the fixed `rotates every 12 s` claim with the live indicator).
  expect(screen.getByText('Page 1 / 3')).toBeInTheDocument();
  // The rotation deadline is armed in a post-commit effect — wait for
  // the indicator instead of asserting synchronously.
  await waitFor(() =>
    expect(document.querySelector('.pb-foot .pb-rotate')).toBeInTheDocument(),
  );
});

test('the Tracking long-data state renders 30 or more rows', async () => {
  renderAt('/management/tracking?state=long');

  await screen.findByText('0114-60-0101-00');
  // 1 long-identifier sample + 6 base rows + 28 generated rows = 35.
  const generated = screen.getAllByText(/^0114-60-01\d\d-00$/);
  expect(generated.length).toBeGreaterThanOrEqual(28);
  const listRows = screen
    .getAllByRole('row')
    .filter((row) => row.closest('.tk-table'));
  expect(listRows.length).toBeGreaterThanOrEqual(31); // header + 30+ rows
});
