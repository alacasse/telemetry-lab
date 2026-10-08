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
Normal shutdown has four seconds total for workers, guarded cleanup and transport
closure. A process watchdog exits 1 at that deadline; unfinished workers or lost
authority prohibit recovery/release. Restart recovers the room into `interrupted`;
only the explicit resume action restarts thermal movement.

Protected engine transactions now share local admission, including renewal with
priority over queued business work. Waiting happens before session checkout;
the existing five-second renewal deadline still applies. Database pool and SQL
timeouts are unchanged. External contention and real database errors still disable
the process. Normal stop cancels queued admission and retains the same four-second
watchdog. See [availability evidence](thermal-availability-2026-10-08.md).

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

## Standalone thermal engine

The autonomous service runs `python -m thermal_engine.main`. It composes the shared
process without importing `demo` and does not run migrations. Its default image
target contains only runtime dependencies, shared packages and the engine; it runs
as UID/GID 10001 with Python as PID 1. Python and uv images are pinned by digest;
`uv sync --frozen --no-dev` installs versions and distribution hashes from `uv.lock`.
This fixes build inputs, not a promise of byte-identical image manifests.

```sh
docker build --target runtime -f services/thermal-engine/Dockerfile -t telemetry-thermal:phase3 .
PYTHON=.venv/bin/python sh scripts/test-thermal-engine.sh
```

The second command builds a separate validation target and creates an isolated,
uniquely named Compose project. It uses PostgreSQL and LocalStack, runs migrations
and creates fixture queues before starting ingestion, worker and the real runtime
image. All application connections use Docker DNS; no host port, host credential
directory or source volume is mounted. The script removes only its own containers,
network and volumes on exit. It does not affect the existing browser demo.
The validation target contains fixture controls and is not the deployable image.

Standalone configuration is read from the environment, never `.env`:

| Variable | Contract |
| --- | --- |
| `THERMAL_TRANSPORT_MODE` | Required: `local-emulator` or `aws` |
| `APP_ENV` | Required: `local` for the emulator, `staging` for AWS |
| `AWS_REGION`, `RELEASE_REVISION` | Required explicit region and source/build identity |
| `QUEUE_BACKEND` | Required `sqs` |
| `THERMAL_INGESTION_URL` | Complete HTTP route in emulator mode; HTTPS in AWS mode; no embedded credentials, query or fragment |
| `THERMAL_QUEUE_URL` | Dedicated command queue URL; engine sends, receives and deletes messages |
| `QUEUE_URL` | Measurement queue URL used to reject accidental command/measurement queue sharing |
| `DATABASE_URL` | Explicit PostgreSQL connection in emulator mode; forbidden in staging |
| `DATABASE_SECRET_ARN` | Required in AWS mode; existing JSON DB secret format including host, username, password and database name |
| `STAGING_AUTH_SECRET_ARN` | Required in AWS mode; existing secret token sent as `X-Staging-Token` to ingestion |
| `AWS_ENDPOINT_URL` | Required HTTP emulator endpoint; endpoint overrides forbidden in AWS mode |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Emulator requires `test`/`test`, with no session token; no static credentials are included in the image |
| `THERMAL_TIME_MULTIPLIER` | Default 1, finite range 0.5–2 |
| `THERMAL_INITIALIZE_THERMOSTAT` | Default `1`; `0` disables creation of an absent room, but never disables recovery |

Both queues must be standard queues; FIFO is rejected before acquisition. AWS
URLs must use the configured region's standard SQS hostname and an account/queue
path. Distinct hostname aliases cannot make the same queue count as dedicated.
The engine never creates queues, provisions services or changes the database
schema. The AWS SDK resolves temporary role credentials normally; do not bake
credentials into images. Secret resolution occurs before acquisition, outside
authority locks. The ingestion token is loaded at startup; rotating it requires a
coordinated restart of the engine.

For a source invocation from the repository root, set the variables above and use
`PYTHONPATH=.:services/thermal-engine .venv/bin/python -m thermal_engine.main`.
`compose.thermal-engine.yml` is a complete synthetic local example; it is not an
AWS deployment manifest. A future supervisor must respect code 75, lease expiry
and explicit resume. Availability still comes from passive PostgreSQL observation,
not the existence of a running container; health policy for a chosen host remains
to be decided.

The disposable validation covers processed measurements and command receipts,
busy refusal, SIGTERM/SIGINT, crash/expiry/replacement, stationary interruption,
explicit resume without catch-up, and exact-body SQS redelivery with a new receipt.
Existing launcher suites additionally cover ambiguous outbox sends and lost
acknowledgments. Results and limitations are recorded in [validation](validation.md).

## Alternative service stack

The repository also contains Docker Compose and Make targets for the separate service stack. `make help` lists those targets. That stack has different ports, configuration and volume lifecycle from the interactive launcher; its volume retention setting does not apply to the disposable launcher.

## Troubleshooting

### Concurrent thermal timeout investigation

Use fresh ports and preserve each run's evidence:

```sh
.venv/bin/python scripts/reproduce-thermal-timeouts.py --repetitions 3 --parallel 2 --first-port 19000
```

The harness runs thermostat and historical references sequentially, then repeats
both with bounded concurrency. `--skip-baseline` supports subsequent increases in
concurrency. Existing `.run/demo/<port>` directories and occupied ports are refused.
Each run records source hashes, launcher and child exit codes, copied logs, and
unexpected fatal counts under a unique `.run/validation/timeout-investigation/`
directory. Keep sources unchanged during a measurement batch. A missing thermal
log is a failed measurement. Restart counts include deliberate test crashes and
must not be interpreted as unexpected failures.

The harness enables `--thermal-diagnostics`; individual launchers accept that flag
too. Standalone processes accept `THERMAL_DIAGNOSTICS=1`. Fatal reports always
include exception module/type, sanitized stack frame locations, PID, generation,
loop and monotonic time. Opt-in diagnostics add pool identifiers, backend PIDs,
checkout and hold durations, and slow action/scheduling timing (threshold 0.2 s).
SQLSTATE is retained when available; exception messages, SQL, payloads, locals and
connection URLs are omitted. Pool acquisition timing includes connection creation
when required; it is not exclusively queue waiting. Pool IDs are process-local.

Timing output uses a bounded, nonblocking queue and can be dropped under sink
pressure or at exit. Absence of timing records is not proof of absence of waiting.
Fatal handling arms the existing shutdown watchdog before writing diagnostics.
No pool size, SQL timeout, lease or business deadline is changed by this switch.

For a concurrent local SAM build, isolate temporary dependency-installation I/O:

```sh
sh scripts/build-lambda-bundles-local.sh "$PWD/.run/lambda-build-new"
```

The output directory must not exist. This helper uses the already-local SAM
Python 3.12 image (`--pull never`), frozen dependencies, read-only sources and a
2 GiB executable tmpfs for temporary pip/uv files. Final bundles remain on disk;
PostgreSQL storage, fsync and runtime deadlines are unchanged. Sufficient memory
is required; a failed build is not a successful load-validation run. This local
helper neither uploads nor deploys anything. Shared disk saturation can still
trigger the documented fail-closed behavior, even with this mitigation.

For an injected diagnostic/fail-closed control, the PostgreSQL suite includes a
real-process test retaining the sole business connection until checkout fails:

```sh
TELEMETRY_LAB_CONTENTION_EVIDENCE="$PWD/.run/injected-contention.json" \
  PYTHON=.venv/bin/python sh scripts/test-postgres.sh
```

Use a new evidence filename. This injection verifies permanent inactivation and
absence of late business cleanup; it does **not** establish the cause of an earlier
unobserved failure. See [the investigation](timeout-investigation-2026-10-08.md).

The PostgreSQL suite now also runs finite 1.3-second retained-commit regressions
for both business and renewal transactions. These require continued progress by
the same owner/generation and clean shutdown. The fatal lock control now uses an
independent PostgreSQL holder, which cannot share engine admission; the earlier
renewal-commit control and its evidence remain in the historical record.
To retain the finite-delay evidence, set `TELEMETRY_LAB_AVAILABILITY_EVIDENCE` to a
fresh JSON path when running `scripts/test-postgres.sh`. The test writes separate
`-business.json` and `-renewal.json` files. Injection holds a real transaction before
DBAPI commit; it does not emulate the storage device or claim to reproduce WALSync.

- If Docker is unavailable, start the daemon and rerun the launcher.
- If the selected HTTP port is occupied, select another port rather than terminating an unrelated process.
- If the UI reports missing evidence, inspect the dated snapshot and relevant logs. A queue count or a published command is insufficient proof of completion.
- If a setting response is uncertain, read the current setting and saved operation before taking an explicit recovery action.
