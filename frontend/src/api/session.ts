// User sign-in API (the account chip and its dialogs): the current
// user sign-in of this browser, signing in and out, and changing the
// signed-in user's own password and saved theme. Users are application accounts, never
// Workers — the Scan Station badge sign-in is a different thing with
// its own API.
//
// The sign-in itself travels as an HttpOnly cookie the browser holds;
// no response body carries a token or a credential, so this module only
// maps the wire shape. A malformed answer throws (the session UI then
// treats the state as unknown) and an unknown permission key throws
// rather than being dropped. The server stays authoritative over every
// rule; the permission list here only decides what the UI shows.
//
// Production-safe: no mock data, no framework imports.

import {
  ApiError,
  apiRequest,
  apiRequestWithStatus,
  refusalFlag,
} from './client';
import { PERMISSIONS } from './roles';
import type { Permission } from './roles';
import { writeOutcomeUnknown } from './scan-station';

export interface SessionUser {
  id: number;
  loginName: string;
  displayName: string;
  roleId: number;
  roleName: string;
  /** Avatar cache version (ISO 8601); null when there is no avatar. */
  avatarUpdatedAt: string | null;
  /** The role's permission keys, sorted by key. */
  permissions: Permission[];
  /** An administrator set the password: a new one must be chosen first. */
  mustChangePassword: boolean;
  /** When this sign-in ends (ISO 8601); null = it never expires. */
  sessionExpiresAt: string | null;
  /** Saved theme (GUI_DESIGN §2.1 User tier); null = no preference. */
  themePreference: 'dark' | 'light' | null;
}

export interface SessionState {
  /** The signed-in user; null when nobody is signed in here. */
  user: SessionUser | null;
  /** PartFlow has no administrator yet (first-run setup is available). */
  setupOpen: boolean;
}

const KNOWN_PERMISSIONS: ReadonlySet<string> = new Set(PERMISSIONS);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function malformed(): Error {
  return new Error('The server answered a malformed user sign-in state.');
}

/** Map a saved theme from the wire (`DARK` / `LIGHT`); anything else,
 * including a missing value, is malformed. */
function toTheme(wire: unknown): 'dark' | 'light' {
  if (wire === 'DARK') return 'dark';
  if (wire === 'LIGHT') return 'light';
  throw malformed();
}

function toSessionUser(wire: unknown): SessionUser {
  if (!isRecord(wire)) throw malformed();
  const {
    id,
    login_name,
    display_name,
    role_id,
    role_name,
    avatar_updated_at,
    permissions,
    must_change_password,
    session_expires_at,
    theme_preference,
  } = wire;
  if (
    typeof id !== 'number' ||
    typeof login_name !== 'string' ||
    typeof display_name !== 'string' ||
    typeof role_id !== 'number' ||
    typeof role_name !== 'string' ||
    (avatar_updated_at !== null && typeof avatar_updated_at !== 'string') ||
    !Array.isArray(permissions) ||
    typeof must_change_password !== 'boolean' ||
    (session_expires_at !== null && typeof session_expires_at !== 'string')
  ) {
    throw malformed();
  }
  return {
    id,
    loginName: login_name,
    displayName: display_name,
    roleId: role_id,
    roleName: role_name,
    avatarUpdatedAt: avatar_updated_at,
    permissions: permissions.map((key: unknown) => {
      // The server sends only known keys; anything else means this
      // client is out of date — fail loudly rather than drop a grant.
      if (typeof key !== 'string' || !KNOWN_PERMISSIONS.has(key)) {
        throw new Error(
          `Unknown permission key from the server: ${String(key)}`,
        );
      }
      return key as Permission;
    }),
    mustChangePassword: must_change_password,
    sessionExpiresAt: session_expires_at,
    themePreference:
      theme_preference === null ? null : toTheme(theme_preference),
  };
}

/** Map one `SessionStateResponse`; throws on a malformed body. */
export function toSessionState(wire: unknown): SessionState {
  if (!isRecord(wire) || typeof wire.setup_open !== 'boolean') {
    throw malformed();
  }
  if (wire.user !== null && !isRecord(wire.user)) throw malformed();
  return {
    user: wire.user === null ? null : toSessionUser(wire.user),
    setupOpen: wire.setup_open,
  };
}

const SESSION_PATH = '/api/session';

/** The user sign-in of this browser (also renews the cookie lifetime). */
export async function getSession(): Promise<SessionState> {
  return toSessionState(await apiRequest<unknown>(SESSION_PATH));
}

/** How a user with two-step sign-in proves it is them after the password. */
export const SECOND_STEP_METHODS = [
  'TOTP',
  'RECOVERY_CODE',
  'EMAIL_CODE',
] as const;

export type SecondStepMethod = (typeof SECOND_STEP_METHODS)[number];

const KNOWN_SECOND_STEP_METHODS: ReadonlySet<string> = new Set(
  SECOND_STEP_METHODS,
);

/** The open second step of a sign-in (its token is an HttpOnly cookie). */
export interface SecondStep {
  /** The ways this user may finish signing in, the app code first. */
  methods: SecondStepMethod[];
  /** The masked address an emailed code goes to; null when none can be
   * sent (no address on the account, or no outgoing mail server). */
  emailHint: string | null;
  /** When the second step ends (ISO 8601). */
  expiresAt: string;
}

/** What a password sign-in answered: signed in, or a second step. */
export type SignInResult =
  | { kind: 'signed-in'; state: SessionState }
  | { kind: 'second-step'; step: SecondStep };

function toSecondStep(wire: unknown): SecondStep {
  if (!isRecord(wire)) throw malformed();
  const { methods, email_hint, expires_at } = wire;
  if (
    !Array.isArray(methods) ||
    (email_hint !== null && typeof email_hint !== 'string') ||
    typeof expires_at !== 'string'
  ) {
    throw malformed();
  }
  return {
    methods: methods.map((method: unknown) => {
      if (
        typeof method !== 'string' ||
        !KNOWN_SECOND_STEP_METHODS.has(method)
      ) {
        throw malformed();
      }
      return method as SecondStepMethod;
    }),
    emailHint: email_hint,
    expiresAt: expires_at,
  };
}

/** Sign in; a new sign-in replaces any earlier one of this browser. A
 * user with two-step sign-in is answered with the second step instead
 * (202), which `completeSecondStep` finishes. */
export async function signIn(
  loginName: string,
  password: string,
): Promise<SignInResult> {
  const { status, data } = await apiRequestWithStatus<unknown>(SESSION_PATH, {
    method: 'POST',
    body: { login_name: loginName, password },
  });
  if (status === 202) return { kind: 'second-step', step: toSecondStep(data) };
  return { kind: 'signed-in', state: toSessionState(data) };
}

/** Finish the open second step with a code; signs in on success. */
export async function completeSecondStep(
  method: SecondStepMethod,
  code: string,
): Promise<SessionState> {
  return toSessionState(
    await apiRequest<unknown>(`${SESSION_PATH}/second-step`, {
      method: 'POST',
      body: { method, code },
    }),
  );
}

export interface SignInCodeSent {
  /** The masked address the code went to. */
  sentTo: string;
  /** Seconds before another code may be asked for. */
  resendAfterSeconds: number;
  /** Codes this sign-in may still ask for. */
  sendsLeft: number;
}

/** Email a sign-in code for the open second step. */
export async function sendSignInCode(): Promise<SignInCodeSent> {
  const wire = await apiRequest<unknown>(
    `${SESSION_PATH}/second-step/email-code`,
    { method: 'POST' },
  );
  if (
    !isRecord(wire) ||
    typeof wire.sent_to !== 'string' ||
    typeof wire.resend_after_seconds !== 'number' ||
    typeof wire.sends_left !== 'number'
  ) {
    throw malformed();
  }
  return {
    sentTo: wire.sent_to,
    resendAfterSeconds: wire.resend_after_seconds,
    sendsLeft: wire.sends_left,
  };
}

/** The second step is over (expired, too many wrong codes, or two-step
 * sign-in was turned off): the password has to be entered again. */
export function secondStepEnded(error: unknown): boolean {
  return refusalFlag(error, 'second_step_expired');
}

/** The signed-in user's own two-step sign-in. */
export interface TwoStepStatus {
  enabled: boolean;
  recoveryCodesLeft: number;
  /** The user's own email address; null = none. */
  email: string | null;
  /** A sign-in code can be emailed now (an address and a mail server). */
  emailReady: boolean;
}

/** A new authenticator app waiting for its first code. */
export interface TwoStepSetup {
  /** The secret key, for typing into the app (base32). */
  secret: string;
  /** The otpauth link apps add the account from. */
  uri: string;
  /** The QR code of the link: rows of '1' (dark) and '0' (light). */
  qr: string[];
}

const TWO_STEP_PATH = `${SESSION_PATH}/two-step`;

function toTwoStepStatus(wire: unknown): TwoStepStatus {
  if (
    !isRecord(wire) ||
    typeof wire.enabled !== 'boolean' ||
    typeof wire.recovery_codes_left !== 'number' ||
    (wire.email !== null && typeof wire.email !== 'string') ||
    typeof wire.email_ready !== 'boolean'
  ) {
    throw malformed();
  }
  return {
    enabled: wire.enabled,
    recoveryCodesLeft: wire.recovery_codes_left,
    email: wire.email,
    emailReady: wire.email_ready,
  };
}

function toCodes(wire: unknown): string[] {
  if (
    !Array.isArray(wire) ||
    !wire.every((code: unknown) => typeof code === 'string')
  ) {
    throw malformed();
  }
  return wire as string[];
}

export async function getTwoStepStatus(): Promise<TwoStepStatus> {
  return toTwoStepStatus(await apiRequest<unknown>(TWO_STEP_PATH));
}

/** Start adding an authenticator app (the password again). */
export async function beginTwoStepSetup(
  currentPassword: string,
): Promise<TwoStepSetup> {
  const wire = await apiRequest<unknown>(`${TWO_STEP_PATH}/totp`, {
    method: 'POST',
    body: { current_password: currentPassword },
  });
  if (
    !isRecord(wire) ||
    typeof wire.secret !== 'string' ||
    typeof wire.uri !== 'string'
  ) {
    throw malformed();
  }
  return { secret: wire.secret, uri: wire.uri, qr: toCodes(wire.qr) };
}

/** Confirm the new app with its first code; answers the recovery codes
 * when this turned two-step sign-in on, else null. */
export async function confirmTwoStepSetup(
  code: string,
): Promise<string[] | null> {
  const wire = await apiRequest<unknown>(`${TWO_STEP_PATH}/totp/confirm`, {
    method: 'POST',
    body: { code },
  });
  if (!isRecord(wire)) throw malformed();
  return wire.recovery_codes === null ? null : toCodes(wire.recovery_codes);
}

/** Turn two-step sign-in off (the password again). */
export async function turnOffTwoStep(
  currentPassword: string,
): Promise<TwoStepStatus> {
  return toTwoStepStatus(
    await apiRequest<unknown>(`${TWO_STEP_PATH}/totp/remove`, {
      method: 'POST',
      body: { current_password: currentPassword },
    }),
  );
}

/** Replace the recovery codes (the password again); the old ones stop
 * working. */
export async function createRecoveryCodes(
  currentPassword: string,
): Promise<string[]> {
  const wire = await apiRequest<unknown>(`${TWO_STEP_PATH}/recovery-codes`, {
    method: 'POST',
    body: { current_password: currentPassword },
  });
  if (!isRecord(wire)) throw malformed();
  return toCodes(wire.recovery_codes);
}

/** Sign out; answered the same when nobody was signed in. */
export async function signOut(): Promise<void> {
  await apiRequest<undefined>(SESSION_PATH, { method: 'DELETE' });
}

/** Change the signed-in user's own password; every other sign-in of the
 * user ends and this browser receives a new one. */
export async function changeOwnPassword(
  currentPassword: string,
  newPassword: string,
): Promise<SessionState> {
  return toSessionState(
    await apiRequest<unknown>(`${SESSION_PATH}/password`, {
      method: 'PUT',
      body: { current_password: currentPassword, new_password: newPassword },
    }),
  );
}

/** Change the signed-in user's own email address (the password again);
 * null removes it. Answers the stored address. The previous address hears
 * of the change when a mail server is set up. */
export async function changeOwnEmail(
  currentPassword: string,
  email: string | null,
): Promise<string | null> {
  const wire = await apiRequest<unknown>(`${SESSION_PATH}/email`, {
    method: 'PUT',
    body: { current_password: currentPassword, email },
  });
  if (
    !isRecord(wire) ||
    (wire.email !== null && typeof wire.email !== 'string')
  ) {
    throw malformed();
  }
  return wire.email;
}

/** Save the signed-in User's own Dark/Light preference. Not audited; an
 * absolute value, so resending is safe. An ended sign-in never opens the
 * Sign-in dialog (the caller handles it). */
export async function saveOwnThemePreference(
  theme: 'dark' | 'light',
): Promise<'dark' | 'light'> {
  const wire = await apiRequest<unknown>(`${SESSION_PATH}/theme-preference`, {
    method: 'PUT',
    body: { theme_preference: theme === 'dark' ? 'DARK' : 'LIGHT' },
    promptSignIn: false,
  });
  if (!isRecord(wire)) throw malformed();
  // The server echoes the value this request saved or kept.
  return toTheme(wire.theme_preference);
}

/**
 * The server was too busy checking other passwords: a DEFINITE refusal
 * (nothing was written or counted), even though it is a 5xx answer.
 */
export function isPasswordCheckBusy(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 503) return false;
  return (
    isRecord(error.body) &&
    (error.body as { password_check_busy?: unknown }).password_check_busy ===
      true
  );
}

/**
 * The outcome rule of every user sign-in write (sign in, setup,
 * password changes, sign-in settings): the repository's unknown-outcome
 * rule, except that a busy password check is a definite refusal.
 */
export function signInWriteOutcomeUnknown(error: unknown): boolean {
  return !isPasswordCheckBusy(error) && writeOutcomeUnknown(error);
}
