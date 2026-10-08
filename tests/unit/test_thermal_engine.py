from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from thermal_engine.config import EngineConfig
from thermal_engine.transports import ThermalQueue, publish_reading


@pytest.fixture
def local_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return {
        "APP_ENV": "local", "THERMAL_TRANSPORT_MODE": "local-emulator",
        "AWS_REGION": "ca-central-1", "RELEASE_REVISION": "unit-revision",
        "QUEUE_BACKEND": "sqs", "DATABASE_URL": "postgresql+psycopg://u:p@postgres/db",
        "AWS_ENDPOINT_URL": "http://localstack:4566", "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test", "THERMAL_INGESTION_URL": "http://ingestion:8000/telemetry",
        "QUEUE_URL": "http://localstack:4566/000000000000/measurements",
        "THERMAL_QUEUE_URL": "http://localstack:4566/000000000000/commands",
    }


def aws_env(local: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in local.items() if key not in {
        "DATABASE_URL", "AWS_ENDPOINT_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    }}
    env.update({
        "APP_ENV": "staging", "THERMAL_TRANSPORT_MODE": "aws",
        "DATABASE_SECRET_ARN": "synthetic-db-secret", "STAGING_AUTH_SECRET_ARN": "synthetic-auth",
        "THERMAL_INGESTION_URL": "https://ingestion.example/telemetry",
        "QUEUE_URL": "https://sqs.ca-central-1.amazonaws.com/123456789012/measurements",
        "THERMAL_QUEUE_URL": "https://sqs.ca-central-1.amazonaws.com/123456789012/commands",
    })
    return env


def test_config_allows_explicit_docker_dns_and_never_reads_dotenv(
    local_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("APP_ENV=staging\nDATABASE_URL=sqlite:///ignored.db\n")
    config = EngineConfig.from_env(local_env)
    assert config.settings.database_url == local_env["DATABASE_URL"]
    assert config.time_multiplier == 1
    assert config.initialize is True


@pytest.mark.parametrize("key,value", [
    ("THERMAL_TRANSPORT_MODE", ""), ("AWS_REGION", ""), ("RELEASE_REVISION", ""),
    ("QUEUE_BACKEND", "memory"), ("AWS_ACCESS_KEY_ID", "real"),
    ("AWS_SECRET_ACCESS_KEY", ""), ("AWS_SESSION_TOKEN", "session"),
    ("AWS_ENDPOINT_URL", ""), ("DATABASE_URL", "sqlite:///test.db"),
    ("THERMAL_INGESTION_URL", "/telemetry"),
    ("THERMAL_INGESTION_URL", "http://u:p@ingestion/telemetry"),
    ("THERMAL_TIME_MULTIPLIER", "nan"), ("THERMAL_TIME_MULTIPLIER", "3"),
    ("THERMAL_INITIALIZE_THERMOSTAT", "yes"),
    ("THERMAL_QUEUE_URL", "http://LOCALSTACK:4566/000000000000/%6deasurements/"),
    ("THERMAL_QUEUE_URL", "http://localhost:4566/000000000000/measurements"),
])
def test_invalid_dependency_configuration_is_rejected(
    local_env: dict[str, str], key: str, value: str,
) -> None:
    local_env[key] = value
    with pytest.raises(ValueError):
        EngineConfig.from_env(local_env)


@pytest.mark.parametrize("key,value", [
    ("AWS_ENDPOINT_URL", "http://localhost:4566"),
    ("AWS_ENDPOINT_URL_SQS", "https://override.example"),
    ("DATABASE_URL", "postgresql://u:p@host/db"),
    ("DATABASE_SECRET_ARN", ""), ("STAGING_AUTH_SECRET_ARN", ""),
    ("THERMAL_INGESTION_URL", "http://ingestion.example/telemetry"),
    ("THERMAL_QUEUE_URL", "https://queue.amazonaws.com/123456789012/commands"),
    ("THERMAL_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123456789012/commands"),
])
def test_aws_configuration_rejects_missing_secrets_and_endpoint_overrides(
    local_env: dict[str, str], key: str, value: str,
) -> None:
    env = aws_env(local_env)
    env[key] = value
    with pytest.raises(ValueError):
        EngineConfig.from_env(env)


@pytest.mark.parametrize("mode", ["aws", "local-emulator"])
@pytest.mark.parametrize("queue_variable", ["THERMAL_QUEUE_URL", "QUEUE_URL"])
def test_fifo_queues_are_rejected_before_acquiring_authority(
    local_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, mode: str, queue_variable: str,
) -> None:
    from thermal_engine import main

    env = aws_env(local_env) if mode == "aws" else local_env
    env[queue_variable] += ".fifo"
    with pytest.raises(ValueError, match="FIFO is unsupported"):
        EngineConfig.from_env(env)
    load_config = EngineConfig.from_env
    monkeypatch.setattr(main.EngineConfig, "from_env", lambda: load_config(env))
    create_sessions = Mock()
    monkeypatch.setattr(main, "create_authority_session_factory", create_sessions)
    assert main.run() == 1
    create_sessions.assert_not_called()


@pytest.mark.parametrize("mode", ["aws", "local-emulator"])
def test_sqs_exact_receipts_and_sdk_credential_chain(
    local_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    env = aws_env(local_env) if mode == "aws" else local_env
    config = EngineConfig.from_env(env)
    client = Mock()
    delivery = {"Body": '{ "exact" : 1 }', "ReceiptHandle": "exact-handle"}
    client.send_message.return_value = {"MessageId": "actual-receipt"}
    client.receive_message.return_value = {"Messages": [delivery]}
    factory = Mock(return_value=client)
    monkeypatch.setattr("thermal_engine.transports.boto3.client", factory)
    queue = ThermalQueue(config)
    kwargs = factory.call_args.kwargs
    assert not any(key.startswith("aws_") for key in kwargs)
    assert kwargs.get("endpoint_url") == (env.get("AWS_ENDPOINT_URL") if mode != "aws" else None)
    bounded = kwargs["config"]
    assert (bounded.connect_timeout, bounded.read_timeout) == (1, 2)
    assert bounded.retries == {"total_max_attempts": 1}
    assert queue.publish(delivery["Body"]) == "actual-receipt"
    client.send_message.assert_called_once_with(
        QueueUrl=config.queue_url, MessageBody=delivery["Body"],
    )
    assert queue.receive()[0] is delivery
    client.receive_message.assert_called_once_with(
        QueueUrl=config.queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=1, VisibilityTimeout=3,
    )
    queue.acknowledge(delivery["ReceiptHandle"])
    client.delete_message.assert_called_once_with(
        QueueUrl=config.queue_url, ReceiptHandle="exact-handle",
    )
    client.get_queue_url.assert_not_called()
    client.create_queue.assert_not_called()
    client.send_message.side_effect = RuntimeError("transport failed")
    with pytest.raises(RuntimeError, match="transport failed"):
        queue.publish("body")


def test_http_sends_exact_utf8_body_identity_auth_and_returns_actual_receipt() -> None:
    body = '{ "value": "é", "unchanged" : true }'
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"receipt": "actual"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert publish_reading(client, "https://ingestion.example/telemetry", body, "simulation",
                               token="synthetic") == {"receipt": "actual"}
    request = requests[0]
    assert request.content == body.encode("utf-8")
    assert request.headers["X-Correlation-Id"] == "simulation"
    assert request.headers["X-Staging-Token"] == "synthetic"


def test_http_transport_errors_propagate() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            publish_reading(client, "https://ingestion.example/telemetry", "body", "simulation")


@pytest.mark.parametrize("response", [httpx.Response(200, text="broken-json"),
                                      httpx.Response(200, json=["unexpected"])])
def test_invalid_http_receipt_is_a_retryable_transport_error(response: httpx.Response) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(httpx.DecodingError):
            publish_reading(client, "https://ingestion.example/telemetry", "body", "simulation")


def test_invalid_startup_configuration_has_no_database_side_effects(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from thermal_engine import main

    monkeypatch.setenv("THERMAL_TRANSPORT_MODE", "invalid")
    create_sessions = Mock()
    monkeypatch.setattr(main, "create_authority_session_factory", create_sessions)
    assert main.run() == 1
    create_sessions.assert_not_called()
    assert capsys.readouterr().out == "Thermal engine startup failed: ValueError\n"


def test_secret_resolution_precedes_authority_and_http_uses_token(
    local_env: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from thermal_engine import main

    config = EngineConfig.from_env(aws_env(local_env))
    calls: list[str] = []

    def database(_: Any) -> str:
        calls.append("database-secret")
        return "postgresql+psycopg://u:p@host/db"

    def token(_: Any) -> str:
        calls.append("auth-secret")
        return "synthetic-auth-token"

    monkeypatch.setattr(type(config.settings), "resolve_database_url", database)
    monkeypatch.setattr(type(config.settings), "resolve_staging_auth_token", token)
    monkeypatch.setattr(main.EngineConfig, "from_env", lambda: config)
    monkeypatch.setattr(main, "create_authority_session_factory", lambda _: calls.append("pool"))
    monkeypatch.setattr(main, "PgAuthority", lambda *args, **kwargs: calls.append("authority"))
    monkeypatch.setattr(main, "ThermalQueue", lambda _: Mock())
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(main.httpx, "Client", Mock(return_value=client))
    publish = Mock(return_value={"receipt": "actual"})
    monkeypatch.setattr(main, "publish_reading", publish)

    def process(*args: Any, **kwargs: Any) -> int:
        assert calls == ["database-secret", "auth-secret", "pool", "pool", "authority"]
        with kwargs["transports"]() as transports:
            assert transports.publish_reading("exact-body", "simulation") == {"receipt": "actual"}
        return 75

    monkeypatch.setattr(main, "run_process", process)
    assert main.run() == 75
    publish.assert_called_once_with(
        client, config.ingestion_url, "exact-body", "simulation", token="synthetic-auth-token",
    )


def test_local_demo_loopback_guard_is_not_weakened(monkeypatch: pytest.MonkeyPatch) -> None:
    from demo.thermal_runtime import ingestion_url

    monkeypatch.setenv("THERMAL_INGESTION_URL", "http://ingestion:8000/telemetry")
    with pytest.raises(ValueError, match="local ingestion route"):
        ingestion_url()
