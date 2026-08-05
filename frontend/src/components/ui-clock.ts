// Shared UI clock — the single time source for every derived
// time-dependent display value (GUI_DESIGN §4.10, v16).
//
// Original timestamps (Movement times, state changes, due dates) are
// fixed data; only the DERIVED presentation values (elapsed durations,
// day countdowns, live clocks) change with wall-clock time. Every view
// derives those values from the one shared tick published here, so:
// - the same timestamp yields the same displayed value in every view
//   at the same moment (no per-component timers that drift apart);
// - minute-precision values re-render once per minute, second-precision
//   clocks once per second — never faster than their precision needs;
// - after a data change (movement, state change) the affected component
//   re-renders immediately and re-derives from the current shared tick,
//   without waiting for the next interval (formatters clamp a timestamp
//   newer than the tick to the smallest unit instead of going negative).
//
// Production-safe: framework glue only — no mock data.

import { useSyncExternalStore } from 'react';

export type ClockPrecision = 'minute' | 'second';

interface Channel {
  intervalMs: number;
  now: number;
  timer: number | null;
  listeners: Set<() => void>;
}

const channels: Record<ClockPrecision, Channel> = {
  minute: {
    intervalMs: 60_000,
    now: Date.now(),
    timer: null,
    listeners: new Set(),
  },
  second: {
    intervalMs: 1_000,
    now: Date.now(),
    timer: null,
    listeners: new Set(),
  },
};

function subscribeTo(channel: Channel, listener: () => void): () => void {
  if (channel.listeners.size === 0) {
    // First subscriber (re)starts the interval from a fresh reading —
    // a channel that was idle never serves a stale first snapshot.
    channel.now = Date.now();
    channel.timer = window.setInterval(() => {
      channel.now = Date.now();
      for (const notify of channel.listeners) notify();
    }, channel.intervalMs);
  }
  channel.listeners.add(listener);
  return () => {
    channel.listeners.delete(listener);
    if (channel.listeners.size === 0 && channel.timer !== null) {
      window.clearInterval(channel.timer);
      channel.timer = null;
    }
  };
}

/**
 * Current time (epoch ms) at the requested precision. All subscribers
 * of one precision share one interval and one snapshot — components
 * re-render together and derive identical values from it.
 */
export function useUiClock(precision: ClockPrecision = 'minute'): number {
  const channel = channels[precision];
  return useSyncExternalStore(
    (listener) => subscribeTo(channel, listener),
    () => channel.now,
  );
}
