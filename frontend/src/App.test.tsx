import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { App } from './App';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function stubFetch(implementation: () => Promise<Response>) {
  vi.stubGlobal('fetch', vi.fn(implementation));
}

test('shows the loading state while the health request is pending', () => {
  // A fetch that never settles keeps the component in its initial state.
  stubFetch(() => new Promise<Response>(() => {}));

  render(<App />);

  expect(screen.getByRole('heading', { name: 'PartFlow' })).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent(
    'Checking backend connection…',
  );
});

test('shows the connected state when the health endpoint succeeds', async () => {
  stubFetch(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          status: 'ok',
          service: 'partflow-api',
          database: 'connected',
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    ),
  );

  render(<App />);

  expect(await screen.findByText('Backend connected.')).toBeInTheDocument();
});

test('shows the unavailable state when the health request fails', async () => {
  stubFetch(() => Promise.reject(new TypeError('Failed to fetch')));

  render(<App />);

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Backend unavailable.',
  );
});

test('shows the unavailable state when the health request times out', async () => {
  // Regression: the health request is bounded by AbortSignal.timeout,
  // which rejects fetch with a TimeoutError DOMException. The screen
  // must fall back to unavailable instead of loading forever.
  const fetchMock = vi.fn(() =>
    Promise.reject(
      new DOMException('The operation timed out.', 'TimeoutError'),
    ),
  );
  vi.stubGlobal('fetch', fetchMock);

  render(<App />);

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Backend unavailable.',
  );
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/health',
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

test('shows the unavailable state when the backend returns a non-success response', async () => {
  stubFetch(() =>
    Promise.resolve(
      new Response(JSON.stringify({ status: 'unavailable' }), {
        status: 503,
      }),
    ),
  );

  render(<App />);

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Backend unavailable.',
  );
});
