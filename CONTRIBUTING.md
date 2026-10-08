# Contributing

Keep changes focused on observable behavior. Preserve explicit evidence states: missing or unavailable proof must never become an invented success. Update the relevant contract documentation when behavior changes.

## Development checks

Use Python 3.12, `uv`, Node.js for browser-controller tests, and Docker for real integration checks:

```sh
uv sync --frozen --python 3.12 --group dev
uv run ruff check .
uv run mypy .
uv run pytest
node --test tests/ui/*.test.cjs
PYTHON=.venv/bin/python sh scripts/test-postgres.sh
PYTHON=.venv/bin/python sh scripts/test-thermal-command-migration.sh
sh scripts/demo-local.sh --port 8089 --test
sh scripts/demo-local.sh --port 8090 --test-thermostat --startup-order runtime-first
sh scripts/demo-local.sh --port 8093 --test-crash-windows
```

Ordinary pytest execution can skip opt-in integration tests. The launcher test modes provision real disposable PostgreSQL and LocalStack services and clean them up on exit. Check scripts and GitHub Actions for the exact environment required by each suite.

CI separates quality, integration, Lambda bundle verification and Terraform validation. Packaging checks validate artifacts; Terraform checks validate configuration. Neither deploys the application.

Describe the problem, changed behavior, checks actually run and any limits in a pull request. Keep generated caches, runtime logs, credentials, Terraform state and local environment files out of version control. Use synthetic data in examples and tests.

Cloud resource creation, deployment and destruction require an explicit execution decision. Local demo work does not authorize those operations.
