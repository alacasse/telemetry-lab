from __future__ import annotations

import pytest

from packages.aws.secrets import clear_secret_cache, get_database_url_from_secret, get_secret_string
from packages.config import Settings


class SecretsManagerStub:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0

    def get_secret_value(self, **kwargs: str) -> dict[str, str]:
        secret_id = kwargs["SecretId"]
        self.calls += 1
        return {"ARN": secret_id, "SecretString": self.payload}


def test_get_secret_string_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_secret_cache()
    client = SecretsManagerStub("token-123")
    settings = Settings(_env_file=None)
    monkeypatch.setattr(
        "packages.aws.secrets.get_secrets_manager_client", lambda settings=None: client
    )

    first = get_secret_string(
        "arn:aws:secretsmanager:ca-central-1:123:secret:token", settings=settings
    )
    second = get_secret_string(
        "arn:aws:secretsmanager:ca-central-1:123:secret:token", settings=settings
    )

    assert first == "token-123"
    assert second == "token-123"
    assert client.calls == 1
    clear_secret_cache()


def test_get_database_url_from_secret_builds_psycopg_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_secret_cache()
    client = SecretsManagerStub(
        '{"engine":"postgresql+psycopg","username":"telemetry_lab","password":"s3cr3t","host":"db.internal","port":5432,"dbname":"telemetry_lab"}'
    )
    settings = Settings(_env_file=None)
    monkeypatch.setattr(
        "packages.aws.secrets.get_secrets_manager_client", lambda settings=None: client
    )

    database_url = get_database_url_from_secret(
        "arn:aws:secretsmanager:ca-central-1:123:secret:db",
        settings=settings,
    )

    assert database_url == "postgresql+psycopg://telemetry_lab:s3cr3t@db.internal:5432/telemetry_lab"
    clear_secret_cache()
