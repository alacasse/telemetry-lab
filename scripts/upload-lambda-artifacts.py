from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload packaged Lambda artifacts to S3.")
    parser.add_argument("--manifest", type=Path, default=ROOT / "dist/lambdas/manifest.json")
    parser.add_argument("--bucket", required=True)
    return parser.parse_args()


def resolve_artifact_path(path_value: str) -> Path:
    artifact_path = Path(path_value)
    if artifact_path.is_absolute():
        return artifact_path
    return ROOT / artifact_path


def load_targets(manifest_path: Path) -> list[dict[str, str]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_dirty") is not False:
        raise RuntimeError("Rebuild artifacts from a clean, committed checkout before uploading")
    targets = manifest.get("targets")
    if not isinstance(targets, list):
        raise RuntimeError("Manifest does not contain a 'targets' list")

    resolved_targets: list[dict[str, str]] = []
    for target in targets:
        service = target.get("service")
        artifact_path = target.get("artifact_path")
        artifact_key = target.get("artifact_key")
        digest = target.get("sha256")
        if not all(isinstance(value, str) for value in (service, artifact_path, artifact_key)):
            raise RuntimeError("Manifest target is missing required upload fields")
        path = resolve_artifact_path(artifact_path)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Artifact missing or checksum mismatch for {service}")
        resolved_targets.append(
            {
                "service": service,
                "artifact_path": artifact_path,
                "artifact_key": artifact_key,
            }
        )
    return resolved_targets


def main() -> None:
    args = parse_args()
    targets = load_targets(args.manifest.resolve())
    s3_client = boto3.client("s3")

    for target in targets:
        artifact_path = resolve_artifact_path(target["artifact_path"])
        if not artifact_path.is_file():
            raise RuntimeError(f"Artifact file not found: {artifact_path}")
        s3_client.upload_file(str(artifact_path), args.bucket, target["artifact_key"])
        print(f"uploaded {target['service']} -> s3://{args.bucket}/{target['artifact_key']}")


if __name__ == "__main__":
    main()
