"""Real production adapter roundtrip on a private LocalStack queue."""
from __future__ import annotations

import os
import time
from dataclasses import replace
from uuid import uuid4


def test_adapter_preserves_exact_body_and_redelivery() -> None:
    if os.getenv("THERMAL_CONTAINER_VALIDATION") != "1":
        import pytest

        pytest.skip("requires disposable thermal-engine Compose validation")
    from thermal_engine.config import EngineConfig
    from thermal_engine.transports import ThermalQueue

    config = EngineConfig.from_env()
    original = ThermalQueue(config)
    url = original.client.create_queue(QueueName=f"adapter-{uuid4().hex}")["QueueUrl"]
    transport = ThermalQueue(replace(config, queue_url=url))
    body = '{ "simulation_id": "' + str(uuid4()) + '", "sequence": 1, ' \
           '"command_type": "heating.start" }'
    try:
        identity = transport.publish(body)
        deadline = time.monotonic() + 12
        first: list[dict] = []
        while time.monotonic() < deadline and not first:
            first = transport.receive()
        assert len(first) == 1
        assert first[0]["Body"] == body
        assert first[0]["MessageId"] == identity
        # No acknowledgement simulates loss after application commit.
        deadline = time.monotonic() + 12
        redelivered: list[dict] = []
        while time.monotonic() < deadline and not redelivered:
            redelivered = transport.receive()
        assert len(redelivered) == 1
        assert redelivered[0]["Body"] == body
        assert redelivered[0]["MessageId"] == identity
        assert redelivered[0]["ReceiptHandle"] != first[0]["ReceiptHandle"]
        transport.acknowledge(redelivered[0]["ReceiptHandle"])
        assert transport.receive() == []
    finally:
        original.client.delete_queue(QueueUrl=url)


if __name__ == "__main__":
    test_adapter_preserves_exact_body_and_redelivery()
