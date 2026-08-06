import { StrictMode } from 'react';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { formatElapsedSince } from '../views/dates';
import { useUiClock } from './ui-clock';

// The shared UI clock is the single time source for derived time
// displays: minute-precision consumers re-render once per minute from
// ONE shared snapshot (no per-component timers), second-precision
// consumers once per second, and a data change re-derives immediately
// from the current tick without waiting for the next interval.

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  // Vitest runs without injected globals, so testing-library's automatic
  // cleanup is not registered — without this, containers leak between
  // tests and screen queries find stale duplicates.
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function MinuteProbe({ id, since }: { id: string; since: string }) {
  const now = useUiClock('minute');
  return (
    <span data-testid={id}>
      {formatElapsedSince(since, now)}·{now}
    </span>
  );
}

function SecondProbe() {
  const now = useUiClock('second');
  return <span data-testid="sec">{now}</span>;
}

test('minute consumers share one snapshot and tick once per minute', async () => {
  const since = new Date(Date.now() - 5 * 60_000).toISOString();
  render(
    <>
      <MinuteProbe id="a" since={since} />
      <MinuteProbe id="b" since={since} />
    </>,
  );

  // Both subscribers read the SAME shared snapshot — identical output.
  expect(screen.getByTestId('a').textContent).toBe(
    screen.getByTestId('b').textContent,
  );
  expect(screen.getByTestId('a').textContent).toContain('5m');

  // Below the minute interval nothing re-renders…
  await act(async () => {
    await vi.advanceTimersByTimeAsync(59_000);
  });
  expect(screen.getByTestId('a').textContent).toContain('5m');

  // …at the tick both consumers advance together to the same value.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1_000);
  });
  expect(screen.getByTestId('a').textContent).toContain('6m');
  expect(screen.getByTestId('a').textContent).toBe(
    screen.getByTestId('b').textContent,
  );
});

test('second-precision consumers tick once per second', async () => {
  render(<SecondProbe />);
  const first = screen.getByTestId('sec').textContent;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1_000);
  });
  expect(screen.getByTestId('sec').textContent).not.toBe(first);
});

test('a data change re-derives immediately — clamped, never negative', async () => {
  // A timestamp NEWER than the last minute tick (a movement recorded
  // just now) must render <1m at once — no wait for the next interval.
  const { rerender } = render(
    <MinuteProbe id="a" since={new Date(Date.now() - 60_000).toISOString()} />,
  );
  expect(screen.getByTestId('a').textContent).toMatch(/^1m·/);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
  rerender(
    <MinuteProbe id="a" since={new Date(Date.now() + 5_000).toISOString()} />,
  );
  expect(screen.getByTestId('a').textContent).toMatch(/^<1m·/);
});

test('the interval stops when the last subscriber unmounts', () => {
  const { unmount } = render(<SecondProbe />);
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});

// --- Regression tests with REAL timers -------------------------------
//
// The fake-timer tests above cannot detect the failure mode that
// blanked the Production Board and Area Board: an inline subscribe
// callback made useSyncExternalStore re-subscribe on every render,
// each re-subscribe refreshed the snapshot, and the changed snapshot
// scheduled the next render — an infinite loop that React aborts with
// "Maximum update depth exceeded", unmounting the whole tree. The
// tests below run with real timers and real (double-invoking)
// StrictMode effects, and fail against that implementation.

const realDelay = (ms: number) => new Promise((r) => setTimeout(r, ms));

test('StrictMode render churn never re-subscribes: stable references, no render loop, no timer leak (real timers)', async () => {
  vi.useRealTimers();
  const setIntervalSpy = vi.spyOn(window, 'setInterval');
  const clearIntervalSpy = vi.spyOn(window, 'clearInterval');
  const since = new Date(Date.now() - 5 * 60_000).toISOString();

  // Many consumers on both precisions, under StrictMode like main.tsx.
  // With the broken implementation this render never settles — React
  // throws "Maximum update depth exceeded" and the assertions fail.
  const ui = (tick: number) => (
    <StrictMode>
      <MinuteProbe id={`m1-${tick}`} since={since} />
      <MinuteProbe id={`m2-${tick}`} since={since} />
      <SecondProbe />
    </StrictMode>
  );
  const { rerender, unmount } = render(ui(0));
  await act(() => realDelay(150));
  const intervalsAfterMount = setIntervalSpy.mock.calls.length;

  // Re-render repeatedly with changed props: subscribe/getSnapshot must
  // keep their identity, so NOT ONE additional subscription (and hence
  // interval start) may happen — churn here is the render-loop seed.
  for (let tick = 1; tick <= 5; tick++) {
    rerender(ui(tick));
  }
  await act(() => realDelay(150));
  expect(setIntervalSpy.mock.calls.length).toBe(intervalsAfterMount);

  // Consumers stayed alive (the loop would have blanked them) and both
  // minute consumers still share one snapshot.
  expect(screen.getByTestId('m1-5').textContent).toBe(
    screen.getByTestId('m2-5').textContent,
  );

  // No timer leak: after the last subscriber unmounts, every interval
  // that was ever started has been cleared again.
  unmount();
  expect(clearIntervalSpy.mock.calls.length).toBe(
    setIntervalSpy.mock.calls.length,
  );
});

test('the second-precision clock advances under real timers', async () => {
  vi.useRealTimers();
  render(
    <StrictMode>
      <SecondProbe />
    </StrictMode>,
  );
  const first = screen.getByTestId('sec').textContent;
  await act(() => realDelay(1_200));
  expect(screen.getByTestId('sec').textContent).not.toBe(first);
});
