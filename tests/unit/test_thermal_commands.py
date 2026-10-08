import json
from uuid import uuid4

import pytest

from packages.thermal.commands import CommandRejected, parse_command_message


@pytest.mark.parametrize(
    "command_type", ["heating.start", "heating.stop", "cooling.start", "cooling.stop"]
)
def test_explicit_command_types(command_type: str) -> None:
    identity = str(uuid4())
    message = parse_command_message(
        json.dumps(
            {
                "simulation_id": identity,
                "sequence": 1,
                "command_type": command_type,
            }
        )
    )
    assert (message.simulation_id, message.sequence, message.command_type) == (
        identity,
        1,
        command_type,
    )


def test_missing_type_is_distinct_from_invalid_present_type() -> None:
    assert (
        parse_command_message(
            json.dumps({"simulation_id": str(uuid4()), "sequence": 1})
        ).command_type
        is None
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("simulation_id", "bad"),
        ("simulation_id", 1),
        ("sequence", True),
        ("sequence", "1"),
        ("sequence", 1.0),
        ("sequence", 0),
        ("sequence", -1),
        ("sequence", None),
        ("command_type", None),
        ("command_type", True),
        ("command_type", "unknown"),
    ],
)
def test_wire_rejects_coercion_and_invalid_type(field: str, value: object) -> None:
    body = {"simulation_id": str(uuid4()), "sequence": 1, "command_type": "heating.start"}
    body[field] = value
    with pytest.raises(CommandRejected):
        parse_command_message(json.dumps(body))


@pytest.mark.parametrize("body", ["null", "[]", "{}", "not-json", '{"sequence":1,"sequence":2}'])
def test_invalid_command_body(body: str) -> None:
    with pytest.raises(CommandRejected):
        parse_command_message(body)


def test_unexpected_wire_fields_are_rejected() -> None:
    with pytest.raises(CommandRejected):
        parse_command_message(
            json.dumps(
                {
                    "simulation_id": str(uuid4()),
                    "sequence": 1,
                    "command_type": "heating.start",
                    "heater_on": True,
                }
            )
        )


def test_excessively_nested_wire_is_permanently_rejected() -> None:
    with pytest.raises(CommandRejected):
        parse_command_message("[" * 2000 + "0" + "]" * 2000)
