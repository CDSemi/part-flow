import { Fragment, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import type { SessionUser } from '../api/session';
import { RetryFailedLoadsContext } from '../api/use-api-data';
import { ErrorState, LoadingState } from '../components/view-states';
import { useConnectivity } from './connectivity-context';
import { SessionContext, hasPermission, useSession } from './session-context';
import type { SessionValue } from './session-context';

// The sign-in gate of an area that needs a signed-in user —
// Administration (Phase 14 slice 2) and Management (slice 3). Nothing
// of the area is rendered, and so no request of it is sent, before a
// user is signed in. Signed out, a sign-in panel replaces the area, and
// entering the area signed out opens the Sign-in dialog once over it
// (not while first-run setup is open — setup needs the token from the
// server log). A user who must still replace a password an
// administrator set is not signed in yet for the area (the server
// refuses every request until then): a panel waits behind the Choose a
// new password dialog.
//
// Work survives an ended sign-in: when the server refuses a sign-in as
// ended, the area stays mounted with its open editors and drafts
// (presented as the user and with the permissions it was rendered with)
// while the Sign-in dialog — or, after an administrator set the
// password, the Choose a new password dialog — is open. When the same
// user's sign-in is usable again, every load that failed in the
// meantime runs again. The area belongs to the user it was rendered
// for: a different user signing in remounts it, dropping the drafts; an
// explicit sign-out shows the sign-in panel.

type Presented = 'sections' | 'checking' | 'gate' | 'password';

export type GatedArea = 'Administration' | 'Management';

const AREA_COPY: Record<
  GatedArea,
  { gate: string; password: string; offline: string }
> = {
  Administration: {
    gate: "Sign in to view and change PartFlow's configuration.",
    password:
      "Choose a new password to view and change PartFlow's configuration.",
    offline: 'Administration needs the connection to the PartFlow server.',
  },
  Management: {
    gate: "Sign in to use PartFlow's Management screens.",
    password: "Choose a new password to use PartFlow's Management screens.",
    offline: 'Management needs the connection to the PartFlow server.',
  },
};

export function SignInGate({
  area,
  onSignedIn,
  children,
}: {
  area: GatedArea;
  /** Called when the area appears after a sign-in from its panel (the
   * area moves focus to its active navigation item). */
  onSignedIn: () => void;
  /** The area, rendered for its owner. */
  children: ReactNode;
}) {
  const session = useSession();
  const { status: connectivity } = useConnectivity();
  const { status, setupOpen, endedBy, openSignIn } = session;
  const copy = AREA_COPY[area];
  // The user the area is rendered for (its drafts belong to them).
  const [owner, setOwner] = useState<SessionUser | null>(null);
  const user = status === 'signed-in' ? session.user : null;
  const changePending = user?.mustChangePassword === true;
  const signedInUser = changePending ? null : user;
  // The area stays for its owner while the sign-in is ended, or while
  // the same user must first replace an administrator-set password.
  const keepSections =
    owner !== null &&
    ((status !== 'signed-in' && endedBy === 'expired') ||
      (changePending && user?.id === owner.id));
  if (signedInUser !== null && signedInUser !== owner) {
    setOwner(signedInUser);
  } else if (
    (status === 'signed-out' || changePending) &&
    !keepSections &&
    owner !== null
  ) {
    setOwner(null);
  }
  // Each time a kept area becomes usable again for the same user, the
  // loads that failed meanwhile (refused as signed out) run again.
  const [keptBefore, setKeptBefore] = useState(false);
  const [resumed, setResumed] = useState(0);
  if (keepSections !== keptBefore) {
    setKeptBefore(keepSections);
    if (
      !keepSections &&
      signedInUser !== null &&
      signedInUser.id === owner?.id
    ) {
      setResumed((count) => count + 1);
    }
  }
  const presented: Presented =
    signedInUser !== null || keepSections
      ? 'sections'
      : changePending
        ? 'password'
        : status === 'unknown'
          ? 'checking'
          : 'gate';

  const gateSignInRef = useRef<HTMLButtonElement>(null);

  // Entering the area signed out (the first known sign-in of this
  // entry) opens the Sign-in dialog once; closing it returns focus to
  // the panel's Sign in button (the dialog restores focus to its opener).
  const entryDecided = useRef(false);
  useEffect(() => {
    if (entryDecided.current || status === 'unknown') return;
    entryDecided.current = true;
    if (status === 'signed-out' && !setupOpen) {
      gateSignInRef.current?.focus();
      openSignIn();
    }
  }, [status, setupOpen, openSignIn]);

  // A sign-in from the panel moves focus into the area.
  const signedInRef = useRef(onSignedIn);
  signedInRef.current = onSignedIn;
  const previouslyPresented = useRef<Presented | null>(null);
  useEffect(() => {
    const previous = previouslyPresented.current;
    previouslyPresented.current = presented;
    if (
      presented === 'sections' &&
      (previous === 'gate' || previous === 'password')
    ) {
      signedInRef.current();
    }
  }, [presented]);

  if (presented === 'checking') {
    return session.checking || connectivity === 'connecting' ? (
      <LoadingState label="Checking your sign-in" />
    ) : connectivity === 'unavailable' ? (
      <ErrorState message={copy.offline} />
    ) : (
      <ErrorState
        message="Your sign-in could not be checked."
        onRetry={() => void session.refresh()}
      />
    );
  }

  if (presented === 'password') {
    return (
      <div className="signin-gate">
        <div className="signin-gatepanel">
          <h1>{area}</h1>
          <p>{copy.password}</p>
        </div>
      </div>
    );
  }

  if (presented === 'gate') {
    return (
      <div className="signin-gate">
        <div className="signin-gatepanel">
          <h1>{area}</h1>
          {setupOpen ? (
            <>
              <p>
                PartFlow has no administrator yet. Set up PartFlow with the
                setup token from the server log, or sign in if you already have
                an account.
              </p>
              <div className="row">
                <button className="btn primary" onClick={session.openSetup}>
                  Set up PartFlow
                </button>
                <button
                  ref={gateSignInRef}
                  className="btn ghost"
                  onClick={openSignIn}
                >
                  Sign in
                </button>
              </div>
            </>
          ) : (
            <>
              <p>{copy.gate}</p>
              <div className="row">
                <button
                  ref={gateSignInRef}
                  className="btn primary"
                  onClick={openSignIn}
                >
                  Sign in
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    );
  }

  const areaUser = signedInUser ?? owner;
  // A kept area is presented as the user and with the permissions it
  // was rendered with; the server refuses every write until that user's
  // sign-in is usable again.
  const areaSession: SessionValue = keepSections
    ? { ...session, user: owner, can: (key) => hasPermission(owner, key) }
    : session;

  return (
    <SessionContext.Provider value={areaSession}>
      <RetryFailedLoadsContext.Provider value={resumed}>
        <Fragment key={areaUser?.id ?? 0}>{children}</Fragment>
      </RetryFailedLoadsContext.Provider>
    </SessionContext.Provider>
  );
}
