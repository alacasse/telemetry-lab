"""Run inside the AWS Lambda runtime image, with no host site-packages available."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile


def main() -> None:
    output = Path(sys.argv[1]).resolve()
    manifest = json.loads((output / "manifest.json").read_text())
    for target in manifest["targets"]:
        archive_path = output / f"{target['service']}.zip"
        assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == target["sha256"]
        with tempfile.TemporaryDirectory() as directory:
            with ZipFile(archive_path) as archive:
                archive.extractall(directory)
            subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    "import sys,importlib; sys.path.insert(0,sys.argv[1]); "
                    "module,attribute=sys.argv[2].rsplit('.',1); "
                    "assert callable(getattr(importlib.import_module(module),attribute))",
                    directory,
                    target["handler"],
                ],
                cwd=directory,
                check=True,
            )
        print(f"verified {target['service']}: checksum and isolated runtime import")


if __name__ == "__main__":
    main()
