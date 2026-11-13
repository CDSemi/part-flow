// Display-only mirror of the server's password length rule (6 to 256
// characters, counted after Unicode NFKC normalization): the dialogs
// answer early, the server stays authoritative and decides.

export const MIN_PASSWORD_LENGTH = 6;
export const MAX_PASSWORD_LENGTH = 256;

/** The length rule as the password dialogs state it. */
export const PASSWORD_LENGTH_HINT = `At least ${MIN_PASSWORD_LENGTH} characters.`;

/** The reason a new password (and its repetition) would be refused, or
 * null when it may be sent. */
export function newPasswordError(next: string, repeat: string): string | null {
  const length = Array.from(next.normalize('NFKC')).length;
  if (length < MIN_PASSWORD_LENGTH) return PASSWORD_LENGTH_HINT;
  if (length > MAX_PASSWORD_LENGTH) {
    return 'A password can be at most 256 characters long.';
  }
  if (next !== repeat) return 'The new passwords do not match.';
  return null;
}
