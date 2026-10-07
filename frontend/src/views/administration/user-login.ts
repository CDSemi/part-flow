// Client mirror of the server's login-name rule (Administration →
// Users), used only to answer early and to preview the stored form;
// the server is authoritative. A login name is trimmed, must be plain
// ASCII, is saved in lowercase and may contain only a–z, 0–9 and
// . _ @ + - (at most 128 characters).

const LOGIN_NAME_PATTERN = /^[a-z0-9._@+-]{1,128}$/;

/** Any character outside 7-bit ASCII. Checked before lowering (as the
 * server does), so a non-ASCII letter never lowers into an ASCII one. */
function hasNonAscii(value: string): boolean {
  return Array.from(value).some((char) => char.codePointAt(0)! > 0x7f);
}

export const LOGIN_NAME_RULE_MESSAGE =
  'A login name may contain only letters (a–z), digits and . _ @ + -, with no spaces, and at most 128 characters.';

/** The login name as the server stores it: trimmed, lowercase. */
export function canonicalLoginName(raw: string): string {
  return raw.trim().toLowerCase();
}

/** The reason an entered login name would be refused, or null. */
export function loginNameError(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return 'A login name is required.';
  if (hasNonAscii(trimmed)) return LOGIN_NAME_RULE_MESSAGE;
  if (!LOGIN_NAME_PATTERN.test(trimmed.toLowerCase())) {
    return LOGIN_NAME_RULE_MESSAGE;
  }
  return null;
}
