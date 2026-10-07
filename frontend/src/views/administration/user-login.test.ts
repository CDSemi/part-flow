import { expect, test } from 'vitest';

import {
  LOGIN_NAME_RULE_MESSAGE,
  canonicalLoginName,
  loginNameError,
} from './user-login';

// The client mirror of the login-name rule (Administration → Users):
// trimmed, plain ASCII, saved in lowercase, a–z 0–9 . _ @ + -, at most
// 128 characters. The server stays authoritative.

test('the canonical login name is trimmed and lowercase', () => {
  expect(canonicalLoginName('  JDoe ')).toBe('jdoe');
  expect(canonicalLoginName('a.b_c@D+e-f')).toBe('a.b_c@d+e-f');
});

test('the rule message is the server copy', () => {
  expect(LOGIN_NAME_RULE_MESSAGE).toBe(
    'A login name may contain only letters (a–z), digits and . _ @ + -, with no spaces, and at most 128 characters.',
  );
});

test('an empty login name is required; others follow the server rule', () => {
  expect(loginNameError('')).toBe('A login name is required.');
  expect(loginNameError('   ')).toBe('A login name is required.');
  const refused = [
    'j doe',
    'jdoé',
    // Kelvin sign: lowers to an ASCII "k", refused before lowering.
    'Kelvin',
    'a/b',
    'a'.repeat(129),
  ];
  for (const value of refused) {
    expect(loginNameError(value)).toBe(LOGIN_NAME_RULE_MESSAGE);
  }
  for (const value of ['a.b_c@d+e-f', 'a'.repeat(128), 'ABC', ' jdoe ']) {
    expect(loginNameError(value)).toBeNull();
  }
});
