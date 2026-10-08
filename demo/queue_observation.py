"""Passive local queue sampling. This module cannot publish, receive or ack messages."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.config import Config

from packages.config import Settings

ATTRIBUTES = {
    "available": "ApproximateNumberOfMessages",
    "in_flight": "ApproximateNumberOfMessagesNotVisible",
    "delayed": "ApproximateNumberOfMessagesDelayed",
}
CACHE_SECONDS = 15
STALE_SECONDS = 30


class QueueObservation:
    def __init__(
        self, read: Callable[[], dict[str, str]], clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._read = read
        self._clock = clock
        self._lock = Lock()
        self._next_read = 0.0
        self._sample_time: float | None = None
        self._observed_at: datetime | None = None
        self._attempted_at: datetime | None = None
        self._counts: dict[str, int] | None = None
        self._available = False

    def snapshot(self) -> dict[str, Any]:
        # Coalesce concurrent requests, and cache failures too (no retry storm).
        with self._lock:
            cached = self._clock() < self._next_read
            if not cached:
                self._attempted_at = datetime.now(UTC)
                try:
                    attributes = self._read()
                    counts = {key: int(attributes[attr]) for key, attr in ATTRIBUTES.items()}
                    if any(value < 0 for value in counts.values()):
                        raise ValueError("Invalid counter")
                    self._counts = counts
                    self._observed_at = datetime.now(UTC)
                    self._sample_time = self._clock()
                    self._available = True
                except Exception:
                    # Retain only the dated last sample; never leak SDK errors/URLs.
                    self._available = False
                self._next_read = self._clock() + CACHE_SECONDS
            age = (
                max(0.0, self._clock() - self._sample_time)
                if self._sample_time is not None
                else None
            )
            return {
                "source": "SQS.GetQueueAttributes",
                "environment": "local",
                "runtime": "sqs-compatible-emulator",
                "scope": "queue",
                "quality": "approximate" if self._available else "unavailable",
                "sample_quality": "approximate" if self._counts is not None else "unknown",
                "observed_at": self._observed_at,
                "attempted_at": self._attempted_at,
                "fetched_at": datetime.now(UTC),
                "age_seconds": age,
                "freshness": "unknown" if age is None else (
                    "stale" if age >= STALE_SECONDS else "fresh"
                ),
                "stale_after_seconds": STALE_SECONDS,
                "cache_seconds": CACHE_SECONDS,
                "cached": cached,
                "counts": dict(self._counts) if self._counts is not None else None,
            }


def local_queue_observation(settings: Settings) -> QueueObservation:
    endpoint = urlparse(settings.aws_endpoint_url or "")
    if (
        settings.is_staging or settings.queue_backend != "sqs"
        or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
        or endpoint.scheme != "http"
    ):
        raise RuntimeError("Queue observation requires the local demo emulator")
    client = boto3.client(
        "sqs", region_name=settings.aws_region, endpoint_url=settings.aws_endpoint_url,
        aws_access_key_id="test", aws_secret_access_key="test",
        config=Config(connect_timeout=1, read_timeout=2, retries={"total_max_attempts": 1}),
    )

    def read() -> dict[str, str]:
        # Never use SQSQueueClient.queue_url: its local discovery may CREATE a queue.
        url = client.get_queue_url(QueueName=settings.queue_name)["QueueUrl"]
        return dict(client.get_queue_attributes(
            QueueUrl=url, AttributeNames=list(ATTRIBUTES.values())
        )["Attributes"])

    return QueueObservation(read)
