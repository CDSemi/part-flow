// Application policies API (Administration → Worker sessions,
// Correction permissions, Settings and History archival & purge,
// Phase 13; Backups, Phase 16).
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
// holds the settings of History archival & purge (the Movement-history
// retention period, the shortest allowed period, the Start minute of the
// host job, the preview time limit, the wait limit, the purge batch
// limits and the automatic runs with the standing authorization of their
// approval step; a partial merge, Phase 16), which the host job and the
// preview read. Security → User sign-in reads the user sign-in
// policy (how long a user's sign-in lasts, when failed sign-ins lock an
// account, whether an administrator-set password must be replaced) —
// readable by any signed-in user, written as a partial merge by users
// whose role may configure system settings. Backups holds how often the
// server's hourly backup task takes a scheduled backup, how many its
// rotation keeps and how many pre-purge backups it keeps — replaced as a
// whole, read by that task at its next run. Settings → Outgoing mail server and Security → Two-step sign-in
// (Phase 17) hold the mail server PartFlow sends through, with its
// timeout, and every number of two-step sign-in (a partial merge like User
// sign-in). Security → Password rules and Remember me (owner request of
// 2026-11-17) hold the rules every new password must satisfy and whether
// the sign-in dialog offers Remember me, for how long (both partial
// merges like User sign-in). The
// server validates and stays authoritative;
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

/** What decides that an automatic archival run is needed: the live
 * Movement rows, the age of the oldest retained Movement, or the size of
 * the database. */
export const ARCHIVE_MEASURES = [
  'LIVE_MOVEMENT_ROWS',
  'RETAINED_HISTORY_AGE',
  'DATABASE_SIZE',
] as const;

export type ArchiveMeasure = (typeof ARCHIVE_MEASURES)[number];

/** The standing authorization of automatic runs (while their approval
 * step is off): the audited change that switched the step off, with its
 * User as they stand now. */
export interface StandingAuthorization {
  auditEventId: number;
  /** Who switched the approval step off (null: the server names nobody). */
  authorizedBy: { userId: number; displayName: string } | null;
  authorizedAt: string | null;
  reason: string | null;
  /** That User is still active and still holds Run history archival and
   * purge (read live by the server). */
  valid: boolean;
  /** Why it is no longer valid, as the server words it; null while valid. */
  problem: string | null;
}

/** The settings of Administration → History archival & purge. */
export interface RetentionPolicy {
  /** The Movement-history retention period in whole months; null = no
   * retention period (nothing is eligible). */
  retentionPeriodMonths: number | null;
  /** The shortest retention period allowed, in whole months (changed
   * only with a reason). */
  shortestRetentionMonths: number;
  /** The minute of the hour at which the host job's hourly work starts. */
  startMinute: number;
  /** The time limit of a preview, in seconds. */
  previewSeconds: number;
  /** Days a run may wait for its approval or its off-host copy
   * confirmation (or a failed last run may stand) before the monitoring
   * line fails. */
  maxWaitDays: number;
  /** The most Movements one purge batch deletes (whole components; a
   * larger component is a batch of its own). */
  purgeBatchMovements: number;
  /** The most Part Numbers one purge batch locks. */
  purgeBatchPartNumbers: number;
  /** The host job creates runs by itself (archival design B step 4). */
  automaticRuns: boolean;
  /** What decides that an automatic run is needed (an `ArchiveMeasure`,
   * as the server answered it). */
  measure: string;
  /** Live Movement rows above which the row measure passes. */
  limitMovementRows: number;
  /** Months the oldest retained Movement must be older than the cutoff
   * by more than, for the age measure to pass. */
  limitHistoryMonths: number;
  /** Database size in GiB above which the size measure passes. */
  limitDatabaseGib: number;
  /** An automatic run waits for a person's approval (`PROPOSED`). */
  approvalRequired: boolean;
  /** Days between automatic runs, from the end of the last one. */
  intervalDays: number;
  /** Set exactly while the approval step is off. */
  standingAuthorization: StandingAuthorization | null;
  /** The policy singleton's timestamp (shared by every section). */
  updatedAt: string;
}

export type RetentionSetting = Exclude<
  keyof RetentionPolicy,
  'updatedAt' | 'standingAuthorization'
>;

/** Some settings of History archival & purge (a partial merge). */
export type RetentionPatch = Partial<Pick<RetentionPolicy, RetentionSetting>>;

/** The wire name of each setting. */
const RETENTION_WIRE: Record<RetentionSetting, string> = {
  retentionPeriodMonths: 'retention_period_months',
  shortestRetentionMonths: 'shortest_retention_months',
  startMinute: 'archive_start_minute',
  previewSeconds: 'archive_preview_seconds',
  maxWaitDays: 'archive_max_wait_days',
  purgeBatchMovements: 'archive_purge_batch_movements',
  purgeBatchPartNumbers: 'archive_purge_batch_part_numbers',
  automaticRuns: 'archive_automatic_runs',
  measure: 'archive_measure',
  limitMovementRows: 'archive_limit_movement_rows',
  limitHistoryMonths: 'archive_limit_history_months',
  limitDatabaseGib: 'archive_limit_database_gib',
  approvalRequired: 'archive_approval_required',
  intervalDays: 'archive_interval_days',
};

/** The settings that are not whole numbers (all others are). */
const RETENTION_TYPES: Partial<Record<RetentionSetting, 'boolean' | 'string'>> =
  {
    automaticRuns: 'boolean',
    measure: 'string',
    approvalRequired: 'boolean',
  };

const RETENTION_POLICY_PATH = '/api/policies/data-retention';

/** An ApiError, so the card states this reason (a plain Error reads as
 * "could not be reached", which a 200 answer was not). */
function missingSetting(name: string): ApiError {
  return new ApiError(
    200,
    `The server answered without the History archival & purge setting ${name}.`,
  );
}

interface StandingAuthorizationWire {
  audit_event_id: number;
  authorized_by: { user_id: number; display_name: string } | null;
  authorized_at: string | null;
  reason: string | null;
  valid: boolean;
  problem: string | null;
}

function toStandingAuthorization(value: unknown): StandingAuthorization | null {
  if (value === null) return null;
  if (typeof value !== 'object') {
    throw missingSetting('standing_authorization');
  }
  const wire = value as StandingAuthorizationWire;
  return {
    auditEventId: wire.audit_event_id,
    authorizedBy:
      wire.authorized_by === null
        ? null
        : {
            userId: wire.authorized_by.user_id,
            displayName: wire.authorized_by.display_name,
          },
    authorizedAt: wire.authorized_at,
    reason: wire.reason,
    valid: wire.valid,
    problem: wire.problem,
  };
}

function toRetentionPolicy(wire: Record<string, unknown>): RetentionPolicy {
  const policy: Record<string, unknown> = { updatedAt: wire.updated_at };
  for (const [setting, name] of Object.entries(RETENTION_WIRE)) {
    const value = wire[name];
    const expected = RETENTION_TYPES[setting as RetentionSetting] ?? 'number';
    const nullable = setting === 'retentionPeriodMonths';
    if (typeof value !== expected && !(nullable && value === null)) {
      throw missingSetting(name);
    }
    policy[setting] = value;
  }
  policy.standingAuthorization = toStandingAuthorization(
    wire.standing_authorization,
  );
  return policy as unknown as RetentionPolicy;
}

/** The History archival & purge settings (Administration → History
 * archival & purge). */
export async function getRetentionPolicy(): Promise<RetentionPolicy> {
  return toRetentionPolicy(
    await apiRequest<Record<string, unknown>>(RETENTION_POLICY_PATH),
  );
}

/**
 * Store ONLY the provided settings (a partial merge: every other setting
 * keeps its stored value). `retentionPeriodMonths: null` clears the
 * retention period. `reason` is sent with a change of the shortest
 * allowed period and with `confirmation`, the typed confirmation that
 * switches the approval step of automatic runs off or, while it is off,
 * renews its standing authorization; the server checks both and answers
 * its own message.
 */
export async function updateRetentionPolicy(
  patch: RetentionPatch,
  reason?: string,
  confirmation?: string,
): Promise<RetentionPolicy> {
  const body: Record<string, unknown> = {};
  for (const [setting, name] of Object.entries(RETENTION_WIRE)) {
    const value = patch[setting as RetentionSetting];
    if (value !== undefined) body[name] = value;
  }
  if (reason !== undefined) body.reason = reason;
  if (confirmation !== undefined) body.confirmation = confirmation;
  return toRetentionPolicy(
    await apiRequest<Record<string, unknown>>(RETENTION_POLICY_PATH, {
      method: 'PUT',
      body,
    }),
  );
}

export interface BackupPolicy {
  /** A scheduled backup is due this many whole hours after the newest
   * one (1, 2, 3, 4, 6, 8, 12 or 24). */
  intervalHours: number;
  /** The newest scheduled backup of each of the last N hours, days and
   * weeks (UTC) is kept. */
  keepHourly: number;
  keepDaily: number;
  keepWeekly: number;
  /** The newest N pre-purge backups (taken by the history archival job
   * before each purge) are kept. */
  keepPrePurge: number;
}

interface BackupPolicyWire {
  backup_interval_hours: number;
  backup_keep_hourly: number;
  backup_keep_daily: number;
  backup_keep_weekly: number;
  backup_keep_pre_purge: number;
  updated_at: string;
}

const BACKUP_POLICY_PATH = '/api/policies/backups';

function toBackupPolicy(wire: BackupPolicyWire): BackupPolicy {
  return {
    intervalHours: wire.backup_interval_hours,
    keepHourly: wire.backup_keep_hourly,
    keepDaily: wire.backup_keep_daily,
    keepWeekly: wire.backup_keep_weekly,
    keepPrePurge: wire.backup_keep_pre_purge,
  };
}

/** The backup policy (Administration → Backups). */
export async function getBackupPolicy(): Promise<BackupPolicy> {
  const wire = await apiRequest<BackupPolicyWire>(BACKUP_POLICY_PATH);
  return toBackupPolicy(wire);
}

/** Replace the backup policy (all five fields); an unchanged policy is a
 * server no-op. */
export async function updateBackupPolicy(
  policy: BackupPolicy,
): Promise<BackupPolicy> {
  const wire = await apiRequest<BackupPolicyWire>(BACKUP_POLICY_PATH, {
    method: 'PUT',
    body: {
      backup_interval_hours: policy.intervalHours,
      backup_keep_hourly: policy.keepHourly,
      backup_keep_daily: policy.keepDaily,
      backup_keep_weekly: policy.keepWeekly,
      backup_keep_pre_purge: policy.keepPrePurge,
    },
  });
  return toBackupPolicy(wire);
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

/** The user sign-in policy (Administration → Security → User sign-in). */
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

/** How PartFlow protects its connection to the outgoing mail server. */
export const MAIL_SECURITIES = ['STARTTLS', 'TLS', 'NONE'] as const;

export type MailSecurity = (typeof MAIL_SECURITIES)[number];

export interface MailServerPolicy {
  /** The server's host name or IP address; null = no mail server. */
  host: string | null;
  port: number;
  security: MailSecurity;
  /** The user name of the sign-in to the server; null = no sign-in. */
  username: string | null;
  /** Whether a password is stored (the password is never answered). */
  passwordSet: boolean;
  /** The address PartFlow sends from; null exactly when there is no host. */
  sender: string | null;
  /** Seconds each connect, read or write with the server may take. */
  timeoutSeconds: number;
}

interface MailServerPolicyWire {
  mail_host: string | null;
  mail_port: number;
  mail_security: string;
  mail_username: string | null;
  mail_password_set: boolean;
  mail_sender: string | null;
  mail_timeout_seconds: number;
  updated_at: string;
}

const MAIL_SERVER_POLICY_PATH = '/api/policies/mail-server';
const KNOWN_MAIL_SECURITIES: ReadonlySet<string> = new Set(MAIL_SECURITIES);

function toMailServerPolicy(wire: MailServerPolicyWire): MailServerPolicy {
  if (!KNOWN_MAIL_SECURITIES.has(wire.mail_security)) {
    throw new Error(
      `Unknown mail connection security from the server: ${wire.mail_security}`,
    );
  }
  return {
    host: wire.mail_host,
    port: wire.mail_port,
    security: wire.mail_security as MailSecurity,
    username: wire.mail_username,
    passwordSet: wire.mail_password_set,
    sender: wire.mail_sender,
    timeoutSeconds: wire.mail_timeout_seconds,
  };
}

/** The outgoing mail server (Administration → Settings). */
export async function getMailServerPolicy(): Promise<MailServerPolicy> {
  const wire = await apiRequest<MailServerPolicyWire>(MAIL_SERVER_POLICY_PATH);
  return toMailServerPolicy(wire);
}

/**
 * Replace the outgoing mail server. `password`: left out (undefined) keeps
 * the stored password, null clears it, a string replaces it. An unchanged
 * server is a server no-op; saving sends nothing.
 */
export async function updateMailServerPolicy(server: {
  host: string | null;
  port: number;
  security: MailSecurity;
  username: string | null;
  sender: string | null;
  timeoutSeconds: number;
  password?: string | null;
}): Promise<MailServerPolicy> {
  const wire = await apiRequest<MailServerPolicyWire>(MAIL_SERVER_POLICY_PATH, {
    method: 'PUT',
    body: {
      mail_host: server.host,
      mail_port: server.port,
      mail_security: server.security,
      mail_username: server.username,
      mail_sender: server.sender,
      mail_timeout_seconds: server.timeoutSeconds,
      ...(server.password !== undefined
        ? { mail_password: server.password }
        : {}),
    },
  });
  return toMailServerPolicy(wire);
}

/** Send one test message through the stored mail server; answers with the
 * address it went to. */
export async function sendTestMail(to: string): Promise<string> {
  const wire = await apiRequest<{ sent_to: string }>(
    `${MAIL_SERVER_POLICY_PATH}/test`,
    { method: 'POST', body: { to } },
  );
  return wire.sent_to;
}

/** Every number of two-step sign-in (Administration → Security → Two-step
 * sign-in; owner decisions of 2026-11-17). */
export interface TwoStepPolicy {
  /** Minutes a second step stays open after the password. */
  windowMinutes: number;
  /** Wrong codes one sign-in may take before it ends. */
  maxCodes: number;
  /** Wrong codes since the last full sign-in that lock the account. */
  lockoutCodes: number;
  /** Codes one sign-in may email. */
  emailCodes: number;
  /** Seconds between two emailed codes. */
  emailIntervalSeconds: number;
  /** Digits of an emailed code. */
  emailCodeDigits: number;
  /** Recovery codes made at a time. */
  recoveryCodes: number;
  /** Characters of a recovery code. */
  recoveryCodeLength: number;
  /** Minutes a new authenticator app waits for its first code. */
  setupMinutes: number;
  /** 30-second steps an authenticator app's clock may be early or late. */
  clockDriftSteps: number;
  /** The policy singleton's timestamp (shared by every section). */
  updatedAt: string;
}

export type TwoStepSetting = Exclude<keyof TwoStepPolicy, 'updatedAt'>;

/** The wire name of each setting. */
const TWO_STEP_WIRE: Record<TwoStepSetting, string> = {
  windowMinutes: 'two_step_window_minutes',
  maxCodes: 'two_step_max_codes',
  lockoutCodes: 'two_step_lockout_codes',
  emailCodes: 'two_step_email_codes',
  emailIntervalSeconds: 'two_step_email_interval_seconds',
  emailCodeDigits: 'two_step_email_code_digits',
  recoveryCodes: 'two_step_recovery_codes',
  recoveryCodeLength: 'two_step_recovery_code_length',
  setupMinutes: 'two_step_setup_minutes',
  clockDriftSteps: 'two_step_clock_drift_steps',
};

export const TWO_STEP_SETTINGS = Object.keys(TWO_STEP_WIRE) as TwoStepSetting[];

const TWO_STEP_POLICY_PATH = '/api/policies/two-step';

function toTwoStepPolicy(wire: Record<string, unknown>): TwoStepPolicy {
  const policy: Record<string, unknown> = { updatedAt: wire.updated_at };
  for (const setting of TWO_STEP_SETTINGS) {
    const value = wire[TWO_STEP_WIRE[setting]];
    if (typeof value !== 'number') {
      throw new Error(`Missing two-step setting from the server: ${setting}`);
    }
    policy[setting] = value;
  }
  return policy as unknown as TwoStepPolicy;
}

/** The two-step sign-in policy (Administration → Security). */
export async function getTwoStepPolicy(): Promise<TwoStepPolicy> {
  return toTwoStepPolicy(
    await apiRequest<Record<string, unknown>>(TWO_STEP_POLICY_PATH),
  );
}

/** Store ONLY the provided two-step settings (a partial merge: every other
 * setting keeps its stored value); an unchanged value is a server no-op. */
export async function updateTwoStepPolicy(
  patch: Partial<Record<TwoStepSetting, number>>,
): Promise<TwoStepPolicy> {
  const body: Record<string, number> = {};
  for (const setting of TWO_STEP_SETTINGS) {
    const value = patch[setting];
    if (value !== undefined) body[TWO_STEP_WIRE[setting]] = value;
  }
  return toTwoStepPolicy(
    await apiRequest<Record<string, unknown>>(TWO_STEP_POLICY_PATH, {
      method: 'PUT',
      body,
    }),
  );
}

/** The password rules of Administration → Security → Password rules. */
export interface PasswordRulesPolicy {
  /** Fewest characters a new password may have (4–256). */
  minLength: number;
  /** Most characters a new password may have (4–256, never below the
   * minimum). */
  maxLength: number;
  requireDigit: boolean;
  requireSpecial: boolean;
  requireUppercase: boolean;
  requireLowercase: boolean;
  /** The policy singleton's timestamp (shared by every section). */
  updatedAt: string;
}

export type PasswordRuleSetting = Exclude<
  keyof PasswordRulesPolicy,
  'updatedAt'
>;

/** The wire name of each password rule. */
const PASSWORD_RULES_WIRE: Record<PasswordRuleSetting, string> = {
  minLength: 'password_min_length',
  maxLength: 'password_max_length',
  requireDigit: 'password_require_digit',
  requireSpecial: 'password_require_special',
  requireUppercase: 'password_require_uppercase',
  requireLowercase: 'password_require_lowercase',
};

const PASSWORD_RULES_PATH = '/api/policies/password-rules';

function toPasswordRulesPolicy(
  wire: Record<string, unknown>,
): PasswordRulesPolicy {
  const policy: Record<string, unknown> = { updatedAt: wire.updated_at };
  for (const [setting, name] of Object.entries(PASSWORD_RULES_WIRE)) {
    const value = wire[name];
    const expected = setting.endsWith('Length') ? 'number' : 'boolean';
    if (typeof value !== expected) {
      throw new Error(`Missing password rule from the server: ${setting}`);
    }
    policy[setting] = value;
  }
  return policy as unknown as PasswordRulesPolicy;
}

/** The stored password rules (Administration → Security). */
export async function getPasswordRulesPolicy(): Promise<PasswordRulesPolicy> {
  return toPasswordRulesPolicy(
    await apiRequest<Record<string, unknown>>(PASSWORD_RULES_PATH),
  );
}

/** Store ONLY the provided password rules (a partial merge: every other
 * rule keeps its stored value); an unchanged value is a server no-op. */
export async function updatePasswordRulesPolicy(
  patch: Partial<Omit<PasswordRulesPolicy, 'updatedAt'>>,
): Promise<PasswordRulesPolicy> {
  const body: Record<string, number | boolean> = {};
  for (const [setting, name] of Object.entries(PASSWORD_RULES_WIRE)) {
    const value = patch[setting as PasswordRuleSetting];
    if (value !== undefined) body[name] = value;
  }
  return toPasswordRulesPolicy(
    await apiRequest<Record<string, unknown>>(PASSWORD_RULES_PATH, {
      method: 'PUT',
      body,
    }),
  );
}

/** Remember me of Administration → Security → Remember me. */
export interface RememberMePolicy {
  /** The sign-in dialog offers Remember me. */
  offered: boolean;
  /** A remembered sign-in ends after `days`; false = until sign-out. */
  expires: boolean;
  /** Whole days a remembered sign-in lasts (kept while `expires` is off). */
  days: number;
  /** The policy singleton's timestamp (shared by every section). */
  updatedAt: string;
}

interface RememberMePolicyWire {
  remember_me_offered: boolean;
  remember_me_expires: boolean;
  remember_me_days: number;
  updated_at: string;
}

const REMEMBER_ME_PATH = '/api/policies/remember-me';

function toRememberMePolicy(wire: RememberMePolicyWire): RememberMePolicy {
  return {
    offered: wire.remember_me_offered,
    expires: wire.remember_me_expires,
    days: wire.remember_me_days,
    updatedAt: wire.updated_at,
  };
}

/** Remember me as stored (Administration → Security). */
export async function getRememberMePolicy(): Promise<RememberMePolicy> {
  return toRememberMePolicy(
    await apiRequest<RememberMePolicyWire>(REMEMBER_ME_PATH),
  );
}

/** Store ONLY the provided Remember me settings (a partial merge). */
export async function updateRememberMePolicy(
  patch: Partial<Omit<RememberMePolicy, 'updatedAt'>>,
): Promise<RememberMePolicy> {
  return toRememberMePolicy(
    await apiRequest<RememberMePolicyWire>(REMEMBER_ME_PATH, {
      method: 'PUT',
      body: {
        ...(patch.offered !== undefined
          ? { remember_me_offered: patch.offered }
          : {}),
        ...(patch.expires !== undefined
          ? { remember_me_expires: patch.expires }
          : {}),
        ...(patch.days !== undefined ? { remember_me_days: patch.days } : {}),
      },
    }),
  );
}
