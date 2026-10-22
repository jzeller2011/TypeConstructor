# -*- coding: utf-8 -*-
"""Run outside Revit:  python tests/test_schema.py"""
from __future__ import print_function
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))

from typeconstructor.schema import TypeSchema, NONE, validate_new_code, suggest_param  # noqa: E402
from typeconstructor import project  # noqa: E402

DOORS = TypeSchema.load(os.path.join(HERE, "..", "config", "doors.json"))


def rec(name, **params):
    """Fake a Revit type: width/height in feet like the API returns them."""
    raw = {}
    for k, v in params.items():
        raw[k] = v
    return DOORS.build_record(name, "Door-Single", name, raw)


class NameTests(unittest.TestCase):
    def test_parse_example(self):
        v = DOORS.parse_name("Swing Single-WD-36x84-CAP-HM-90")
        self.assertEqual(v["operation"], "Swing Single")
        self.assertEqual(v["panel"], "WD")
        self.assertEqual(v["width"], "36")
        self.assertEqual(v["height"], "84")
        self.assertEqual(v["frame_style"], "CAP")
        self.assertEqual(v["frame_material"], "HM")
        self.assertEqual(v["rating"], "90")

    def test_loose_separator_and_case(self):
        v = DOORS.parse_name("Swing Single-wd-36X84-CAP -HM-90")
        self.assertIsNotNone(v)
        self.assertEqual(v["panel"], "WD")
        self.assertEqual(v["frame_material"], "HM")

    def test_optional_rating(self):
        v = DOORS.parse_name("Swing Double-HM-72x84-KD-HM")
        self.assertEqual(v["rating"], "")

    def test_nonconforming(self):
        self.assertIsNone(DOORS.parse_name("Single-Flush 36\" x 84\""))
        self.assertIsNone(DOORS.parse_name("Swing Single-XX-36x84-CAP-HM"))

    def test_compose(self):
        s = {"operation": "swing single", "panel": "wd", "width": "36.0",
             "height": "84", "frame_style": "cap", "frame_material": "hm", "rating": "90"}
        self.assertEqual(DOORS.compose(s), "Swing Single-WD-36x84-CAP-HM-90")
        s["rating"] = NONE
        self.assertEqual(DOORS.compose(s), "Swing Single-WD-36x84-CAP-HM")

    def test_roundtrip(self):
        n = "Sliding-GL-35.5x96-WRAP-AL"
        self.assertEqual(DOORS.compose(DOORS.parse_name(n)), n)


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.recs = [
            rec("Swing Single-WD-36x84-CAP-HM-90", width="36", height="84", rating="90"),
            rec("Swing Single-WD-36x84-CAP-HM", width="36", height="84"),
            rec("Swing Single-WD-30x84-CAP-HM", width="30", height="84"),
            rec("Swing Single-HM-36x84-KD-HM-60", width="36", height="84", rating="60"),
            rec("Swing Double-WD-72x84-CAP-HM-90", width="72", height="84", rating="90"),
            rec("Old door 36", width="36", height="84"),
            # name says 36 wide but the parameter says 34
            rec("Swing Single-WD-36x80-CAP-HM", width="34", height="80"),
        ]
        DOORS.mark_duplicates(self.recs)

    def test_param_wins_and_mismatch_flagged(self):
        r = self.recs[-1]
        self.assertEqual(r.values["width"], "34")
        self.assertIn("Width (in)", r.mismatches)

    def test_progressive_filter(self):
        s = {"operation": "Swing Single"}
        self.assertEqual(len(DOORS.filter(self.recs, s)), 5)
        s["panel"] = "WD"
        self.assertEqual(len(DOORS.filter(self.recs, s)), 4)
        s["width"] = "36"
        self.assertEqual(len(DOORS.filter(self.recs, s)), 2)
        s["rating"] = NONE
        self.assertEqual([r.name for r in DOORS.filter(self.recs, s)],
                         ["Swing Single-WD-36x84-CAP-HM"])

    def test_exact_match_blocks_creation(self):
        s = {"operation": "Swing Single", "panel": "WD", "width": "36", "height": "84",
             "frame_style": "CAP", "frame_material": "HM", "rating": "90"}
        res = DOORS.evaluate(self.recs, s)
        self.assertTrue(res["complete"])
        self.assertFalse(res["ready"])
        self.assertEqual(res["exact"][0].name, "Swing Single-WD-36x84-CAP-HM-90")

    def test_blank_optional_is_none_for_exact(self):
        s = {"operation": "Swing Single", "panel": "WD", "width": "36", "height": "84",
             "frame_style": "CAP", "frame_material": "HM"}
        res = DOORS.evaluate(self.recs, s)
        self.assertEqual([r.name for r in res["exact"]], ["Swing Single-WD-36x84-CAP-HM"])

    def test_new_type_ready(self):
        s = {"operation": "Swing Single", "panel": "WD", "width": "42", "height": "84",
             "frame_style": "CAP", "frame_material": "HM", "rating": "20"}
        res = DOORS.evaluate(self.recs, s)
        self.assertTrue(res["ready"], res)
        self.assertEqual(res["name"], "Swing Single-WD-42x84-CAP-HM-20")

    def test_validation(self):
        res = DOORS.evaluate(self.recs, {"operation": "Swing Single", "panel": "XX", "width": "abc"})
        errs = " | ".join(res["errors"])
        self.assertIn("Panel 'XX' isn't a known value", errs)
        self.assertIn("Width (in) must be a positive number", errs)
        self.assertIn("Height (in) is required", errs)
        self.assertFalse(res["ready"])

    def test_facets(self):
        opts = dict((c, n) for c, n, _ in DOORS.options_for(
            "width", self.recs, {"operation": "Swing Single", "panel": "WD"}))
        self.assertEqual(opts, {"30": 1, "34": 1, "36": 2})

    def test_choose_source_respects_operation(self):
        s = {"operation": "Swing Double", "panel": "WD", "width": "60"}
        self.assertEqual(DOORS.choose_source(self.recs, s).name, "Swing Double-WD-72x84-CAP-HM-90")
        self.assertIsNone(DOORS.choose_source(self.recs, {"operation": "Pocket"}))

    def test_name_taken_with_different_params(self):
        # name collides with a type whose params say otherwise
        s = {"operation": "Swing Single", "panel": "WD", "width": "36", "height": "80",
             "frame_style": "CAP", "frame_material": "HM"}
        res = DOORS.evaluate(self.recs, s)
        self.assertEqual(res["name_taken"].name, "Swing Single-WD-36x80-CAP-HM")
        self.assertFalse(res["ready"])

    def test_example_walls_config_loads(self):
        p = os.path.join(HERE, "..", "config", "_example_basic_walls.json")
        w = TypeSchema.load(p)
        self.assertEqual(w.parse_name("INT-MS358-4.875-1")["thickness"], "4.875")


class ProjectTests(unittest.TestCase):
    """Project store: new values, descriptions, parameter mapping, extras."""

    def setUp(self):
        self.base = DOORS.raw

    def schema_with(self, store, discovered=None):
        return TypeSchema(project.effective_dict(
            self.base, project.section(store, "Doors"), discovered))

    def test_found_values_parse_and_validate(self):
        plain = DOORS
        self.assertIsNone(plain.parse_name("Swing Single-WWD3-36x84-CAP-HM"))
        s = self.schema_with(project.empty_store(), {"panel": {"WWD3"}})
        self.assertEqual(s.parse_name("Swing Single-WWD3-36x84-CAP-HM")["panel"], "WWD3")
        self.assertIsNone(s.by_key["panel"].validate("WWD3"))
        self.assertEqual(s.by_key["panel"].find_option("wwd3").origin, "found")

    def test_new_project_value_with_description(self):
        store = project.empty_store()
        project.set_value(store, "Doors", "panel", "WWD10", "3-panel Wood Door")
        s = self.schema_with(store)
        opt = s.by_key["panel"].find_option("WWD10")
        self.assertEqual((opt.origin, opt.desc), ("project", "3-panel Wood Door"))
        self.assertEqual(s.compose({"operation": "Swing Single", "panel": "wwd10", "width": "36",
                                    "height": "84", "frame_style": "CAP", "frame_material": "HM"}),
                         "Swing Single-WWD10-36x84-CAP-HM")

    def test_description_on_found_value_and_firm_override(self):
        store = project.empty_store()
        project.set_value(store, "Doors", "panel", "WWD3", "WD door, special project panel")
        project.set_value(store, "Doors", "panel", "WD", "Wood, stain grade")
        s = self.schema_with(store, {"panel": {"WWD3", "WD"}})
        f = s.by_key["panel"]
        self.assertEqual(f.describe("WWD3"), "WD door, special project panel")
        self.assertEqual(f.describe("WD"), "Wood, stain grade")
        self.assertEqual(f.find_option("WD").firm_desc, "Wood")
        self.assertEqual([o.code for o in f.values].count("WWD3"), 1)

    def test_set_value_replaces_other_case(self):
        store = project.empty_store()
        project.set_value(store, "Doors", "panel", "wwd10", "a")
        project.set_value(store, "Doors", "panel", "WWD10", "b")
        self.assertEqual(store["schemas"]["Doors"]["values"]["panel"], {"WWD10": {"desc": "b"}})

    def test_param_mapping_overrides(self):
        store = project.empty_store()
        project.set_mapping(store, "Doors", {"panel": "PANEL TYPE", "operation": ""}, [])
        s = self.schema_with(store)
        self.assertEqual(s.by_key["panel"].params, ["PANEL TYPE"])
        self.assertEqual(s.by_key["operation"].params, [])
        self.assertEqual(s.by_key["width"].builtins, ["DOOR_WIDTH", "FAMILY_WIDTH_PARAM"])

    def test_extra_field_filters_but_not_identity(self):
        store = project.empty_store()
        project.set_mapping(store, "Doors", {}, [{"param": "Hardware Set"}])
        s = self.schema_with(store)
        hw = s.by_key["x_hardware_set"]
        self.assertTrue(hw.extra)
        self.assertFalse(hw.required)
        a = s.build_record("Swing Single-WD-36x84-CAP-HM", "F", 1,
                           {"width": "36", "height": "84", "x_hardware_set": "HW-1"})
        b = s.build_record("Swing Single-WD-30x84-CAP-HM", "F", 2,
                           {"width": "30", "height": "84", "x_hardware_set": "HW-2"})
        self.assertEqual(len(s.filter([a, b], {"x_hardware_set": "HW-2"})), 1)
        staged = {"operation": "Swing Single", "panel": "WD", "width": "36", "height": "84",
                  "frame_style": "CAP", "frame_material": "HM", "x_hardware_set": "HW-9"}
        res = s.evaluate([a, b], staged)
        self.assertEqual([r.name for r in res["exact"]], [a.name])   # hardware doesn't make it new
        self.assertEqual(s.compose(staged), "Swing Single-WD-36x84-CAP-HM")

    def test_validate_new_code(self):
        self.assertIsNone(validate_new_code(DOORS, "panel", "WWD10"))
        self.assertIn("separates parts", validate_new_code(DOORS, "panel", "WD-3"))
        self.assertIsNotNone(validate_new_code(DOORS, "panel", ""))
        self.assertIsNotNone(validate_new_code(DOORS, "width", "wide"))

    def test_suggest_param(self):
        names = ["PANEL TYPE", "Panel Width", "Width", "Height", "FIRE RATING", "Frame Type"]
        self.assertIsNone(suggest_param(DOORS.by_key["panel"], names))       # 2 start with "panel"
        self.assertEqual(suggest_param(DOORS.by_key["width"], names), "Width")
        self.assertEqual(suggest_param(DOORS.by_key["rating"], names), "FIRE RATING")
        self.assertEqual(suggest_param(DOORS.by_key["frame_style"], names), "Frame Type")
        self.assertEqual(suggest_param(DOORS.by_key["panel"], ["PANEL TYPE", "Width"]), "PANEL TYPE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
