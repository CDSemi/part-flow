"""Passkeys: the WebAuthn ceremonies of sign-in and two-step sign-in (Phase 17).

Owner decision of 2026-11-13 (IMPLEMENTATION_ROADMAP Phase 17 item 8). The
relying party is the host PartFlow is opened at: its id is the host name of
the request's ``Origin``, which must be HTTPS — browsers offer WebAuthn
only in a secure context, and ``http://localhost`` serves development
(fido2's ``verify_rp_id``). A passkey is made at one host and works only
there, so each stored passkey keeps its relying party id and only those of
the request's host are offered or accepted.

Options are answered in the JSON form of WebAuthn Level 3 (binary values as
unpadded base64url), which the browser turns into the arguments of
``navigator.credentials.create`` and ``get``. Every check of an answer —
its type, origin, challenge, host, user presence and verification and
signature — is fido2's (``Fido2Server.register_complete`` and
``authenticate_complete``); this module adds the signature counter rule
and the storage form. A malformed or failing answer is ``None``: the
callers refuse it in their own words. Nothing here touches the database.
"""

import logging
import secrets
import struct
from collections.abc import Mapping, Sequence
from typing import Any, Final, NamedTuple
from urllib.parse import urlparse

from fido2.rpid import verify_rp_id
from fido2.server import Fido2Server
from fido2.utils import websafe_encode
from fido2.webauthn import (
    AttestedCredentialData,
    AuthenticationResponse,
    PublicKeyCredentialRpEntity,
    PublicKeyCredentialUserEntity,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.application.errors import InvalidInputError

logger = logging.getLogger(__name__)

RP_NAME: Final = "PartFlow"
CHALLENGE_BYTES: Final = 32
NOT_AVAILABLE_MESSAGE: Final = (
    "Passkeys work only when PartFlow is opened over HTTPS at its own address. Open PartFlow at"
    " its HTTPS address and try again."
)
# What a broken answer of a browser or authenticator can raise while it is
# parsed and checked (base64, CBOR, COSE and the fido2 checks themselves).
_REFUSED: Final = (ValueError, KeyError, TypeError, IndexError, struct.error)


class Registered(NamedTuple):
    """A verified new passkey, in its storage form."""

    credential_id: bytes
    credential_data: bytes
    sign_count: int


class StoredPasskey(NamedTuple):
    """A stored passkey an answer may come from."""

    id: int
    credential_data: bytes
    sign_count: int


class Asserted(NamedTuple):
    """The stored passkey a verified answer came from, and its new counter."""

    passkey_id: int
    sign_count: int


def relying_party(origin: str | None) -> str:
    """The relying party id of a request's ``Origin``; refused unless WebAuthn works there."""
    host = urlparse(origin).hostname if origin else None
    if not host or not verify_rp_id(host, str(origin)):
        raise InvalidInputError(NOT_AVAILABLE_MESSAGE)
    return host


def relying_party_or_none(origin: str | None) -> str | None:
    """``relying_party``, or None where passkeys cannot work (no refusal)."""
    try:
        return relying_party(origin)
    except InvalidInputError:
        return None


def _server(rp_id: str) -> Fido2Server:
    return Fido2Server(PublicKeyCredentialRpEntity(name=RP_NAME, id=rp_id))


def _state(challenge: bytes, user_verification: UserVerificationRequirement) -> dict[str, Any]:
    """The fido2 state of a request, rebuilt from the stored challenge."""
    return {"challenge": websafe_encode(challenge), "user_verification": user_verification}


def user_handle(user_id: int) -> bytes:
    """The WebAuthn user handle of a User: their id, never a name or address."""
    return str(user_id).encode("ascii")


def registration_options(
    rp_id: str,
    *,
    user_id: int,
    login_name: str,
    display_name: str,
    existing: Sequence[bytes],
) -> tuple[dict[str, Any], bytes]:
    """Creation options for a new passkey of the User, and their challenge.

    A discoverable credential (it can sign in without a login name) whose
    authenticator verifies the person; the User's passkeys at this host are
    excluded, so the same authenticator is not registered twice.
    """
    challenge = secrets.token_bytes(CHALLENGE_BYTES)
    options, _state_unused = _server(rp_id).register_begin(
        PublicKeyCredentialUserEntity(
            id=user_handle(user_id), name=login_name, display_name=display_name
        ),
        [AttestedCredentialData(data) for data in existing],
        resident_key_requirement=ResidentKeyRequirement.REQUIRED,
        user_verification=UserVerificationRequirement.REQUIRED,
        challenge=challenge,
    )
    return dict(options), challenge


def verify_registration(
    rp_id: str, challenge: bytes, credential: Mapping[str, Any]
) -> Registered | None:
    """The new passkey the browser answered with, verified; None when refused."""
    try:
        auth_data = _server(rp_id).register_complete(
            _state(challenge, UserVerificationRequirement.REQUIRED), credential
        )
        data = auth_data.credential_data
        if data is None:
            return None
        return Registered(
            credential_id=bytes(data.credential_id),
            credential_data=bytes(data),
            sign_count=int(auth_data.counter),
        )
    except _REFUSED as error:
        logger.info("Passkey registration refused: %s", type(error).__name__)
        return None


def authentication_options(
    rp_id: str,
    *,
    allowed: Sequence[bytes] | None,
    user_verification: UserVerificationRequirement,
) -> tuple[dict[str, Any], bytes]:
    """Request options and their challenge.

    ``allowed`` lists the attested credential data of the passkeys that may
    answer (a second step); None leaves the choice to the authenticator (a
    sign-in with a passkey alone).
    """
    challenge = secrets.token_bytes(CHALLENGE_BYTES)
    options, _state_unused = _server(rp_id).authenticate_begin(
        None if allowed is None else [AttestedCredentialData(data) for data in allowed],
        user_verification=user_verification,
        challenge=challenge,
    )
    return dict(options), challenge


def answered_credential(credential: Mapping[str, Any]) -> tuple[bytes, bytes | None] | None:
    """The credential id and user handle an answer names, before any check; None if malformed."""
    try:
        response = AuthenticationResponse.from_dict(credential)
        handle = response.response.user_handle
        return bytes(response.raw_id), None if handle is None else bytes(handle)
    except _REFUSED:
        return None


def verify_assertion(
    rp_id: str,
    challenge: bytes,
    user_verification: UserVerificationRequirement,
    candidates: Sequence[StoredPasskey],
    credential: Mapping[str, Any],
) -> Asserted | None:
    """The stored passkey a verified answer came from; None when refused.

    The signature counter must grow when the authenticator keeps one (either
    counter non-zero): a counter that does not may come from a cloned
    authenticator.
    """
    try:
        response = AuthenticationResponse.from_dict(credential)
        by_id = {AttestedCredentialData(c.credential_data).credential_id: c for c in candidates}
        stored = by_id.get(bytes(response.raw_id))
        if stored is None:
            return None
        _server(rp_id).authenticate_complete(
            _state(challenge, user_verification),
            [AttestedCredentialData(stored.credential_data)],
            credential,
        )
        counter = int(response.response.authenticator_data.counter)
    except _REFUSED as error:
        logger.info("Passkey answer refused: %s", type(error).__name__)
        return None
    if (counter or stored.sign_count) and counter <= stored.sign_count:
        logger.warning("Passkey %s answered with a counter that did not grow", stored.id)
        return None
    return Asserted(passkey_id=stored.id, sign_count=counter)


def second_step_options(rp_id: str, allowed: Sequence[bytes]) -> tuple[dict[str, Any], bytes]:
    """Request options of a second step: one of the User's passkeys, presence enough."""
    return authentication_options(
        rp_id, allowed=allowed, user_verification=UserVerificationRequirement.PREFERRED
    )


def sign_in_options(rp_id: str) -> tuple[dict[str, Any], bytes]:
    """Request options of a sign-in with a passkey alone: any passkey, the person verified."""
    return authentication_options(
        rp_id, allowed=None, user_verification=UserVerificationRequirement.REQUIRED
    )


def verify_second_step(
    rp_id: str,
    challenge: bytes,
    candidates: Sequence[StoredPasskey],
    credential: Mapping[str, Any],
) -> Asserted | None:
    return verify_assertion(
        rp_id, challenge, UserVerificationRequirement.PREFERRED, candidates, credential
    )


def verify_sign_in(
    rp_id: str,
    challenge: bytes,
    candidates: Sequence[StoredPasskey],
    credential: Mapping[str, Any],
) -> Asserted | None:
    return verify_assertion(
        rp_id, challenge, UserVerificationRequirement.REQUIRED, candidates, credential
    )
