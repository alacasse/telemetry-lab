# Telemetry Lab

Telemetry Lab is an educational, synthetic thermostat demo. Follow a temperature measurement through an HTTP API, an SQS-compatible queue, a Python worker and PostgreSQL, then follow a control command back to the simulator. The browser shows the evidence behind each step.

The project demonstrates asynchronous processing, transaction boundaries, duplicate handling and explicit recovery. Temperatures and actuator states are simulated. It does not control physical HVAC equipment or use machine learning. Local execution and LocalStack tests do not establish an AWS deployment.

## Quickstart

Install Docker with a running daemon, Python 3.12 and `uv`. Run from the repository root:

```sh
sh scripts/demo-local.sh --port 8088
```

Open [http://127.0.0.1:8088](http://127.0.0.1:8088). The launcher starts disposable PostgreSQL and LocalStack containers, applies migrations, and starts the API, processing worker and thermal simulator in separate processes. It uses loopback endpoints and explicit dummy AWS credentials rather than host AWS credentials or the repository `.env`.

Choose COOL or HEAT and a setpoint between 15 and 30 °C in 0.5 °C steps. Inspect **Details** for measurements, commands and processing evidence. The existing interface retains its French explanatory text.

**Ctrl-C stops the composition and destroys its database and queues.** State survives a browser reload while that composition remains running. Reloading performs passive reads; recovery and retries require explicit actions.

## Explore the project

- [Documentation index](docs/README.md): contracts, architecture and operational boundaries.
- [Local runbook](docs/local-runbook.md): launch, diagnostics and integration checks.
- [Thermostat behavior](docs/thermostat.md): settings, inversion guards and recovery.
- [Validation](docs/validation.md): evidence for this repository and its limitations.
- [Contributing](CONTRIBUTING.md): checks and contribution expectations.
- [Postman](postman/README.md): API exploration.

GitHub Actions defines four check groups: quality, integration, Lambda bundles and Terraform validation. Workflow definitions are not proof of a successful remote run; consult the validation record and the actual Actions results.

AWS-oriented infrastructure and packaging are preparation material. There is no active GitHub-to-AWS deployment integration. See [AWS preparation](docs/aws-preparation.md) before considering cloud execution.
