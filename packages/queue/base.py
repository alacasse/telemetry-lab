from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from packages.schemas.queue import QueueEnvelope


@dataclass(frozen=True)
class QueueDelivery:
    body: QueueEnvelope
    receipt_handle: str
    transport_message_id: str | None = None


class QueueClient(Protocol):
    def ensure_queue(self) -> None: ...

    def publish_message(self, message: QueueEnvelope) -> str: ...

    def receive_messages(
        self, max_messages: int = 10, wait_seconds: int = 1
    ) -> list[QueueDelivery]: ...

    def ack_message(self, receipt_handle: str) -> None: ...
