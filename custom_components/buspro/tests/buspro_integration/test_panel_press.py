"""Tests for explicit panel press classification; no bus packets are sent."""

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BUSPRO_PATH = Path(__file__).parents[2]
sys.path.insert(0, str(BUSPRO_PATH))
from panel_press import load_press_mappings, parse_press_mappings  # noqa: E402


CATALOG = {"HDL-MP8B.46-A": {"button_count": 8}}
SITE_MAP = BUSPRO_PATH / "panel_press_mappings.json"


def telegram(code, target, payload):
    return SimpleNamespace(
        operate_code=SimpleNamespace(value=bytes.fromhex(code)),
        target_address=target,
        payload=payload,
    )


class PanelPressMapTest(unittest.TestCase):
    def test_site_map_contains_exactly_seven_known_panels(self):
        maps = load_press_mappings(SITE_MAP, CATALOG)
        self.assertEqual(
            set(maps), {"1.4", "1.5", "1.6", "1.7", "1.8", "1.10", "1.11"}
        )
        self.assertEqual(sum(len(item.commands) for item in maps.values()), 18)
        self.assertEqual(maps["1.5"].commands, {})
        self.assertEqual(maps["1.6"].event_types_for_button(5),
                         ("single_press", "long_press"))
        self.assertEqual(maps["1.6"].event_types_for_button(1), ())
        self.assertNotIn("double_press", {
            press for panel in maps.values() for _, press in panel.commands.values()
        })

    def test_configured_short_and_long_targets_are_distinct(self):
        maps = load_press_mappings(SITE_MAP, CATALOG)
        self.assertEqual(
            maps["1.6"].match(telegram("E01C", (1, 99), [20, 255])),
            (5, "single_press"),
        )
        self.assertEqual(
            maps["1.6"].match(telegram("E01C", (1, 99), [18, 255])),
            (5, "long_press"),
        )
        self.assertEqual(
            maps["1.11"].match(telegram("E01C", (1, 99), [23, 255])),
            (8, "long_press"),
        )
        self.assertEqual(
            maps["1.4"].match(telegram("E01C", (1, 99), [13, 255])),
            (8, "single_press"),
        )

    def test_unverified_telegrams_do_not_create_press_events(self):
        panel = load_press_mappings(SITE_MAP, CATALOG)["1.6"]
        self.assertIsNone(panel.match(telegram("E01D", (1, 99), [20, 1])))
        self.assertIsNone(panel.match(telegram("E01C", (1, 99), [20, 1])))
        self.assertIsNone(panel.match(telegram("E01C", (1, 98), [20, 255])))
        self.assertIsNone(panel.match(telegram("E01C", (1, 99), [20, 255, 0])))
        self.assertIsNone(panel.match(telegram("E01C", (1, 99), [[20], 255])))

    def test_conflicting_or_invalid_mappings_fail_closed(self):
        valid = {
            "panels": {
                "1.6": {
                    "model": "HDL-MP8B.46-A",
                    "actions": [
                        {"button": 5, "press": "single_press",
                         "operate_code": "E01C", "target_address": "1.99",
                         "payload": [20, 255]}
                    ],
                }
            }
        }
        self.assertEqual(len(parse_press_mappings(valid, CATALOG)["1.6"].commands), 1)
        duplicate = copy.deepcopy(valid)
        duplicate["panels"]["1.6"]["actions"].append(
            {**duplicate["panels"]["1.6"]["actions"][0], "press": "long_press"}
        )
        with self.assertRaises(ValueError):
            parse_press_mappings(duplicate, CATALOG)

        for changed in (
            {"button": 9}, {"press": "double"}, {"target_address": "1.999"},
            {"operate_code": "E01D"}, {"payload": [20, 1, 0]},
        ):
            invalid = copy.deepcopy(valid)
            invalid["panels"]["1.6"]["actions"][0].update(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                parse_press_mappings(invalid, CATALOG)


if __name__ == "__main__":
    unittest.main()
