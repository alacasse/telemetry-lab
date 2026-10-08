from __future__ import annotations

from packages.config import Settings
from packages.queue.base import QueueClient
from packages.queue.memory import InMemoryQueueClient
from packages.queue.sqs import SQSQueueClient


def get_queue_client(settings: Settings) -> QueueClient:
    if settings.queue_backend == "inmemory":
        return InMemoryQueueClient()
    return SQSQueueClient(settings)
