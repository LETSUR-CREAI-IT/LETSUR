import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from alerts import acknowledge, connect, prepare, sync
from github_source import SourceError
import operations

ROOT = Path(__file__).resolve().parent
DAY = date(2026, 10, 27)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = cls(2026, 10, 28, 6, 0, tzinfo=timezone.utc)
        return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / 'output')
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)
        self.live = self.state / 'alerts.sqlite'
        self.config = {'max_source_age_hours': 36, 'urgent_days': 5, 'dashboard_url': None}
        self.settings = {'SLACK_TEAM_ID': 'TTEST', 'SLACK_CHANNEL_ID': 'CTEST', 'SLACK_BOT_TOKEN': 'dummy'}
        self.document = json.loads((ROOT / 'fixtures.json').read_text(encoding='utf-8'))
        self.document['events'] = self.document['events'][:2]
        self.source_file = self.state / 'input.json'
        self.source_file.write_text(json.dumps(self.document), encoding='utf-8')
        self.source = {'fresh': True, 'stale_reasons': [], 'events_path': str(self.source_file),
                       'metadata': {'run_id': 123, 'run_attempt': 1, 'artifact_id': 456,
                                    'run_created_at': '2026-10-28T04:00:00Z'}}
        self.sent = []
        owner = self

        class FakeSlack:
            def __init__(self, token):
                pass

            def authenticate(self, team):
                return {'team_id': team}

            def send(self, payload, identity):
                owner.sent.append(payload)
                return {'channel': payload['channel'], 'ts': '100.' + str(len(owner.sent))}

        for target, value in (
            ('operations.datetime', FrozenDatetime),
            ('operations.github_token', lambda: None),
            ('operations.SlackClient', FakeSlack),
            ('operations.status', lambda state: {'listener_running': True}),
        ):
            context = patch(target, value)
            context.start()
            self.addCleanup(context.stop)

    def seed(self, ack=False):
        with closing(connect(self.live)) as db:
            sync(db, self.document, DAY, 5)
            report = prepare(db, DAY, 'CTEST')
            if ack:
                acknowledge(db, report['cards'][0], 'UTEST', DAY)

    def snapshot(self):
        with closing(sqlite3.connect(self.live)) as db:
            return list(db.iterdump())

    def test_preview_uses_live_ack_without_advancing_or_mutating_live_state(self):
        self.seed(ack=True)
        before = self.snapshot()
        with patch('operations.fetch_latest', return_value=self.source):
            result = operations.cycle('preview', self.config, self.state, self.settings)
        self.assertEqual(result['cards'], 0)
        self.assertEqual(result['sent_messages'], 0)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.snapshot(), before)

    def test_failed_source_still_reminds_existing_urgent_events_once(self):
        self.seed()
        with patch('operations.fetch_latest', side_effect=SourceError('network')):
            first = operations.cycle('send', self.config, self.state, self.settings)
            second = operations.cycle('send', self.config, self.state, self.settings)
        self.assertEqual(first['status'], 'needs_attention')
        self.assertEqual(first['source_error'], 'network')
        self.assertEqual(first['sent_messages'], 1)
        self.assertEqual(second['sent_messages'], 0)
        self.assertEqual(len(self.sent), 1)
        visible = json.dumps(self.sent[0], ensure_ascii=False)
        self.assertIn('수집 갱신 실패', visible)
        self.assertIn('기존 미확인 사건', visible)

    def test_no_source_and_no_prior_events_never_sends(self):
        # Listener initialization may have created an empty DB before any collection.
        with closing(connect(self.live)):
            pass
        with patch('operations.fetch_latest', side_effect=SourceError('authentication')):
            result = operations.cycle('send', self.config, self.state, self.settings)
        self.assertEqual(result['status'], 'source_unavailable')
        self.assertEqual(result['sent_messages'], 0)
        self.assertEqual(self.sent, [])

    def test_stale_source_does_not_import_new_events(self):
        self.seed()
        new = dict(self.document['events'][0], event_id='new-stale-event')
        self.document['events'].append(new)
        self.source_file.write_text(json.dumps(self.document), encoding='utf-8')
        stale = dict(self.source, fresh=False, stale_reasons=['newer_run_pending'])
        with patch('operations.fetch_latest', return_value=stale):
            result = operations.cycle('send', self.config, self.state, self.settings)
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['source_events'], 2)
        with closing(sqlite3.connect(self.live)) as db:
            self.assertIsNone(db.execute("SELECT id FROM events WHERE id='new-stale-event'").fetchone())

    def test_watch_waits_for_collection_and_skips_already_processed_source(self):
        with patch('operations.fetch_latest', return_value=dict(self.source, fresh=False,
                                                               stale_reasons=['newer_run_pending'])):
            waiting = operations.cycle('watch', self.config, self.state, self.settings)
        self.assertEqual(waiting['status'], 'collection_in_progress')
        self.assertEqual(self.sent, [])
        with patch('operations.fetch_latest', return_value=self.source):
            first = operations.cycle('watch', self.config, self.state, self.settings)
            second = operations.cycle('watch', self.config, self.state, self.settings)
        self.assertEqual(first['sent_messages'], 1)
        self.assertEqual(second['status'], 'no_new_collection')
        self.assertEqual(len(self.sent), 1)

    def test_missing_collection_fallback_reminds_once_in_evening(self):
        self.seed()
        operations.atomic_json(self.state / 'processed-source.json',
            {'source_key': '123-1-456', 'processed_day': '2026-10-27'})
        class Evening(FrozenDatetime):
            @classmethod
            def now(cls, tz=None):
                fixed = cls(2026, 10, 28, 14, 5, tzinfo=timezone.utc)
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
        with patch('operations.fetch_latest', return_value=self.source):
            daytime = operations.cycle('watch', self.config, self.state, self.settings)
            self.assertEqual(daytime['status'], 'no_new_collection')
            with patch('operations.datetime', Evening):
                first = operations.cycle('watch', self.config, self.state, self.settings)
                second = operations.cycle('watch', self.config, self.state, self.settings)
        self.assertEqual(first['source_error'], 'no_collection_today')
        self.assertEqual(first['sent_messages'], 1)
        self.assertEqual(second['sent_messages'], 0)


if __name__ == '__main__':
    unittest.main()
