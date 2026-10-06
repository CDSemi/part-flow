// Application policies API (Administration → Worker sessions,
// Correction permissions and Settings, Phase 13).
//
// The global policy singleton the server seeds and audits. It holds the
// default sliding inactivity timeout of scanned Worker Sessions in whole
// minutes (1–720; per-Area overrides live on the Area,
// `api/environment.ts`) and the three badge-confirmation options of the
// sensitive Scan Station actions (PROJECT_PROFILE §19; default on). The
// PUT is a partial merge — every field absent from the body keeps its
// stored value — so each writer here sends ONLY the field it changes and
// a stale read never overwrites another administrator's change. The
// singleton also holds the Correction permissions section's Undo reason
// policy (PROJECT_PROFILE §16 "require a reason when configured"; one
// global switch, default off), read and written through its own section
// endpoint, and the Settings section's Due Soon warning policy — the
// window behind every derived due countdown (GUI_DESIGN §3.12 / §9),
// replaced as a whole (its fields share a cross-field rule). The server
// validates and stays authoritative; this module only maps the wire
// shape, and refuses a Due Soon answer outside the server's ranges
// rather than letting it degrade the countdowns silently.
//
// Production-safe: no mock data, no framework imports.

import { isDueSoonPolicy } from '../views/dates';
import type { DueSoonPolicy } from '../views/dates';
import { ApiError, apiRequest } from './client';
import type { SensitiveAction } from './scan-station';

export interface WorkerSessionPolicy {
  /** Default Worker session timeout in whole minutes. */
  timeoutMinutes: number;
  /** Per sensitive action: its final gate is a required Worker badge
   * scan in Scanned-session Areas (else the final question). */
  badgeConfirmation: { done: boolean; queue: boolean; undo: boolean };
  updatedAt: string;
}

interface WorkerSessionPolicyWire {
  worker_session_timeout_minutes: number;
  badge_confirm_done: boolean;
  badge_confirm_queue: boolean;
  badge_confirm_undo: boolean;
  updated_at: string;
}

const BADGE_CONFIRMATION_FIELD: Record<
  SensitiveAction,
  'badge_confirm_done' | 'badge_confirm_queue' | 'badge_confirm_undo'
> = {
  DONE: 'badge_confirm_done',
  QUEUE: 'badge_confirm_queue',
  UNDO: 'badge_confirm_undo',
};

function toWorkerSessionPolicy(
  wire: WorkerSessionPolicyWire,
): WorkerSessionPolicy {
  return {
    timeoutMinutes: wire.worker_session_timeout_minutes,
    badgeConfirmation: {
      done: wire.badge_confirm_done,
      queue: wire.badge_confirm_queue,
      undo: wire.badge_confirm_undo,
    },
    updatedAt: wire.updated_at,
  };
}

export async function getWorkerSessionPolicy(): Promise<WorkerSessionPolicy> {
  const wire = await apiRequest<WorkerSessionPolicyWire>(
    '/api/policies/worker-sessions',
  );
  return toWorkerSessionPolicy(wire);
}

/** Store the default timeout ONLY (the badge-confirmation options are
 * not sent and stay as stored); an unchanged value is a server no-op. */
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

/** Store ONE badge-confirmation option; the timeout and the other two
 * options are not sent, so the server keeps them as stored. */
export async function updateBadgeConfirmation(
  action: SensitiveAction,
  enabled: boolean,
): Promise<WorkerSessionPolicy> {
  const wire = await apiRequest<WorkerSessionPolicyWire>(
    '/api/policies/worker-sessions',
    {
      method: 'PUT',
      body: { [BADGE_CONFIRMATION_FIELD[action]]: enabled },
    },
  );
  return toWorkerSessionPolicy(wire);
}

export interface CorrectionPermissionsPolicy {
  /** Every Undo requires a reason (enforced by the server). */
  undoReasonRequired: boolean;
  /** The policy singleton's timestamp (shared by every section). */
  updatedAt: string;
}

interface CorrectionPermissionsPolicyWire {
  undo_reason_required: boolean;
  updated_at: string;
}

function toCorrectionPermissionsPolicy(
  wire: CorrectionPermissionsPolicyWire,
): CorrectionPermissionsPolicy {
  return {
    undoReasonRequired: wire.undo_reason_required,
    updatedAt: wire.updated_at,
  };
}

export async function getCorrectionPermissionsPolicy(): Promise<CorrectionPermissionsPolicy> {
  const wire = await apiRequest<CorrectionPermissionsPolicyWire>(
    '/api/policies/correction-permissions',
  );
  return toCorrectionPermissionsPolicy(wire);
}

/** Turn the Undo reason requirement on or off; an unchanged value is a
 * server no-op. */
export async function updateUndoReasonRequired(
  required: boolean,
): Promise<CorrectionPermissionsPolicy> {
  const wire = await apiRequest<CorrectionPermissionsPolicyWire>(
    '/api/policies/correction-permissions',
    { method: 'PUT', body: { undo_reason_required: required } },
  );
  return toCorrectionPermissionsPolicy(wire);
}

interface DueSoonPolicyWire {
  due_soon_min_days: number;
  due_soon_lead_time_percent: number;
  due_soon_max_days: number;
  updated_at: string;
}

const DUE_SOON_POLICY_PATH = '/api/policies/due-soon';

function toDueSoonPolicy(wire: DueSoonPolicyWire): DueSoonPolicy {
  const policy = {
    minDays: wire.due_soon_min_days,
    leadTimePercent: wire.due_soon_lead_time_percent,
    maxDays: wire.due_soon_max_days,
  };
  if (!isDueSoonPolicy(policy)) {
    // An ApiError, so the view states this reason (a plain Error reads
    // as "could not be reached", which a 200 answer was not).
    throw new ApiError(
      200,
      'The server answered an invalid Due Soon warning policy.',
    );
  }
  return policy;
}

/** The Due Soon warning policy (Administration → Settings). */
export async function getDueSoonPolicy(): Promise<DueSoonPolicy> {
  const wire = await apiRequest<DueSoonPolicyWire>(DUE_SOON_POLICY_PATH);
  return toDueSoonPolicy(wire);
}

/** Replace the Due Soon warning policy (all three fields); an unchanged
 * policy is a server no-op. */
export async function updateDueSoonPolicy(
  policy: DueSoonPolicy,
): Promise<DueSoonPolicy> {
  const wire = await apiRequest<DueSoonPolicyWire>(DUE_SOON_POLICY_PATH, {
    method: 'PUT',
    body: {
      due_soon_min_days: policy.minDays,
      due_soon_lead_time_percent: policy.leadTimePercent,
      due_soon_max_days: policy.maxDays,
    },
  });
  return toDueSoonPolicy(wire);
}
