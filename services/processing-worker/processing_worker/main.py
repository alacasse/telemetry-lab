from __future__ import annotations

import time

from sqlalchemy.orm import Session, sessionmaker

from packages.aws import configure_tracing
from packages.config import Settings, get_settings
from packages.db.session import create_session_factory
from packages.logging import configure_logging, get_logger
from packages.queue import get_queue_client
from packages.queue.base import QueueClient
from processing_worker.processor import process_delivery


def run_worker_iteration(
    queue_client: QueueClient,
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> int:
    logger = get_logger("processing-worker")
    deliveries = queue_client.receive_messages(
        max_messages=settings.worker_max_messages,
        wait_seconds=settings.worker_receive_wait_seconds,
    )
    for delivery in deliveries:
        result = process_delivery(delivery, session_factory, settings)
        if result.acknowledged:
            queue_client.ack_message(delivery.receipt_handle)
        logger.info("worker_delivery_completed", extra={"outcome": result.outcome})
    return len(deliveries)


def main() -> None:
    settings = get_settings()
    settings.require_staging_runtime_env_vars("APP_ENV", "DATABASE_SECRET_ARN", "QUEUE_BACKEND")
    configure_tracing("processing-worker", settings)
    configure_logging("processing-worker", settings.log_level)
    logger = get_logger("processing-worker")
    queue_client = get_queue_client(settings)
    queue_client.ensure_queue()
    session_factory = create_session_factory(settings)
    idle_sleep_seconds = settings.worker_idle_sleep_seconds

    logger.info("worker_started", extra={"outcome": "started"})
    while True:
        delivery_count = run_worker_iteration(queue_client, session_factory, settings)
        if delivery_count == 0 and idle_sleep_seconds > 0:
            time.sleep(idle_sleep_seconds)


if __name__ == "__main__":
    main()
