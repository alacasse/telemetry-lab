# Thermal availability correction — 8 October 2026

This follow-up addresses sibling engine transactions turning a finite commit
stall into fatal pool/SQL-lock errors. It is separate from the earlier
[SAM environment mitigation](timeout-investigation-2026-10-08.md). Historical
phase 3 and timeout-investigation evidence is retained without alteration.

## Mechanism and scope

The original recorded 18424/18425 failures established WALSync → retained
connection/authority lock → one-second deadline failure. The hardware/filesystem
origin and SAM's individual contribution were not established. Ports 18381/18382
still lack enough trace evidence to identify their original cause.

The pre-correction engine launched five loops independently. Tick/command share a
clock mutex, but publication, receive preflight and renewal did not share local
transaction admission. All nevertheless acquire the same PostgreSQL authority
row. The separate renewal pool avoided business-pool checkout but did not remove
row contention. A healthy 1.3-second commit could therefore cause a sibling to
produce a genuine one-second pool or SQL-lock error. Permanent inactivation after
that error was correct; issuing the competing local admission was avoidable.

The lock cannot be released before commit: a successor must wait for the admitted
transaction to finish. PostgreSQL documents [row locks through transaction end](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-ROWS)
and notes that even `SELECT FOR UPDATE` can cause disk writes. Publication loops
still acquire authority every 100 ms before finding no work; clock passes still
run at 50 ms intervals and persist a heartbeat even when idle/interrupted. Those
transactions increase exposure, but changing their cadence or selection contract
is unnecessary for this targeted correction and was not mixed into the comparison.
Network calls were already outside the authority lock and remain so.

## Change and deadlines

`PgAuthority.transaction()` queues local sibling transactions before creating a
session, then guards owner/generation/expiry under the existing database lock and
holds local admission through commit/rollback and session closure. Renewal shares
this gate and takes priority when waiting, retaining admission through local
postcommit confirmation. A database error marks the authority failed before the
gate reopens. Domain rejection still rolls back without poisoning authority.

The scheduling change adds no safety budget: waiters check the existing five-second
confirmed-renewal deadline every 50 ms and again before entry; normal stop wakes
and cancels them. The lease stays ten seconds and renewal's interval stays two
seconds. Pool capacity remains one per pool, checkout/SQL lock waits one second,
connection/statement/idle limits two seconds. PostgreSQL remains on disk with
fsync enabled.

If renewal wakes with confirmation age `a`, a currently admitted transaction has
`d` seconds remaining, and renewal takes `r` seconds, confirmation still requires
`a + d + r < 5`. Priority removes further business queueing from that expression;
it cannot make an arbitrarily slow database safe. Actual SQL errors, external
contention, authority loss or the five-second gap still permanently disable the
process. An already admitted transaction can finish after expiry while retaining
the fence, as the existing contract allows. This is not a guarantee that every
commit is cancellable or that every storage stall can be survived.

The separate HTTP freshness contract is also unchanged: a clock proof older than
three seconds is `unavailable`. A surviving multi-second stall can therefore cause
a transient unavailable observation. The correction prevents the demonstrated
avoidable permanent inactivation; it does not guarantee uninterrupted HTTP
availability or hide stale clock evidence.

Normal stop cancels queued admissions without converting it to authority failure.
The independent four-second watchdog still covers joins, cleanup and transport
closure. Final recovery/release bypasses the stopped local queue only after all
threads stop, with a fresh database guard. Startup acquisition/recovery/creation
remains one atomic transaction. Busy startup still exits 75. API/worker writes and
passive observation do not participate in the local gate. No network call spans it.

## Evidence

Raw evidence and the saved starting working tree are under
`.run/validation/availability-fix-2026-10-08/`. The
[machine-readable record](validation-availability-2026-10-08.json) identifies
source manifests, commands, return codes, observations and artifact hashes.

The finite-delay regression uses a spawned real five-loop process, production
session factories and real PostgreSQL. One genuine business or renewal transaction
is retained for 1.3 seconds immediately before DBAPI commit, independently of any
fatal event. This is controlled commit retention, not an injected storage device
or a claim of physical WALSync reproduction. The delay is below the existing
two-second idle-transaction bound. The test requires queued contenders, continued
business steps, heartbeat and renewal, exact owner/generation continuity, transport
progress and a graceful stop within four seconds.

The unchanged before sources fail both finite-delay cases with code 1, pool
TimeoutError and/or SQLSTATE 55P03. The corrected sources pass both. External pool
occupation and an independent PostgreSQL row holder remain fatal controls; they
assert permanent rejection, unchanged business state, no transport effect and no
late release. Historical delayed-renewal fatal artifacts are not overwritten:
the old test holder waited for failure, so it was replaced by an external-lock
control and these finite-delay availability tests.

Unit controls cover renewal priority, cancellation before checkout, permanent
five-second failure, local admission through commit/rollback, failure publication
before a sibling enters, and admission through renewal confirmation. Existing
PostgreSQL fencing, startup rollback, real connection loss, suspension/takeover,
passive multiuser observation and no-late-cleanup tests remain in the suite.

The exact final integration file also includes SIGTERM while several loops wait
behind the retained business commit. All three cases fail on the saved before
sources and pass on the corrected sources. The separate queued-stop run completed
in approximately **1.018 seconds**, preserving the generation and releasing the
owner without a fatal report. `test-final-red.*` and `test-final-green.*` retain
both results; `test-baseline-runtime/` contains the before runtime plus the exact
final test. The final whole PostgreSQL suite passes **17 cases**.

## Concurrent workload comparison

Each version runs eight fresh compositions (four thermostat and four historical
scenario suites), at most four concurrently, through the same reproduction
harness. Before uses ports **18500–18507**; after uses **18520–18527**. Frozen source
copies and per-launch hashes prevent in-flight source edits. Two complete SAM
builds per campaign use the same frozen before sources and local image, ordinary
container disk for temporary installations/caches, and separate final bundle
directories. They do not use the previous tmpfs helper. These bundles establish
the load, not packaging qualification of the corrected code.

The passive observer targets only those disposable databases, with autocommit,
a one-second statement limit and nominal 0.2-second polling. Actual before median
intervals were **0.264–0.297 seconds**, with a maximum gap of **2.281 seconds**.
No SQL text or business content is collected. A build starts once the four
designated launch logs exist. Because completed compositions are replaced as
executor slots become free, the second build need not overlap four still-live
engines. Build intervals and exact launch/exit times are retained, not described
as continuous four-way concurrency throughout.

| Measurement | Before | Corrected |
| --- | --- | --- |
| Compositions with unexpected fatal reports | **7/8** | **0/8** |
| Nonzero launcher exits | 7/8 | 0/8 |
| Thermostat scenarios passed | 1/4 | 4/4 |
| Historical scenarios passed | 0/4 | 4/4 |
| Raw fatal reports | 34 | 0 |
| Successful full disk-backed SAM builds | 2/2 | 2/2 |
| Sampled WALSync rows | 666 | 921 |
| Sampled rows with blockers | 39 | 15 |
| Recorded connection holds over one second | 11 | 8 |
| Largest recorded connection hold | 4.676 s | 3.213 s |

These are observed frequencies, not independent-trial probability estimates.
Samples/holds may repeat one underlying wait. In particular, the corrected lot
did not reproduce the before lot's longest 4.676-second hold. The controlled
1.3-second tests and the correlated surviving 3.213-second WALSync event support
the specific admission correction; the table does not establish universal
resilience under every storage delay. Within `packages/thermal`, only
`authority.py`, `runtime.py` and `process.py` differ across the two source copies. All eight manifests
within each campaign match. Deliberate crash-test replacements are not counted
as unexpected fatal failures.

The before batch has **7/8 compositions with fatal reports** (all seven also
exit nonzero), with **34 raw fatal lines**, not 34 independent faults. In one
confirmed chain, port **18503**, generation 4, command-publication backend **165**
holds the business connection from monotonic **46258.4821**. Samples through
**46259.3298** show that backend in WALSync and renewal backend **166** waiting on
`transactionid`, blocked by 165. Clock checkout fails at **46259.4900**. This lies
inside SAM wave 1's interval, **46253.7092–46292.3804**. The exact correlation is
saved in `before-load/correlation.json`.

The corrected batch retains a directly comparable trigger: on port **18523**,
generation 1, reading-publication backend **91** holds its connection for
**3.2134 seconds** (monotonic **46475.2841–46478.4975**). Multiple observations of
that same backend show WALSync during the interval, inside the first SAM build.
The composition completes with code 0 and no fatal report. Thus the availability
regression is not explained by making all connection holds shorter than one
second. `after-load/correlation.json` preserves the observations.

Both campaigns overlap auxiliary pytest/PostgreSQL checks at different times.
They use the same bounded composition/build workload, but do not isolate SAM's
individual contribution or reproduce the host scheduler identically.

## Final checks and preservation

| Check | Result |
| --- | --- |
| Ruff / mypy | Passed; 122 Python sources |
| Default Python suite | 261 passed, 37 opt-in skips, 21 existing warnings |
| Disposable PostgreSQL suite | 17 passed |
| Focused admission/availability tests | 11 passed; exact final integration file has 3 failures before the fix |
| Eight corrected launcher compositions | All passed, no unexpected fatal reports |
| Separate crash windows, port 18540 | 2 passed, 8 deliberately deselected |
| Standalone runtime container | Pipeline, receipts, busy 75, lease-expiry replacement, explicit resume/no catch-up, exact-body redelivery passed |
| Container shutdown | SIGTERM 1.141 s; SIGINT 1.133 s, including Docker host timing |
| PostgreSQL durability | `fsync=on`, `synchronous_commit=on`, disk volume, no tmpfs |
| Independent general/import reviews | No actionable findings |
| Historical raw evidence | All 792 hashes matched; no missing files |

Qualified runtime image:
`sha256:bae3e8291180a87ef414c9053be1b9c7766309b7570ed7db928596a63594a976`.
Container and crash checks ran after the comparison completed. Their own
containers, network and test volume were removed; no unrelated resources were
removed. All 14 containers present before the load campaign remained afterward.
The existing blocked-output/watchdog tests passed in the default suite.

Reproduction commands (fresh evidence paths and unused ports required):

```sh
.venv/bin/ruff check .
.venv/bin/mypy .
.venv/bin/pytest -ra
PYTHON=.venv/bin/python sh scripts/test-postgres.sh
.venv/bin/python scripts/demo-local.py --test-crash-windows --port 18540
PYTHON=.venv/bin/python sh scripts/test-thermal-engine.sh
```

The exact recorded load commands, image identity and timings are in each
`before-load/` and `after-load/` directory. The local `run-load.py` harness and
frozen trees are retained for inspection. Do not rerun their existing evidence
directories or reuse recorded PIDs for signals.

## Qualification limits

Finite local runs are not a throughput guarantee or AWS evidence. Passive wait
samples record transaction age and sampled wait event, not exact continuous wait
duration. Timing diagnostics can be dropped. The comparative workload keeps SAM
temporaries on ordinary disk and never moves PostgreSQL to memory or changes fsync.
Independent builds and host scheduling are not bit-for-bit load replays; controlled
regressions supply the causal before/after evidence.

No commit, push, AWS call, cloud provisioning, upload or deployment is authorized
or performed. Only disposable resources belonging to these validation runs may be
stopped. Existing demonstrations and historical evidence must remain intact.
