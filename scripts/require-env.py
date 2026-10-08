from __future__ import annotations

import argparse
import os


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Require non-empty environment variables.")
    parser.add_argument("--var", action="append", dest="vars", default=[])
    return parser.parse_args()


def validate_required_env_vars(names: list[str]) -> None:
    missing = sorted(name for name in names if not os.getenv(name))
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {joined}")


def main() -> None:
    args = parse_args()
    if not args.vars:
        raise RuntimeError("At least one --var argument is required")
    validate_required_env_vars(args.vars)
    print("validated")


if __name__ == "__main__":
    main()
