import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { RouterContext } from './router-context';
import type { NavigationGuard } from './router-context';
import {
  DEFAULT_MANAGEMENT_SUBVIEW,
  managementEntrySubview,
  resolvePath,
} from './router-core';
import type { ManagementSubview } from './router-core';

export function RouterProvider({ children }: { children: ReactNode }) {
  const [path, setPath] = useState(() => window.location.pathname);
  // Last-used Management sub view — session-only presentation state
  // (GUI_DESIGN §1.1); intentionally not persisted anywhere.
  const lastManagementSubview = useRef<ManagementSubview>(
    DEFAULT_MANAGEMENT_SUBVIEW,
  );
  // Single active navigation guard (unsaved-change protection). A ref,
  // not state: registering a guard must never re-render the router.
  const guardRef = useRef<NavigationGuard | null>(null);
  const pathRef = useRef(path);
  pathRef.current = path;
  // The Management sub views the signed-in user may open (null while
  // unknown), pushed in by the shell, which reads the sign-in. A ref:
  // it only steers the next bare '/management' entry.
  const readableRef = useRef<ReadonlySet<ManagementSubview> | null>(null);
  // The landing of the last bare '/management' entry redirect made
  // while the readable sub views were unknown, and the last-used sub
  // view it was resolved from; cleared by any navigation and by the
  // first known readable set (which corrects it at most once).
  const landingRef = useRef<{
    path: string;
    lastUsed: ManagementSubview;
  } | null>(null);

  useEffect(() => {
    const onPopState = () => {
      // Browser back/forward already changed the URL. If the active view
      // refuses to be left, restore the guarded URL as a new entry.
      if (guardRef.current && !guardRef.current()) {
        window.history.pushState({}, '', pathRef.current);
        return;
      }
      landingRef.current = null;
      setPath(window.location.pathname);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const resolved = resolvePath(
    path,
    lastManagementSubview.current,
    readableRef.current,
  );
  const redirectTo = 'redirect' in resolved ? resolved.redirect : null;

  const navigate = useCallback(
    (to: string) => {
      if (to === path && window.location.pathname === path) return;
      if (guardRef.current && !guardRef.current()) return;
      landingRef.current = null;
      window.history.pushState({}, '', to);
      setPath(to);
    },
    [path],
  );

  const setNavigationGuard = useCallback((guard: NavigationGuard | null) => {
    guardRef.current = guard;
  }, []);

  // A bare '/management' entry made before the sign-in was known (or
  // before a sign-in from the Management panel) lands again on the first
  // sub view the user may open once it is known — only once, only while
  // the URL is still that landing, and never past a view holding unsaved
  // work (an active navigation guard); a deep link is never redirected.
  // A later change of the readable set (another sign-in, changed
  // permissions) never moves the open view. The router never reads the
  // sign-in itself (the session lives below it).
  const setManagementReadable = useCallback(
    (readable: ReadonlySet<ManagementSubview> | null) => {
      readableRef.current = readable;
      const landing = landingRef.current;
      // An ended sign-in never moves the open view (its work is kept).
      if (readable === null) return;
      landingRef.current = null;
      if (landing === null || landing.path !== pathRef.current) return;
      if (guardRef.current) return;
      const target = `/management/${managementEntrySubview(landing.lastUsed, readable)}`;
      if (target === landing.path) return;
      window.history.replaceState({}, '', target);
      setPath(target);
    },
    [],
  );

  // Entry redirects ('/' and '/management') replace the history entry so
  // browser back/forward never lands on a forwarding URL.
  useEffect(() => {
    if (redirectTo) {
      // Only a landing resolved without the readable sub views is
      // corrected once they are known.
      landingRef.current =
        redirectTo.startsWith('/management/') && readableRef.current === null
          ? { path: redirectTo, lastUsed: lastManagementSubview.current }
          : null;
      window.history.replaceState({}, '', redirectTo);
      setPath(redirectTo);
    }
  }, [redirectTo]);

  if ('redirect' in resolved) {
    return null; // one render while the entry redirect settles
  }

  if (resolved.view === 'management') {
    lastManagementSubview.current = resolved.subview;
  }

  return (
    <RouterContext.Provider
      value={{
        route: resolved,
        path,
        navigate,
        setNavigationGuard,
        setManagementReadable,
      }}
    >
      {children}
    </RouterContext.Provider>
  );
}
