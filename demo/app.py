"""Local-only ASGI composition; not a public access or deployment mechanism."""

import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from ingestion_service.main import app as ingestion_app
from query_service.main import app as query_app

from demo.queue_observation import QueueObservation, local_queue_observation
from demo.thermal_routes import multiplier, runtime_observation
from demo.thermal_routes import router as thermal_router
from packages.config import get_settings

settings = get_settings()
if settings.is_staging or os.environ.get("TELEMETRY_LAB_LOCAL_DEMO") != "1":
    raise RuntimeError("The demo entry point is local only")

multiplier()  # Fail at startup on invalid server physics configuration.
app = FastAPI(docs_url=None, redoc_url=None)
app.include_router(thermal_router)
app.mount("/ingestion", ingestion_app)
app.mount("/query", query_app)


@app.get("/environment")
def environment() -> dict:
    return {
        "environment": "local",
        "runtime": "local-python-process",
        "process_id": os.getpid(),
        "thermal_runtime": runtime_observation(),
        "transport": "SQS compatible emulator (LocalStack in scripts/demo-local.py)",
        "release_revision": settings.release_revision,
    }


@lru_cache(maxsize=1)
def queue_reader() -> QueueObservation:
    return local_queue_observation(settings)


@app.get("/queue-observation")
def queue_observation(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return queue_reader().snapshot()


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True))
