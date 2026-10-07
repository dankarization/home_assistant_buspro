"""Tests for explicit panel press classification; no bus packets are sent."""

import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BUSPRO_PATH = Path(__file__).parents[2]
sys.path.insert(0, str(BUSPRO_PATH))
from panel_press import load_press_mappings, parse_press_mappings  # noqa: E402


CATALOG = {
    "HDL-MP8B.46-A": {"button_count": 8},
    "HDL-MPL8.46-A": {"button_count": 8, "page_count": 4},
}
SITE_MAP = BUSPRO_PATH / "panel_press_mappings.json"


def telegram(code, target, payload):
    return SimpleNamespace(
        operate_code=SimpleNamespace(value=bytes.fromhex(code)),
        target_address=target,
        payload=payload,
    )


class PanelPressMapTest(unittest.TestCase):
    def test_site_map_preserves_old_panels_and_adds_configured_pages(self):
        maps = load_press_mappings(SITE_MAP, CATALOG)
        self.assertEqual(
            set(maps), {"1.4", "1.5", "1.6", "1.7", "1.8", "1.10", "1.11", "1.12"}
        )
        self.assertEqual(sum(len(item.commands) for item in maps.values()), 57)
        self.assertEqual(maps["1.5"].commands, {})
        self.assertEqual(maps["1.6"].event_types_for_button(1, 5),
                         ("single_press", "long_press"))
        self.assertEqual(maps["1.6"].event_types_for_button(1, 1), ())
        self.assertEqual(len(maps["1.12"].buttons()), 24)
        self.assertEqual(len(maps["1.12"].button_names), 24)
        for button in range(1, 9):
            self.assertEqual(maps["1.12"].event_types_for_button(4, button), ())
        for page, button, name in (
            (1, 8, "Master"),
            (2, 1, "HA 2.1"),
            (2, 2, "HA 2.2"),
            (2, 3, "HA 2.3"),
            (2, 4, "HA 2.4"),
        ):
            self.assertEqual(maps["1.12"].event_types_for_button(page, button),
                             ("single_press",))
            self.assertEqual(maps["1.12"].name_for_button(page, button), name)
        self.assertEqual(maps["1.12"].name_for_button(2, 5), "Porch UP")
        self.assertNotIn("double_press", {
            press for panel in maps.values() for _, _, press in panel.commands.values()
        })

    def test_configured_short_and_long_targets_are_distinct(self):
        maps = load_press_mappings(SITE_MAP, CATALOG)
        self.assertEqual(
            maps["1.6"].match(telegram("E01C", (1, 99), [20, 255, 0, 0, 5])),
            (1, 5, "single_press"),
        )
        self.assertEqual(
            maps["1.6"].match(telegram("E01C", (1, 99), [18, 255, 0, 0, 5])),
            (1, 5, "long_press"),
        )
        self.assertEqual(
            maps["1.11"].match(telegram("E01C", (1, 99), [23, 255])),
            (1, 8, "long_press"),
        )
        self.assertEqual(
            maps["1.4"].match(telegram("E01C", (1, 99), [13, 255, 0, 0, 8])),
            (1, 8, "single_press"),
        )

    def test_new_panel_pages_and_key_commands_are_distinct(self):
        panel = load_press_mappings(SITE_MAP, CATALOG)["1.12"]
        expected = {}
        for button, channel in enumerate((4, 12, 3, 7, 6, 5, 11), 1):
            for level in (100, 0):
                expected[("0031", (1, 3), (channel, level, 0, 0))] = (1, button, "single_press")
        for page, switches in (
            (2, ((20, 18), (21, 19), (16, 14), (17, 15))),
            (3, ((28, 26), (29, 27), (24, 22), (25, 23))),
        ):
            for button, (short, long) in enumerate(switches, 5):
                expected[("E01C", (1, 99), (short, 255))] = (page, button, "single_press")
                expected[("E01C", (1, 99), (long, 255))] = (page, button, "long_press")
        for button, level in enumerate((10, 11, 12, 13), 1):
            expected[("0031", (1, 3), (1, level, 0, 0))] = (3, button, "single_press")
        for (page, button), level in zip(
            ((1, 8), (2, 1), (2, 2), (2, 3), (2, 4)), range(14, 19)
        ):
            expected[("0031", (1, 3), (1, level, 0, 0))] = (page, button, "single_press")
        self.assertEqual(panel.commands, expected)
        cases = (
            ("0031", (1, 3), [4, 100, 0, 0, 1], (1, 1, "single_press")),
            ("0031", (1, 3), [4, 0, 0, 0, 1], (1, 1, "single_press")),
            ("E01C", (1, 99), [21, 255, 0, 0, 6], (2, 6, "single_press")),
            ("E01C", (1, 99), [19, 255, 0, 0, 6], (2, 6, "long_press")),
            ("0031", (1, 3), [1, 12, 0, 0, 3], (3, 3, "single_press")),
            ("E01C", (1, 99), [23, 255, 0, 0, 8], (3, 8, "long_press")),
            ("0031", (1, 3), [1, 14, 0, 0, 2], (1, 8, "single_press")),
            ("0031", (1, 3), [1, 15, 0, 0, 1], (2, 1, "single_press")),
            ("0031", (1, 3), [1, 16, 0, 0, 2], (2, 2, "single_press")),
            ("0031", (1, 3), [1, 17, 0, 0, 3], (2, 3, "single_press")),
            ("0031", (1, 3), [1, 18, 0, 0, 4], (2, 4, "single_press")),
        )
        for code, target, payload, expected in cases:
            with self.subTest(expected=expected, payload=payload):
                self.assertEqual(panel.match(telegram(code, target, payload)), expected)

    def test_unverified_telegrams_do_not_create_press_events(self):
        panel = load_press_mappings(SITE_MAP, CATALOG)["1.6"]
        self.assertIsNone(panel.match(telegram("E01D", (1, 99), [20, 1])))
        self.assertIsNone(panel.match(telegram("E01C", (1, 99), [20, 1])))
        self.assertIsNone(panel.match(telegram("E01C", (1, 98), [20, 255])))
        self.assertIsNone(panel.match(telegram("E01C", (1, 99), [20, 255, 0])))
        self.assertIsNone(panel.match(telegram("E01C", (1, 99), [[20], 255])))
        self.assertIsNone(panel.match(telegram("E01C", (1, 99), [20, 255, 1, 0, 5])))
        self.assertIsNone(panel.match(telegram("E01C", (1, 99), [20, 255, 0, 0, 5, 1])))
        new_panel = load_press_mappings(SITE_MAP, CATALOG)["1.12"]
        for last_byte in range(1, 9):
            # All five Single ON/OFF keys share this OFF payload.
            self.assertIsNone(new_panel.match(
                telegram("0031", (1, 3), [1, 0, 0, 0, last_byte])
            ))
        self.assertIsNone(new_panel.match(telegram("0031", (1, 3), [1, 19, 0, 0, 1])))
        self.assertIsNone(new_panel.match(telegram("0031", (1, 4), [1, 14, 0, 0, 8])))
        self.assertIsNone(new_panel.match(telegram("0031", (1, 3), [1, 11, 0, 1, 2])))
        self.assertIsNone(new_panel.match(telegram("E01C", (1, 99), [20, 0, 0, 0, 5])))
        # The last byte is not treated as a page/key marker without evidence.
        self.assertEqual(panel.match(telegram("E01C", (1, 99), [20, 255, 0, 0, 6])),
                         (1, 5, "single_press"))

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

        legacy = copy.deepcopy(valid)
        legacy["panels"]["1.6"]["actions"][0].update(
            operate_code="0002", payload=[1, 2]
        )
        self.assertEqual(
            parse_press_mappings(legacy, CATALOG)["1.6"].match(
                telegram("0002", (1, 99), [1, 2])
            ),
            (1, 5, "single_press"),
        )
        legacy["panels"]["1.6"]["actions"][0].update(
            operate_code="0031", payload=[1, 100, 0, 3]
        )
        self.assertEqual(
            parse_press_mappings(legacy, CATALOG)["1.6"].match(
                telegram("0031", (1, 99), [1, 100, 0, 3, 7])
            ),
            (1, 5, "single_press"),
        )

        for changed in (
            {"button": 9}, {"press": "double"}, {"target_address": "1.999"},
            {"operate_code": "E01D"}, {"payload": [20, 1, 0]},
            {"page": 2}, {"payload": [20, 255, 0, 1, 5]},
        ):
            invalid = copy.deepcopy(valid)
            invalid["panels"]["1.6"]["actions"][0].update(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                parse_press_mappings(invalid, CATALOG)

        invalid_name = copy.deepcopy(valid)
        invalid_name["panels"]["1.6"]["button_names"] = {"4.1": "Invalid"}
        with self.assertRaises(ValueError):
            parse_press_mappings(invalid_name, CATALOG)


if __name__ == "__main__":
    unittest.main()
