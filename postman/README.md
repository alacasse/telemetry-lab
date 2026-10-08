# Postman

Import `telemetry-lab.postman_collection.json` with `telemetry-lab.local.postman_environment.json` to explore the separate ingestion and query service APIs. Check the environment URLs against the running service ports before sending requests. The interactive launcher composes routes under its own HTTP origin, so the service environment is not automatically interchangeable with it.

The `collections/`, `environments/` and `globals/` directories contain additional API workflow assets. Use synthetic telemetry and retain the same idempotency inputs when intentionally testing duplicate processing. An accepted ingestion response is a receipt; read the query result to confirm business processing.

`telemetry-lab.aws.postman_environment.json` is a template for future authorized cloud work. It is not evidence of a live deployment. Do not commit credentials or tokens into exported environments. `scripts/render-postman-env.py` can render configured environment values; review its arguments and output before use.
