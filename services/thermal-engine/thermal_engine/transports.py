"""Bounded HTTP and SQS adapters, independent of the local demo."""

from __future__ import annotations

from typing import Any, cast

import boto3
import httpx
from botocore.config import Config

from thermal_engine.config import EngineConfig


class ThermalQueue:
    def __init__(self, config: EngineConfig) -> None:
        kwargs: dict[str, Any] = {
            "region_name": config.settings.aws_region,
            "config": Config(
                connect_timeout=1, read_timeout=2, retries={"total_max_attempts": 1},
                ignore_configured_endpoint_urls=config.transport_mode == "aws",
            ),
        }
        if config.transport_mode == "local-emulator":
            kwargs["endpoint_url"] = config.settings.aws_endpoint_url
        # Credentials always come from the SDK provider chain, including explicit
        # synthetic environment credentials in local-emulator mode.
        self.client = boto3.client("sqs", **kwargs)
        self.url = config.queue_url

    def publish(self, body: str) -> str:
        return str(self.client.send_message(QueueUrl=self.url, MessageBody=body)["MessageId"])

    def receive(self) -> list[dict]:
        return cast(list[dict], self.client.receive_message(
            QueueUrl=self.url, MaxNumberOfMessages=1, WaitTimeSeconds=1, VisibilityTimeout=3,
        ).get("Messages", []))

    def acknowledge(self, receipt: str) -> None:
        self.client.delete_message(QueueUrl=self.url, ReceiptHandle=receipt)


def publish_reading(
    client: httpx.Client, url: str, body: str, simulation_id: str, *, token: str | None = None,
) -> dict:
    headers = {"X-Correlation-Id": simulation_id, "Content-Type": "application/json"}
    if token is not None:
        headers["X-Staging-Token"] = token
    response = client.post(url, content=body.encode("utf-8"), headers=headers)
    response.raise_for_status()
    try:
        receipt = response.json()
    except ValueError:
        raise httpx.DecodingError("Ingestion receipt must be valid JSON") from None
    if not isinstance(receipt, dict):
        raise httpx.DecodingError("Ingestion receipt must be a JSON object")
    return receipt
