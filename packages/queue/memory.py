from __future__ import annotations

from collections import deque
from uuid import uuid4

from packages.queue.base import QueueClient, QueueDelivery
from packages.schemas.queue import QueueEnvelope


class InMemoryQueueClient(QueueClient):
    def __init__(self) -> None:
        self._messages: deque[QueueDelivery] = deque()

    def ensure_queue(self) -> None:
        return None

    def publish_message(self, message: QueueEnvelope) -> str:
        receipt_handle = str(uuid4())
        transport_id = str(uuid4())
        self._messages.append(
            QueueDelivery(
                body=message, receipt_handle=receipt_handle, transport_message_id=transport_id
            )
        )
        return transport_id

    def receive_messages(
        self, max_messages: int = 10, wait_seconds: int = 1
    ) -> list[QueueDelivery]:
        deliveries: list[QueueDelivery] = []
        for _ in range(min(max_messages, len(self._messages))):
            deliveries.append(self._messages.popleft())
        return deliveries

    def ack_message(self, receipt_handle: str) -> None:
        return None
