"""Host orchestration only; every application probe executes inside Compose."""
from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from typing import Any, cast


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", "compose", *args], check=check, text=True,
                          capture_output=True, timeout=120)


def probe(action: str = "snapshot") -> dict[str, Any]:
    result = compose("exec", "-T", "validation", "python",
                     "scripts/thermal-engine-probe.py", action)
    return cast(dict[str, Any], json.loads(result.stdout.strip().splitlines()[-1]))


def wait(predicate: Callable[[dict[str, Any]], bool], timeout: float = 40) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = probe()
        if predicate(last):
            return last
        time.sleep(0.1)
    raise AssertionError(f"persistent evidence deadline exceeded: {last}")


def signal_exit(signal: str) -> float:
    cid = compose("ps", "-q", "thermal-engine").stdout.strip()
    started = time.monotonic()
    subprocess.run(["docker", "kill", "--signal", signal, cid], check=True,
                   capture_output=True, timeout=10)
    exited = subprocess.run(["docker", "wait", cid], check=True, text=True,
                            capture_output=True, timeout=6)
    assert exited.stdout.strip() == "0", (signal, exited.stdout, exited.stderr)
    elapsed = time.monotonic() - started
    assert elapsed <= 4.75, (
        f"{signal} exceeded 4-second shutdown + 0.75s Docker tolerance: {elapsed}"
    )
    return elapsed


def main() -> None:
    try:
        compose("up", "-d", "postgres", "localstack")
        compose("run", "--rm", "-T", "setup")
        compose("up", "-d", "ingestion", "worker", "thermal-engine", "validation")
        wait(lambda s: bool(s["owner"] and s["room"] and s["clock"] != "None"))
        cid = compose("ps", "-q", "thermal-engine").stdout.strip()
        image = subprocess.run(["docker", "inspect", "--format", "{{.Image}}", cid],
                               check=True, capture_output=True, text=True, timeout=10)
        print(f"Runtime image {image.stdout.strip()}", flush=True)
        print("Startup authority and thermostat established", flush=True)
        probe("settings")
        wait(lambda s: all(s[key] > 0 for key in
                          ("published", "processed", "applied", "command_receipts")))
        print("Reading/worker/command pipeline proved", flush=True)
        term = signal_exit("SIGTERM")
        wait(lambda s: s["free"])
        compose("up", "-d", "thermal-engine")
        wait(lambda s: bool(s["owner"] and s["room"]["status"] == "interrupted"))
        # Recovery leaves business state stationary while the real owner keeps renewing.
        # Freezing the process would test its watchdog instead of busy admission.
        compose("stop", "worker")
        observed = probe()
        before = wait(lambda s: s["clock"] != observed["clock"] and not s["free"])
        contender = compose("exec", "-T", "validation", "python", "-m",
                            "thermal_engine.main", check=False)
        assert contender.returncode == 75, contender.stdout + contender.stderr
        after = probe()
        assert not after["free"], after
        for key in ("generation", "owner", "room", "business", "published", "applied"):
            assert before[key] == after[key], (key, before, after)
        print("Busy contender code 75 and unchanged business state proved", flush=True)
        compose("up", "-d", "worker")
        print(f"SIGTERM exit {term:.3f}s", flush=True)
        interrupt = signal_exit("SIGINT")
        wait(lambda s: s["free"])
        compose("up", "-d", "thermal-engine")
        wait(lambda s: bool(s["owner"]))
        probe("resume")
        wait(lambda s: s["room"]["status"] == "active")
        print(f"SIGINT exit {interrupt:.3f}s", flush=True)
        before_crash = probe()
        compose("kill", "-s", "SIGKILL", "thermal-engine")
        wait(lambda s: s["free"], timeout=20)
        compose("up", "-d", "thermal-engine")
        replaced = wait(lambda s: s["generation"] > before_crash["generation"] and
                        s["room"]["status"] == "interrupted")
        stable = wait(lambda s: s["clock"] != replaced["clock"])
        assert stable["room"]["temperature_c"] == replaced["room"]["temperature_c"]
        assert stable["room"]["simulated_seconds"] == replaced["room"]["simulated_seconds"]
        probe("resume")
        resumed = wait(lambda s: s["room"]["step"] > stable["room"]["step"])
        assert resumed["room"]["simulated_seconds"] - stable["room"]["simulated_seconds"] < 3
        compose("exec", "-T", "validation", "python",
                "tests/integration/test_thermal_engine_container.py")
        print(f"Container thermal pipeline, exclusion, TERM={term:.3f}s, INT={interrupt:.3f}s, "
              "crash expiry, explicit resume and transport redelivery passed")
    except BaseException:
        logs = compose("logs", "--no-color", check=False)
        print(logs.stdout + logs.stderr)
        raise


if __name__ == "__main__":
    main()
