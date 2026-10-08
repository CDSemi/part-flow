import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MutableRefObject, ReactNode } from 'react';

import { DEFAULT_THEME, ThemeContext, resolveTheme } from './theme-context';
import type {
  StationThemeBinding,
  StationThemeRead,
  Theme,
  UserThemeBinding,
} from './theme-context';

// Dark is the default: PartFlow is shop-floor first (GUI_DESIGN §2.1).
// The theme resolves the signed-in User's preference → the Scan Station
// preference → Dark; Worker Sessions never affect it.
// - The User tier is live since Phase 14 slice 8: the session binds the
//   signed-in User's saved preference, which applies on every route, and
//   while that User is signed in the toggle saves it to the User's
//   account on every route (Scan Station and Kiosk included) — only
//   while connected and no new password is required, with at most one
//   request in flight. A later session read never changes the screen.
// - The Scan Station tier is live on station routes (Phase 13 slice 10):
//   the station view binds its saved preference, which applies when the
//   station loads unless the User tier governs. Its toggle saves it for
//   that station only while the browser knows nobody is signed in, the
//   station context is loaded and the connection is up, with at most one
//   request in flight. A context read that overlaps a save of that
//   station never reverts the screen — also after leaving the station
//   and returning while its save is still in flight.
// - While the sign-in state is unknown, the toggle is session-only.
// - Offline, while a station context is loading or in error, while a
//   new password is required, or when a save fails, the change applies
//   to this browser session only and nothing is queued.
// - Signing out (or an ended sign-in) returns the screen to the station
//   preference on station routes and to Dark elsewhere.
// - Other routes keep an anonymous choice for the session.

/** The bound User tier: the session's signed-in User. */
interface BoundUser {
  userId: number;
  /** The User's saved preference as last read or confirmed. */
  applied: Theme | null;
  writable: boolean;
  save: UserThemeBinding['save'];
  onSaveFailed: UserThemeBinding['onSaveFailed'];
  inFlight: boolean;
  /** The latest choice still to be saved. */
  desired: Theme | null;
}

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
  showWarning: StationThemeBinding['showWarning'];
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
 * toggles never land out of order. `savesOpen` says whether the station
 * tier may still be saved (the browser knows nobody is signed in). */
function sendSave(
  tier: StationTier,
  bound: BoundStation,
  displayed: MutableRefObject<Theme>,
  savesOpen: () => boolean,
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
    // attempted: send it once (not a retry) — unless a User signed in
    // meanwhile, whose toggle saves the User tier instead.
    if (
      bound.writable &&
      savesOpen() &&
      bound.desired !== null &&
      bound.desired !== target
    ) {
      sendSave(tier, bound, displayed, savesOpen);
      return;
    }
    bound.desired = null;
    // No automatic retry; an unknown outcome is resolved by the next
    // fresh read. A non-writable binding reports nothing: the offline
    // banner or the error state already shows the condition. Nor does a
    // save that settles after a User signed in: the station copy's
    // recovery would now save the User tier.
    if (!saved && bound.writable && savesOpen()) {
      bound.onSaveFailed(displayed.current);
    }
  };
  void bound.save(target).then(
    () => settle(true),
    () => settle(false),
  );
}

/** Single-flight save of the User tier (no epoch: no User read applies
 * while bound). A save of a released binding changes nothing on screen
 * and never restarts; its failure is still reported, as released. */
function sendUserSave(
  slot: MutableRefObject<BoundUser | null>,
  bound: BoundUser,
  tier: StationTier,
  displayed: MutableRefObject<Theme>,
): void {
  const target = bound.desired;
  if (target === null) return;
  bound.inFlight = true;
  const settle = (saved: boolean, error: unknown) => {
    bound.inFlight = false;
    // A failure shows in the station's floating notice while a loaded,
    // writable station is bound.
    const station = tier.bound?.writable ? tier.bound.showWarning : undefined;
    if (slot.current !== bound) {
      if (!saved) bound.onSaveFailed(displayed.current, error, station, true);
      return;
    }
    // The server echoes the requested value (never a later writer's).
    if (saved) bound.applied = target;
    // A newer choice made while this request was in flight was never
    // attempted: send it once (not a retry).
    if (bound.writable && bound.desired !== null && bound.desired !== target) {
      sendUserSave(slot, bound, tier, displayed);
      return;
    }
    bound.desired = null;
    // No automatic retry. A non-writable binding reports nothing: the
    // offline banner or the forced password change already explains it.
    if (!saved && bound.writable) {
      bound.onSaveFailed(displayed.current, error, station, false);
    }
  };
  void bound.save(target).then(
    () => settle(true, undefined),
    (error: unknown) => settle(false, error),
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
  const user = useRef<BoundUser | null>(null);
  // True until the session reports otherwise, so a tree without the
  // session binding keeps the station behavior.
  const stationSavesAllowed = useRef(true);

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

  /** The User tier's saved preference (null = none, or nobody bound). */
  const userTier = useCallback(() => user.current?.applied ?? null, []);

  /** The User tier decides the screen: a saved preference, or a save of
   * one pending. */
  const userGoverns = useCallback(() => {
    const bound = user.current;
    return (
      bound !== null &&
      (bound.applied !== null || bound.inFlight || bound.desired !== null)
    );
  }, []);

  /** The station tier may be saved: the browser knows nobody is signed in. */
  const stationSavesOpen = useCallback(
    () => stationSavesAllowed.current && user.current === null,
    [],
  );

  const toggleTheme = useCallback(() => {
    const next: Theme = themeRef.current === 'dark' ? 'light' : 'dark';
    show(next);
    // While a User is signed in, the toggle saves the User tier on every
    // route and never the station's.
    const bound = user.current;
    if (bound !== null) {
      if (!bound.writable) {
        // Session-only: nothing is sent and nothing stays queued.
        bound.desired = null;
        return;
      }
      bound.desired = next;
      if (!bound.inFlight) sendUserSave(user, bound, tier.current, themeRef);
      return;
    }
    const station = tier.current.bound;
    if (station === null) return;
    if (!station.writable || !stationSavesOpen()) {
      // Session-only: nothing is sent and nothing stays queued.
      station.desired = null;
      return;
    }
    station.desired = next;
    if (!station.inFlight) {
      sendSave(tier.current, station, themeRef, stationSavesOpen);
    }
  }, [show, stationSavesOpen]);

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
          bound.showWarning = next.showWarning;
        }
        return;
      }
      const fresh = isFresh(tier.current, next.stationId, next.read);
      if (!same) {
        // Entering a station route applies its saved theme (none → Dark)
        // below the User's — unless the read overlaps a save of this
        // station still in flight from before the route was left: then
        // the screen keeps the choice being saved and the next fresh
        // read applies. A User save in flight likewise keeps the screen.
        tier.current.bound = {
          stationId: next.stationId,
          applied: fresh ? next.read.preference : undefined,
          writable: next.writable,
          save: next.save,
          onSaveFailed: next.onSaveFailed,
          showWarning: next.showWarning,
          inFlight: false,
          desired: null,
        };
        if (fresh && !(user.current?.inFlight ?? false)) {
          show(resolveTheme(userTier(), next.read.preference));
        }
        return;
      }
      bound.writable = next.writable;
      bound.save = next.save;
      bound.onSaveFailed = next.onSaveFailed;
      bound.showWarning = next.showWarning;
      // Only a fresh, changed read is adopted: an unchanged reload never
      // overrides a session-only choice, and a read that overlaps a save
      // of this station (sent before it started, or while it was in
      // flight) never reverts it. While the User tier governs, station
      // reads never change the screen.
      if (fresh && next.read.preference !== bound.applied) {
        bound.applied = next.read.preference;
        if (!userGoverns()) {
          show(resolveTheme(userTier(), next.read.preference));
        }
      }
    },
    [show, userTier, userGoverns],
  );

  const releaseStation = useCallback((stationId: string) => {
    // The theme stays: other routes keep the session theme. An in-flight
    // save of the released binding settles without effect.
    if (tier.current.bound?.stationId === stationId) tier.current.bound = null;
  }, []);

  const stationThemeEpoch = useCallback(() => tier.current.epoch, []);

  const releaseUser = useCallback(
    (userId: number) => {
      if (user.current?.userId !== userId) return;
      // Sign-out returns to Station → Dark (an unadopted station read
      // counts as none). An in-flight save of the released binding
      // changes nothing on screen and never restarts.
      user.current = null;
      show(resolveTheme(null, tier.current.bound?.applied ?? null));
    },
    [show],
  );

  const bindUser = useCallback(
    (next: UserThemeBinding) => {
      const current = user.current;
      if (current !== null && current.userId !== next.userId) {
        releaseUser(current.userId);
      }
      const bound = user.current;
      if (bound !== null) {
        // The same User: a later session read never changes the screen.
        bound.writable = next.writable;
        bound.save = next.save;
        bound.onSaveFailed = next.onSaveFailed;
        return;
      }
      user.current = {
        userId: next.userId,
        applied: next.preference,
        writable: next.writable,
        save: next.save,
        onSaveFailed: next.onSaveFailed,
        inFlight: false,
        desired: null,
      };
      // No saved preference: the lower tiers already govern the screen.
      if (next.preference !== null) show(next.preference);
      // A queued anonymous station choice is never sent after sign-in.
      const station = tier.current.bound;
      if (station !== null) station.desired = null;
    },
    [show, releaseUser],
  );

  const setStationSaves = useCallback((allowed: boolean) => {
    stationSavesAllowed.current = allowed;
  }, []);

  const value = useMemo(
    () => ({
      theme,
      toggleTheme,
      stationThemeEpoch,
      bindStation,
      releaseStation,
      bindUser,
      releaseUser,
      setStationSaves,
    }),
    [
      theme,
      toggleTheme,
      stationThemeEpoch,
      bindStation,
      releaseStation,
      bindUser,
      releaseUser,
      setStationSaves,
    ],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}
