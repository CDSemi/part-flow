"""A software WebAuthn authenticator for the passkey tests (Phase 17).

Answers the options PartFlow sends the way a browser hands back
``navigator.credentials.create`` and ``get`` (the WebAuthn Level 3 JSON
form, binary values as unpadded base64url): an ES256 (P-256) key pair per
passkey, the attestation format "none", the user-present and -verified
flags and a signature counter. Nothing leaves the process.
"""

import hashlib
import json
import os
import struct
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fido2 import cbor
from fido2.cose import ES256
from fido2.utils import websafe_decode, websafe_encode

_PRESENT = 0x01
_VERIFIED = 0x04
_ATTESTED = 0x40


@dataclass
class SoftPasskey:
    rp_id: str
    user_handle: bytes
    credential_id: bytes
    private_key: ec.EllipticCurvePrivateKey
    sign_count: int = 0


class SoftAuthenticator:
    """Passkeys made and used at ``origin`` (the page the browser shows)."""

    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.passkeys: list[SoftPasskey] = []

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}
        ).encode()

    def create(self, options: dict[str, Any], *, challenge: str | None = None) -> dict[str, Any]:
        """A new passkey for the creation options; ``challenge`` signs another one instead."""
        public = options["publicKey"]
        rp_id = public["rp"]["id"]
        credential_id = os.urandom(32)
        key = ec.generate_private_key(ec.SECP256R1())
        cose_key = cbor.encode(dict(ES256.from_cryptography_key(key.public_key())))  # type: ignore[no-untyped-call]
        attested = bytes(16) + struct.pack(">H", len(credential_id)) + credential_id + cose_key
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([_PRESENT | _VERIFIED | _ATTESTED])
            + struct.pack(">I", 0)
            + attested
        )
        attestation = cbor.encode({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client_data = self._client_data("webauthn.create", challenge or public["challenge"])
        self.passkeys.append(
            SoftPasskey(rp_id, websafe_decode(public["user"]["id"]), credential_id, key)
        )
        return {
            "id": websafe_encode(credential_id),
            "rawId": websafe_encode(credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": websafe_encode(client_data),
                "attestationObject": websafe_encode(attestation),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def get(
        self,
        options: dict[str, Any],
        passkey: SoftPasskey | None = None,
        *,
        verified: bool = True,
        counter: int | None = None,
        challenge: str | None = None,
    ) -> dict[str, Any]:
        """An answer to the request options, signed by ``passkey`` (else the first that fits).

        The counter grows by one unless ``counter`` sets it.
        """
        public = options["publicKey"]
        rp_id = public["rpId"]
        allowed = {websafe_decode(c["id"]) for c in public.get("allowCredentials") or []}
        chosen = passkey or next(
            p
            for p in self.passkeys
            if p.rp_id == rp_id and (not allowed or p.credential_id in allowed)
        )
        chosen.sign_count = chosen.sign_count + 1 if counter is None else counter
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([_PRESENT | (_VERIFIED if verified else 0)])
            + struct.pack(">I", chosen.sign_count)
        )
        client_data = self._client_data("webauthn.get", challenge or public["challenge"])
        signature = chosen.private_key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": websafe_encode(chosen.credential_id),
            "rawId": websafe_encode(chosen.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": websafe_encode(client_data),
                "authenticatorData": websafe_encode(auth_data),
                "signature": websafe_encode(signature),
                "userHandle": websafe_encode(chosen.user_handle),
            },
            "clientExtensionResults": {},
        }
