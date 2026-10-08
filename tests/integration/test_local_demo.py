"""Opt-in real HTTP/LocalStack/PostgreSQL tests with distinct API and worker processes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select, text

from packages.db.models import HvacDecision, TelemetryEvent

BASE = os.getenv("TELEMETRY_LAB_DEMO_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="Run scripts/demo-local.py --test")


def send(client: httpx.Client) -> dict:
    correlation = str(uuid4())
    response = client.post(
        "/ingestion/telemetry",
        headers={"X-Correlation-ID": correlation},
        json={
            "building_id": "demo-" + correlation,
            "zone_id": "synthetic",
            "timestamp": datetime.now(UTC).isoformat(),
            "temperature_c": 23,
            "humidity_pct": 45,
            "occupancy": 8,
            "co2_ppm": 1400,
            "hvac_mode": "ventilation",
            "airflow_pct": 40,
        },
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


def trace(client: httpx.Client, receipt: dict) -> dict:
    response = client.get("/query/simulations/" + receipt["correlation_id"])
    assert response.status_code == 200, response.text
    return dict(response.json())


def wait_result(client: httpx.Client, receipt: dict) -> dict:
    for _ in range(100):
        result = trace(client, receipt)
        if result["results"]:
            return result
        time.sleep(0.1)
    pytest.fail("No persisted result within 10 seconds")


def test_real_transport_interrupt_before_commit_and_missing_journal(tmp_path: Path) -> None:
    assert BASE
    engine = create_engine(os.environ["TELEMETRY_LAB_TEST_POSTGRES_URL"])
    worker_pid = int(os.environ["TELEMETRY_LAB_DEMO_WORKER_PID"])
    with httpx.Client(base_url=BASE, timeout=10) as client:
        receipt = send(client)
        result = wait_result(client, receipt)
        business = result["results"][0]
        decision = business["decisions"][0]
        assert decision["decision_type"] == "increase_ventilation"
        assert decision["recommended_airflow_pct"] == 55
        assert decision["applied"] is False
        assert business["event_id"] == receipt["event_id"]
        assert (
            len(
                {
                    receipt["event_id"],
                    receipt["envelope_id"],
                    receipt["transport_receipt"]["message_id"],
                }
            )
            == 3
        )
        assert receipt["transport_receipt"]["runtime"] == "sqs-compatible-emulator"
        assert [o["kind"] for o in result["observations"]] == ["started", "processed"]
        for observation in result["observations"]:
            assert observation["transport_message_id"] == receipt["transport_receipt"]["message_id"]
            assert observation["process_id"] == worker_pid
            assert observation["process_id"] != os.getpid()
            assert observation["runtime"] == "local-python-process"
        assert trace(client, receipt)["results"][0]["decisions"][0] == decision
        unknown = client.get("/query/simulations/" + str(uuid4())).json()
        assert unknown["result_quality"] == "unknown"
        assert unknown["observations"] == unknown["results"] == []
        targeted = client.get(
            "/query/simulations/" + receipt["correlation_id"], params={"event_id": str(uuid4())}
        ).json()
        assert targeted["results"] == targeted["observations"] == []
        assert client.get("/query/simulations/x", params={"event_id": "x" * 65}).status_code == 422

        # Stop the ordinary consumer, then SIGKILL a separate consumer after its business
        # and terminal journal writes have flushed, but BEFORE its outer commit.
        marker = tmp_path / "before-commit"
        os.kill(worker_pid, signal.SIGSTOP)
        child = None
        interrupted_receipt = send(client)
        code = """
import time
from pathlib import Path
from packages.config import get_settings
from packages.db.session import create_session_factory
from packages.queue.sqs import SQSQueueClient
from processing_worker import processor
from processing_worker.main import run_worker_iteration
original = processor.observe_result
def pause(*args, **kwargs):
    result = original(*args, **kwargs)
    Path(MARKER).write_text('flushed, not committed')
    time.sleep(60)
    return result
processor.observe_result = pause
settings = get_settings()
q = SQSQueueClient(settings)
sessions = create_session_factory(settings)
while True:
    run_worker_iteration(q, sessions, settings)
""".replace("MARKER", repr(str(marker)))
        try:
            child = subprocess.Popen([sys.executable, "-c", code], cwd=tmp_path)
            for _ in range(150):
                if marker.exists():
                    break
                assert child.poll() is None
                time.sleep(0.1)
            assert marker.exists(), "Interrupt worker did not reach the pre-commit barrier"
            before = trace(client, interrupted_receipt)
            assert before["results"] == []
            assert [o["kind"] for o in before["observations"]] == ["started"]
            with engine.connect() as connection:
                for model in (TelemetryEvent, HvacDecision):
                    assert (
                        connection.execute(
                            select(model).where(model.event_id == interrupted_receipt["event_id"])
                        ).first()
                        is None
                    )
            child.kill()
            child.wait(timeout=5)
            after = trace(client, interrupted_receipt)
            assert after["results"] == []
            assert [o["kind"] for o in after["observations"]] == ["started"]
        finally:
            if child and child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            os.kill(worker_pid, signal.SIGCONT)
        recovered = wait_result(client, interrupted_receipt)
        attempts = {o["attempt_id"] for o in recovered["observations"]}
        assert len(attempts) >= 2
        assert recovered["results"][0]["decisions"][0]["recommended_airflow_pct"] == 55

        # A missing journal table must neither roll back a valid business transaction
        # nor hide its result. Reads fail independently and are explicitly unavailable.
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE processing_observations RENAME TO journal_saved"))
        try:
            no_journal_receipt = send(client)
            no_journal = wait_result(client, no_journal_receipt)
            assert no_journal["journal_quality"] == "unavailable"
            assert no_journal["observations"] == []
            assert no_journal["results"][0]["decisions"][0]["recommended_airflow_pct"] == 55
            # Past the queue's visibility timeout: a journal error must not cause replay.
            time.sleep(4)
            with engine.connect() as connection:
                assert (
                    len(
                        connection.execute(
                            select(HvacDecision).where(
                                HvacDecision.event_id == no_journal_receipt["event_id"]
                            )
                        ).all()
                    )
                    == 1
                )
        finally:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE journal_saved RENAME TO processing_observations")
                )
        time.sleep(4)
        assert trace(client, no_journal_receipt)["observations"] == []

        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE telemetry_events RENAME TO events_saved"))
        try:
            response = client.get("/query/simulations/" + receipt["correlation_id"])
            assert response.status_code == 503
            assert response.json() == {"detail": "trace_unavailable"}
        finally:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE events_saved RENAME TO telemetry_events"))
        assert wait_result(client, receipt)["results"][0]["decisions"][0] == decision
    engine.dispose()


def test_exact_post_duplicate_redelivery_after_commit_and_missing_evidence(tmp_path: Path) -> None:
    import json

    assert BASE
    engine = create_engine(os.environ["TELEMETRY_LAB_TEST_POSTGRES_URL"])
    worker_pid = int(os.environ["TELEMETRY_LAB_DEMO_WORKER_PID"])
    correlation = str(uuid4())
    body = json.dumps(
        {
            "building_id": "demo-" + correlation,
            "zone_id": "synthetic",
            "timestamp": datetime.now(UTC).isoformat(),
            "temperature_c": 23,
            "humidity_pct": 45,
            "occupancy": 8,
            "co2_ppm": 1400,
            "hvac_mode": "ventilation",
            "airflow_pct": 40,
        },
        indent=2,
    )
    with httpx.Client(base_url=BASE, timeout=10) as client:

        def post() -> dict:
            response = client.post(
                "/ingestion/telemetry",
                content=body,
                headers={"X-Correlation-ID": correlation, "Content-Type": "application/json"},
            )
            assert response.status_code == 202
            assert response.request.content.decode() == body
            return dict(response.json())

        first = post()
        initial = wait_result(client, first)
        original = initial["results"][0]
        time.sleep(0.2)  # Let the ordinary worker finish its acknowledgement before SIGSTOP.
        os.kill(worker_pid, signal.SIGSTOP)
        child = None
        marker = tmp_path / "duplicate-before-commit"
        release = tmp_path / "commit-allowed"
        second = post()
        for field in ("event_id", "envelope_id"):
            assert first[field] != second[field]
        assert first["transport_receipt"]["message_id"] != second["transport_receipt"]["message_id"]
        assert first["correlation_id"] == second["correlation_id"]
        code = (
            """
import time
from pathlib import Path
from packages.config import get_settings
from packages.db.session import create_session_factory
from packages.queue.sqs import SQSQueueClient
from processing_worker import processor
original = processor.observe_result
def pause(*args, **kwargs):
    result = original(*args, **kwargs)
    Path(MARKER).write_text('duplicate flushed, not committed')
    while not Path(RELEASE).exists():
        time.sleep(0.02)
    return result
processor.observe_result = pause
settings = get_settings()
q = SQSQueueClient(settings)
sessions = create_session_factory(settings)
for _ in range(15):
    deliveries = q.receive_messages(1, 1)
    if deliveries:
        delivery = deliveries[0]
        assert delivery.body.payload.event_id == EXPECTED
        result = processor.process_delivery(delivery, sessions, settings)
        assert result.outcome == 'duplicate' and result.acknowledged
        break  # Deliberately omit DeleteMessage AFTER commit, to cause actual SQS redelivery.
else:
    raise RuntimeError('No SQS delivery')
""".replace("MARKER", repr(str(marker)))
            .replace("RELEASE", repr(str(release)))
            .replace("EXPECTED", repr(second["event_id"]))
        )
        try:
            child = subprocess.Popen([sys.executable, "-c", code], cwd=tmp_path)
            for _ in range(150):
                if marker.exists():
                    break
                assert child.poll() is None
                time.sleep(0.1)
            assert marker.exists()
            before = trace(client, second)
            pending = [o for o in before["observations"] if o["event_id"] == second["event_id"]]
            assert [o["kind"] for o in pending] == ["started"]
            assert before["results"][0]["decisions"] == original["decisions"]
            release.write_text("commit")
            assert child.wait(timeout=5) == 0
            committed = trace(client, second)
            assert len([o for o in committed["observations"] if o["kind"] == "duplicate"]) == 1
        finally:
            if child and child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            os.kill(worker_pid, signal.SIGCONT)
        for _ in range(100):
            proof = trace(client, second)
            duplicates = [o for o in proof["observations"] if o["kind"] == "duplicate"]
            if len(duplicates) >= 2:
                break
            time.sleep(0.1)
        assert len(duplicates) == 2
        assert len({o["attempt_id"] for o in duplicates}) == 2
        for observation in duplicates:
            assert observation["event_id"] == second["event_id"]
            assert observation["envelope_id"] == second["envelope_id"]
            assert observation["transport_message_id"] == second["transport_receipt"]["message_id"]
            assert observation["original_event_id"] == first["event_id"]
        send_proof = next(s for s in proof["sends"] if s["event_id"] == second["event_id"])
        assert [a["delivery"] for a in send_proof["attempts"]] == ["first_observed", "redelivery"]
        assert proof["results"][0]["decisions"] == original["decisions"]
        targeted = client.get(
            "/query/simulations/" + correlation, params={"event_id": second["event_id"]}
        ).json()
        assert targeted["results"][0]["event_id"] == first["event_id"]
        assert len(targeted["sends"]) == 1

        # No duplicate terminal observation: the original remains readable, but is NOT
        # evidence that this third POST was processed. No fabricated link from normalized data.
        time.sleep(0.2)
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE processing_observations RENAME TO journal_saved"))
        try:
            third = post()
            time.sleep(4)
            missing = trace(client, third)
            assert missing["journal_quality"] == "unavailable"
            assert missing["sends"] == missing["observations"] == []
            assert missing["results"][0]["decisions"] == original["decisions"]
            targeted_missing = client.get(
                "/query/simulations/" + correlation, params={"event_id": third["event_id"]}
            ).json()
            assert targeted_missing["results"] == []
        finally:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE journal_saved RENAME TO processing_observations")
                )
        time.sleep(4)
        final = trace(client, third)
        assert not any(o["event_id"] == third["event_id"] for o in final["observations"])
        with engine.connect() as connection:
            events = (
                connection.execute(
                    select(TelemetryEvent.event_id).where(
                        TelemetryEvent.building_id == "demo-" + correlation
                    )
                )
                .scalars()
                .all()
            )
            decisions = (
                connection.execute(
                    select(HvacDecision.decision_id).where(
                        HvacDecision.event_id.in_(
                            [first["event_id"], second["event_id"], third["event_id"]]
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert events == [first["event_id"]]
            assert decisions == [original["decisions"][0]["decision_id"]]
        # Optional local evidence output supplied only by the disposable launcher.
        evidence = os.getenv("TELEMETRY_LAB_DEMO_EVIDENCE")
        if evidence:
            Path(evidence).write_text(
                json.dumps(
                    {
                        "initial_receipt": first,
                        "resend_receipt": second,
                        "trace_after_redelivery": proof,
                        "missing_journal_trace": missing,
                        "targeted_missing_journal_trace": targeted_missing,
                        "database_event_ids": events,
                        "database_decision_ids": decisions,
                    },
                    indent=2,
                )
                + "\n"
            )
    engine.dispose()


def test_sequential_scenario_and_passive_queue_observation() -> None:
    import hashlib
    import json
    from datetime import timedelta

    from packages.config import get_settings
    from packages.queue.sqs import SQSQueueClient

    assert BASE
    worker_pid = int(os.environ["TELEMETRY_LAB_DEMO_WORKER_PID"])
    correlation = str(uuid4())
    timestamp = datetime.now(UTC)
    bodies = [json.dumps({
        "building_id": "demo-" + correlation, "zone_id": "synthetic",
        "timestamp": (timestamp + timedelta(milliseconds=i)).isoformat(),
        "temperature_c": 23, "humidity_pct": 45, "occupancy": 8,
        "co2_ppm": co2, "hvac_mode": "ventilation", "airflow_pct": 40,
    }, indent=2) for i, co2 in enumerate((700, 1400))]
    queue = SQSQueueClient(get_settings())
    with httpx.Client(base_url=BASE, timeout=10) as client:
        def post(body: str) -> dict:
            response = client.post("/ingestion/telemetry", content=body, headers={
                "Content-Type": "application/json", "X-Correlation-ID": correlation,
            })
            assert response.status_code == 202
            assert response.request.content.decode() == body
            return dict(response.json())

        def wait_event(receipt: dict) -> dict:
            for _ in range(100):
                proof = trace(client, receipt)
                if any(r["event_id"] == receipt["event_id"] for r in proof["results"]):
                    return proof
                time.sleep(0.1)
            pytest.fail("Expected event not persisted")

        # The observer gets a real queued message while the real worker is paused.
        os.kill(worker_pid, signal.SIGSTOP)
        try:
            time.sleep(1.2)  # Allow the already-issued long poll to expire before publishing.
            first = post(bodies[0])
            samples = [client.get("/queue-observation").json() for _ in range(3)]
            for sample in samples:
                assert sample["source"] == "SQS.GetQueueAttributes"
                assert sample["quality"] == "approximate" and sample["scope"] == "queue"
                assert sample["environment"] == "local"
                assert all(v >= 0 for v in sample["counts"].values())
                assert "QueueUrl" not in sample and "ReceiptHandle" not in sample
            assert samples[1]["cached"] and samples[2]["cached"]
            assert samples[0]["observed_at"] == samples[2]["observed_at"]
            assert trace(client, first)["results"] == []
            # Only THIS TEST consumes, after passive HTTP reads. Count=1 and unchanged
            # message identity/body prove those reads didn't receive/delete the message.
            delivery = queue.client.receive_message(
                QueueUrl=queue.queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=1,
                AttributeNames=["ApproximateReceiveCount"],
            )["Messages"][0]
            assert delivery["MessageId"] == first["transport_receipt"]["message_id"]
            assert delivery["Attributes"]["ApproximateReceiveCount"] == "1"
            envelope = json.loads(delivery["Body"])
            assert envelope["payload"]["event_id"] == first["event_id"]
            assert envelope["payload"]["co2_ppm"] == 700
            queue.client.change_message_visibility(
                QueueUrl=queue.queue_url, ReceiptHandle=delivery["ReceiptHandle"],
                VisibilityTimeout=0,
            )
        finally:
            os.kill(worker_pid, signal.SIGCONT)
        first_trace = wait_event(first)
        confirmed_at = datetime.now(UTC)
        assert first_trace["results"][0]["decisions"] == []
        assert first_trace["results"][0]["status"] == "processed"
        second = post(bodies[1])  # Only after a targeted persisted result, never queue order.
        second_trace = wait_event(second)
        results = {r["event_id"]: r for r in second_trace["results"]}
        assert len(results) == 2
        assert datetime.fromisoformat(results[second["event_id"]]["received_at"]) >= confirmed_at
        decision = results[second["event_id"]]["decisions"][0]
        assert decision["decision_type"] == "increase_ventilation"
        assert decision["recommended_airflow_pct"] == 55
        assert not decision["applied"]
        resend = post(bodies[1])
        for _ in range(100):
            final = trace(client, resend)
            duplicates = [o for o in final["observations"]
                          if o["event_id"] == resend["event_id"] and o["kind"] == "duplicate"]
            if duplicates:
                break
            time.sleep(0.1)
        assert duplicates and duplicates[0]["original_event_id"] == second["event_id"]
        assert len(final["results"]) == 2
        assert sum(len(r["decisions"]) for r in final["results"]) == 1
        assert final["measurements"] == [
            {"original_event_id": first["event_id"], "send_event_ids": [first["event_id"]]},
            {"original_event_id": second["event_id"],
             "send_event_ids": [second["event_id"], resend["event_id"]]},
        ]
        for receipt in (first, second, resend):
            observations = [o for o in final["observations"]
                            if o["event_id"] == receipt["event_id"]]
            assert observations
            assert all(o["envelope_id"] == receipt["envelope_id"] and
                       o["transport_message_id"] == receipt["transport_receipt"]["message_id"]
                       for o in observations)
        evidence = os.getenv("TELEMETRY_LAB_DEMO_EVIDENCE")
        if evidence:
            Path(evidence).with_name("slice3-evidence.json").write_text(json.dumps({
                "first_receipt": first, "second_receipt": second, "resend_receipt": resend,
                "normal_result_confirmed_at": confirmed_at.isoformat(),
                "first_persisted_trace": first_trace, "final_trace": final,
                "queue_samples": samples,
                "observer_passive_receive_count_after_reads": delivery["Attributes"],
                "request_body_sha256": [hashlib.sha256(b.encode()).hexdigest() for b in bodies],
                "timestamps": [json.loads(b)["timestamp"] for b in bodies],
            }, indent=2) + "\n")


@pytest.mark.parametrize("temperature", [22, 25.5, 25.6, 29])
def test_temperature_result_and_exact_resend(temperature: float) -> None:
    import hashlib
    import json

    assert BASE
    correlation = str(uuid4())
    body = json.dumps({
        "building_id": "demo-" + correlation, "zone_id": "salle-synthetique",
        "timestamp": datetime.now(UTC).isoformat(), "temperature_c": temperature,
        "humidity_pct": 45, "occupancy": 8, "co2_ppm": 700,
        "hvac_mode": "ventilation", "airflow_pct": 40,
    }, indent=2)
    engine = create_engine(os.environ["TELEMETRY_LAB_TEST_POSTGRES_URL"])
    with httpx.Client(base_url=BASE, timeout=10) as client:
        def post() -> dict:
            response = client.post("/ingestion/telemetry", content=body, headers={
                "Content-Type": "application/json", "X-Correlation-ID": correlation,
            })
            assert response.status_code == 202
            assert response.request.content == body.encode()
            return dict(response.json())

        first = post()
        initial = wait_result(client, first)
        business = initial["results"][0]
        assert business["event_id"] == first["event_id"]
        assert business["status"] == "processed"
        assert business["measurement"]["temperature_c"] == temperature
        decisions = business["decisions"]
        if temperature <= 25.5:
            assert decisions == []
        else:
            assert len(decisions) == 1
            assert decisions[0]["decision_type"] == "adjust_airflow"
            assert decisions[0]["recommended_hvac_mode"] == "cooling"
            assert decisions[0]["recommended_airflow_pct"] == 50
            assert decisions[0]["applied"] is False
        resend = post()
        assert first["event_id"] != resend["event_id"]
        assert first["envelope_id"] != resend["envelope_id"]
        assert first["transport_receipt"]["message_id"] != resend["transport_receipt"]["message_id"]
        for _ in range(100):
            final = trace(client, resend)
            duplicates = [o for o in final["observations"]
                          if o["kind"] == "duplicate" and o["event_id"] == resend["event_id"]]
            if duplicates:
                break
            time.sleep(0.1)
        assert duplicates and duplicates[0]["original_event_id"] == first["event_id"]
        assert len(final["results"]) == 1
        assert final["results"][0]["decisions"] == decisions
        assert all(o["process_id"] == int(os.environ["TELEMETRY_LAB_DEMO_WORKER_PID"])
                   for o in final["observations"])
        with engine.connect() as connection:
            event_ids = list(connection.execute(select(TelemetryEvent.event_id).where(
                TelemetryEvent.building_id == "demo-" + correlation)).scalars())
            decision_ids = list(connection.execute(select(HvacDecision.decision_id).where(
                HvacDecision.event_id.in_([first["event_id"], resend["event_id"]]))).scalars())
        assert event_ids == [first["event_id"]]
        assert decision_ids == [d["decision_id"] for d in decisions]
        evidence = os.getenv("TELEMETRY_LAB_DEMO_EVIDENCE")
        if evidence:
            Path(evidence).with_name(f"temperature-{temperature}-evidence.json").write_text(
                json.dumps({"temperature": temperature, "body": body,
                            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                            "first_receipt": first, "resend_receipt": resend,
                            "initial_trace": initial, "final_trace": final,
                            "database_event_ids": event_ids, "database_decision_ids": decision_ids},
                           indent=2) + "\n")
    engine.dispose()
