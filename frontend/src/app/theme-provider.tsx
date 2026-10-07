import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MutableRefObject, ReactNode } from 'react';

import { DEFAULT_THEME, ThemeContext, resolveTheme } from './theme-context';
import type {
  StationThemeBinding,
  StationThemeRead,
  Theme,
} from './theme-context';

// Dark is the default: PartFlow is shop-floor first (GUI_DESIGN §2.1).
// The theme resolves authenticated User preference → Scan Station
// preference → Dark default; Worker Sessions never affect it.
// - The Scan Station tier is live on station routes (Phase 13 slice 10):
//   the station view binds its saved preference, which applies when the
//   station loads, and the toggle there saves it for that station — only
//   while the station context is loaded and the connection is up, with
//   at most one request in flight. A context read that overlaps a save
//   of that station never reverts the screen — also after leaving the
//   station and returning while its save is still in flight.
// - Offline, while the station context is loading or in error, or when a
//   save fails, the change applies to this browser session only and
//   nothing is queued.
// - The User tier is an empty slot until Phase 14.
// - Other routes keep the choice for the session.

// The User tier (GUI_DESIGN §2.1 ①) needs an authenticated User — Phase
// 14 (OD-19); empty until then.
const USER_PREFERENCE: Theme | null = null;

/** The bound station tier. Write state belongs to its binding: a new
 * binding starts with nothing in flight and nothing desired. */
interface BoundStation {
  stationId: string;
  /** The server value last adopted (a fresh read or a settled save);
   * undefined = nothing adopted yet (the binding's first read overlapped
   * a save of this station). */
  applied: Theme | null | undefined;
  writable: boolean;
  save: (theme: Theme) => Promise<unknown>;
  onSaveFailed: (displayed: Theme) => void;
  inFlight: boolean;
  /** The latest choice still to be saved. */
  desired: Theme | null;
}

/** Saves of one station across its bindings: a save of a released
 * binding still overlaps the reads of the station's next binding. */
interface StationSaves {
  /** Saves started and not yet settled. */
  inFlight: number;
  /** The epoch at the latest start or settle of a save. */
  epoch: number;
}

/** The station tier's mutable state. `epoch` is +1 when a save starts
 * and +1 when it settles. */
interface StationTier {
  epoch: number;
  bound: BoundStation | null;
  saves: Map<string, StationSaves>;
}

function noteSave(tier: StationTier, stationId: string, delta: 1 | -1): void {
  tier.epoch += 1;
  const saves = tier.saves.get(stationId) ?? { inFlight: 0, epoch: 0 };
  saves.inFlight += delta;
  saves.epoch = tier.epoch;
  tier.saves.set(stationId, saves);
}

/** Rule 1c: a read is fresh only if no save of its station was in flight
 * when it was sent, started since, or settled since. */
function isFresh(tier: StationTier, stationId: string, read: StationThemeRead) {
  const saves = tier.saves.get(stationId);
  return (
    saves === undefined || (saves.inFlight === 0 && read.epoch >= saves.epoch)
  );
}

/** Single-flight save of one binding: the latest choice wins, so quick
 * toggles never land out of order. */
function sendSave(
  tier: StationTier,
  bound: BoundStation,
  displayed: MutableRefObject<Theme>,
): void {
  const target = bound.desired;
  if (target === null) return;
  bound.inFlight = true;
  noteSave(tier, bound.stationId, 1);
  const settle = (saved: boolean) => {
    noteSave(tier, bound.stationId, -1);
    bound.inFlight = false;
    if (tier.bound !== bound) return;
    // The server echoes the requested value (never a later writer's).
    if (saved) bound.applied = target;
    // A newer choice made while this request was in flight was never
    // attempted: send it once (not a retry).
    if (bound.writable && bound.desired !== null && bound.desired !== target) {
      sendSave(tier, bound, displayed);
      return;
    }
    bound.desired = null;
    // No automatic retry; an unknown outcome is resolved by the next
    // fresh read. A non-writable binding reports nothing: the offline
    // banner or the error state already shows the condition.
    if (!saved && bound.writable) bound.onSaveFailed(displayed.current);
  };
  void bound.save(target).then(
    () => settle(true),
    () => settle(false),
  );
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(DEFAULT_THEME);
  // Mirrors the theme on screen synchronously, for toggles within one
  // render and for the failure notice.
  const themeRef = useRef<Theme>(DEFAULT_THEME);
  const tier = useRef<StationTier>({
    epoch: 0,
    bound: null,
    saves: new Map(),
  });

  // The theme class lives on <body> so every surface — navigation,
  // dialogs, banners and view content — follows the selected mode.
  useEffect(() => {
    document.body.classList.remove('dark', 'light');
    document.body.classList.add(theme);
  }, [theme]);

  const show = useCallback((next: Theme) => {
    themeRef.current = next;
    setTheme(next);
  }, []);

  const toggleTheme = useCallback(() => {
    const next: Theme = themeRef.current === 'dark' ? 'light' : 'dark';
    show(next);
    const bound = tier.current.bound;
    if (bound === null) return;
    if (!bound.writable) {
      // Session-only: nothing is sent and nothing stays queued.
      bound.desired = null;
      return;
    }
    bound.desired = next;
    if (!bound.inFlight) sendSave(tier.current, bound, themeRef);
  }, [show]);

  const bindStation = useCallback(
    (next: StationThemeBinding) => {
      const bound = tier.current.bound;
      const same = bound !== null && bound.stationId === next.stationId;
      if (next.read === null) {
        // An unreadable context creates no binding; the bound station's
        // turns session-only and keeps what it adopted and the screen.
        if (same) {
          bound.writable = false;
          bound.save = next.save;
          bound.onSaveFailed = next.onSaveFailed;
        }
        return;
      }
      const fresh = isFresh(tier.current, next.stationId, next.read);
      if (!same) {
        // Entering a station route applies its saved theme (none → Dark)
        // — unless the read overlaps a save of this station still in
        // flight from before the route was left: then the screen keeps
        // the choice being saved and the next fresh read applies.
        tier.current.bound = {
          stationId: next.stationId,
          applied: fresh ? next.read.preference : undefined,
          writable: next.writable,
          save: next.save,
          onSaveFailed: next.onSaveFailed,
          inFlight: false,
          desired: null,
        };
        if (fresh) show(resolveTheme(USER_PREFERENCE, next.read.preference));
        return;
      }
      bound.writable = next.writable;
      bound.save = next.save;
      bound.onSaveFailed = next.onSaveFailed;
      // Only a fresh, changed read applies: an unchanged reload never
      // overrides a session-only choice, and a read that overlaps a save
      // of this station (sent before it started, or while it was in
      // flight) never reverts it.
      if (fresh && next.read.preference !== bound.applied) {
        bound.applied = next.read.preference;
        show(resolveTheme(USER_PREFERENCE, next.read.preference));
      }
    },
    [show],
  );

  const releaseStation = useCallback((stationId: string) => {
    // The theme stays: other routes keep the session theme. An in-flight
    // save of the released binding settles without effect.
    if (tier.current.bound?.stationId === stationId) tier.current.bound = null;
  }, []);

  const stationThemeEpoch = useCallback(() => tier.current.epoch, []);

  const value = useMemo(
    () => ({
      theme,
      toggleTheme,
      stationThemeEpoch,
      bindStation,
      releaseStation,
    }),
    [theme, toggleTheme, stationThemeEpoch, bindStation, releaseStation],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}
