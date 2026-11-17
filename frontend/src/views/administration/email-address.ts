// The canonical email address rule of the server
// (app.domain.email_address, Phase 17), mirrored only to answer early in
// the editors that take an address (Administration → Users and the
// outgoing mail server): trimmed, plain ASCII, a local part of permitted
// characters, '@' and a lowercased domain with a dot, 254 characters at
// most. The server re-validates every address.

const MAX_LENGTH = 254;
const MAX_LOCAL_LENGTH = 64;
const ATOM = "[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+";
const LOCAL_PART = new RegExp(`^${ATOM}(?:\\.${ATOM})*$`);
const DOMAIN_LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

export const EMAIL_ADDRESS_ERROR =
  'Enter an email address such as name@example.com: no spaces, a domain with a dot after the @, and at most 254 characters.';

function isAscii(text: string): boolean {
  for (let index = 0; index < text.length; index += 1) {
    if (text.charCodeAt(index) > 127) return false;
  }
  return true;
}

/**
 * The address the server stores for `text`: '' for blank input (no
 * address), or null when the server would refuse it. Only the domain is
 * lowercased: `Ann@Example.COM` is saved as `Ann@example.com`.
 */
export function canonicalEmail(text: string): string | null {
  const stripped = text.trim();
  if (stripped === '') return '';
  if (!isAscii(stripped) || stripped.length > MAX_LENGTH) return null;
  const at = stripped.lastIndexOf('@');
  const local = stripped.slice(0, Math.max(at, 0));
  if (at < 0 || local.length > MAX_LOCAL_LENGTH || !LOCAL_PART.test(local)) {
    return null;
  }
  const labels = stripped
    .slice(at + 1)
    .toLowerCase()
    .split('.');
  if (labels.length < 2 || !labels.every((label) => DOMAIN_LABEL.test(label))) {
    return null;
  }
  return `${local}@${labels.join('.')}`;
}
