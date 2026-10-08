# Versions, image content and Python distributions are pinned for repeatable builds.
FROM ghcr.io/astral-sh/uv:0.11.6@sha256:b1e699368d24c57cda93c338a57a8c5a119009ba809305cc8e86986d4a006754 AS uv
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=dependencies /app/.venv /app/.venv
COPY packages ./packages
COPY services/thermal-engine/thermal_engine ./thermal_engine
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "-m", "thermal_engine.main"]

# Only the disposable validation composition uses this target. The runtime above
# contains neither the demo launcher, migration runner nor validation controls.
FROM runtime AS validation
COPY services/ingestion-service/ingestion_service ./ingestion_service
COPY services/processing-worker/processing_worker ./processing_worker
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts/thermal-engine-probe.py ./scripts/thermal-engine-probe.py
COPY tests/integration/test_thermal_engine_container.py ./tests/integration/test_thermal_engine_container.py
ENTRYPOINT ["python"]

# A plain docker build selects the deployable runtime, never the fixture image.
FROM runtime AS final
