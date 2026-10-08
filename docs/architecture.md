# Architecture and contracts

## Processing paths

```mermaid
flowchart LR
    Simulator -->|HTTP telemetry| API
    API -->|measurement message| SQS[SQS-compatible measurement queue]
    SQS --> Worker
    Worker --> PostgreSQL
    PostgreSQL -->|pending control command| Commands[SQS-compatible command queue]
    Commands --> Simulator
    Browser -->|passive snapshots| API
```

`demo/` composes the local browser experience and thermal runtime. `services/` contains ingestion, query, processing and simulator applications. `packages/` holds shared domain, database, configuration and queue code. `migrations/` owns the PostgreSQL schema.

The API accepting a measurement establishes receipt/publication evidence, not completion. The worker commits business results in PostgreSQL before acknowledging successful processing to the queue. A failed pre-commit attempt can be redelivered. A committed message can also be delivered again when acknowledgement fails.

## Duplicates and retries

The worker checks the measurement idempotency key before creating another business event. A detected duplicate refers to its original event when the available journal evidence permits that relationship to be shown. This is a deduplication contract, not a general exactly-once guarantee.

The browser retains exact submitted payloads and receipts in its tab cache. An explicit retry resends the preserved body; it does not reconstruct a payload from normalized results. A lost HTTP response leaves the outcome ambiguous. Reloading resumes observation, not submission.

Thermostat setting operations use a UUID `operation_id` and `expected_revision`. Identical retries reuse the operation identity; conflicting reuse or stale state is rejected. Read the saved operation and current snapshot before deciding whether to continue after an uncertain response.

## Evidence and transaction boundaries

Business results and the processing journal have different roles. Journal observations are best effort; missing observations remain unknown. A terminal observation is associated with the business transaction and becomes visible after commit. A journal failure must not fabricate a business failure or trigger a second committed business event.

The UI reads snapshots with measurement, command and operation identities and timestamps. The last command may originate from a different measurement than the latest displayed measurement. The diagram pulses on newly observed evidence; initial or identical snapshots do not create new processing evidence.

Queue counts are approximate aggregate observations. They do not prove that an individual measurement completed. The local queue-observation route reads queue attributes and does not consume messages. Missing counts remain unavailable, and old samples retain their original timestamp.

The database persists during one launcher session. Browser receipts additionally depend on tab storage. Neither survives deletion of the disposable environment as a durable audit system.
