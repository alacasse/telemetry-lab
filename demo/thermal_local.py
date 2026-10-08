"""Local configuration and passive PostgreSQL runtime observation."""

import math
import os
from functools import lru_cache

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from packages.config import get_settings
from packages.thermal.authority import (
    create_authority_session_factory,
    observe_authority,
    unknown_observation,
)


@lru_cache(maxsize=1)
def observation_sessions() -> sessionmaker[Session]:
    # A separate bounded pool keeps observation independent of API transactions,
    # including routes which already hold a simulation row lock.
    return create_authority_session_factory(get_settings())


def runtime_observation() -> dict:
    try:
        with observation_sessions()() as session:
            return observe_authority(session)
    except (SQLAlchemyError, ValueError):
        return unknown_observation()


def multiplier() -> float:
    value = float(os.environ.get("THERMAL_TIME_MULTIPLIER", "1"))
    if not math.isfinite(value) or not 0.5 <= value <= 2:
        raise ValueError("THERMAL_TIME_MULTIPLIER must be between 0.5 and 2")
    return value
