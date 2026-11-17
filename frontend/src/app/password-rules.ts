// Display-only mirror of the server's password rules (Administration →
// Security): the minimum and maximum length, counted after Unicode NFKC
// normalization, and the optional digit, special-character, uppercase
// and lowercase requirements, judged by Unicode general category exactly
// as the server judges them (a digit is `Nd`, an uppercase letter `Lu`, a
// lowercase letter `Ll`, a special character a punctuation mark or a
// symbol — `P` or `S`; a space is none of them). The dialogs show the
// active rules before anyone types and name each unmet one; the server
// stays authoritative and decides.

import type { PasswordRules } from '../api/session';

/** The rules a fresh install starts with (6 to 256 characters, nothing
 * else): shown until the stored rules have been read. */
export const DEFAULT_PASSWORD_RULES: PasswordRules = {
  minLength: 6,
  maxLength: 256,
  requireDigit: false,
  requireSpecial: false,
  requireUppercase: false,
  requireLowercase: false,
};

/** One rule as the password dialogs list it. */
export interface PasswordRuleCheck {
  /** Stable key for the list. */
  key: string;
  /** The rule, shown before anyone types. */
  label: string;
  /** Whether `password` satisfies it. */
  met: boolean;
  /** The unmet rule as the server words its refusal. */
  message: string;
}

const DIGIT = /\p{Nd}/u;
const SPECIAL = /[\p{P}\p{S}]/u;
const UPPERCASE = /\p{Lu}/u;
const LOWERCASE = /\p{Ll}/u;

/** Every active rule of `rules`, judged against `password`. */
export function passwordRuleChecks(
  password: string,
  rules: PasswordRules,
): PasswordRuleCheck[] {
  const normalized = password.normalize('NFKC');
  const length = Array.from(normalized).length;
  const checks: PasswordRuleCheck[] = [
    {
      key: 'length',
      label: `${rules.minLength} to ${rules.maxLength} characters`,
      met: length >= rules.minLength && length <= rules.maxLength,
      message:
        length > rules.maxLength
          ? `A password can be at most ${rules.maxLength} characters long.`
          : `A password must be at least ${rules.minLength} characters long.`,
    },
  ];
  if (rules.requireDigit) {
    checks.push({
      key: 'digit',
      label: 'A digit',
      met: DIGIT.test(normalized),
      message: 'A password must contain a digit.',
    });
  }
  if (rules.requireSpecial) {
    checks.push({
      key: 'special',
      label: 'A special character such as ! # % or *',
      met: SPECIAL.test(normalized),
      message:
        'A password must contain a special character such as ! # % or *.',
    });
  }
  if (rules.requireUppercase) {
    checks.push({
      key: 'uppercase',
      label: 'An uppercase letter',
      met: UPPERCASE.test(normalized),
      message: 'A password must contain an uppercase letter.',
    });
  }
  if (rules.requireLowercase) {
    checks.push({
      key: 'lowercase',
      label: 'A lowercase letter',
      met: LOWERCASE.test(normalized),
      message: 'A password must contain a lowercase letter.',
    });
  }
  return checks;
}

/** The reason a new password (and its repetition) would be refused —
 * every unmet rule, one sentence each — or null when it may be sent. */
export function newPasswordError(
  next: string,
  repeat: string,
  rules: PasswordRules,
): string | null {
  const unmet = passwordRuleChecks(next, rules).filter((check) => !check.met);
  if (unmet.length > 0) return unmet.map((check) => check.message).join(' ');
  if (next !== repeat) return 'The new passwords do not match.';
  return null;
}
