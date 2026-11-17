import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { ConnectivityContext } from '../app/connectivity-context';
import type { ConnectivityStatus } from '../app/connectivity-context';
import { RouterProvider } from '../app/router-provider';
import { SessionProvider } from '../app/session-provider';
import { AccountChip } from './AccountChip';

// The user account chip and its dialogs (Sign in, Change password —
// voluntary and forced — Set up PartFlow, Two-step sign-in and Email
// address) through the real session provider, against an in-memory fake
// of the /api/session and /api/setup wire contract.

const A5 =
  'Sign-in failed. Check your login name and password. If it keeps failing, ask an administrator — the account may be locked or inactive.';
const A1 =
  'You are not signed in, or your sign-in has ended. Sign in to continue.';
const P3 = 'The current password is not correct.';
const P7 =
  'This account is locked after too many failed attempts. Try again later or ask an administrator.';
const B1 = 'PartFlow is busy checking other passwords. Try again in a moment.';
const S1 = 'PartFlow already has an administrator. Sign in instead.';
const S2 =
  'The setup token is not correct. Copy the current token from the PartFlow server log.';
const UNREACHABLE = 'The PartFlow server could not be reached. Try again.';
/** The production `web` tier's own 429 copy (spec P16-S2 §4.6). */
const RATE_LIMITED =
  'Too many attempts from this computer. Wait a minute, then try again. Nothing was changed.';
const CHANGE_UNKNOWN =
  'The server did not answer — your password may or may not have been changed. If signing in with the new password fails, use the old one.';
const PASSWORD = 'correct horse battery';
const NEW_PASSWORD = 'a brand new passphrase';
const TOKEN = 'ABCD-EFGH-IJKL-MNOP';

interface WireUser {
  id: number;
  login_name: string;
  display_name: string;
  role_id: number;
  role_name: string;
  avatar_updated_at: string | null;
  permissions: string[];
  must_change_password: boolean;
  session_expires_at: string | null;
  theme_preference: 'DARK' | 'LIGHT' | null;
}

const JANE: WireUser = {
  id: 7,
  login_name: 'jdoe',
  display_name: 'Jane Doe',
  role_id: 2,
  role_name: 'Manager',
  avatar_updated_at: null,
  permissions: ['VIEW_PRODUCTION_DATA'],
  must_change_password: false,
  session_expires_at: '2026-11-05T08:00:00Z',
  theme_preference: null,
};

interface Fake {
  user: WireUser | null;
  setupOpen: boolean;
  /** login name → [password, temporary] */
  accounts: Record<string, [string, boolean]>;
  eligibleRoles: { id: number; name: string }[];
  /** Phase 17: logins with two-step sign-in on. */
  twoStep: Set<string>;
  recoveryCodes: string[];
  emailHint: string | null;
  /** The signed-in user's own address (Phase 17). */
  email: string | null;
  /** The jdoe passkeys (Phase 17); a second step offers PASSKEY with one. */
  passkeys: { id: number; name: string }[];
  /** The login whose second step is open, if any. */
  pendingStep: string | null;
  wrongCodes: number;
  emailCodeSent: boolean;
}

let fake: Fake;
/** Requests received, oldest first (method, url, parsed body). */
let requests: { method: string; url: string; body: unknown }[];
/** Per "METHOD url": the next answer is this failure instead. */
let failures: Record<string, 'network' | { status: number; body: unknown }>;
/** Per "METHOD url": the request waits for this before it is handled. */
let holds: Partial<Record<string, Promise<void>>>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

const APP_CODE = '123456';
const EMAIL_CODE = '87654321';
const NEW_CODES = Array.from(
  { length: 10 },
  (_, index) => `abcd-efgh-jk${String(index).padStart(2, '2')}`,
);
const WRONG_CODE = 'That code is not correct. Check the code and try again.';
const STEP_ENDED = 'Too many wrong codes. Sign in again with your password.';
const WRONG_CURRENT = 'The current password is not correct.';
const PASSKEY_FAILED =
  'Sign-in with a passkey failed. Use a passkey saved for PartFlow at this address, or sign in with your login name and password. If it keeps failing, ask an administrator — the account may be locked or inactive.';
/** The id the fake WebAuthn answers with (base64url of "cred-1"). */
const PASSKEY_ID = 'Y3JlZC0x';
const PASSKEY_REQUEST = {
  publicKey: {
    challenge: 'Y2hhbGxlbmdl',
    rpId: 'localhost',
    userVerification: 'required',
  },
};
const PASSKEY_CREATION = {
  publicKey: {
    rp: { name: 'PartFlow', id: 'localhost' },
    user: { id: 'Nw', name: 'jdoe', displayName: 'Jane Doe' },
    challenge: 'Y2hhbGxlbmdl',
    pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
    excludeCredentials: [],
    authenticatorSelection: {
      residentKey: 'required',
      userVerification: 'required',
    },
  },
};

function twoStepStatus(): unknown {
  const login = fake.user?.login_name ?? '';
  return {
    enabled: fake.twoStep.has(login) || fake.passkeys.length > 0,
    authenticator_app: fake.twoStep.has(login),
    passkeys: fake.passkeys.map((passkey) => ({
      ...passkey,
      rp_id: 'localhost',
      created_at: '2026-11-17T08:00:00Z',
      last_used_at: null,
    })),
    recovery_codes_left:
      fake.twoStep.has(login) || fake.passkeys.length > 0
        ? fake.recoveryCodes.length
        : 0,
    email: fake.email,
    email_ready: fake.email !== null,
  };
}

function handleTwoStep(
  url: string,
  method: string,
  body: Record<string, unknown> | undefined,
): Response | null {
  if (url === '/api/session/second-step' && method === 'POST') {
    const login = fake.pendingStep;
    if (login === null) {
      return json({ detail: STEP_ENDED, second_step_expired: true }, 401);
    }
    const code = String(body?.code);
    const ok =
      (body?.method === 'TOTP' && code === APP_CODE) ||
      (body?.method === 'RECOVERY_CODE' && fake.recoveryCodes.includes(code)) ||
      (body?.method === 'EMAIL_CODE' &&
        fake.emailCodeSent &&
        code === EMAIL_CODE);
    if (!ok) {
      fake.wrongCodes += 1;
      if (fake.wrongCodes >= 2) {
        fake.pendingStep = null;
        return json({ detail: STEP_ENDED, second_step_expired: true }, 401);
      }
      return json({ detail: WRONG_CODE, second_step_failed: true }, 401);
    }
    if (body?.method === 'RECOVERY_CODE') {
      fake.recoveryCodes = fake.recoveryCodes.filter((c) => c !== code);
    }
    fake.pendingStep = null;
    fake.user = { ...JANE, login_name: login };
    return json(sessionState());
  }
  if (url === '/api/session/second-step/passkey-options' && method === 'POST') {
    return json({ options: PASSKEY_REQUEST });
  }
  if (url === '/api/session/second-step/passkey' && method === 'POST') {
    const login = fake.pendingStep;
    if (login === null) {
      return json({ detail: STEP_ENDED, second_step_expired: true }, 401);
    }
    if ((body?.credential as { id?: string }).id !== PASSKEY_ID) {
      return json({ detail: WRONG_CODE, second_step_failed: true }, 401);
    }
    fake.pendingStep = null;
    fake.user = { ...JANE, login_name: login };
    return json(sessionState());
  }
  if (url === '/api/session/passkey/options' && method === 'POST') {
    return json({ options: PASSKEY_REQUEST });
  }
  if (url === '/api/session/passkey' && method === 'POST') {
    if ((body?.credential as { id?: string }).id !== PASSKEY_ID) {
      return json({ detail: PASSKEY_FAILED, sign_in_failed: true }, 401);
    }
    fake.user = JANE;
    return json(sessionState());
  }
  if (url === '/api/session/second-step/email-code' && method === 'POST') {
    fake.emailCodeSent = true;
    return json({
      sent_to: fake.emailHint,
      resend_after_seconds: 60,
      sends_left: 2,
    });
  }
  if (!url.startsWith('/api/session/two-step')) return null;
  const login = fake.user?.login_name ?? '';
  if (url === '/api/session/two-step' && method === 'GET') {
    return json(twoStepStatus());
  }
  if (
    body !== undefined &&
    'current_password' in body &&
    body.current_password !== fake.accounts[login]?.[0]
  ) {
    return json({ detail: WRONG_CURRENT }, 422);
  }
  if (url === '/api/session/two-step/totp' && method === 'POST') {
    return json({
      secret: 'JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP',
      uri: 'otpauth://totp/PartFlow:jdoe?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP',
      qr: ['1101', '0110', '1011', '0101'],
    });
  }
  if (url === '/api/session/two-step/totp/confirm' && method === 'POST') {
    if (body?.code !== APP_CODE) {
      return json(
        {
          detail:
            'That code does not match. Type the 6-digit code your authenticator app shows now.',
        },
        422,
      );
    }
    fake.twoStep.add(login);
    fake.recoveryCodes = [...NEW_CODES];
    return json({ recovery_codes: NEW_CODES });
  }
  if (url === '/api/session/two-step/totp/remove' && method === 'POST') {
    fake.twoStep.delete(login);
    fake.recoveryCodes = [];
    return json(twoStepStatus());
  }
  if (url === '/api/session/two-step/passkeys/options' && method === 'POST') {
    return json({ options: PASSKEY_CREATION });
  }
  if (url === '/api/session/two-step/passkeys' && method === 'POST') {
    const first = !fake.twoStep.has(login) && fake.passkeys.length === 0;
    fake.passkeys.push({
      id: fake.passkeys.length + 1,
      name: String(body?.name),
    });
    if (!first) return json({ recovery_codes: null });
    fake.recoveryCodes = [...NEW_CODES];
    return json({ recovery_codes: NEW_CODES });
  }
  const removing = /^\/api\/session\/two-step\/passkeys\/(\d+)\/remove$/.exec(
    url,
  );
  if (removing && method === 'POST') {
    fake.passkeys = fake.passkeys.filter((p) => p.id !== Number(removing[1]));
    if (!fake.twoStep.has(login) && fake.passkeys.length === 0) {
      fake.recoveryCodes = [];
    }
    return json(twoStepStatus());
  }
  if (url === '/api/session/two-step/recovery-codes' && method === 'POST') {
    fake.recoveryCodes = NEW_CODES.map((code) => code.toUpperCase());
    return json({ recovery_codes: fake.recoveryCodes });
  }
  return null;
}

function sessionState(): unknown {
  return { user: fake.user, setup_open: fake.setupOpen };
}

async function handle(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? 'GET';
  const body =
    typeof init?.body === 'string'
      ? (JSON.parse(init.body) as Record<string, unknown>)
      : undefined;
  requests.push({ method, url, body });
  const key = `${method} ${url}`;
  if (holds[key]) await holds[key];
  const failure = failures[key];
  if (failure) {
    delete failures[key];
    if (failure === 'network') throw new TypeError('Failed to fetch');
    return json(failure.body, failure.status);
  }
  if (url === '/api/health') return json({ status: 'ok' });
  if (url === '/api/session' && method === 'GET') return json(sessionState());
  if (url === '/api/session' && method === 'POST') {
    const login = String(body?.login_name).trim().toLowerCase();
    const account = fake.accounts[login];
    if (!account || account[0] !== body?.password) {
      return json({ detail: A5, sign_in_failed: true }, 401);
    }
    if (fake.twoStep.has(login)) {
      fake.pendingStep = login;
      fake.wrongCodes = 0;
      return json(
        {
          methods: [
            ...(fake.passkeys.length === 0 ? ['TOTP'] : ['PASSKEY']),
            ...(fake.recoveryCodes.length ? ['RECOVERY_CODE'] : []),
            ...(fake.emailHint !== null ? ['EMAIL_CODE'] : []),
          ],
          email_hint: fake.emailHint,
          expires_at: '2026-11-16T10:15:00Z',
        },
        202,
      );
    }
    fake.user = { ...JANE, must_change_password: account[1] };
    return json(sessionState());
  }
  if (url === '/api/session' && method === 'DELETE') {
    fake.user = null;
    return new Response(null, { status: 204 });
  }
  if (url === '/api/session/password' && method === 'PUT') {
    if (!fake.user) {
      return json({ detail: A1, authentication_required: true }, 401);
    }
    const account = fake.accounts[fake.user.login_name];
    if (account[0] !== body?.current_password) {
      return json({ detail: P3 }, 422);
    }
    fake.accounts[fake.user.login_name] = [String(body?.new_password), false];
    fake.user = { ...fake.user, must_change_password: false };
    return json(sessionState());
  }
  if (url === '/api/session/email' && method === 'PUT') {
    if (!fake.user) {
      return json({ detail: A1, authentication_required: true }, 401);
    }
    if (body?.current_password !== fake.accounts[fake.user.login_name][0]) {
      return json({ detail: P3 }, 422);
    }
    fake.email = body.email === null ? null : String(body.email);
    return json({ email: fake.email });
  }
  const twoStep = handleTwoStep(url, method, body);
  if (twoStep) return twoStep;
  if (url === '/api/setup' && method === 'GET') {
    return json({
      open: fake.setupOpen,
      eligible_roles: fake.setupOpen ? fake.eligibleRoles : [],
    });
  }
  if (url === '/api/setup/administrator' && method === 'POST') {
    if (!fake.setupOpen) {
      return json({ detail: S1, setup_closed: true }, 409);
    }
    if (body?.setup_token !== TOKEN) {
      return json({ detail: S2, setup_token_invalid: true }, 403);
    }
    fake.setupOpen = false;
    fake.user = {
      ...JANE,
      id: 1,
      login_name: String(body.login_name),
      display_name: String(body.display_name),
      role_id: Number(body.role_id),
      role_name: 'Administrator',
      permissions: ['MANAGE_USERS_AND_ROLES'],
    };
    return json(sessionState(), 201);
  }
  return json({ detail: `Unexpected ${key}` }, 404);
}

beforeEach(() => {
  window.history.replaceState({}, '', '/administration');
  fake = {
    user: null,
    setupOpen: false,
    accounts: { jdoe: [PASSWORD, false] },
    eligibleRoles: [{ id: 1, name: 'Administrator' }],
    twoStep: new Set<string>(),
    recoveryCodes: [],
    emailHint: null,
    email: null,
    passkeys: [],
    pendingStep: null,
    wrongCodes: 0,
    emailCodeSent: false,
  };
  requests = [];
  failures = {};
  holds = {};
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      handle(String(input), init),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function sent(method: string, url: string) {
  return requests.filter((r) => r.method === method && r.url === url);
}

/** A view underneath the dialogs with its own local state. */
function Draft() {
  const [value, setValue] = useState('');
  return (
    <input
      aria-label="Draft"
      value={value}
      onChange={(event) => setValue(event.target.value)}
    />
  );
}

function tree(connectivity: ConnectivityStatus) {
  return (
    <ConnectivityContext.Provider
      value={{ status: connectivity, retry: vi.fn() }}
    >
      <RouterProvider>
        <SessionProvider>
          <nav aria-label="Primary">
            <AccountChip />
          </nav>
          <Draft />
        </SessionProvider>
      </RouterProvider>
    </ConnectivityContext.Provider>
  );
}

async function renderShell(connectivity: ConnectivityStatus = 'connected') {
  const view = render(tree(connectivity));
  await waitFor(() => expect(sent('GET', '/api/session')).toHaveLength(1));
  await act(async () => {});
  return view;
}

async function signInAsJane() {
  fake.user = JANE;
  await renderShell();
  return screen.findByRole('button', { name: 'Account: Jane Doe' });
}

function openSignIn() {
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  return screen.getByRole('dialog', { name: 'Sign in' });
}

function fillSignIn(dialog: HTMLElement, login: string, password: string) {
  fireEvent.change(within(dialog).getByLabelText('Login name'), {
    target: { value: login },
  });
  fireEvent.change(within(dialog).getByLabelText('Password'), {
    target: { value: password },
  });
}

/* ============ Account chip (FC-4) ============ */

test('signed out, the chip offers Sign in — and Set up PartFlow while setup is open', async () => {
  await renderShell();
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Set up PartFlow' })).toBeNull();
  cleanup();

  requests = [];
  fake.setupOpen = true;
  await renderShell();
  const nav = screen.getByRole('navigation', { name: 'Primary' });
  expect(
    within(nav)
      .getAllByRole('button')
      .map((b) => b.textContent),
  ).toEqual(['Set up PartFlow', 'Sign in']);
});

test('signed in, the chip opens a menu with the role, Change password… and Sign out', async () => {
  const chip = await signInAsJane();
  expect(chip).toHaveAttribute('aria-haspopup', 'menu');
  expect(chip).toHaveTextContent('Jane Doe');
  expect(chip.querySelector('.worker-avatar')?.textContent).toBe('JD');

  fireEvent.click(chip);
  expect(chip).toHaveAttribute('aria-expanded', 'true');
  const menu = screen.getByRole('menu', { name: 'Account' });
  expect(screen.getByText('Manager')).toBeInTheDocument();
  expect(
    within(menu)
      .getAllByRole('menuitem')
      .map((item) => item.textContent),
  ).toEqual([
    'Change password…',
    'Two-step sign-in…',
    'Email address…',
    'Sign out',
  ]);
  expect(
    within(menu).getByRole('menuitem', { name: 'Change password…' }),
  ).toHaveFocus();

  // Escape closes the menu and returns focus to the chip.
  fireEvent.keyDown(menu, { key: 'Escape' });
  expect(screen.queryByRole('menu')).toBeNull();
  expect(chip).toHaveFocus();

  fireEvent.click(chip);
  fireEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }));
  expect(
    await screen.findByRole('button', { name: 'Sign in' }),
  ).toBeInTheDocument();
  expect(sent('DELETE', '/api/session')).toHaveLength(1);
});

test('offline, Sign out is disabled', async () => {
  fake.user = JANE;
  await renderShell('unavailable');
  fireEvent.click(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  );
  expect(screen.getByRole('menuitem', { name: 'Sign out' })).toBeDisabled();
});

/* ============ Sign in (FC-5) ============ */

test('Sign in: focus on the login name, client checks, a refusal clears only the password', async () => {
  await renderShell();
  const dialog = openSignIn();
  expect(within(dialog).getByLabelText('Login name')).toHaveFocus();
  expect(within(dialog).getByLabelText('Login name')).toHaveAttribute(
    'autocomplete',
    'username',
  );
  expect(within(dialog).getByLabelText('Password')).toHaveAttribute(
    'autocomplete',
    'current-password',
  );

  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  expect(
    within(dialog)
      .getAllByRole('alert')
      .map((a) => a.textContent),
  ).toEqual(['Enter your login name.', 'Enter your password.']);
  expect(sent('POST', '/api/session')).toHaveLength(0);

  fillSignIn(dialog, 'jdoe', 'wrong password!');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  expect((await within(dialog).findByRole('alert')).textContent).toBe(A5);
  expect(within(dialog).getByLabelText('Login name')).toHaveValue('jdoe');
  expect(within(dialog).getByLabelText('Password')).toHaveValue('');
  expect(sent('POST', '/api/session')[0].body).toEqual({
    login_name: 'jdoe',
    password: 'wrong password!',
  });
});

test('Sign in succeeds over the current view: the dialog closes and the view keeps its draft', async () => {
  await renderShell();
  fireEvent.change(screen.getByLabelText('Draft'), {
    target: { value: 'half-typed note' },
  });
  const dialog = openSignIn();
  fillSignIn(dialog, 'JDoe', PASSWORD);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));

  expect(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByLabelText('Draft')).toHaveValue('half-typed note');
  expect(window.location.pathname).toBe('/administration');
});

test('FR-31: Sign in while outdated is disabled with the update reason', async () => {
  await renderShell('outdated');
  const dialog = openSignIn();
  expect(
    within(dialog).getByText(
      'PartFlow was updated — reload the page to continue.',
    ),
  ).toBeInTheDocument();
  expect(
    within(dialog).queryByText(
      'Signing in needs the connection to the PartFlow server.',
    ),
  ).toBeNull();
  expect(
    within(dialog).getByRole('button', { name: 'Sign in' }),
  ).toBeDisabled();
});

test('Sign in offline is disabled with a note; Escape closes, but not while signing in', async () => {
  await renderShell('unavailable');
  let dialog = openSignIn();
  expect(
    within(dialog).getByText(
      'Signing in needs the connection to the PartFlow server.',
    ),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole('button', { name: 'Sign in' }),
  ).toBeDisabled();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  cleanup();

  requests = [];
  await renderShell();
  let release = () => {};
  holds['POST /api/session'] = new Promise<void>((resolve) => {
    release = resolve;
  });
  dialog = openSignIn();
  fillSignIn(dialog, 'jdoe', PASSWORD);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  await waitFor(() => expect(sent('POST', '/api/session')).toHaveLength(1));
  expect(
    within(dialog).getByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeDisabled();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  fireEvent.mouseDown(dialog.parentElement as HTMLElement);
  expect(screen.getByRole('dialog', { name: 'Sign in' })).toBe(dialog);
  release();
  expect(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  ).toBeInTheDocument();
});

test('Sign in: a busy check is a definite refusal; no answer re-reads the sign-in once', async () => {
  await renderShell();
  const dialog = openSignIn();
  failures['POST /api/session'] = {
    status: 503,
    body: { detail: B1, password_check_busy: true },
  };
  fillSignIn(dialog, 'jdoe', PASSWORD);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  expect((await within(dialog).findByRole('alert')).textContent).toBe(B1);
  expect(sent('GET', '/api/session')).toHaveLength(1);
  expect(within(dialog).getByRole('button', { name: 'Sign in' })).toBeEnabled();

  // The sign-in commits, the answer is lost: the re-read shows it.
  vi.mocked(fetch).mockImplementationOnce(async (input, init) => {
    await handle(String(input), init);
    throw new TypeError('Failed to fetch');
  });
  fillSignIn(dialog, 'jdoe', PASSWORD);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  expect(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  ).toBeInTheDocument();
  expect(sent('GET', '/api/session')).toHaveLength(2);
  expect(screen.queryByRole('dialog')).toBeNull();
});

test('FA-1: Sign in: the web rate limit (429) is a definite refusal shown as sent', async () => {
  await renderShell();
  const dialog = openSignIn();
  failures['POST /api/session'] = {
    status: 429,
    body: { detail: RATE_LIMITED, rate_limited: true },
  };
  fillSignIn(dialog, 'jdoe', PASSWORD);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    RATE_LIMITED,
  );
  expect(screen.getByRole('dialog', { name: 'Sign in' })).toBe(dialog);
  expect(within(dialog).getByRole('button', { name: 'Sign in' })).toBeEnabled();
  expect(within(dialog).queryByText(UNREACHABLE)).toBeNull();
  // A definite refusal never re-reads the sign-in.
  expect(sent('POST', '/api/session')).toHaveLength(1);
  expect(sent('GET', '/api/session')).toHaveLength(1);
});

test('Sign in: no answer and still signed out keeps the dialog with the unreachable text', async () => {
  await renderShell();
  const dialog = openSignIn();
  failures['POST /api/session'] = 'network';
  fillSignIn(dialog, 'jdoe', PASSWORD);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    UNREACHABLE,
  );
  await waitFor(() => expect(sent('GET', '/api/session')).toHaveLength(2));
  expect(sent('POST', '/api/session')).toHaveLength(1);
  await waitFor(() =>
    expect(
      within(dialog).getByRole('button', { name: 'Sign in' }),
    ).toBeEnabled(),
  );
});

/* ============ Change password (FC-6) ============ */

function fillChange(
  dialog: HTMLElement,
  current: string,
  next: string,
  repeat = next,
) {
  fireEvent.change(within(dialog).getByLabelText('Current password'), {
    target: { value: current },
  });
  fireEvent.change(within(dialog).getByLabelText('New password'), {
    target: { value: next },
  });
  fireEvent.change(within(dialog).getByLabelText('Repeat new password'), {
    target: { value: repeat },
  });
}

test('a temporary password opens the forced dialog: no Cancel, Escape ignored, Sign out offered', async () => {
  fake.accounts.jdoe = [PASSWORD, true];
  await renderShell();
  const signIn = openSignIn();
  fillSignIn(signIn, 'jdoe', PASSWORD);
  fireEvent.click(within(signIn).getByRole('button', { name: 'Sign in' }));

  const dialog = await screen.findByRole('dialog', {
    name: 'Choose a new password',
  });
  expect(
    within(dialog).getByText(
      'An administrator set your password. Choose a new one to continue.',
    ),
  ).toBeInTheDocument();
  expect(
    within(dialog).queryByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeNull();
  expect(
    within(dialog).getByRole('button', { name: 'Sign out' }),
  ).toBeInTheDocument();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  fireEvent.mouseDown(dialog.parentElement as HTMLElement);
  expect(screen.getByRole('dialog')).toBe(dialog);

  // Client checks: nothing is sent.
  fillChange(dialog, PASSWORD, 'short');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Change password' }),
  );
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'At least 6 characters.',
  );
  fillChange(dialog, PASSWORD, NEW_PASSWORD, `${NEW_PASSWORD}!`);
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Change password' }),
  );
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'The new passwords do not match.',
  );
  expect(sent('PUT', '/api/session/password')).toHaveLength(0);

  fillChange(dialog, PASSWORD, NEW_PASSWORD);
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Change password' }),
  );
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Your password was changed.',
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(sent('PUT', '/api/session/password')[0].body).toEqual({
    current_password: PASSWORD,
    new_password: NEW_PASSWORD,
  });
});

test('the forced dialog signs out', async () => {
  fake.user = { ...JANE, must_change_password: true };
  await renderShell();
  const dialog = await screen.findByRole('dialog', {
    name: 'Choose a new password',
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign out' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument();
  expect(sent('DELETE', '/api/session')).toHaveLength(1);
});

test('FR-27: the forced dialog while outdated offers its own Reload page (the notice is behind it) and reloads once', async () => {
  const reload = vi.fn();
  const location = Object.getOwnPropertyDescriptor(window, 'location')!;
  const real = window.location;
  Object.defineProperty(window, 'location', {
    configurable: true,
    get: () => ({
      reload,
      get pathname() {
        return real.pathname;
      },
      get search() {
        return real.search;
      },
      get href() {
        return real.href;
      },
    }),
  });
  try {
    fake.user = { ...JANE, must_change_password: true };
    await renderShell('connected');
    let dialog = await screen.findByRole('dialog', {
      name: 'Choose a new password',
    });
    expect(
      within(dialog).queryByRole('button', { name: 'Reload page' }),
    ).toBeNull();
    cleanup();

    requests = [];
    await renderShell('outdated');
    dialog = await screen.findByRole('dialog', {
      name: 'Choose a new password',
    });
    expect(
      within(dialog).getByText(
        'PartFlow was updated — reload the page to continue.',
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: 'Sign out' }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole('button', { name: 'Change password' }),
    ).toBeDisabled();
    const button = within(dialog).getByRole('button', { name: 'Reload page' });
    expect(button).toBeEnabled();
    expect(within(dialog).getByLabelText('Current password')).toHaveFocus();
    fireEvent.click(button);
    expect(reload).toHaveBeenCalledTimes(1);
  } finally {
    Object.defineProperty(window, 'location', location);
  }
});

test('the voluntary dialog offers no Reload page while outdated: it can be cancelled', async () => {
  fake.user = JANE;
  await renderShell('outdated');
  fireEvent.click(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  );
  fireEvent.click(screen.getByRole('menuitem', { name: 'Change password…' }));
  const dialog = screen.getByRole('dialog', { name: 'Change password' });
  expect(
    within(dialog).queryByRole('button', { name: 'Reload page' }),
  ).toBeNull();
  expect(
    within(dialog).getByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeEnabled();
});

test('the voluntary dialog cancels; server refusals (P3, P7, B1) show in place', async () => {
  const chip = await signInAsJane();
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole('menuitem', { name: 'Change password…' }));
  let dialog = screen.getByRole('dialog', { name: 'Change password' });
  expect(within(dialog).getByLabelText('Current password')).toHaveFocus();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(chip).toHaveFocus();

  fireEvent.click(chip);
  fireEvent.click(screen.getByRole('menuitem', { name: 'Change password…' }));
  dialog = screen.getByRole('dialog', { name: 'Change password' });
  fillChange(dialog, 'not my password', NEW_PASSWORD);
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Change password' }),
  );
  expect((await within(dialog).findByRole('alert')).textContent).toBe(P3);
  expect(within(dialog).getByLabelText('Current password')).toHaveValue('');
  expect(within(dialog).getByLabelText('New password')).toHaveValue(
    NEW_PASSWORD,
  );

  for (const [failure, text] of [
    [{ status: 409, body: { detail: P7, account_locked: true } }, P7],
    [{ status: 503, body: { detail: B1, password_check_busy: true } }, B1],
  ] as const) {
    failures['PUT /api/session/password'] = failure;
    fillChange(dialog, PASSWORD, NEW_PASSWORD);
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Change password' }),
    );
    await waitFor(() =>
      expect(within(dialog).getByRole('alert').textContent).toBe(text),
    );
    expect(
      within(dialog).getByRole('button', { name: 'Change password' }),
    ).toBeEnabled();
  }
  // A definite refusal never re-reads the sign-in.
  expect(sent('GET', '/api/session')).toHaveLength(1);
});

test('the voluntary dialog closes unchanged at once; typed input asks Discard password change? first', async () => {
  const chip = await signInAsJane();
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole('menuitem', { name: 'Change password…' }));
  let dialog = screen.getByRole('dialog', { name: 'Change password' });
  // Typed and removed again is unchanged: Escape closes at once.
  const current = within(dialog).getByLabelText('Current password');
  fireEvent.change(current, { target: { value: PASSWORD } });
  fireEvent.change(current, { target: { value: '' } });
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();

  fireEvent.click(chip);
  fireEvent.click(screen.getByRole('menuitem', { name: 'Change password…' }));
  dialog = screen.getByRole('dialog', { name: 'Change password' });
  fireEvent.change(within(dialog).getByLabelText('New password'), {
    target: { value: NEW_PASSWORD },
  });
  fireEvent.keyDown(dialog, { key: 'Escape' });
  let ask = screen.getByRole('dialog', { name: 'Discard password change?' });
  expect(ask).toHaveTextContent('Your entered information will not be saved.');
  expect(screen.getByRole('dialog', { name: 'Change password' })).toBe(dialog);
  fireEvent.click(within(ask).getByRole('button', { name: 'Keep editing' }));
  expect(
    screen.queryByRole('dialog', { name: 'Discard password change?' }),
  ).toBeNull();
  expect(within(dialog).getByLabelText('New password')).toHaveValue(
    NEW_PASSWORD,
  );

  // The backdrop and Cancel (Esc) ask the same.
  fireEvent.mouseDown(dialog.parentElement as HTMLElement);
  ask = screen.getByRole('dialog', { name: 'Discard password change?' });
  fireEvent.click(within(ask).getByRole('button', { name: 'Keep editing' }));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  ask = screen.getByRole('dialog', { name: 'Discard password change?' });
  fireEvent.click(within(ask).getByRole('button', { name: 'Discard input' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(sent('PUT', '/api/session/password')).toHaveLength(0);
});

test('an unanswered change is never resent: signed out afterwards opens Sign in with the same text', async () => {
  const chip = await signInAsJane();
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole('menuitem', { name: 'Change password…' }));
  const dialog = screen.getByRole('dialog', { name: 'Change password' });
  // The change commits (the sign-in ends); the answer is lost.
  vi.mocked(fetch).mockImplementationOnce(async (input, init) => {
    await handle(String(input), init);
    fake.user = null;
    throw new TypeError('Failed to fetch');
  });
  fillChange(dialog, PASSWORD, NEW_PASSWORD);
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Change password' }),
  );

  const signIn = await screen.findByRole('dialog', { name: 'Sign in' });
  expect(within(signIn).getByRole('status')).toHaveTextContent(CHANGE_UNKNOWN);
  expect(screen.queryByRole('dialog', { name: 'Change password' })).toBeNull();
  expect(sent('PUT', '/api/session/password')).toHaveLength(1);
  expect(sent('GET', '/api/session')).toHaveLength(2);
});

test('an unanswered change that did not commit re-enables the submit after the re-read', async () => {
  const chip = await signInAsJane();
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole('menuitem', { name: 'Change password…' }));
  const dialog = screen.getByRole('dialog', { name: 'Change password' });
  let releaseRead = () => {};
  holds['GET /api/session'] = new Promise<void>((resolve) => {
    releaseRead = resolve;
  });
  failures['PUT /api/session/password'] = 'network';
  fillChange(dialog, PASSWORD, NEW_PASSWORD);
  const submit = within(dialog).getByRole('button', {
    name: 'Change password',
  });
  fireEvent.click(submit);

  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    CHANGE_UNKNOWN,
  );
  for (const label of [
    'Current password',
    'New password',
    'Repeat new password',
  ]) {
    expect(within(dialog).getByLabelText(label)).toHaveValue('');
  }
  expect(submit).toBeDisabled();
  await waitFor(() => expect(sent('GET', '/api/session')).toHaveLength(2));
  releaseRead();
  await waitFor(() => expect(submit).toBeEnabled());
  expect(screen.getByRole('dialog', { name: 'Change password' })).toBe(dialog);
  expect(sent('PUT', '/api/session/password')).toHaveLength(1);
});

test('an unanswered change whose re-read also fails is re-read once the connection is regained', async () => {
  fake.user = JANE;
  const shell = await renderShell();
  fireEvent.click(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  );
  fireEvent.click(screen.getByRole('menuitem', { name: 'Change password…' }));
  const dialog = screen.getByRole('dialog', { name: 'Change password' });
  failures['PUT /api/session/password'] = 'network';
  failures['GET /api/session'] = 'network';
  fillChange(dialog, PASSWORD, NEW_PASSWORD);
  const submit = within(dialog).getByRole('button', {
    name: 'Change password',
  });
  fireEvent.click(submit);

  expect((await within(dialog).findByRole('alert')).textContent).toBe(
    CHANGE_UNKNOWN,
  );
  const reads = sent('GET', '/api/session').length;
  await act(async () => {});
  expect(submit).toBeDisabled();

  // The connection drops and comes back: the sign-in is read again.
  shell.rerender(tree('unavailable'));
  await act(async () => {});
  expect(sent('GET', '/api/session')).toHaveLength(reads);
  shell.rerender(tree('connected'));
  await waitFor(() => expect(submit).toBeEnabled());
  expect(sent('GET', '/api/session')).toHaveLength(reads + 1);
  expect(screen.getByRole('dialog', { name: 'Change password' })).toBe(dialog);
  expect(sent('PUT', '/api/session/password')).toHaveLength(1);
});

/* ============ Set up PartFlow (FC-7) ============ */

function openSetup() {
  fireEvent.click(screen.getByRole('button', { name: 'Set up PartFlow' }));
  return screen.getByRole('dialog', { name: 'Set up PartFlow' });
}

function fillSetup(dialog: HTMLElement, token = TOKEN) {
  for (const [label, value] of [
    ['Setup token', token],
    ['Name', 'Ada Admin'],
    ['Login name', 'Admin'],
    ['Password', PASSWORD],
    ['Repeat password', PASSWORD],
  ]) {
    fireEvent.change(within(dialog).getByLabelText(label), {
      target: { value },
    });
  }
}

test('Set up PartFlow: one eligible role is preselected; a wrong token keeps every entry; success signs in', async () => {
  fake.setupOpen = true;
  await renderShell();
  const dialog = openSetup();
  const token = await within(dialog).findByLabelText('Setup token');
  expect(token).toHaveFocus();
  expect(
    within(dialog).getByText(
      'PartFlow has no administrator yet. Enter the setup token from the PartFlow server log, then create the first administrator account.',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).getByLabelText('Role')).toHaveValue('1');

  fillSetup(dialog, 'WRONG-TOKEN');
  expect(within(dialog).getByText('admin')).toBeInTheDocument(); // Saved as:
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Create administrator' }),
  );
  expect((await within(dialog).findByRole('alert')).textContent).toBe(S2);
  expect(within(dialog).getByLabelText('Setup token')).toHaveValue(
    'WRONG-TOKEN',
  );
  expect(within(dialog).getByLabelText('Password')).toHaveValue(PASSWORD);

  fireEvent.change(within(dialog).getByLabelText('Setup token'), {
    target: { value: TOKEN },
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Create administrator' }),
  );
  expect(await screen.findByRole('status')).toHaveTextContent(
    'PartFlow is set up. You are signed in as Ada Admin.',
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(
    screen.getByRole('button', { name: 'Account: Ada Admin' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Set up PartFlow' })).toBeNull();
  expect(sent('POST', '/api/setup/administrator').at(-1)?.body).toEqual({
    setup_token: TOKEN,
    login_name: 'admin',
    display_name: 'Ada Admin',
    role_id: 1,
    password: PASSWORD,
  });
});

test('Set up PartFlow answers that setup is closed — on open and after a submit', async () => {
  // Closed by the time the dialog opens (the chip still offered it).
  fake.setupOpen = true;
  await renderShell();
  fake.setupOpen = false;
  let dialog = openSetup();
  expect(await within(dialog).findByText(S1)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  expect(screen.getByRole('dialog', { name: 'Sign in' })).toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.queryByRole('button', { name: 'Set up PartFlow' }),
    ).toBeNull(),
  );
  cleanup();

  // Closed between opening and submitting.
  requests = [];
  fake.setupOpen = true;
  await renderShell();
  dialog = openSetup();
  await within(dialog).findByLabelText('Setup token');
  fillSetup(dialog);
  fake.setupOpen = false;
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Create administrator' }),
  );
  expect(await within(dialog).findByText(S1)).toBeInTheDocument();
  expect(
    within(dialog).getByRole('button', { name: 'Sign in' }),
  ).toBeInTheDocument();
});

test('Set up PartFlow: no eligible role or offline disables creating; Cancel sends nothing', async () => {
  fake.setupOpen = true;
  fake.eligibleRoles = [];
  await renderShell();
  let dialog = openSetup();
  expect(
    await within(dialog).findByText(
      'No role can administer PartFlow. A role must be allowed to manage users and roles and correction permissions.',
    ),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole('button', { name: 'Create administrator' }),
  ).toBeDisabled();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  cleanup();

  requests = [];
  fake.eligibleRoles = [
    { id: 1, name: 'Administrator' },
    { id: 4, name: 'Owner' },
  ];
  await renderShell('unavailable');
  dialog = openSetup();
  await within(dialog).findByLabelText('Setup token');
  // Two eligible roles: nothing is preselected.
  expect(within(dialog).getByLabelText('Role')).toHaveValue('');
  fillSetup(dialog);
  expect(
    within(dialog).getByRole('button', { name: 'Create administrator' }),
  ).toBeDisabled();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  // The entered input asks first; discarding it closes the dialog.
  fireEvent.click(
    within(screen.getByRole('dialog', { name: 'Discard setup?' })).getByRole(
      'button',
      { name: 'Discard input' },
    ),
  );
  expect(screen.queryByRole('dialog')).toBeNull();
  dialog = openSetup();
  await within(dialog).findByLabelText('Setup token');
  fireEvent.mouseDown(dialog.parentElement as HTMLElement);
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(sent('POST', '/api/setup/administrator')).toHaveLength(0);
});

test('Set up PartFlow ignores Cancel, Escape and the backdrop while creating', async () => {
  fake.setupOpen = true;
  await renderShell();
  const dialog = openSetup();
  await within(dialog).findByLabelText('Setup token');
  fillSetup(dialog);
  let release = () => {};
  holds['POST /api/setup/administrator'] = new Promise<void>((resolve) => {
    release = resolve;
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Create administrator' }),
  );
  await waitFor(() =>
    expect(sent('POST', '/api/setup/administrator')).toHaveLength(1),
  );
  expect(
    within(dialog).getByRole('button', { name: 'Cancel (Esc)' }),
  ).toBeDisabled();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  fireEvent.mouseDown(dialog.parentElement as HTMLElement);
  expect(screen.getByRole('dialog', { name: 'Set up PartFlow' })).toBe(dialog);
  release();
  expect(
    await screen.findByRole('button', { name: 'Account: Ada Admin' }),
  ).toBeInTheDocument();
});

test('Set up PartFlow closes unchanged at once; entered input asks Discard setup? first', async () => {
  fake.setupOpen = true;
  await renderShell();
  let dialog = openSetup();
  // The one eligible role is preselected: that is the opening form.
  await within(dialog).findByLabelText('Setup token');
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();

  dialog = openSetup();
  const token = await within(dialog).findByLabelText('Setup token');
  fireEvent.change(token, { target: { value: TOKEN } });
  fireEvent.keyDown(dialog, { key: 'Escape' });
  let ask = screen.getByRole('dialog', { name: 'Discard setup?' });
  expect(ask).toHaveTextContent('Your entered information will not be saved.');
  expect(screen.getByRole('dialog', { name: 'Set up PartFlow' })).toBe(dialog);
  fireEvent.click(within(ask).getByRole('button', { name: 'Keep editing' }));
  expect(screen.queryByRole('dialog', { name: 'Discard setup?' })).toBeNull();
  expect(within(dialog).getByLabelText('Setup token')).toHaveValue(TOKEN);

  // The backdrop and Cancel (Esc) ask the same.
  fireEvent.mouseDown(dialog.parentElement as HTMLElement);
  ask = screen.getByRole('dialog', { name: 'Discard setup?' });
  fireEvent.click(within(ask).getByRole('button', { name: 'Keep editing' }));
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  ask = screen.getByRole('dialog', { name: 'Discard setup?' });
  fireEvent.click(within(ask).getByRole('button', { name: 'Discard input' }));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(sent('POST', '/api/setup/administrator')).toHaveLength(0);
});

/* ============ Two-step sign-in (Phase 17) ============ */

async function passwordStep() {
  await renderShell();
  const dialog = openSignIn();
  fillSignIn(dialog, 'jdoe', PASSWORD);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  await within(dialog).findByLabelText('Code from your authenticator app');
  return dialog;
}

function typeCode(dialog: HTMLElement, label: string, code: string) {
  fireEvent.change(within(dialog).getByLabelText(label), {
    target: { value: code },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
}

test('two-step: after the password, the app code signs in; a wrong code stays in the second step', async () => {
  fake.twoStep.add('jdoe');
  fake.recoveryCodes = [...NEW_CODES];
  const dialog = await passwordStep();
  const field = within(dialog).getByLabelText(
    'Code from your authenticator app',
  );
  expect(field).toHaveFocus();
  expect(field).toHaveAttribute('autocomplete', 'one-time-code');
  expect(within(dialog).queryByLabelText('Password')).toBeNull();
  expect(
    within(dialog).queryByRole('button', { name: /^Email me/ }),
  ).toBeNull();

  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'Enter the 6-digit code your authenticator app shows.',
  );
  expect(sent('POST', '/api/session/second-step')).toHaveLength(0);

  typeCode(dialog, 'Code from your authenticator app', '000000');
  expect(await within(dialog).findByText(WRONG_CODE)).toBeInTheDocument();
  expect(
    within(dialog).getByLabelText('Code from your authenticator app'),
  ).toHaveValue('');

  typeCode(dialog, 'Code from your authenticator app', APP_CODE);
  expect(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('dialog', { name: 'Sign in' })).toBeNull();
  expect(sent('POST', '/api/session/second-step').at(-1)?.body).toEqual({
    method: 'TOTP',
    code: APP_CODE,
  });
});

test('two-step: a recovery code signs in instead', async () => {
  fake.twoStep.add('jdoe');
  fake.recoveryCodes = [...NEW_CODES];
  const dialog = await passwordStep();
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Use a recovery code' }),
  );
  typeCode(dialog, 'Recovery code', NEW_CODES[3]);
  expect(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  ).toBeInTheDocument();
  expect(sent('POST', '/api/session/second-step').at(-1)?.body).toEqual({
    method: 'RECOVERY_CODE',
    code: NEW_CODES[3],
  });
});

test('two-step: Email me a code names the masked address and its code signs in', async () => {
  fake.twoStep.add('jdoe');
  fake.emailHint = 'j•••@example.com';
  const dialog = await passwordStep();
  expect(
    within(dialog).queryByRole('button', { name: 'Use a recovery code' }),
  ).toBeNull();
  fireEvent.click(
    within(dialog).getByRole('button', {
      name: 'Email me a code at j•••@example.com',
    }),
  );
  expect(
    await within(dialog).findByText(
      'PartFlow sent a code to j•••@example.com.',
    ),
  ).toBeInTheDocument();
  expect(within(dialog).getByLabelText('Code from the email')).toHaveFocus();
  expect(
    within(dialog).getByRole('button', { name: 'Email me another code' }),
  ).toBeInTheDocument();
  typeCode(dialog, 'Code from the email', EMAIL_CODE);
  expect(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  ).toBeInTheDocument();
  expect(sent('POST', '/api/session/second-step/email-code')).toHaveLength(1);
});

test('two-step: an ended second step returns to the password with the reason', async () => {
  fake.twoStep.add('jdoe');
  const dialog = await passwordStep();
  typeCode(dialog, 'Code from your authenticator app', '000000');
  await within(dialog).findByText(WRONG_CODE);
  typeCode(dialog, 'Code from your authenticator app', '000001');
  expect(await within(dialog).findByText(STEP_ENDED)).toBeInTheDocument();
  expect(within(dialog).getByLabelText('Login name')).toHaveValue('jdoe');
  expect(within(dialog).getByLabelText('Password')).toHaveValue('');
  expect(
    within(dialog).queryByLabelText('Code from your authenticator app'),
  ).toBeNull();
});

async function openTwoStep() {
  fireEvent.click(await signInAsJane());
  fireEvent.click(screen.getByRole('menuitem', { name: 'Two-step sign-in…' }));
  const dialog = screen.getByRole('dialog', { name: 'Two-step sign-in' });
  await waitFor(() =>
    expect(within(dialog).queryByText('Loading…')).toBeNull(),
  );
  return dialog;
}

function enterPassword(
  dialog: HTMLElement,
  button: string,
  password = PASSWORD,
) {
  fireEvent.change(within(dialog).getByLabelText('Password'), {
    target: { value: password },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: button }));
}

test('Two-step sign-in…: setting up asks for the password, shows the code and key, and turns on with the first code', async () => {
  const dialog = await openTwoStep();
  expect(dialog).toHaveTextContent('Two-step sign-in is off.');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Set up…' }));
  enterPassword(dialog, 'Continue', 'not my password');
  expect(await within(dialog).findByText(WRONG_CURRENT)).toBeInTheDocument();
  enterPassword(dialog, 'Continue');

  expect(
    await within(dialog).findByRole('img', {
      name: 'QR code for your authenticator app',
    }),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText('JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP'),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole('link', {
      name: 'Open in an authenticator app on this device',
    }),
  ).toHaveAttribute(
    'href',
    'otpauth://totp/PartFlow:jdoe?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP',
  );
  fireEvent.change(
    within(dialog).getByLabelText('Code from your authenticator app'),
    { target: { value: APP_CODE } },
  );
  fireEvent.click(within(dialog).getByRole('button', { name: 'Turn on' }));

  const list = await within(dialog).findByRole('list', {
    name: 'Recovery codes',
  });
  expect(within(list).getAllByRole('listitem')).toHaveLength(10);
  expect(dialog).toHaveTextContent('Two-step sign-in is on.');
  fireEvent.click(within(dialog).getByRole('button', { name: 'I saved them' }));
  expect(
    await within(dialog).findByText(
      'You have 10 recovery codes left. Each works once if you lose your app and passkeys.',
    ),
  ).toBeInTheDocument();
  expect(sent('POST', '/api/session/two-step/totp')[1].body).toEqual({
    current_password: PASSWORD,
  });
});

test('Two-step sign-in…: on, new recovery codes and turning off each ask for the password', async () => {
  fake.twoStep.add('jdoe');
  fake.recoveryCodes = [...NEW_CODES];
  const dialog = await openTwoStep();
  expect(dialog).toHaveTextContent(
    'Your account has no email address, so PartFlow cannot email you a sign-in code.',
  );

  fireEvent.click(
    within(dialog).getByRole('button', { name: 'New recovery codes…' }),
  );
  expect(dialog).toHaveTextContent(
    'Your current recovery codes stop working when the new ones are made.',
  );
  enterPassword(dialog, 'Create new codes');
  expect(await within(dialog).findByText('ABCD-EFGH-JK22')).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: 'I saved them' }));

  fireEvent.click(
    await within(dialog).findByRole('button', {
      name: 'Remove the authenticator app…',
    }),
  );
  expect(dialog).toHaveTextContent(
    'Signing in will take only your password, and your recovery codes are deleted.',
  );
  enterPassword(dialog, 'Remove app');
  expect(
    await within(dialog).findByText(/^Two-step sign-in is off\./),
  ).toBeInTheDocument();
  expect(sent('POST', '/api/session/two-step/totp/remove')[0].body).toEqual({
    current_password: PASSWORD,
  });
});

test('Two-step sign-in…: offline, nothing can be changed', async () => {
  fake.user = JANE;
  await renderShell('unavailable');
  fireEvent.click(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  );
  fireEvent.click(screen.getByRole('menuitem', { name: 'Two-step sign-in…' }));
  const dialog = screen.getByRole('dialog', { name: 'Two-step sign-in' });
  expect(
    await within(dialog).findByRole('button', { name: 'Set up…' }),
  ).toBeDisabled();
});

/* ============ Email address… (Phase 17) ============ */

const EMAIL_ERROR =
  'Enter an email address such as name@example.com: no spaces, a domain with a dot after the @, and at most 254 characters.';

async function openEmail() {
  fireEvent.click(await signInAsJane());
  fireEvent.click(screen.getByRole('menuitem', { name: 'Email address…' }));
  const dialog = screen.getByRole('dialog', { name: 'Email address' });
  await waitFor(() =>
    expect(within(dialog).queryByText('Loading…')).toBeNull(),
  );
  return dialog;
}

test('Email address…: the password again saves the canonical address', async () => {
  const dialog = await openEmail();
  const field = within(dialog).getByLabelText('Email address');
  expect(field).toHaveValue('');
  expect(field).toHaveFocus();

  // Checked here first: a malformed address never costs a password try.
  fireEvent.change(field, { target: { value: 'jane@' } });
  fireEvent.change(within(dialog).getByLabelText('Password'), {
    target: { value: PASSWORD },
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Save email address' }),
  );
  expect(within(dialog).getByRole('alert')).toHaveTextContent(EMAIL_ERROR);
  expect(sent('PUT', '/api/session/email')).toHaveLength(0);

  fireEvent.change(field, { target: { value: ' Jane@Example.COM ' } });
  fireEvent.change(within(dialog).getByLabelText('Password'), {
    target: { value: 'not my password' },
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Save email address' }),
  );
  expect(await within(dialog).findByText(WRONG_CURRENT)).toBeInTheDocument();
  // A refusal asks for the password again and keeps the address (an
  // email field drops the spaces around it as it is typed).
  expect(within(dialog).getByLabelText('Password')).toHaveValue('');
  expect(field).toHaveValue('Jane@Example.COM');

  fireEvent.change(within(dialog).getByLabelText('Password'), {
    target: { value: PASSWORD },
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Save email address' }),
  );
  expect(
    await within(dialog).findByText(
      'Saved: your email address is now Jane@example.com.',
    ),
  ).toBeInTheDocument();
  expect(sent('PUT', '/api/session/email').at(-1)?.body).toEqual({
    current_password: PASSWORD,
    email: 'Jane@example.com',
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
  expect(screen.queryByRole('dialog', { name: 'Email address' })).toBeNull();
});

test('Email address…: an empty field removes the address; the old one hears of it', async () => {
  fake.email = 'jane@example.com';
  const dialog = await openEmail();
  const field = within(dialog).getByLabelText('Email address');
  expect(field).toHaveValue('jane@example.com');
  // The same address is no change.
  fireEvent.change(within(dialog).getByLabelText('Password'), {
    target: { value: PASSWORD },
  });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Save email address' }),
  );
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'This is already your email address.',
  );
  expect(sent('PUT', '/api/session/email')).toHaveLength(0);

  fireEvent.change(field, { target: { value: '  ' } });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Save email address' }),
  );
  expect(
    await within(dialog).findByText(
      'Saved: your account has no email address now.',
    ),
  ).toBeInTheDocument();
  expect(dialog).toHaveTextContent(
    'When an outgoing mail server is set up, PartFlow tells jane@example.com about the change.',
  );
  expect(sent('PUT', '/api/session/email').at(-1)?.body).toEqual({
    current_password: PASSWORD,
    email: null,
  });
});

test('Email address…: typed input asks before closing; offline nothing is saved', async () => {
  const dialog = await openEmail();
  fireEvent.change(within(dialog).getByLabelText('Email address'), {
    target: { value: 'jane@example.com' },
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel (Esc)' }));
  expect(
    screen.getByRole('dialog', { name: 'Discard email address change?' }),
  ).toBeInTheDocument();
  cleanup();
  requests = [];

  fake.user = JANE;
  await renderShell('unavailable');
  fireEvent.click(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  );
  fireEvent.click(screen.getByRole('menuitem', { name: 'Email address…' }));
  const offline = screen.getByRole('dialog', { name: 'Email address' });
  expect(
    await within(offline).findByRole('button', { name: 'Save email address' }),
  ).toBeDisabled();
});

/* ============ Passkeys (Phase 17) ============ */

/** A stand-in for the browser's PublicKeyCredential. */
class FakePublicKeyCredential {
  readonly id = PASSKEY_ID;
  readonly type = 'public-key';
  toJSON() {
    return {
      id: PASSKEY_ID,
      rawId: PASSKEY_ID,
      type: 'public-key',
      response: { clientDataJSON: 'e30' },
      clientExtensionResults: {},
    };
  }
}

/** A secure context whose browser can use passkeys; returns the
 * navigator.credentials stand-in. */
function installWebAuthn() {
  vi.stubGlobal('PublicKeyCredential', FakePublicKeyCredential);
  Object.defineProperty(window, 'isSecureContext', {
    value: true,
    configurable: true,
  });
  const credentials = {
    create: vi.fn<
      (options: CredentialCreationOptions) => Promise<FakePublicKeyCredential>
    >(() => Promise.resolve(new FakePublicKeyCredential())),
    get: vi.fn<
      (options: CredentialRequestOptions) => Promise<FakePublicKeyCredential>
    >(() => Promise.resolve(new FakePublicKeyCredential())),
  };
  Object.defineProperty(navigator, 'credentials', {
    value: credentials,
    configurable: true,
  });
  return credentials;
}

afterEach(() => {
  Reflect.deleteProperty(navigator, 'credentials');
  Reflect.deleteProperty(window, 'isSecureContext');
});

test('passkeys: without a browser that can use them, no passkey control is offered', async () => {
  await renderShell();
  const dialog = openSignIn();
  expect(
    within(dialog).queryByRole('button', { name: 'Sign in with a passkey' }),
  ).toBeNull();
});

test('passkeys: a passkey alone signs in; a dismissed request changes nothing', async () => {
  const credentials = installWebAuthn();
  await renderShell();
  let dialog = openSignIn();
  credentials.get.mockRejectedValueOnce(
    new DOMException('Dismissed', 'NotAllowedError'),
  );
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Sign in with a passkey' }),
  );
  await waitFor(() => expect(credentials.get).toHaveBeenCalledTimes(1));
  await waitFor(() =>
    expect(
      within(dialog).getByRole('button', { name: 'Sign in with a passkey' }),
    ).toBeEnabled(),
  );
  expect(within(dialog).queryByRole('alert')).toBeNull();
  expect(sent('POST', '/api/session/passkey')).toHaveLength(0);

  dialog = screen.getByRole('dialog', { name: 'Sign in' });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Sign in with a passkey' }),
  );
  expect(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  ).toBeInTheDocument();
  const options = credentials.get.mock.calls[1][0] as unknown as {
    publicKey: { challenge: ArrayBuffer; rpId: string };
  };
  expect(options.publicKey.challenge).toBeInstanceOf(ArrayBuffer);
  expect(options.publicKey.rpId).toBe('localhost');
  expect(sent('POST', '/api/session/passkey')[0].body).toEqual({
    credential: {
      id: PASSKEY_ID,
      rawId: PASSKEY_ID,
      type: 'public-key',
      response: { clientDataJSON: 'e30' },
      clientExtensionResults: {},
    },
  });
});

test('passkeys: a refused passkey sign-in shows the reason and keeps the dialog', async () => {
  const credentials = installWebAuthn();
  credentials.get.mockResolvedValueOnce(
    Object.assign(new FakePublicKeyCredential(), {
      toJSON: () => ({ id: 'other' }),
    }),
  );
  await renderShell();
  const dialog = openSignIn();
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Sign in with a passkey' }),
  );
  expect(await within(dialog).findByRole('alert')).toHaveTextContent(
    PASSKEY_FAILED,
  );
});

test('passkeys: a passkey answers the second step', async () => {
  const credentials = installWebAuthn();
  fake.twoStep.add('jdoe');
  fake.passkeys = [{ id: 1, name: 'Laptop' }];
  fake.recoveryCodes = [...NEW_CODES];
  await renderShell();
  const dialog = openSignIn();
  fillSignIn(dialog, 'jdoe', PASSWORD);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
  // A passkey user starts with the passkey; a recovery code stays offered.
  fireEvent.click(
    await within(dialog).findByRole('button', { name: 'Use passkey' }),
  );
  expect(
    await screen.findByRole('button', { name: 'Account: Jane Doe' }),
  ).toBeInTheDocument();
  expect(credentials.get).toHaveBeenCalledTimes(1);
  expect(sent('POST', '/api/session/second-step/passkey')).toHaveLength(1);
});

test('Two-step sign-in…: a passkey is added with the password and a name, and removed with the password', async () => {
  const credentials = installWebAuthn();
  const dialog = await openTwoStep();
  expect(dialog).toHaveTextContent('No passkey yet.');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Add a passkey…' }),
  );
  enterPassword(dialog, 'Continue', 'not my password');
  expect(await within(dialog).findByText(WRONG_CURRENT)).toBeInTheDocument();
  enterPassword(dialog, 'Continue');
  const name = await within(dialog).findByLabelText('Passkey name');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Create passkey' }),
  );
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'Name the passkey with 1 to 80 characters',
  );
  fireEvent.change(name, { target: { value: '  Work laptop ' } });
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Create passkey' }),
  );
  // The first factor: the recovery codes are shown once.
  expect(await within(dialog).findByText('abcd-efgh-jk22')).toBeInTheDocument();
  const created = credentials.create.mock.calls[0][0] as unknown as {
    publicKey: { challenge: ArrayBuffer; user: { id: ArrayBuffer } };
  };
  expect(created.publicKey.challenge).toBeInstanceOf(ArrayBuffer);
  expect(created.publicKey.user.id).toBeInstanceOf(ArrayBuffer);
  expect(sent('POST', '/api/session/two-step/passkeys')[0].body).toEqual({
    name: 'Work laptop',
    credential: new FakePublicKeyCredential().toJSON(),
  });
  fireEvent.click(within(dialog).getByRole('button', { name: 'I saved them' }));
  expect(await within(dialog).findByText('Work laptop')).toBeInTheDocument();
  expect(dialog).toHaveTextContent('Two-step sign-in is on.');

  fireEvent.click(
    within(dialog).getByRole('button', {
      name: 'Remove the passkey Work laptop…',
    }),
  );
  expect(dialog).toHaveTextContent(
    'Signing in will take only your password, and your recovery codes are deleted.',
  );
  enterPassword(dialog, 'Remove passkey');
  expect(
    await within(dialog).findByText(/^Two-step sign-in is off\./),
  ).toBeInTheDocument();
  expect(
    sent('POST', '/api/session/two-step/passkeys/1/remove')[0].body,
  ).toEqual({ current_password: PASSWORD });
});
