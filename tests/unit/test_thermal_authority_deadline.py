"""Deterministic local deadline checks independent of PostgreSQL fencing tests."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from packages.thermal.authority import AuthorityLost, PgAuthority
from packages.thermal.models import ThermalAuthority


def test_suspension_during_postcommit_confirmation_cannot_refresh_deadline() -> None:
    sessions = MagicMock()
    session = sessions.return_value.__enter__.return_value
    now = datetime.now(UTC)
    session.get.return_value = ThermalAuthority(
        resource="shared-room", generation=1, expires_at=now + timedelta(seconds=10)
    )
    session.scalar.return_value = now
    postcommit = [False]
    samples = [0]

    def committed(*args: object) -> None:
        postcommit[0] = True

    session.begin.return_value.__exit__.side_effect = committed

    def monotonic() -> float:
        if not postcommit[0]:
            return 0
        samples[0] += 1
        # Suspension occurs after the first postcommit sample. A second sample
        # must never replace the original confirmation without checking its age.
        return 0 if samples[0] == 1 else 5

    authority = PgAuthority(sessions, monotonic=monotonic)
    authority._confirmed = 0
    # Database fencing is exercised by PostgreSQL tests.
    with patch.object(authority, "guard"):
        authority.renew()
    with pytest.raises(AuthorityLost):
        authority.check_active()
    assert not authority.active
