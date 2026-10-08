from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT,
    ROOT / "services/ingestion-service",
    ROOT / "services/query-service",
    ROOT / "services/processing-worker",
    ROOT / "services/simulator",
):
    sys.path.insert(0, str(path))
