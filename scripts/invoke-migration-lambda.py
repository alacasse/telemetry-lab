from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import boto3
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG_PATH = ROOT / "alembic.ini"


def get_repo_head_revision() -> str:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    current_head = ScriptDirectory.from_config(config).get_current_head()
    if current_head is None:
        raise RuntimeError("Alembic script directory has no head revision")
    return current_head


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Invoke the Telemetry Lab migration Lambda.")
    parser.add_argument("--function-name", required=True)
    parser.add_argument("--action", choices=("migrate", "inspect"), default="migrate")
    parser.add_argument("--revision", default="head")
    parser.add_argument("--expected-head", default=get_repo_head_revision())
    return parser.parse_args()


def decode_payload(payload_bytes: bytes) -> dict[str, Any]:
    payload = json.loads(payload_bytes or b"{}")
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected migration payload shape: {payload!r}")
    return payload


def validate_migration_payload(
    payload: dict[str, Any], *, action: str, expected_head: str, revision: str
) -> None:
    if payload.get("status") != "ok":
        raise RuntimeError(f"Unexpected migration payload: {payload}")

    payload_action = payload.get("operation")
    if payload_action != action:
        raise RuntimeError(f"Unexpected migration operation: {payload}")

    payload_expected_head = payload.get("expected_head_revision")
    if payload_expected_head != expected_head:
        raise RuntimeError(
            f"Migration Lambda head revision does not match the repo head: {payload}"
        )

    if action == "migrate":
        if payload.get("requested_revision") != revision:
            raise RuntimeError(f"Unexpected requested migration revision: {payload}")
        if payload.get("current_revision") != payload.get("requested_revision_resolved"):
            raise RuntimeError(f"Schema revision mismatch after migration: {payload}")

    if payload.get("current_revision") != expected_head:
        raise RuntimeError(f"Schema gate failed, database is not at head: {payload}")
    if payload.get("schema_matches_expected_head") is not True:
        raise RuntimeError(f"Schema gate reported a mismatch: {payload}")


def main() -> None:
    args = parse_args()
    lambda_client = boto3.client("lambda")
    response = lambda_client.invoke(
        FunctionName=args.function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({"action": args.action, "revision": args.revision}).encode("utf-8"),
    )
    status_code = response.get("StatusCode")
    payload = decode_payload(response["Payload"].read())

    if response.get("FunctionError"):
        raise RuntimeError(f"Migration Lambda failed: {payload}")
    if status_code != 200:
        raise RuntimeError(f"Unexpected Lambda invoke status code: {status_code}")
    validate_migration_payload(
        payload,
        action=args.action,
        expected_head=args.expected_head,
        revision=args.revision,
    )

    print(json.dumps(payload))


if __name__ == "__main__":
    main()
