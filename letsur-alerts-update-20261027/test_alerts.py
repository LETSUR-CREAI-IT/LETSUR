import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from alerts import connect, sync, prepare, acknowledge, render, simulated_success, digest, material, health_events, small_decrease, \
    event_eligible, observed
from dashboard_cards import parse


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

    def cards_document(self):
        return json.loads(Path(__file__).with_name("fixtures_cards.json").read_text(encoding="utf-8"))

    def judged_ids(self, document=None, today=None):
        report = prepare(self.db, today or self.today, "test", judged=parse(document or self.cards_document()))
        return sorted(i["event_id"] for c in report["cards"] for i in c["items"])

    def test_record_cards_are_not_alerts(self):
        sync(self.db, self.document, self.today, 5)
        self.assertEqual(prepare(self.db, self.today, "test")["event_count"], 3)
        # fixtures_cards.json: the card of both prices is REVIEW and APPLIES, the card of the discount is RECORD
        self.assertEqual(self.judged_ids(), ["demo-input", "demo-output"])

    def test_not_applies_cards_are_not_alerts(self):
        # 2026-10-28 가안: a change on a route LETSUR does not use is left to the dashboard (decision 26-4 still sent it).
        sync(self.db, self.document, self.today, 5)
        document = self.cards_document()
        document["cards"][0]["applicability"] = "NOT APPLIES"
        self.assertEqual(self.judged_ids(document), [])

    def test_only_the_record_action_and_the_not_applies_badge_are_excluded(self):
        sync(self.db, self.document, self.today, 5)
        for action, badge, expected in (("REVIEW", "NOT APPLIES", 0), ("VERIFY", "UNKNOWN", 3),
                                        ("MONITOR", "APPLIES", 3), ("RECORD", "APPLIES", 0)):
            with self.subTest(action=action, badge=badge):
                document = self.cards_document()
                for card in document["cards"]:
                    card.update(action=action, applicability=badge)
                self.assertEqual(len(self.judged_ids(document)), expected)

    def test_record_is_decided_per_event_inside_a_bot_card(self):
        sync(self.db, self.document, self.today, 5)
        document = self.cards_document()
        document["cards"][0]["events"] = ["demo-input"]
        document["cards"].append(dict(document["cards"][0], id="g_0000000000a2", events=["demo-output"], action="RECORD"))
        self.assertEqual(self.judged_ids(document), ["demo-input"])

    def test_events_without_a_dashboard_card_follow_the_bot_rules(self):
        sync(self.db, self.document, self.today, 5)
        document = self.cards_document()
        document["cards"].pop(1)
        self.assertEqual(self.judged_ids(document), ["demo-discount", "demo-input", "demo-output"])

    def test_overdue_alert_is_sent_through_day_7_and_stops_on_day_8(self):
        events = []
        for days in (6, 7, 8, 30):
            event = copy.deepcopy(self.document["events"][0])
            event["event_id"] = "overdue-" + str(days)
            event["dates"]["effective_from"] = (self.today - timedelta(days=days)).isoformat()
            events.append(event)
        sync(self.db, {"events": events}, self.today, 5)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE urgent=1").fetchone()[0], 4)
        for judged in (None, parse(self.cards_document())):
            with self.subTest(judged=judged is not None):
                report = prepare(self.db, self.today, "test", judged=judged)
                self.assertEqual(sorted(i["event_id"] for c in report["cards"] for i in c["items"]),
                                 ["overdue-6", "overdue-7"])

    def test_latched_alert_stops_on_the_eighth_day_after_the_deadline(self):
        sync(self.db, {"events": [self.document["events"][0]]}, self.today, 5)
        deadline = date(2026, 10, 29)
        for days, expected in ((-3, 1), (0, 1), (6, 1), (7, 1), (8, 0), (9, 0), (40, 0)):
            with self.subTest(days_after_deadline=days):
                self.assertEqual(prepare(self.db, deadline + timedelta(days=days), "test")["event_count"], expected)

    def test_overdue_limit_does_not_touch_events_without_a_date(self):
        self.load()
        self.document["events"][2]["dates"] = {}
        self.document["events"][2]["period_end"] = None
        self.load()
        report = prepare(self.db, self.today + timedelta(days=30), "test")
        self.assertEqual([c["days"] for c in report["cards"]], [None])

    def test_report_carries_dashboard_strings_names_and_card_ids(self):
        events = []
        for event_id, model, route in (("mapped", "gemini-3.8-flash", "google_direct"),
                                       ("labelled", "gpt-x", "vercel_ai_gateway"),
                                       ("raw", "model-3", "no_such_route")):
            event = copy.deepcopy(self.document["events"][0])
            event.update(event_id=event_id, model_id=model, route=route)
            events.append(event)
        sync(self.db, {"events": events}, self.today, 5)
        document = self.cards_document()
        document["cards"] = [dict(document["cards"][0], id="g_0000000000d1", events=["mapped"],
                                  model="Gemini 3.8 Flash", route="구글 직접")]
        report = prepare(self.db, self.today, "test", judged=parse(document))
        cards = {c["model"]: c for c in report["cards"]}
        self.assertEqual(sorted(cards), ["Gemini 3.8 Flash", "gpt-x", "model-3"])
        self.assertEqual(cards["Gemini 3.8 Flash"]["route"], "구글 직접")
        self.assertEqual(cards["Gemini 3.8 Flash"]["items"][0]["dashboard_card"], "g_0000000000d1")
        self.assertEqual(cards["gpt-x"]["route"], "Vercel · vercel_ai_gateway")
        self.assertEqual(cards["model-3"]["route"], "no_such_route")
        self.assertIsNone(cards["gpt-x"]["items"][0]["dashboard_card"])
        self.assertEqual(report["names"]["routes"]["google_direct"], "Google · google_direct")
        plain = prepare(self.db, self.today, "test")
        self.assertEqual({c["card_id"] for c in plain["cards"]}, {c["card_id"] for c in report["cards"]})
        self.assertEqual(plain["names"], {})
        self.assertEqual({c["route"] for c in plain["cards"]}, {"google_direct", "vercel_ai_gateway", "no_such_route"})

    def test_text_preview_uses_the_dashboard_item_and_unit_names(self):
        sync(self.db, self.document, self.today, 5)
        text = render(prepare(self.db, self.today, "test", judged=parse(self.cards_document())))
        self.assertIn("입력 토큰: 1 → 2 USD / 100만 토큰", text)
        self.assertIn("출력 토큰: 4 → 6 USD / 100만 토큰", text)
        self.assertNotIn("1M tokens", text)
        self.assertIn("input: 1 → 2 USD / 1M tokens", render(prepare(self.db, self.today, "test")))

    def new_change_document(self, **changes):
        """demo-no-date, a confirmed price change with no deadline, first seen the day before self.today (KST)."""
        event = copy.deepcopy(self.document["events"][3])
        event["dates"] = {"observed_after_utc": "2026-10-25T05:00:00Z"}
        event.update(changes)
        return {"events": [event]}

    def test_a_new_cost_change_on_a_route_letsur_may_use_goes_out_once(self):
        # 2026-10-28 가안: a confirmed price change has no deadline and never becomes urgent, so it goes out once instead.
        # fixtures_cards.json puts demo-no-date in an UNKNOWN card.
        sync(self.db, self.new_change_document(), self.today, 5)
        judged = parse(self.cards_document())
        report = prepare(self.db, self.today, "test", judged=judged)
        self.assertEqual([(c["alert"], [i["event_id"] for i in c["items"]]) for c in report["cards"]],
                         [("new", ["demo-no-date"])])
        self.assertIn("새로 확인된 변화 · 한 번만 알림", render(report))
        simulated_success(self.db, report)
        self.assertEqual(prepare(self.db, self.today + timedelta(days=1), "test", judged=judged)["cards"], [])

    def test_a_new_change_needs_the_dashboard_a_letsur_route_and_a_recent_confirmed_observation(self):
        cases = {
            "no cards.json": (self.new_change_document(), None),
            "NOT APPLIES": (self.new_change_document(), "NOT APPLIES"),
            "seen 8 days ago": (self.new_change_document(dates={"observed_after_utc": "2026-10-18T05:00:00Z"}), "APPLIES"),
            "not confirmed": (self.new_change_document(status="candidate"), "APPLIES"),
        }
        for number, (label, (document, badge)) in enumerate(cases.items()):
            with self.subTest(label):
                db = connect(Path(self.tmp.name) / f"case{number}.sqlite")
                try:
                    sync(db, document, self.today, 5)
                    judged = None
                    if badge:
                        cards = self.cards_document()
                        cards["cards"][2]["applicability"] = badge
                        judged = parse(cards)
                    self.assertEqual(prepare(db, self.today, "test", judged=judged)["cards"], [])
                finally:
                    db.close()

    def test_a_stop_of_selling_goes_out_when_first_seen_and_again_from_five_days_before(self):
        event = copy.deepcopy(self.document["events"][0])  # demo-input, in the APPLIES card of fixtures_cards.json
        event.update(event_type="model_end", dates={"effective_from": "2026-11-20", "published": "2026-10-26"})
        sync(self.db, {"events": [event]}, self.today, 5)
        judged = parse(self.cards_document())
        first = prepare(self.db, self.today, "test", judged=judged)
        self.assertEqual([(c["alert"], c["days"]) for c in first["cards"]], [("new", 25)])
        simulated_success(self.db, first)
        self.assertEqual(prepare(self.db, date(2026, 10, 27), "test", judged=judged)["cards"], [])
        later = prepare(self.db, date(2026, 11, 15), "test", judged=judged)
        self.assertEqual([(c["alert"], c["days"]) for c in later["cards"]], [("urgent", 5)])

    def test_a_retirement_schedules_earliest_date_goes_out_and_says_it_is_the_earliest(self):
        # 2026-10-29: a date of the Gemini deprecations table is a confirmed model_end on Google's route whose date is the
        # earliest possible one; an exact retirement date gets no such line.
        event = copy.deepcopy(self.document["events"][0])  # demo-input, in the APPLIES card of fixtures_cards.json
        event.update(event_type="model_end", dates={"effective_from": "2026-11-24", "published": None, "date_basis": "not_before",
                                                    "date_text": "November 24, 2026"},
                     evidence_refs=[{"run_id": "20261026T013000Z-a"}])
        sync(self.db, {"events": [event]}, self.today, 5)
        report = prepare(self.db, self.today, "test", judged=parse(self.cards_document()))
        self.assertEqual([(c["alert"], c["stage"], c["days"], c["date_basis"]) for c in report["cards"]], [("new", 0, 29, "not_before")])
        self.assertIn("※ 가장 이른 종료일입니다. 이 날짜 전에는 끝나지 않으며 정확한 종료일은 아닙니다.", render(report))
        exact = dict(event, dates=dict(event["dates"], date_basis="stated"))
        db = connect(Path(self.tmp.name) / "exact.sqlite")
        try:
            sync(db, {"events": [exact]}, self.today, 5)
            report = prepare(db, self.today, "test", judged=parse(self.cards_document()))
            self.assertEqual([(c["days"], c["date_basis"]) for c in report["cards"]], [(29, "stated")])
            self.assertNotIn("가장 이른 종료일", render(report))
        finally:
            db.close()

    def test_a_stop_of_selling_comes_before_a_cost_change_whatever_the_deadline(self):
        stop = copy.deepcopy(self.document["events"][0])
        stop.update(event_id="demo-stop", event_type="model_end", dates={"effective_from": "2026-10-30"})
        sync(self.db, {"events": [self.document["events"][0], stop]}, self.today, 5)
        report = prepare(self.db, self.today, "test")
        self.assertEqual([(c["stage"], c["days"]) for c in report["cards"]], [(0, 4), (1, 3)])


    def supplier_end(self, **changes):
        """Vercel's deprecated_at end of one supplier on LETSUR's route (결정 기록 37절): a channel API's own dated stop that
        stays a candidate (undocumented field, stop not seen) and has no publication day, only the run that carried it."""
        event = copy.deepcopy(self.document["events"][0])  # demo-input, in the APPLIES card of fixtures_cards.json
        event.update(event_type="route_change", subtags=["remove"], status="candidate", conflicts=[],
                     dates={"effective_from": "2026-11-18", "published": None},
                     sources=[{"authority": "channel_official", "role": "api"}],
                     evidence_refs=[{"run_id": "20261026T013000Z-a", "locator": "data.endpoints[0].deprecated_at"}],
                     gaps=[{"field": "undocumented_field", "class": "check_first", "reason": "deprecated_at is not documented"},
                           {"field": "shutdown_confirmed", "class": "limits_value", "reason": "call stop not observed"}])
        event.update(changes)
        return event

    def test_a_channel_dated_end_of_a_supplier_goes_out_when_first_seen_while_still_a_candidate(self):
        sync(self.db, {"events": [self.supplier_end()]}, self.today, 5)
        judged = parse(self.cards_document())
        first = prepare(self.db, self.today, "test", judged=judged)
        self.assertEqual([(c["alert"], c["stage"], c["days"]) for c in first["cards"]], [("new", 0, 23)])
        simulated_success(self.db, first)
        later = prepare(self.db, date(2026, 11, 13), "test", judged=judged)
        self.assertEqual([(c["alert"], c["days"]) for c in later["cards"]], [("urgent", 5)])

    def test_the_end_of_supply_gaps_open_only_dated_official_route_removals(self):
        self.assertTrue(event_eligible(self.supplier_end()))
        other_gap = self.supplier_end()
        other_gap["gaps"].append({"field": "scope", "reason": "unknown"})
        cases = {"model end": self.supplier_end(event_type="model_end", subtags=[]),
                 "price change": self.supplier_end(event_type="price_change", subtags=[]),
                 "other gap": other_gap,
                 "unofficial": self.supplier_end(sources=[{"authority": "unofficial", "role": "tracker"}]),
                 "no route": self.supplier_end(route=""),
                 "no date": self.supplier_end(dates={"published": None})}
        for label, event in cases.items():
            with self.subTest(label):
                self.assertFalse(event_eligible(event))

    def test_first_seen_falls_back_to_the_earliest_run_that_carried_the_event(self):
        event = self.supplier_end(evidence_refs=[{"run_id": "20261027T160000Z-b"}, {"run_id": "20261026T013000Z-a"}])
        self.assertEqual(observed(event), date(2026, 10, 26))  # 01:30 UTC is 10:30 KST the same day
        self.assertEqual(observed(self.supplier_end(evidence_refs=[{"run_id": "20261026T160000Z-c"}])), date(2026, 10, 27))
        self.assertIsNone(observed(self.supplier_end(evidence_refs=[])))
        self.assertIsNone(observed(self.supplier_end(evidence_refs=[{"run_id": "not-a-run"}])))
        self.assertEqual(observed(self.supplier_end(dates={"published": "2026-10-20"})), date(2026, 10, 20))

    def test_a_price_cut_under_ten_percent_is_not_a_new_change(self):
        # 2026-10-28 (결정 기록 29절 렛서 답변): cuts are sent from 10%, rises whatever their size, discounts from 10%
        self.assertTrue(small_decrease({"event_type": "price_change", "change": {"pct": "-9.99"}}))
        self.assertFalse(small_decrease({"event_type": "price_change", "change": {"pct": "-10"}}))
        self.assertFalse(small_decrease({"event_type": "price_change", "change": {"pct": "2"}}))
        self.assertTrue(small_decrease({"event_type": "price_change", "before": {"amount": "1"}, "after": {"amount": "0.95"}}))
        self.assertTrue(small_decrease({"event_type": "limited_discount", "change": {"discount_pct": 5}}))
        self.assertFalse(small_decrease({"event_type": "limited_discount", "change": {"discount_pct": 50}}))
        for number, (change, expected) in enumerate(((({"pct": "-5"}), []), ({"pct": "-35"}, ["demo-no-date"]), ({"pct": "3"}, ["demo-no-date"]))):
            with self.subTest(change=change):
                db = connect(Path(self.tmp.name) / f"cut{number}.sqlite")
                try:
                    sync(db, self.new_change_document(change=change), self.today, 5)
                    report = prepare(db, self.today, "test", judged=parse(self.cards_document()))
                    self.assertEqual([i["event_id"] for c in report["cards"] for i in c["items"]], expected)
                finally:
                    db.close()

    def test_a_failing_source_is_sent_once_after_the_market_changes(self):
        # 수집 점검 (2026-10-28 박건 제안): health.failing becomes an event of today, sent once, after the market cards
        failing = [{"key": "src_a", "sinceRun": "20261024T050000Z-x", "model": "가상 모델 A", "route": "가상 공급 경로",
                    "error": "ValueError: page changed", "since": "2026-10-24", "url": "https://src.example/p"}]
        judged = parse(self.cards_document())
        sync(self.db, self.document, self.today, 5)
        sync(self.db, {"events": health_events(failing)}, self.today, 5)
        report = prepare(self.db, self.today, "test", judged=judged)
        self.assertEqual([c["alert"] for c in report["cards"]], ["urgent", "health"])
        health = report["cards"][-1]
        self.assertEqual((health["items"][0]["event_id"], health["links"]), ("health:src_a:20261024T050000Z-x", ["https://src.example/p"]))
        text = render(report)
        self.assertIn("수집 점검 · 2026-10-24부터 실패", text)
        self.assertIn("오류 : ValueError: page changed", text)
        simulated_success(self.db, report)
        tomorrow = self.today + timedelta(days=1)
        sync(self.db, {"events": health_events(failing)}, tomorrow, 5)
        self.assertNotIn("health", [c["alert"] for c in prepare(self.db, tomorrow, "test", judged=judged)["cards"]])
        # a streak that was never sent is not sent once it is no longer in today's input either
        db = connect(Path(self.tmp.name) / "stale.sqlite")
        try:
            sync(db, {"events": health_events(failing)}, self.today, 5)
            self.assertEqual(prepare(db, tomorrow, "test", judged=judged)["cards"], [])
        finally:
            db.close()

    def failing_source(self, key="src_a", **judgement):
        return {"key": key, "sinceRun": "20261024T050000Z-x", "model": "가상 모델 A", "route": "가상 공급 경로",
                "error": "ValueError: page changed", "since": "2026-10-24", "url": "https://src.example/p", **judgement}

    FORMAT = {"kind": "format", "label": "수집기 수정 필요", "action": "원문을 열어 바뀐 형식에 맞게 수집기를 고치세요", "runs": 1, "fix": True}
    TEMPORARY = {"kind": "temporary", "label": "일시 오류", "action": "연속으로 실패했습니다. 출처가 열리는지 확인하세요", "runs": 2, "fix": True}

    def test_only_a_failure_the_dashboard_asks_to_act_on_becomes_an_event(self):
        # 2026-10-29 (박건: 수집기를 고쳐야 하는 실패만 가려 알린다): a temporary error on its first failed run waits for the next run
        waiting = self.failing_source("slow", **dict(self.TEMPORARY, runs=1, fix=False))
        events = health_events([waiting, self.failing_source("again", **self.TEMPORARY), self.failing_source("fmt", **self.FORMAT),
                                self.failing_source("older")])
        self.assertEqual([event["source_key"] for event in events], ["again", "fmt", "older"])
        self.assertEqual(events[0]["failure"], {"kind": "temporary", "label": "일시 오류", "action": self.TEMPORARY["action"], "runs": 2})
        self.assertNotIn("failure", events[2])  # an older cards.json carries no judgement: the event is what it was
        self.assertEqual(health_events(None), [])

    def test_the_judgement_does_not_change_an_events_identity(self):
        # a streak already sent before 2026-10-29 must not go out again because its event now carries a judgement
        plain, judged = health_events([self.failing_source()])[0], health_events([self.failing_source(**self.FORMAT)])[0]
        self.assertEqual(digest(material(plain)), digest(material(judged)))
        sync(self.db, {"events": [plain]}, self.today, 5)
        simulated_success(self.db, prepare(self.db, self.today, "test"))
        tomorrow = self.today + timedelta(days=1)
        sync(self.db, {"events": [judged]}, tomorrow, 5)
        self.assertEqual(prepare(self.db, tomorrow, "test")["cards"], [])

    def test_the_card_says_the_judgement_and_what_to_do(self):
        sync(self.db, {"events": health_events([self.failing_source(**self.FORMAT)])}, self.today, 5)
        text = render(prepare(self.db, self.today, "test"))
        self.assertIn("1. 수집기 수정 필요 · 가상 모델 A · 가상 공급 경로\n수집 점검 · 2026-10-24부터 실패\n"
                      "조치 : 원문을 열어 바뀐 형식에 맞게 수집기를 고치세요\n근거 : https://src.example/p\n  오류 : ValueError: page changed", text)
        db = connect(Path(self.tmp.name) / "repeat.sqlite")
        try:
            sync(db, {"events": health_events([self.failing_source(**self.TEMPORARY)])}, self.today, 5)
            text = render(prepare(db, self.today, "test"))
        finally:
            db.close()
        self.assertIn("1. 일시 오류가 2회 이어짐 · 가상 모델 A · 가상 공급 경로\n", text)
        self.assertIn("조치 : 연속으로 실패했습니다. 출처가 열리는지 확인하세요", text)
        # no judgement (an older cards.json): the card reads as before
        db = connect(Path(self.tmp.name) / "older.sqlite")
        try:
            sync(db, {"events": health_events([self.failing_source()])}, self.today, 5)
            text = render(prepare(db, self.today, "test"))
        finally:
            db.close()
        self.assertIn("1. 가상 모델 A / 가상 공급 경로\n수집 점검 · 2026-10-24부터 실패\n", text)
        self.assertNotIn("조치 :", text)

    def test_cards_of_two_judgements_are_not_mixed(self):
        failing = [self.failing_source("fmt", **self.FORMAT), self.failing_source("slow", **self.TEMPORARY), self.failing_source("fmt2", **self.FORMAT)]
        sync(self.db, {"events": health_events(failing)}, self.today, 5)
        cards = prepare(self.db, self.today, "test")["cards"]
        self.assertEqual(sorted((c["items"][0]["failure"]["kind"], len(c["items"])) for c in cards), [("format", 2), ("temporary", 1)])

    def test_a_temporary_error_is_sent_once_it_has_failed_twice_in_a_row(self):
        # day 1: first failed run, nothing; day 2: the second run fails too, one alert for the streak; day 3: not again
        day1, day2, day3 = self.today, self.today + timedelta(days=1), self.today + timedelta(days=2)
        sync(self.db, {"events": health_events([self.failing_source(**dict(self.TEMPORARY, runs=1, fix=False))])}, day1, 5)
        self.assertEqual(prepare(self.db, day1, "test")["cards"], [])
        sync(self.db, {"events": health_events([self.failing_source(**self.TEMPORARY)])}, day2, 5)
        report = prepare(self.db, day2, "test")
        self.assertEqual([c["alert"] for c in report["cards"]], ["health"])
        self.assertEqual(report["cards"][0]["items"][0]["event_id"], "health:src_a:20261024T050000Z-x")
        simulated_success(self.db, report)
        sync(self.db, {"events": health_events([self.failing_source(**dict(self.TEMPORARY, runs=3))])}, day3, 5)
        self.assertEqual(prepare(self.db, day3, "test")["cards"], [])
        # a temporary error that cleared after one run was never sent, and a new streak is a new alert
        sync(self.db, {"events": health_events([self.failing_source(**dict(self.TEMPORARY, runs=2), sinceRun="20261030T050000Z-y")])},
             day3 + timedelta(days=1), 5)
        self.assertEqual(len(prepare(self.db, day3 + timedelta(days=1), "test")["cards"]), 1)

    def test_the_message_says_whether_letsur_uses_the_route_and_what_to_check(self):
        # 2026-10-28 (결정 기록 29절 렛서 답변): 현재 이용 중인 경로인지, 근거 링크와 추가 확인 사항
        sync(self.db, self.document, self.today, 5)
        document = self.cards_document()
        document["cards"][0].update(usage="이 경로로 사용 중 (가정)", check="렛서 계정에서 확인", links=["https://src.example/p"])
        text = render(prepare(self.db, self.today, "test", judged=parse(document)))
        for line in ("렛서 사용 : 이 경로로 사용 중 (가정)", "확인할 것 : 렛서 계정에서 확인", "근거 : https://src.example/p"):
            self.assertIn(line, text)


if __name__ == "__main__":
    unittest.main()
