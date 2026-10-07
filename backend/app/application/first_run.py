"""First-run setup: the first administrator (Phase 14 slice 1; owner decisions OD-P4/OD-P5).

While no administrator exists, PartFlow offers the **Set up PartFlow**
screen, which creates the first administrator and signs it in. An
administrator, for setup purposes, is an ACTIVE User WITH A PASSWORD
whose role holds ``MANAGE_USERS_AND_ROLES`` (permission keys, never role
names). The chosen role must hold both management keys —
``MANAGE_USERS_AND_ROLES`` and ``MANAGE_CORRECTION_PERMISSIONS`` — so
the first administrator can later manage every permission.

Protection against a LAN takeover before setup: a one-time setup token
(160 random bits, base32, eight groups of four) lives only in this
process's memory, is printed once to the server log at WARNING (logger
``app.first_run``) when the process first observes that no
administrator exists, and is required by the creation. It is never
stored and never returned. After a successful creation the token
rotates, so it stops working; a restart issues a new one. Several
backend processes each hold and announce their own token; the database
check below closes setup for all of them.

The creation is atomic: it runs under the transaction-scoped
``partflow:user-administration`` advisory lock with the "no
administrator" re-check inside it (a second concurrent creation waits
and is then refused), re-reads the chosen role's grants under a ``FOR
SHARE`` role lock, and inserts the User, its password, its first
session and the audit rows in one transaction.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import threading
from typing import Final, NamedTuple

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.application import audit, authentication, password_hashing, user_access, users
from app.application.common import commit, flush, required_text
from app.application.errors import (
    InvalidInputError,
    SetupClosedError,
    SetupTokenInvalidError,
)
from app.domain.enums import AuditEntityType, AuditEventType, Permission
from app.domain.password_policy import InvalidPasswordError, validate_new_password
from app.infrastructure.models import Role, RolePermission, User, UserCredential

# The one logger that may carry a secret: the setup token announcement.
logger = logging.getLogger("app.first_run")

_ELIGIBLE_KEYS: Final = frozenset(
    {Permission.MANAGE_USERS_AND_ROLES, Permission.MANAGE_CORRECTION_PERMISSIONS}
)
_TOKEN_BYTES: Final = 20
_GROUP: Final = 4
_SOURCE: Final = {"source": "first-run-setup"}

_SETUP_CLOSED: Final = "PartFlow already has an administrator. Sign in instead."
_TOKEN_INVALID: Final = (
    "The setup token is not correct. Copy the current token from the PartFlow server log."
)
_ANNOUNCEMENT: Final = (
    "PartFlow first-run setup is open: no administrator exists. Setup token: {token}"
    ' — enter it in PartFlow under "Set up PartFlow". It stops working once the first'
    " administrator is created or the server restarts."
)
_STARTUP_UNKNOWN: Final = (
    "First-run state could not be determined at startup; the setup token is announced when"
    " the setup screen is first requested."
)


def _normalize_token(value: str) -> str:
    return value.replace(" ", "").replace("-", "").upper()


def _token_digest(value: str) -> bytes:
    return hashlib.sha256(_normalize_token(value).encode("utf-8")).digest()


def _new_token() -> str:
    raw = base64.b32encode(secrets.token_bytes(_TOKEN_BYTES)).decode("ascii")
    return "-".join(raw[index : index + _GROUP] for index in range(0, len(raw), _GROUP))


class SetupGate:
    """The process's current setup token (thread-safe; ``app.state.setup_gate``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token = _new_token()
        self._announced = False

    def matches(self, presented: str) -> bool:
        """Whether ``presented`` is the current token (spaces, dashes and case ignored)."""
        with self._lock:
            expected = _token_digest(self._token)
        return hmac.compare_digest(_token_digest(presented), expected)

    def announce_once(self, log: logging.Logger) -> None:
        """Log the current token, once per token."""
        with self._lock:
            if self._announced:
                return
            self._announced = True
            token = self._token
        log.warning(_ANNOUNCEMENT.format(token=token))

    def rotate(self) -> None:
        """Replace the token with a new, unannounced one (after a creation)."""
        with self._lock:
            self._token = _new_token()
            self._announced = False


class RoleRef(NamedTuple):
    id: int
    name: str


class SetupStatus(NamedTuple):
    open: bool
    # Empty when setup is closed.
    roles: list[RoleRef]


def is_setup_open(session: Session, gate: SetupGate) -> bool:
    """Whether no administrator exists; announces the token while setup is open."""
    open_ = not user_access.administrator_exists(session)
    if open_:
        gate.announce_once(logger)
    return open_


def announce_if_open(engine: Engine, gate: SetupGate) -> None:
    """Startup: announce the token when no administrator exists, and warn
    when no one may manage correction permissions (Phase 14 slice 2).

    A database error never stops startup: the token is then announced
    when the setup screen or the session state is first requested.
    """
    try:
        with Session(engine) as session:
            is_setup_open(session, gate)
            user_access.warn_if_correction_management_lost(session)
    except DBAPIError:
        logger.warning(_STARTUP_UNKNOWN)


def _eligible(session: Session, role_id: int) -> bool:
    granted = set(
        session.scalars(
            select(RolePermission.permission).where(
                RolePermission.role_id == role_id,
                RolePermission.permission.in_(sorted(_ELIGIBLE_KEYS)),
            )
        )
    )
    return granted == set(_ELIGIBLE_KEYS)


def _not_eligible(role_id: int) -> InvalidInputError:
    return InvalidInputError(
        f"Role {role_id} cannot administer PartFlow. Choose a role that may manage users and"
        " roles and correction permissions."
    )


def eligible_roles(session: Session) -> list[RoleRef]:
    """Roles the first administrator may hold, by name, id."""
    holders = (
        select(RolePermission.role_id)
        .where(RolePermission.permission.in_(sorted(_ELIGIBLE_KEYS)))
        .group_by(RolePermission.role_id)
        # (role_id, permission) is the primary key: each grant counts once.
        .having(func.count() == len(_ELIGIBLE_KEYS))
    )
    rows = session.execute(
        select(Role.id, Role.name).where(Role.id.in_(holders)).order_by(Role.name, Role.id)
    )
    return [RoleRef(id=role_id, name=name) for role_id, name in rows]


def setup_status(session: Session, gate: SetupGate) -> SetupStatus:
    """Whether setup is open and, while it is, the eligible roles."""
    if not is_setup_open(session, gate):
        return SetupStatus(open=False, roles=[])
    return SetupStatus(open=True, roles=eligible_roles(session))


def create_first_administrator(
    session: Session,
    gate: SetupGate,
    *,
    setup_token: str,
    login_name: object,
    display_name: object,
    role_id: object,
    password: str,
) -> authentication.SessionGrant:
    """Create the first administrator and sign it in; the token then rotates."""
    if not gate.matches(setup_token):
        # The same fact GET /api/setup discloses: a retry after an
        # unknown outcome (the token rotated) reads "already set up".
        closed = user_access.administrator_exists(session)
        session.rollback()
        raise SetupClosedError(_SETUP_CLOSED) if closed else SetupTokenInvalidError(_TOKEN_INVALID)

    clean_name = required_text(display_name, "Name")
    login = users.canonical_login_name(login_name)
    role = users.require_role(session, role_id)
    chosen_role_id = role.id
    if not _eligible(session, chosen_role_id):
        raise _not_eligible(chosen_role_id)
    try:
        validate_new_password(password)
    except InvalidPasswordError as exc:
        raise InvalidInputError(str(exc)) from exc
    # Release the connection before scrypt.
    session.rollback()
    password_hash = password_hashing.hash_password(password)

    user_access.acquire_user_administration_lock(session)
    if user_access.administrator_exists(session):
        session.rollback()
        raise SetupClosedError(_SETUP_CLOSED)
    locked_role = session.get(
        Role, chosen_role_id, populate_existing=True, with_for_update={"read": True}
    )
    if locked_role is None:  # roles are never deleted; answered like any missing role
        session.rollback()
        raise InvalidInputError(f"Role {chosen_role_id} does not exist.")
    if not _eligible(session, chosen_role_id):
        session.rollback()
        raise _not_eligible(chosen_role_id)
    users.reject_duplicate_login(session, login)

    user = User(login_name=login, display_name=clean_name, role_id=chosen_role_id, is_active=True)
    session.add(user)
    flush(session, users.USER_CONFLICTS)
    now = session.scalar(select(func.now()))
    assert now is not None  # now() always answers
    credential = UserCredential(
        user_id=user.id,
        password_hash=password_hash,
        password_is_temporary=False,  # the person chose it
        password_changed_at=now,
        failed_attempts=0,
    )
    session.add(credential)
    session.flush()
    grant = authentication.open_first_session(session, user.id)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.USER,
        entity_id=str(user.id),
        before_data=None,
        after_data=users.profile_snapshot(user),
        metadata=dict(_SOURCE),
    )
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.USER,
        entity_id=str(user.id),
        before_data=authentication.password_snapshot(None),
        after_data=authentication.password_snapshot(credential),
        metadata=dict(_SOURCE),
    )
    commit(session, users.USER_CONFLICTS)
    gate.rotate()
    logger.info("First administrator created: user %s", grant.principal.user_id)
    return grant
