FROM python:3.12-slim

ENV PATH="/root/.local/bin:${PATH}"
ENV PYTHONPATH="/app"
WORKDIR /app

RUN python -m pip install uv

COPY pyproject.toml README.md /app/
COPY packages /app/packages
COPY services/query-service /app/services/query-service

RUN uv sync

CMD ["uv", "run", "--directory", "/app/services/query-service", "uvicorn", "query_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
