"""Local contention scheduling, deadlines and cleanup independent of PostgreSQL."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

from packages.thermal.authority import AuthorityLost, AuthorityStopping, PgAuthority


def authority_with_clock(clock: list[float] | None = None) -> PgAuthority:
    samples = clock if clock is not None else [0.0]
    authority = PgAuthority(MagicMock(), monotonic=lambda: samples[0])
    authority._confirmed = 0
    return authority


def wait_observer(authority: PgAuthority, names: list[str]) -> dict[str, threading.Event]:
    waiting = {name: threading.Event() for name in names}
    original = authority._admission_changed.wait

    def wait(timeout: float | None = None) -> bool:
        event = waiting.get(threading.current_thread().name)
        if event is not None:
            event.set()
        return original(timeout)

    authority._admission_changed.wait = wait  # type: ignore[method-assign]
    return waiting


def test_queued_renewal_precedes_business_already_waiting() -> None:
    authority = authority_with_clock()
    waiting = wait_observer(authority, ["business", "renewal"])
    order: list[str] = []
    errors: list[Exception] = []

    def run(name: str, renewal: bool) -> None:
        try:
            with authority._admission(renewal=renewal):
                order.append(name)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("business", False), name="business"),
        threading.Thread(target=run, args=("renewal", True), name="renewal"),
    ]
    with authority._admission():
        for thread in threads:
            thread.start()
            assert waiting[thread.name].wait(1)
        assert order == []
    for thread in threads:
        thread.join(1)
        assert not thread.is_alive()
    assert errors == []
    assert order == ["renewal", "business"]
    assert authority.active


@pytest.mark.parametrize("action", ["deadline", "fail", "stop"])
def test_queued_admission_cancels_before_checkout(action: str) -> None:
    clock = [0.0]
    authority = authority_with_clock(clock)
    waiting = wait_observer(authority, ["waiter"])
    sessions = MagicMock()
    errors: list[Exception] = []

    def run() -> None:
        try:
            with authority.transaction(sessions):
                raise AssertionError("Cancelled waiter entered a database transaction")
        except Exception as exc:
            errors.append(exc)

    with authority._admission():
        waiter = threading.Thread(target=run, name="waiter")
        waiter.start()
        assert waiting["waiter"].wait(1)
        if action == "deadline":
            clock[0] = 4.999
            assert authority.active
            clock[0] = 5.0
            with authority._admission_changed:
                authority._admission_changed.notify_all()
        elif action == "fail":
            authority.fail()
        else:
            authority.stop_admission()
        waiter.join(1)
        assert not waiter.is_alive()
        sessions.assert_not_called()
    assert len(errors) == 1
    assert isinstance(errors[0], AuthorityStopping if action == "stop" else AuthorityLost)
    assert authority.active is (action == "stop")


@pytest.mark.parametrize("outcome", ["commit", "rollback", "commit_failure"])
def test_admission_remains_held_through_transaction_exit(outcome: str) -> None:
    authority = authority_with_clock()
    sessions = MagicMock()
    session = sessions.return_value.__enter__.return_value
    waiting = wait_observer(authority, ["sibling"])
    admitted = threading.Event()
    errors: list[Exception] = []

    def sibling() -> None:
        try:
            with authority._admission():
                admitted.set()
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=sibling, name="sibling")

    def exiting(*args: object) -> None:
        thread.start()
        assert waiting["sibling"].wait(1)
        assert not admitted.is_set(), "Sibling entered before commit/rollback completed"
        if outcome == "commit_failure":
            raise SQLAlchemyError("Injected commit failure")

    session.begin.return_value.__exit__.side_effect = exiting
    expected: type[Exception] | None = {
        "commit": None,
        "rollback": ValueError,
        "commit_failure": SQLAlchemyError,
    }[outcome]

    def transact() -> None:
        with authority.transaction(sessions):
            if outcome == "rollback":
                raise ValueError("Domain rejection")

    with patch.object(authority, "guard"):
        if expected is None:
            transact()
        else:
            with pytest.raises(expected):
                transact()
    thread.join(1)
    assert not thread.is_alive()
    if outcome == "commit_failure":
        assert not admitted.is_set()
        assert len(errors) == 1 and isinstance(errors[0], AuthorityLost)
        assert not authority.active
    else:
        assert admitted.is_set() and errors == []
        assert authority.active


def test_renewal_keeps_admission_until_postcommit_confirmation() -> None:
    from datetime import UTC, datetime, timedelta

    from packages.thermal.models import ThermalAuthority

    committed = threading.Event()
    confirming = threading.Event()
    release = threading.Event()
    attempted = threading.Event()
    completed = threading.Event()
    errors: list[Exception] = []
    renewal_sessions = MagicMock()
    session = renewal_sessions.return_value.__enter__.return_value
    now = datetime.now(UTC)
    session.get.return_value = ThermalAuthority(
        resource="shared-room", generation=1, expires_at=now + timedelta(seconds=10)
    )
    session.scalar.return_value = now
    session.begin.return_value.__exit__.side_effect = lambda *args: committed.set()

    def monotonic() -> float:
        if committed.is_set() and threading.current_thread().name == "renewal-confirmation":
            confirming.set()
            assert release.wait(2)
        return 0

    authority = PgAuthority(renewal_sessions, monotonic=monotonic)
    authority._confirmed = 0
    business_sessions = MagicMock()

    def renew() -> None:
        try:
            authority.renew()
        except Exception as exc:
            errors.append(exc)

    def business() -> None:
        attempted.set()
        try:
            with authority.transaction(business_sessions):
                completed.set()
        except Exception as exc:
            errors.append(exc)

    renewer = threading.Thread(target=renew, name="renewal-confirmation")
    waiter = threading.Thread(target=business)
    with patch.object(authority, "guard"):
        renewer.start()
        try:
            assert confirming.wait(1)
            assert authority._transaction_running
            waiter.start()
            assert attempted.wait(1)
            business_sessions.assert_not_called()
            assert not completed.is_set()
        finally:
            release.set()
            renewer.join(1)
            if waiter.ident is not None:
                waiter.join(1)
    assert not renewer.is_alive() and not waiter.is_alive()
    assert errors == [] and completed.is_set()
    assert authority.active
