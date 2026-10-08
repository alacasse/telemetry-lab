from __future__ import annotations

import json
import logging
import os
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "service",
            "event_id",
            "building_id",
            "zone_id",
            "correlation_id",
            "outcome",
            "release_revision",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        release_revision = payload.get("release_revision") or os.getenv("RELEASE_REVISION")
        if release_revision:
            payload["release_revision"] = release_revision
        return json.dumps(payload, default=str)


def configure_logging(service_name: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    service_logger = logging.getLogger(service_name)
    service_logger.propagate = True


class ServiceLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        kwargs["extra"] = {**(self.extra or {}), **kwargs.get("extra", {})}
        return msg, kwargs


def get_logger(service_name: str) -> logging.LoggerAdapter[logging.Logger]:
    return ServiceLoggerAdapter(logging.getLogger(service_name), {"service": service_name})
