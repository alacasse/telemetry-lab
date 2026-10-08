SHELL := /bin/sh
.SHELLFLAGS := -eu -c
.DEFAULT_GOAL := up
.ONESHELL:
.SILENT:
MAKEFLAGS += --no-builtin-rules
.SUFFIXES:

# Prefer the classic docker-compose binary when it is available, then fall back to
# the Docker CLI plugin. Both respect host-level variables such as DOCKER_HOST.
COMPOSE_FILE_PATH := $(firstword $(wildcard docker-compose.yml docker-compose.yaml))
COMPOSE_CMD ?= $(shell if command -v docker-compose >/dev/null 2>&1; then printf '%s' docker-compose; elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then printf '%s' 'docker compose'; fi)

UV ?= uv
PYTHON ?= python3
ENV_FILE ?= .env
RUN_DIR ?= .run
LOG_DIR ?= $(RUN_DIR)/logs
PID_DIR ?= $(RUN_DIR)/pids

POSTGRES_SERVICE ?= postgres
QUEUE_SERVICE ?= localstack
INGESTION_SERVICE ?= ingestion-service
QUERY_SERVICE ?= query-service
WORKER_SERVICE ?= processing-worker
SIMULATOR_SERVICE ?= simulator

CORE_SERVICES ?= $(POSTGRES_SERVICE) $(QUEUE_SERVICE) $(INGESTION_SERVICE) $(QUERY_SERVICE) $(WORKER_SERVICE)
BUILD_SERVICES ?= $(INGESTION_SERVICE) $(QUERY_SERVICE) $(WORKER_SERVICE) $(SIMULATOR_SERVICE)
DEFAULT_SERVICES ?= $(CORE_SERVICES)
TARGET_SERVICES = $(if $(strip $(SERVICE)),$(SERVICE),$(DEFAULT_SERVICES))
TARGET_BUILD_SERVICES = $(if $(strip $(SERVICE)),$(SERVICE),$(BUILD_SERVICES))
TARGET_LOG_SERVICES = $(if $(strip $(SERVICE)),$(SERVICE),$(DEFAULT_SERVICES))

INGESTION_BASE_URL ?= http://localhost:8000
QUERY_BASE_URL ?= http://localhost:8001
LOCALSTACK_HEALTH_URL ?= http://localhost:4566/_localstack/health
POSTGRES_USER ?= telemetry_lab
POSTGRES_DB ?= telemetry_lab
FOLLOW ?= 0
TIMESTAMPS ?= 0
DRY_RUN ?= 0
KEEP_VOLUMES ?= 1
MIGRATE_RETRIES ?= 20
MIGRATE_DELAY ?= 2

COMPOSE_FILE_ARG = $(if $(strip $(COMPOSE_FILE_PATH)),-f $(COMPOSE_FILE_PATH),)
COMPOSE_ENV_FILE_ARG = $(if $(wildcard $(ENV_FILE)),--env-file $(ENV_FILE),)
COMPOSE_ARGS = $(strip $(COMPOSE_FILE_ARG) $(COMPOSE_ENV_FILE_ARG))
LOG_FOLLOW_ARG = $(if $(filter 1 true yes,$(FOLLOW)),-f,)
LOG_TS_ARG = $(if $(filter 1 true yes,$(TIMESTAMPS)),--timestamps,)

.PHONY: help env bootstrap up down restart status logs ps health all clean migrate lint type-check test test-unit test-integration smoke demo wait-for-postgres

define run
cmd='$(1)'; \
if [ "$(DRY_RUN)" = "1" ]; then \
  printf '[dry-run] %s\n' "$$cmd"; \
else \
  eval "$$cmd"; \
fi
endef

define maybe_source_env
env_file='$(ENV_FILE)'; \
case "$$env_file" in \
  /*|./*|../*) env_source="$$env_file" ;; \
  *) env_source="./$$env_file" ;; \
esac; \
if [ -f "$$env_source" ]; then \
  set -a; \
  . "$$env_source"; \
  set +a; \
fi
endef

help: ## Show available targets and common variables
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "%-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\nVariables:\n"
	@printf "  SERVICE=<name>         operate on one service when supported\n"
	@printf "  DRY_RUN=1              print commands without executing them\n"
	@printf "  FOLLOW=1               stream logs for the logs target\n"
	@printf "  TIMESTAMPS=1           request timestamped logs in Compose mode\n"
	@printf "  COMPOSE_PROFILES=demo  include profiled services such as simulator\n"

env: ## Create .env from .env.example when missing
	if [ -f "$(ENV_FILE)" ]; then
		printf '%s already exists\n' "$(ENV_FILE)"
	elif [ -f ".env.example" ]; then
		$(call run,cp ".env.example" "$(ENV_FILE)")
	else
		printf 'No %s or .env.example found; skipping\n' "$(ENV_FILE)"
	fi

bootstrap: env ## Install local development dependencies with uv
	if ! command -v "$(UV)" >/dev/null 2>&1; then
		printf 'uv is required for bootstrap but was not found in PATH\n' >&2
		exit 1
	fi
	$(call run,$(UV) sync --group dev)

migrate: ## Apply Alembic migrations with the local toolchain
	if ! command -v "$(UV)" >/dev/null 2>&1; then
		printf 'uv is required to run Alembic migrations; install uv or run migrations manually\n' >&2
		exit 1
	fi
	$(call maybe_source_env)
	$(call run,$(UV) run alembic upgrade head)

wait-for-postgres: ## Wait until Postgres accepts connections
	if [ -z "$(strip $(COMPOSE_FILE_PATH))" ]; then
		printf 'wait-for-postgres is only available when a Compose stack is defined\n' >&2
		exit 1
	fi
	if [ -z "$(COMPOSE_CMD)" ]; then
		printf 'Docker Compose is required but neither docker-compose nor docker compose is available\n' >&2
		exit 1
	fi
	attempt=1
	while [ "$$attempt" -le "$(MIGRATE_RETRIES)" ]; do
		if [ "$(DRY_RUN)" = "1" ]; then
			printf '[dry-run] %s\n' "$(COMPOSE_CMD) $(COMPOSE_ARGS) exec -T $(POSTGRES_SERVICE) pg_isready -U $(POSTGRES_USER) -d $(POSTGRES_DB)"
			exit 0
		fi
		if $(COMPOSE_CMD) $(COMPOSE_ARGS) exec -T $(POSTGRES_SERVICE) pg_isready -U "$(POSTGRES_USER)" -d "$(POSTGRES_DB)" >/dev/null 2>&1; then
			printf 'Postgres is ready\n'
			exit 0
		fi
		printf 'Waiting for Postgres (%s/%s)\n' "$$attempt" "$(MIGRATE_RETRIES)"
		sleep "$(MIGRATE_DELAY)"
		attempt=$$((attempt + 1))
	done
	printf 'Postgres did not become ready in time\n' >&2
	exit 1

up: env ## Start the stack; this is the default target
ifneq ($(strip $(COMPOSE_FILE_PATH)),)
	if [ -z "$(COMPOSE_CMD)" ]; then
		printf 'Compose file found at %s but Docker Compose is unavailable\n' "$(COMPOSE_FILE_PATH)" >&2
		exit 1
	fi
	$(call run,$(COMPOSE_CMD) $(COMPOSE_ARGS) up -d $(TARGET_SERVICES))
	if [ -z "$(strip $(SERVICE))" ]; then
		$(MAKE) --no-print-directory DRY_RUN=$(DRY_RUN) wait-for-postgres
		$(MAKE) --no-print-directory DRY_RUN=$(DRY_RUN) migrate
		printf 'Stack started with Compose. Run `make health` to validate service readiness.\n'
	else
		printf 'Started %s with Compose.\n' "$(SERVICE)"
	fi
else
	if ! command -v "$(UV)" >/dev/null 2>&1; then
		printf 'No Compose file was found and uv is unavailable for fallback startup\n' >&2
		exit 1
	fi
	mkdir -p "$(LOG_DIR)" "$(PID_DIR)"
	$(call maybe_source_env)
	start_service() {
		name="$$1"
		command="$$2"
		pid_file="$(PID_DIR)/$$name.pid"
		log_file="$(LOG_DIR)/$$name.log"
		if [ -f "$$pid_file" ] && kill -0 "$$(cat "$$pid_file")" >/dev/null 2>&1; then
			printf '%s is already running (pid=%s)\n' "$$name" "$$(cat "$$pid_file")"
			return 0
		fi
		if [ "$(DRY_RUN)" = "1" ]; then
			printf '[dry-run] nohup %s > %s 2>&1 & echo $$! > %s\n' "$$command" "$$log_file" "$$pid_file"
			return 0
		fi
		nohup sh -c "$$command" >"$$log_file" 2>&1 &
		echo $$! >"$$pid_file"
		printf 'Started %s (pid=%s)\n' "$$name" "$$(cat "$$pid_file")"
	}
	case "$(strip $(SERVICE))" in
		"" )
			printf 'Compose file not present; starting app services only. Ensure DATABASE_URL and AWS_ENDPOINT_URL point to reachable backing services.\n'
			start_service "$(INGESTION_SERVICE)" "$(UV) run --directory services/ingestion-service uvicorn ingestion_service.main:app --host 0.0.0.0 --port 8000"
			start_service "$(QUERY_SERVICE)" "$(UV) run --directory services/query-service uvicorn query_service.main:app --host 0.0.0.0 --port 8001"
			start_service "$(WORKER_SERVICE)" "$(UV) run --directory services/processing-worker python -m processing_worker.main"
			;;
		$(INGESTION_SERVICE) ) start_service "$(INGESTION_SERVICE)" "$(UV) run --directory services/ingestion-service uvicorn ingestion_service.main:app --host 0.0.0.0 --port 8000" ;;
		$(QUERY_SERVICE) ) start_service "$(QUERY_SERVICE)" "$(UV) run --directory services/query-service uvicorn query_service.main:app --host 0.0.0.0 --port 8001" ;;
		$(WORKER_SERVICE) ) start_service "$(WORKER_SERVICE)" "$(UV) run --directory services/processing-worker python -m processing_worker.main" ;;
		$(SIMULATOR_SERVICE) ) start_service "$(SIMULATOR_SERVICE)" "$(UV) run --directory services/simulator python -m simulator.main --mode continuous" ;;
		* )
			printf 'No Compose file was found and no fallback command is defined for service `%s`\n' "$(SERVICE)" >&2
			exit 1
			;;
	esac
endif

down: ## Stop the stack or one service
ifneq ($(strip $(COMPOSE_FILE_PATH)),)
	if [ -z "$(COMPOSE_CMD)" ]; then
		printf 'Compose file found at %s but Docker Compose is unavailable\n' "$(COMPOSE_FILE_PATH)" >&2
		exit 1
	fi
	if [ -n "$(strip $(SERVICE))" ]; then
		$(call run,$(COMPOSE_CMD) $(COMPOSE_ARGS) stop $(SERVICE))
	else
		if [ "$(KEEP_VOLUMES)" = "1" ]; then
			$(call run,$(COMPOSE_CMD) $(COMPOSE_ARGS) down --remove-orphans)
		else
			$(call run,$(COMPOSE_CMD) $(COMPOSE_ARGS) down --remove-orphans -v)
		fi
	fi
else
	stop_service() {
		name="$$1"
		pid_file="$(PID_DIR)/$$name.pid"
		if [ ! -f "$$pid_file" ]; then
			printf '%s is not tracked in %s\n' "$$name" "$(PID_DIR)"
			return 0
		fi
		pid="$$(cat "$$pid_file")"
		if [ "$(DRY_RUN)" = "1" ]; then
			printf '[dry-run] kill %s && rm -f %s\n' "$$pid" "$$pid_file"
			return 0
		fi
		if kill -0 "$$pid" >/dev/null 2>&1; then
			kill "$$pid"
		fi
		rm -f "$$pid_file"
		printf 'Stopped %s\n' "$$name"
	}
	case "$(strip $(SERVICE))" in
		"" )
			stop_service "$(INGESTION_SERVICE)"
			stop_service "$(QUERY_SERVICE)"
			stop_service "$(WORKER_SERVICE)"
			stop_service "$(SIMULATOR_SERVICE)"
			;;
		* ) stop_service "$(SERVICE)" ;;
	esac
endif

restart: ## Restart the stack or one service
	$(MAKE) --no-print-directory DRY_RUN=$(DRY_RUN) SERVICE="$(SERVICE)" down
	$(MAKE) --no-print-directory DRY_RUN=$(DRY_RUN) SERVICE="$(SERVICE)" up

ps: ## Show running services or tracked local processes
ifneq ($(strip $(COMPOSE_FILE_PATH)),)
	if [ -z "$(COMPOSE_CMD)" ]; then
		printf 'Compose file found at %s but Docker Compose is unavailable\n' "$(COMPOSE_FILE_PATH)" >&2
		exit 1
	fi
	$(call run,$(COMPOSE_CMD) $(COMPOSE_ARGS) ps $(strip $(SERVICE)))
else
	mkdir -p "$(PID_DIR)"
	printf '%-22s %-12s %s\n' SERVICE STATUS PID
	for pid_file in "$(PID_DIR)"/*.pid; do
		if [ ! -f "$$pid_file" ]; then
			continue
		fi
		service="$$(basename "$$pid_file" .pid)"
		pid="$$(cat "$$pid_file")"
		if kill -0 "$$pid" >/dev/null 2>&1; then
			status=running
		else
			status=stale
		fi
		if [ -n "$(strip $(SERVICE))" ] && [ "$$service" != "$(SERVICE)" ]; then
			continue
		fi
		printf '%-22s %-12s %s\n' "$$service" "$$status" "$$pid"
	done
endif

status: ## Show service status summary
	$(MAKE) --no-print-directory DRY_RUN=$(DRY_RUN) SERVICE="$(SERVICE)" ps

logs: ## Show logs; use FOLLOW=1 and TIMESTAMPS=1 when needed
ifneq ($(strip $(COMPOSE_FILE_PATH)),)
	if [ -z "$(COMPOSE_CMD)" ]; then
		printf 'Compose file found at %s but Docker Compose is unavailable\n' "$(COMPOSE_FILE_PATH)" >&2
		exit 1
	fi
	$(call run,$(COMPOSE_CMD) $(COMPOSE_ARGS) logs $(LOG_FOLLOW_ARG) $(LOG_TS_ARG) $(TARGET_LOG_SERVICES))
else
	if [ "$(TIMESTAMPS)" = "1" ]; then
		printf 'Fallback service logs already contain JSON timestamps emitted by the application logger.\n'
	fi
	show_file() {
		log_file="$$1"
		if [ ! -f "$$log_file" ]; then
			printf 'Missing log file: %s\n' "$$log_file" >&2
			return 1
		fi
		if [ "$(FOLLOW)" = "1" ]; then
			if [ "$(DRY_RUN)" = "1" ]; then
				printf '[dry-run] tail -n 100 -f %s\n' "$$log_file"
			else
				tail -n 100 -f "$$log_file"
			fi
		else
			if [ "$(DRY_RUN)" = "1" ]; then
				printf '[dry-run] tail -n 100 %s\n' "$$log_file"
			else
				tail -n 100 "$$log_file"
			fi
		fi
	}
	if [ -n "$(strip $(SERVICE))" ]; then
		show_file "$(LOG_DIR)/$(SERVICE).log"
	else
		for service in $(INGESTION_SERVICE) $(QUERY_SERVICE) $(WORKER_SERVICE) $(SIMULATOR_SERVICE); do
			log_file="$(LOG_DIR)/$$service.log"
			if [ -f "$$log_file" ]; then
				printf '\n==> %s <==\n' "$$log_file"
				show_file "$$log_file"
			fi
		done
	fi
endif

health: ## Run health checks against the active services
	if ! command -v curl >/dev/null 2>&1; then
		printf 'curl is required for health checks but was not found in PATH\n' >&2
		exit 1
	fi
ifneq ($(strip $(COMPOSE_FILE_PATH)),)
	if [ -z "$(COMPOSE_CMD)" ]; then
		printf 'Compose file found at %s but Docker Compose is unavailable\n' "$(COMPOSE_FILE_PATH)" >&2
		exit 1
	fi
	running_services="$$( $(COMPOSE_CMD) $(COMPOSE_ARGS) ps --services --filter status=running 2>/dev/null || true )"
	status=0
	check_running() {
		name="$$1"
		if [ "$(DRY_RUN)" = "1" ]; then
			printf '[dry-run] %s\n' "$(COMPOSE_CMD) $(COMPOSE_ARGS) ps $$name"
			return 0
		fi
		if printf '%s\n' "$$running_services" | grep -qx "$$name"; then
			printf '[ok] %s is running\n' "$$name"
		else
			printf '[fail] %s is not running\n' "$$name" >&2
			status=1
		fi
	}
	check_http() {
		name="$$1"
		url="$$2"
		if [ "$(DRY_RUN)" = "1" ]; then
			printf '[dry-run] curl -fsS %s\n' "$$url"
			return 0
		fi
		if curl -fsS "$$url" >/dev/null; then
			printf '[ok] %s responded at %s\n' "$$name" "$$url"
		else
			printf '[fail] %s did not respond at %s\n' "$$name" "$$url" >&2
			status=1
		fi
	}
	check_postgres() {
		if [ "$(DRY_RUN)" = "1" ]; then
			printf '[dry-run] %s\n' "$(COMPOSE_CMD) $(COMPOSE_ARGS) exec -T $(POSTGRES_SERVICE) pg_isready -U $(POSTGRES_USER) -d $(POSTGRES_DB)"
			return 0
		fi
		if $(COMPOSE_CMD) $(COMPOSE_ARGS) exec -T $(POSTGRES_SERVICE) pg_isready -U "$(POSTGRES_USER)" -d "$(POSTGRES_DB)" >/dev/null 2>&1; then
			printf '[ok] %s accepted connections\n' "$(POSTGRES_SERVICE)"
		else
			printf '[fail] %s did not accept connections\n' "$(POSTGRES_SERVICE)" >&2
			status=1
		fi
	}
	for service in $(TARGET_LOG_SERVICES); do
		case "$$service" in
			$(POSTGRES_SERVICE)) check_postgres ;;
			$(QUEUE_SERVICE)) check_http "$(QUEUE_SERVICE)" "$(LOCALSTACK_HEALTH_URL)" ;;
			$(INGESTION_SERVICE)) check_http "$(INGESTION_SERVICE)" "$(INGESTION_BASE_URL)/health" ;;
			$(QUERY_SERVICE)) check_http "$(QUERY_SERVICE)" "$(QUERY_BASE_URL)/health" ;;
			$(WORKER_SERVICE)) check_running "$(WORKER_SERVICE)" ;;
			$(SIMULATOR_SERVICE)) check_running "$(SIMULATOR_SERVICE)" ;;
			*) check_running "$$service" ;;
		esac
	done
	exit "$$status"
else
	mkdir -p "$(PID_DIR)"
	status=0
	check_pid() {
		name="$$1"
		pid_file="$(PID_DIR)/$$name.pid"
		if [ "$(DRY_RUN)" = "1" ]; then
			printf '[dry-run] test -f %s && kill -0 $$(cat %s)\n' "$$pid_file" "$$pid_file"
			return 0
		fi
		if [ ! -f "$$pid_file" ]; then
			printf '[fail] %s has no pid file\n' "$$name" >&2
			status=1
			return 0
		fi
		pid="$$(cat "$$pid_file")"
		if kill -0 "$$pid" >/dev/null 2>&1; then
			printf '[ok] %s is running with pid %s\n' "$$name" "$$pid"
		else
			printf '[fail] %s has a stale pid file (%s)\n' "$$name" "$$pid" >&2
			status=1
		fi
	}
	check_http() {
		name="$$1"
		url="$$2"
		if [ "$(DRY_RUN)" = "1" ]; then
			printf '[dry-run] curl -fsS %s\n' "$$url"
			return 0
		fi
		if curl -fsS "$$url" >/dev/null; then
			printf '[ok] %s responded at %s\n' "$$name" "$$url"
		else
			printf '[fail] %s did not respond at %s\n' "$$name" "$$url" >&2
			status=1
		fi
	}
	selected_services="$(if $(strip $(SERVICE)),$(SERVICE),$(INGESTION_SERVICE) $(QUERY_SERVICE) $(WORKER_SERVICE))"
	for service in $$selected_services; do
		case "$$service" in
			$(INGESTION_SERVICE)) check_pid "$(INGESTION_SERVICE)"; check_http "$(INGESTION_SERVICE)" "$(INGESTION_BASE_URL)/health" ;;
			$(QUERY_SERVICE)) check_pid "$(QUERY_SERVICE)"; check_http "$(QUERY_SERVICE)" "$(QUERY_BASE_URL)/health" ;;
			$(WORKER_SERVICE)) check_pid "$(WORKER_SERVICE)" ;;
			$(SIMULATOR_SERVICE)) check_pid "$(SIMULATOR_SERVICE)" ;;
			*) printf '[warn] no fallback health probe is defined for %s\n' "$$service" ;;
		esac
	done
	exit "$$status"
endif

all: ## Build service artifacts or Docker images
ifneq ($(strip $(COMPOSE_FILE_PATH)),)
	if [ -z "$(COMPOSE_CMD)" ]; then
		printf 'Compose file found at %s but Docker Compose is unavailable\n' "$(COMPOSE_FILE_PATH)" >&2
		exit 1
	fi
	case "$(strip $(SERVICE))" in
		"" ) build_targets="$(BUILD_SERVICES)" ;;
		$(POSTGRES_SERVICE)|$(QUEUE_SERVICE) )
			printf 'Service %s uses a published image and does not support local builds\n' "$(SERVICE)" >&2
			exit 1
			;;
		* ) build_targets="$(SERVICE)" ;;
	esac
	if [ "$(DRY_RUN)" = "1" ]; then
		printf '[dry-run] %s\n' "$(COMPOSE_CMD) $(COMPOSE_ARGS) build $$build_targets"
	else
		$(COMPOSE_CMD) $(COMPOSE_ARGS) build $$build_targets
	fi
else
	if ! command -v "$(UV)" >/dev/null 2>&1; then
		printf 'uv is required for the fallback build workflow\n' >&2
		exit 1
	fi
	$(call run,$(UV) run python -m compileall packages services tests)
	$(call run,$(UV) run python scripts/package-lambdas.py)
endif

clean: ## Remove generated artifacts, caches, and fallback runtime state
	$(call run,rm -rf "$(RUN_DIR)" .pytest_cache .mypy_cache .ruff_cache build htmlcov dist/lambdas)
	$(call run,find . -type d -name __pycache__ -prune -exec rm -rf {} +)
	$(call run,find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete)

lint: ## Run Ruff checks
	if ! command -v "$(UV)" >/dev/null 2>&1; then
		printf 'uv is required to run lint checks\n' >&2
		exit 1
	fi
	$(call run,$(UV) run ruff check .)

type-check: ## Run mypy
	if ! command -v "$(UV)" >/dev/null 2>&1; then
		printf 'uv is required to run type checks\n' >&2
		exit 1
	fi
	$(call run,$(UV) run mypy .)

test: ## Run the full pytest suite
	if ! command -v "$(UV)" >/dev/null 2>&1; then
		printf 'uv is required to run tests\n' >&2
		exit 1
	fi
	$(call run,$(UV) run pytest)

test-unit: ## Run unit tests only
	if ! command -v "$(UV)" >/dev/null 2>&1; then
		printf 'uv is required to run tests\n' >&2
		exit 1
	fi
	$(call run,$(UV) run pytest tests/unit)

test-integration: ## Run integration tests only
	if ! command -v "$(UV)" >/dev/null 2>&1; then
		printf 'uv is required to run tests\n' >&2
		exit 1
	fi
	$(call run,$(UV) run pytest tests/integration)

smoke: ## Run the repository smoke test script
	if [ ! -f scripts/run-smoke-tests.sh ]; then
		printf 'scripts/run-smoke-tests.sh is missing\n' >&2
		exit 1
	fi
	$(call maybe_source_env)
	$(call run,sh ./scripts/run-smoke-tests.sh)

demo: ## Send a deterministic telemetry batch through the stack
ifneq ($(strip $(COMPOSE_FILE_PATH)),)
	if [ -z "$(COMPOSE_CMD)" ]; then
		printf 'Compose file found at %s but Docker Compose is unavailable\n' "$(COMPOSE_FILE_PATH)" >&2
		exit 1
	fi
	if [ "$(DRY_RUN)" = "1" ]; then
		printf '[dry-run] COMPOSE_PROFILES=%s %s %s run --rm %s\n' "$${COMPOSE_PROFILES:-demo}" "$(COMPOSE_CMD)" "$(COMPOSE_ARGS)" "$(SIMULATOR_SERVICE)"
	else
		COMPOSE_PROFILES="$${COMPOSE_PROFILES:-demo}" $(COMPOSE_CMD) $(COMPOSE_ARGS) run --rm $(SIMULATOR_SERVICE)
	fi
else
	if ! command -v "$(UV)" >/dev/null 2>&1; then
		printf 'uv is required to run the simulator locally\n' >&2
		exit 1
	fi
	$(call maybe_source_env)
	$(call run,$(UV) run --directory services/simulator python -m simulator.main --mode batch)
endif
