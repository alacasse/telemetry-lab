FROM python:3.12-slim

ENV PATH="/root/.local/bin:${PATH}"
ENV PYTHONPATH="/app"
WORKDIR /app

RUN python -m pip install uv

COPY pyproject.toml README.md /app/
COPY packages /app/packages
COPY services/processing-worker /app/services/processing-worker

RUN uv sync

CMD ["uv", "run", "--directory", "/app/services/processing-worker", "python", "-m", "processing_worker.main"]
