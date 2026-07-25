import { createContext, useContext } from 'react';

import type { Route } from './router-core';

/**
 * Navigation guard: returns true to allow leaving the current view.
 * The guard itself asks the user (e.g. via window.confirm), so a view
 * with unsaved changes can require explicit confirmation for top-level
 * navigation, sub-view navigation and browser back/forward alike.
 */
export type NavigationGuard = () => boolean;

export interface RouterValue {
  route: Route;
  path: string;
  navigate: (to: string) => void;
  /** Register (or clear with null) the single active navigation guard. */
  setNavigationGuard: (guard: NavigationGuard | null) => void;
}

export const RouterContext = createContext<RouterValue | null>(null);

export function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error('useRouter must be used within RouterProvider');
  return value;
}
