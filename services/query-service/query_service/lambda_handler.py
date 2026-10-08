from __future__ import annotations

from functools import lru_cache
from typing import Any

from mangum import Mangum

from query_service.main import app


@lru_cache(maxsize=1)
def get_adapter() -> Mangum:
    return Mangum(app, lifespan="off")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return get_adapter()(event, context)
