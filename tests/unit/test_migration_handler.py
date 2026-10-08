from __future__ import annotations

from pathlib import Path

import pytest

from packages.db.migration_handler import inspect_schema, run_migrations


def test_run_migrations_reports_current_and_head_revision(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'telemetry_lab.sqlite'}"

    result = run_migrations(database_url=database_url)

    assert result["status"] == "ok"
    assert result["operation"] == "migrate"
    assert result["requested_revision"] == "head"
    assert result["requested_revision_resolved"] == "0007_shared_room_authority"
    assert result["previous_revision"] is None
    assert result["current_revision"] == "0007_shared_room_authority"
    assert result["expected_head_revision"] == "0007_shared_room_authority"
    assert result["schema_matches_requested_revision"] is True
    assert result["schema_matches_expected_head"] is True


def test_inspect_schema_reports_head_match_after_migration(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'telemetry_lab.sqlite'}"
    run_migrations(database_url=database_url)

    result = inspect_schema(database_url=database_url)

    assert result == {
        "status": "ok",
        "operation": "inspect",
        "expected_head_revision": "0007_shared_room_authority",
        "current_revision": "0007_shared_room_authority",
        "schema_matches_expected_head": True,
    }


def test_alembic_preserves_url_encoded_password() -> None:
    from packages.db.migration_handler import get_alembic_config

    url = "postgresql+psycopg://demo:synthetic%40pass%25word@localhost/telemetry_lab"
    assert get_alembic_config(url).get_main_option("sqlalchemy.url") == url


def test_upgrade_existing_database_from_outside_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import create_engine, text

    database_url = f"sqlite:///{tmp_path / 'existing.sqlite'}"
    run_migrations("0001_initial", database_url=database_url)
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO telemetry_events
            (event_id,building_id,zone_id,event_timestamp,received_at,temperature_c,
             humidity_pct,occupancy,co2_ppm,hvac_mode,airflow_pct,raw_payload,
             anomaly_flags,processing_status,idempotency_key)
            VALUES ('legacy','b','z','2026-10-07','2026-10-07',23,45,8,1400,
                    'ventilation',40,'{}','[]','processed','legacy-key')
        """)
        )
    monkeypatch.chdir(tmp_path)
    assert run_migrations(database_url=database_url)["schema_matches_expected_head"]
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT event_id,correlation_id FROM telemetry_events")
        ).one() == ("legacy", None)
    engine.dispose()


def test_typed_migration_preserves_command_proofs_and_limits_legacy(tmp_path: Path) -> None:
    from sqlalchemy import create_engine, text

    url = f"sqlite:///{tmp_path / 'thermal.sqlite'}"
    run_migrations("0003_thermal_simulations", database_url=url)
    engine = create_engine(url)
    with engine.begin() as connection:
        for sequence, status, heater_on in [
            (1, "pending", True),
            (2, "applied", False),
            (3, "cancelled", False),
        ]:
            connection.execute(
                text("""
                INSERT INTO thermal_commands
                (simulation_id,sequence,decision_id,applied_state_version,reading_sequence,
                 heater_on,created_at,transport_message_id,published_at,applied_at,status)
                VALUES ('legacy',:sequence,:decision,17,:sequence,:heater_on,'2026-10-07',
                        :receipt,'2026-10-07','2026-10-07',:status)
            """),
                {
                    "sequence": sequence,
                    "decision": f"decision-{sequence}",
                    "heater_on": heater_on,
                    "receipt": f"receipt-{sequence}",
                    "status": status,
                },
            )
        original = (
            connection.execute(text("SELECT * FROM thermal_commands ORDER BY sequence"))
            .mappings()
            .all()
        )
    run_migrations(database_url=url, revision="0004_typed_thermal_commands")
    with engine.begin() as connection:
        migrated = (
            connection.execute(text("SELECT * FROM thermal_commands ORDER BY sequence"))
            .mappings()
            .all()
        )
        for old, new in zip(original, migrated, strict=True):
            assert all(new[key] == value for key, value in old.items())
            assert new["command_type"] == ("heating.start" if old["heater_on"] else "heating.stop")
            assert new["legacy_wire_allowed"] == 1
        connection.execute(
            text("""
            INSERT INTO thermal_commands
            (simulation_id,sequence,decision_id,reading_sequence,heater_on,created_at,status,command_type)
            VALUES ('new',1,'decision-new',1,true,'2026-10-07','pending','heating.start')
        """)
        )
        assert (
            connection.scalar(
                text("SELECT legacy_wire_allowed FROM thermal_commands WHERE simulation_id='new'")
            )
            == 0
        )
    from alembic import command

    from packages.db.migration_handler import get_alembic_config

    command.downgrade(get_alembic_config(url), "0003_thermal_simulations")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT * FROM thermal_commands WHERE simulation_id='legacy' ORDER BY sequence"
                )
            )
            .mappings()
            .all()
            == original
        )
    engine.dispose()
