from __future__ import annotations

import json
import os

import boto3
from botocore.exceptions import ClientError

from packages.config import Settings
from packages.queue.base import QueueClient, QueueDelivery
from packages.schemas.queue import QueueEnvelope


class SQSQueueClient(QueueClient):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        client_kwargs = {
            "region_name": settings.aws_region,
            "endpoint_url": settings.aws_endpoint_url,
        }

        if settings.aws_endpoint_url is not None:
            client_kwargs["aws_access_key_id"] = os.getenv("AWS_ACCESS_KEY_ID", "test")
            client_kwargs["aws_secret_access_key"] = os.getenv("AWS_SECRET_ACCESS_KEY", "test")
            session_token = os.getenv("AWS_SESSION_TOKEN")
            if session_token:
                client_kwargs["aws_session_token"] = session_token

        self.client = boto3.client("sqs", **client_kwargs)
        self._queue_url: str | None = settings.queue_url

    def ensure_queue(self) -> None:
        if self._queue_url is not None:
            return

        if self.settings.should_autocreate_queue:
            response = self.client.create_queue(QueueName=self.settings.queue_name)
            self._queue_url = response["QueueUrl"]
            return

        try:
            response = self.client.get_queue_url(QueueName=self.settings.queue_name)
        except self.client.exceptions.QueueDoesNotExist as exc:
            raise RuntimeError(f"SQS queue '{self.settings.queue_name}' does not exist") from exc
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code == "AWS.SimpleQueueService.NonExistentQueue":
                raise RuntimeError(
                    f"SQS queue '{self.settings.queue_name}' does not exist"
                ) from exc
            raise

        self._queue_url = response["QueueUrl"]

    @property
    def queue_url(self) -> str:
        self.ensure_queue()
        assert self._queue_url is not None
        return self._queue_url

    def publish_message(self, message: QueueEnvelope) -> str:
        response = self.client.send_message(
            QueueUrl=self.queue_url,
            MessageBody=message.model_dump_json(),
        )
        return str(response["MessageId"])

    def receive_messages(
        self, max_messages: int = 10, wait_seconds: int = 1
    ) -> list[QueueDelivery]:
        response = self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_seconds,
        )
        deliveries: list[QueueDelivery] = []
        for item in response.get("Messages", []):
            deliveries.append(
                QueueDelivery(
                    body=QueueEnvelope.model_validate(json.loads(item["Body"])),
                    receipt_handle=item["ReceiptHandle"],
                    transport_message_id=item["MessageId"],
                )
            )
        return deliveries

    def ack_message(self, receipt_handle: str) -> None:
        self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)
