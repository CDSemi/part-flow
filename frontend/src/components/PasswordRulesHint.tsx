import './account-dialogs.css';

import type { PasswordRules } from '../api/session';
import { passwordRuleChecks } from '../app/password-rules';

/**
 * The active password rules under a new-password field (Administration →
 * Security): listed before anyone types, then each marked met (✓) or not
 * (✕) as the password is typed. The dialog's own error line names every
 * unmet rule when the user tries to save; the server decides.
 */
export function PasswordRulesHint({
  rules,
  password,
}: {
  rules: PasswordRules;
  password: string;
}) {
  const typed = password !== '';
  return (
    <ul className="pw-rules" aria-label="Password rules">
      {passwordRuleChecks(password, rules).map((check) => {
        const state = !typed ? 'rule' : check.met ? 'met' : 'unmet';
        return (
          <li key={check.key} className={`pw-rule-${state}`}>
            <span aria-hidden="true">
              {state === 'rule' ? '•' : state === 'met' ? '✓' : '✕'}
            </span>{' '}
            {check.label}
            {typed ? (
              <span className="pw-rule-state">
                {check.met ? ' — met' : ' — not met'}
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
