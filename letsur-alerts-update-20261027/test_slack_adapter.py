import copy
import hashlib
import hmac
import json
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlencode

from alerts import connect, prepare, sync
from slack_adapter import build_messages, handle_interaction, message_batches, verify_signature


class SlackAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.db = connect(Path(self.tmp.name) / "state.sqlite")
        self.today = date(2026, 10, 26)
        self.now = datetime(2026, 10, 26, 4, tzinfo=timezone.utc)
        self.secret = "local-test-secret"
        self.doc = json.loads(Path(__file__).with_name("fixtures.json").read_text(encoding="utf-8"))
        sync(self.db, self.doc, self.today, 5)
        self.report = prepare(self.db, self.today, "C_TEST")
        self.messages = build_messages(self.db, self.report)
        self.token = self.messages[0]["blocks"][2]["elements"][0]["value"]

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def payload(self):
        return {"type": "block_actions", "team": {"id": "T_TEST"},
                "channel": {"id": "C_TEST"}, "user": {"id": "U_TEST"},
                "actions": [{"action_id": "ack_card", "value": self.token}]}

    def signed(self, payload, timestamp=None):
        stamp = str(int(self.now.timestamp()) if timestamp is None else timestamp)
        raw = urlencode({"payload": json.dumps(payload)}).encode()
        signature = "v0=" + hmac.new(self.secret.encode(), b"v0:" + stamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
        return {"X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": signature}, raw

    def call(self, payload):
        headers, raw = self.signed(payload)
        return handle_interaction(self.db, headers, raw, self.secret, "T_TEST", "C_TEST", self.now)

    def test_payload_is_plain_text_and_button_snapshot_is_opaque(self):
        malicious = copy.deepcopy(self.report)
        malicious["cards"][0]["model"] = "<!channel> <@U123> & fake"
        messages = build_messages(self.db, malicious)
        self.assertEqual(messages[0]["blocks"][1]["text"]["type"], "plain_text")
        self.assertEqual(messages[0]["parse"], "none")
        self.assertLess(len(self.token), 100)
        self.assertNotIn("demo-discount", self.token)
        self.assertFalse(any("url" in el for b in messages[0]["blocks"] for el in b.get("elements", [])))

    def test_empty_report_sends_nothing(self):
        self.assertEqual(build_messages(self.db, {"cards": []}), [])

    def test_many_cards_split_at_block_limit(self):
        report = copy.deepcopy(self.report)
        report["cards"] = [copy.deepcopy(report["cards"][0]) for _ in range(40)]
        messages = build_messages(self.db, report)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(m["blocks"]) <= 40 for m in messages))
        self.assertTrue(all(len(b.get("text", {}).get("text", "")) <= 3000 for m in messages for b in m["blocks"]))

    def test_batch_metadata_contains_exact_displayed_members(self):
        batches = message_batches(self.db, self.report, max_blocks=4)
        self.assertEqual(len(batches), 2)
        for payload, cards in batches:
            self.assertEqual(len(cards), 1)
            token = payload["blocks"][2]["elements"][0]["value"]
            stored = json.loads(self.db.execute("SELECT card_json FROM slack_ack_tokens WHERE token=?", (token,)).fetchone()[0])
            self.assertEqual([i["event_id"] for i in stored["items"]], [i["event_id"] for i in cards[0]["items"]])

    def test_candidate_label_and_reason_never_claim_confirmation(self):
        report = copy.deepcopy(self.report)
        report["cards"][0]["items"][0]["status"] = "candidate"
        report["cards"][0]["items"][0]["gaps"] = [{"field": "raw_check", "reason": "parser_fingerprint_mismatch"}]
        report["cards"][0]["items"][0]["delivery_kind"] = "initial"
        text = build_messages(self.db, report)[0]["blocks"][1]["text"]["text"]
        self.assertIn("긴급 확인 필요 · 미확정", text)
        self.assertIn("과거 원문 재검증 필요", text)
        self.assertNotIn("신뢰 상태 변경", text)

    def test_long_card_splits_without_acknowledging_hidden_items(self):
        report = copy.deepcopy(self.report)
        card = report["cards"][1]
        report["cards"] = [card]
        base = copy.deepcopy(card["items"][0])
        card["items"] = [dict(base, event_id=f"long-{n}", billing_item="긴 요금 이름" * 20) for n in range(50)]
        messages = build_messages(self.db, report)
        buttons = [el for m in messages for b in m["blocks"] for el in b.get("elements", []) if el["action_id"] == "ack_card"]
        self.assertGreater(len(buttons), 1)
        stored = [json.loads(self.db.execute("SELECT card_json FROM slack_ack_tokens WHERE token=?", (b["value"],)).fetchone()[0]) for b in buttons]
        ids = [i["event_id"] for c in stored for i in c["items"]]
        self.assertEqual(len(ids), 50)
        self.assertEqual(len(set(ids)), 50)
        self.assertTrue(all(len(b.get("text", {}).get("text", "")) <= 3000 for m in messages for b in m["blocks"]))

    def test_bad_dashboard_url_rejected(self):
        for url in ("http://example.com", "javascript:alert(1)", "https://user:secret@example.com"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                build_messages(self.db, self.report, dashboard_url=url)

    def test_signed_click_acknowledges_only_snapshot_and_retry_is_idempotent(self):
        self.assertEqual(self.call(self.payload())[0], 200)
        self.assertEqual(self.call(self.payload())[0], 200)
        self.assertEqual(prepare(self.db, self.today, "C_TEST")["event_count"], 2)

    def test_wrong_signature_raw_body_and_expired_request_rejected(self):
        headers, raw = self.signed(self.payload())
        self.assertTrue(verify_signature(headers, raw, self.secret, self.now))
        self.assertFalse(verify_signature(headers, raw + b"x", self.secret, self.now))
        headers["X-Slack-Signature"] = "v0=bad"
        self.assertEqual(handle_interaction(self.db, headers, raw, self.secret, "T_TEST", "C_TEST", self.now)[0], 401)
        headers, raw = self.signed(self.payload(), int(self.now.timestamp()) - 301)
        self.assertFalse(verify_signature(headers, raw, self.secret, self.now))

    def test_headers_are_case_insensitive_and_future_timestamp_checked(self):
        headers, raw = self.signed(self.payload())
        self.assertTrue(verify_signature({k.lower(): v for k, v in headers.items()}, raw, self.secret, self.now))
        headers, raw = self.signed(self.payload(), int(self.now.timestamp()) + 301)
        self.assertFalse(verify_signature(headers, raw, self.secret, self.now))

    def test_wrong_team_channel_actor_token_and_action_rejected(self):
        cases = [("team", {"id": "OTHER"}, 403), ("channel", {"id": "OTHER"}, 403),
                 ("user", {"id": ""}, 400),
                 ("actions", [{"action_id": "ack_card", "value": "unknown"}], 403),
                 ("actions", [{"action_id": "wrong", "value": self.token}], 400)]
        for key, value, status in cases:
            with self.subTest(key=key, value=value):
                payload = self.payload()
                payload[key] = value
                self.assertEqual(self.call(payload)[0], status)
        self.assertEqual(prepare(self.db, self.today, "C_TEST")["event_count"], 3)

    def test_stale_snapshot_cannot_ack_updated_card(self):
        self.doc["events"][2]["change"]["discount_pct"] = "60"
        sync(self.db, self.doc, self.today, 5)
        self.assertEqual(self.call(self.payload())[0], 409)
        self.assertEqual(prepare(self.db, self.today, "C_TEST")["event_count"], 3)

    def test_malformed_signed_body_is_controlled_error(self):
        stamp = str(int(self.now.timestamp()))
        raw = b"payload=%7Bbroken"
        signature = "v0=" + hmac.new(self.secret.encode(), b"v0:" + stamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
        headers = {"X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": signature}
        self.assertEqual(handle_interaction(self.db, headers, raw, self.secret, "T_TEST", "C_TEST", self.now)[0], 400)


if __name__ == "__main__":
    unittest.main()
