from __future__ import annotations

import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_MODULE = runpy.run_path(str(ROOT / "scripts/export-lambda-manifest-env.py"))
TERRAFORM_OUTPUTS_MODULE = runpy.run_path(str(ROOT / "scripts/export-terraform-outputs-env.py"))
PLAN_CHECK_MODULE = runpy.run_path(str(ROOT / "scripts/check-terraform-plan.py"))
MIGRATION_INVOKE_MODULE = runpy.run_path(str(ROOT / "scripts/invoke-migration-lambda.py"))
POSTMAN_ENV_MODULE = runpy.run_path(str(ROOT / "scripts/render-postman-env.py"))
REQUIRE_ENV_MODULE = runpy.run_path(str(ROOT / "scripts/require-env.py"))

render_manifest_dotenv = MANIFEST_MODULE["render_dotenv"]
validate_targets = MANIFEST_MODULE["validate_targets"]
render_terraform_outputs_dotenv = TERRAFORM_OUTPUTS_MODULE["render_dotenv"]
collect_scalar_outputs = TERRAFORM_OUTPUTS_MODULE["collect_scalar_outputs"]
validate_required_outputs = TERRAFORM_OUTPUTS_MODULE["validate_required_outputs"]
assert_no_nat_gateways = PLAN_CHECK_MODULE["assert_no_nat_gateways"]
assert_lambda_vpc_placement = PLAN_CHECK_MODULE["assert_lambda_vpc_placement"]
validate_migration_payload = MIGRATION_INVOKE_MODULE["validate_migration_payload"]
render_postman_environment = POSTMAN_ENV_MODULE["render_environment"]
validate_required_env_vars = REQUIRE_ENV_MODULE["validate_required_env_vars"]


def test_export_lambda_manifest_env_renders_expected_values() -> None:
    services = validate_targets(
        [
            {
                "service": "ingestion",
                "artifact_key": "lambdas/abc/ingestion.zip",
                "artifact_path": "dist/lambdas/ingestion.zip",
                "git_revision": "abc",
                "handler": "ingestion_service.lambda_handler.handler",
            },
            {
                "service": "query",
                "artifact_key": "lambdas/abc/query.zip",
                "artifact_path": "dist/lambdas/query.zip",
                "git_revision": "abc",
                "handler": "query_service.lambda_handler.handler",
            },
            {
                "service": "worker",
                "artifact_key": "lambdas/abc/worker.zip",
                "artifact_path": "dist/lambdas/worker.zip",
                "git_revision": "abc",
                "handler": "processing_worker.lambda_handler.handler",
            },
            {
                "service": "migration",
                "artifact_key": "lambdas/abc/migration.zip",
                "artifact_path": "dist/lambdas/migration.zip",
                "git_revision": "abc",
                "handler": "packages.db.migration_handler.handler",
            },
        ]
    )

    dotenv = render_manifest_dotenv(services)

    assert "LAMBDA_ARTIFACT_REVISION=abc" in dotenv
    assert "STAGING_ARTIFACT_REVISION=abc" in dotenv
    assert "LAMBDA_QUERY_ARTIFACT_KEY=lambdas/abc/query.zip" in dotenv
    assert "LAMBDA_MIGRATION_HANDLER=packages.db.migration_handler.handler" in dotenv


def test_export_terraform_outputs_env_keeps_scalar_values() -> None:
    dotenv = render_terraform_outputs_dotenv(
        {
            "api_endpoint": {"value": "https://example.execute-api.ca-central-1.amazonaws.com"},
            "migration_function_name": {"value": "telemetry-lab-staging-migration"},
            "function_names": {"value": {"ingestion": "ignored-map"}},
        }
    )

    assert (
        "TERRAFORM_OUTPUT_API_ENDPOINT=https://example.execute-api.ca-central-1.amazonaws.com"
        in dotenv
    )
    assert "TERRAFORM_OUTPUT_MIGRATION_FUNCTION_NAME=telemetry-lab-staging-migration" in dotenv
    assert "FUNCTION_NAMES" not in dotenv


def test_validate_required_terraform_outputs_rejects_missing_values() -> None:
    scalar_outputs = collect_scalar_outputs(
        {
            "api_endpoint": {"value": "https://example.execute-api.ca-central-1.amazonaws.com"},
            "function_names": {"value": {"ingestion": "ignored-map"}},
        }
    )

    with pytest.raises(RuntimeError, match="migration_function_name"):
        validate_required_outputs(scalar_outputs, ["api_endpoint", "migration_function_name"])


def test_terraform_plan_checks_accept_expected_layout() -> None:
    assert_no_nat_gateways([])
    assert_lambda_vpc_placement(
        [
            {
                "address": 'module.lambda_services.aws_lambda_function.this["ingestion"]',
                "type": "aws_lambda_function",
                "name": "this",
                "index": "ingestion",
                "values": {"vpc_config": []},
            },
            {
                "address": 'module.lambda_services.aws_lambda_function.this["query"]',
                "type": "aws_lambda_function",
                "name": "this",
                "index": "query",
                "values": {"vpc_config": [{}]},
            },
            {
                "address": 'module.lambda_services.aws_lambda_function.this["worker"]',
                "type": "aws_lambda_function",
                "name": "this",
                "index": "worker",
                "values": {"vpc_config": [{}]},
            },
            {
                "address": 'module.lambda_services.aws_lambda_function.this["migration"]',
                "type": "aws_lambda_function",
                "name": "this",
                "index": "migration",
                "values": {"vpc_config": [{}]},
            },
        ]
    )


def test_validate_migration_payload_accepts_schema_at_head() -> None:
    validate_migration_payload(
        {
            "status": "ok",
            "operation": "migrate",
            "requested_revision": "head",
            "requested_revision_resolved": "0001_initial",
            "current_revision": "0001_initial",
            "expected_head_revision": "0001_initial",
            "schema_matches_expected_head": True,
        },
        action="migrate",
        expected_head="0001_initial",
        revision="head",
    )


def test_validate_migration_payload_rejects_schema_drift() -> None:
    with pytest.raises(RuntimeError, match="Schema gate failed"):
        validate_migration_payload(
            {
                "status": "ok",
                "operation": "inspect",
                "current_revision": "0000_previous",
                "expected_head_revision": "0001_initial",
                "schema_matches_expected_head": False,
            },
            action="inspect",
            expected_head="0001_initial",
            revision="head",
        )


def test_render_postman_environment_overrides_aws_values() -> None:
    rendered = render_postman_environment(
        {
            "values": [
                {"key": "baseUrl", "value": "https://example.invalid", "type": "default"},
                {"key": "queryLimit", "value": "5", "type": "default"},
            ]
        },
        base_url="https://api.example.com/",
        building_id="building-123",
        zone_id="zone-9",
        query_limit="10",
        smoke_max_attempts="7",
        telemetry_timestamp="2026-04-10T21:00:00Z",
        staging_token="staging-token",
    )
    values = {item["key"]: item["value"] for item in rendered["values"]}

    assert values["baseUrl"] == "https://api.example.com"
    assert values["telemetryBuildingId"] == "building-123"
    assert values["zoneId"] == "zone-9"
    assert values["queryLimit"] == "10"
    assert values["smokeMaxAttempts"] == "7"
    assert values["stagingToken"] == "staging-token"
    assert values["telemetryTimestamp"] == "2026-04-10T21:00:00Z"


def test_validate_required_env_vars_rejects_missing_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_OIDC_ROLE_ARN", raising=False)

    with pytest.raises(RuntimeError, match="AWS_OIDC_ROLE_ARN"):
        validate_required_env_vars(["AWS_OIDC_ROLE_ARN"])
