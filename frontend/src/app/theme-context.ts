import { createContext, useContext, useEffect, useRef } from 'react';

export type Theme = 'dark' | 'light';

/** Dark is the default (GUI_DESIGN §2.1 ③). */
export const DEFAULT_THEME: Theme = 'dark';

/** GUI_DESIGN §2.1 precedence: ① the authenticated User's preference,
 * ② the Scan Station's preference, ③ Dark. */
export function resolveTheme(user: Theme | null, station: Theme | null): Theme {
  return user ?? station ?? DEFAULT_THEME;
}

/** One read of the station's saved preference, with the provider's save
 * epoch captured immediately BEFORE the read was sent. */
export interface StationThemeRead {
  preference: Theme | null;
  epoch: number;
}

export interface StationThemeBinding {
  stationId: string;
  /** null = the station context is not readable now (loading or error). */
  read: StationThemeRead | null;
  /** Context loaded and connected: the toggle saves. Otherwise session-only. */
  writable: boolean;
  save: (theme: Theme) => Promise<unknown>;
  /** Called with the theme on screen when a save fails while writable. */
  onSaveFailed: (displayed: Theme) => void;
}

export interface ThemeValue {
  theme: Theme;
  toggleTheme: () => void;
  /** The station save epoch: +1 when a save starts and +1 when it settles
   * (stable function). A read is fresh only if no save of its station
   * was in flight when it was sent, or started or settled since
   * (theme-provider rule 1c). */
  stationThemeEpoch: () => number;
  /** Registers or refreshes the station tier (theme-provider rules). */
  bindStation: (binding: StationThemeBinding) => void;
  /** Unbinds when that station is the bound one; the theme stays. */
  releaseStation: (stationId: string) => void;
}

export const ThemeContext = createContext<ThemeValue | null>(null);

export function useTheme(): ThemeValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error('useTheme must be used within ThemeProvider');
  return value;
}

export interface StationThemeOptions {
  writable: boolean;
  save: (theme: Theme) => Promise<unknown>;
  onSaveFailed: (displayed: Theme) => void;
}

/** Bind the current station route's theme tier. `read` undefined = the
 * station context is not loaded or is in its error state. */
export function useStationTheme(
  stationId: string,
  read: StationThemeRead | undefined,
  options: StationThemeOptions,
): void {
  const { bindStation, releaseStation } = useTheme();
  // The latest callbacks, read only when a save starts or settles: a new
  // closure per render never re-binds or re-applies anything.
  const latest = useRef(options);
  useEffect(() => {
    latest.current = options;
  });

  useEffect(() => () => releaseStation(stationId), [stationId, releaseStation]);

  const unread = read === undefined;
  const preference = read?.preference ?? null;
  const epoch = read?.epoch ?? 0;
  const writable = options.writable && !unread;
  useEffect(() => {
    bindStation({
      stationId,
      read: unread ? null : { preference, epoch },
      writable,
      save: (theme) => latest.current.save(theme),
      onSaveFailed: (theme) => latest.current.onSaveFailed(theme),
    });
  }, [bindStation, stationId, unread, preference, epoch, writable]);
}
