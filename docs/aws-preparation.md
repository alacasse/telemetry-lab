# AWS preparation

The repository contains Lambda packaging tools, SAM-related artifacts, Terraform configuration and staging helper scripts for possible future cloud work. Their presence does not establish deployed resources, successful cloud execution or production readiness.

GitHub Actions runs quality, integration, bundle and Terraform checks. It has no active AWS deployment job or GitHub AWS identity integration. Optional GitLab OIDC infrastructure configuration remains inactive preparation material; it is not the CI execution path for this repository.

Before any cloud execution, make a separate decision covering the AWS account and region, identity provider and trust policy, least-privilege roles, networking and public access, database version and capacity, Lambda concurrency quotas, secrets, cost limits, migration/recovery and teardown. Inspect Terraform inputs and a reviewed plan for the intended account. LocalStack behavior cannot substitute for that verification.

Do not run provisioning, apply, staging deployment or destroy scripts as part of the local quickstart. No AWS apply is required to demonstrate the local thermostat.

Lambda bundle verification checks artifact contents and entrypoints. Terraform initialization/validation checks configuration without provisioning resources. Record actual cloud validation separately if cloud work is later authorized.
