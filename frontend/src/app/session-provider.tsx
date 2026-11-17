import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { setAuthFailureListener } from '../api/client';
import { getSession, signOut as signOutRequest } from '../api/session';
import type { SessionState, SessionUser } from '../api/session';
import { ChangePasswordDialog } from '../components/ChangePasswordDialog';
import { EmailDialog } from '../components/EmailDialog';
import { FirstRunSetupDialog } from '../components/FirstRunSetupDialog';
import { SignInDialog } from '../components/SignInDialog';
import { useToastNotice } from '../components/toast-notice';
import { TwoStepDialog } from '../components/TwoStepDialog';
import { useConnectivity } from './connectivity-context';
import { useRouter } from './router-context';
import { isChromeHidden } from './router-core';
import { SessionContext, hasPermission } from './session-context';
import type {
  SessionEnd,
  SessionStatus,
  SessionValue,
} from './session-context';

// The user sign-in of this browser (application Users — never Workers,
// whose Scan Station badge sign-in is separate). The server is the only
// authority: this provider reads `GET /api/session` at start (which also
// renews the cookie lifetime), keeps the answer, and re-reads it on
// request. A read that fails (no answer, a malformed body) leaves the
// state `unknown`; it is read again each time the connection to the
// server is regained while still unknown. Nothing is polled, queued or
// retried on its own. `checking` says a read is in flight; `endedBy`
// says whether the last sign-in ended by signing out or because the
// server refused it as ended (Administration keeps open work for the
// latter). A refusal of a request sent with `promptSignIn: false` (the
// theme save) records the ended sign-in without opening the Sign-in
// dialog.
//
// The sign-in, change-password, two-step sign-in, email address and
// first-run setup dialogs render AFTER the routed view, so they stack
// above any view dialog and the view underneath stays mounted with its
// drafts. They never render inside the top navigation: its backdrop
// filter makes it the containing block of their fixed-position overlay,
// which would center them on the navigation bar instead of the screen.
// A password an administrator set opens the forced change dialog, which
// stays until the password is changed or the user signs out. The two-step
// sign-in and email address dialogs close when the sign-in ends. Scan
// Station production mode and the Production Board kiosk never show these
// dialogs.

type DialogKind =
  | 'sign-in'
  | 'setup'
  | 'change-password'
  | 'two-step-sign-in'
  | 'email-address';

interface KnownState {
  status: SessionStatus;
  user: SessionUser | null;
  setupOpen: boolean;
  endedBy: SessionEnd;
}

const SIGN_OUT_FAILED = 'Sign-out did not complete. Try again.';

export function SessionProvider({ children }: { children: ReactNode }) {
  const { status: connectivity } = useConnectivity();
  const { route } = useRouter();
  const [state, setState] = useState<KnownState>({
    status: 'unknown',
    user: null,
    setupOpen: false,
    endedBy: null,
  });
  // The first read starts on mount, so the state is being checked from
  // the first render on.
  const [checking, setChecking] = useState(true);
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [signInNotice, setSignInNotice] = useState<string | null>(null);
  const { showNotice, noticeElement } = useToastNotice();

  // Every answer that sets the state takes a new generation, so a read
  // sent earlier never overwrites a later sign-in, sign-out or change.
  const generation = useRef(0);
  const reading = useRef(0);
  const statusRef = useRef<SessionStatus>(state.status);
  useEffect(() => {
    statusRef.current = state.status;
  });

  const apply = useCallback((next: SessionState) => {
    generation.current += 1;
    setState((current) => ({
      status: next.user ? 'signed-in' : 'signed-out',
      user: next.user,
      setupOpen: next.setupOpen,
      endedBy: next.user ? null : current.endedBy,
    }));
  }, []);

  const applySignedOut = useCallback((endedBy: SessionEnd) => {
    generation.current += 1;
    setState((current) => ({
      status: 'signed-out',
      user: null,
      setupOpen: current.setupOpen,
      endedBy,
    }));
  }, []);

  /** Read the sign-in; the answer, or null when it could not be read
   * (a known state is then kept, an unknown one stays unknown). */
  const reload = useCallback(async (): Promise<SessionState | null> => {
    const sent = ++generation.current;
    reading.current += 1;
    setChecking(true);
    try {
      const next = await getSession();
      if (generation.current === sent) apply(next);
      return next;
    } catch {
      return null;
    } finally {
      reading.current -= 1;
      if (reading.current === 0) setChecking(false);
    }
  }, [apply]);

  useEffect(() => {
    void reload();
    return () => {
      generation.current += 1;
    };
  }, [reload]);

  // Regaining the connection while the state is unknown reads it again
  // (once per regained connection — never a polling loop). An outdated
  // page reads like a connected one: reads are allowed (GUI §3 rule 13).
  useEffect(() => {
    if (
      (connectivity === 'connected' || connectivity === 'outdated') &&
      statusRef.current === 'unknown' &&
      reading.current === 0
    ) {
      void reload();
    }
  }, [connectivity, reload]);

  useEffect(() => {
    setAuthFailureListener((kind, prompt) => {
      if (kind === 'authentication_required') {
        applySignedOut('expired');
        if (prompt) {
          setSignInNotice(null);
          setDialog('sign-in');
        }
      } else {
        // The server requires a new password first: the re-read state
        // opens the forced change dialog.
        void reload();
      }
    });
    return () => setAuthFailureListener(null);
  }, [applySignedOut, reload]);

  const signOut = useCallback(async () => {
    try {
      await signOutRequest();
      applySignedOut('sign-out');
      setDialog(null);
    } catch {
      const after = await reload();
      if (after === null || after.user !== null) showNotice(SIGN_OUT_FAILED);
      else applySignedOut('sign-out');
    }
  }, [applySignedOut, reload, showNotice]);

  const { status, user, setupOpen, endedBy } = state;

  // The signed-in user's own dialogs close when the sign-in ends.
  useEffect(() => {
    if (user !== null) return;
    setDialog((current) =>
      current === 'two-step-sign-in' || current === 'email-address'
        ? null
        : current,
    );
  }, [user]);

  const value = useMemo<SessionValue>(
    () => ({
      status,
      user,
      setupOpen,
      checking,
      endedBy,
      can: (permission) => hasPermission(user, permission),
      openSignIn: () => {
        setSignInNotice(null);
        setDialog('sign-in');
      },
      openSetup: () => setDialog('setup'),
      openChangePassword: () => setDialog('change-password'),
      openTwoStepSignIn: () => setDialog('two-step-sign-in'),
      openEmailAddress: () => setDialog('email-address'),
      signOut,
      refresh: async () => {
        await reload();
      },
    }),
    [status, user, setupOpen, checking, endedBy, signOut, reload],
  );

  const closeDialog = () => {
    setDialog(null);
    setSignInNotice(null);
  };
  const signedIn = (next: SessionState) => {
    apply(next);
    closeDialog();
  };

  const forced = user?.mustChangePassword === true;
  const showDialogs = !isChromeHidden(route);
  const changeOpen = forced || (dialog === 'change-password' && user !== null);
  const ownDialog = showDialogs && !changeOpen && user !== null ? dialog : null;

  return (
    <SessionContext.Provider value={value}>
      {children}
      {showDialogs && changeOpen ? (
        <ChangePasswordDialog
          forced={forced}
          onCancel={closeDialog}
          onChanged={(next) => {
            signedIn(next);
            showNotice('Your password was changed.');
          }}
          onSignOut={signOut}
          onRefresh={reload}
          onEnded={(notice) => {
            setSignInNotice(notice);
            setDialog('sign-in');
          }}
        />
      ) : null}
      {ownDialog === 'two-step-sign-in' ? (
        <TwoStepDialog onClose={closeDialog} />
      ) : null}
      {ownDialog === 'email-address' ? (
        <EmailDialog onClose={closeDialog} />
      ) : null}
      {showDialogs && !changeOpen && dialog === 'sign-in' ? (
        <SignInDialog
          notice={signInNotice}
          onCancel={closeDialog}
          onSignedIn={signedIn}
          onRefresh={reload}
        />
      ) : null}
      {showDialogs && !changeOpen && dialog === 'setup' ? (
        <FirstRunSetupDialog
          onCancel={closeDialog}
          onCreated={(next) => {
            signedIn(next);
            if (next.user) {
              showNotice(
                `PartFlow is set up. You are signed in as ${next.user.displayName}.`,
              );
            }
          }}
          onOpenSignIn={() => {
            setSignInNotice(null);
            setDialog('sign-in');
          }}
          onRefresh={reload}
        />
      ) : null}
      {noticeElement}
    </SessionContext.Provider>
  );
}
