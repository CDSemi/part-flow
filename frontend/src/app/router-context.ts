import { createContext, useContext } from 'react';

import type { Route } from './router-core';

export interface RouterValue {
  route: Route;
  path: string;
  navigate: (to: string) => void;
}

export const RouterContext = createContext<RouterValue | null>(null);

export function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error('useRouter must be used within RouterProvider');
  return value;
}
