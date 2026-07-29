import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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
  expect(banner).toHaveTextContent('OFFLINE — the backend is unavailable');
  expect(banner).toHaveTextContent('nothing is recorded or queued');
  expect(
    screen.getByRole('button', { name: 'Retry connection' }),
  ).toBeInTheDocument();
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
  expect(screen.getByRole('button', { name: 'ENTER' })).toBeDisabled();
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
