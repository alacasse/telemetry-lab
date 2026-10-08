#!/usr/bin/env python3
"""Measure local thermal failures using fresh disposable launcher compositions.

Runs both modes once in isolation, then repeats both with bounded concurrency.
Only launchers created here receive termination signals; their own cleanup owns
all child processes and containers. Existing .run/demo directories are refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODES = ("thermostat", "legacy")
ACTIVE: set[subprocess.Popen[str]] = set()
LOCK = threading.Lock()
STOP = threading.Event()


def classify(log: str) -> dict[str, int]:
    # SIGKILL/recovery tests produce restarts, not Python fatal tracebacks.
    # Count exception terminal lines only; references in source/pytest text are
    # not evidence that an exception escaped the thermal process.
    errors = re.findall(
        r"(?m)^(?:thermal fatal: |[\w.]+\.)?(TimeoutError|AuthorityLost)(?:[: ].*)?$", log
    )
    return {
        "unexpected_fatal_timeout": errors.count("TimeoutError"),
        "unexpected_fatal_authority_lost": errors.count("AuthorityLost"),
        # Count raw fatal reports, not inferred distinct underlying faults.
        "unexpected_fatal_total": len(re.findall(r"(?m)^thermal fatal: \S+", log)),
    }


def source_hashes() -> dict[str, str]:
    """Snapshot sources before launch; keep sources unchanged throughout a run."""
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in ("scripts", "packages", "demo", "services", "tests/integration")
        for path in sorted((ROOT / directory).rglob("*.py"))
    }


def failed_launch(result: dict[str, Any]) -> bool:
    return bool(
        result["launcher_exit_code"] != 0
        or result.get("timed_out", False)
        or "error" in result
        or not result.get("thermal_log_present", False)
        or result.get("unexpected_fatal_total", 0) > 0
    )


def free_port(port: int) -> None:
    if (ROOT / ".run/demo" / str(port)).exists():
        raise ValueError(f"Refusing reused evidence directory for port {port}")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repetitions", type=int, default=3, help="Repetitions per mode after isolated baselines"
    )
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--first-port", type=int, default=19000)
    parser.add_argument(
        "--timeout",
        type=float,
        default=900,
        help="Seconds per launcher, followed by its own cleanup",
    )
    args = parser.parse_args()
    baseline_count = 0 if args.skip_baseline else 2
    count = baseline_count + 2 * args.repetitions
    if (
        args.repetitions < 1
        or not 1 <= args.parallel <= 8
        or args.timeout <= 0
        or not 1024 <= args.first_port <= 65535 - count + 1
    ):
        parser.error(
            "Require repetitions >=1, parallel 1..8, positive timeout and valid port range"
        )
    # Validate the complete allocation before starting a composition.
    for port in range(args.first_port, args.first_port + count):
        free_port(port)
    output = (
        ROOT
        / ".run/validation/timeout-investigation"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    )
    output.mkdir(parents=True, exist_ok=False)
    (output / "configuration.json").write_text(
        json.dumps(
            {
                **vars(args),
                "revision": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                "git_status": subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=ROOT, text=True
                ),
                "classification": (
                    "Fatal exception lines; deliberate SIGKILL tests counted as restarts"
                ),
                "source_sha256": source_hashes(),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Evidence: {output}", flush=True)

    def stop(signum: int, frame: FrameType | None) -> None:
        STOP.set()
        with LOCK:
            for process in ACTIVE:
                if process.poll() is None:
                    process.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    def run(job: tuple[str, str, int, int]) -> dict[str, Any]:
        phase, mode, index, port = job
        directory = output / f"{phase}-{mode}-{index:02d}-{port}"
        directory.mkdir()
        argv = [
            str(ROOT / ".venv/bin/python"),
            str(ROOT / "scripts/demo-local.py"),
            "--thermal-diagnostics",
            "--port",
            str(port),
        ]
        argv += (
            ["--test-thermostat", "--startup-order", "runtime-first"]
            if mode == "thermostat"
            else ["--test"]
        )
        result: dict[str, Any] = {
            "phase": phase,
            "mode": mode,
            "port": port,
            "argv": argv,
            "launcher_exit_code": None,
            "timed_out": False,
        }
        started = time.monotonic()
        try:
            if STOP.is_set():
                result["error"] = "Interrupted before launch"
                return result
            free_port(port)
            result["source_sha256"] = source_hashes()
            result["launched_at_utc"] = datetime.now(UTC).isoformat()
            (directory / "launch.json").write_text(json.dumps(result, indent=2) + "\n")
            with (directory / "launcher.log").open("w") as handle:
                with LOCK:
                    if STOP.is_set():
                        result["error"] = "Interrupted before launch"
                        return result
                    process = subprocess.Popen(
                        argv, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, text=True
                    )
                    ACTIVE.add(process)
                try:
                    result["launcher_exit_code"] = process.wait(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    result["timed_out"] = True
                    process.terminate()
                    # Never SIGKILL the launcher: its finally owns cleanup.
                    result["launcher_exit_code"] = process.wait()
                finally:
                    with LOCK:
                        ACTIVE.discard(process)
            source = ROOT / ".run/demo" / str(port)
            if source.exists():
                shutil.copytree(source, directory / "demo")
            process_exits = source / "process-exits.json"
            if process_exits.exists():
                result["process_exits"] = json.loads(process_exits.read_text())
            thermal = source / "thermal.log"
            result.update(classify(thermal.read_text(errors="replace") if thermal.exists() else ""))
            result["thermal_log_present"] = thermal.exists()
            runtime = source / "runtime.json"
            if runtime.exists():
                runtime_state = json.loads(runtime.read_text())
                result["thermal_restarts_including_deliberate_crash_tests"] = runtime_state.get(
                    "thermal_restarts"
                )
                result["thermal_exits"] = runtime_state.get("thermal_exits", [])
        except (OSError, ValueError) as exc:
            result["error"] = str(exc)
        finally:
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            print(
                json.dumps({key: value for key, value in result.items() if key != "source_sha256"}),
                flush=True,
            )
        return result

    results = []
    jobs = (
        []
        if args.skip_baseline
        else [("baseline", mode, 0, args.first_port + i) for i, mode in enumerate(MODES)]
    )
    for job in jobs:
        results.append(run(job))
    repeated = [
        ("parallel", mode, index + 1, args.first_port + baseline_count + index * 2 + i)
        for index in range(args.repetitions)
        for i, mode in enumerate(MODES)
    ]
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        results.extend(pool.map(run, repeated))
    summary = []
    for phase in ("baseline", "parallel"):
        for mode in MODES:
            selected = [r for r in results if r["phase"] == phase and r["mode"] == mode]
            summary.append(
                {
                    "phase": phase,
                    "mode": mode,
                    "runs": len(selected),
                    "failed_launchers": sum(failed_launch(r) for r in selected),
                    "runs_with_unexpected_fatal": sum(
                        bool(
                            r.get("unexpected_fatal_total", 0)
                            or r.get("unexpected_fatal_timeout", 0)
                            or r.get("unexpected_fatal_authority_lost", 0)
                        )
                        for r in selected
                    ),
                }
            )
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return int(
        STOP.is_set()
        or any(r["failed_launchers"] or r["runs_with_unexpected_fatal"] for r in summary)
    )


if __name__ == "__main__":
    sys.exit(main())
