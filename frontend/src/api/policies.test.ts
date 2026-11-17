import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import {
  getSignInPolicy,
  getTwoStepPolicy,
  updateSignInPolicy,
  updateTwoStepPolicy,
} from './policies';

// The user sign-in policy: wire mapping and the partial PUT body (only
// the provided settings travel, so a stale editor never reverts another
// administrator's change).

let fetchMock: ReturnType<typeof vi.fn>;

const WIRE = {
  user_session_expires: true,
  user_session_days: 30,
  sign_in_lockout_attempts: 10,
  sign_in_lockout_minutes: 15,
  require_password_change: true,
  updated_at: '2026-10-06T08:00:00Z',
};

beforeEach(() => {
  fetchMock = vi.fn(() =>
    Promise.resolve(new Response(JSON.stringify(WIRE), { status: 200 })),
  );
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test('getSignInPolicy maps the five settings', async () => {
  expect(await getSignInPolicy()).toEqual({
    sessionExpires: true,
    sessionDays: 30,
    lockoutAttempts: 10,
    lockoutMinutes: 15,
    requirePasswordChange: true,
    updatedAt: '2026-10-06T08:00:00Z',
  });
  expect(fetchMock.mock.calls[0][0]).toBe('/api/policies/sign-in');
});

test('updateSignInPolicy sends only the provided settings, in snake_case', async () => {
  await updateSignInPolicy({ sessionDays: 7 });
  await updateSignInPolicy({ sessionExpires: false });
  await updateSignInPolicy({
    lockoutAttempts: 3,
    lockoutMinutes: 60,
    requirePasswordChange: false,
  });
  const bodies = fetchMock.mock.calls.map(([path, init]) => {
    const request = init as RequestInit;
    expect(path).toBe('/api/policies/sign-in');
    expect(request.method).toBe('PUT');
    return JSON.parse(request.body as string) as unknown;
  });
  expect(bodies).toEqual([
    { user_session_days: 7 },
    { user_session_expires: false },
    {
      sign_in_lockout_attempts: 3,
      sign_in_lockout_minutes: 60,
      require_password_change: false,
    },
  ]);
});

const TWO_STEP_WIRE = {
  two_step_window_minutes: 15,
  two_step_max_codes: 5,
  two_step_lockout_codes: 10,
  two_step_email_codes: 3,
  two_step_email_interval_seconds: 60,
  two_step_email_code_digits: 8,
  two_step_recovery_codes: 10,
  two_step_recovery_code_length: 12,
  two_step_setup_minutes: 15,
  two_step_clock_drift_steps: 1,
  updated_at: '2026-11-17T08:00:00Z',
};

test('the two-step policy maps every number and sends only the provided ones', async () => {
  fetchMock.mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(TWO_STEP_WIRE), { status: 200 }),
    ),
  );
  expect(await getTwoStepPolicy()).toEqual({
    windowMinutes: 15,
    maxCodes: 5,
    lockoutCodes: 10,
    emailCodes: 3,
    emailIntervalSeconds: 60,
    emailCodeDigits: 8,
    recoveryCodes: 10,
    recoveryCodeLength: 12,
    setupMinutes: 15,
    clockDriftSteps: 1,
    updatedAt: '2026-11-17T08:00:00Z',
  });
  await updateTwoStepPolicy({ maxCodes: 3, clockDriftSteps: 0 });
  const [path, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
  expect(path).toBe('/api/policies/two-step');
  expect(init.method).toBe('PUT');
  expect(JSON.parse(init.body as string)).toEqual({
    two_step_max_codes: 3,
    two_step_clock_drift_steps: 0,
  });

  const partial: Record<string, unknown> = { ...TWO_STEP_WIRE };
  delete partial.two_step_max_codes;
  fetchMock.mockImplementationOnce(() =>
    Promise.resolve(new Response(JSON.stringify(partial), { status: 200 })),
  );
  await expect(getTwoStepPolicy()).rejects.toThrow(
    'Missing two-step setting from the server: maxCodes',
  );
});
