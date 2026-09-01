import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from '../App';

/**
 * Minimal real environment for the routing tests: two active Scan
 * Stations bound to two Areas, no production quantity. The Scan Station
 * is a real view since Phase 5, so its routes load this context from
 * the stubbed `/api` instead of a mock registry.
 */
const STATIONS = [
  { station_id: 'LATHE-ST-01', area_id: 1 },
  { station_id: 'DEBURR-ST-01', area_id: 2 },
];
const AREAS = [
  { id: 1, name: 'Lathe', color: '#3366ff' },
  { id: 2, name: 'Deburr', color: '#33aa66' },
];

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

beforeEach(() => {
  // Health answers ok; the real views additionally load their
  // configuration lists — a near-empty environment keeps these routing
  // tests focused on navigation.
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/barcode-configuration/')) {
        return json({ detail: 'Not configured.' }, 404);
      }
      const context = /\/api\/scan-stations\/([^/]+)\/context$/.exec(url);
      if (context) {
        const station = STATIONS.find(
          (s) => s.station_id === decodeURIComponent(context[1]),
        );
        if (!station) {
          return json(
            { detail: `Scan Station '${context[1]}' does not exist.` },
            404,
          );
        }
        const area = AREAS.find((a) => a.id === station.area_id)!;
        return json({
          station_id: station.station_id,
          department: { id: 1, name: 'Machining' },
          area: { ...area, description: null, is_terminal: false },
          operations: [],
          has_machines: false,
        });
      }
      if (/\/api\/areas\/\d+\/inventory$/.test(url)) {
        return json({
          area: { ...AREAS[0], description: null, is_terminal: false },
          has_machines: false,
          lines: [],
          total_part_numbers: 0,
          total_quantity: 0,
          queued: [],
          queued_quantity: 0,
          machines: [],
          on_machine_quantity: 0,
          processing: [],
          processing_quantity: 0,
          finished: [],
          finished_quantity: 0,
        });
      }
      if (/\/api\/scan-stations$/.test(url)) {
        return json(STATIONS.map((s) => ({ ...s, is_active: true })));
      }
      if (/\/api\/areas$/.test(url)) {
        return json(
          AREAS.map((a) => ({
            ...a,
            department_id: 1,
            barcode_value: `PF:AREA:${a.id}`,
            description: null,
            icon_url: null,
            is_terminal: false,
            is_active: true,
          })),
        );
      }
      if (/\/api\/departments$/.test(url)) {
        return json([{ id: 1, name: 'Machining', is_active: true }]);
      }
      if (/\/api\/work-orders\/completed\?/.test(url)) {
        return json({
          work_orders: [],
          total: 0,
          next_cursor_completed_at: null,
          next_cursor_id: null,
        });
      }
      if (
        /\/api\/(machines|operations|work-orders|part-numbers|route-templates)/.test(
          url,
        )
      ) {
        return json([]);
      }
      return json({ status: 'ok' });
    }),
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
    await screen.findByRole('heading', { name: /Production/ }),
  ).toBeInTheDocument();
  // Kiosk mode: no top application navigation, board-owned header
  // with one shared connectivity status instead.
  expect(screen.queryByRole('navigation', { name: 'Primary' })).toBeNull();
  expect(document.querySelector('.pb-head.pbk-head')).not.toBeNull();

  // The board arrived through a lazy chunk: its DOM is committed once
  // the heading is found, but React flushes the passive effects of that
  // (non-act) render — including the Ctrl+Shift+K keydown listener —
  // in a later task. Flush them deterministically before the shortcut
  // is fired, so the test never races the listener registration.
  await act(async () => {});

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
    await screen.findByRole('heading', { name: /Production/ }),
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

test('Management exposes the seven sub views in the approved order', async () => {
  renderAt('/management/area-board');

  // Part Numbers sits next to last; Machines last, directly after it
  // (GUI_DESIGN §1.1).
  const nav = await screen.findByRole('navigation', {
    name: 'Management sub views',
  });
  expect(Array.from(nav.querySelectorAll('a'), (a) => a.textContent)).toEqual([
    'Area Board',
    'Work Orders',
    'PN Tracking',
    'Priority',
    'Planned Routes',
    'Part Numbers',
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

test('the Part Numbers management view renders from its URL', async () => {
  renderAt('/management/part-numbers');

  expect(
    await screen.findByRole('region', { name: 'Part Numbers' }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole('heading', { name: 'Part Numbers' }),
  ).toBeInTheDocument();
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
  await screen.findByRole('heading', { name: /Production/ });

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
      screen.getByRole('heading', { name: /Production/ }),
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
