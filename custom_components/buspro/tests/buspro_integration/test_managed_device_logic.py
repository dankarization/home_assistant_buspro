"""Regression tests for Buspro managed-device option handling."""

import importlib.util
import sys
import unittest
from pathlib import Path


BUSPRO_PATH = Path(__file__).parents[2]

spec = importlib.util.spec_from_file_location(
    "_buspro_managed_logic_test",
    BUSPRO_PATH / "managed" / "logic.py",
)
_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = _module
spec.loader.exec_module(_module)

build_channel_records = _module.build_channel_records
channels_for_device_type = _module.channels_for_device_type
fixed_channel_count = _module.fixed_channel_count
is_channel_configured = _module.is_channel_configured
is_runtime_channel = _module.is_runtime_channel
registry_disabled_update = _module.registry_disabled_update
removed_managed_unique_ids = _module.removed_managed_unique_ids


def _device(address, *unique_ids):
    return {
        "address": address,
        "channels": [{"unique_id": unique_id} for unique_id in unique_ids],
    }


class ManagedDeviceLogicTest(unittest.TestCase):
    def test_cleanup_returns_only_removed_managed_ids(self):
        old = [
            _device("2.5", "relay-1", "relay-2"),
            _device("2.6", "relay-3"),
        ]
        new = [
            _device("2.5", "relay-1"),
            _device("2.6", "relay-3"),
        ]

        removed = removed_managed_unique_ids(old, new)

        self.assertEqual(removed, {"relay-2"})
        self.assertNotIn("buspro-2.100-action", removed)
        self.assertNotIn("buspro-2.9-dimmer-connectivity", removed)

    def test_catalog_model_has_fixed_physical_count(self):
        catalog = {
            "HDL-MR1210.433": {"channels": 12},
            "HDL-MR1610.433": {"channels": 16},
            "Generic relay": {
                "channels": 64,
                "configurable_channels": True,
            },
        }

        self.assertEqual(fixed_channel_count(catalog, "HDL-MR1210.433"), 12)
        self.assertEqual(fixed_channel_count(catalog, "HDL-MR1610.433"), 16)
        self.assertIsNone(fixed_channel_count(catalog, "Generic relay"))

    def test_empty_name_disables_runtime_channel(self):
        self.assertFalse(is_channel_configured(""))
        self.assertFalse(is_channel_configured("   "))
        self.assertTrue(is_channel_configured("Kitchen light"))
        self.assertFalse(is_runtime_channel({"enabled": False}))
        self.assertTrue(is_runtime_channel({"enabled": True}))
        self.assertTrue(is_runtime_channel({}))

    def test_channel_identity_survives_name_edit(self):
        existing = {
            1: {
                "object_id": "existing_entity_id",
                "unique_id": "(2, 5)-1",
            }
        }

        channels = build_channel_records(
            "buspro", "2.5", "relay", [1], {1: "Renamed"}, existing
        )

        self.assertEqual(channels[0]["object_id"], "existing_entity_id")
        self.assertEqual(channels[0]["unique_id"], "(2, 5)-1")
        self.assertTrue(channels[0]["enabled"])

    def test_integration_disabled_state_tracks_empty_name(self):
        self.assertEqual(
            registry_disabled_update(False, None), (True, "integration")
        )
        self.assertEqual(
            registry_disabled_update(True, "integration"), (True, None)
        )
        self.assertEqual(
            registry_disabled_update(True, "user"), (False, "user")
        )

    def test_mixed_output_channels_split_by_platform_and_round_trip(self):
        channel_types = {
            **{channel: "dimmer" for channel in (1, 2)},
            **{channel: "relay" for channel in range(3, 13)},
        }
        names = {
            channel: "" if channel in (2, 10, 12) else f"Output {channel}"
            for channel in range(1, 13)
        }

        channels = build_channel_records(
            "buspro",
            "1.3",
            "mixed_output",
            list(range(1, 13)),
            names,
            channel_types=channel_types,
        )
        device = {"device_type": "mixed_output", "channels": channels}

        self.assertEqual(
            [item["number"] for item in channels_for_device_type(device, "dimmer")],
            [1, 2],
        )
        self.assertEqual(
            [item["number"] for item in channels_for_device_type(device, "relay")],
            list(range(3, 13)),
        )
        self.assertEqual(
            [item["number"] for item in channels if not is_runtime_channel(item)],
            [2, 10, 12],
        )
        self.assertEqual(channels[0]["unique_id"], "buspro-1.3-dimmer-1")
        self.assertEqual(channels[2]["unique_id"], "buspro-1.3-relay-3")

        existing = {item["number"]: item for item in channels}
        renamed = dict(names)
        renamed[1] = "Renamed output"
        rebuilt = build_channel_records(
            "buspro",
            "1.3",
            "mixed_output",
            list(range(1, 13)),
            renamed,
            existing,
            channel_types,
        )

        self.assertEqual(len(rebuilt), 12)
        self.assertEqual(
            [(item["object_id"], item["unique_id"]) for item in rebuilt],
            [(item["object_id"], item["unique_id"]) for item in channels],
        )
        self.assertEqual(
            [item["device_type"] for item in rebuilt],
            ["dimmer", "dimmer"] + ["relay"] * 10,
        )
        self.assertFalse(rebuilt[1]["enabled"])
        self.assertFalse(rebuilt[9]["enabled"])
        self.assertFalse(rebuilt[11]["enabled"])

    def test_platform_routing_preserves_legacy_parent_device_types(self):
        channels = [{"number": 1}, {"number": 2}]
        device = {"device_type": "relay", "channels": channels}

        self.assertEqual(channels_for_device_type(device, "relay"), channels)
        self.assertEqual(channels_for_device_type(device, "dimmer"), [])


if __name__ == "__main__":
    unittest.main()
