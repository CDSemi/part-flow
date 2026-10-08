import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { apiRequest } from '../api/client';
import type { SessionUser } from '../api/session';
import { ConnectivityContext } from './connectivity-context';
import type { ConnectivityStatus } from './connectivity-context';
import { newPasswordError } from './password-rules';
import { RouterProvider } from './router-provider';
import { SessionContext, hasPermission, useSession } from './session-context';
import type { SessionValue } from './session-context';
import { SessionProvider } from './session-provider';

// The user sign-in provider: one `GET /api/session` at start, `unknown`
// on a failed or malformed read (read again when the connection is
// regained), and the reactions to an ended sign-in and to a required
// password change. The dialogs themselves have their own suite.

const WIRE_USER = {
  id: 7,
  login_name: 'jdoe',
  display_name: 'Jane Doe',
  role_id: 2,
  role_name: 'Manager',
  avatar_updated_at: null,
  permissions: ['MANAGE_USERS_AND_ROLES'],
  must_change_password: false,
  session_expires_at: null,
  theme_preference: null,
};

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

/** The next `GET /api/session` answers; a function answers per call. */
let sessionAnswer: () => Promise<Response>;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  window.history.replaceState({}, '', '/administration');
  sessionAnswer = () => json({ user: null, setup_open: false });
  fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/session' && init?.method === 'DELETE') {
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    if (url === '/api/session') return sessionAnswer();
    if (url === '/api/policies/sign-in') {
      return json(
        {
          detail:
            'You are not signed in, or your sign-in has ended. Sign in to continue.',
          authentication_required: true,
        },
        401,
      );
    }
    if (url === '/api/policies/due-soon') {
      return json(
        {
          detail: 'Choose a new password before you continue.',
          password_change_required: true,
        },
        403,
      );
    }
    return json({ detail: `Unexpected ${url}` }, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function sessionReads(): number {
  return fetchMock.mock.calls.filter(
    ([input]) => String(input) === '/api/session',
  ).length;
}

function Probe() {
  const session = useSession();
  return (
    <>
      <span data-testid="status">{session.status}</span>
      <span data-testid="user">{session.user?.displayName ?? '-'}</span>
      <span data-testid="can">
        {String(session.can('MANAGE_USERS_AND_ROLES'))}
      </span>
      <span data-testid="checking">{String(session.checking)}</span>
      <span data-testid="ended-by">{session.endedBy ?? '-'}</span>
      <button onClick={() => void session.signOut()}>sign out</button>
      <button onClick={() => void session.refresh()}>refresh</button>
      <button
        onClick={() => {
          apiRequest('/api/policies/sign-in').catch(() => undefined);
        }}
      >
        ended
      </button>
      <button
        onClick={() => {
          apiRequest('/api/policies/sign-in', { promptSignIn: false }).catch(
            () => undefined,
          );
        }}
      >
        ended quietly
      </button>
      <button
        onClick={() => {
          apiRequest('/api/policies/due-soon').catch(() => undefined);
        }}
      >
        change required
      </button>
    </>
  );
}

function tree(connectivity: ConnectivityStatus = 'connected') {
  return (
    <ConnectivityContext.Provider
      value={{ status: connectivity, retry: vi.fn() }}
    >
      <RouterProvider>
        <SessionProvider>
          <Probe />
        </SessionProvider>
      </RouterProvider>
    </ConnectivityContext.Provider>
  );
}

const status = () => screen.getByTestId('status').textContent;

/** An explicit session value for a component that reads the session. */
function renderWithSession(ui: React.ReactElement, value: SessionValue) {
  return render(
    <SessionContext.Provider value={value}>{ui}</SessionContext.Provider>,
  );
}

test('useSession throws outside a provider', () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  expect(() => render(<Probe />)).toThrow(
    'useSession must be used within SessionProvider',
  );
});

test('an explicit test value supplies the session', () => {
  const user = {
    id: 1,
    loginName: 'a',
    displayName: 'Ada Admin',
    roleId: 1,
    roleName: 'Administrator',
    avatarUpdatedAt: null,
    permissions: [],
    mustChangePassword: false,
    sessionExpiresAt: null,
    themePreference: null,
  } satisfies SessionUser;
  renderWithSession(
    <ConnectivityContext.Provider
      value={{ status: 'connected', retry: vi.fn() }}
    >
      <Probe />
    </ConnectivityContext.Provider>,
    {
      status: 'signed-in',
      user,
      setupOpen: false,
      checking: false,
      endedBy: null,
      can: (permission) => hasPermission(user, permission),
      openSignIn: vi.fn(),
      openSetup: vi.fn(),
      openChangePassword: vi.fn(),
      signOut: vi.fn(async () => {}),
      refresh: vi.fn(async () => {}),
    },
  );
  expect(status()).toBe('signed-in');
  expect(screen.getByTestId('can').textContent).toBe('false');
  expect(fetchMock).not.toHaveBeenCalled();
});

test('the provider starts signed out or signed in from the server', async () => {
  render(tree());
  expect(status()).toBe('unknown');
  await waitFor(() => expect(status()).toBe('signed-out'));
  expect(screen.getByTestId('user').textContent).toBe('-');
  cleanup();

  sessionAnswer = () => json({ user: WIRE_USER, setup_open: false });
  render(tree());
  await waitFor(() => expect(status()).toBe('signed-in'));
  expect(screen.getByTestId('user').textContent).toBe('Jane Doe');
  expect(screen.getByTestId('can').textContent).toBe('true');
});

test('a failed read stays unknown and is read again when the connection is regained', async () => {
  sessionAnswer = () => Promise.reject(new TypeError('Failed to fetch'));
  const view = render(tree('connected'));
  await waitFor(() => expect(sessionReads()).toBe(1));
  await act(async () => {});
  expect(status()).toBe('unknown');

  // Lost, then regained: one more read.
  view.rerender(tree('unavailable'));
  sessionAnswer = () => json({ user: WIRE_USER, setup_open: false });
  view.rerender(tree('connected'));
  await waitFor(() => expect(status()).toBe('signed-in'));
  expect(sessionReads()).toBe(2);

  // Known now: regaining the connection reads nothing more.
  view.rerender(tree('unavailable'));
  view.rerender(tree('connected'));
  await act(async () => {});
  expect(sessionReads()).toBe(2);
});

test('a malformed answer is unknown, never a crash', async () => {
  sessionAnswer = () => json({ status: 'ok' });
  render(tree());
  await waitFor(() => expect(sessionReads()).toBe(1));
  await act(async () => {});
  expect(status()).toBe('unknown');
  expect(screen.getByTestId('user').textContent).toBe('-');
});

test('an ended sign-in signs out and opens the Sign-in dialog', async () => {
  sessionAnswer = () => json({ user: WIRE_USER, setup_open: false });
  render(tree());
  await waitFor(() => expect(status()).toBe('signed-in'));

  fireEvent.click(screen.getByRole('button', { name: 'ended' }));
  await waitFor(() => expect(status()).toBe('signed-out'));
  expect(screen.getByRole('dialog', { name: 'Sign in' })).toBeInTheDocument();
  expect(sessionReads()).toBe(1);
});

test('an ended sign-in of a request sent without a prompt signs out as expired and opens no dialog', async () => {
  sessionAnswer = () => json({ user: WIRE_USER, setup_open: false });
  render(tree());
  await waitFor(() => expect(status()).toBe('signed-in'));

  fireEvent.click(screen.getByRole('button', { name: 'ended quietly' }));
  await waitFor(() => expect(status()).toBe('signed-out'));
  expect(screen.getByTestId('ended-by').textContent).toBe('expired');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(sessionReads()).toBe(1);

  // A later refusal with the prompt opens the dialog as usual.
  fireEvent.click(screen.getByRole('button', { name: 'ended' }));
  expect(
    await screen.findByRole('dialog', { name: 'Sign in' }),
  ).toBeInTheDocument();
});

test('a required password change re-reads the sign-in and opens the forced dialog', async () => {
  sessionAnswer = () => json({ user: WIRE_USER, setup_open: false });
  render(tree());
  await waitFor(() => expect(status()).toBe('signed-in'));

  sessionAnswer = () =>
    json({
      user: { ...WIRE_USER, must_change_password: true },
      setup_open: false,
    });
  fireEvent.click(screen.getByRole('button', { name: 'change required' }));
  expect(
    await screen.findByRole('dialog', { name: 'Choose a new password' }),
  ).toBeInTheDocument();
  expect(sessionReads()).toBe(2);
});

test('checking is true while the sign-in is read; endedBy tells an ended sign-in from a sign-out', async () => {
  let answer: (response: Response) => void = () => undefined;
  sessionAnswer = () =>
    new Promise<Response>((resolve) => {
      answer = resolve;
    });
  render(tree());
  const checking = () => screen.getByTestId('checking').textContent;
  const endedBy = () => screen.getByTestId('ended-by').textContent;
  expect(checking()).toBe('true');
  expect(status()).toBe('unknown');
  await act(async () => {
    answer(
      new Response(JSON.stringify({ user: WIRE_USER, setup_open: false })),
    );
  });
  await waitFor(() => expect(status()).toBe('signed-in'));
  expect(checking()).toBe('false');
  expect(endedBy()).toBe('-');

  // The server refuses the sign-in as ended.
  fireEvent.click(screen.getByRole('button', { name: 'ended' }));
  await waitFor(() => expect(status()).toBe('signed-out'));
  expect(endedBy()).toBe('expired');

  // Signed in again (here: read again from the server).
  sessionAnswer = () => json({ user: WIRE_USER, setup_open: false });
  fireEvent.click(screen.getByRole('button', { name: 'refresh' }));
  await waitFor(() => expect(status()).toBe('signed-in'));
  expect(endedBy()).toBe('-');
  expect(checking()).toBe('false');

  // An explicit sign-out.
  fireEvent.click(screen.getByRole('button', { name: 'sign out' }));
  await waitFor(() => expect(status()).toBe('signed-out'));
  expect(endedBy()).toBe('sign-out');
});

test('hasPermission: no user holds nothing; a user holds exactly the role keys', () => {
  const user = {
    id: 1,
    loginName: 'a',
    displayName: 'A',
    roleId: 1,
    roleName: 'R',
    avatarUpdatedAt: null,
    permissions: ['CONFIGURE_SYSTEM_SETTINGS'],
    mustChangePassword: false,
    sessionExpiresAt: null,
    themePreference: null,
  } satisfies SessionUser;
  expect(hasPermission(null, 'CONFIGURE_SYSTEM_SETTINGS')).toBe(false);
  expect(hasPermission(user, 'CONFIGURE_SYSTEM_SETTINGS')).toBe(true);
  expect(hasPermission(user, 'MANAGE_USERS_AND_ROLES')).toBe(false);
});

test('newPasswordError mirrors the 12–256 character rule after NFKC and the repetition', () => {
  expect(newPasswordError('', '')).toBe('At least 12 characters.');
  expect(newPasswordError('elevenchars', 'elevenchars')).toBe(
    'At least 12 characters.',
  );
  expect(newPasswordError('twelve chars', 'twelve chars')).toBeNull();
  // NFKC: the ligature "ﬁ" counts as the two characters "fi".
  expect(newPasswordError('ﬁﬁﬁﬁﬁﬁ', 'ﬁﬁﬁﬁﬁﬁ')).toBeNull();
  const long = 'x'.repeat(257);
  expect(newPasswordError(long, long)).toBe(
    'A password can be at most 256 characters long.',
  );
  expect(newPasswordError('twelve chars', 'twelve chars!')).toBe(
    'The new passwords do not match.',
  );
});
