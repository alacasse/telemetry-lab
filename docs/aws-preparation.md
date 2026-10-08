# AWS preparation

The repository contains Lambda packaging tools that use a SAM build image, Terraform configuration and staging helper scripts for possible future cloud work. Their presence does not establish deployed resources, successful cloud execution or production readiness.

Phase 3 adds a standalone thermal-engine image and dependency configuration. The
image runs a continuous shared-room process independently of the browser launcher.
It preserves PostgreSQL fencing and explicit recovery, and supplies bounded HTTP
and dedicated SQS command adapters. The runtime uses the SDK credential provider
chain, which supports execution-role credentials; the current ingestion contract
still uses `X-Staging-Token`, resolved from Secrets Manager at startup. These are
different authentication boundaries. [AWS credential providers](https://docs.aws.amazon.com/sdkref/latest/guide/standardized-credentials.html).

Local Docker validation exercises PostgreSQL and LocalStack over a container
network. It proves the standalone composition locally, not IAM permissions,
secret retrieval from AWS, VPC reachability, cloud scheduling or real SQS delivery.
Build/configuration commands are in the [runbook](local-runbook.md#standalone-thermal-engine).

Before the first AWS engine trial, choose its continuous-process host (ECS/Fargate
remains an option), supply a reviewed image distribution and launch path, and
configure network access to PostgreSQL, ingestion, SQS, Secrets Manager and any
required KMS key. Its role needs `sqs:SendMessage`, `sqs:ReceiveMessage` and
`sqs:DeleteMessage` on the dedicated command queue, plus access to the two startup
secrets and applicable decryption. The engine does not need permission to create
queues or send directly to the measurement queue. Configure standard queues,
revision identity, secret rotation, logs, passive health observation and a
supervisor that handles busy refusal and lease-expiry replacement. Apply migrations
with old engines and their supervisors stopped. Then test real crash, replacement,
explicit resume and end-to-end receipts against AWS. No such execution is claimed.

The browser still needs hosting, route exposure and an access/identity decision.
Those are separate dependencies of the usable HTTPS demo. Individual rooms and
the shared browser-storage issue remain excluded from this phase.

GitHub Actions runs quality, integration, bundle and Terraform checks. It has no active AWS deployment job or GitHub AWS identity integration. Optional GitLab OIDC infrastructure configuration remains inactive preparation material; it is not the CI execution path for this repository.

Before any cloud execution, make a separate decision covering the AWS account and region, identity provider and trust policy, least-privilege roles, networking and public access, database version and capacity, Lambda concurrency quotas, secrets, cost limits, migration/recovery and teardown. Inspect Terraform inputs and a reviewed plan for the intended account. LocalStack behavior cannot substitute for that verification.

Do not run provisioning, apply, staging deployment or destroy scripts as part of the local quickstart. No AWS apply is required to demonstrate the local thermostat.

Lambda bundle verification checks artifact contents and entrypoints. Terraform initialization/validation checks configuration without provisioning resources. Record actual cloud validation separately if cloud work is later authorized.
