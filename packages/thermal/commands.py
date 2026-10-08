"""Strict thermal wire contract and supported actuator intentions."""

import json
from dataclasses import dataclass
from uuid import UUID

COMMAND_TYPES = frozenset({"heating.start", "heating.stop", "cooling.start", "cooling.stop"})
HEATING_TYPES = frozenset({"heating.start", "heating.stop"})


class CommandRejected(ValueError):  # noqa: N818
    """A delivery cannot be matched to a supported authoritative command."""


@dataclass(frozen=True)
class CommandMessage:
    simulation_id: str
    sequence: int
    command_type: str | None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate command field")
        result[key] = value
    return result


def parse_command_message(body: str) -> CommandMessage:
    try:
        value = json.loads(body, object_pairs_hook=_unique_object)
        if not isinstance(value, dict):
            raise ValueError("command must be an object")
        if set(value) - {"simulation_id", "sequence", "command_type"}:
            raise ValueError("unexpected command field")
        identity = value["simulation_id"]
        if not isinstance(identity, str):
            raise ValueError("simulation_id must be a UUID string")
        identity = str(UUID(identity))
        sequence = value["sequence"]
        if type(sequence) is not int or sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        command_type = None
        if "command_type" in value:
            command_type = value["command_type"]
            if not isinstance(command_type, str) or command_type not in COMMAND_TYPES:
                raise ValueError("invalid command_type")
        return CommandMessage(identity, sequence, command_type)
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise CommandRejected("invalid thermal command message") from exc
