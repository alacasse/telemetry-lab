"""Bounded local SQS transport, deliberately separate from telemetry transport."""

from __future__ import annotations

import os
from typing import cast
from urllib.parse import urlparse

import boto3
from botocore.config import Config

from packages.config import Settings


class ThermalQueue:
    def __init__(self, settings: Settings) -> None:
        endpoint = urlparse(settings.aws_endpoint_url or "")
        if (
            settings.is_staging
            or settings.queue_backend != "sqs"
            or endpoint.scheme != "http"
            or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}
            or os.environ.get("TELEMETRY_LAB_LOCAL_DEMO") != "1"
        ):
            raise RuntimeError("Thermal commands require the local demo emulator")
        name = os.environ.get("THERMAL_QUEUE_NAME", "telemetry-lab-local-thermal-commands")
        if name == settings.queue_name or not name.startswith("telemetry-lab-local-"):
            raise ValueError("Thermal command queue must be a dedicated local queue")
        self.client = boto3.client(
            "sqs",
            region_name=settings.aws_region,
            endpoint_url=settings.aws_endpoint_url,
            aws_access_key_id="test",
            aws_secret_access_key="test",
            config=Config(connect_timeout=1, read_timeout=2, retries={"total_max_attempts": 1}),
        )
        self.url = self.client.get_queue_url(QueueName=name)["QueueUrl"]

    def publish(self, body: str) -> str:
        return str(self.client.send_message(QueueUrl=self.url, MessageBody=body)["MessageId"])

    def receive(self) -> list[dict]:
        return cast(
            list[dict],
            self.client.receive_message(
                QueueUrl=self.url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=1,
                VisibilityTimeout=3,
            ).get("Messages", []),
        )

    def acknowledge(self, receipt: str) -> None:
        self.client.delete_message(QueueUrl=self.url, ReceiptHandle=receipt)
