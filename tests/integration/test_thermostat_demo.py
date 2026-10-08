"""Persistent thermostat against a fresh HTTP/PostgreSQL/LocalStack composition.

Run only through scripts/demo-local.py --test-thermostat. Process controls are
restricted to the PIDs exported by that launcher; no existing runtime is reused.
"""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import boto3
import httpx
import pytest

BASE = os.getenv("TELEMETRY_LAB_DEMO_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="Run scripts/demo-local.py --test-thermostat")


def snapshot(client: httpx.Client, identity: str) -> dict:
    response = client.get(f"/thermal-simulations/{identity}")
    assert response.status_code == 200, response.text
    value = response.json()
    assert not (value["heater_on"] and value["cooler_on"]), value
    assert value["simulation_id"] == identity
    assert value["expires_at"] is None
    return dict(value)


def wait(
    client: httpx.Client, identity: str, predicate: Callable[[dict], bool], seconds: float = 20
) -> dict:
    deadline = time.monotonic() + seconds
    latest = {}
    while time.monotonic() < deadline:
        latest = snapshot(client, identity)
        if predicate(latest):
            return latest
        time.sleep(0.1)
    pytest.fail(f"Thermostat condition not met: {latest}")


def history(client: httpx.Client, identity: str, kind: str) -> list[dict]:
    cursor = 0
    items = []
    while True:
        response = client.get(
            f"/thermal-simulations/{identity}/history", params={"kind": kind, "cursor": cursor}
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["items"]) <= 100
        items.extend(page["items"])
        if page["next_cursor"] is None:
            return items
        assert page["next_cursor"] > cursor
        cursor = page["next_cursor"]


def setting(client: httpx.Client, identity: str, mode: str, target: float) -> dict:
    before = snapshot(client, identity)
    body = {
        "operation_id": str(uuid4()),
        "expected_revision": before["settings_revision"],
        "mode": mode,
        "target_c": target,
    }
    response = client.post(f"/thermal-simulations/{identity}/settings", json=body)
    assert response.status_code == 200, response.text
    return body


def idle(value: dict) -> bool:
    reading = value["latest_reading"]
    return (
        value["phase"] == "idle"
        and not value["heater_on"]
        and not value["cooler_on"]
        and reading is not None
        and reading["processed_at"] is not None
        and reading["settings_revision"] == value["settings_revision"]
    )


def stop_proof(commands: list[dict], readings: list[dict], stop: dict) -> dict:
    assert stop["command_type"].endswith(".stop") and stop["applied_at"]
    proof = next(r for r in readings if r["sequence"] == stop["final_reading_sequence"])
    assert proof["final"] and proof["processed_event_id"] and proof["processed_at"]
    assert not proof["heater_on"] and not proof["cooler_on"]
    assert proof["state_version"] == stop["applied_state_version"]
    return proof


def test_persistent_room_settings_proofs_backpressure_and_explicit_recovery() -> None:
    assert BASE
    runtime_file = Path(os.environ["TELEMETRY_LAB_DEMO_RUNTIME"])
    worker = int(os.environ["TELEMETRY_LAB_DEMO_WORKER_PID"])
    evidence: dict = {}
    with httpx.Client(base_url=BASE, timeout=5) as client:
        response = client.get("/thermal-simulations/current")
        assert response.status_code == 200, response.text
        identity = response.json()["simulation_id"]
        initial = wait(client, identity, idle)
        assert initial["policy"] == "thermostat" and initial["status"] == "active"
        assert initial["temperature_c"] == initial["target_c"] == 22
        assert initial["mode"] == "heating" and initial["settings_revision"] == 0
        assert history(client, identity, "commands") == []
        baseline = history(client, identity, "readings")
        assert len(baseline) == 1 and baseline[0]["processed_event_id"]
        # This duration proves both passive reads and absence of the old 30s expiry.
        started = time.monotonic()
        for _ in range(31):
            time.sleep(1)
            resting = snapshot(client, identity)
            assert resting["status"] == "active" and resting["temperature_c"] == 22
        assert time.monotonic() - started > 30
        assert history(client, identity, "readings") == baseline
        evidence["initial"] = initial
        evidence["idle_seconds"] = time.monotonic() - started

        noop = setting(client, identity, "heating", 22)
        wait(client, identity, idle)
        assert history(client, identity, "commands") == []
        recovered = client.get(f"/thermal-simulations/{identity}/settings/{noop['operation_id']}")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["operation_id"] == noop["operation_id"]
        repeated = client.post(f"/thermal-simulations/{identity}/settings", json=noop)
        assert repeated.status_code == 200, repeated.text
        assert snapshot(client, identity)["settings_revision"] == 1
        assert (
            client.post(
                f"/thermal-simulations/{identity}/settings", json={**noop, "target_c": 23}
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/thermal-simulations/{identity}/settings",
                json={**noop, "operation_id": str(uuid4())},
            ).status_code
            == 409
        )
        for target in (14.5, 30.5, 22.1):
            invalid = {
                **noop,
                "operation_id": str(uuid4()),
                "expected_revision": 1,
                "target_c": target,
            }
            assert (
                client.post(f"/thermal-simulations/{identity}/settings", json=invalid).status_code
                == 422
            )
        requests = [
            {**noop, "operation_id": str(uuid4()), "expected_revision": 1} for _ in range(2)
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda body: client.post(
                        f"/thermal-simulations/{identity}/settings", json=body
                    ),
                    requests,
                )
            )
        assert sorted(r.status_code for r in results) == [200, 409]
        wait(client, identity, idle)
        assert snapshot(client, identity)["settings_revision"] == 2

        setting(client, identity, "heating", 23)
        wait(client, identity, lambda s: s["heater_on"])
        hot = wait(client, identity, idle)
        assert hot["temperature_c"] >= 23 and hot["status"] == "active"
        setting(client, identity, "cooling", 21)
        wait(client, identity, lambda s: s["cooler_on"])
        cold = wait(client, identity, idle)
        assert cold["temperature_c"] <= 21
        assert cold["temperature_c"] != 22  # same room, no initialization reset
        commands = history(client, identity, "commands")
        readings = history(client, identity, "readings")
        assert [c["command_type"] for c in commands] == [
            "heating.start",
            "heating.stop",
            "cooling.start",
            "cooling.stop",
        ]
        for command in commands:
            if command["command_type"].endswith(".stop"):
                stop_proof(commands, readings, command)
        evidence.update(heating=hot, cooling=cold)

        # Hold the real worker while physics and publication continue. One periodic
        # unprocessed observation may accumulate, regardless of elapsed ticks.
        setting(client, identity, "heating", 30)
        wait(client, identity, lambda s: s["heater_on"])
        os.kill(worker, signal.SIGSTOP)
        try:
            time.sleep(4)
            delayed = history(client, identity, "readings")
            pending = [r for r in delayed if r["cause"] == "periodic" and not r["processed_at"]]
            assert len(pending) == 1, pending
            time.sleep(2)
            delayed_again = history(client, identity, "readings")
            assert [
                r["sequence"]
                for r in delayed_again
                if r["cause"] == "periodic" and not r["processed_at"]
            ] == [pending[0]["sequence"]]
            setting(client, identity, "cooling", 15)
        finally:
            os.kill(worker, signal.SIGCONT)
        switched = wait(client, identity, lambda s: s["cooler_on"])
        commands = history(client, identity, "commands")
        readings = history(client, identity, "readings")
        opposite = next(c for c in reversed(commands) if c["command_type"] == "cooling.start")
        previous_stop = next(
            c
            for c in reversed(commands)
            if c["sequence"] < opposite["sequence"] and c["command_type"] == "heating.stop"
        )
        proof = stop_proof(commands, readings, previous_stop)
        assert proof["processed_at"] <= opposite["created_at"] <= opposite["applied_at"]
        evidence.update(delayed_readings=delayed_again, inversion=switched, inversion_proof=proof)

        # Delay command delivery while leaving the clock alive to observe the
        # setting. The worker must create the stop before runtime interruption.
        before = json.loads(runtime_file.read_text())
        thermal_pid = before["thermal_pid"]
        endpoint = os.environ["AWS_ENDPOINT_URL"]
        assert endpoint.startswith("http://127.0.0.1:")
        queue = boto3.client(
            "sqs", endpoint_url=endpoint, region_name="ca-central-1",
            aws_access_key_id="test", aws_secret_access_key="test",
        )
        queue_url = queue.get_queue_url(
            QueueName="telemetry-lab-local-thermal-commands"
        )["QueueUrl"]
        queue.set_queue_attributes(QueueUrl=queue_url, Attributes={"DelaySeconds": "5"})
        try:
            setting(client, identity, "heating", 30)
            transition = wait(client, identity, lambda s: s["phase"] == "stop_pending")
            os.kill(thermal_pid, signal.SIGKILL)
        finally:
            queue.set_queue_attributes(QueueUrl=queue_url, Attributes={"DelaySeconds": "0"})
        interrupted = wait(client, identity, lambda s: s["status"] == "interrupted", 10)
        time.sleep(1.2)
        still = snapshot(client, identity)
        assert still["temperature_c"] == interrupted["temperature_c"]
        assert still["simulated_seconds"] == interrupted["simulated_seconds"]
        assert still["settings_revision"] == transition["settings_revision"]
        assert still["stop_command_sequence"] == transition["stop_command_sequence"]
        after = json.loads(runtime_file.read_text())
        assert after["thermal_pid"] != thermal_pid
        for key in ("api_pid", "worker_pid", "postgres_container"):
            assert after[key] == before[key]
        resumed = client.post(f"/thermal-simulations/{identity}/resume")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["temperature_c"] == interrupted["temperature_c"]
        acting = wait(client, identity, lambda s: s["heater_on"])
        commands = history(client, identity, "commands")
        readings = history(client, identity, "readings")
        current_stop = next(
            c for c in commands if c["sequence"] == transition["stop_command_sequence"]
        )
        current_proof = stop_proof(commands, readings, current_stop)
        assert current_proof["sequence"] != proof["sequence"]
        assert acting["status"] == "active"
        # Finish in confirmed idle without waiting to reach the distant target.
        setting(client, identity, "cooling", 30)
        final = wait(client, identity, idle)
        stable = history(client, identity, "readings")
        time.sleep(2)
        assert history(client, identity, "readings") == stable
        assert snapshot(client, identity)["temperature_c"] == final["temperature_c"]
        evidence.update(
            interrupted=interrupted,
            resumed=resumed.json(),
            current_proof=current_proof,
            final=final,
            commands=history(client, identity, "commands"),
            readings=stable,
            settings=history(client, identity, "settings"),
        )
    path = os.getenv("TELEMETRY_LAB_DEMO_EVIDENCE")
    if path:
        Path(path).with_name("thermostat-integration.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
