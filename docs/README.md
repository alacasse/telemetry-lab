# Documentation

This directory is the documentation root for Telemetry Lab.

1. [Architecture and contracts](architecture.md): processing paths, transactions and evidence.
2. [Thermostat behavior](thermostat.md): mode, setpoint, stop proof and recovery.
3. [Local runbook](local-runbook.md): disposable execution and diagnostics.
4. [Validation](validation.md): current repository checks and scope of proof.
5. [Exclusive engine contract](phase2-proposal.md): shared-room authority and failure boundaries.
6. [AWS preparation](aws-preparation.md): infrastructure artifacts and cloud boundaries.
7. [AWS research](aws-readiness-research.md): options and historical findings, with phase 3 corrections.

The [standalone engine runbook](local-runbook.md#standalone-thermal-engine) describes
the phase 3 image, configuration and disposable container validation.

The [concurrent timeout investigation](timeout-investigation-2026-10-08.md)
separates historical evidence, controlled fault injection and new repeated runs.

The [engine availability correction](thermal-availability-2026-10-08.md) records
the subsequent local-admission fix, before/after regressions and concurrent-load
comparison separately from the historical investigation.

The demo is an educational simulation. Source code, local runtime evidence, automated tests, remote CI results and AWS execution are distinct forms of evidence; report each at its actual scope.
