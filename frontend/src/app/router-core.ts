// Minimal history-based routing for the Phase 2 shell.
//
// The Phase 2 navigation requirements are a small, fixed route table with
// meaningful URLs, working browser back/forward, and a not-found state.
// A small router over the History API covers exactly that, so no routing
// dependency is introduced (see README — Phase 2 frontend).

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
