import { useEffect, useRef, useState } from 'react';

import { userAvatarUrl } from '../api/users';
import { useConnectivity } from '../app/connectivity-context';
import { useSession } from '../app/session-context';
import { Avatar } from './Avatar';

/**
 * The user account control of the top navigation. Signed out (or while
 * the sign-in is not known) it offers `Sign in` — and `Set up PartFlow`
 * while PartFlow has no administrator; signed in it shows the user and
 * opens a small menu with the role, `Change password…` and `Sign out`.
 * The top navigation (and with it this chip) is hidden in Scan Station
 * production mode and the Production Board kiosk.
 */
export function AccountChip() {
  const session = useSession();
  const { status: connectivity } = useConnectivity();
  const [menuOpen, setMenuOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const firstItemRef = useRef<HTMLButtonElement>(null);

  const signedIn = session.status === 'signed-in' && session.user !== null;

  // The menu closes on a click outside it; opening focuses its first
  // item.
  useEffect(() => {
    if (!menuOpen) return;
    firstItemRef.current?.focus();
    function onClick(event: MouseEvent) {
      const target = event.target;
      if (
        target instanceof Node &&
        !popoverRef.current?.contains(target) &&
        !buttonRef.current?.contains(target)
      ) {
        setMenuOpen(false);
      }
    }
    document.addEventListener('click', onClick);
    return () => document.removeEventListener('click', onClick);
  }, [menuOpen]);

  useEffect(() => {
    if (!signedIn) setMenuOpen(false);
  }, [signedIn]);

  if (!signedIn || session.user === null) {
    return (
      <span className="acctchip">
        {session.setupOpen ? (
          <button className="btn primary" onClick={session.openSetup}>
            Set up PartFlow
          </button>
        ) : null}
        <button className="btn ghost" onClick={session.openSignIn}>
          Sign in
        </button>
      </span>
    );
  }

  const user = session.user;
  // Focus returns to the account button before a dialog opens, so the
  // dialog restores focus to it when it closes.
  const closeMenu = () => {
    buttonRef.current?.focus();
    setMenuOpen(false);
  };

  return (
    <span className="acctchip">
      <button
        ref={buttonRef}
        className="navbtn acctbtn"
        aria-label={`Account: ${user.displayName}`}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((open) => !open)}
      >
        <Avatar
          name={user.displayName}
          size="sm"
          src={userAvatarUrl({
            id: user.id,
            avatarUpdatedAt: user.avatarUpdatedAt,
          })}
        />
        <span className="acctname">{user.displayName}</span>
      </button>
      {menuOpen ? (
        <div
          ref={popoverRef}
          className="acctmenu"
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation();
              closeMenu();
            }
          }}
        >
          <div className="acctrole">{user.roleName}</div>
          <div role="menu" aria-label="Account">
            <button
              ref={firstItemRef}
              role="menuitem"
              onClick={() => {
                closeMenu();
                session.openChangePassword();
              }}
            >
              Change password…
            </button>
            <button
              role="menuitem"
              disabled={connectivity !== 'connected'}
              onClick={() => {
                closeMenu();
                void session.signOut();
              }}
            >
              Sign out
            </button>
          </div>
        </div>
      ) : null}
    </span>
  );
}
