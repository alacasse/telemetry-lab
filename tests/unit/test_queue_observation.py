from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber

from demo.queue_observation import ATTRIBUTES, QueueObservation, local_queue_observation
from packages.config import Settings


def test_cache_failure_staleness_recovery_and_partial_attributes() -> None:
    now = [0.0]
    calls = []
    response = {attr: "2" for attr in ATTRIBUTES.values()}

    def read() -> dict[str, str]:
        calls.append(True)
        return response

    reader = QueueObservation(read, clock=lambda: now[0])
    first = reader.snapshot()
    assert first["quality"] == "approximate"
    assert first["counts"] == {"available": 2, "in_flight": 2, "delayed": 2}
    with ThreadPoolExecutor(max_workers=8) as pool:
        copies = list(pool.map(lambda _: reader.snapshot(), range(20)))
    assert len(calls) == 1
    assert all(s["observed_at"] == first["observed_at"] and s["cached"] for s in copies)
    response.clear()  # Partial data is not three zeroes.
    now[0] = 16
    failed = reader.snapshot()
    assert failed["quality"] == "unavailable"
    assert failed["counts"] == first["counts"]
    assert failed["observed_at"] == first["observed_at"]
    reader.snapshot()
    assert len(calls) == 2  # Failures cached too.
    now[0] = 31
    assert reader.snapshot()["freshness"] == "stale"
    response.update({attr: "0" for attr in ATTRIBUTES.values()})
    now[0] = 47
    recovered = reader.snapshot()
    assert recovered["quality"] == "approximate" and recovered["freshness"] == "fresh"
    assert recovered["counts"] == {"available": 0, "in_flight": 0, "delayed": 0}


def test_no_sample_is_unknown_and_errors_are_redacted() -> None:
    def fail() -> dict[str, str]:
        raise RuntimeError("private URL, token and receipt handle")

    proof = QueueObservation(fail).snapshot()
    assert proof["counts"] is None and proof["observed_at"] is None
    assert proof["quality"] == "unavailable" and proof["freshness"] == "unknown"
    assert "private" not in str(proof)


def test_sdk_observation_only_discovers_and_reads_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = boto3.client(
        "sqs", region_name="ca-central-1", endpoint_url="http://127.0.0.1:4566",
        aws_access_key_id="test", aws_secret_access_key="test",
    )
    url = "http://127.0.0.1:4566/000000000000/demo"
    with Stubber(client) as stub:
        stub.add_response("get_queue_url", {"QueueUrl": url}, {"QueueName": "demo"})
        stub.add_response("get_queue_attributes", {"Attributes": {
            attr: "3" for attr in ATTRIBUTES.values()
        }}, {"QueueUrl": url, "AttributeNames": list(ATTRIBUTES.values())})
        monkeypatch.setattr(boto3, "client", lambda *a, **kw: client)
        reader = local_queue_observation(Settings(
            _env_file=None, AWS_ENDPOINT_URL="http://127.0.0.1:4566", QUEUE_NAME="demo"
        ))
        assert reader.snapshot()["quality"] == "approximate"
        assert reader.snapshot()["cached"]
        stub.assert_no_pending_responses()
        # Any receive/create/delete/change-visibility call would fail the stub contract.


@pytest.mark.parametrize("values", [
    {"AWS_ENDPOINT_URL": None},
    {"AWS_ENDPOINT_URL": "https://sqs.ca-central-1.amazonaws.com"},
    {"AWS_ENDPOINT_URL": "http://127.0.0.1:4566", "QUEUE_BACKEND": "memory"},
])
def test_local_reader_refuses_nonlocal_configuration(values: dict[str, Any]) -> None:
    with pytest.raises(RuntimeError, match="local demo"):
        local_queue_observation(Settings(_env_file=None, **values))
