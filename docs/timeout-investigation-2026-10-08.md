# Concurrent thermal timeout investigation — 8 October 2026

The historical failure is **not established as resolved**. This investigation adds
safe diagnostics and repeatable measurements. New failures with the same symptom were reproduced under concurrent local load.
Their immediate cause is observed PostgreSQL WALSync stalls retaining connections
and authority locks beyond the existing one-second limits. The older phase 3
failures still lack equivalent traces; their cause cannot be retroactively proven. No engine timeout, pool capacity, lease, expiry,
authority admission or retry policy has been relaxed.

## Historical facts

The base remains `38dfa09cbd0dda24b8eba694735e79b7b8ba9e2d` with the existing
uncommitted phase 3 changes preserved. Original evidence remains in
`.run/validation/phase3/` and `.run/demo/18381/`, `.run/demo/18382/`.

Each failing thermal log contains five `TimeoutError` lines and one `AuthorityLost`
line, without exception module, timestamp, traceback or action. Several loops can
report the same initial failure; ten lines do not establish ten independent faults.
Port 18381 recorded one replacement; port 18382 exhausted three. The historical
suite's six failed assertions include cascading scenario failures. Its later
passing rerun and the thermostat's later passing rerun do not diagnose the failure.

The original phase 3 JSON is retained unchanged, including its over-specific
SQLAlchemy attribution. The prose in `validation.md` is corrected to match the raw
historical evidence. Current injected evidence cannot retroactively supply that
missing exception module.

## Measurements and scope

New logs live under `.run/validation/timeout-investigation/`. Reproduction uses
fresh disposable compositions and independent logs/exit codes. The harness rejects
reused evidence directories and occupied ports, records source hashes, and fails on
fatal logs or missing thermal evidence. Deliberate scenario SIGKILLs remain separate
from Python fatal reports. See `local-runbook.md` for invocations.

An initial uninstrumented thermostat reference on 18401 passed in 66.75 s.
The first instrumented batch (18410–18415) passed both references and all four
concurrent repetitions: zero unexpected fatal reports in six compositions.
Its historical reference overlapped one PostgreSQL test run, so that reference is
not an isolated host-load measurement. Instrumentation and harness improvements
were made during this preliminary batch; its single starting manifest must not be
read as an immutable source snapshot for every child. A subsequent batch freezes
the runtime sources and records hashes per launch.

The two-way concurrent phase overlapped the default Python suite, the PostgreSQL
suite, and the standalone Docker build/validation. The crash-window composition
18419 also overlapped its tail. This is a recorded local workload, not a recreation
of the missing original scheduler. Queue I/O occurs outside authority transactions;
a long receive action is not evidence of a retained database connection.

A bounded passive PostgreSQL sampler for the subsequent batch reads only PID,
state, wait category/event, blocker PIDs and transaction age. It does not collect
SQL text or business content. It adds one observer connection per composition and
uses autocommit plus a one-second statement timeout. Polling and instrumentation
add overhead; timing records are sampled at a 0.2 s threshold and may be dropped.

## Natural failures captured with PostgreSQL observation

In the four-composition batch on 18420–18427, a concurrent SAM build overlapped
failures in 18424 and 18425. The second case's scenario suite eventually passed,
but the harness correctly classifies its fatal logs as a failed measurement.

- **18424:** reading-publication backend 95 retained the sole business connection
  for **1.128221 s**. Six samples show `IO/WALSync`; the last transaction age is
  **1.052457 s**. Clock and command-publication checkouts fail at about **1.000 s**
  with `sqlalchemy.exc.TimeoutError`. Renewal backend 96 waits on `transactionid`,
  blocked by backend 95. Later `AuthorityLost` reports are fail-closed propagation.
- **18425:** renewal backend 94 retains its connection for **1.154032 s**, observed
  in `IO/WALSync`. Business backend 93 waits on `transactionid`, explicitly blocked
  by 94. Command publication fails with `sqlalchemy.exc.OperationalError`, SQLSTATE
  **55P03**, after **1.001538 s**; clock subsequently times out waiting for the pool.

The matching observations are in `postgres-waits.jsonl`, monotonic interval
**43770.69–43771.85**, and the respective thermal logs. PostgreSQL 16 describes
[WALSync](https://www.postgresql.org/docs/16/monitoring-stats.html) as waiting for
WAL to reach durable storage. The lock/blocker and connection-ownership evidence
therefore demonstrate storage synchronization in these failure chains, not a
network call inside an authority transaction or an inferred pool-size defect.

SAM's exact contribution to the host I/O stall remains an inference. Simultaneous
WALSync stalls in independent databases strengthen the shared-storage-pressure
hypothesis, but no host-device attribution was captured for that instant.
A read-only host check found Docker's overlayfs driver under `/var/lib/docker`;
the project path is on a Btrfs `/home` mount. Neither alone attributes the stall to
a device or a particular write.

## Controlled causal tests

`tests/integration/test_thermal_contention.py` runs the real five-loop process in a
spawned child, with separate production business and renewal session factories.
A fixture holds the business connection after `SELECT 1`, without locking the
authority row. It releases that connection only after an actual timeout is logged.

The recorded run measured about **1.000 s** of checkout waiting and **1.003 s** of
connection ownership. Fatal records identify `sqlalchemy.exc.TimeoutError`, a
QueuePool acquisition stack, the occupied business pool/backend PID, and a free
renewal pool. Renewal had committed successfully. The process returns 1, remains
permanently inactive, refuses subsequent guarded operations, retains its authority
row rather than performing late release, and leaves all business data unchanged.
No publication, receive or acknowledgment transport effect occurred.

This proves the causal mechanism **for the injected fault only**: insufficient
business connection availability for the unchanged one-second admission bound
causes fail-closed shutdown. Recovering the connection does not reactivate the
process. It neither proves a network call retained a connection nor explains the
original holder or resource pressure.

## Changes and review

Fatal diagnostics now include loop/action, PID, generation, monotonic time,
exception module/type, validated SQLSTATE and sanitized stack frame locations.
Optional pool timing includes checkout duration, hold duration, pool state and
backend PID. Action timing separately records elapsed duration and wakeup lag.
No exception messages, SQL, local variables, payloads or database URLs are emitted.

Independent review caught synchronous diagnostic I/O retaining a pool slot and an
exit-evidence write preceding container cleanup. Timing output now uses a bounded
nonblocking queue with a daemon sink; records are dropped when full, with no flush
or join wait. A blocked-output regression proves checkout/checkin remain usable. A later real
unread-pipe test also exposed interpreter finalization waiting on the daemon
logger's buffered stderr lock. The sink now uses raw descriptor writes, and the
independent reproduction exits normally. Fatal output follows shutdown-watchdog
admission; no descriptor flags or engine timeouts are changed. Exit evidence is written after
container cleanup. Follow-up general and import reviews found no remaining issues.

A second control delays a real renewal transaction immediately before its
DBAPI commit, while retaining the authority row lock. A coordinated business guard
then fails with SQLSTATE 55P03. This reproduces the locked-commit boundary without
pretending to inject actual storage latency.

The candidate validation correction is `scripts/build-lambda-bundles-local.sh`:
its frozen SAM build keeps temporary pip/uv installation, caches and environment
in a bounded 2 GiB tmpfs. Sources remain read-only; final bundles and PostgreSQL
remain on disk. It neither disables fsync nor moves database state to memory.
The comparison keeps concurrent functional tests running. Build-memory exhaustion
remains a build failure; this helper is not a general guarantee of storage latency.

No speculative engine correction was made. The validation build helper reduces temporary writes on shared disk;
its finite reruns qualify that local mitigation, not a guarantee against future
WALSync stalls. The original historical exception module/holder remains unknown.

## Qualification and remaining limits

| Check | Result |
| --- | --- |
| Ruff / mypy | Passed; 120 Python source files |
| Final default Python suite | 253 passed, 34 opt-in skips, 21 existing warnings |
| Disposable PostgreSQL | 14 passed, including both injected failure chains |
| Final thermostat, 18450 | 1 passed in 65.52 s, no fatal diagnostics |
| Crash windows, 18419 | 2 passed, 8 deliberately deselected |
| Standalone runtime image | `sha256:df266e0c8b97379c64f398d889f393050aa8ef9c2b6cfeb96bc7a94cba59a5d7` |
| Standalone validation | Passed processing/receipts, busy75 unchanged business, SIGTERM/SIGINT, crash/expiry/replacement, explicit resume/no catch-up, redelivery |
| Signal timing | SIGTERM 0.950 s; SIGINT 0.487 s, including host Docker timing |
| Independent general/import reviews | Clean after the diagnostic-output and cleanup-order corrections |

The natural four-way batch had **2/8 compositions with fatal reports**, including
one whose functional suite returned zero. Its largest measured business and
renewal connection holds were **1.128 s** and **1.154 s**. No network-in-transaction
change was found or needed. Increasing the business pool would not remove the
observed renewal/authority-row lock chain.

The first mitigation batch passed **8/8 compositions without fatal reports**.
A first helper attempt failed native-extension loading because the tmpfs was not
executable; it is retained as a failed build. The helper now explicitly allows
execution of build dependencies on its temporary mount, and the full four-bundle
retry passed. That successful retry overlapped the remaining three compositions,
so it is not described as four live runtimes throughout the entire build.
Across this batch, largest recorded checkout wait was **0.611 s**, largest hold
**0.641 s**, and sampled WALSync transaction age **0.452 s**. During the successful
build interval itself, the largest sampled WALSync transaction age was **0.190 s**.
These transaction ages are samples, not exact continuous wait-duration estimates.

Runtime source hashes match across the eight natural-batch and eight mitigation-
batch launches. The harness records launch-specific hashes. Later hardening also
counts any fatal exception type, including a lone OperationalError, rather than
only TimeoutError/AuthorityLost. No engine behavior changed between these batches.

The expected response to actual database or pool deadlines remains permanent
inactivation, followed by supervised lease-expiry replacement and explicit
business resume. Resource isolation mitigates one local build workload; it does
not promise that every storage commit will finish within one second. CPU, memory,
and unrelated I/O can still affect execution. The exact hardware/filesystem cause
of the WALSync stalls and SAM's individual contribution remain unproven. This is
local PostgreSQL/LocalStack evidence, not AWS or production capacity validation.

No commit, push, AWS operation, upload or deployment was performed. Original
phase 3 log hashes were rechecked unchanged. All new disposable compositions own
their containers; existing demonstrations and unrelated containers were preserved.

A further qualification on **18440–18443 passed all four compositions**, with
zero fatal reports of any type. Its successful full SAM build started only after
all four thermal startup records existed, at monotonic **44471.443257**, and ended
at **44502.677547**. The two thermostat compositions completed later, so this
build overlapped all four. Combined mitigation batches therefore observed **0/12**
fatal compositions, versus **2/8** in the natural failing batch. These finite
samples have different host timing and do not constitute a statistical guarantee.

After those workload comparisons, the final raw-descriptor logging safety fix
was independently reviewed and revalidated through the real blocked-pipe test,
the default suite, PostgreSQL controls, a fresh thermostat and the standalone
container. The build/workload figures above refer to their recorded source hashes;
they are not rewritten as observations of a later source revision.

The [machine-readable evidence record](validation-timeouts-2026-10-08.json) lists
per-batch outcomes, source hashes, evidence hashes and explicit limitations.
