from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SERVICES = ("ingestion", "query", "worker", "migration")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Lambda manifest metadata as dotenv variables."
    )
    parser.add_argument("--manifest", type=Path, default=ROOT / "dist/lambdas/manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/lambdas/manifest.env")
    return parser.parse_args()


def load_manifest(manifest_path: Path) -> list[dict[str, object]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    targets = manifest.get("targets")
    if not isinstance(targets, list):
        raise RuntimeError("Manifest does not contain a 'targets' list")
    return targets


def get_required_str(target: dict[str, object], key: str) -> str:
    value = target.get(key)
    if not isinstance(value, str):
        raise RuntimeError(f"Manifest target is missing required string field: {key}")
    return value


def validate_targets(targets: list[dict[str, object]]) -> dict[str, dict[str, str]]:
    services: dict[str, dict[str, str]] = {}
    revisions: set[str] = set()
    required_service_names = {service for service in REQUIRED_SERVICES}

    for target in targets:
        service = get_required_str(target, "service")
        artifact_key = get_required_str(target, "artifact_key")
        artifact_path = get_required_str(target, "artifact_path")
        git_revision = get_required_str(target, "git_revision")
        handler = get_required_str(target, "handler")
        services[service] = {
            "artifact_key": artifact_key,
            "artifact_path": artifact_path,
            "git_revision": git_revision,
            "handler": handler,
        }
        revisions.add(git_revision)

    missing = sorted(required_service_names - set(services))
    if missing:
        raise RuntimeError(f"Manifest is missing required services: {', '.join(missing)}")
    if len(revisions) != 1:
        raise RuntimeError("Manifest targets must all share the same git revision")
    return services


def render_dotenv(services: dict[str, dict[str, str]]) -> str:
    git_revision = services[REQUIRED_SERVICES[0]]["git_revision"]
    lines = [
        f"LAMBDA_ARTIFACT_REVISION={git_revision}",
        f"STAGING_ARTIFACT_REVISION={git_revision}",
    ]
    for service in REQUIRED_SERVICES:
        upper_name = service.upper()
        lines.append(f"LAMBDA_{upper_name}_ARTIFACT_KEY={services[service]['artifact_key']}")
        lines.append(f"LAMBDA_{upper_name}_ARTIFACT_PATH={services[service]['artifact_path']}")
        lines.append(f"LAMBDA_{upper_name}_HANDLER={services[service]['handler']}")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    targets = load_manifest(args.manifest.resolve())
    services = validate_targets(targets)
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_dotenv(services), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
