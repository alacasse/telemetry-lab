from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VPC_ENABLED = {
    "ingestion": False,
    "query": True,
    "worker": True,
    "migration": True,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate key Terraform plan invariants.")
    parser.add_argument("--plan-json", type=Path, default=ROOT / "dist/terraform/plan.json")
    return parser.parse_args()


def walk_resources(module: dict[str, Any]) -> list[dict[str, Any]]:
    resources = list(module.get("resources", []))
    for child in module.get("child_modules", []):
        resources.extend(walk_resources(child))
    return resources


def load_resources(plan_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    resource_changes = payload.get("resource_changes", [])
    planned_values = payload.get("planned_values", {})
    root_module = planned_values.get("root_module")
    resources = walk_resources(root_module) if isinstance(root_module, dict) else []
    return resource_changes, resources


def assert_no_nat_gateways(resource_changes: list[dict[str, Any]]) -> None:
    nat_gateways = [
        change.get("address")
        for change in resource_changes
        if change.get("type") == "aws_nat_gateway"
    ]
    if nat_gateways:
        raise RuntimeError(f"Unexpected NAT gateway resources in plan: {nat_gateways}")


def assert_lambda_vpc_placement(resources: list[dict[str, Any]]) -> None:
    lambda_resources = {
        resource.get("index"): resource
        for resource in resources
        if resource.get("type") == "aws_lambda_function"
        and resource.get("name") == "this"
        and resource.get("address", "").startswith("module.lambda_services.")
    }

    missing = sorted(set(EXPECTED_VPC_ENABLED) - set(lambda_resources))
    if missing:
        raise RuntimeError(f"Missing Lambda resources in plan: {', '.join(missing)}")

    for service, vpc_enabled in EXPECTED_VPC_ENABLED.items():
        resource = lambda_resources[service]
        values = resource.get("values", {})
        vpc_config = values.get("vpc_config") or []
        has_vpc_config = bool(vpc_config)
        if has_vpc_config != vpc_enabled:
            raise RuntimeError(
                "Unexpected VPC placement for "
                f"{service}: expected {vpc_enabled}, got {has_vpc_config}"
            )


def main() -> None:
    resource_changes, resources = load_resources(parse_args().plan_json.resolve())
    assert_no_nat_gateways(resource_changes)
    assert_lambda_vpc_placement(resources)
    print("Terraform plan invariants passed")


if __name__ == "__main__":
    main()
