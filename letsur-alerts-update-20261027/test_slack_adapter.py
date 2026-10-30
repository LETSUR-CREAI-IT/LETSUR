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
from dashboard_cards import parse
from slack_adapter import build_messages, handle_interaction, message_batches, verify_signature


DASHBOARD = "https://dashboard.example/LETSUR/"


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

    def cards_document(self):
        return json.loads(Path(__file__).with_name("fixtures_cards.json").read_text(encoding="utf-8"))

    def judged_messages(self, document, **options):
        report = prepare(self.db, self.today, "C_TEST", judged=parse(document))
        return build_messages(self.db, report, dashboard_url=DASHBOARD, **options)

    def cards(self, messages):
        """[(card text, dashboard button url)] in message order."""
        result = []
        for message in messages:
            blocks = message["blocks"]
            for index, block in enumerate(blocks):
                if block["type"] == "actions":
                    url = next((el.get("url") for el in block["elements"] if el["action_id"] == "open_dashboard"), None)
                    result.append((blocks[index - 1]["text"]["text"], url))
        return result

    def test_dashboard_button_opens_the_dashboard_card_that_holds_the_events(self):
        document = self.cards_document()
        document["cards"][1].update(action="REVIEW", applicability="APPLIES")  # keep the discount alert so both buttons exist
        cards = self.cards(self.judged_messages(document))
        links = {text.splitlines()[0].split(" / ")[0]: url for text, url in cards}
        self.assertEqual(links, {"가상 모델 A": DASHBOARD + "#g_0000000000a1", "가상 모델 B": DASHBOARD + "#g_0000000000b1"})
        self.assertFalse(any("대시보드 카드 외" in text for text, _ in cards))

    def test_events_spread_over_several_dashboard_cards_link_the_first_and_count_the_rest(self):
        extra = copy.deepcopy(self.doc["events"][1])
        extra["event_id"] = "demo-output2"
        sync(self.db, {"events": [extra]}, self.today, 5)
        document = self.cards_document()
        first = document["cards"][0]
        first["events"] = ["demo-input"]
        second = dict(first, id="g_0000000000a2", events=["demo-output"])
        third = dict(first, id="g_0000000000a3", events=["demo-output2"])
        document["cards"] += [second, third]
        (text, url), = [card for card in self.cards(self.judged_messages(document)) if card[0].startswith("가상 모델 A")]
        self.assertEqual(url, DASHBOARD + "#g_0000000000a1")
        self.assertIn("\n대시보드 카드 외 2장", text)
        document["cards"].remove(third)  # demo-output2 now has no dashboard card, so only two cards hold the events
        (text, url), = [card for card in self.cards(self.judged_messages(document)) if card[0].startswith("가상 모델 A")]
        self.assertEqual(url, DASHBOARD + "#g_0000000000a1")
        self.assertIn("\n대시보드 카드 외 1장", text)

    def test_each_displayed_part_of_a_long_card_links_its_own_dashboard_card(self):
        report = prepare(self.db, self.today, "C_TEST", judged=parse(self.cards_document()))
        card = next(c for c in report["cards"] if c["model"] == "가상 모델 A")
        report["cards"] = [card]
        base = card["items"][0]
        card["items"] = [dict(base, event_id=f"long-{n:02d}", dashboard_card=f"g_{n:012d}", billing_item="긴 요금 이름" * 20)
                         for n in range(50)]
        messages = build_messages(self.db, report, dashboard_url=DASHBOARD)
        buttons = [(b["elements"][0]["value"], b["elements"][1]["url"]) for m in messages for b in m["blocks"] if b["type"] == "actions"]
        self.assertGreater(len(buttons), 1)
        for token, url in buttons:
            stored = json.loads(self.db.execute("SELECT card_json FROM slack_ack_tokens WHERE token=?", (token,)).fetchone()[0])
            first = int(stored["items"][0]["event_id"].split("-")[1])
            self.assertEqual(url, DASHBOARD + f"#g_{first:012d}")

    def test_without_a_dashboard_card_the_button_keeps_the_plain_address(self):
        cards = self.cards(build_messages(self.db, self.report, dashboard_url=DASHBOARD))
        self.assertEqual({url for _, url in cards}, {DASHBOARD})
        self.assertFalse(any("대시보드 카드" in text for text, _ in cards))
        document = self.cards_document()
        document["cards"][1].update(action="REVIEW", applicability="APPLIES")
        document["cards"].pop(0)  # no dashboard card holds the two prices
        links = {text.split(" / ")[0]: url for text, url in self.cards(self.judged_messages(document))}
        self.assertEqual(links, {"가상 모델 A": DASHBOARD, "가상 모델 B": DASHBOARD + "#g_0000000000b1"})

    def test_a_fragment_in_the_configured_address_is_replaced_not_doubled(self):
        report = prepare(self.db, self.today, "C_TEST", judged=parse(self.cards_document()))
        (text, url), = self.cards(build_messages(self.db, report, dashboard_url=DASHBOARD + "#old"))
        self.assertEqual(url, DASHBOARD + "#g_0000000000a1")

    def test_a_health_card_says_so_and_links_its_source_and_the_health_screen(self):
        # 수집 점검 (2026-10-28): its own count in the header, the failing source as 근거, the dashboard at #health
        report = copy.deepcopy(self.report)
        report["cards"][0].update(alert="health", links=["https://src.example/p"], usage=None, check=None)
        report["cards"][0]["items"][0].update(event_type="collection_failure", error="ValueError: page changed", since="2026-10-24")
        message = build_messages(self.db, report, dashboard_url=DASHBOARD)[0]
        self.assertEqual(message["text"], f"렛서 알림 · 2026-10-26 · 긴급 {len(report['cards']) - 1}개 · 수집 점검 1개")
        text = message["blocks"][1]["text"]["text"]
        self.assertIn("수집 점검 · 2026-10-24부터 실패", text)
        self.assertIn("오류 : ValueError: page changed", text)
        buttons = {e["text"]["text"]: e.get("url") for e in message["blocks"][2]["elements"]}
        self.assertEqual((buttons["근거"], buttons["대시보드"]), ("https://src.example/p", DASHBOARD + "#health"))

    def test_a_health_card_headline_says_the_judgement_and_the_card_says_what_to_do(self):
        # 2026-10-29 (박건: 수집기를 고쳐야 하는 실패만 가려 알린다): '수집기 수정 필요 · 모델 · 경로', '일시 오류가 2회 이어짐 · …', 조치 line
        for failure, headline, to_do in (
                ({"kind": "format", "label": "수집기 수정 필요", "action": "원문을 열어 바뀐 형식에 맞게 수집기를 고치세요", "runs": 1},
                 "수집기 수정 필요 · 가상 모델 A · 가상 공급 경로", "조치 : 원문을 열어 바뀐 형식에 맞게 수집기를 고치세요"),
                ({"kind": "temporary", "label": "일시 오류", "action": "연속으로 실패했습니다. 출처가 열리는지 확인하세요", "runs": 2},
                 "일시 오류가 2회 이어짐 · 가상 모델 A · 가상 공급 경로", "조치 : 연속으로 실패했습니다. 출처가 열리는지 확인하세요")):
            with self.subTest(kind=failure["kind"]):
                report = copy.deepcopy(self.report)
                report["cards"][0].update(alert="health", model="가상 모델 A", route="가상 공급 경로", usage=None, check=None)
                report["cards"][0]["items"][0].update(event_type="collection_failure", error="ValueError: page changed", since="2026-10-24", failure=failure)
                text = build_messages(self.db, report, dashboard_url=DASHBOARD)[0]["blocks"][1]["text"]["text"]
                self.assertEqual(text.split("\n")[:3], [headline, "수집 점검 · 2026-10-24부터 실패", to_do])
                self.assertIn("오류 : ValueError: page changed", text)

    def test_a_health_card_without_a_judgement_keeps_its_old_text(self):
        report = copy.deepcopy(self.report)
        report["cards"][0].update(alert="health", model="가상 모델 A", route="가상 공급 경로", usage=None, check=None)
        report["cards"][0]["items"][0].update(event_type="collection_failure", error="ValueError: page changed", since="2026-10-24")
        text = build_messages(self.db, report, dashboard_url=DASHBOARD)[0]["blocks"][1]["text"]["text"]
        self.assertEqual(text.split("\n")[:2], ["가상 모델 A / 가상 공급 경로", "수집 점검 · 2026-10-24부터 실패"])
        self.assertNotIn("조치 :", text)

    def test_usage_and_check_lines_and_a_bad_link_gets_no_button(self):
        report = copy.deepcopy(self.report)
        report["cards"][0].update(usage="이 경로로 사용 중 (가정)", check="렛서 계정에서 확인", links=["https://a b"])
        message = build_messages(self.db, report, dashboard_url=DASHBOARD)[0]
        self.assertIn("렛서 사용 : 이 경로로 사용 중 (가정)\n확인할 것 : 렛서 계정에서 확인", message["blocks"][1]["text"]["text"])
        self.assertNotIn("근거", [e["text"]["text"] for e in message["blocks"][2]["elements"]])

    def test_an_earliest_retirement_date_is_said_to_be_one(self):
        report = copy.deepcopy(self.report)
        report["cards"][0]["date_basis"] = "not_before"  # a retirement schedule's earliest date, the Gemini table's (2026-10-29)
        texts = [text for text, _ in self.cards(build_messages(self.db, report))]
        self.assertIn("가장 이른 종료일 · 이 날짜 전에는 끝나지 않음", texts[0])
        self.assertFalse(any("가장 이른 종료일" in text for text in texts[1:]))

    def test_new_changes_are_counted_in_the_header_and_said_to_go_out_once(self):
        # 2026-10-28 가안: a new change goes out once, so the message does not call every card urgent.
        report = copy.deepcopy(self.report)
        report["cards"][0]["alert"] = "new"
        message = build_messages(self.db, report)[0]
        self.assertEqual(message["text"], f"렛서 알림 · 2026-10-26 · 긴급 {len(report['cards']) - 1}개 · 새 변화 1개")
        self.assertIn("새로 확인된 변화 · 한 번만 알림", message["blocks"][1]["text"]["text"])
        self.assertTrue(self.messages[0]["text"].startswith("렛서 긴급 알림 · 2026-10-26"))

    def test_card_text_writes_names_the_way_the_dashboard_does(self):
        report = copy.deepcopy(self.report)
        report["names"] = parse(self.cards_document())["names"]
        card = next(c for c in report["cards"] if c["model"] == "가상 모델 A")
        card["conditions"] = {"endpoint_tag": "novita/fp8", "service_tier": "priority", "region": "eu",
                              "input_token_band": "<=200K", "endpoint_status": "-5", "quantization": "fp8"}
        card["items"][0]["billing_item"] = "cache_read"
        card["items"][1]["billing_item"] = "surprise_item"
        card["items"][1]["after"] = dict(card["items"][1]["after"], unit="per image")
        report["cards"] = [card]
        text = self.cards(build_messages(self.db, report, dashboard_url=DASHBOARD))[0][0]
        self.assertIn("조건 · 엔드포인트: novita/fp8 / 등급: Priority / 지역: EU / 입력 구간: <=200K / 상태: 중단", text)
        self.assertIn("캐시 읽기: 1 → 2 USD / 100만 토큰", text)
        self.assertIn("surprise_item: 4 → 6 USD / per image", text)
        for raw in ("service_tier", "endpoint_status", "cache_read", "1M tokens", "quantization"):
            self.assertNotIn(raw, text)

    def test_unknown_names_fall_back_to_the_raw_id_and_a_normal_status_is_not_shown(self):
        card = copy.deepcopy(next(c for c in self.report["cards"] if c["model"] == "가상 모델 A"))
        card["conditions"] = {"service_tier": "custom-tier", "region": "ap-south", "endpoint_status": "0"}
        for names, expected in (({}, "조건 · service_tier: custom-tier / region: ap-south"),
                                (parse(self.cards_document())["names"], "조건 · 등급: custom-tier / 지역: ap-south")):
            with self.subTest(names=bool(names)):
                text = self.cards(build_messages(self.db, dict(self.report, cards=[card], names=names)))[0][0]
                self.assertIn(expected, text)
                self.assertNotIn("endpoint_status", text)
                self.assertNotIn("상태", text)


if __name__ == "__main__":
    unittest.main()
