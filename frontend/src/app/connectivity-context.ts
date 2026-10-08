import { createContext, useContext } from 'react';

/**
 * `outdated`: the server answers, but it runs another release than the
 * one this page was built for (its health answer, or a 409
 * `release_mismatch` refusal). Writes are blocked exactly as while
 * disconnected (`status !== 'connected'`); reads continue.
 */
export type ConnectivityStatus =
  'connecting' | 'connected' | 'unavailable' | 'outdated';

export interface ConnectivityValue {
  status: ConnectivityStatus;
  /** Explicit user-facing retry: re-runs the health check immediately. */
  retry: () => void;
  /**
   * The `release` of the latest health answer that carried one (null or
   * absent while unknown — a test double may omit it).
   */
  serverRelease?: string | null;
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
