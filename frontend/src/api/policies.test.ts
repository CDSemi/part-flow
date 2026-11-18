import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import {
  getBackupPolicy,
  getPasswordRulesPolicy,
  getRememberMePolicy,
  getRetentionPolicy,
  getSignInPolicy,
  getTwoStepPolicy,
  updateBackupPolicy,
  updatePasswordRulesPolicy,
  updateRememberMePolicy,
  updateRetentionPolicy,
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

function answer(body: unknown) {
  fetchMock.mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status: 200 })),
  );
}

function lastRequest(): { path: string; method?: string; body: unknown } {
  const [path, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit];
  return {
    path,
    method: init.method,
    body: JSON.parse(init.body as string),
  };
}

test('the password rules map every rule and send only the provided ones', async () => {
  const wire = {
    password_min_length: 6,
    password_max_length: 256,
    password_require_digit: false,
    password_require_special: true,
    password_require_uppercase: false,
    password_require_lowercase: false,
    updated_at: '2026-11-17T08:00:00Z',
  };
  answer(wire);
  expect(await getPasswordRulesPolicy()).toEqual({
    minLength: 6,
    maxLength: 256,
    requireDigit: false,
    requireSpecial: true,
    requireUppercase: false,
    requireLowercase: false,
    updatedAt: '2026-11-17T08:00:00Z',
  });
  await updatePasswordRulesPolicy({ minLength: 10, requireDigit: true });
  expect(lastRequest()).toEqual({
    path: '/api/policies/password-rules',
    method: 'PUT',
    body: { password_min_length: 10, password_require_digit: true },
  });
  answer({ ...wire, password_require_digit: 'yes' });
  await expect(getPasswordRulesPolicy()).rejects.toThrow(
    'Missing password rule from the server: requireDigit',
  );
});

test('Remember me maps the three settings and sends only the provided ones', async () => {
  answer({
    remember_me_offered: true,
    remember_me_expires: false,
    remember_me_days: 30,
    updated_at: '2026-11-17T08:00:00Z',
  });
  expect(await getRememberMePolicy()).toEqual({
    offered: true,
    expires: false,
    days: 30,
    updatedAt: '2026-11-17T08:00:00Z',
  });
  await updateRememberMePolicy({ days: 90 });
  expect(lastRequest()).toEqual({
    path: '/api/policies/remember-me',
    method: 'PUT',
    body: { remember_me_days: 90 },
  });
});

const RETENTION_WIRE = {
  retention_period_months: 24,
  shortest_retention_months: 12,
  archive_start_minute: 20,
  archive_preview_seconds: 90,
  archive_max_wait_days: 7,
  archive_purge_batch_movements: 50000,
  archive_purge_batch_part_numbers: 20,
  updated_at: '2026-11-18T08:00:00Z',
};

test('the History archival & purge settings map the purge batch limits and send only the provided ones', async () => {
  answer(RETENTION_WIRE);
  expect(await getRetentionPolicy()).toEqual({
    retentionPeriodMonths: 24,
    shortestRetentionMonths: 12,
    startMinute: 20,
    previewSeconds: 90,
    maxWaitDays: 7,
    purgeBatchMovements: 50000,
    purgeBatchPartNumbers: 20,
    updatedAt: '2026-11-18T08:00:00Z',
  });
  await updateRetentionPolicy({ purgeBatchMovements: 1000 });
  expect(lastRequest()).toEqual({
    path: '/api/policies/data-retention',
    method: 'PUT',
    body: { archive_purge_batch_movements: 1000 },
  });
  await updateRetentionPolicy({ purgeBatchPartNumbers: 5, startMinute: 45 });
  expect(lastRequest().body).toEqual({
    archive_start_minute: 45,
    archive_purge_batch_part_numbers: 5,
  });
  // A setting the server left out is refused with its wire name.
  answer({ ...RETENTION_WIRE, archive_purge_batch_part_numbers: undefined });
  await expect(getRetentionPolicy()).rejects.toThrow(
    'The server answered without the History archival & purge setting archive_purge_batch_part_numbers.',
  );
});

test('the backup policy maps the pre-purge backups kept and replaces all five fields', async () => {
  answer({
    backup_interval_hours: 1,
    backup_keep_hourly: 48,
    backup_keep_daily: 30,
    backup_keep_weekly: 52,
    backup_keep_pre_purge: 3,
    updated_at: '2026-11-18T08:00:00Z',
  });
  expect(await getBackupPolicy()).toEqual({
    intervalHours: 1,
    keepHourly: 48,
    keepDaily: 30,
    keepWeekly: 52,
    keepPrePurge: 3,
  });
  await updateBackupPolicy({
    intervalHours: 6,
    keepHourly: 0,
    keepDaily: 14,
    keepWeekly: 1,
    keepPrePurge: 10,
  });
  expect(lastRequest()).toEqual({
    path: '/api/policies/backups',
    method: 'PUT',
    body: {
      backup_interval_hours: 6,
      backup_keep_hourly: 0,
      backup_keep_daily: 14,
      backup_keep_weekly: 1,
      backup_keep_pre_purge: 10,
    },
  });
});
