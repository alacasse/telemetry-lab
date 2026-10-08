"""Persistent fenced authority for the single shared room."""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from packages.config import Settings
from packages.thermal.models import ThermalAuthority

LEASE_SECONDS = 10
RENEW_SECONDS = 2
FAIL_CLOSED_SECONDS = 5


class AuthorityLost(RuntimeError):  # noqa: N818
    """This process can never admit further work."""


class AuthorityBusy(RuntimeError):  # noqa: N818
    """Another process owns a valid lease."""


class Authority(Protocol):
    def check_active(self) -> None: ...
    def guard(self, session: Session) -> None: ...
    def heartbeat(self, session: Session) -> None: ...
    def fail(self) -> None: ...


def create_authority_session_factory(settings: Settings) -> sessionmaker[Session]:
    url = settings.resolve_database_url()
    if not url.startswith("postgresql"):
        raise ValueError("Shared-room authority requires PostgreSQL")
    engine = create_engine(
        url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        connect_args={
            "connect_timeout": 2,
            "options": "-c lock_timeout=1000 -c statement_timeout=2000 "
            "-c idle_in_transaction_session_timeout=2000",
        },
    )
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class PgAuthority:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        owner_id: str | None = None,
        revision: str | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sessions = sessions
        self.owner_id = str(uuid.UUID(owner_id)) if owner_id else str(uuid.uuid4())
        self.revision = revision
        self.monotonic = monotonic
        self.generation: int | None = None
        self._confirmed: float | None = None
        self._failed = False
        self._state_lock = threading.Lock()

    @property
    def active(self) -> bool:
        try:
            self.check_active()
        except AuthorityLost:
            return False
        return True

    def fail(self) -> None:
        with self._state_lock:
            self._failed = True

    def check_active(self) -> None:
        with self._state_lock:
            if (
                self._failed
                or self._confirmed is None
                or self.monotonic() - self._confirmed >= FAIL_CLOSED_SECONDS
            ):
                self._failed = True
                raise AuthorityLost("Shared-room authority is permanently inactive")

    def _lock(self, session: Session) -> tuple[ThermalAuthority, datetime]:
        if session.get_bind().dialect.name != "postgresql":
            raise ValueError("Shared-room authority requires PostgreSQL")
        # A reused Session may retain an older owner's row. Locking alone does
        # not refresh SQLAlchemy's identity map. Never flush business work before
        # the authority lock, even if an injected session enables autoflush.
        with session.no_autoflush:
            row = session.scalar(
                select(ThermalAuthority)
                .where(ThermalAuthority.resource == "shared-room")
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise AuthorityLost("Permanent shared-room authority row is missing")
            now = session.scalar(select(func.clock_timestamp()))
        assert now is not None
        return row, now

    def acquire(self, session: Session) -> None:
        if self._failed:
            raise AuthorityLost("Shared-room authority is permanently inactive")
        try:
            row, now = self._lock(session)
            if row.owner_id is not None and row.expires_at is not None and row.expires_at > now:
                raise AuthorityBusy("Shared-room authority is already occupied")
            row.generation += 1
            row.owner_id = self.owner_id
            row.expires_at = now + timedelta(seconds=LEASE_SECONDS)
            row.renewed_at = now
            row.clock_passed_at = None
            row.pid = os.getpid()
            row.revision = self.revision
            # Preserve acquisition if this transaction subsequently checks its
            # authority again. Only this already-locked row may be flushed here.
            session.flush([row])
            self.generation = row.generation
            self._confirmed = self.monotonic()
        except AuthorityBusy:
            raise
        except Exception:
            self.fail()
            raise

    def guard(self, session: Session) -> None:
        self.check_active()
        try:
            row, now = self._lock(session)
            self.check_active()
            if (
                row.owner_id != self.owner_id
                or row.generation != self.generation
                or row.expires_at is None
                or row.expires_at <= now
            ):
                raise AuthorityLost("Shared-room lease expired or changed owner")
        except Exception:
            self.fail()
            raise

    def heartbeat(self, session: Session) -> None:
        # Caller already admitted this transaction with guard().
        row = session.get(ThermalAuthority, "shared-room")
        assert row is not None
        row.clock_passed_at = session.scalar(select(func.clock_timestamp()))

    def renew(self) -> None:
        self.check_active()
        try:
            with self.sessions() as session, session.begin():
                self.guard(session)
                row = session.get(ThermalAuthority, "shared-room")
                assert row is not None
                now = session.scalar(select(func.clock_timestamp()))
                assert now is not None
                self.check_active()
                if row.expires_at is None or row.expires_at <= now:
                    raise AuthorityLost("Expired shared-room lease cannot be renewed")
                row.expires_at = now + timedelta(seconds=LEASE_SECONDS)
                row.renewed_at = now
            # Do not let a suspended renewal revive an already failed process.
            with self._state_lock:
                confirmed = self.monotonic()
                if (
                    self._failed
                    or self._confirmed is None
                    or confirmed - self._confirmed >= FAIL_CLOSED_SECONDS
                ):
                    self._failed = True
                    raise AuthorityLost("Shared-room authority is permanently inactive")
                self._confirmed = confirmed
        except Exception:
            self.fail()
            raise

    def release(self, session: Session) -> None:
        self.guard(session)
        row = session.get(ThermalAuthority, "shared-room")
        assert row is not None
        row.owner_id = None
        row.expires_at = None
        row.pid = None


def observe_authority(session: Session) -> dict:
    """Passive MVCC observation, never acquires authority or simulation locks."""
    try:
        row = session.scalar(
            select(ThermalAuthority).where(ThermalAuthority.resource == "shared-room")
        )
        now = session.scalar(select(func.clock_timestamp()))
        assert now is not None
        available = bool(
            row
            and row.owner_id
            and row.expires_at
            and row.expires_at > now
            and row.clock_passed_at
            and (now - row.clock_passed_at).total_seconds() <= 3
        )
        return {
            "status": "available" if available else "unavailable",
            "owner_id": row.owner_id if row else None,
            "generation": row.generation if row else None,
            "expires_at": row.expires_at.isoformat() if row and row.expires_at else None,
            "renewed_at": row.renewed_at.isoformat() if row and row.renewed_at else None,
            "clock_passed_at": row.clock_passed_at.isoformat()
            if row and row.clock_passed_at
            else None,
            "pid": row.pid if row else None,
            "revision": row.revision if row else None,
            "process_id": row.pid if row else None,
            "release_revision": row.revision if row else None,
            "source": "postgresql-authority",
            "authority_free": not bool(
                row and row.owner_id and row.expires_at and row.expires_at > now
            ),
        }
    except SQLAlchemyError:
        return unknown_observation()


def unknown_observation() -> dict:
    return {
        "status": "unknown",
        "owner_id": None,
        "generation": None,
        "expires_at": None,
        "renewed_at": None,
        "clock_passed_at": None,
        "pid": None,
        "revision": None,
        "process_id": None,
        "release_revision": None,
        "source": "postgresql-authority",
        "authority_free": None,
    }
