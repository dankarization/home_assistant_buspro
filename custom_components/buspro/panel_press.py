"""Explicit, passive panel-command to physical-button event mappings.

Buspro control telegrams contain a source and a target command, but no button
number or press kind. A configured, unambiguous target command can identify a
candidate physical press. Verified five-byte commands can distinguish MPL8
zero-level presses that otherwise share a four-byte command prefix.
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
    commands: dict[
        tuple[str, tuple[int, int], tuple[int, ...]], tuple[int, int, str]
    ]
    button_names: dict[tuple[int, int], str]

    def event_types_for_button(self, page: int, button_number: int) -> tuple[str, ...]:
        kinds = {
            press for mapped_page, button, press in self.commands.values()
            if (mapped_page, button) == (page, button_number)
        }
        return tuple(press for press in PRESS_TYPES if press in kinds)

    def buttons(self) -> tuple[tuple[int, int], ...]:
        return tuple(sorted({
            (page, button) for page, button, _ in self.commands.values()
        }))

    def name_for_button(self, page: int, button: int) -> str:
        return self.button_names.get((page, button), f"Button {button}")

    def match(self, telegram) -> tuple[int, int, str] | None:
        code = getattr(getattr(telegram, "operate_code", None), "value", None)
        if not isinstance(code, bytes) or len(code) != 2:
            return None
        try:
            target = tuple(telegram.target_address or ())
            payload = tuple(telegram.payload or ())
            if len(target) != 2 or not all(
                type(value) is int and 0 <= value <= 255 for value in target
            ):
                return None
            if not all(
                type(value) is int and 0 <= value <= 255 for value in payload
            ):
                return None
            code_hex = code.hex().upper()
            if code_hex == "E01C":
                if len(payload) == 5 and payload[2:4] == (0, 0):
                    payload = payload[:2]
                elif len(payload) != 2:
                    return None
            elif code_hex == "0031":
                if len(payload) == 5:
                    exact = self.commands.get((code_hex, target, payload))
                    if exact is not None:
                        return exact
                    if any(
                        mapped_code == code_hex
                        and mapped_target == target
                        and len(mapped_payload) == 5
                        and mapped_payload[:4] == payload[:4]
                        for mapped_code, mapped_target, mapped_payload in self.commands
                    ):
                        return None
                    payload = payload[:4]
                elif len(payload) != 4:
                    return None
            elif code_hex == "0002":
                if len(payload) != 2:
                    return None
            else:
                return None
            # Legacy four-byte commands also match five-byte telegrams when
            # no more specific five-byte command is configured.
            return self.commands.get((code_hex, target, payload))
        except (TypeError, ValueError):
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
        page_count = catalog.get(model, {}).get("page_count", 1)
        if not isinstance(button_count, int) or button_count < 1:
            raise ValueError(f"Unsupported panel model at {source}: {model!r}")
        if type(page_count) is not int or page_count < 1:
            raise ValueError(f"Invalid page count for model at {source}: {model!r}")
        actions = panel.get("actions")
        if not isinstance(actions, list):
            raise ValueError(f"Invalid actions at {source}")

        commands = {}
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError(f"Invalid action at {source}: {action!r}")
            button = action.get("button")
            page = action.get("page", 1)
            press = action.get("press")
            code = action.get("operate_code")
            target = _address(action.get("target_address"))
            payload = action.get("payload")
            if type(button) is not int or not 1 <= button <= button_count:
                raise ValueError(f"Invalid button at {source}: {button!r}")
            if type(page) is not int or not 1 <= page <= page_count:
                raise ValueError(f"Invalid page at {source}: {page!r}")
            if press not in PRESS_TYPES:
                raise ValueError(f"Invalid press kind at {source}: {press!r}")
            if not isinstance(code, str) or code not in CONTROL_CODES:
                raise ValueError(f"Invalid control code at {source}: {code!r}")
            lengths = (4, 5) if code == "0031" else (2,)
            if not isinstance(payload, list) or len(payload) not in lengths:
                raise ValueError(f"Invalid {code} payload at {source}")
            signature = (code, target, tuple(_byte(value) for value in payload))
            if signature in commands:
                raise ValueError(f"Ambiguous or duplicate panel command at {source}: {signature}")
            commands[signature] = (page, button, press)

        names = panel.get("button_names", {})
        if not isinstance(names, dict):
            raise ValueError(f"Invalid button names at {source}")
        button_names = {}
        configured_buttons = {(page, button) for page, button, _ in commands.values()}
        for key, name in names.items():
            if not isinstance(key, str) or re.fullmatch(
                r"[1-9]\d*\.[1-9]\d*", key
            ) is None:
                raise ValueError(f"Invalid button name key at {source}: {key!r}")
            page_button = tuple(int(part) for part in key.split("."))
            if (
                page_button not in configured_buttons
                or not isinstance(name, str)
                or not name.strip()
            ):
                raise ValueError(f"Invalid button name at {source}: {key!r}")
            button_names[page_button] = name.strip()

        result[source] = PanelPressMap(
            model=model, commands=commands, button_names=button_names
        )
    return result


def load_press_mappings(path: Path, catalog: dict) -> dict[str, PanelPressMap]:
    """Read an optional integration-local site map without sending bus traffic."""
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    return parse_press_mappings(json.loads(content), catalog)
