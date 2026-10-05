// Application policies API (Administration → Worker sessions, Phase 13).
//
// The global policy singleton the server seeds and audits. Today it
// holds the default sliding inactivity timeout of scanned Worker
// Sessions in whole minutes (1–720); per-Area overrides live on the Area
// (`api/environment.ts`). The server validates the range and stays
// authoritative; this module only maps the wire shape.
//
// Production-safe: no mock data, no framework imports.

import { apiRequest } from './client';

export interface WorkerSessionPolicy {
  /** Default Worker session timeout in whole minutes. */
  timeoutMinutes: number;
  updatedAt: string;
}

interface WorkerSessionPolicyWire {
  worker_session_timeout_minutes: number;
  updated_at: string;
}

function toWorkerSessionPolicy(
  wire: WorkerSessionPolicyWire,
): WorkerSessionPolicy {
  return {
    timeoutMinutes: wire.worker_session_timeout_minutes,
    updatedAt: wire.updated_at,
  };
}

export async function getWorkerSessionPolicy(): Promise<WorkerSessionPolicy> {
  const wire = await apiRequest<WorkerSessionPolicyWire>(
    '/api/policies/worker-sessions',
  );
  return toWorkerSessionPolicy(wire);
}

/** Store the default timeout; an unchanged value is a server no-op. */
export async function updateWorkerSessionPolicy(
  timeoutMinutes: number,
): Promise<WorkerSessionPolicy> {
  const wire = await apiRequest<WorkerSessionPolicyWire>(
    '/api/policies/worker-sessions',
    {
      method: 'PUT',
      body: { worker_session_timeout_minutes: timeoutMinutes },
    },
  );
  return toWorkerSessionPolicy(wire);
}
