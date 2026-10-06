"""Event entities for HDL Buspro button panels."""

import logging
from datetime import datetime, timezone
from pathlib import Path

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import CONF_MANAGED_DEVICES, DATA_BUSPRO_CONFIG, DOMAIN
from .catalog import DEVICE_CATALOG
from .helpers.entity import registry_device_definitions, registry_device_metadata
from .managed import managed_device_info
from .helpers.logic_controller import (
    logic_controller_coordinator,
    logic_controller_definitions,
)
from .pybuspro.helpers.enums import OperateCode
from .panel_press import load_press_mappings

_LOGGER = logging.getLogger(__name__)

EVENT_ON = "on"
EVENT_OFF = "off"
EVENT_CHANNEL_ON = "channel_on"
EVENT_CHANNEL_OFF = "channel_off"
EVENT_CHANNEL_LEVEL = "channel_level"
EVENT_SCENE = "scene"
EVENT_UNIVERSAL_SWITCH_ON = "universal_switch_on"
EVENT_UNIVERSAL_SWITCH_OFF = "universal_switch_off"
EVENT_LOGIC_TELEGRAM = "telegram"
MAX_DIAGNOSTIC_PAYLOAD = 64


def _panel_telegram_attributes(telegram, device_address):
    """Bounded parsed fields only; never expose UDP endpoints or raw frames."""
    if tuple(telegram.source_address or ()) != device_address:
        return None
    target = telegram.target_address
    payload = telegram.payload
    if (
        not isinstance(target, (tuple, list)) or len(target) != 2
        or not all(type(value) is int and 0 <= value <= 255 for value in target)
        or not isinstance(payload, (tuple, list))
        or not all(type(value) is int and 0 <= value <= 255 for value in payload)
    ):
        return None
    code = telegram.operate_code
    raw_code = getattr(telegram, "operate_code_bytes", None)
    if raw_code is None:
        raw_code = getattr(code, "value", None)
    if not isinstance(raw_code, bytes) or len(raw_code) != 2:
        return None
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "source_address": ".".join(map(str, device_address)),
        "target_address": ".".join(map(str, target)),
        "operate_code": getattr(code, "name", "unknown"),
        "operate_code_hex": raw_code.hex().upper(),
        "raw_payload": list(payload[:MAX_DIAGNOSTIC_PAYLOAD]),
        "payload_length": len(payload),
        "payload_truncated": len(payload) > MAX_DIAGNOSTIC_PAYLOAD,
    }


def _channel_entity(hass, target_address, channel):
    """Resolve a physical Buspro output channel to its HA entity."""
    if hass is None or not target_address:
        return None
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(
        identifiers={(DOMAIN, target_address)}, connections=set()
    )
    if device is None:
        return None

    suffix = f"-{channel}"
    entity_registry = er.async_get(hass)
    # Look up only this device's entries instead of scanning the whole registry
    # on every decoded channel telegram.
    candidates = [
        entry
        for entry in er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        )
        if entry.platform == DOMAIN
        and entry.unique_id.endswith(suffix)
        and entry.entity_id.split(".", 1)[0] in {"cover", "fan", "light", "switch"}
    ]
    if len(candidates) != 1:
        return None
    entry = candidates[0]
    return {
        "entity_id": entry.entity_id,
        "name": entry.name or entry.original_name or entry.entity_id,
    }


def _decode_action(telegram, hass=None):
    """Decode a panel command into an event type and readable attributes."""
    payload = list(telegram.payload or ())
    target_address = tuple(telegram.target_address or ())
    target_key = ".".join(str(part) for part in target_address)
    target = (
        registry_device_metadata(hass, target_key)
        if hass is not None and len(target_address) == 2
        else {}
    )
    attributes = {
        "target_address": target_key,
        "target_device": target.get("name", f"HDL Buspro {target_key}"),
        "target_model": target.get("model", "Buspro device"),
        "raw_payload": payload,
    }

    if telegram.operate_code == OperateCode.SingleChannelControl:
        if len(payload) < 2:
            return None
        channel = int(payload[0])
        level = int(payload[1])
        transition_seconds = (
            int(payload[2]) * 60 + int(payload[3]) if len(payload) >= 4 else 0
        )
        if level == 0:
            event_type = EVENT_CHANNEL_OFF
            action = "off"
        elif level == 100:
            event_type = EVENT_CHANNEL_ON
            action = "on"
        else:
            event_type = EVENT_CHANNEL_LEVEL
            action = f"{level}%"
        channel_entity = _channel_entity(hass, target_key, channel)
        output_name = (
            channel_entity["name"]
            if channel_entity is not None
            else f"{attributes['target_device']}, channel {channel}"
        )
        attributes.update(
            {
                "channel": channel,
                "level": level,
                "transition_seconds": transition_seconds,
                "summary": f"{output_name}: {action}",
            }
        )
        if channel_entity is not None:
            attributes["target_entity"] = channel_entity["entity_id"]
            attributes["target_entity_name"] = channel_entity["name"]
        return event_type, attributes

    if telegram.operate_code == OperateCode.SceneControl:
        if len(payload) < 2:
            return None
        attributes.update(
            {
                "area": int(payload[0]),
                "scene": int(payload[1]),
                "summary": (
                    f"{attributes['target_device']}: area {payload[0]}, "
                    f"scene {payload[1]}"
                ),
            }
        )
        return EVENT_SCENE, attributes

    if telegram.operate_code == OperateCode.UniversalSwitchControl:
        if len(payload) < 2:
            return None
        switch_number = int(payload[0])
        status = int(payload[1])
        action = "off" if status == 0 else "on"
        event_type = (
            EVENT_UNIVERSAL_SWITCH_OFF
            if status == 0
            else EVENT_UNIVERSAL_SWITCH_ON
        )
        attributes.update(
            {
                "switch_number": switch_number,
                "status": status,
                "summary": (
                    f"{attributes['target_device']}, universal switch "
                    f"{switch_number}: {action}"
                ),
            }
        )
        return event_type, attributes

    return None


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up button events for supported catalog and UI-managed panels."""
    module = hass.data[DATA_BUSPRO_CONFIG]["entry_modules"][config_entry.entry_id]
    panels = panel_definitions(hass, config_entry)
    try:
        press_mappings = await hass.async_add_executor_job(
            load_press_mappings,
            Path(__file__).with_name("panel_press_mappings.json"),
            DEVICE_CATALOG,
        )
    except (OSError, ValueError) as err:
        _LOGGER.error("Panel press mappings are invalid; no mapped presses will fire: %s", err)
        press_mappings = {}

    entities = []
    for address, (name, button_count, device_info) in panels.items():
        device_address = tuple(int(part) for part in address.split("."))
        model = device_info.get("model")
        uses_press_map = model in {"HDL-MP8B.46-A", "HDL-MPL8.46-A"}
        press_map = press_mappings.get(address) if uses_press_map else None
        if press_map is not None and press_map.model != device_info.get("model"):
            _LOGGER.warning("Ignoring panel press map for %s: model mismatch", address)
            press_map = None
        # Mapped models expose only configured, distinguishable page/key pairs.
        # Unmapped legacy panel behavior and existing entity IDs remain intact.
        if uses_press_map:
            buttons = press_map.buttons() if press_map is not None else ()
        else:
            buttons = ((1, number) for number in range(1, button_count + 1))
        paged = DEVICE_CATALOG.get(model, {}).get("page_count", 1) > 1
        entities.extend(
            BusproPanelButtonEvent(
                module.hdl,
                device_address,
                address,
                name,
                button_number,
                device_info,
                press_map=press_map,
                page=page,
                paged=paged,
            )
            for page, button_number in buttons
        )
        entities.append(
            BusproPanelActionEvent(
                hass,
                module.hdl,
                device_address,
                address,
                device_info,
            )
        )
        entities.append(
            BusproPanelTelegramEvent(module.hdl, device_address, address, device_info)
        )

    for address, device_info in logic_controller_definitions(
        hass, config_entry
    ).items():
        entities.append(
            BusproLogicControllerEvent(
                hass,
                logic_controller_coordinator(module, address),
                address,
                device_info,
            )
        )

    async_add_entities(entities)


class BusproLogicControllerEvent(EventEntity):
    """Commands and broadcasts transmitted by an HDL logic controller."""

    _attr_event_types = [
        EVENT_CHANNEL_ON,
        EVENT_CHANNEL_OFF,
        EVENT_CHANNEL_LEVEL,
        EVENT_SCENE,
        EVENT_UNIVERSAL_SWITCH_ON,
        EVENT_UNIVERSAL_SWITCH_OFF,
        EVENT_LOGIC_TELEGRAM,
    ]
    _attr_has_entity_name = True
    _attr_name = "Logic event"
    _attr_should_poll = False

    def __init__(self, hass, coordinator, address, device_info):
        self._hass = hass
        self._coordinator = coordinator
        self._telegram_cb = self._handle_telegram
        self._attr_unique_id = f"{DOMAIN}-{address}-logic-event"
        self._attr_device_info = device_info

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._coordinator.register_telegram_cb(self._telegram_cb)

    async def async_will_remove_from_hass(self):
        self._coordinator.unregister_telegram_cb(self._telegram_cb)
        await super().async_will_remove_from_hass()

    def _handle_telegram(self, telegram):
        if telegram.operate_code in {
            OperateCode.IsDeviceOnlineResponse,
            OperateCode.ReadFirmwareVersionResponse,
        }:
            return

        decoded = _decode_action(telegram, self._hass)
        if decoded is not None:
            event_type, attributes = decoded
        else:
            operate_code = telegram.operate_code
            value = getattr(operate_code, "value", b"")
            attributes = {
                "operate_code": getattr(operate_code, "name", str(operate_code)),
                "operate_code_hex": (
                    value.hex().upper() if isinstance(value, bytes) else str(value)
                ),
                "source_address": ".".join(
                    str(part) for part in (telegram.source_address or ())
                ),
                "target_address": ".".join(
                    str(part) for part in (telegram.target_address or ())
                ),
                "raw_payload": list(telegram.payload or ()),
            }
            event_type = EVENT_LOGIC_TELEGRAM

        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()


def panel_definitions(hass, config_entry):
    """Return supported panel addresses and their entity metadata."""
    panels = {}

    for device in registry_device_definitions(hass, config_entry):
        spec = DEVICE_CATALOG.get(device.get("model"), {})
        if spec.get("panel_actions") or spec.get("button_count"):
            panels[device["address"]] = (
                device["name"],
                int(spec.get("button_count", 0)),
                device["device_info"],
            )

    for device_config in config_entry.options.get(CONF_MANAGED_DEVICES, []):
        spec = DEVICE_CATALOG.get(device_config.get("model"), {})
        if spec.get("panel_actions") or spec.get("button_count"):
            panels[device_config["address"]] = (
                device_config["name"],
                int(spec.get("button_count", 0)),
                managed_device_info(device_config),
            )

    return panels


class BusproPanelButtonEvent(EventEntity):
    """A panel key with a configured, distinguishable target command."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = [EVENT_ON, EVENT_OFF]
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        buspro,
        device_address,
        address,
        device_name,
        button_number,
        device_info,
        press_map=None,
        page=1,
        paged=False,
    ):
        self._buspro = buspro
        self._device_address = device_address
        self._button_number = button_number
        self._page = page
        self._paged = paged
        self._press_map = press_map
        if press_map is not None:
            self._attr_event_types = list(
                press_map.event_types_for_button(page, button_number)
            )
        self._telegram_cb = self._handle_telegram
        label = (
            press_map.name_for_button(page, button_number)
            if press_map is not None else f"Button {button_number}"
        )
        self._attr_name = f"Page {page} {label}" if paged else label
        self._attr_unique_id = (
            f"{DOMAIN}-{address}-page-{page}-button-{button_number}"
            if paged else f"{DOMAIN}-{address}-button-{button_number}"
        )
        self._attr_device_info = device_info
        self._attr_extra_state_attributes = {
            "button_number": button_number,
            "panel_name": device_name,
        }
        if paged:
            self._attr_extra_state_attributes["page"] = page

    async def async_added_to_hass(self):
        """Subscribe to telegrams involving this physical panel."""
        await super().async_added_to_hass()
        self._buspro.register_telegram_received_device_cb(
            self._telegram_cb, self._device_address
        )

    async def async_will_remove_from_hass(self):
        """Unsubscribe from panel telegrams."""
        self._buspro.unregister_telegram_received_device_cb(
            self._telegram_cb, self._device_address
        )
        await super().async_will_remove_from_hass()

    def _handle_telegram(self, telegram):
        if tuple(telegram.source_address or ()) != self._device_address:
            return
        if self._press_map is not None:
            match = self._press_map.match(telegram)
            if match is None or match[:2] != (self._page, self._button_number):
                return
            press_type = match[2]
            self._trigger_event(
                press_type,
                {
                    "button_number": self._button_number,
                    **({"page": self._page} if self._paged else {}),
                    "press_type": press_type,
                    "source_address": list(self._device_address),
                    "target_address": list(telegram.target_address or ()),
                    "raw_payload": list(telegram.payload or ()),
                },
            )
            self.async_write_ha_state()
            return

        payload = telegram.payload or []
        if (
            telegram.operate_code != OperateCode.UniversalSwitchControl
            or len(payload) < 2
            or payload[0] != self._button_number
        ):
            return

        status = int(payload[1])
        event_type = EVENT_OFF if status == 0 else EVENT_ON
        self._trigger_event(
            event_type,
            {
                "button_number": self._button_number,
                "status": status,
                "target_address": list(telegram.target_address or ()),
            },
        )
        self.async_write_ha_state()


class BusproPanelActionEvent(EventEntity):
    """All identifiable commands transmitted by a supported panel."""

    _attr_event_types = [
        EVENT_CHANNEL_ON,
        EVENT_CHANNEL_OFF,
        EVENT_CHANNEL_LEVEL,
        EVENT_SCENE,
        EVENT_UNIVERSAL_SWITCH_ON,
        EVENT_UNIVERSAL_SWITCH_OFF,
    ]
    _attr_has_entity_name = True
    _attr_name = "Action"
    _attr_should_poll = False

    def __init__(self, hass, buspro, device_address, address, device_info):
        self._hass = hass
        self._buspro = buspro
        self._device_address = device_address
        self._telegram_cb = self._handle_telegram
        self._attr_unique_id = f"{DOMAIN}-{address}-action"
        self._attr_device_info = device_info

    async def async_added_to_hass(self):
        """Subscribe to telegrams involving this physical panel."""
        await super().async_added_to_hass()
        self._buspro.register_telegram_received_device_cb(
            self._telegram_cb, self._device_address
        )

    async def async_will_remove_from_hass(self):
        """Unsubscribe from panel telegrams."""
        self._buspro.unregister_telegram_received_device_cb(
            self._telegram_cb, self._device_address
        )
        await super().async_will_remove_from_hass()

    def _handle_telegram(self, telegram):
        if tuple(telegram.source_address or ()) != self._device_address:
            return

        decoded = _decode_action(telegram, self._hass)
        if decoded is None:
            return
        event_type, attributes = decoded
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()


class BusproPanelTelegramEvent(EventEntity):
    """Every parsed telegram whose sender is this panel, without action decoding."""

    _attr_event_types = [EVENT_LOGIC_TELEGRAM]
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Telegram"
    _attr_should_poll = False

    def __init__(self, buspro, device_address, address, device_info):
        self._buspro = buspro
        self._device_address = device_address
        self._telegram_cb = self._handle_telegram
        self._attr_unique_id = f"{DOMAIN}-{address}-telegram"
        self._attr_device_info = device_info

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._buspro.register_telegram_received_device_cb(
            self._telegram_cb, self._device_address
        )

    async def async_will_remove_from_hass(self):
        self._buspro.unregister_telegram_received_device_cb(
            self._telegram_cb, self._device_address
        )
        await super().async_will_remove_from_hass()

    def _handle_telegram(self, telegram):
        attributes = _panel_telegram_attributes(telegram, self._device_address)
        if attributes is None:
            return
        self._trigger_event(EVENT_LOGIC_TELEGRAM, attributes)
        self.async_write_ha_state()


class BusproPanelLastTelegramSensor(SensorEntity):
    """Timestamp and bounded fields of the latest parsed panel telegram."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Last telegram"
    _attr_should_poll = False

    def __init__(self, buspro, device_address, address, device_info):
        self._buspro = buspro
        self._device_address = device_address
        self._telegram_cb = self._handle_telegram
        self._attr_unique_id = f"{DOMAIN}-{address}-last-telegram"
        self._attr_device_info = device_info

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._buspro.register_telegram_received_device_cb(
            self._telegram_cb, self._device_address
        )

    async def async_will_remove_from_hass(self):
        self._buspro.unregister_telegram_received_device_cb(
            self._telegram_cb, self._device_address
        )
        await super().async_will_remove_from_hass()

    def _handle_telegram(self, telegram):
        attributes = _panel_telegram_attributes(telegram, self._device_address)
        if attributes is None:
            return
        self._attr_native_value = attributes.pop("observed_at")
        self._attr_extra_state_attributes = attributes
        self.async_write_ha_state()


class BusproPanelLastActionSensor(SensorEntity):
    """Readable summary of the most recent command transmitted by a panel."""

    _attr_has_entity_name = True
    _attr_name = "Last action"
    _attr_should_poll = False

    def __init__(self, hass, buspro, device_address, address, device_info):
        self._hass = hass
        self._buspro = buspro
        self._device_address = device_address
        self._telegram_cb = self._handle_telegram
        self._attr_unique_id = f"{DOMAIN}-{address}-last-action"
        self._attr_device_info = device_info

    async def async_added_to_hass(self):
        """Subscribe to telegrams involving this physical panel."""
        await super().async_added_to_hass()
        self._buspro.register_telegram_received_device_cb(
            self._telegram_cb, self._device_address
        )

    async def async_will_remove_from_hass(self):
        """Unsubscribe from panel telegrams."""
        self._buspro.unregister_telegram_received_device_cb(
            self._telegram_cb, self._device_address
        )
        await super().async_will_remove_from_hass()

    def _handle_telegram(self, telegram):
        if tuple(telegram.source_address or ()) != self._device_address:
            return
        decoded = _decode_action(telegram, self._hass)
        if decoded is None:
            return
        event_type, attributes = decoded
        self._attr_native_value = attributes["summary"]
        self._attr_extra_state_attributes = {
            "action_type": event_type,
            **attributes,
        }
        self.async_write_ha_state()
