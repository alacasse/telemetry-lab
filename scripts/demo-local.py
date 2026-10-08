#!/usr/bin/env python3
"""Disposable local stack. No host .env, AWS credentials, or existing Docker volumes."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, cwd=ROOT).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument(
        "--test", action="store_true", help="Run real integration tests then clean up"
    )
    parser.add_argument(
        "--test-thermostat", action="store_true",
        help="Run persistent thermostat integration tests on a fresh room, then clean up",
    )
    parser.add_argument(
        "--legacy-thermal", action="store_true",
        help="Keep historical scenario initialization (also used by --test)",
    )
    parser.add_argument("--thermal-multiplier", type=float, default=1.0)
    parser.add_argument(
        "--startup-order", choices=("api-first", "runtime-first", "worker-first"),
        default="api-first", help="Process startup order for local recovery validation",
    )
    args = parser.parse_args()
    if args.test_thermostat and (args.test or args.legacy_thermal):
        parser.error("--test-thermostat requires thermostat initialization")
    if not 0.5 <= args.thermal_multiplier <= 2.0:
        parser.error("--thermal-multiplier must be between 0.5 and 2")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    command("docker", "info", "--format", "{{.ServerVersion}}")
    containers: list[str] = []
    processes: list[subprocess.Popen] = []
    logs = ROOT / ".run" / "demo" / str(args.port)
    logs.mkdir(parents=True, exist_ok=True)
    handles = []
    test_process = None

    def interrupted(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        pg = command(
            "docker",
            "run",
            "-d",
            "--rm",
            "-p",
            "127.0.0.1::5432",
            "-e",
            "POSTGRES_DB=telemetry_lab",
            "-e",
            "POSTGRES_USER=telemetry_lab",
            "-e",
            "POSTGRES_PASSWORD=local-demo",
            "postgres:16-alpine",
        )
        containers.append(pg)
        sqs = command(
            "docker",
            "run",
            "-d",
            "--rm",
            "-p",
            "127.0.0.1::4566",
            "-e",
            "SERVICES=sqs",
            "-e",
            "AWS_DEFAULT_REGION=ca-central-1",
            "-e",
            "SQS_ENDPOINT_STRATEGY=path",
            "localstack/localstack:3.4",
        )
        containers.append(sqs)
        pg_port = command("docker", "port", pg, "5432/tcp").rsplit(":", 1)[1]
        sqs_port = command("docker", "port", sqs, "4566/tcp").rsplit(":", 1)[1]
        endpoint = f"http://127.0.0.1:{sqs_port}"
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(
                (
                    "AWS_",
                    "APP_",
                    "DATABASE_",
                    "QUEUE_",
                    "STAGING_",
                    "WORKER_",
                    "PYTHON",
                    "THERMAL_",
                    "TELEMETRY_LAB_",
                )
            )
        }
        revision = command("git", "rev-parse", "HEAD")
        if command("git", "status", "--porcelain"):
            revision += "-dirty"
        env.update(
            {
                "APP_ENV": "local",
                "DATABASE_URL": f"postgresql+psycopg://telemetry_lab:local-demo@127.0.0.1:{pg_port}/telemetry_lab",
                "QUEUE_BACKEND": "sqs",
                "QUEUE_NAME": "telemetry-lab-local-demo",
                "AWS_ENDPOINT_URL": endpoint,
                "AWS_REGION": "ca-central-1",
                "AWS_ACCESS_KEY_ID": "test",
                "AWS_SECRET_ACCESS_KEY": "test",
                "AWS_EC2_METADATA_DISABLED": "true",
                "AWS_CONFIG_FILE": "/dev/null",
                "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
                "ENABLE_XRAY": "false",
                "WORKER_RECEIVE_WAIT_SECONDS": "1",
                "WORKER_MAX_MESSAGES": "1",
                "RELEASE_REVISION": revision,
                "TELEMETRY_LAB_LOCAL_DEMO": "1",
                "THERMAL_QUEUE_NAME": "telemetry-lab-local-thermal-commands",
                "THERMAL_RUNTIME_STATE": str(logs / "runtime.json"),
                "THERMAL_TIME_MULTIPLIER": str(args.thermal_multiplier),
                "THERMAL_INITIALIZE_THERMOSTAT": "0" if args.test or args.legacy_thermal else "1",
                "THERMAL_INGESTION_URL": f"http://127.0.0.1:{args.port}/ingestion/telemetry",
                "PYTHONPATH": os.pathsep.join(
                    str(ROOT / p)
                    for p in (
                        ".",
                        "services/ingestion-service",
                        "services/query-service",
                        "services/processing-worker",
                    )
                ),
            }
        )
        for _ in range(60):
            ready = (
                subprocess.run(
                    ["docker", "exec", pg, "pg_isready", "-U", "telemetry_lab"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            )
            try:
                sqs_ready = httpx.get(endpoint + "/_localstack/health", timeout=1).is_success
            except httpx.HTTPError:
                sqs_ready = False
            if ready and sqs_ready:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Local containers did not become ready within 60 seconds")

        with tempfile.TemporaryDirectory(prefix="telemetry-lab-demo-") as cwd:
            # Empty cwd deliberately prevents Settings from loading the repository's .env.
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from packages.db.migration_handler import run_migrations; run_migrations()",
                ],
                env=env,
                cwd=cwd,
                check=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from packages.config import get_settings; "
                    "from packages.queue.sqs import SQSQueueClient; "
                    "q=SQSQueueClient(get_settings()); q.ensure_queue(); "
                    "q.client.set_queue_attributes(QueueUrl=q.queue_url, "
                    "Attributes={'VisibilityTimeout':'3'}); "
                    "q.client.create_queue(QueueName='telemetry-lab-local-thermal-commands', "
                    "Attributes={'VisibilityTimeout':'3'})",
                ],
                env=env,
                cwd=cwd,
                check=True,
            )
            entries = dict((
                (
                    "api",
                    [
                        "-m",
                        "uvicorn",
                        "demo.app:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(args.port),
                    ],
                ),
                ("worker", ["-m", "processing_worker.main"]),
                ("thermal", ["-m", "demo.thermal_runtime"]),
            ))
            order = {
                "api-first": ("api", "worker", "thermal"),
                "runtime-first": ("thermal", "worker", "api"),
                "worker-first": ("worker", "api", "thermal"),
            }[args.startup_order]
            started = {}
            log_handles = {}
            for name in order:
                argv = entries[name]
                handle = (logs / f"{name}.log").open("w")
                handles.append(handle)
                processes.append(
                    subprocess.Popen(
                        [sys.executable, *argv],
                        cwd=cwd,
                        env=env,
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                    )
                )
                started[name], log_handles[name] = processes[-1], handle
                if name != order[-1] and args.startup_order != "api-first":
                    time.sleep(1)
            # Stable identities for supervision, evidence and cleanup regardless of launch order.
            processes = [started[name] for name in ("api", "worker", "thermal")]
            handles = [log_handles[name] for name in ("api", "worker", "thermal")]
            base = f"http://127.0.0.1:{args.port}"
            for _ in range(100):
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError(f"Local process exited; inspect {logs}")
                try:
                    response = httpx.get(base + "/environment", timeout=1)
                    if (
                        response.is_success
                        and response.json().get("process_id") == processes[0].pid
                    ):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Local HTTP server did not become ready")
            state: dict[str, Any] = {
                "base_url": base,
                "launcher_pid": os.getpid(),
                "api_pid": processes[0].pid,
                "worker_pid": processes[1].pid,
                "thermal_pid": processes[2].pid,
                "thermal_restarts": 0,
                "thermal_status": "available",
                "startup_order": args.startup_order,
                "postgres_container": pg,
                "localstack_container": sqs,
                "release_revision": revision,
            }
            (logs / "runtime.json").write_text(json.dumps(state, indent=2) + "\n")
            print(
                f"Demo ready: {base} (API PID {processes[0].pid}, "
                f"worker PID {processes[1].pid}). Ctrl-C stops processes and disposable DB.",
                flush=True,
            )
            if args.test or args.test_thermostat:
                env.update(
                    {
                        "TELEMETRY_LAB_DEMO_URL": base,
                        "TELEMETRY_LAB_DEMO_EVIDENCE": str(logs / (
                            "thermostat-evidence.json" if args.test_thermostat
                            else "slice2-evidence.json"
                        )),
                        "TELEMETRY_LAB_DEMO_WORKER_PID": str(processes[1].pid),
                        "TELEMETRY_LAB_DEMO_THERMAL_PID": str(processes[2].pid),
                        "TELEMETRY_LAB_DEMO_RUNTIME": str(logs / "runtime.json"),
                        "TELEMETRY_LAB_TEST_POSTGRES_URL": env["DATABASE_URL"],
                    }
                )
                test_process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        *(
                            [str(ROOT / "tests/integration/test_thermostat_demo.py")]
                            if args.test_thermostat else [
                                str(ROOT / "tests/integration/test_local_demo.py"),
                                str(ROOT / "tests/integration/test_thermal_demo.py"),
                            ]
                        ),
                        "-v",
                    ],
                    cwd=cwd,
                    env=env,
                )
            else:
                test_process = None
            while True:
                if any(p.poll() is not None for p in processes[:2]):
                    raise RuntimeError(f"Local process exited; inspect {logs}")
                if processes[2].poll() is not None and state["thermal_restarts"] < 3:
                    time.sleep(0.5)
                    processes[2] = subprocess.Popen(
                        [sys.executable, "-m", "demo.thermal_runtime"],
                        cwd=cwd,
                        env=env,
                        stdout=handles[2],
                        stderr=subprocess.STDOUT,
                    )
                    state["thermal_pid"] = processes[2].pid
                    state["thermal_status"] = "available"
                    state["thermal_restarts"] += 1
                    (logs / "runtime.json").write_text(json.dumps(state, indent=2) + "\n")
                if processes[2].poll() is not None and state["thermal_restarts"] >= 3:
                    if state.get("thermal_status") != "unavailable":
                        state["thermal_status"] = "unavailable"
                        (logs / "runtime.json").write_text(json.dumps(state, indent=2) + "\n")
                        print(
                            "Thermal restart budget exhausted; "
                            "other local services remain available.",
                            flush=True,
                        )
                if test_process is not None and test_process.poll() is not None:
                    return test_process.returncode
                time.sleep(0.1)
    except KeyboardInterrupt:
        return 0
    finally:
        if test_process is not None and test_process.poll() is None:
            test_process.terminate()
            try:
                test_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                test_process.kill()
                test_process.wait()
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for handle in handles:
            handle.close()
        for container in reversed(containers):
            subprocess.run(
                ["docker", "stop", "-t", "3", container], stdout=subprocess.DEVNULL, check=False
            )


if __name__ == "__main__":
    raise SystemExit(main())
