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
// voluntary and forced — and Set up PartFlow) through the real session
// provider, against an in-memory fake of the /api/session and /api/setup
// wire contract.

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
};

interface Fake {
  user: WireUser | null;
  setupOpen: boolean;
  /** login name → [password, temporary] */
  accounts: Record<string, [string, boolean]>;
  eligibleRoles: { id: number; name: string }[];
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
  ).toEqual(['Change password…', 'Sign out']);
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
  fillChange(dialog, PASSWORD, 'too short');
  fireEvent.click(
    within(dialog).getByRole('button', { name: 'Change password' }),
  );
  expect(within(dialog).getByRole('alert')).toHaveTextContent(
    'At least 12 characters.',
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
