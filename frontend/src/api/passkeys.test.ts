import { expect, test } from 'vitest';

import {
  base64UrlToBuffer,
  bufferToBase64Url,
  credentialToJson,
  toCreationOptions,
  toRequestOptions,
} from './passkeys';

// Passkeys in the browser: the JSON form of WebAuthn (binary values as
// unpadded base64url) to the arguments of navigator.credentials and back.

const bytes = (buffer: ArrayBuffer | BufferSource | undefined) =>
  Array.from(new Uint8Array(buffer as ArrayBuffer));

test('base64url round-trips any bytes without padding', () => {
  const data = new Uint8Array([0, 1, 250, 251, 252, 253, 254, 255]);
  const text = bufferToBase64Url(data.buffer);
  expect(text).toBe('AAH6-_z9_v8');
  expect(bytes(base64UrlToBuffer(text))).toEqual(Array.from(data));
  expect(bufferToBase64Url(new Uint8Array([102, 111, 111]))).toBe('Zm9v');
});

test('creation options turn the challenge, the user id and excluded ids into bytes', () => {
  const options = toCreationOptions({
    publicKey: {
      rp: { name: 'PartFlow', id: 'pf.example.com' },
      user: { id: 'MTc', name: 'ann', displayName: 'Ann' },
      challenge: 'Zm9v',
      pubKeyCredParams: [{ type: 'public-key', alg: -7 }],
      excludeCredentials: [{ type: 'public-key', id: 'YmFy' }],
    },
  });
  expect(bytes(options.challenge)).toEqual([102, 111, 111]);
  expect(bytes(options.user.id)).toEqual([49, 55]);
  expect(options.user.name).toBe('ann');
  expect(options.rp).toEqual({ name: 'PartFlow', id: 'pf.example.com' });
  expect(bytes(options.excludeCredentials?.[0].id)).toEqual([98, 97, 114]);
});

test('request options turn the challenge and the allowed ids into bytes', () => {
  const options = toRequestOptions({
    publicKey: {
      challenge: 'Zm9v',
      rpId: 'pf.example.com',
      allowCredentials: [{ type: 'public-key', id: 'YmFy' }],
      userVerification: 'preferred',
    },
  });
  expect(bytes(options.challenge)).toEqual([102, 111, 111]);
  expect(options.rpId).toBe('pf.example.com');
  expect(bytes(options.allowCredentials?.[0].id)).toEqual([98, 97, 114]);
  // A sign-in with a passkey alone names none.
  expect(
    toRequestOptions({ publicKey: { challenge: 'Zm9v' } }).allowCredentials,
  ).toBeUndefined();
});

test('an answer becomes the JSON form when the browser has no toJSON', () => {
  const buffer = (text: string) => new TextEncoder().encode(text).buffer;
  const credential = {
    id: 'YmFy',
    rawId: buffer('bar'),
    type: 'public-key',
    authenticatorAttachment: 'platform',
    response: {
      clientDataJSON: buffer('{}'),
      authenticatorData: buffer('ad'),
      signature: buffer('sig'),
      userHandle: buffer('17'),
    },
    getClientExtensionResults: () => ({}),
  } as unknown as PublicKeyCredential;
  expect(credentialToJson(credential)).toEqual({
    id: 'YmFy',
    rawId: 'YmFy',
    type: 'public-key',
    authenticatorAttachment: 'platform',
    response: {
      clientDataJSON: 'e30',
      authenticatorData: 'YWQ',
      signature: 'c2ln',
      userHandle: 'MTc',
    },
    clientExtensionResults: {},
  });
});
