from __future__ import annotations

import runpy
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/package-lambdas.py"))
ROOT = MODULE["ROOT"]
format_output_path = MODULE["format_output_path"]


def test_format_output_path_uses_repo_relative_path_inside_workspace() -> None:
    archive_path = ROOT / "dist/lambdas/ingestion.zip"

    assert format_output_path(archive_path) == "dist/lambdas/ingestion.zip"


def test_format_output_path_falls_back_to_absolute_path_outside_workspace(tmp_path: Path) -> None:
    archive_path = tmp_path / "artifacts" / "ingestion.zip"

    assert format_output_path(archive_path) == str(archive_path)


def test_bundle_dependencies_include_requested_psycopg_binary_extra() -> None:
    assert "psycopg-binary" in MODULE["resolve_runtime_distributions"]()


def test_archive_import_uses_lambda_handler_syntax(tmp_path: Path) -> None:
    archive_path = tmp_path / "valid.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("example.py", "def handler(event, context): return {}\n")
    MODULE["verify_handler_importable"](archive_path, "example.handler")


def test_archive_cannot_import_missing_dependency_from_build_environment(tmp_path: Path) -> None:
    archive_path = tmp_path / "incomplete.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("example.py", "import fastapi\ndef handler(event, context): return {}\n")
    with pytest.raises(subprocess.CalledProcessError):
        MODULE["verify_handler_importable"](archive_path, "example.handler")


def test_build_rejects_mismatched_runtime() -> None:
    with pytest.raises(RuntimeError, match="Build needs Linux"):
        MODULE["verify_build_platform"]("python0.0", "x86_64")


def test_upload_refuses_dirty_or_unverified_sources(tmp_path: Path) -> None:
    import json

    upload = runpy.run_path(str(ROOT / "scripts/upload-lambda-artifacts.py"))
    manifest = tmp_path / "manifest.json"
    for dirty in (True, None):
        manifest.write_text(json.dumps({"source_dirty": dirty, "targets": []}))
        with pytest.raises(RuntimeError, match="clean, committed"):
            upload["load_targets"](manifest)


def test_upload_verifies_archive_bytes_before_any_upload(tmp_path: Path) -> None:
    import hashlib
    import json

    upload = runpy.run_path(str(ROOT / "scripts/upload-lambda-artifacts.py"))
    archive = tmp_path / "worker.zip"
    archive.write_bytes(b"original archive")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_dirty": False,
                "targets": [
                    {
                        "service": "worker",
                        "artifact_path": str(archive),
                        "artifact_key": "lambdas/test/worker.zip",
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )
    assert len(upload["load_targets"](manifest)) == 1
    archive.write_bytes(b"modified archive")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        upload["load_targets"](manifest)
