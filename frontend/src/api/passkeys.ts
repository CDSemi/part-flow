// Passkeys in the browser (Phase 17, owner decision of 2026-11-13): the
// WebAuthn calls behind signing in with a passkey, the passkey answer of a
// second step and adding a passkey from the account menu.
//
// The server sends WebAuthn options in their JSON form (binary values as
// unpadded base64url) and takes the browser's answer in the same form. This
// module converts both ways — with the browser's own
// PublicKeyCredential.parse*OptionsFromJSON and toJSON where they exist —
// and calls navigator.credentials.create / get. Browsers offer WebAuthn
// only in a secure context (HTTPS, or http://localhost), so every passkey
// control is hidden elsewhere (passkeysSupported). The server checks every
// answer; nothing here decides whether a passkey is valid.
//
// Production-safe: no mock data, no framework imports.

/** The options of a creation or a request, as the server sends them. */
export interface PasskeyOptionsJson {
  publicKey: Record<string, unknown>;
}

/** The browser's answer, in the JSON form the server takes. */
export type PasskeyCredentialJson = Record<string, unknown>;

/** The person dismissed the browser's passkey window, or it timed out. */
export class PasskeyCancelledError extends Error {
  constructor() {
    super('The passkey request was cancelled.');
    this.name = 'PasskeyCancelledError';
  }
}

/** Whether this page can use passkeys at all (a secure context with WebAuthn). */
export function passkeysSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.isSecureContext &&
    typeof window.PublicKeyCredential !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    navigator.credentials !== undefined
  );
}

export function base64UrlToBuffer(text: string): ArrayBuffer {
  const base64 = text.replace(/-/g, '+').replace(/_/g, '/');
  const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes.buffer;
}

export function bufferToBase64Url(
  buffer: ArrayBuffer | ArrayBufferView,
): string {
  // A view, or the buffer itself (checked as a view: an ArrayBuffer of
  // another realm is not an instance of this one's).
  const bytes = ArrayBuffer.isView(buffer)
    ? new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength)
    : new Uint8Array(buffer);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

function descriptors(
  value: unknown,
): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.map(
    (item: { id: string; type?: string; transports?: string[] }) => ({
      ...item,
      type: 'public-key',
      id: base64UrlToBuffer(item.id),
    }),
  ) as PublicKeyCredentialDescriptor[];
}

/** The arguments of navigator.credentials.create for the server's options. */
export function toCreationOptions(
  options: PasskeyOptionsJson,
): PublicKeyCredentialCreationOptions {
  const json = options.publicKey;
  const user = json.user as { id: string; name: string; displayName: string };
  return {
    ...(json as unknown as PublicKeyCredentialCreationOptions),
    challenge: base64UrlToBuffer(json.challenge as string),
    user: { ...user, id: base64UrlToBuffer(user.id) },
    excludeCredentials: descriptors(json.excludeCredentials),
  };
}

/** The arguments of navigator.credentials.get for the server's options. */
export function toRequestOptions(
  options: PasskeyOptionsJson,
): PublicKeyCredentialRequestOptions {
  const json = options.publicKey;
  return {
    ...(json as unknown as PublicKeyCredentialRequestOptions),
    challenge: base64UrlToBuffer(json.challenge as string),
    allowCredentials: descriptors(json.allowCredentials),
  };
}

interface AnswerResponse {
  clientDataJSON: ArrayBuffer;
  attestationObject?: ArrayBuffer;
  authenticatorData?: ArrayBuffer;
  signature?: ArrayBuffer;
  userHandle?: ArrayBuffer | null;
  getTransports?: () => string[];
}

/** The browser's answer in the JSON form the server takes. */
export function credentialToJson(
  credential: PublicKeyCredential,
): PasskeyCredentialJson {
  const withJson = credential as PublicKeyCredential & {
    toJSON?: () => PasskeyCredentialJson;
  };
  if (typeof withJson.toJSON === 'function') return withJson.toJSON();
  const response = credential.response as unknown as AnswerResponse;
  const answer: Record<string, unknown> = {
    clientDataJSON: bufferToBase64Url(response.clientDataJSON),
  };
  if (response.attestationObject) {
    answer.attestationObject = bufferToBase64Url(response.attestationObject);
    if (typeof response.getTransports === 'function') {
      answer.transports = response.getTransports();
    }
  }
  if (response.authenticatorData) {
    answer.authenticatorData = bufferToBase64Url(response.authenticatorData);
  }
  if (response.signature)
    answer.signature = bufferToBase64Url(response.signature);
  if (response.userHandle)
    answer.userHandle = bufferToBase64Url(response.userHandle);
  return {
    id: credential.id,
    rawId: bufferToBase64Url(credential.rawId),
    type: credential.type,
    response: answer,
    authenticatorAttachment:
      (credential as { authenticatorAttachment?: string | null })
        .authenticatorAttachment ?? null,
    clientExtensionResults: credential.getClientExtensionResults(),
  };
}

function cancelled(error: unknown): boolean {
  return (
    error instanceof DOMException &&
    (error.name === 'NotAllowedError' || error.name === 'AbortError')
  );
}

/** Make a new passkey; PasskeyCancelledError when the person dismisses it. */
export async function createPasskey(
  options: PasskeyOptionsJson,
): Promise<PasskeyCredentialJson> {
  try {
    const credential = await navigator.credentials.create({
      publicKey: toCreationOptions(options),
    });
    if (!(credential instanceof PublicKeyCredential))
      throw new PasskeyCancelledError();
    return credentialToJson(credential);
  } catch (error) {
    if (cancelled(error)) throw new PasskeyCancelledError();
    throw error;
  }
}

/** Answer a request with a passkey; PasskeyCancelledError when the person dismisses it. */
export async function getPasskey(
  options: PasskeyOptionsJson,
): Promise<PasskeyCredentialJson> {
  try {
    const credential = await navigator.credentials.get({
      publicKey: toRequestOptions(options),
    });
    if (!(credential instanceof PublicKeyCredential))
      throw new PasskeyCancelledError();
    return credentialToJson(credential);
  } catch (error) {
    if (cancelled(error)) throw new PasskeyCancelledError();
    throw error;
  }
}
