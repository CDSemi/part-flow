import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { useConnectivity } from './connectivity-context';
import { ConnectivityProvider } from './connectivity-provider';

// Fast connectivity-loss detection (browser online/offline events +
// ~1 s health polling). These tests drive the provider directly with
// fake timers and mocked browser connectivity events — no lazy views.

function Probe() {
  const { status, retry } = useConnectivity();
  return (
    <>
      <span data-testid="status">{status}</span>
      <button onClick={retry}>retry</button>
    </>
  );
}

function statusText() {
  return screen.getByTestId('status').textContent;
}

function healthOk() {
  return Promise.resolve(
    new Response(JSON.stringify({ status: 'ok' }), { status: 200 }),
  );
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function renderProvider() {
  render(
    <ConnectivityProvider>
      <Probe />
    </ConnectivityProvider>,
  );
  // Flush the initial health check (resolves in microtasks).
  await act(async () => {});
}

test('a browser offline event marks the application unavailable immediately', async () => {
  vi.stubGlobal('fetch', vi.fn(healthOk));
  await renderProvider();
  expect(statusText()).toBe('connected');

  act(() => {
    window.dispatchEvent(new Event('offline'));
  });

  // Immediate — no probe or timer needed.
  expect(statusText()).toBe('unavailable');
});

test('a browser online event triggers an immediate health check', async () => {
  const fetchMock = vi.fn(healthOk);
  vi.stubGlobal('fetch', fetchMock);
  await renderProvider();
  act(() => {
    window.dispatchEvent(new Event('offline'));
  });
  expect(statusText()).toBe('unavailable');
  const callsBefore = fetchMock.mock.calls.length;

  await act(async () => {
    window.dispatchEvent(new Event('online'));
  });

  expect(fetchMock.mock.calls.length).toBe(callsBefore + 1);
  expect(statusText()).toBe('connected');
});

test('backend loss surfaces in approximately one second of polling', async () => {
  let failing = false;
  const fetchMock = vi.fn(() =>
    failing ? Promise.reject(new TypeError('Failed to fetch')) : healthOk(),
  );
  vi.stubGlobal('fetch', fetchMock);
  await renderProvider();
  expect(statusText()).toBe('connected');

  failing = true;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });

  expect(statusText()).toBe('unavailable');
});

test('a hung health request is timed out and reported unavailable', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(
              init.signal?.reason ?? new DOMException('aborted', 'AbortError'),
            ),
          );
        }),
    ),
  );
  render(
    <ConnectivityProvider>
      <Probe />
    </ConnectivityProvider>,
  );
  expect(statusText()).toBe('connecting');

  // The request timeout (900 ms) is below the 1 s probe interval.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(900);
  });

  expect(statusText()).toBe('unavailable');
});

test('health probes never overlap', async () => {
  let firstCall = true;
  const fetchMock = vi.fn((): Promise<Response> => {
    if (firstCall) {
      firstCall = false;
      return healthOk();
    }
    // Later probes hang (and ignore the abort signal): the in-flight
    // guard must skip every following interval tick.
    return new Promise<Response>(() => {});
  });
  vi.stubGlobal('fetch', fetchMock);
  await renderProvider();
  expect(statusText()).toBe('connected');

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(fetchMock.mock.calls.length).toBe(2);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(fetchMock.mock.calls.length).toBe(2); // still just the hung probe
});

test('passive probes never flip the UI back to connecting (no flicker)', async () => {
  let resolved = false;
  const fetchMock = vi.fn((): Promise<Response> => {
    if (!resolved) {
      resolved = true;
      return healthOk();
    }
    return new Promise<Response>(() => {});
  });
  vi.stubGlobal('fetch', fetchMock);
  await renderProvider();
  expect(statusText()).toBe('connected');

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });

  // A pending passive probe leaves the status alone.
  expect(statusText()).toBe('connected');
});

test('regaining focus or visibility triggers an immediate re-check', async () => {
  let failing = false;
  const fetchMock = vi.fn(() =>
    failing ? Promise.reject(new TypeError('Failed to fetch')) : healthOk(),
  );
  vi.stubGlobal('fetch', fetchMock);
  await renderProvider();
  expect(statusText()).toBe('connected');

  failing = true;
  await act(async () => {
    window.dispatchEvent(new Event('focus'));
  });
  expect(statusText()).toBe('unavailable');

  failing = false;
  await act(async () => {
    document.dispatchEvent(new Event('visibilitychange'));
  });
  expect(statusText()).toBe('connected');
});

test('unmount cleans up timers, listeners and the in-flight request', async () => {
  const fetchMock = vi.fn(healthOk);
  vi.stubGlobal('fetch', fetchMock);
  const view = render(
    <ConnectivityProvider>
      <Probe />
    </ConnectivityProvider>,
  );
  await act(async () => {});
  const callsBefore = fetchMock.mock.calls.length;

  view.unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10_000);
  });
  window.dispatchEvent(new Event('online'));
  window.dispatchEvent(new Event('focus'));

  expect(fetchMock.mock.calls.length).toBe(callsBefore);
});
