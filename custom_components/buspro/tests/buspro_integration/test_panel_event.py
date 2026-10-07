"""Stubbed HA event-platform regression tests for mapped MP8B presses."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BUSPRO_PATH = Path(__file__).parents[2]


def _load_event_module():
    prefix = "_buspro_panel_event_test"

    def module(name, **members):
        item = types.ModuleType(name)
        item.__dict__.update(members)
        return item

    class EventEntity:
        async def async_added_to_hass(self):
            pass

        async def async_will_remove_from_hass(self):
            pass

        def _trigger_event(self, kind, attributes):
            self.events = getattr(self, "events", []) + [(kind, attributes)]

        def async_write_ha_state(self):
            pass

    class SensorEntity(EventEntity):
        pass

    class EventDeviceClass:
        BUTTON = "button"

    class EntityCategory:
        DIAGNOSTIC = "diagnostic"

    package = module(prefix)
    package.__path__ = [str(BUSPRO_PATH)]
    pybuspro = module(f"{prefix}.pybuspro")
    pybuspro.__path__ = [str(BUSPRO_PATH / "pybuspro")]
    helpers = module(f"{prefix}.pybuspro.helpers")
    helpers.__path__ = [str(BUSPRO_PATH / "pybuspro" / "helpers")]

    enums_spec = importlib.util.spec_from_file_location(
        f"{prefix}.pybuspro.helpers.enums",
        BUSPRO_PATH / "pybuspro" / "helpers" / "enums.py",
    )
    enums = importlib.util.module_from_spec(enums_spec)
    enums_spec.loader.exec_module(enums)

    catalog = {
        "HDL-MP8B.46-A": {"panel_actions": True, "button_count": 8},
        "HDL-MPL8.46-A": {"panel_actions": True, "button_count": 8, "page_count": 4},
    }
    helpers_package = module(f"{prefix}.helpers")
    helpers_package.__path__ = [str(BUSPRO_PATH / "helpers")]
    modules = {
        prefix: package,
        f"{prefix}.const": module(
            f"{prefix}.const",
            CONF_MANAGED_DEVICES="managed_devices",
            DATA_BUSPRO_CONFIG="buspro_config",
            DOMAIN="buspro",
        ),
        f"{prefix}.catalog": module(
            f"{prefix}.catalog", DEVICE_CATALOG=catalog
        ),
        f"{prefix}.helpers": helpers_package,
        f"{prefix}.helpers.entity": module(
            f"{prefix}.helpers.entity",
            registry_device_definitions=lambda *_: [],
            registry_device_metadata=lambda *_: {},
        ),
        f"{prefix}.managed": module(
            f"{prefix}.managed",
            managed_device_info=lambda config: {"model": config["model"]},
        ),
        f"{prefix}.helpers.logic_controller": module(
            f"{prefix}.helpers.logic_controller",
            logic_controller_coordinator=lambda *_: None,
            logic_controller_definitions=lambda *_: {},
        ),
        f"{prefix}.pybuspro": pybuspro,
        f"{prefix}.pybuspro.helpers": helpers,
        f"{prefix}.pybuspro.helpers.enums": enums,
        "homeassistant": module("homeassistant"),
        "homeassistant.components": module("homeassistant.components"),
        "homeassistant.components.event": module(
            "homeassistant.components.event",
            EventDeviceClass=EventDeviceClass,
            EventEntity=EventEntity,
        ),
        "homeassistant.components.sensor": module(
            "homeassistant.components.sensor", SensorEntity=SensorEntity
        ),
        "homeassistant.const": module(
            "homeassistant.const", EntityCategory=EntityCategory
        ),
        "homeassistant.helpers": module("homeassistant.helpers"),
        "homeassistant.helpers.device_registry": module(
            "homeassistant.helpers.device_registry"
        ),
        "homeassistant.helpers.entity_registry": module(
            "homeassistant.helpers.entity_registry"
        ),
    }
    with patch.dict(sys.modules, modules):
        press_spec = importlib.util.spec_from_file_location(
            f"{prefix}.panel_press", BUSPRO_PATH / "panel_press.py"
        )
        press_module = importlib.util.module_from_spec(press_spec)
        sys.modules[press_spec.name] = press_module
        press_spec.loader.exec_module(press_module)
        spec = importlib.util.spec_from_file_location(
            f"{prefix}.event", BUSPRO_PATH / "event.py"
        )
        event_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = event_module
        spec.loader.exec_module(event_module)
    return event_module, enums.OperateCode


class _Buspro:
    def __init__(self):
        self.callbacks = []

    def register_telegram_received_device_cb(self, callback, address):
        self.callbacks.append((callback, address))

    def unregister_telegram_received_device_cb(self, callback, address):
        self.callbacks.remove((callback, address))

    def receive(self, telegram):
        for callback, address in list(self.callbacks):
            if address in (telegram.source_address, telegram.target_address):
                callback(telegram)


class _Hass:
    def __init__(self, buspro):
        self.data = {
            "buspro_config": {
                "entry_modules": {"entry": SimpleNamespace(hdl=buspro)}
            }
        }

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class PanelEventTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.event, self.OperateCode = _load_event_module()
        self.buspro = _Buspro()
        self.hass = _Hass(self.buspro)
        self.entry = SimpleNamespace(
            entry_id="entry",
            options={"managed_devices": [
                {"address": "1.6", "name": "Kitchen wall",
                 "model": "HDL-MP8B.46-A"}
            ]},
        )

    async def _entities(self, mapping=True):
        entities = []
        if mapping:
            await self.event.async_setup_entry(self.hass, self.entry, entities.extend)
        else:
            with patch.object(self.event, "load_press_mappings", return_value={}):
                await self.event.async_setup_entry(self.hass, self.entry, entities.extend)
        for entity in entities:
            await entity.async_added_to_hass()
        return entities

    async def test_only_mapped_buttons_exist_and_gestures_are_exact(self):
        entities = await self._entities()
        buttons = {
            item._button_number: item
            for item in entities
            if isinstance(item, self.event.BusproPanelButtonEvent)
        }
        self.assertEqual(set(buttons), {5, 6, 7, 8})
        self.assertEqual(buttons[5]._attr_event_types,
                         ["single_press", "long_press"])

        def receive(source, target, payload):
            self.buspro.receive(SimpleNamespace(
                source_address=source,
                target_address=target,
                payload=payload,
                operate_code=self.OperateCode.UniversalSwitchControl,
            ))

        receive((1, 6), (1, 99), [20, 255, 0, 0, 5])
        receive((1, 6), (1, 99), [18, 255, 0, 0, 5])
        receive((1, 6), (1, 99), [20, 1])
        receive((1, 6), (1, 98), [20, 255, 0, 0, 6])
        receive((1, 99), (1, 6), [20, 255])
        self.assertEqual(
            [kind for kind, _ in buttons[5].events],
            ["single_press", "long_press"],
        )
        self.assertFalse(hasattr(buttons[6], "events"))
        self.assertEqual(buttons[5]._attr_unique_id, "buspro-1.6-button-5")

    async def test_paged_panel_entities_are_typed_and_sender_bound(self):
        self.event._channel_entity = lambda *_: None
        self.entry.options["managed_devices"] = [{
            "address": "1.12", "name": "ENTRY",
            "model": "HDL-MPL8.46-A",
        }]
        entities = await self._entities()
        buttons = {
            (item._page, item._button_number): item
            for item in entities
            if isinstance(item, self.event.BusproPanelButtonEvent)
        }
        self.assertEqual(len(buttons), 24)
        self.assertNotIn((4, 1), buttons)
        self.assertEqual(buttons[2, 5]._attr_event_types,
                         ["single_press", "long_press"])
        self.assertEqual(buttons[3, 3]._attr_event_types, ["single_press"])
        self.assertEqual(buttons[3, 3]._attr_unique_id,
                         "buspro-1.12-page-3-button-3")
        self.assertEqual(buttons[2, 5]._attr_name, "Page 2 Porch UP")

        def receive(source, target, payload, code):
            self.buspro.receive(SimpleNamespace(
                source_address=source,
                target_address=target,
                payload=payload,
                operate_code=code,
            ))

        receive((1, 12), (1, 99), [20, 255, 0, 0, 5],
                self.OperateCode.UniversalSwitchControl)
        receive((1, 12), (1, 99), [18, 255, 0, 0, 5],
                self.OperateCode.UniversalSwitchControl)
        receive((1, 12), (1, 3), [1, 12, 0, 0, 3],
                self.OperateCode.SingleChannelControl)
        receive((1, 12), (1, 3), [1, 0, 0, 0, 3],
                self.OperateCode.SingleChannelControl)
        receive((1, 99), (1, 12), [20, 255, 0, 0, 5],
                self.OperateCode.UniversalSwitchControl)
        receive((1, 12), (1, 98), [20, 255, 0, 0, 5],
                self.OperateCode.UniversalSwitchControl)
        receive((1, 12), (1, 99), [20, 0, 0, 0, 5],
                self.OperateCode.UniversalSwitchControl)
        self.assertEqual([kind for kind, _ in buttons[2, 5].events],
                         ["single_press", "long_press"])
        self.assertEqual([kind for kind, _ in buttons[3, 3].events],
                         ["single_press"])
        self.assertEqual(buttons[3, 3].events[0][1]["page"], 3)
        self.assertFalse(hasattr(buttons[2, 6], "events"))
        actions = [item for item in entities
                   if isinstance(item, self.event.BusproPanelActionEvent)]
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(actions[0].events), 6)

    async def test_s121_new_keys_have_stable_entities_and_ignore_shared_off(self):
        self.event._channel_entity = lambda *_: None
        self.entry.options["managed_devices"] = [{
            "address": "1.12", "name": "ENTRY",
            "model": "HDL-MPL8.46-A",
        }]
        entities = await self._entities()
        buttons = {
            (item._page, item._button_number): item
            for item in entities
            if isinstance(item, self.event.BusproPanelButtonEvent)
        }
        for (page, button), (name, level) in {
            (1, 8): ("Master", 14),
            (2, 1): ("HA 2.1", 15),
            (2, 2): ("HA 2.2", 16),
            (2, 3): ("HA 2.3", 17),
            (2, 4): ("HA 2.4", 18),
        }.items():
            with self.subTest(page=page, button=button):
                entity = buttons[page, button]
                self.assertEqual(entity._attr_name, f"Page {page} {name}")
                self.assertEqual(entity._attr_unique_id,
                                 f"buspro-1.12-page-{page}-button-{button}")
                self.assertEqual(entity._attr_event_types, ["single_press"])
                for payload in ([1, level, 0, 0, button], [1, 0, 0, 0, button]):
                    self.buspro.receive(SimpleNamespace(
                        source_address=(1, 12), target_address=(1, 3),
                        payload=payload,
                        operate_code=self.OperateCode.SingleChannelControl,
                    ))
                self.assertEqual([kind for kind, _ in entity.events],
                                 ["single_press"])
                self.assertEqual(entity.events[0][1]["raw_payload"],
                                 [1, level, 0, 0, button])
        for page_button in ((1, 8), (2, 1), (2, 2), (2, 3), (2, 4)):
            self.assertEqual(len(buttons[page_button].events), 1)

    async def test_seven_panels_expose_nine_mapped_buttons_and_actions(self):
        self.entry.options["managed_devices"] = [
            {"address": address, "name": f"Panel {address}",
             "model": "HDL-MP8B.46-A"}
            for address in ("1.4", "1.5", "1.6", "1.7", "1.8", "1.10", "1.11")
        ]
        entities = await self._entities()
        buttons = [item for item in entities
                   if isinstance(item, self.event.BusproPanelButtonEvent)]
        actions = [item for item in entities
                   if isinstance(item, self.event.BusproPanelActionEvent)]
        diagnostics = [item for item in entities
                       if isinstance(item, self.event.BusproPanelTelegramEvent)]
        self.assertEqual(len(buttons), 9)
        self.assertEqual(len(actions), 7)
        self.assertEqual(len(diagnostics), 7)

    async def test_missing_map_never_falls_back_to_uv_number_as_button(self):
        entities = await self._entities(mapping=False)
        self.assertFalse(any(
            isinstance(item, self.event.BusproPanelButtonEvent)
            for item in entities
        ))
        self.assertTrue(any(
            isinstance(item, self.event.BusproPanelActionEvent)
            for item in entities
        ))
        self.assertTrue(any(
            isinstance(item, self.event.BusproPanelTelegramEvent)
            for item in entities
        ))

    async def test_diagnostic_telegram_is_independent_of_action_mapping(self):
        entities = await self._entities()
        diagnostic = next(item for item in entities
                          if isinstance(item, self.event.BusproPanelTelegramEvent))
        last = self.event.BusproPanelLastTelegramSensor(
            self.buspro, (1, 6), "1.6", {"model": "HDL-MP8B.46-A"}
        )
        await last.async_added_to_hass()
        action = next(item for item in entities
                      if isinstance(item, self.event.BusproPanelActionEvent))
        button = next(item for item in entities
                      if isinstance(item, self.event.BusproPanelButtonEvent)
                      and item._button_number == 5)
        self.assertEqual(diagnostic._attr_unique_id, "buspro-1.6-telegram")
        self.assertEqual(last._attr_unique_id, "buspro-1.6-last-telegram")
        self.assertEqual(action._attr_unique_id, "buspro-1.6-action")
        self.assertEqual(button._attr_unique_id, "buspro-1.6-button-5")

        def receive(source, target, payload, code, raw_code=None):
            self.buspro.receive(SimpleNamespace(
                source_address=source, target_address=target, payload=payload,
                operate_code=code, operate_code_bytes=raw_code,
                udp_address=("private-gateway", 6000), udp_data=b"private-frame",
            ))

        # Unknown parsed opcode and unsupported known opcode still reach diagnostics.
        receive((1, 6), (1, 99), [7, 8], None, b"\xAB\xCD")
        receive((1, 6), (1, 99), [7, 8],
                self.OperateCode.ReadStatusOfChannels, b"\x00\x33")
        self.assertEqual(len(diagnostic.events), 2)
        self.assertEqual(diagnostic.events[0][0], "telegram")
        self.assertEqual(diagnostic.events[0][1]["operate_code_hex"], "ABCD")
        self.assertEqual(diagnostic.events[0][1]["operate_code"], "unknown")
        self.assertEqual(last._attr_extra_state_attributes["operate_code_hex"], "0033")
        self.assertFalse(hasattr(action, "events"))
        self.assertFalse(hasattr(button, "events"))

        # Known action and typed button continue firing unchanged.
        receive((1, 6), (1, 99), [20, 255, 0, 0, 5],
                self.OperateCode.UniversalSwitchControl, b"\xE0\x1C")
        self.assertEqual(len(diagnostic.events), 3)
        self.assertEqual(len(action.events), 1)
        self.assertEqual(len(button.events), 1)
        self.assertEqual(last._attr_extra_state_attributes["raw_payload"],
                         [20, 255, 0, 0, 5])
        self.assertRegex(last._attr_native_value,
                         r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00$")
        self.assertNotIn("private-gateway", str(diagnostic.events))
        self.assertNotIn("private-frame", str(diagnostic.events))

        # Device-address subscriptions include target traffic; exact sender filter excludes it.
        receive((1, 99), (1, 6), [1], None, b"\xAB\xCD")
        self.assertEqual(len(diagnostic.events), 3)
        self.assertEqual(last._attr_extra_state_attributes["source_address"], "1.6")
        receive((1, 6), (1, 99), list(range(100)), None, b"\xAB\xCD")
        bounded = diagnostic.events[-1][1]
        self.assertEqual(bounded["payload_length"], 100)
        self.assertEqual(len(bounded["raw_payload"]), 64)
        self.assertTrue(bounded["payload_truncated"])
        await last.async_will_remove_from_hass()
        await diagnostic.async_will_remove_from_hass()


if __name__ == "__main__":
    unittest.main()
