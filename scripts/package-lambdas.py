from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = ROOT / "pyproject.toml"
DEFAULT_OUTPUT_DIR = ROOT / "dist/lambdas"
MAX_COMPRESSED_BYTES = 70 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
DEFAULT_RUNTIME = "python3.12"
DEFAULT_ARCHITECTURE = "x86_64"
IMPORT_CHECK = """
from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

archive_path = Path(sys.argv[1])
module_name, attribute_name = sys.argv[2].rsplit(".", maxsplit=1)

with tempfile.TemporaryDirectory() as temp_dir:
    with ZipFile(archive_path) as archive:
        archive.extractall(temp_dir)
    sys.path.insert(0, temp_dir)
    module = importlib.import_module(module_name)
    getattr(module, attribute_name)
"""


@dataclass(frozen=True)
class BundleEntry:
    source: Path
    destination: Path


@dataclass(frozen=True)
class LambdaPackageTarget:
    name: str
    handler: str
    bundle_entries: tuple[BundleEntry, ...]


SHARED_ENTRY = BundleEntry(source=ROOT / "packages", destination=Path("packages"))

TARGETS = (
    LambdaPackageTarget(
        name="ingestion",
        handler="ingestion_service.lambda_handler.handler",
        bundle_entries=(
            BundleEntry(
                source=ROOT / "services/ingestion-service/ingestion_service",
                destination=Path("ingestion_service"),
            ),
            SHARED_ENTRY,
        ),
    ),
    LambdaPackageTarget(
        name="query",
        handler="query_service.lambda_handler.handler",
        bundle_entries=(
            BundleEntry(
                source=ROOT / "services/query-service/query_service",
                destination=Path("query_service"),
            ),
            SHARED_ENTRY,
        ),
    ),
    LambdaPackageTarget(
        name="worker",
        handler="processing_worker.lambda_handler.handler",
        bundle_entries=(
            BundleEntry(
                source=ROOT / "services/processing-worker/processing_worker",
                destination=Path("processing_worker"),
            ),
            SHARED_ENTRY,
        ),
    ),
    LambdaPackageTarget(
        name="migration",
        handler="packages.db.migration_handler.handler",
        bundle_entries=(
            SHARED_ENTRY,
            BundleEntry(source=ROOT / "migrations", destination=Path("migrations")),
            BundleEntry(source=ROOT / "alembic.ini", destination=Path("alembic.ini")),
        ),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package Lambda-targeted services into deployable zip bundles."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where the zip artifacts will be written.",
    )
    parser.add_argument(
        "--runtime",
        default=DEFAULT_RUNTIME,
        help="Runtime metadata recorded in the manifest.",
    )
    parser.add_argument(
        "--architecture",
        default=DEFAULT_ARCHITECTURE,
        help="Architecture metadata recorded in the manifest.",
    )
    return parser.parse_args()


def load_runtime_dependency_names() -> list[str]:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    environment = {key: str(value) for key, value in default_environment().items()}
    environment["extra"] = ""
    names: list[str] = []
    for dependency in dependencies:
        requirement = Requirement(dependency)
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        names.append(str(requirement))
    return names


def resolve_runtime_distributions() -> dict[str, metadata.Distribution]:
    environment = {key: str(value) for key, value in default_environment().items()}
    pending = load_runtime_dependency_names()
    resolved: dict[str, metadata.Distribution] = {}
    visited: set[tuple[str, frozenset[str]]] = set()

    while pending:
        requested = Requirement(pending.pop())
        name = canonicalize_name(requested.name)
        key = (name, frozenset(requested.extras))
        if key in visited:
            continue
        visited.add(key)

        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError as exc:
            raise RuntimeError(f"Runtime dependency '{name}' is not installed") from exc

        resolved[name] = distribution
        for required_dependency in distribution.requires or []:
            requirement = Requirement(required_dependency)
            if requirement.marker and not any(
                requirement.marker.evaluate({**environment, "extra": extra})
                for extra in {"", *requested.extras}
            ):
                continue
            pending.append(str(requirement))

    return resolved


def should_skip_path(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def copy_runtime_dependencies(target_dir: Path) -> None:
    copied_paths: set[Path] = set()
    for distribution in resolve_runtime_distributions().values():
        for package_file in distribution.files or []:
            relative_path = Path(str(package_file))
            if ".." in relative_path.parts:
                continue
            source_path = Path(str(distribution.locate_file(package_file)))
            if not source_path.is_file() or should_skip_path(source_path):
                continue
            destination_path = target_dir / relative_path
            if destination_path in copied_paths:
                continue
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)
            copied_paths.add(destination_path)


def copy_bundle_entry(entry: BundleEntry, target_dir: Path) -> None:
    if entry.source.is_file():
        destination_path = target_dir / entry.destination
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.source, destination_path)
        return

    for source_path in sorted(entry.source.rglob("*")):
        if not source_path.is_file() or should_skip_path(source_path):
            continue
        destination_path = target_dir / entry.destination / source_path.relative_to(entry.source)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)


def iter_bundle_files(bundle_dir: Path) -> list[Path]:
    return sorted(
        path for path in bundle_dir.rglob("*") if path.is_file() and not should_skip_path(path)
    )


def write_archive(bundle_dir: Path, archive_path: Path) -> tuple[int, int]:
    files = iter_bundle_files(bundle_dir)
    uncompressed_size = sum(path.stat().st_size for path in files)
    with ZipFile(archive_path, mode="w", compression=ZIP_DEFLATED) as archive:
        for file_path in files:
            archive.write(file_path, arcname=file_path.relative_to(bundle_dir))
    compressed_size = archive_path.stat().st_size
    return compressed_size, uncompressed_size


def verify_size_limits(
    *,
    archive_path: Path,
    compressed_size: int,
    uncompressed_size: int,
) -> None:
    if compressed_size > MAX_COMPRESSED_BYTES:
        raise RuntimeError(
            f"{archive_path.name} is {compressed_size} bytes, "
            "which exceeds the compressed size limit"
        )
    if uncompressed_size > MAX_UNCOMPRESSED_BYTES:
        raise RuntimeError(
            f"{archive_path.name} is {uncompressed_size} bytes unpacked, "
            "which exceeds the Lambda limit"
        )


def verify_handler_importable(archive_path: Path, handler: str) -> None:
    subprocess.run(
        [sys.executable, "-I", "-S", "-c", IMPORT_CHECK, str(archive_path), handler],
        cwd=ROOT,
        check=True,
        env=os.environ.copy(),
    )


def verify_build_platform(runtime: str, architecture: str) -> None:
    actual_runtime = f"python{sys.version_info.major}.{sys.version_info.minor}"
    actual_architecture = {"aarch64": "arm64", "AMD64": "x86_64"}.get(
        platform.machine(), platform.machine()
    )
    if (
        platform.system() != "Linux"
        or runtime != actual_runtime
        or architecture != actual_architecture
    ):
        raise RuntimeError(
            f"Build needs Linux {runtime} {architecture}; got "
            f"{platform.system()} {actual_runtime} {actual_architecture}. "
            "Use the matching AWS SAM build image, then verify in the Lambda runtime image."
        )


def get_git_revision() -> str:
    for env_var in ("CI_COMMIT_SHA", "GITHUB_SHA", "SOURCE_VERSION"):
        value = os.getenv(env_var)
        if value:
            return value

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def get_source_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return bool(result.stdout.strip())


def clean_output_dir(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_file in output_dir.glob("*.zip"):
        stale_file.unlink()


def format_output_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_target_bundle(
    *,
    target: LambdaPackageTarget,
    output_dir: Path,
    runtime: str,
    architecture: str,
    git_revision: str,
) -> dict[str, object]:
    archive_path = output_dir / f"{target.name}.zip"
    with tempfile.TemporaryDirectory(prefix=f"bundle-{target.name}-") as temp_dir:
        bundle_dir = Path(temp_dir)
        copy_runtime_dependencies(bundle_dir)
        for entry in target.bundle_entries:
            copy_bundle_entry(entry, bundle_dir)
        compressed_size, uncompressed_size = write_archive(bundle_dir, archive_path)

    verify_size_limits(
        archive_path=archive_path,
        compressed_size=compressed_size,
        uncompressed_size=uncompressed_size,
    )
    verify_handler_importable(archive_path, target.handler)

    return {
        "service": target.name,
        "artifact_path": format_output_path(archive_path),
        "artifact_key": f"lambdas/{git_revision}/{archive_path.name}",
        "handler": target.handler,
        "runtime": runtime,
        "architecture": architecture,
        "git_revision": git_revision,
        "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "compressed_size_bytes": compressed_size,
        "uncompressed_size_bytes": uncompressed_size,
    }


def main() -> None:
    args = parse_args()
    verify_build_platform(args.runtime, args.architecture)
    output_dir = args.output_dir.resolve()
    clean_output_dir(output_dir)
    git_revision = get_git_revision()

    targets = [
        build_target_bundle(
            target=target,
            output_dir=output_dir,
            runtime=args.runtime,
            architecture=args.architecture,
            git_revision=git_revision,
        )
        for target in TARGETS
    ]
    manifest = {"source_dirty": get_source_dirty(), "targets": targets}

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    for item in targets:
        print(f"created {item['artifact_path']} ({item['handler']})")
    print(f"wrote {format_output_path(manifest_path)}")


if __name__ == "__main__":
    main()
