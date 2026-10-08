"""Fatal log classification must recognize the runtime's real output."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/reproduce-thermal-timeouts.py"
SPEC = importlib.util.spec_from_file_location("reproduce_thermal_timeouts", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


def test_historical_and_structured_fatal_lines() -> None:
    log = (
        "thermal fatal: TimeoutError\n"
        "thermal fatal: AuthorityLost\n"
        "thermal fatal: TimeoutError worker=publisher operation=flush\n"
        "thermal fatal: AuthorityLost: renewal deadline exceeded\n"
    )
    assert HARNESS.classify(log) == {
        "unexpected_fatal_timeout": 2,
        "unexpected_fatal_authority_lost": 2,
        "unexpected_fatal_total": 4,
    }


def test_deliberate_crash_and_exception_source_are_not_fatal_evidence() -> None:
    log = (
        "test_real_crash_recovery PASSED\n"
        "SIGKILL thermal process\n"
        "    raise TimeoutError\n"
        "assert type(exc).__name__ == 'AuthorityLost'\n"
    )
    assert HARNESS.classify(log) == {
        "unexpected_fatal_timeout": 0,
        "unexpected_fatal_authority_lost": 0,
        "unexpected_fatal_total": 0,
    }


def test_success_requires_thermal_log_and_no_launcher_errors() -> None:
    assert HARNESS.failed_launch({"launcher_exit_code": 0})
    assert HARNESS.failed_launch(
        {
            "launcher_exit_code": 0,
            "thermal_log_present": True,
            "timed_out": True,
        }
    )
    assert HARNESS.failed_launch(
        {
            "launcher_exit_code": 0,
            "thermal_log_present": True,
            "error": "copy failed",
        }
    )
    assert not HARNESS.failed_launch({"launcher_exit_code": 0, "thermal_log_present": True})


def test_source_hash_snapshot_matches_actual_launcher() -> None:
    import hashlib

    hashes = HARNESS.source_hashes()
    launcher = SCRIPT.parent / "demo-local.py"
    assert hashes["scripts/demo-local.py"] == hashlib.sha256(launcher.read_bytes()).hexdigest()


def test_operational_error_fails_even_when_launcher_succeeds() -> None:
    classified = HARNESS.classify("thermal fatal: OperationalError: WALSync stalled\n")
    assert classified == {
        "unexpected_fatal_timeout": 0,
        "unexpected_fatal_authority_lost": 0,
        "unexpected_fatal_total": 1,
    }
    assert HARNESS.failed_launch(
        {
            "launcher_exit_code": 0,
            "thermal_log_present": True,
            **classified,
        }
    )
