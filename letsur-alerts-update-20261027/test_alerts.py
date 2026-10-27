import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from alerts import connect, sync, prepare, acknowledge, simulated_success, digest, material


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.db = connect(Path(self.tmp.name) / "state.sqlite")
        self.today = date(2026, 10, 26)
        self.document = json.loads(Path(__file__).with_name("fixtures.json").read_text(encoding="utf-8"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def load(self):
        sync(self.db, self.document, self.today, 5)
        return prepare(self.db, self.today, "test")

    def test_initial_backlog_overdue_discount_and_grouping(self):
        report = self.load()
        self.assertEqual((report["card_count"], report["event_count"]), (2, 3))
        self.assertEqual(report["cards"][0]["days"], -2)
        self.assertEqual(len(report["cards"][1]["items"]), 2)

    def test_d5_boundary_and_no_observation_date_as_deadline(self):
        event = self.document["events"][0]
        for days, expected in ((6, 0), (5, 1), (0, 1), (-1, 1)):
            with self.subTest(days=days):
                event["event_id"] = "boundary-" + str(days)
                event["dates"]["effective_from"] = (self.today + timedelta(days=days)).isoformat()
                sync(self.db, {"events": [copy.deepcopy(event)]}, self.today, 5)
                row = self.db.execute("SELECT urgent FROM events WHERE id=?", (event["event_id"],)).fetchone()
                self.assertEqual(row["urgent"], expected)
        event["event_id"] = "observation-only"
        event["dates"] = {"observed_after_utc": "2026-10-26T00:00:00Z"}
        sync(self.db, {"events": [event]}, self.today, 5)
        self.assertEqual(self.db.execute("SELECT urgent FROM events WHERE id='observation-only'").fetchone()[0], 0)

    def test_preview_does_not_count_as_delivery_and_next_day_reminds(self):
        report = self.load()
        self.assertEqual(prepare(self.db, self.today, "test")["event_count"], 3)
        simulated_success(self.db, report)
        simulated_success(self.db, report)
        self.assertEqual(prepare(self.db, self.today, "test")["event_count"], 0)
        self.assertEqual(prepare(self.db, self.today + timedelta(days=1), "test")["event_count"], 3)

    def test_ack_whole_card_and_material_change_resets_only_changed_member(self):
        report = self.load()
        card = next(c for c in report["cards"] if c["model"] == "가상 모델 A")
        acknowledge(self.db, card, "tester", self.today)
        self.assertEqual(prepare(self.db, self.today, "test")["event_count"], 1)
        event = self.document["events"][0]
        event["revision"] = 2
        event["after"]["amount"] = "3"
        report = self.load()
        ids = [i["event_id"] for c in report["cards"] for i in c["items"]]
        self.assertIn("demo-input", ids)
        self.assertNotIn("demo-output", ids)
        row = self.db.execute("SELECT ack_user,ack_day FROM events WHERE id='demo-output'").fetchone()
        self.assertEqual((row["ack_user"], row["ack_day"]), ("tester", "2026-10-26"))

    def test_stale_ack_is_atomic(self):
        report = self.load()
        card = next(c for c in report["cards"] if c["model"] == "가상 모델 A")
        self.document["events"][1]["after"]["amount"] = "7"
        self.load()
        with self.assertRaises(ValueError):
            acknowledge(self.db, card, "tester", self.today)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE ack_signature IS NOT NULL").fetchone()[0], 0)

    def test_editorial_revision_keeps_ack(self):
        card = self.load()["cards"][0]
        acknowledge(self.db, card, "tester", self.today)
        event = self.document["events"][2]
        event["revision"] = 2
        event["dates"]["note"] = "표기 수정"
        event["period_end"]["as_of"] = "2026-10-27"
        self.assertEqual(self.load()["event_count"], 2)

    def test_lost_date_retains_urgent_and_resets_ack(self):
        self.load()
        self.document["events"][2]["dates"] = {}
        self.document["events"][2]["period_end"] = None
        report = self.load()
        card = next(c for c in report["cards"] if c["model"] == "가상 모델 B")
        self.assertIsNone(card["days"])
        self.assertEqual(report["event_count"], 3)

    def test_missing_event_not_treated_as_cancellation(self):
        self.load()
        sync(self.db, {"events": []}, self.today + timedelta(days=1), 5)
        report = prepare(self.db, self.today + timedelta(days=1), "test")
        self.assertEqual(report["event_count"], 3)
        self.assertTrue(all(i["missing_in_current_input"] for c in report["cards"] for i in c["items"]))

    def test_candidate_initially_excluded_but_status_downgrade_is_not_silent(self):
        self.load()
        self.document["events"][0]["status"] = "candidate"
        report = self.load()
        items = [i for c in report["cards"] for i in c["items"]]
        self.assertNotIn("demo-candidate", [i["event_id"] for i in items])
        self.assertTrue(any(i["event_id"] == "demo-input" and i["status"] == "candidate" for i in items))

    def test_different_route_or_conditions_not_grouped(self):
        self.document["events"][1]["route"] = "다른 경로"
        self.assertEqual(self.load()["card_count"], 3)

    def test_changed_after_delivery_allowed_same_day(self):
        report = self.load()
        simulated_success(self.db, report)
        self.document["events"][0]["after"]["amount"] = "9"
        report = self.load()
        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["cards"][0]["items"][0]["delivery_kind"], "changed")

    def test_duplicate_ids_rejected_before_any_write(self):
        self.document["events"].append(copy.deepcopy(self.document["events"][0]))
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_daily_promotion_without_new_collection(self):
        event = copy.deepcopy(self.document["events"][0])
        event["dates"]["effective_from"] = "2026-11-01"
        sync(self.db, {"events": [event]}, self.today, 5)
        self.assertEqual(prepare(self.db, self.today, "test")["event_count"], 0)
        tomorrow = self.today + timedelta(days=1)
        report = prepare(self.db, tomorrow, "test")
        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["cards"][0]["days"], 5)
        self.assertTrue(report["cards"][0]["items"][0]["missing_in_current_input"])

    def test_custom_threshold_applies_on_database_only_preview(self):
        event = copy.deepcopy(self.document["events"][0])
        event["dates"]["effective_from"] = "2026-11-02"
        sync(self.db, {"events": [event]}, self.today, 5)
        self.assertEqual(prepare(self.db, self.today, "test", urgent_days=7)["event_count"], 1)
        self.assertEqual(prepare(self.db, self.today, "test", urgent_days=1)["event_count"], 1)

    def test_postponement_does_not_silently_unlatch(self):
        self.load()
        self.document["events"][0]["dates"]["effective_from"] = "2027-10-26"
        report = self.load()
        self.assertEqual(report["event_count"], 3)
        row = self.db.execute("SELECT urgent FROM events WHERE id='demo-input'").fetchone()
        self.assertEqual(row["urgent"], 1)

    def test_ack_timestamp_and_audit_survive_future_change(self):
        card = next(c for c in self.load()["cards"] if c["model"] == "가상 모델 B")
        clock = datetime(2026, 10, 26, 12, 30, 25, 123456, timezone(timedelta(hours=9)))
        acknowledge(self.db, card, "tester", self.today, timestamp=clock)
        acknowledge(self.db, card, "other-user", self.today, timestamp=clock + timedelta(hours=1))
        audit = self.db.execute("SELECT * FROM acknowledgements").fetchall()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["user"], "tester")
        self.assertEqual(audit[0]["acknowledged_at_utc"], "2026-10-26T03:30:25.123456+00:00")
        self.document["events"][2]["change"]["discount_pct"] = "25"
        new_card = next(c for c in self.load()["cards"] if c["model"] == "가상 모델 B")
        acknowledge(self.db, new_card, "other-user", self.today, timestamp=clock + timedelta(hours=2))
        audit = self.db.execute("SELECT * FROM acknowledgements ORDER BY id").fetchall()
        self.assertEqual([row["user"] for row in audit], ["tester", "other-user"])
        self.assertNotEqual(audit[0]["signature"], audit[1]["signature"])

    def test_bad_ack_user_and_naive_timestamp_do_not_write(self):
        card = self.load()["cards"][0]
        for user, stamp in ((" ", None), ("tester", datetime(2026, 10, 26))):
            with self.subTest(user=user):
                with self.assertRaises(ValueError):
                    acknowledge(self.db, card, user, self.today, timestamp=stamp)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM acknowledgements").fetchone()[0], 0)

    def test_backdating_rejected_without_state_change(self):
        card = self.load()["cards"][0]
        tomorrow = self.today + timedelta(days=1)
        prepare(self.db, tomorrow, "test")
        for operation in (
            lambda: self.load(),
            lambda: prepare(self.db, self.today, "test"),
            lambda: acknowledge(self.db, card, "tester", self.today),
        ):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM acknowledgements").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT value FROM metadata WHERE key='as_of'").fetchone()[0], tomorrow.isoformat())

    def test_malformed_second_event_rejected_before_first_write(self):
        self.document["events"][1]["conditions"] = []
        with self.assertRaises(ValueError):
            self.load()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertIsNone(self.db.execute("SELECT value FROM metadata WHERE key='as_of'").fetchone())

    def test_invalid_date_is_unknown_and_does_not_use_observed_date(self):
        event = self.document["events"][0]
        event["dates"] = {"effective_from": "2026-02-30", "observed_after_utc": "2026-10-26T00:00:00Z"}
        sync(self.db, {"events": [event]}, self.today, 5)
        self.assertEqual(prepare(self.db, self.today, "test")["event_count"], 0)

    def test_formatting_only_numeric_change_keeps_ack(self):
        card = next(c for c in self.load()["cards"] if c["model"] == "가상 모델 A")
        acknowledge(self.db, card, "tester", self.today)
        event = self.document["events"][0]
        event["before"]["amount"] = "1.000"
        event["after"]["amount"] = "2.0"
        event["change"]["pct"] = "100.00"
        self.assertEqual(self.load()["event_count"], 1)

    def candidate(self):
        event = copy.deepcopy(self.document["events"][4])
        event["sources"] = [{"authority": "channel_official", "role": "notice"}]
        event["gaps"] = [{"field": "raw_check", "reason": "parser_fingerprint_mismatch"}]
        return event

    def test_official_candidate_with_explicit_schedule_warns(self):
        event = self.candidate()
        sync(self.db, {"events": [event]}, self.today, 5)
        report = prepare(self.db, self.today, "test")
        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["cards"][0]["items"][0]["status"], "candidate")

    def test_candidate_ambiguous_unofficial_or_derived_date_excluded(self):
        cases = []
        unofficial = self.candidate()
        unofficial["sources"][0]["authority"] = "unofficial"
        cases.append(unofficial)
        attribution = self.candidate()
        attribution["gaps"].append({"field": "model_attribution", "reason": "ambiguous"})
        cases.append(attribution)
        scope = self.candidate()
        scope["gaps"].append({"field": "scope", "reason": "unknown"})
        cases.append(scope)
        unknown_reason = self.candidate()
        unknown_reason["gaps"][0]["reason"] = "new_unrecognized_code"
        cases.append(unknown_reason)
        derived = self.candidate()
        derived["event_type"] = "limited_discount"
        derived["period_end"] = {"date": "2026-10-27", "basis": "derived_from_publication"}
        cases.append(derived)
        no_route = self.candidate()
        no_route["route"] = ""
        cases.append(no_route)
        for i, event in enumerate(cases):
            event["event_id"] = "excluded-" + str(i)
        sync(self.db, {"events": cases}, self.today, 5)
        self.assertEqual(prepare(self.db, self.today, "test")["event_count"], 0)

    def test_legacy_database_migration_keeps_ack_delivery_and_unknown_time(self):
        path = Path(self.tmp.name) / "legacy.sqlite"
        old = sqlite3.connect(path)
        old.executescript("""CREATE TABLE events (
            id TEXT PRIMARY KEY,payload TEXT NOT NULL,signature TEXT NOT NULL,
            urgent INTEGER NOT NULL DEFAULT 0,ack_signature TEXT,ack_user TEXT,
            ack_day TEXT,seen_day TEXT NOT NULL);
            CREATE TABLE simulated_deliveries (
            channel TEXT NOT NULL,day TEXT NOT NULL,event_id TEXT NOT NULL,
            signature TEXT NOT NULL,PRIMARY KEY(channel,day,event_id,signature));""")
        event = self.document["events"][0]
        old.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)",
                    (event["event_id"], json.dumps(event), "legacy-signature", 1,
                     "legacy-signature", "legacy-user", "2026-10-26", "2026-10-26"))
        old.execute("INSERT INTO simulated_deliveries VALUES(?,?,?,?)",
                    ("test", "2026-10-26", event["event_id"], "legacy-signature"))
        old.commit()
        old.close()
        migrated = connect(path)
        try:
            row = migrated.execute("SELECT * FROM events").fetchone()
            expected = digest(material(event))
            self.assertEqual(row["signature"], expected)
            self.assertEqual(row["ack_signature"], expected)
            audit = migrated.execute("SELECT * FROM acknowledgements").fetchone()
            self.assertEqual(audit["source"], "legacy")
            self.assertIsNone(audit["acknowledged_at_utc"])
            self.assertEqual(migrated.execute("SELECT signature FROM simulated_deliveries").fetchone()[0], expected)
        finally:
            migrated.close()
        reopened = connect(path)
        try:
            self.assertEqual(reopened.execute("SELECT COUNT(*) FROM acknowledgements").fetchone()[0], 1)
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
