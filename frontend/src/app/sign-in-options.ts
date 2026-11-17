// What the sign-in and password dialogs show before anyone types
// (Administration → Security): whether the sign-in dialog offers Remember
// me, and the password rules. Read once per dialog from the public
// `/api/session/sign-in-options`; while it is unread or unreadable the
// dialogs offer no Remember me and show the default password rules — the
// server applies the stored rules either way.

import { useApiData } from '../api/use-api-data';
import { getSignInOptions } from '../api/session';
import type { PasswordRules, SignInOptions } from '../api/session';
import { DEFAULT_PASSWORD_RULES } from './password-rules';

/** The sign-in options; null until read (or when they cannot be read). */
export function useSignInOptions(): SignInOptions | null {
  const { state } = useApiData(getSignInOptions);
  return state.status === 'ready' ? state.data : null;
}

/** The stored password rules; the defaults until they have been read. */
export function usePasswordRules(): PasswordRules {
  return useSignInOptions()?.passwordRules ?? DEFAULT_PASSWORD_RULES;
}
