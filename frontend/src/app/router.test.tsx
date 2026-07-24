import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../App';

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

test('the application shell renders with the four top-level entries', async () => {
  renderAt('/scan-station');

  const nav = screen.getByRole('navigation', { name: 'Primary' });
  expect(nav).toBeInTheDocument();
  for (const label of [
    'Scan Station',
    'Production Board',
    'Management',
    'Administration',
  ]) {
    expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
  }
  expect(await screen.findByText('ONLINE')).toBeInTheDocument();
});

test('the root URL redirects to the Scan Station', () => {
  renderAt('/');

  expect(window.location.pathname).toBe('/scan-station');
  expect(
    screen.getByRole('region', { name: 'Scan Station' }),
  ).toBeInTheDocument();
});

test('top-level navigation switches views and updates the URL', () => {
  renderAt('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Production Board' }));

  expect(window.location.pathname).toBe('/production-board');
  expect(
    screen.getByRole('heading', { name: 'Machine Shop — Production' }),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole('link', { name: 'Administration' }));

  expect(window.location.pathname).toBe('/administration');
  expect(screen.getByRole('heading', { name: 'Areas' })).toBeInTheDocument();
});

test('Management opens Area Board first and exposes the sub navigation', () => {
  renderAt('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Management' }));

  expect(window.location.pathname).toBe('/management/area-board');
  expect(
    screen.getByRole('navigation', { name: 'Management sub views' }),
  ).toBeInTheDocument();
  // Area Board initially opens its All Areas overview.
  expect(screen.getByRole('button', { name: /All Areas/ })).toHaveAttribute(
    'aria-pressed',
    'true',
  );

  fireEvent.click(screen.getByRole('link', { name: 'Tracking' }));

  expect(window.location.pathname).toBe('/management/tracking');
  expect(screen.getByRole('heading', { name: 'Tracking' })).toBeInTheDocument();
});

test('returning to Management restores the last-used sub view during the session', () => {
  renderAt('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Management' }));
  fireEvent.click(screen.getByRole('link', { name: 'Purchase Orders' }));
  expect(window.location.pathname).toBe('/management/purchase-orders');

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));
  expect(window.location.pathname).toBe('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Management' }));
  expect(window.location.pathname).toBe('/management/purchase-orders');
  expect(
    screen.getByRole('heading', { name: 'Purchase Orders' }),
  ).toBeInTheDocument();
});

test('a Management sub view renders directly from its URL', () => {
  renderAt('/management/priority');

  expect(
    screen.getByRole('heading', {
      name: 'Priority Management — Hot PO Demand',
    }),
  ).toBeInTheDocument();
});

test('browser back and forward navigation works', async () => {
  renderAt('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Production Board' }));
  expect(window.location.pathname).toBe('/production-board');

  window.history.back();
  await waitFor(() =>
    expect(
      screen.getByRole('region', { name: 'Scan Station' }),
    ).toBeInTheDocument(),
  );
  expect(window.location.pathname).toBe('/scan-station');

  window.history.forward();
  await waitFor(() =>
    expect(
      screen.getByRole('heading', { name: 'Machine Shop — Production' }),
    ).toBeInTheDocument(),
  );
  expect(window.location.pathname).toBe('/production-board');
});

test('unknown routes render the application-level not-found state', () => {
  renderAt('/no-such-view');

  expect(
    screen.getByRole('heading', { name: 'Page not found' }),
  ).toBeInTheDocument();
  expect(screen.getByText('/no-such-view')).toBeInTheDocument();
  expect(
    screen.getByRole('link', { name: 'Go to Scan Station' }),
  ).toBeInTheDocument();
});
