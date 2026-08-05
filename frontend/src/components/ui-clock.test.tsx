import { act, render, screen } from '@testing-library/react';
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
  vi.useRealTimers();
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
