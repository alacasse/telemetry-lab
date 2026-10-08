from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Terraform JSON outputs as dotenv variables."
    )
    parser.add_argument("--input", type=Path, default=ROOT / "dist/terraform/outputs.json")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/terraform/outputs.env")
    parser.add_argument("--required-output", action="append", default=[])
    return parser.parse_args()


def normalize_env_name(name: str) -> str:
    return "TERRAFORM_OUTPUT_" + name.upper().replace("-", "_")


def encode_env_value(value: Any) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    return None


def collect_scalar_outputs(payload: dict[str, Any]) -> dict[str, str]:
    scalar_outputs: dict[str, str] = {}
    for name in sorted(payload):
        value = payload[name].get("value") if isinstance(payload[name], dict) else None
        encoded = encode_env_value(value)
        if encoded is None:
            continue
        scalar_outputs[name] = encoded
    return scalar_outputs


def validate_required_outputs(scalar_outputs: dict[str, str], required_outputs: list[str]) -> None:
    missing = sorted(name for name in required_outputs if name not in scalar_outputs)
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required Terraform outputs: {joined}")


def render_dotenv(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for name, encoded in sorted(collect_scalar_outputs(payload).items()):
        lines.append(f"{normalize_env_name(name)}={encoded}")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    payload = json.loads(args.input.resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Terraform output JSON must be an object")
    scalar_outputs = collect_scalar_outputs(payload)
    validate_required_outputs(scalar_outputs, args.required_output)
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_dotenv(payload), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
