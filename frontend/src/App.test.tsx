import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { App } from './App';

beforeEach(() => {
  // A concrete station URL: /scan-station itself is the Station
  // Selector and has no scan input.
  window.history.replaceState({}, '', '/scan-station/LATHE-ST-01');
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function stubFetch(implementation: () => Promise<Response>) {
  const fetchMock = vi.fn(implementation);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function healthOk() {
  return Promise.resolve(
    new Response(
      JSON.stringify({
        status: 'ok',
        service: 'partflow-api',
        database: 'connected',
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ),
  );
}

test('shows the connecting state while the health request is pending', async () => {
  // A fetch that never settles keeps the shell in its connecting state.
  stubFetch(() => new Promise<Response>(() => {}));

  render(<App />);

  expect(screen.getByText('CONNECTING…')).toBeInTheDocument();
  // Production-write controls are not enabled before the backend confirms.
  // (Views load lazily through the development-only mock boundary.)
  expect(await screen.findByLabelText('Scan barcode')).toBeDisabled();
});

test('shows ONLINE when the health endpoint succeeds', async () => {
  stubFetch(healthOk);

  render(<App />);

  expect(await screen.findByText('ONLINE')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(await screen.findByLabelText('Scan barcode')).toBeEnabled();
});

test('shows OFFLINE with the persistent banner when the health request fails', async () => {
  const fetchMock = stubFetch(() =>
    Promise.reject(new TypeError('Failed to fetch')),
  );

  render(<App />);

  expect(await screen.findByText('OFFLINE')).toBeInTheDocument();
  const banner = screen.getByRole('alert');
  // The one compact statement — exact copy, no extra explanatory
  // sentences about scan queues, read-only data or recovery.
  expect(banner.querySelector('.msg')?.textContent?.trim()).toBe(
    '⚠ OFFLINE — Connection to the PartFlow server has been lost. Production actions are disabled',
  );
  expect(banner.textContent).not.toContain('scans will not be recorded');
  expect(banner.textContent).not.toContain('read-only information');
  expect(banner.textContent).not.toContain('until the connection is restored');
  // Two explicit regions, no separator element and no `|`: the
  // message region fills the remaining space; the WHOLE right rail is
  // the Retry button itself (divided by its own border-left), flush
  // with the banner's right edge — the shared action-zone pattern
  // (same as the Scan Station Undo region).
  expect(banner.querySelector('.sep')).toBeNull();
  expect(banner.textContent).not.toContain('|');
  const retryButton = screen.getByRole('button', {
    name: 'Retry connection',
  });
  expect(retryButton).toHaveClass('zone-action');
  expect(banner.lastElementChild).toBe(retryButton);
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/health',
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

test('shows OFFLINE when the health request times out', async () => {
  // Regression: the health request is bounded by AbortSignal.timeout,
  // which rejects fetch with a TimeoutError DOMException. The shell must
  // fall back to unavailable instead of loading forever.
  stubFetch(() =>
    Promise.reject(
      new DOMException('The operation timed out.', 'TimeoutError'),
    ),
  );

  render(<App />);

  expect(await screen.findByText('OFFLINE')).toBeInTheDocument();
});

test('shows OFFLINE when the backend returns a non-success response', async () => {
  stubFetch(() =>
    Promise.resolve(
      new Response(JSON.stringify({ status: 'unavailable' }), { status: 503 }),
    ),
  );

  render(<App />);

  expect(await screen.findByText('OFFLINE')).toBeInTheDocument();
});

test('disables production-write controls while disconnected but keeps read-only mock data visible', async () => {
  stubFetch(() => Promise.reject(new TypeError('Failed to fetch')));

  render(<App />);
  await screen.findByText('OFFLINE');

  // Write-oriented controls are disabled.
  expect(await screen.findByLabelText('Scan barcode')).toBeDisabled();
  expect(
    screen.getByRole('button', { name: '⌨ Enter PN manually' }),
  ).toBeDisabled();
  expect(screen.getByRole('button', { name: '⟲ UNDO' })).toBeDisabled();

  // Already displayed read-only mock information stays visible.
  expect(screen.getByText('In this Area now')).toBeInTheDocument();
});

test('retry re-runs the health check and recovers to ONLINE', async () => {
  let failing = true;
  stubFetch(() =>
    failing ? Promise.reject(new TypeError('Failed to fetch')) : healthOk(),
  );

  render(<App />);
  await screen.findByText('OFFLINE');

  failing = false;
  fireEvent.click(screen.getByRole('button', { name: 'Retry connection' }));

  expect(await screen.findByText('ONLINE')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(await screen.findByLabelText('Scan barcode')).toBeEnabled();
});

/* ============ Offline banner presentation contract ============ */

test('the Retry action rail is a full-height region at the banner edge', () => {
  // Presentation contract on the stylesheet itself (jsdom applies no
  // CSS): the banner is two regions — the message fills the remaining
  // space and may wrap its text; the Retry rail is the shared
  // .zone-action pattern (surface and hover/focus-visible/active from
  // styles/global.css — the same treatment as the Scan Station Undo
  // region), stretched to the full banner height at the right edge,
  // divided by its own stronger border-left, with the text centered
  // vertically. The banner carries no own padding, so no leftover
  // background sits right of the rail.
  const css = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), 'app', 'shell.css'),
    'utf8',
  );
  const retry = /\.offbanner \.retry \{[^}]*}/s.exec(css)![0];
  expect(retry).toContain('align-self: stretch');
  expect(retry).toContain('border-left: 2px solid');
  expect(retry).toContain('align-items: center');
  expect(retry).not.toContain('min-height: 40px');
  expect(css).not.toMatch(/\.offbanner \.sep/);
  const banner = /\.offbanner \{[^}]*}/s.exec(css)![0];
  expect(banner).toContain('padding: 0');
  const msg = /\.offbanner \.msg \{[^}]*}/s.exec(css)![0];
  expect(msg).toContain('flex: 1 1 auto');
  expect(msg).toContain('min-width: 0');
});
