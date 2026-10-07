// Display-only mirror of the server's password length rule (12 to 256
// characters, counted after Unicode NFKC normalization): the dialogs
// answer early, the server stays authoritative and decides.

export const MIN_PASSWORD_LENGTH = 12;
export const MAX_PASSWORD_LENGTH = 256;

/** The reason a new password (and its repetition) would be refused, or
 * null when it may be sent. */
export function newPasswordError(next: string, repeat: string): string | null {
  const length = Array.from(next.normalize('NFKC')).length;
  if (length < MIN_PASSWORD_LENGTH) return 'At least 12 characters.';
  if (length > MAX_PASSWORD_LENGTH) {
    return 'A password can be at most 256 characters long.';
  }
  if (next !== repeat) return 'The new passwords do not match.';
  return null;
}
