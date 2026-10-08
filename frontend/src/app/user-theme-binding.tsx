import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { refusalFlag } from '../api/client';
import { saveOwnThemePreference } from '../api/session';
import type { SessionUser } from '../api/session';
import { useToastNotice } from '../components/toast-notice';
import { useConnectivity } from './connectivity-context';
import { useSession } from './session-context';
import { useTheme, useUserTheme } from './theme-context';
import type { Theme } from './theme-context';

const SIGN_IN_ENDED = 'Sign-in ended';
const SIGN_IN_ENDED_DETAIL =
  'Your sign-in has ended. Sign in again to save your theme to your account.';
const NOT_CONFIRMED = 'Theme not confirmed for your account';

function themeName(theme: Theme): string {
  return theme === 'dark' ? 'Dark' : 'Light';
}

/** Feeds the session into the theme provider: the signed-in User's tier and
 * whether the Scan Station tier may be saved. Reports a User-tier save that
 * PartFlow did not confirm. Renders only its toast. */
export function UserThemeBinding(): ReactNode {
  const { status, user } = useSession();
  const { status: connectivity } = useConnectivity();
  const { setStationSaves } = useTheme();
  const { showNotice, noticeElement } = useToastNotice();

  // The bound User is kept while the sign-in state is unknown, so the
  // station tier is never written for a User who may still be signed in.
  const [kept, setKept] = useState<SessionUser | null>(null);
  const bound =
    status === 'signed-in' ? user : status === 'signed-out' ? null : kept;
  if (bound !== kept) setKept(bound);

  // The Scan Station tier is saved only while nobody is known signed in.
  useEffect(() => {
    setStationSaves(status === 'signed-out');
  }, [status, setStationSaves]);

  const onSaveFailed = useCallback(
    (
      displayed: Theme,
      error: unknown,
      stationWarning: ((title: string, detail: string) => void) | undefined,
      released: boolean,
    ) => {
      // A required password change: its own dialog explains it.
      if (refusalFlag(error, 'password_change_required')) return;
      let title: string;
      let detail: string;
      if (refusalFlag(error, 'authentication_required')) {
        title = SIGN_IN_ENDED;
        detail = SIGN_IN_ENDED_DETAIL;
      } else if (released) {
        // Signed out, or another User signed in: nothing to report.
        return;
      } else {
        const shown = themeName(displayed);
        const other = themeName(displayed === 'dark' ? 'light' : 'dark');
        title = NOT_CONFIRMED;
        detail = `${shown} mode applies to this browser session only — PartFlow did not confirm saving it to your account. To save ${shown} for your account, switch to ${other} and back.`;
      }
      if (stationWarning) stationWarning(title, detail);
      else showNotice(`⚠ ${detail}`);
    },
    [showNotice],
  );

  useUserTheme(
    bound ? { id: bound.id, themePreference: bound.themePreference } : null,
    {
      writable:
        status === 'signed-in' &&
        user !== null &&
        !user.mustChangePassword &&
        connectivity === 'connected',
      save: saveOwnThemePreference,
      onSaveFailed,
    },
  );

  return noticeElement;
}
