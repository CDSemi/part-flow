// Application policies API (Administration → Worker sessions,
// Correction permissions, Settings and History archival & purge,
// Phase 13).
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
// replaced as a whole (its fields share a cross-field rule). It also
// holds the Movement-history retention period of History archival &
// purge, stored for later archival maintenance only — nothing reads it
// to archive or purge. Settings → User sign-in reads the user sign-in
// policy (how long a user's sign-in lasts, when failed sign-ins lock an
// account, whether an administrator-set password must be replaced) —
// readable by any signed-in user, written as a partial merge by users
// whose role may configure system settings. The server validates and
// stays authoritative;
// this module only maps the wire shape, and refuses a Due Soon answer
// outside the server's ranges rather than letting it degrade the
// countdowns silently.
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

export interface RetentionPolicy {
  /** The Movement-history retention period in whole months; null = no
   * retention period. */
  retentionPeriodMonths: number | null;
  /** The policy singleton's timestamp (shared by every section). */
  updatedAt: string;
}

interface RetentionPolicyWire {
  retention_period_months: number | null;
  updated_at: string;
}

const RETENTION_POLICY_PATH = '/api/policies/data-retention';

function toRetentionPolicy(wire: RetentionPolicyWire): RetentionPolicy {
  return {
    retentionPeriodMonths: wire.retention_period_months,
    updatedAt: wire.updated_at,
  };
}

/** The Movement-history retention period (Administration → History
 * archival & purge). */
export async function getRetentionPolicy(): Promise<RetentionPolicy> {
  const wire = await apiRequest<RetentionPolicyWire>(RETENTION_POLICY_PATH);
  return toRetentionPolicy(wire);
}

/** Set the retention period in whole months, or clear it with null; an
 * unchanged value is a server no-op. */
export async function updateRetentionPolicy(
  retentionPeriodMonths: number | null,
): Promise<RetentionPolicy> {
  const wire = await apiRequest<RetentionPolicyWire>(RETENTION_POLICY_PATH, {
    method: 'PUT',
    body: { retention_period_months: retentionPeriodMonths },
  });
  return toRetentionPolicy(wire);
}

export interface SignInPolicy {
  /** User sign-ins expire after `sessionDays`; false = never expire. */
  sessionExpires: boolean;
  /** Whole days a user's sign-in lasts (kept while expiry is off). */
  sessionDays: number;
  /** Failed sign-ins before the account is locked. */
  lockoutAttempts: number;
  /** How long a lock lasts, in whole minutes. */
  lockoutMinutes: number;
  /** A password an administrator set must be replaced at sign-in. */
  requirePasswordChange: boolean;
  /** The policy singleton's timestamp (shared by every section). */
  updatedAt: string;
}

interface SignInPolicyWire {
  user_session_expires: boolean;
  user_session_days: number;
  sign_in_lockout_attempts: number;
  sign_in_lockout_minutes: number;
  require_password_change: boolean;
  updated_at: string;
}

const SIGN_IN_POLICY_PATH = '/api/policies/sign-in';

function toSignInPolicy(wire: SignInPolicyWire): SignInPolicy {
  return {
    sessionExpires: wire.user_session_expires,
    sessionDays: wire.user_session_days,
    lockoutAttempts: wire.sign_in_lockout_attempts,
    lockoutMinutes: wire.sign_in_lockout_minutes,
    requirePasswordChange: wire.require_password_change,
    updatedAt: wire.updated_at,
  };
}

/** The user sign-in policy (Administration → Settings → User sign-in). */
export async function getSignInPolicy(): Promise<SignInPolicy> {
  const wire = await apiRequest<SignInPolicyWire>(SIGN_IN_POLICY_PATH);
  return toSignInPolicy(wire);
}

/** Store ONLY the provided user sign-in settings (a partial merge: every
 * other setting keeps its stored value); an unchanged value is a server
 * no-op. */
export async function updateSignInPolicy(
  patch: Partial<Omit<SignInPolicy, 'updatedAt'>>,
): Promise<SignInPolicy> {
  const wire = await apiRequest<SignInPolicyWire>(SIGN_IN_POLICY_PATH, {
    method: 'PUT',
    body: {
      ...(patch.sessionExpires !== undefined
        ? { user_session_expires: patch.sessionExpires }
        : {}),
      ...(patch.sessionDays !== undefined
        ? { user_session_days: patch.sessionDays }
        : {}),
      ...(patch.lockoutAttempts !== undefined
        ? { sign_in_lockout_attempts: patch.lockoutAttempts }
        : {}),
      ...(patch.lockoutMinutes !== undefined
        ? { sign_in_lockout_minutes: patch.lockoutMinutes }
        : {}),
      ...(patch.requirePasswordChange !== undefined
        ? { require_password_change: patch.requirePasswordChange }
        : {}),
    },
  });
  return toSignInPolicy(wire);
}
