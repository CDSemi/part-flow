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

// `useSyncExternalStore` re-subscribes whenever the subscribe function's
// identity changes, so the subscribe and snapshot functions below are
// created ONCE per precision at module scope and never inside the hook.
// An inline `(listener) => subscribeTo(channel, listener)` closure would
// be a new reference on every render: React would then unsubscribe and
// re-subscribe each render, each re-subscribe of a momentarily empty
// channel refreshes `now`, the changed snapshot schedules another
// render, and under StrictMode this feedback loop crashes the view
// with "Maximum update depth exceeded" (observed on the Production
// Board and Area Board — every useUiClock consumer was affected).
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

// Stable per-precision function references (see the note above
// subscribeTo). Both records are keyed by precision so the hook body
// allocates nothing and passes identical references on every render.
const subscribers: Record<
  ClockPrecision,
  (listener: () => void) => () => void
> = {
  minute: (listener) => subscribeTo(channels.minute, listener),
  second: (listener) => subscribeTo(channels.second, listener),
};

const snapshots: Record<ClockPrecision, () => number> = {
  minute: () => channels.minute.now,
  second: () => channels.second.now,
};

/**
 * Current time (epoch ms) at the requested precision. All subscribers
 * of one precision share one interval and one snapshot — components
 * re-render together and derive identical values from it.
 */
export function useUiClock(precision: ClockPrecision = 'minute'): number {
  return useSyncExternalStore(subscribers[precision], snapshots[precision]);
}
