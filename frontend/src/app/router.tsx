import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react';
import type { AnchorHTMLAttributes, ReactNode } from 'react';

// Minimal history-based router for the Phase 2 shell.
//
// The Phase 2 navigation requirements are a small, fixed route table with
// meaningful URLs, working browser back/forward, and a not-found state.
// A ~100-line router over the History API covers exactly that, so no
// routing dependency is introduced (see README — Phase 2 frontend).

export const MANAGEMENT_SUBVIEWS = [
  'area-board',
  'tracking',
  'purchase-orders',
  'priority',
] as const;

export type ManagementSubview = (typeof MANAGEMENT_SUBVIEWS)[number];

export type Route =
  | { view: 'scan-station' }
  | { view: 'production-board' }
  | { view: 'management'; subview: ManagementSubview }
  | { view: 'administration' }
  | { view: 'not-found'; path: string };

export const DEFAULT_MANAGEMENT_SUBVIEW: ManagementSubview = 'area-board';

function isManagementSubview(value: string): value is ManagementSubview {
  return (MANAGEMENT_SUBVIEWS as readonly string[]).includes(value);
}

/**
 * Resolve a pathname to a route, or to a redirect target for the two
 * entry paths that forward elsewhere ('/' and bare '/management').
 */
export function resolvePath(
  pathname: string,
  lastManagementSubview: ManagementSubview,
): Route | { redirect: string } {
  const path = pathname.replace(/\/+$/, '') || '/';
  if (path === '/') return { redirect: '/scan-station' };
  if (path === '/scan-station') return { view: 'scan-station' };
  if (path === '/production-board') return { view: 'production-board' };
  if (path === '/administration') return { view: 'administration' };
  if (path === '/management')
    return { redirect: `/management/${lastManagementSubview}` };
  const managementMatch = /^\/management\/([^/]+)$/.exec(path);
  if (managementMatch && isManagementSubview(managementMatch[1])) {
    return { view: 'management', subview: managementMatch[1] };
  }
  return { view: 'not-found', path };
}

interface RouterValue {
  route: Route;
  path: string;
  navigate: (to: string) => void;
}

const RouterContext = createContext<RouterValue | null>(null);

export function RouterProvider({ children }: { children: ReactNode }) {
  const [path, setPath] = useState(() => window.location.pathname);
  // Last-used Management sub view — session-only presentation state
  // (GUI_DESIGN §1.1); intentionally not persisted anywhere.
  const lastManagementSubview = useRef<ManagementSubview>(
    DEFAULT_MANAGEMENT_SUBVIEW,
  );

  useEffect(() => {
    const onPopState = () => setPath(window.location.pathname);
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const resolved = resolvePath(path, lastManagementSubview.current);
  const redirectTo = 'redirect' in resolved ? resolved.redirect : null;

  const navigate = useCallback(
    (to: string) => {
      if (to === path && window.location.pathname === path) return;
      window.history.pushState({}, '', to);
      setPath(to);
    },
    [path],
  );

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
    <RouterContext.Provider value={{ route: resolved, path, navigate }}>
      {children}
    </RouterContext.Provider>
  );
}

export function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error('useRouter must be used within RouterProvider');
  return value;
}

interface LinkProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  to: string;
}

/** Anchor that keeps meaningful URLs while navigating client-side. */
export function Link({ to, onClick, children, ...rest }: LinkProps) {
  const { navigate } = useRouter();
  return (
    <a
      href={to}
      onClick={(event) => {
        onClick?.(event);
        if (
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        ) {
          return;
        }
        event.preventDefault();
        navigate(to);
      }}
      {...rest}
    >
      {children}
    </a>
  );
}
