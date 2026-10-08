# Validation

## Engine availability correction — 8 October 2026

The engine now coordinates its own protected transactions before database
checkout, with renewal priority and admission held through commit/confirmation.
This fixes the demonstrated sibling-contention mechanism without changing pool
size, SQL limits, the 10/2/5-second authority contract or four-second shutdown.
It remains permanently inactive after genuine database errors or authority loss.

A new frozen-source comparison retains ordinary disk-backed SAM builds in both
campaigns: **7/8** before compositions had fatal reports versus **0/8** corrected
compositions, with **2/2 successful SAM builds in each**. The corrected engine
survived a **3.213-second connection hold with same-backend WALSync observations**.
The exact final PostgreSQL regression file fails all three cases before the fix
and passes afterward: finite business/renewal commit retention and SIGTERM while
other loops wait. This is engine correction evidence, separate from the earlier
tmpfs mitigation below, and remains a finite local qualification.

Ruff and mypy (122 sources) pass. The default suite passes **261 tests** with
**37 opt-in skips** and 21 warnings; the dedicated PostgreSQL suite passes **17**,
including external pool/SQL-lock failures, real connection loss, fencing,
passive multiuser observation and the new availability/stop regressions.
The focused tests pass **11** cases. General and import reviews found no actionable
issues. All **792** historical local evidence hashes were rechecked unchanged.
The separate crash-window composition passes both cases, and the rebuilt
standalone container passes its pipeline, exclusion, replacement, resume and
redelivery checks. SIGTERM/SIGINT complete in 1.141/1.133 seconds. Live PostgreSQL
durability remains enabled on disk; the checks clean up only their own resources.

See the [mechanism, deadlines and complete qualification](thermal-availability-2026-10-08.md)
and [new evidence record](validation-availability-2026-10-08.json). No commit,
push, AWS operation, upload or deployment was performed. Historical results below
retain their original scope.

## Concurrent timeout investigation — 8 October 2026

New concurrent runs reproduced the symptom and captured PostgreSQL WALSync stalls
retaining connections/authority locks beyond the existing one-second limits.
A natural four-way batch had fatal runtime reports in **2/8** compositions; one
functional suite still returned zero. Controlled PostgreSQL tests separately
exercise pool starvation and a delayed renewal commit, including permanent
inactivation and no late business effects.

A local SAM build helper isolates temporary installation writes in bounded tmpfs,
while leaving PostgreSQL and final artifacts on disk. Subsequent workload batches
recorded **0/12** fatal compositions, including a successful complete build while
four runtimes were active. This is finite local mitigation evidence, not a promise
of storage latency or a retrospective diagnosis of the original 18381/18382 logs.
The engine's pool size, timeouts, lease, renewal and expiry contracts are unchanged.

See the [investigation and qualification](timeout-investigation-2026-10-08.md) and
[separate evidence record](validation-timeouts-2026-10-08.json). Historical phase 3
results and their original JSON remain preserved below.

## Phase 3 standalone thermal engine — 8 October 2026

Implemented on the working tree based on
`38dfa09cbd0dda24b8eba694735e79b7b8ba9e2d`, without a new commit or push.
The engine image and explicit dependency configuration are now runnable outside
`demo`. Hosting, public HTTP routes and actual AWS execution remain unestablished.
Source hashes, image identity and local log hashes are recorded in
[the phase 3 evidence record](validation-phase3-2026-10-08.json). Raw logs and bundles
remain under `.run/validation/phase3/`, excluded from Git.
The [runbook](local-runbook.md#standalone-thermal-engine) documents the realized
configuration; [AWS preparation](aws-preparation.md) lists the remaining decisions.

| Check | Observed result |
| --- | --- |
| Ruff / mypy | Passed; 115 Python source files checked |
| Default Python suite | 240 passed, 32 opt-in skips, 21 existing warnings |
| JavaScript controller/DOM suites | 137 passed (27 + 52 + 23 + 35) |
| Disposable PostgreSQL | 12 passed, including authority and multiuser cases |
| Historical data / command migration | 1 passed |
| Existing thermostat, runtime-first, port 18384 | 1 passed |
| Existing historical scenarios, port 18385 | 15 passed; two crash-window cases excluded deliberately |
| Existing outbox crash windows, port 18383 | 2 passed; eight other thermal cases excluded deliberately |
| Four Lambda bundles | Rebuilt with frozen dependencies in SAM Python 3.12/x86_64; checksums and isolated handler/shared thermal imports passed in Lambda runtime image with networking disabled |
| Runtime image isolation | Linux/amd64; UID/GID 10001, direct Python entrypoint, no `demo` package; standalone import passed with network disabled and read-only filesystem |
| Standalone container composition | Passed: real processing/receipts, busy code 75 with unchanged business hash, crash/expiry/replacement, explicit resume/no catch-up, exact-body redelivery/new receipt |
| Container signals | SIGTERM exit 0 in 0.864 s; SIGINT exit 0 in 1.147 s (host Docker timing includes CLI overhead) |
| General and import reviews | No remaining actionable findings after follow-up |

New unit coverage rejects incomplete/unsafe dependency configuration before DB
work, including shared or unsupported FIFO queue URLs. It exercises standard SDK
credential construction, exact UTF-8 request bodies and real receipt propagation,
malformed-receipt retries, staging token resolution before authority, and busy
startup without transport construction. Subprocess tests block transport closure
or guarded cleanup, then send SIGTERM followed by SIGINT: the four-second watchdog
terminates with code 1 without extending the original deadline. Existing authority
and transport tests retain lost receipts, postcommit acknowledgement loss,
redelivery, real PostgreSQL fencing and permanent disablement.

The disposable container composition uses Docker DNS for PostgreSQL, ingestion and
LocalStack, with no host ports, source mounts or credentials. It checks processed
measurements, command receipts/application, a competing process's code 75 and
unchanged business state, graceful SIGTERM/SIGINT, SIGKILL/lease expiry/replacement,
stationary interruption, explicit resume without catch-up, and real adapter
redelivery preserving body/message identity with a new receipt handle. The engine
runs the production runtime image; fixture tooling lives in a separate image target.

Initial parallel launcher validations (ports 18381/18382), running alongside
container builds and other suites, encountered fatal exceptions logged only as
`TimeoutError` (their module was not captured). The historical scenario composition
exhausted its restart attempts; the thermostat composition recorded one restart.
The unchanged sources passed the
separate reruns above. The single-connection business pool and one-second checkout
limit are unchanged. Contention is a plausible explanation, not a proven diagnosis
of the exact holder; no load-resilience claim is made and no protection was relaxed.
The first sandboxed default pytest run stalled in HTTP TestClient; the suite passed
with local execution permissions. Early container test versions suspended the
owner to compare busy-start state; the stronger graceful-exit assertion revealed
that the suspension could itself trigger fail-closed behavior. The final fixture
keeps the owner renewing while an interrupted room is stable, stops the fixture
worker during the comparison, and checks all persisted thermal business columns.
An intermediate attempt to drain every reading was also rejected: unpublished
readings correctly remain pending during interruption. The final test preserves
that contract rather than resuming or changing business expiry for convenience.

Review identified acceptance of FIFO URLs although this adapter implements only
standard SQS publication; rejection now occurs before DB sessions/acquisition,
with four regression cases. Malformed HTTP receipt JSON is treated as a transport
retry, preserving the pending body. No business expiry, lease, renewal interval or
fail-closed deadline was changed.

Reproduction commands:

```sh
.venv/bin/ruff check .
.venv/bin/mypy .
.venv/bin/pytest -ra
node tests/ui/demo.test.cjs
node tests/ui/thermal.test.cjs
node tests/ui/thermostat.test.cjs
node tests/ui/thermostat-flow.test.cjs
PYTHON=.venv/bin/python sh scripts/test-postgres.sh
PYTHON=.venv/bin/python sh scripts/test-thermal-command-migration.sh
.venv/bin/python scripts/demo-local.py --test-thermostat --startup-order runtime-first --port 18384
.venv/bin/python scripts/demo-local.py --test --port 18385
.venv/bin/python scripts/demo-local.py --test-crash-windows --port 18383
PYTHON=.venv/bin/python sh scripts/test-thermal-engine.sh
```

The passing reruns above were sequential; this does not resolve the concurrent
failure. CI now includes the standalone container check; no remote CI run is
claimed. No existing unrelated demo was migrated or restarted. No AWS resources,
uploads, deployments, account queries or destruction occurred. AWS roles/secrets,
networking, scheduler replacement, browser access and cloud receipts still need
an explicitly authorized cloud trial. Browser verification is automated; native
visual inspection and load testing were not repeated.

## Phase 2 exclusive shared-room engine — 8 October 2026

Implemented on the working tree based on `8b597ef12fea86b776ed67704e7835e2fef9fe6c`.
The [realized contract](phase2-proposal.md) replaces the proposal. Source and log
hashes are recorded in [the phase 2 evidence record](validation-phase2-2026-10-08.json).
All reported execution is local; artifacts describe a dirty working tree, not a
published release.

| Check | Observed result |
| --- | --- |
| Ruff / mypy | Passed; 106 Python source files checked |
| Default Python suite | 199 passed, 31 opt-in skips, 21 existing deprecation warnings |
| JavaScript controller and DOM suites | 137 tests passed (27 + 52 + 23 + 35) |
| Disposable PostgreSQL | 12 passed: pipeline, ten authority cases and multiuser/passive observation |
| Historical-data / command migration | 1 passed |
| Thermostat, runtime-first, port 18282 | 1 passed |
| Thermostat, API-first, port 18287 | 1 passed on the final authority code |
| Historical scenarios, port 18285 | 15 passed; two crash-window cases deliberately run separately |
| Outbox crash windows, port 18286 | 2 passed; eight other thermal cases deliberately excluded here |
| Four Lambda bundles | Final sources rebuilt in SAM Python 3.12/x86_64; all four checksums and isolated imports passed in the Lambda runtime image with networking disabled |
| Independent general and import reviews | No remaining actionable findings; general review repeated after cache hardening |

All 31 service-backed tests skipped by default were exercised in the separate
disposable runs. The historical and crash suites passed before the final
identity-map hardening; the final PostgreSQL regression suite and API-first
thermostat run additionally exercise that change.

The PostgreSQL tests use independent processes for acquisition, lock ordering and
actual `SIGSTOP`/`SIGCONT` replacement. They check busy startup without recovery,
lease expiry, permanent disablement on connection loss, atomic startup rollback,
old-generation rejection and late-cleanup refusal. A retained-session regression
exercises SQLAlchemy identity-map staleness during release/takeover. The multiuser
HTTP test checks competing settings and passive observation while authority is
locked, including routes that already hold a simulation lock. Unit tests add
postcommit renewal suspension, failed receipts, redelivery and lost acknowledgments,
aggregate shutdown timing and the launcher's unknown/busy/restart-budget decisions.

Migrations were applied before engines started in every disposable composition.
The existing unrelated running compositions were not migrated or stopped. The
source change therefore does not establish that a previously running demo has
loaded the new code. See the migration instructions in [the runbook](local-runbook.md).
No AWS changes, uploads, deployments, multi-room support or browser-storage fix
were performed. Browser results are automated controller/DOM checks; native visual
inspection and load testing were not repeated.

Initial checks exposed stale migration-head assertions, a duplicate test module
basename, and one test fixture retaining another test's lease. These were corrected.
A duplicate PostgreSQL run hit the temporary bootstrap server before the published
TCP endpoint was ready; the test script now waits for that endpoint. The original
combined historical run exceeded the three-restart budget after adding real lease
ownership to crash relays. The two relay cases now run in their own composition,
with both commands present in CI. One early relay run exhausted the unchanged
30-second business expiry; the intentionally unacknowledged test delivery now uses
a one-second visibility timeout and overlaps child startup with redelivery instead
of sleeping 3.2 seconds. Production transport defaults and business expiry are
unchanged. Review found and corrected a renewal-confirmation suspension race and
a retained ORM-authority row which could otherwise admit stale ownership.

Reproduction commands:

```sh
.venv/bin/ruff check .
.venv/bin/mypy .
.venv/bin/pytest -ra
node tests/ui/demo.test.cjs
node tests/ui/thermal.test.cjs
node tests/ui/thermostat.test.cjs
node tests/ui/thermostat-flow.test.cjs
PYTHON=.venv/bin/python sh scripts/test-postgres.sh
PYTHON=.venv/bin/python sh scripts/test-thermal-command-migration.sh
.venv/bin/python scripts/demo-local.py --test-thermostat --startup-order runtime-first --port 18282
.venv/bin/python scripts/demo-local.py --test --port 18285
.venv/bin/python scripts/demo-local.py --test-crash-windows --port 18286
.venv/bin/python scripts/demo-local.py --test-thermostat --startup-order api-first --port 18287
```

Raw logs and bundles are local under `.run/validation/phase2/`. Lambda packaging
uses the locked SAM Python 3.12/x86_64 build; verification checks all four checksums
and isolated handlers/shared thermal imports inside the Lambda runtime image with
networking disabled. CI configuration now includes the separate crash composition;
no remote CI execution is claimed here.

## Phase 1 shared thermostat extraction — 8 October 2026

The phase 1 working tree is based on `d9c50383e660398ec0785f637eedcbb9e536668a`.
It extracts the HTTP router and thermal runtime into `packages/thermal`, while
retaining local composition in `demo`. The earlier qualification below describes
its own revision; it is not substituted for these checks.

| Check | Observed result |
| --- | --- |
| Ruff / mypy | Passed; 100 Python source files checked |
| Default Python suite | 180 passed, 20 opt-in skips, 21 existing deprecation warnings |
| HTTP contracts and local helpers | 24 passed, included above |
| Thermal runtime, receipt failures and redelivery | 36 passed, included above |
| JavaScript controller and DOM suites | 137 passed (27 + 52 + 23 + 35) |
| Disposable PostgreSQL integration | 1 passed |
| Historical-data and command migration | 1 passed |
| Thermostat API-first, port 18181 | 1 passed |
| Thermostat runtime-first, port 18182 | 1 passed |
| Historical scenarios, port 18183 | 17 passed, including real receipt-loss, crash, redelivery and resume scenarios |
| Four Lambda bundles | Rebuilt in SAM Python 3.12 / x86_64; checksums, handlers and both shared thermal modules imported successfully in the Lambda runtime image with networking disabled |
| Independent general and import-topology reviews | No actionable findings |

The HTTP tests exercise passive router construction and reloads, missing resources,
validation errors, database failure, settings revisions/idempotency, history, and
runtime provenance. Runtime tests additionally inject receipt-transaction commit
failures after measurement or command acceptance and acknowledgement failure after
command application, proving replay retains identities without repeating effects.
All 20 service-backed tests skipped in the default suite were exercised by the
separate disposable integration runs above; the thermostat ran twice to cover
both startup orders.

Initial checks caught an import-order error and two incorrect new test expectations
(SQLite timestamp normalization and the existing `stopping` response). These were
corrected before the final passing run. Sandbox restrictions initially blocked
Docker and hung HTTP TestClient calls; those checks succeeded with local execution
permissions. An unsupported Node isolation flag was rejected; the four scripts
were then executed directly to obtain their individual test counts.

Reproduction commands (from the repository root):

```sh
.venv/bin/ruff check .
.venv/bin/mypy .
.venv/bin/pytest -ra
node tests/ui/demo.test.cjs
node tests/ui/thermal.test.cjs
node tests/ui/thermostat.test.cjs
node tests/ui/thermostat-flow.test.cjs
PYTHON=.venv/bin/python sh scripts/test-postgres.sh
PYTHON=.venv/bin/python sh scripts/test-thermal-command-migration.sh
.venv/bin/python scripts/demo-local.py --test-thermostat --startup-order api-first --port 18181
.venv/bin/python scripts/demo-local.py --test-thermostat --startup-order runtime-first --port 18182
.venv/bin/python scripts/demo-local.py --test --port 18183
```

The [phase 1 evidence record](validation-phase1-2026-10-08.json) preserves source
and log hashes. Raw logs and rebuilt archives are local under `.run/validation/phase1/`. The bundle
manifest explicitly records `source_dirty: true`: these are working-tree artifacts,
not published release artifacts. The build used the frozen lockfile in the SAM
image; verification used `scripts/verify-lambda-bundles.py` in the Lambda image
with `--network none` and Python `-I -S` isolation. No cloud execution or deployment
was performed. Browser checks here mean automated controller/DOM behavior; native
browser visual inspection was not repeated because static assets were unchanged.

## Local qualification — 8 October 2026

The [machine-readable record](validation-2026-10-08.json) identifies the checked sources, local evidence hashes and limits. Quality checks and an independent clean clone were qualified at `a6164a23ab37e502cf1679e9f8a58aea7d4cda10`. Earlier disposable-service checks used the recorded working revision with the same server, migration and test behavior. Documentation and validation records are separate from the runtime revision.

| Check | Observed result |
| --- | --- |
| Locked Python 3.12 installation, Ruff, mypy | Passed; 96 Python source files checked by mypy |
| Default Python suite | 152 passed, 20 opt-in skips, 21 existing deprecation warnings |
| JavaScript controller and DOM suites | 125 passed |
| PostgreSQL transaction/migration integration | 1 passed |
| Migration with historical data and queued commands | 1 passed |
| Thermostat, runtime-first startup | 1 passed |
| Historical HTTP/queue/thermal scenarios | 17 passed |
| Independent clean clone, launcher shell entrypoint, thermostat | 1 passed |
| Four Lambda bundles | Checksums and isolated imports passed in Python 3.12 runtime, network disabled |
| Terraform bootstrap and staging | Formatting, locked initialization without backend, and validation passed |
| Gitleaks | No leaks found in the two implementation commits |
| Independent general and import-topology reviews | No actionable findings |

The 20 skipped service-backed tests were exercised by the separate integration commands above. A skipped test alone is not evidence of success. No AWS deployment, cloud acceptance test, artifact upload or resource migration was performed.

## Browser observations

The French interface was inspected on desktop and at 390 px (375 px document width, no horizontal overflow). Keyboard input set the target; Space selected COOL; Details opened with Enter. In the same room, heating was stopped before cooling began. The measured 19.9 °C endpoint remained visible rather than being clamped to the 20 °C target.

Terminating the isolated thermal process caused the room to become interrupted. Existing dated evidence remained visible, controls were blocked, and the room resumed only after the explicit recovery action. That composition subsequently shut down cleanly for an unestablished reason; it is not claimed to remain available. A separate composition at the clean revision above verified a passive reload: target 22.5 °C and processed temperature 22.6 °C survived, with one control POST before and after the reload.

Initial attempts exposed two line-length errors from longer names and incomplete browser-fixture key replacements; both were corrected before the passing final checks. A browser opened before startup was ready and initially received connection refused. These attempts are not counted as successful validation.

Raw logs and screenshots remain local under `.run/validation/`, excluded from Git. The JSON record stores their hashes and source hashes. Validation does not establish production readiness, prolonged-load behavior, multiple simultaneous thermal runtimes, physical equipment control or screen-reader compatibility.

## Publication scan

Scanning the evidence commit produced four generic API-key alerts on source-file SHA-256 strings. Each value was recomputed from its referenced source file and independently reviewed. `.gitleaksignore` contains only the four exact historical commit/path/rule/line fingerprints; no file or detection rule is broadly excluded. The qualified three-commit history scan found no remaining leaks. The original local record above is retained unchanged.

## GitHub Actions

The workflow defines quality, disposable integration, Lambda bundle and Terraform jobs. The first completed remote run passed quality, bundles, Terraform and the thermostat scenario, but the following historical scenario failed before startup because it reused the same local port. The workflow now assigns different ports to the two compositions. Consult [Actions](https://github.com/alacasse/telemetry-lab/actions) for the result associated with each published commit; workflow configuration alone is not execution evidence. This is separate from the successful local qualification recorded above.
