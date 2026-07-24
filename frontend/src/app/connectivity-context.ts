import { createContext, useContext } from 'react';

export type ConnectivityStatus = 'connecting' | 'connected' | 'unavailable';

export interface ConnectivityValue {
  status: ConnectivityStatus;
  /** Explicit user-facing retry: re-runs the health check immediately. */
  retry: () => void;
}

export const ConnectivityContext = createContext<ConnectivityValue | null>(
  null,
);

export function useConnectivity(): ConnectivityValue {
  const value = useContext(ConnectivityContext);
  if (!value) {
    throw new Error('useConnectivity must be used within ConnectivityProvider');
  }
  return value;
}
