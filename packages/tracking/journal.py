"""Best-effort evidence, separate from business outcome and queue acknowledgement."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from packages.config import Settings
from packages.db.models import ProcessingObservation
from packages.schemas.queue import QueueEnvelope


@dataclass(frozen=True)
class Attempt:
    message: QueueEnvelope
    settings: Settings
    transport_message_id: str | None = None
    attempt_id: str = field(default_factory=lambda: str(uuid4()))
    started: float = field(default_factory=monotonic)

    def observation(self, kind: str, original_event_id: str | None) -> ProcessingObservation:
        return ProcessingObservation(
            observation_id=str(uuid4()),
            attempt_id=self.attempt_id,
            correlation_id=self.message.correlation_id,
            event_id=self.message.payload.event_id,
            envelope_id=self.message.message_id,
            transport_message_id=self.transport_message_id,
            original_event_id=original_event_id,
            kind=kind,
            observed_at=datetime.now(UTC),
            runtime="local-python-process" if not self.settings.is_staging else "unknown",
            process_id=os.getpid(),
            release_revision=self.settings.release_revision,
            elapsed_ms=(monotonic() - self.started) * 1000 if kind != "started" else None,
        )


def append_observation(
    session: Session,
    attempt: Attempt,
    kind: str,
    original_event_id: str | None = None,
) -> None:
    session.add(attempt.observation(kind, original_event_id))
    session.flush()


def observe_independently(
    sessions: sessionmaker[Session],
    attempt: Attempt,
    kind: str,
) -> bool:
    try:
        with sessions.begin() as session:
            append_observation(session, attempt, kind)
        return True
    except Exception:
        # Missing evidence is unknown. Never reinterpret the business result or raise here.
        return False


def observe_result(
    session: Session,
    attempt: Attempt | None,
    kind: str,
    original_event_id: str,
) -> bool:
    if attempt is None:
        return False
    # Flush business writes BEFORE the savepoint: business errors must propagate.
    session.flush()
    try:
        with session.begin_nested():
            append_observation(session, attempt, kind, original_event_id)
        return True
    except Exception:
        # A journal constraint/table failure rolls back only the savepoint. If the whole
        # connection failed, the outer commit still fails and delivery remains retryable.
        return False
