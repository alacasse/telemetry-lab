# Validation

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
