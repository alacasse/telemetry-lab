"""Transport-independent lifecycle of the exclusive thermal engine."""
from __future__ import annotations

import json
import os
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from types import FrameType

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from packages.thermal import diagnostics
from packages.thermal.authority import AuthorityBusy, AuthorityStopping, PgAuthority
from packages.thermal.runtime import (
    CommandTransport,
    RoomClock,
    command_publication,
    reading_publication,
    receive_commands,
)
from packages.thermal.service import create_thermostat, recover_simulations


@dataclass(frozen=True)
class ThermalTransports:
    queue: CommandTransport
    publish_reading: Callable[[str, str], dict]


def initialize_runtime(
    sessions: sessionmaker[Session], authority: PgAuthority, *, initialize: bool,
    time_multiplier: float = 1,
) -> None:
    """Commit authority, recovery and optional initialization atomically."""
    try:
        with sessions() as session, session.begin():
            authority.acquire(session)
            session.execute(text("SELECT pg_advisory_xact_lock(72410931)"))
            recover_simulations(session)
            if initialize:
                create_thermostat(session, multiplier=time_multiplier)
    except Exception:
        authority.fail()
        raise


def run_process(
    sessions: sessionmaker[Session], authority: PgAuthority, *,
    transports: Callable[[], AbstractContextManager[ThermalTransports]],
    time_multiplier: float = 1, initialize: bool = True,
    retry_exceptions: tuple[type[Exception], ...] = (),
    runtime_name: str = "thermal", revision: str | None = None,
) -> int:
    """Supervise five loops; bound the entire stop, including transport cleanup.

    The process watchdog is deliberately independent of client and database I/O.
    At the four-second deadline it exits without admitting any late cleanup.
    This function runs on the process main thread, where signal handlers belong.
    """
    try:
        initialize_runtime(
            sessions, authority, initialize=initialize, time_multiplier=time_multiplier
        )
    except AuthorityBusy:
        print("Thermal runtime refused: shared-room authority is occupied", flush=True)
        return 75

    if diagnostics.enabled():
        diagnostics.start_timing_sink()

    stop, fatal = threading.Event(), threading.Event()
    deadline: float | None = None
    watchdog: threading.Timer | None = None
    stop_lock = threading.RLock()

    def request_stop() -> None:
        nonlocal deadline, watchdog
        with stop_lock:
            if deadline is None:
                deadline = time.monotonic() + 4
                watchdog = threading.Timer(4, lambda: os._exit(1))
                watchdog.daemon = True
                watchdog.start()
            stop.set()
            authority.stop_admission()

    def fail(exc: Exception, loop: str = "supervisor", action: str = "supervise") -> None:
        authority.fail()
        fatal.set()
        request_stop()
        print(f"thermal fatal: {type(exc).__name__}", flush=True)
        try:
            diagnostics.emit(
                diagnostics.fatal_payload(
                    exc, loop=loop, action=action, generation=authority.generation
                )
            )
        except Exception:
            pass

    def bounded_loop(action: Callable[[], None], interval: float, name: str) -> None:
        expected = time.monotonic()
        while not stop.is_set():
            started = time.monotonic()
            lag = max(0, started - expected)
            try:
                with diagnostics.action_context(name, name, authority.generation):
                    authority.check_active()
                    action()
            except AuthorityStopping:
                return
            except retry_exceptions as exc:
                print(f"thermal transport retry: {type(exc).__name__}", flush=True)
            except Exception as exc:
                fail(exc, name, name)
                return
            finally:
                elapsed = time.monotonic() - started
                if diagnostics.enabled() and (elapsed >= 0.2 or lag >= 0.2):
                    diagnostics.emit_timing(
                        {
                            "event": "thermal_action_timing",
                            "process_id": os.getpid(),
                            "monotonic": time.monotonic(),
                            "loop": name,
                            "action": name,
                            "elapsed_seconds": elapsed,
                            "scheduling_lag_seconds": lag,
                            "generation": authority.generation
                            if isinstance(authority.generation, int)
                            else None,
                        }
                    )
            expected = time.monotonic() + interval
            stop.wait(interval)

    @contextmanager
    def supervised_transports() -> Iterator[ThermalTransports]:
        with transports() as transport:
            try:
                yield transport
            except Exception as exc:
                # Arm the deadline before a failing adapter exit can block.
                fail(exc)
                raise

    threads: list[threading.Thread] = []
    previous_handlers: dict[
        signal.Signals, Callable[[int, FrameType | None], object] | int | None
    ] = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, lambda *_: request_stop())
        with supervised_transports() as transport:
            clock = RoomClock(sessions, authority=authority)
            threads = [
                threading.Thread(target=bounded_loop, args=(action, interval, name), daemon=True)
                for action, interval, name in (
                    (authority.renew, 2.0, "authority_renew"),
                    (clock.tick, 0.05, "clock_tick"),
                    (
                        lambda: reading_publication(
                            sessions, transport.publish_reading, authority=authority
                        ),
                        0.1,
                        "reading_publication",
                    ),
                    (
                        lambda: command_publication(sessions, transport.queue, authority=authority),
                        0.1,
                        "command_publication",
                    ),
                    (lambda: receive_commands(clock, transport.queue), 0.05, "receive_commands"),
                )
            ]
            for thread in threads:
                thread.start()
            print(json.dumps({
                "runtime": runtime_name, "process_id": os.getpid(),
                "release_revision": revision,
            }), flush=True)
            try:
                while not stop.wait(0.2):
                    authority.check_active()
                    if not all(thread.is_alive() for thread in threads):
                        raise RuntimeError("Thermal runtime thread failed")
            except Exception as exc:
                fail(exc)
            finally:
                request_stop()
                assert deadline is not None
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(timeout=max(0, deadline - time.monotonic()))
            if fatal.is_set() or any(thread.is_alive() for thread in threads):
                authority.fail()
                return 1
            if time.monotonic() >= deadline:
                authority.fail()
                return 1
            authority.check_active()
            with sessions() as session, session.begin():
                authority.guard(session)
                recover_simulations(session)
                authority.release(session)
        return 0
    except Exception as exc:
        fail(exc)
        return 1
    finally:
        if watchdog is not None:
            watchdog.cancel()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
