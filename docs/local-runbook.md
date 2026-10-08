# Local runbook

## Start and stop

Prerequisites: a running Docker daemon, Python 3.12 and `uv`. Node.js is needed for JavaScript checks, not for serving the static interface.

```sh
sh scripts/demo-local.sh --port 8088
```

Choose an unused loopback port if 8088 is occupied. Leave the launcher running and open its printed URL. It creates its own database and queues; no cloud credentials are needed. Ctrl-C stops its processes and removes its disposable containers. The database and queue state are lost on exit.

The launcher writes process logs and `runtime.json` under `.run/demo/<port>/`. Inspect the API, worker and thermal logs for a failed step. `GET /environment` exposes local runtime metadata. Launch revision metadata and currently served static files may differ if sources are edited while the composition is running; restart before qualifying a changed build.

A browser reload preserves the stored room within the same running environment. It performs passive reads. A new tab can inspect server evidence but may lack exact submitted bodies and receipts from another tab, so retry availability can differ.

The launcher still starts `demo.app` and `demo.thermal_runtime`. The API assembles
`packages.thermal.http.create_router` with its database sessions, validated local
speed and runtime observation callback. The thermal process assembles
`packages.thermal.runtime` with the local HTTP measurement publisher and dedicated
command queue. Local environment parsing and passive PostgreSQL observation live in
`demo/thermal_local.py`; the shared modules do not inspect launcher state.

The thermal process acquires authority, recovers and optionally creates the default
room in one transaction before starting its workers. A restarted interrupted room waits for explicit resume.
The local-only guard, loopback ingestion address checks and queue separation still
apply. `THERMAL_TIME_MULTIPLIER` is validated at composition startup (0.5–2); restart
the composition to change it. Router construction and browser reload never seed or
recover a room.

The engine uses a 10-second lease, renewed every two seconds. After a crash the
launcher waits for lease expiry before spending a restart attempt (maximum three).
Database observation failure causes passive waiting; a busy engine exits 75 and
ends automatic retries. A running PID is diagnostic information, not evidence of
availability. API snapshots expose PostgreSQL owner, generation, expiry,
`renewed_at` and `clock_passed_at` beside the existing runtime fields.

### Migration to phase 2

Apply `0007_shared_room_authority` with thermal engines stopped. Older engines do
not honor this fence. Stop their supervisor as well, or otherwise prevent its
restarts, before running Alembic. Preserve the database if its state is needed;
Ctrl-C on the disposable launcher removes its database. All disposable validation
commands below migrate before spawning engines. A fresh launcher uses the new
schema automatically. Do not run an old engine against a migrated shared room.

Local engine and observation pools use one-second pool/SQL-lock waits and
two-second connection, statement and idle-transaction limits. Renewal has its own
pool. Five seconds without confirmed renewal permanently disables that process.
Normal shutdown waits four seconds total for workers; unfinished workers or lost
authority prohibit recovery/release. Restart recovers the room into `interrupted`;
only the explicit resume action restarts thermal movement.

## Technical inspection

The main page shows dated evidence for the latest processed measurement and command alongside the thermostat. Recovery controls appear only when needed; there is no Details panel or normal session-stop button.

The historical interface remains directly available at `/legacy.html` on the launcher's printed URL (for example, `http://127.0.0.1:8088/legacy.html`). Its scenario and replay controls can submit requests; it is not a read-only history viewer.

For passive technical inspection, `GET /thermal-simulations/current` returns the current session snapshot and its `simulation_id`. `GET /thermal-simulations/{simulation_id}/history?kind=readings&cursor=0` returns a page of dated measurements and their exact bodies. The other supported kinds are `decisions`, `commands` and `settings`; use the response's `next_cursor` for subsequent pages, until it is null. These API routes remain available even though their controls have been removed from the main page.

## Integration checks

Use separate free ports; each test mode creates and removes its own composition:

```sh
uv sync --frozen --python 3.12 --group dev
PYTHON=.venv/bin/python sh scripts/test-postgres.sh
PYTHON=.venv/bin/python sh scripts/test-thermal-command-migration.sh
sh scripts/demo-local.sh --port 8089 --test
sh scripts/demo-local.sh --port 8090 --test-thermostat --startup-order runtime-first
sh scripts/demo-local.sh --port 8093 --test-crash-windows
```

`--test` exercises the local message pipeline and scenario routes.
`--test-crash-windows` runs the two outbox crash/command-redelivery cases in a
separate composition, so the complete suite retains the three-restart budget. `--test-thermostat` exercises the persistent thermostat behavior on a fresh room. Report the actual results, skipped tests and runtime used in [validation](validation.md).

## Alternative service stack

The repository also contains Docker Compose and Make targets for the separate service stack. `make help` lists those targets. That stack has different ports, configuration and volume lifecycle from the interactive launcher; its volume retention setting does not apply to the disposable launcher.

## Troubleshooting

- If Docker is unavailable, start the daemon and rerun the launcher.
- If the selected HTTP port is occupied, select another port rather than terminating an unrelated process.
- If the UI reports missing evidence, inspect the dated snapshot and relevant logs. A queue count or a published command is insufficient proof of completion.
- If a setting response is uncertain, read the current setting and saved operation before taking an explicit recovery action.
