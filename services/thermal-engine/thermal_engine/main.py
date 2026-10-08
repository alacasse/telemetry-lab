"""Run the standalone shared-room thermal engine."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from botocore.exceptions import BotoCoreError, ClientError

from packages.thermal.authority import PgAuthority, create_authority_session_factory
from packages.thermal.process import ThermalTransports, run_process
from thermal_engine.config import EngineConfig, validate_database_url
from thermal_engine.transports import ThermalQueue, publish_reading


def run() -> int:
    try:
        config = EngineConfig.from_env()
        # Secret resolution and validation are completed before database authority
        # or startup recovery; no network call holds the authority lock.
        validate_database_url(config.settings.resolve_database_url())
        token = config.settings.resolve_staging_auth_token()
        sessions = create_authority_session_factory(config.settings)
        renewal_sessions = create_authority_session_factory(config.settings)
        authority = PgAuthority(renewal_sessions, revision=config.settings.release_revision)

        @contextmanager
        def transports() -> Iterator[ThermalTransports]:
            queue = ThermalQueue(config)
            with httpx.Client(timeout=httpx.Timeout(2, connect=1), trust_env=False) as client:
                yield ThermalTransports(
                    queue=queue,
                    publish_reading=lambda body, simulation_id: publish_reading(
                        client, config.ingestion_url, body, simulation_id, token=token,
                    ),
                )

        return run_process(
            sessions, authority, time_multiplier=config.time_multiplier,
            initialize=config.initialize, transports=transports,
            retry_exceptions=(httpx.HTTPError, BotoCoreError, ClientError),
            runtime_name="standalone-thermal", revision=config.settings.release_revision,
        )
    except Exception as exc:
        # Dependency errors may include credentials, URLs or secret payloads.
        print(f"Thermal engine startup failed: {type(exc).__name__}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
