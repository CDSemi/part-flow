import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { RouterContext } from './router-context';
import type { NavigationGuard } from './router-context';
import { DEFAULT_MANAGEMENT_SUBVIEW, resolvePath } from './router-core';
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

  useEffect(() => {
    const onPopState = () => {
      // Browser back/forward already changed the URL. If the active view
      // refuses to be left, restore the guarded URL as a new entry.
      if (guardRef.current && !guardRef.current()) {
        window.history.pushState({}, '', pathRef.current);
        return;
      }
      setPath(window.location.pathname);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const resolved = resolvePath(path, lastManagementSubview.current);
  const redirectTo = 'redirect' in resolved ? resolved.redirect : null;

  const navigate = useCallback(
    (to: string) => {
      if (to === path && window.location.pathname === path) return;
      if (guardRef.current && !guardRef.current()) return;
      window.history.pushState({}, '', to);
      setPath(to);
    },
    [path],
  );

  const setNavigationGuard = useCallback((guard: NavigationGuard | null) => {
    guardRef.current = guard;
  }, []);

  // Entry redirects ('/' and '/management') replace the history entry so
  // browser back/forward never lands on a forwarding URL.
  useEffect(() => {
    if (redirectTo) {
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
      value={{ route: resolved, path, navigate, setNavigationGuard }}
    >
      {children}
    </RouterContext.Provider>
  );
}
