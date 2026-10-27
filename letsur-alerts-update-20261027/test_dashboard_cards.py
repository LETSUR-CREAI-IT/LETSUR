import copy
import json
import tempfile
import unittest
from pathlib import Path

from dashboard_cards import CardsError, condition_value, from_source, name, parse

ROOT = Path(__file__).resolve().parent
TEST_TEMP = ROOT / "output"
TEST_TEMP.mkdir(exist_ok=True)


def cards_document():
    return json.loads((ROOT / "fixtures_cards.json").read_text(encoding="utf-8"))


class DashboardCardsTests(unittest.TestCase):
    def test_parse_indexes_each_event_with_its_card_action_and_strings(self):
        judged = parse(cards_document())
        self.assertEqual(judged["base_day"], "2026-10-26")
        self.assertEqual(judged["events"]["demo-input"], {
            "card": "g_0000000000a1", "action": "REVIEW", "model": "가상 모델 A", "route": "가상 공급 경로"})
        self.assertEqual(judged["events"]["demo-output"]["card"], "g_0000000000a1")
        self.assertEqual(judged["events"]["demo-discount"]["action"], "RECORD")
        self.assertEqual(judged["names"]["routes"]["google_direct"], "Google · google_direct")

    def test_files_that_cannot_be_trusted_are_rejected(self):
        def duplicate_card_id(doc):
            doc["cards"][1]["id"] = doc["cards"][0]["id"]

        def event_in_two_cards(doc):
            doc["cards"][1]["events"].append("demo-input")

        def event_twice_in_one_card(doc):
            doc["cards"][0]["events"].append("demo-input")

        cases = {
            "other schema": lambda doc: doc.update(schema=2),
            "boolean schema": lambda doc: doc.update(schema=True),
            "text schema": lambda doc: doc.update(schema="1"),
            "no schema": lambda doc: doc.pop("schema"),
            "cards not a list": lambda doc: doc.update(cards={}),
            "card not an object": lambda doc: doc["cards"].append("g_x"),
            "card id with a space": lambda doc: doc["cards"][0].update(id="g 1"),
            "card id too long": lambda doc: doc["cards"][0].update(id="g" * 65),
            "card id missing": lambda doc: doc["cards"][0].pop("id"),
            "duplicate card id": duplicate_card_id,
            "action missing": lambda doc: doc["cards"][0].pop("action"),
            "events not a list": lambda doc: doc["cards"][0].update(events="demo-input"),
            "event id empty": lambda doc: doc["cards"][0].update(events=[""]),
            "event id not text": lambda doc: doc["cards"][0].update(events=[1]),
            "event in two cards": event_in_two_cards,
            "event twice in one card": event_twice_in_one_card,
            "names not an object": lambda doc: doc.update(names=[]),
            "name table not an object": lambda doc: doc["names"].update(routes=[]),
        }
        with self.assertRaises(CardsError):
            parse([])
        for label, mutate in cases.items():
            with self.subTest(label):
                doc = cards_document()
                mutate(doc)  # in place; what an edit returns does not matter
                with self.assertRaises(CardsError):
                    parse(doc)

    def test_unknown_actions_missing_name_tables_and_blank_strings_are_tolerated(self):
        doc = cards_document()
        doc["cards"][0]["action"] = "SNOOZE"
        doc["cards"][0]["model"] = None
        doc["cards"][0]["route"] = ""
        del doc["names"]["units"]
        doc["names"]["items"]["input"] = ""
        judged = parse(doc)
        self.assertEqual(judged["events"]["demo-input"], {
            "card": "g_0000000000a1", "action": "SNOOZE", "model": None, "route": None})
        self.assertEqual(judged["names"]["units"], {})
        self.assertNotIn("input", judged["names"]["items"])
        del doc["names"]
        self.assertEqual(parse(doc)["names"]["routes"], {})

    def test_names_come_from_the_tables_and_fall_back_to_the_raw_id(self):
        names = parse(cards_document())["names"]
        self.assertEqual(name(names, "routes", "vercel_ai_gateway"), "Vercel · vercel_ai_gateway")
        self.assertEqual(name(names, "items", "cache_write"), "캐시 쓰기")
        self.assertEqual(name(names, "units", "1M tokens"), "100만 토큰")
        self.assertEqual(name(names, "conditions", "input_token_band"), "입력 구간")
        self.assertEqual(name(names, "routes", "no_such_route"), "no_such_route")
        self.assertEqual(name(names, "no_such_table", "input"), "input")
        self.assertIsNone(name(names, "routes", None))
        self.assertEqual(name({}, "routes", "openrouter"), "openrouter")
        self.assertEqual(name(None, "routes", "openrouter"), "openrouter")

    def test_condition_values_use_tier_region_and_status_names(self):
        names = parse(cards_document())["names"]
        self.assertEqual(condition_value(names, "service_tier", "priority"), "Priority")
        self.assertEqual(condition_value(names, "service_tier", "Flex"), "Flex")
        self.assertEqual(condition_value(names, "service_tier", "custom-tier"), "custom-tier")
        self.assertEqual(condition_value(names, "region", "eu"), "EU")
        self.assertEqual(condition_value(names, "region", "ap-south"), "ap-south")
        self.assertEqual(condition_value(names, "endpoint_status", "-5"), "중단")
        self.assertEqual(condition_value(names, "endpoint_status", -5), "중단")
        self.assertEqual(condition_value(names, "endpoint_status", "-7"), "-7")
        self.assertEqual(condition_value(names, "endpoint_status", "0"), "")
        self.assertEqual(condition_value(names, "endpoint_tag", "novita/fp8"), "novita/fp8")
        self.assertEqual(condition_value({}, "service_tier", "priority"), "priority")


class FromSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def write(self, document, filename="cards.json"):
        path = self.folder / filename
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return {"cards_path": str(path), "cards_error": None}

    def test_a_readable_file_gives_the_judgement_and_no_warning(self):
        judged, warning = from_source(self.write(cards_document()))
        self.assertIsNone(warning)
        self.assertEqual(judged["events"]["demo-discount"]["action"], "RECORD")

    def test_every_way_of_missing_the_file_gives_a_warning_that_names_the_reason(self):
        broken = copy.deepcopy(cards_document())
        broken["schema"] = 2
        duplicate = cards_document()
        duplicate["cards"][1]["events"].append("demo-input")
        (self.folder / "garbage.json").write_text("{not json", encoding="utf-8")
        cases = [
            (None, "no_source"),
            ({"cards_path": None, "cards_error": "pages_artifact_expired"}, "pages_artifact_expired"),
            ({}, "cards_missing"),
            ({"cards_path": str(self.folder / "garbage.json"), "cards_error": None}, "unreadable_cards"),
            ({"cards_path": str(self.folder / "absent.json"), "cards_error": None}, "unreadable_cards"),
            (self.write(broken, "schema.json"), "invalid_cards:schema"),
            (self.write(duplicate, "duplicate.json"), "invalid_cards:duplicate_event"),
        ]
        for source, reason in cases:
            with self.subTest(reason):
                judged, warning = from_source(source)
                self.assertIsNone(judged)
                self.assertIn("cards.json", warning)
                self.assertIn("기존 규칙으로 발송", warning)
                self.assertIn(reason, warning)


if __name__ == "__main__":
    unittest.main()
