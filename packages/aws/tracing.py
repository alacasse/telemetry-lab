from __future__ import annotations

from functools import lru_cache
from typing import Any

from packages.config import Settings, get_settings


@lru_cache(maxsize=1)
def _get_xray_recorder() -> Any | None:
    try:
        from aws_xray_sdk.core import xray_recorder
    except ImportError:
        return None
    return xray_recorder


def configure_tracing(service_name: str, settings: Settings | None = None) -> None:
    resolved = settings or get_settings()
    if not resolved.enable_xray:
        return

    recorder = _get_xray_recorder()
    if recorder is None:
        return
    recorder.configure(service=service_name, context_missing="LOG_ERROR")


def annotate_trace(**annotations: object) -> None:
    recorder = _get_xray_recorder()
    if recorder is None:
        return

    segment = recorder.current_segment() or recorder.current_subsegment()
    if segment is None:
        return

    for key, value in annotations.items():
        if value is None:
            continue
        segment.put_annotation(key, value)
