"""Explicit, passive panel-command to physical-button event mappings.

Buspro control telegrams contain a source and a target command, but no button
number or press kind. Only a site-verified, unambiguous command may identify a
physical press; timing or universal-switch numbers are not button identities.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


PRESS_TYPES = ("single_press", "double_press", "long_press")
CONTROL_CODES = {"0002", "0031", "E01C"}


def _address(value: str) -> tuple[int, int]:
    if not isinstance(value, str) or re.fullmatch(r"\d{1,3}\.\d{1,3}", value) is None:
        raise ValueError(f"Invalid Buspro address: {value!r}")
    parts = tuple(int(part) for part in value.split("."))
    if any(part > 255 for part in parts) or value != ".".join(map(str, parts)):
        raise ValueError(f"Invalid Buspro address: {value!r}")
    return parts


def _byte(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 255:
        raise ValueError(f"Invalid Buspro payload byte: {value!r}")
    return value


@dataclass(frozen=True)
class PanelPressMap:
    """A validated lookup for one physical panel."""

    model: str
    commands: dict[tuple[str, tuple[int, int], tuple[int, ...]], tuple[int, str]]

    def event_types_for_button(self, button_number: int) -> tuple[str, ...]:
        kinds = {
            press for button, press in self.commands.values() if button == button_number
        }
        return tuple(press for press in PRESS_TYPES if press in kinds)

    def match(self, telegram) -> tuple[int, str] | None:
        code = getattr(getattr(telegram, "operate_code", None), "value", None)
        if not isinstance(code, bytes) or len(code) != 2:
            return None
        try:
            target = tuple(telegram.target_address or ())
            payload = tuple(telegram.payload or ())
            return self.commands.get((code.hex().upper(), target, payload))
        except TypeError:
            return None


def parse_press_mappings(data: dict, catalog: dict) -> dict[str, PanelPressMap]:
    """Validate mappings completely; a conflicting/invalid file emits no events."""
    if not isinstance(data, dict) or not isinstance(data.get("panels"), dict):
        raise ValueError("Panel press mappings need a panels object")

    result = {}
    for source, panel in data["panels"].items():
        _address(source)
        if not isinstance(panel, dict):
            raise ValueError(f"Invalid panel mapping at {source}")
        model = panel.get("model")
        if not isinstance(model, str):
            raise ValueError(f"Invalid panel model at {source}: {model!r}")
        button_count = catalog.get(model, {}).get("button_count", 0)
        if not isinstance(button_count, int) or button_count < 1:
            raise ValueError(f"Unsupported panel model at {source}: {model!r}")
        actions = panel.get("actions")
        if not isinstance(actions, list):
            raise ValueError(f"Invalid actions at {source}")

        commands = {}
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError(f"Invalid action at {source}: {action!r}")
            button = action.get("button")
            press = action.get("press")
            code = action.get("operate_code")
            target = _address(action.get("target_address"))
            payload = action.get("payload")
            if type(button) is not int or not 1 <= button <= button_count:
                raise ValueError(f"Invalid button at {source}: {button!r}")
            if press not in PRESS_TYPES:
                raise ValueError(f"Invalid press kind at {source}: {press!r}")
            if not isinstance(code, str) or code not in CONTROL_CODES:
                raise ValueError(f"Invalid control code at {source}: {code!r}")
            if not isinstance(payload, list) or len(payload) != (4 if code == "0031" else 2):
                raise ValueError(f"Invalid {code} payload at {source}")
            signature = (code, target, tuple(_byte(value) for value in payload))
            if signature in commands:
                raise ValueError(f"Ambiguous or duplicate panel command at {source}: {signature}")
            commands[signature] = (button, press)

        result[source] = PanelPressMap(model=model, commands=commands)
    return result


def load_press_mappings(path: Path, catalog: dict) -> dict[str, PanelPressMap]:
    """Read an optional integration-local site map without sending bus traffic."""
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    return parse_press_mappings(json.loads(content), catalog)
