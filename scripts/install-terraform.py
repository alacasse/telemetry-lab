from __future__ import annotations

import argparse
import stat
import urllib.request
from pathlib import Path
from zipfile import ZipFile

DEFAULT_VERSION = "1.8.5"
DEFAULT_PLATFORM = "linux_amd64"
DEFAULT_INSTALL_DIR = Path(".local/bin")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and install a Terraform binary.")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--platform", default=DEFAULT_PLATFORM)
    parser.add_argument("--install-dir", type=Path, default=DEFAULT_INSTALL_DIR)
    return parser.parse_args()


def build_download_url(version: str, platform: str) -> str:
    archive_name = f"terraform_{version}_{platform}.zip"
    return f"https://releases.hashicorp.com/terraform/{version}/{archive_name}"


def ensure_executable(path: Path) -> None:
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main() -> None:
    args = parse_args()
    install_dir = args.install_dir.resolve()
    install_dir.mkdir(parents=True, exist_ok=True)
    target_path = install_dir / "terraform"
    download_url = build_download_url(args.version, args.platform)

    with urllib.request.urlopen(download_url) as response:  # noqa: S310
        archive_path = install_dir / f"terraform-{args.version}.zip"
        archive_path.write_bytes(response.read())

    with ZipFile(archive_path) as archive:
        archive.extract("terraform", path=install_dir)

    archive_path.unlink()
    ensure_executable(target_path)
    print(
        str(target_path.relative_to(Path.cwd()))
        if target_path.is_relative_to(Path.cwd())
        else target_path
    )


if __name__ == "__main__":
    main()
