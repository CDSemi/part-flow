import { cleanup, render, screen } from '@testing-library/react';
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

test('a view renders its loading state', () => {
  renderAt('/management/tracking?state=loading');

  expect(
    screen.getByRole('status', { name: 'Loading Tracking' }),
  ).toBeInTheDocument();
});

test('a view renders its empty state', () => {
  renderAt('/management/tracking?state=empty');

  expect(
    screen.getByText(/No PNs match the current filters/),
  ).toBeInTheDocument();
});

test('a view renders its error state', () => {
  renderAt('/management/area-board?state=error');

  expect(screen.getByRole('alert')).toHaveTextContent(
    'Area Board data could not be loaded.',
  );
});

test('the Production Board renders long data without wrapping the PN', () => {
  renderAt('/production-board?state=long');

  // Over-long identifier from the long-data mock set.
  const longPn = screen.getByText(
    /PF-MANIFOLD-ASSY-00847-REV-C-EXTENDED-VALIDATION/,
  );
  expect(longPn).toBeInTheDocument();
  // Many rows render without corrupting the table.
  expect(screen.getByText('PF-LONGRUN-024')).toBeInTheDocument();
});
