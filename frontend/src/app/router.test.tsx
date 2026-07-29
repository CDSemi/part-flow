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

test('the root URL redirects to the Station Selector — never to a station', async () => {
  renderAt('/');

  expect(window.location.pathname).toBe('/scan-station');
  expect(
    await screen.findByRole('region', { name: 'Scan Station' }),
  ).toBeInTheDocument();
  // The selector lists the active stations; no station is auto-loaded.
  expect(
    screen.getByRole('heading', { name: 'Select a Scan Station' }),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText('Scan barcode')).not.toBeInTheDocument();
});

test('/scan-station/:stationId loads the selected station', async () => {
  renderAt('/scan-station/LATHE-ST-01');

  expect(await screen.findByLabelText('Scan barcode')).toBeInTheDocument();
  expect(screen.getByText('LATHE-ST-01')).toBeInTheDocument();
});

test('selecting a station from the Station Selector navigates to its URL', async () => {
  renderAt('/scan-station');

  fireEvent.click(await screen.findByRole('button', { name: /DEBURR-ST-01/ }));

  expect(window.location.pathname).toBe('/scan-station/DEBURR-ST-01');
  expect(await screen.findByLabelText('Scan barcode')).toBeInTheDocument();
});

test('an unknown Station ID shows an explicit error — no silent fallback', async () => {
  renderAt('/scan-station/NO-SUCH-STATION');

  expect(
    await screen.findByText(/Unknown or inactive Scan Station/),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText('Scan barcode')).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Choose a Scan Station' }),
  ).toBeInTheDocument();
});

test('top-level navigation switches views and updates the URL', async () => {
  renderAt('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Production Board' }));

  expect(window.location.pathname).toBe('/production-board');
  expect(
    await screen.findByRole('heading', { name: 'Machine Shop — Production' }),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole('link', { name: 'Administration' }));

  expect(window.location.pathname).toBe('/administration');
  expect(
    await screen.findByRole('heading', { name: 'Areas' }),
  ).toBeInTheDocument();
});

test('Management opens Area Board first and exposes the sub navigation', async () => {
  renderAt('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Management' }));

  expect(window.location.pathname).toBe('/management/area-board');
  expect(
    screen.getByRole('navigation', { name: 'Management sub views' }),
  ).toBeInTheDocument();
  // Area Board initially opens its All Areas overview.
  expect(
    await screen.findByRole('button', { name: /All Areas/ }),
  ).toHaveAttribute('aria-pressed', 'true');

  fireEvent.click(screen.getByRole('link', { name: 'Tracking' }));

  expect(window.location.pathname).toBe('/management/tracking');
  expect(
    await screen.findByRole('heading', { name: 'Tracking' }),
  ).toBeInTheDocument();
});

test('returning to Management restores the last-used sub view during the session', async () => {
  renderAt('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Management' }));
  fireEvent.click(screen.getByRole('link', { name: 'Work Orders' }));
  expect(window.location.pathname).toBe('/management/work-orders');

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));
  expect(window.location.pathname).toBe('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Management' }));
  expect(window.location.pathname).toBe('/management/work-orders');
  expect(
    await screen.findByRole('heading', { name: 'Work Orders' }),
  ).toBeInTheDocument();
});

test('a Management sub view renders directly from its URL', async () => {
  renderAt('/management/priority');

  expect(
    await screen.findByRole('heading', {
      name: 'Priority Management — Hot WO Demand',
    }),
  ).toBeInTheDocument();
});

test('browser back and forward navigation works', async () => {
  renderAt('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Production Board' }));
  expect(window.location.pathname).toBe('/production-board');
  await screen.findByRole('heading', { name: 'Machine Shop — Production' });

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
