"""Create Slack payload previews and exercise delivery/ack locally; no network."""
import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import uuid

from alerts import connect, sync, prepare, render, acknowledge
from delivery import pending_report, enqueue, attempt
from slack_adapter import message_batches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="output/hardened-demo")
    args = parser.parse_args()
    output = Path(args.output) / ("run-" + uuid.uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    document = json.loads(Path(__file__).with_name("fixtures.json").read_text(encoding="utf-8"))
    today = date(2026, 10, 26)
    now = datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)
    # A separate DB per run keeps the simulated timeline reproducible.
    with connect(output / "records.sqlite") as db:
        sync(db, document, today, 5)
        before = prepare(db, today, "C_LOCAL_DEMO")
        batches = message_batches(db, before)
        (output / "slack-payloads.json").write_text(json.dumps(
            {"notice": "합성 자료 · 외부 발송 없음", "messages": [p for p, _ in batches]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "before.txt").write_text(render(before), encoding="utf-8")
        calls = []
        def fake_sender(payload, identity):
            calls.append(identity)
            return {"channel": payload["channel"], "ts": "offline." + str(len(calls))}
        pending = pending_report(db, before)
        for payload, cards in message_batches(db, pending):
            identity = enqueue(db, pending, payload, cards, now)
            assert attempt(db, identity, fake_sender, now) == "sent"
        assert pending_report(db, prepare(db, today, "C_LOCAL_DEMO"))["event_count"] == 0
        if before["cards"]:
            acknowledge(db, before["cards"][0], "LOCAL_TESTER", today, timestamp=now)
        after = prepare(db, date(2026, 10, 27), "C_LOCAL_DEMO")
        (output / "after-next-day.txt").write_text(render(after), encoding="utf-8")
        (output / "summary.json").write_text(json.dumps({
            "mode": "OFFLINE_ONLY", "before_cards": before["card_count"],
            "next_day_unacknowledged_cards": after["card_count"],
            "fake_sender_calls": len(calls), "actual_slack_calls": 0
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("오프라인 검증 완료. 실제 Slack 호출 0회.")
        print(str(output.resolve()))


if __name__ == "__main__":
    main()
