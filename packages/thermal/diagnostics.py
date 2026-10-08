"""Bounded, secret-free runtime diagnostics; never part of control flow."""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool
from sqlalchemy.pool.base import ConnectionPoolEntry

_local = threading.local()
_lock = threading.Lock()
_held: dict[int, dict[str, Any]] = {}
_pools: dict[int, QueuePool] = {}
_timing_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=128)
_sink_started = False


def enabled() -> bool:
    return os.environ.get("THERMAL_DIAGNOSTICS") == "1"


def emit(payload: dict[str, Any]) -> None:
    try:
        print(json.dumps(payload), file=sys.stderr, flush=True)
    except Exception:
        pass


def write_timing(payload: dict[str, Any]) -> None:
    """Use raw descriptor writes so a blocked daemon owns no Python I/O lock."""
    try:
        descriptor = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        # In-memory test/caller streams have no descriptor to flush at exit.
        emit(payload)
        return
    try:
        encoded = (json.dumps(payload) + "\n").encode("utf-8")
        while encoded:
            written = os.write(descriptor, encoded)
            if written <= 0:
                return
            encoded = encoded[written:]
    except Exception:
        pass


def start_timing_sink() -> None:
    global _sink_started
    with _lock:
        if _sink_started:
            return
        _sink_started = True

    def drain() -> None:
        while True:
            payload = _timing_queue.get()
            write_timing(payload)

    threading.Thread(target=drain, name="thermal-diagnostics", daemon=True).start()


def emit_timing(payload: dict[str, Any]) -> None:
    """Never wait for output or space; slow sinks lose optional timing records."""
    try:
        _timing_queue.put_nowait(payload)
    except Exception:
        pass


@contextmanager
def action_context(loop: str, action: str, generation: object = None) -> Iterator[None]:
    previous = getattr(_local, "owner", None)
    _local.owner = {
        "loop": loop,
        "action": action,
        "generation": generation if isinstance(generation, int) else None,
    }
    try:
        yield
    finally:
        _local.owner = previous


def pool_state(pool: QueuePool) -> dict[str, Any]:
    return {
        "pool_id": id(pool),
        "size": pool.size(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
    }


def pool_snapshot() -> list[dict[str, Any]]:
    with _lock:
        return [dict(item) for item in _held.values()]


class DiagnosticQueuePool(QueuePool):
    """Measure the existing acquisition path without extra checkouts."""

    def _do_get(self) -> ConnectionPoolEntry:
        started = time.monotonic()
        try:
            return super()._do_get()
        finally:
            try:
                elapsed = time.monotonic() - started
                _local.checkout_wait = elapsed
                if elapsed >= 0.2:
                    emit_timing(
                        {
                            "event": "thermal_pool_wait",
                            "process_id": os.getpid(),
                            "monotonic": time.monotonic(),
                            "wait_seconds": elapsed,
                            "pool": pool_state(self),
                            **(getattr(_local, "owner", None) or {}),
                        }
                    )
            except Exception:
                pass


def install_pool_diagnostics(engine: Engine) -> None:
    start_timing_sink()
    pool = engine.pool
    if not isinstance(pool, QueuePool):
        return
    with _lock:
        _pools[id(pool)] = pool

    def checkout(_connection: Any, record: Any, _proxy: Any) -> None:
        try:
            with _lock:
                _held[id(record)] = {
                    "pool_id": id(pool),
                    "backend_pid": backend_pid(_connection),
                    "thread_id": threading.get_ident(),
                    "held_since_monotonic": time.monotonic(),
                    "checkout_wait_seconds": getattr(_local, "checkout_wait", None),
                    **(getattr(_local, "owner", None) or {}),
                }
        except Exception:
            pass

    def checkin(_connection: Any, record: Any) -> None:
        try:
            with _lock:
                owner = _held.pop(id(record), None)
            if owner is not None:
                elapsed = time.monotonic() - owner["held_since_monotonic"]
                if elapsed >= 0.2:
                    emit_timing(
                        {
                            "event": "thermal_pool_hold",
                            "process_id": os.getpid(),
                            "monotonic": time.monotonic(),
                            "hold_seconds": elapsed,
                            "pool": pool_state(pool),
                            **owner,
                        }
                    )
        except Exception:
            pass

    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)


def backend_pid(connection: Any) -> int | None:
    try:
        value = connection.info.backend_pid
        return value if isinstance(value, int) else None
    except Exception:
        return None


def sqlstate(exc: Exception) -> str | None:
    try:
        value = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if isinstance(value, str) and re.fullmatch(r"[A-Z0-9]{5}", value):
            return value
    except Exception:
        pass
    return None


def fatal_payload(exc: Exception, *, loop: str, action: str, generation: object) -> dict[str, Any]:
    frames = []
    traceback = exc.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        frames.append(
            {
                "file": os.path.basename(frame.f_code.co_filename),
                "function": frame.f_code.co_name,
                "line": traceback.tb_lineno,
            }
        )
        traceback = traceback.tb_next
    return {
        "event": "thermal_fatal",
        "exception_type": type(exc).__name__,
        "exception_module": type(exc).__module__,
        "sqlstate": sqlstate(exc),
        "process_id": os.getpid(),
        "generation": generation if isinstance(generation, int) else None,
        "monotonic": time.monotonic(),
        "loop": loop,
        "action": action,
        "frames": frames,
        "pool_checked_out": pool_snapshot(),
        "pools": [pool_state(pool) for pool in list(_pools.values())],
    }
