import copy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from alerts import connect, sync, prepare, acknowledge
from delivery import enqueue, attempt, pending_report, DefiniteFailure


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.db = connect(Path(self.tmp.name) / "delivery.sqlite")
        self.today = date(2026, 10, 26)
        self.now = datetime(2026, 10, 26, tzinfo=timezone.utc)
        self.document = json.loads(Path(__file__).with_name("fixtures.json").read_text(encoding="utf-8"))
        sync(self.db, self.document, self.today, 5)
        self.report = prepare(self.db, self.today, "C_TEST")
        self.calls = []

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def sender(self, payload, identity):
        self.calls.append(identity)
        return {"channel": payload["channel"], "ts": "123.456"}

    def stage(self, report=None):
        report = report or self.report
        return enqueue(self.db, report, {"channel": report["channel"], "text": "test"}, report["cards"], self.now)

    def test_success_is_idempotent_and_next_day_reminds(self):
        identity = self.stage()
        self.assertEqual(attempt(self.db, identity, self.sender, self.now), "sent")
        self.assertEqual(attempt(self.db, identity, self.sender, self.now), "sent")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(pending_report(self.db, self.report)["event_count"], 0)
        tomorrow = prepare(self.db, self.today + timedelta(days=1), "C_TEST")
        self.assertEqual(pending_report(self.db, tomorrow)["event_count"], 3)

    def test_definite_rejection_can_retry_but_not_immediately(self):
        identity = self.stage()
        def limited(*args):
            raise DefiniteFailure("rate_limited", retryable=True, retry_after_seconds=10)
        self.assertEqual(attempt(self.db, identity, limited, self.now), "retry_later")
        self.assertEqual(attempt(self.db, identity, self.sender, self.now), "retry_later")
        self.assertEqual(attempt(self.db, identity, self.sender, self.now + timedelta(seconds=11)), "sent")

    def test_unknown_outcome_never_blindly_retries(self):
        identity = self.stage()
        def timeout(*args):
            raise TimeoutError("secret must not be saved")
        self.assertEqual(attempt(self.db, identity, timeout, self.now), "needs_reconciliation")
        self.assertEqual(attempt(self.db, identity, self.sender, self.now), "needs_reconciliation")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.db.execute("SELECT last_error FROM delivery_outbox").fetchone()[0], "outcome_unknown")

    def test_ack_after_staging_prevents_obsolete_send(self):
        identity = self.stage()
        acknowledge(self.db, self.report["cards"][0], "tester", self.today)
        self.assertEqual(attempt(self.db, identity, self.sender, self.now), "rebuild_required")
        self.assertEqual(self.calls, [])

    def test_modified_content_prevents_obsolete_send(self):
        identity = self.stage()
        self.document["events"][0]["after"]["amount"] = "9"
        sync(self.db, self.document, self.today, 5)
        self.assertEqual(attempt(self.db, identity, self.sender, self.now), "rebuild_required")

    def test_crashed_claim_is_not_resubmitted(self):
        identity = self.stage()
        with self.db:
            self.db.execute("UPDATE delivery_outbox SET state='sending' WHERE id=?", (identity,))
        self.assertEqual(attempt(self.db, identity, self.sender, self.now), "needs_reconciliation")

    def test_frozen_payload_and_new_revision_are_distinct(self):
        identity = self.stage()
        again = enqueue(self.db, self.report, {"channel": "C_TEST", "text": "different ack tokens"}, self.report["cards"], self.now)
        self.assertEqual(identity, again)
        self.assertEqual(json.loads(self.db.execute("SELECT payload FROM delivery_outbox WHERE id=?", (identity,)).fetchone()[0])["text"], "test")
        attempt(self.db, identity, self.sender, self.now)
        self.document["events"][0]["after"]["amount"] = "9"
        sync(self.db, self.document, self.today, 5)
        updated = pending_report(self.db, prepare(self.db, self.today, "C_TEST"))
        self.assertEqual(updated["event_count"], 1)
        self.assertEqual(updated["cards"][0]["items"][0]["delivery_kind"], "changed")
        self.assertNotEqual(identity, self.stage(updated))

    def test_max_attempts_and_permanent_failure(self):
        identity = self.stage()
        def fail(*args):
            raise DefiniteFailure("channel_not_found")
        self.assertEqual(attempt(self.db, identity, fail, self.now), "failed")
        self.assertEqual(attempt(self.db, identity, self.sender, self.now + timedelta(hours=1)), "failed")
        self.assertEqual(self.calls, [])

    def test_overlapping_messages_do_not_send_same_version_twice(self):
        full = self.stage()
        sub_report = copy.deepcopy(self.report)
        sub_report["cards"] = sub_report["cards"][:1]
        sub = self.stage(sub_report)
        self.assertNotEqual(full, sub)
        attempt(self.db, sub, self.sender, self.now)
        self.assertEqual(attempt(self.db, full, self.sender, self.now), "rebuild_required")
        self.assertEqual(len(self.calls), 1)

    def test_uncertain_overlapping_job_is_not_sent_again(self):
        full = self.stage()
        sub_report = copy.deepcopy(self.report)
        sub_report["cards"] = sub_report["cards"][:1]
        sub = self.stage(sub_report)
        def timeout(*args):
            raise TimeoutError()
        attempt(self.db, sub, timeout, self.now)
        self.assertEqual(attempt(self.db, full, self.sender, self.now), "needs_reconciliation")


if __name__ == "__main__":
    unittest.main()
