# Validation

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

The workflow defines quality, disposable integration, Lambda bundle and Terraform jobs. Remote execution was pending when this local record was written. Consult [Actions](https://github.com/alacasse/telemetry-lab/actions) for the result associated with a published commit; workflow configuration alone is not execution evidence.
