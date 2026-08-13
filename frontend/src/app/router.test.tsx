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

  fireEvent.click(
    await screen.findByRole('button', { name: 'Open DEBURR-ST-01' }),
  );

  expect(window.location.pathname).toBe('/scan-station/DEBURR-ST-01');
  expect(await screen.findByLabelText('Scan barcode')).toBeInTheDocument();
});

test('/scan-station/:stationId resolves to the standard mode with top navigation', async () => {
  renderAt('/scan-station/LATHE-ST-01');

  expect(await screen.findByLabelText('Scan barcode')).toBeInTheDocument();
  expect(
    screen.getByRole('navigation', { name: 'Primary' }),
  ).toBeInTheDocument();
  expect(screen.queryByText('Production mode')).toBeNull();
});

test('the production route direct-loads with the navigation hidden', async () => {
  renderAt('/scan-station/LATHE-ST-01/production');

  expect(await screen.findByLabelText('Scan barcode')).toBeInTheDocument();
  expect(screen.queryByRole('navigation', { name: 'Primary' })).toBeNull();
  expect(screen.getByText('Production mode')).toBeInTheDocument();
});

test('browser back/forward moves between standard and production modes', async () => {
  renderAt('/scan-station/LATHE-ST-01');
  await screen.findByLabelText('Scan barcode');

  fireEvent.keyDown(window, { key: 'K', ctrlKey: true, shiftKey: true });
  await screen.findByText('Production mode');
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01/production');

  window.history.back();
  await waitFor(() =>
    expect(
      screen.getByRole('navigation', { name: 'Primary' }),
    ).toBeInTheDocument(),
  );
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01');

  window.history.forward();
  await waitFor(() =>
    expect(screen.getByText('Production mode')).toBeInTheDocument(),
  );
  expect(window.location.pathname).toBe('/scan-station/LATHE-ST-01/production');
});

test('an unknown Station ID shows an explicit error — no silent fallback', async () => {
  renderAt('/scan-station/NO-SUCH-STATION');

  expect(
    await screen.findByText(/Scan Station “.*” is unavailable/),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText('Scan barcode')).not.toBeInTheDocument();
  expect(
    screen.getByRole('button', { name: 'Select another Scan Station' }),
  ).toBeInTheDocument();
});

test('/production-board/kiosk hides the navigation; the standard route keeps it', async () => {
  renderAt('/production-board/kiosk');

  expect(
    await screen.findByRole('heading', { name: 'Live Production' }),
  ).toBeInTheDocument();
  // Kiosk mode: no top application navigation, board-owned header
  // with one shared connectivity status instead.
  expect(screen.queryByRole('navigation', { name: 'Primary' })).toBeNull();
  expect(document.querySelector('.pb-head.pbk-head')).not.toBeNull();

  // Ctrl+Shift+K returns to the standard route with the normal
  // application navigation.
  fireEvent.keyDown(window, { key: 'K', ctrlKey: true, shiftKey: true });
  await waitFor(() =>
    expect(
      screen.getByRole('navigation', { name: 'Primary' }),
    ).toBeInTheDocument(),
  );
  expect(window.location.pathname).toBe('/production-board');
  expect(document.querySelector('.pb-head.pbk-head')).toBeNull();
});

test('top-level navigation switches views and updates the URL', async () => {
  renderAt('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Production Board' }));

  expect(window.location.pathname).toBe('/production-board');
  expect(
    await screen.findByRole('heading', { name: 'Live Production' }),
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

  fireEvent.click(screen.getByRole('link', { name: 'PN Tracking' }));

  expect(window.location.pathname).toBe('/management/tracking');
  expect(
    await screen.findByRole('heading', { name: 'PN Tracking' }),
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

test('Management exposes the six sub views in the approved order', async () => {
  renderAt('/management/area-board');

  const nav = await screen.findByRole('navigation', {
    name: 'Management sub views',
  });
  expect(Array.from(nav.querySelectorAll('a'), (a) => a.textContent)).toEqual([
    'Area Board',
    'Work Orders',
    'PN Tracking',
    'Priority',
    'Planned Routes',
    'Machines',
  ]);
});

test('the Completed Work Orders page renders from its URL with Work Orders active', async () => {
  renderAt('/management/work-orders/completed');

  expect(
    await screen.findByRole('heading', { name: 'Completed Work Orders' }),
  ).toBeInTheDocument();
  // The sub-view bar keeps Work Orders active — the page belongs to
  // the Work Orders sub view (GUI_DESIGN §11.5).
  const workOrdersLink = screen.getByRole('link', { name: 'Work Orders' });
  expect(workOrdersLink.className).toContain('active');
  // The back action returns to the active WO list.
  fireEvent.click(screen.getByRole('link', { name: '‹ Work Orders' }));
  expect(window.location.pathname).toBe('/management/work-orders');
  expect(
    await screen.findByRole('heading', { name: 'Work Orders' }),
  ).toBeInTheDocument();
});

test('returning to Management from the completed page re-enters the active WO list', async () => {
  renderAt('/management/work-orders/completed');
  await screen.findByRole('heading', { name: 'Completed Work Orders' });

  fireEvent.click(screen.getByRole('link', { name: 'Scan Station' }));
  expect(window.location.pathname).toBe('/scan-station');

  // Last-used-sub-view restoration stores only the sub view — it
  // never lands deep inside the history page.
  fireEvent.click(screen.getByRole('link', { name: 'Management' }));
  expect(window.location.pathname).toBe('/management/work-orders');
  expect(
    await screen.findByRole('heading', { name: 'Work Orders' }),
  ).toBeInTheDocument();
});

test('the Machines management view renders from its URL', async () => {
  renderAt('/management/machines');

  expect(
    await screen.findByRole('region', { name: 'Machines' }),
  ).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Machines' })).toBeInTheDocument();
});

test('the Planned Routes management view renders from its URL', async () => {
  renderAt('/management/planned-routes');

  expect(
    await screen.findByRole('region', { name: 'Planned Routes' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('heading', { name: 'Planned Routes' }),
  ).toBeInTheDocument();
});

test('browser back and forward navigation works', async () => {
  renderAt('/scan-station');

  fireEvent.click(screen.getByRole('link', { name: 'Production Board' }));
  expect(window.location.pathname).toBe('/production-board');
  await screen.findByRole('heading', { name: 'Live Production' });

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
      screen.getByRole('heading', { name: 'Live Production' }),
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
