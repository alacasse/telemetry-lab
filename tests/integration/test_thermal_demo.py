"""Real local heating loop: HTTP, independent processes, PostgreSQL and LocalStack."""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

BASE = os.getenv("TELEMETRY_LAB_DEMO_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="Run scripts/demo-local.py --test")


def wait_snapshot(
    client: httpx.Client, simulation_id: str, predicate: Callable[[dict], bool], seconds: float = 35
) -> dict:
    until = time.monotonic() + seconds
    latest = {}
    while time.monotonic() < until:
        response = client.get(f"/thermal-simulations/{simulation_id}")
        assert response.status_code == 200, response.text
        latest = response.json()
        if predicate(latest):
            return dict(latest)
        time.sleep(0.15)
    pytest.fail(f"Thermal condition not met: {latest}")


def record(name: str, value: dict) -> None:
    evidence = os.getenv("TELEMETRY_LAB_DEMO_EVIDENCE")
    if evidence:
        Path(evidence).with_name(f"thermal-{name}.json").write_text(
            json.dumps(value, indent=2) + "\n"
        )


def test_real_heating_completion_exact_resend_and_admission() -> None:
    assert BASE
    simulation_id = str(uuid4())
    started = time.monotonic()
    with httpx.Client(base_url=BASE, timeout=5) as client:
        environment = client.get("/environment").json()
        response = client.post("/thermal-simulations", json={"simulation_id": simulation_id})
        assert response.status_code in (200, 201), response.text
        initial = response.json()
        repeated = client.post("/thermal-simulations", json={"simulation_id": simulation_id})
        assert repeated.status_code in (200, 201)
        assert repeated.json()["simulation_id"] == simulation_id
        assert repeated.json()["expires_at"] == initial["expires_at"]
        conflict = client.post("/thermal-simulations", json={"simulation_id": str(uuid4())})
        assert conflict.status_code == 409
        assert simulation_id in conflict.text
        assert (
            client.post(
                "/thermal-simulations",
                json={
                    "simulation_id": str(uuid4()),
                    "multiplier": 2,
                },
            ).status_code
            == 422
        )
        final = wait_snapshot(
            client, simulation_id, lambda s: s["status"] in ("completed", "expired")
        )
        assert final["status"] == "completed", final
        assert final["expires_at"] == initial["expires_at"]
        readings = final["readings"]
        assert readings[0]["temperature_c"] == 19
        assert readings[0]["heater_on"] is False
        assert readings[-1]["heater_on"] is False
        assert readings[-1]["temperature_c"] >= 22
        assert readings[-1]["processed_event_id"]
        assert len(final["commands"]) == 2
        assert all(c["applied_at"] for c in final["commands"])
        body = readings[-1]["body"]
        resend = client.post(
            "/ingestion/telemetry",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Correlation-ID": simulation_id,
            },
        )
        assert resend.status_code == 202, resend.text
        receipt = resend.json()
        until = time.monotonic() + 8
        trace = {}
        while time.monotonic() < until:
            trace = client.get(f"/query/simulations/{simulation_id}").json()
            if any(
                o["kind"] == "duplicate" and o["event_id"] == receipt["event_id"]
                for o in trace["observations"]
            ):
                break
            time.sleep(0.1)
        else:
            pytest.fail("Exact resend was not confirmed as duplicate")
        after = client.get(f"/thermal-simulations/{simulation_id}").json()
        assert after["commands"] == final["commands"]
        assert after["temperature_c"] == final["temperature_c"]
        record(
            "nominal",
            {
                "environment": environment,
                "initial": initial,
                "final": final,
                "observed_seconds": time.monotonic() - started,
                "resend_receipt": receipt,
                "resend_trace": trace,
            },
        )


def test_concurrent_admission_worker_block_and_real_expiry() -> None:
    assert BASE
    worker = int(os.environ["TELEMETRY_LAB_DEMO_WORKER_PID"])
    ids = [str(uuid4()), str(uuid4())]
    with httpx.Client(base_url=BASE, timeout=5) as client:
        os.kill(worker, signal.SIGSTOP)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(
                    executor.map(
                        lambda identity: client.post(
                            "/thermal-simulations", json={"simulation_id": identity}
                        ),
                        ids,
                    )
                )
            assert sorted(r.status_code for r in responses) in ([200, 409], [201, 409])
            active = next(r.json() for r in responses if r.status_code != 409)
            identity = active["simulation_id"]
            final = wait_snapshot(client, identity, lambda s: s["status"] == "expired")
            assert not final["heater_on"]
            assert final["temperature_c"] == 19
            assert final["commands"] == []
            resume = client.post(f"/thermal-simulations/{identity}/resume")
            assert resume.status_code in (200, 409)
            assert client.get(f"/thermal-simulations/{identity}").json()["status"] == "expired"
        finally:
            os.kill(worker, signal.SIGCONT)
        time.sleep(2)
        late = client.get(f"/thermal-simulations/{identity}").json()
        assert late["status"] == "expired"
        assert not late["heater_on"]
        assert late["commands"] == []
        record("expired", {"final": final, "after_late_processing": late})


@pytest.mark.parametrize("mode", ["heating", "cooling"])
def test_runtime_restart_requires_explicit_resume_without_catchup(mode: str) -> None:
    assert BASE
    runtime_file = Path(os.environ["TELEMETRY_LAB_DEMO_RUNTIME"])
    identity = str(uuid4())
    with httpx.Client(base_url=BASE, timeout=5) as client:
        initial = client.post(
            "/thermal-simulations", json={"simulation_id": identity, "mode": mode}
        ).json()
        actuator = "heater_on" if mode == "heating" else "cooler_on"
        heating = wait_snapshot(client, identity, lambda s: s[actuator], seconds=8)
        before = json.loads(runtime_file.read_text())
        os.kill(before["thermal_pid"], signal.SIGKILL)
        interrupted = wait_snapshot(client, identity, lambda s: s["status"] == "interrupted", 18)
        time.sleep(1.4)
        still = client.get(f"/thermal-simulations/{identity}").json()
        assert still["status"] == "interrupted"
        assert still["temperature_c"] == interrupted["temperature_c"]
        assert still["simulated_seconds"] == interrupted["simulated_seconds"]
        assert still["expires_at"] == initial["expires_at"]
        after = json.loads(runtime_file.read_text())
        assert after["thermal_pid"] != before["thermal_pid"]
        assert after["api_pid"] == before["api_pid"]
        assert after["worker_pid"] == before["worker_pid"]
        assert after["postgres_container"] == before["postgres_container"]
        resumed = client.post(f"/thermal-simulations/{identity}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["expires_at"] == initial["expires_at"]
        assert resumed.json()["temperature_c"] == interrupted["temperature_c"]
        duplicate_resume = client.post(f"/thermal-simulations/{identity}/resume").json()
        assert duplicate_resume["expires_at"] == initial["expires_at"]
        final = wait_snapshot(client, identity, lambda s: s["status"] in ("completed", "expired"))
        assert final["status"] == "completed"
        assert final["readings"][-1]["heater_on"] is False
        record(
            f"resume-{mode}",
            {
                "heating": heating,
                "interrupted": interrupted,
                "still": still,
                "resumed": resumed.json(),
                "final": final,
                "runtime": after,
            },
        )


@pytest.mark.parametrize("mode", ["heating", "cooling"])
def test_real_outbox_crash_windows_and_command_redelivery(tmp_path: Path, mode: str) -> None:
    """Kill one-shot relays after real effects, before their durable receipt/ack."""
    import subprocess
    import sys

    assert BASE
    runtime_file = Path(os.environ["TELEMETRY_LAB_DEMO_RUNTIME"])
    thermal_pid = json.loads(runtime_file.read_text())["thermal_pid"]
    identity = str(uuid4())

    def child(code: str, expected: int = 0) -> None:
        result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, timeout=20)
        assert result.returncode == expected

    setup = """
import os, json, httpx, time
from packages.thermal.authority import PgAuthority, AuthorityBusy
from packages.config import get_settings
from packages.db.session import create_session_factory
from demo.thermal_queue import ThermalQueue
from demo.thermal_runtime import publish_reading
from packages.thermal import runtime as rt
from functools import partial
sessions = create_session_factory(get_settings())
authority = PgAuthority(sessions)
deadline = time.monotonic() + 15
while True:
    try:
        with sessions() as session, session.begin():
            authority.acquire(session)
        break
    except AuthorityBusy:
        assert time.monotonic() < deadline
        time.sleep(0.1)
"""
    with httpx.Client(base_url=BASE, timeout=5) as client:
        os.kill(thermal_pid, signal.SIGSTOP)
        try:
            # Let the existing lease expire before starting this 30-second
            # business scenario; each relay acquires its own fenced generation.
            time.sleep(10.2)
            response = client.post(
                "/thermal-simulations", json={"simulation_id": identity, "mode": mode}
            )
            assert response.status_code == 200, response.text
            # HTTP has accepted the exact reading and queued it, but the publishing
            # process dies before storing its receipt. The command remains durable.
            child(
                setup
                + """
def crash(*args, **kwargs): os._exit(42)
rt.mark_published = crash
with httpx.Client(timeout=3) as client:
    rt.reading_publication(
        sessions, partial(publish_reading, client, os.environ['THERMAL_INGESTION_URL']), authority
    )
""",
                42,
            )
            pending = wait_snapshot(client, identity, lambda s: len(s["commands"]) == 1, 5)
            assert pending["readings"][0]["published_at"] is None
            assert pending["commands"][0]["published_at"] is None
            assert pending["commands"][0]["applied_at"] is None
            assert len(pending["decisions"]) == 1
            child(
                setup
                + """
with httpx.Client(timeout=3) as client:
    rt.reading_publication(
        sessions, partial(publish_reading, client, os.environ['THERMAL_INGESTION_URL']), authority
    )
queue = ThermalQueue(get_settings())
rt.command_publication(sessions, queue, authority)
for _ in range(5):
    # Speed only this intentionally unacknowledged transport delivery; the
    # simulation's original 30-second business deadline stays unchanged.
    messages = queue.client.receive_message(
        QueueUrl=queue.url, MaxNumberOfMessages=1, WaitTimeSeconds=1, VisibilityTimeout=1,
    ).get('Messages', [])
    if messages:
        body = json.loads(messages[0]['Body'])
        assert rt.RoomClock(sessions, authority).command(
            body['simulation_id'], body['sequence'], body['command_type']
        )
        with sessions() as session, session.begin():
            authority.release(session)
        os._exit(43)  # applied transaction committed; no DeleteMessage
raise RuntimeError('No command received')
""",
                43,
            )
            applied = client.get(f"/thermal-simulations/{identity}").json()
            assert applied["commands"][0]["applied_at"]
            assert applied["readings"][0]["transport_receipt"]["status"] == "accepted"
            # Child startup overlaps the one-second SQS visibility timeout;
            # its bounded receive loop waits for redelivery if necessary.
            child(
                setup
                + """
queue = ThermalQueue(get_settings())
for _ in range(5):
    messages = queue.receive()
    if messages:
        body = json.loads(messages[0]['Body'])
        assert rt.RoomClock(sessions, authority).command(
            body['simulation_id'], body['sequence'], body['command_type']
        )
        queue.acknowledge(messages[0]['ReceiptHandle'])
        break
else: raise RuntimeError('Command was not redelivered')
with sessions() as session, session.begin():
    authority.release(session)
"""
            )
            duplicate = client.get(f"/thermal-simulations/{identity}").json()
            assert duplicate["commands"] == applied["commands"]
            assert len(duplicate["decisions"]) == 1
            assert (
                duplicate["temperature_c"]
                == applied["temperature_c"]
                == (19 if mode == "heating" else 25)
            )
        finally:
            os.kill(thermal_pid, signal.SIGCONT)
        wait_snapshot(client, identity, lambda s: s["status"] == "interrupted", 18)
        resumed = client.post(f"/thermal-simulations/{identity}/resume")
        assert resumed.status_code == 200, resumed.text
        final = wait_snapshot(client, identity, lambda s: s["status"] in ("completed", "expired"))
        assert final["status"] == "completed", final
        assert final["expires_at"] == pending["expires_at"]
        record(
            f"crash-windows-{mode}",
            {
                "committed_unpublished": pending,
                "applied_unacked": applied,
                "after_redelivery": duplicate,
                "final": final,
            },
        )


def assert_stop_confirmation(snapshot: dict, mode: str) -> dict:
    assert snapshot["stop_confirmed"] is True, snapshot
    assert snapshot["heater_on"] is False
    assert snapshot["cooler_on"] is False
    stop = next(
        c for c in snapshot["commands"] if c["sequence"] == snapshot["stop_command_sequence"]
    )
    assert stop["command_type"] == f"{mode}.stop"
    assert stop["applied_at"]
    final = next(r for r in snapshot["readings"] if r["sequence"] == stop["final_reading_sequence"])
    assert final["final"] and final["processed_event_id"] and final["processed_at"]
    assert final["heater_on"] is False and final["cooler_on"] is False
    return dict(stop)


def test_real_cooling_completion() -> None:
    assert BASE
    identity = str(uuid4())
    with httpx.Client(base_url=BASE, timeout=5) as client:
        response = client.post(
            "/thermal-simulations", json={"simulation_id": identity, "mode": "cooling"}
        )
        assert response.status_code in (200, 201), response.text
        initial = response.json()
        assert initial["mode"] == "cooling"
        assert initial["temperature_c"] == 25
        assert not initial["heater_on"] and not initial["cooler_on"]
        conflict = client.post(
            "/thermal-simulations", json={"simulation_id": identity, "mode": "heating"}
        )
        assert conflict.status_code == 409
        final = wait_snapshot(client, identity, lambda s: s["status"] in ("completed", "expired"))
        assert final["status"] == "completed", final
        assert final["expires_at"] == initial["expires_at"]
        assert [c["command_type"] for c in final["commands"]] == ["cooling.start", "cooling.stop"]
        assert all(c["origin"] == "automatic" for c in final["commands"])
        assert final["readings"][0]["temperature_c"] == 25
        assert final["readings"][-1]["temperature_c"] <= 22
        assert all(not r["heater_on"] for r in final["readings"])
        assert_stop_confirmation(final, "cooling")
        record("cooling-nominal", {"initial": initial, "final": final})


@pytest.mark.parametrize("mode,next_mode", [("heating", "cooling"), ("cooling", "heating")])
def test_real_mode_switch_and_concurrent_successor_admission(mode: str, next_mode: str) -> None:
    assert BASE
    identity = str(uuid4())
    actuator = "heater_on" if mode == "heating" else "cooler_on"
    with httpx.Client(base_url=BASE, timeout=5) as client:
        response = client.post(
            "/thermal-simulations", json={"simulation_id": identity, "mode": mode}
        )
        assert response.status_code in (200, 201), response.text
        active = wait_snapshot(client, identity, lambda s: s[actuator], 8)
        premature = client.post(
            "/thermal-simulations",
            json={
                "simulation_id": str(uuid4()),
                "mode": next_mode,
                "predecessor_simulation_id": identity,
            },
        )
        assert premature.status_code == 409
        stopped_response = client.post(f"/thermal-simulations/{identity}/stop")
        assert stopped_response.status_code == 200, stopped_response.text
        request_id = stopped_response.json()["stop_request_id"]
        repeated = client.post(f"/thermal-simulations/{identity}/stop")
        assert repeated.status_code == 200
        assert repeated.json()["stop_request_id"] == request_id
        stopped = wait_snapshot(
            client, identity, lambda s: s["status"] in ("stopped", "expired"), 10
        )
        assert stopped["status"] == "stopped", stopped
        stop = assert_stop_confirmation(stopped, mode)
        assert stop["origin"] == "user"
        assert stop["stop_request_id"] == request_id
        assert stop["decision_id"] is None and stop["reading_sequence"] is None
        assert stopped["expires_at"] == active["expires_at"]
        ids = [str(uuid4()), str(uuid4())]
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda successor: client.post(
                        "/thermal-simulations",
                        json={
                            "simulation_id": successor,
                            "mode": next_mode,
                            "predecessor_simulation_id": identity,
                        },
                    ),
                    ids,
                )
            )
        assert sorted(r.status_code for r in responses) in ([200, 409], [201, 409])
        successor = next(r.json() for r in responses if r.status_code != 409)
        assert successor["mode"] == next_mode
        assert successor["predecessor_simulation_id"] == identity
        assert successor["temperature_c"] == (19 if next_mode == "heating" else 25)
        final = wait_snapshot(
            client, successor["simulation_id"], lambda s: s["status"] in ("completed", "expired")
        )
        assert final["status"] == "completed", final
        assert_stop_confirmation(final, next_mode)
        unchanged = client.get(f"/thermal-simulations/{identity}").json()
        assert {k: v for k, v in unchanged.items() if k != "runtime"} == {
            k: v for k, v in stopped.items() if k != "runtime"
        }
        record(f"switch-{mode}-{next_mode}", {"predecessor": stopped, "successor": final})


def test_real_stop_before_initial_publication() -> None:
    """A stop can be confirmed without briefly starting the cooling actuator."""
    assert BASE
    runtime_file = Path(os.environ["TELEMETRY_LAB_DEMO_RUNTIME"])
    thermal_pid = json.loads(runtime_file.read_text())["thermal_pid"]
    identity = str(uuid4())
    with httpx.Client(base_url=BASE, timeout=5) as client:
        os.kill(thermal_pid, signal.SIGSTOP)
        try:
            created = client.post(
                "/thermal-simulations",
                json={
                    "simulation_id": identity,
                    "mode": "cooling",
                },
            )
            assert created.status_code in (200, 201), created.text
            requested = client.post(f"/thermal-simulations/{identity}/stop")
            assert requested.status_code == 200, requested.text
            assert requested.json()["stop_confirmed"] is False
            blocked = client.post(
                "/thermal-simulations",
                json={
                    "simulation_id": str(uuid4()),
                    "mode": "heating",
                    "predecessor_simulation_id": identity,
                },
            )
            assert blocked.status_code == 409
        finally:
            os.kill(thermal_pid, signal.SIGCONT)
        stopped = wait_snapshot(
            client, identity, lambda s: s["status"] in ("stopped", "expired"), 10
        )
        assert stopped["status"] == "stopped", stopped
        assert_stop_confirmation(stopped, "cooling")
        assert stopped["temperature_c"] == 25
        assert all(not r["cooler_on"] and not r["heater_on"] for r in stopped["readings"])
        assert not any(
            c["command_type"] == "cooling.start" and c["applied_at"] for c in stopped["commands"]
        )
        record("cooling-stop-before-publication", stopped)
