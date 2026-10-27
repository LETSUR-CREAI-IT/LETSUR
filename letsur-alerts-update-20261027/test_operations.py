import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from alerts import acknowledge, connect, prepare, sync
from github_source import SourceError, fetch_latest as real_fetch_latest
import operations
from test_github_source import FakeClient, NOW as GITHUB_NOW, archive, pages_archive, run

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

    def cards_source(self, change=None, content=None, filename='cards.json'):
        """A fetch_latest result whose run has cards.json: the fixture, edited by change, or the given text."""
        document = json.loads((ROOT / 'fixtures_cards.json').read_text(encoding='utf-8'))
        if change:
            change(document)
        path = self.state / filename
        path.write_text(content if content is not None else json.dumps(document, ensure_ascii=False), encoding='utf-8')
        return dict(self.source, cards_path=str(path), cards_error=None)

    def dashboard_url(self, payload):
        return next(e['url'] for b in payload['blocks'] for e in b.get('elements', []) if e['action_id'] == 'open_dashboard')

    def test_cards_json_of_the_run_decides_what_is_sent(self):
        self.config['dashboard_url'] = 'https://dashboard.example/LETSUR/'
        with patch('operations.fetch_latest', return_value=self.cards_source()):
            result = operations.cycle('send', self.config, self.state, self.settings)
        self.assertIsNone(result['cards_warning'])
        self.assertEqual((result['status'], result['sent_messages'], result['events']), ('completed', 1, 2))
        self.assertEqual(self.dashboard_url(self.sent[0]), 'https://dashboard.example/LETSUR/#g_0000000000a1')
        visible = json.dumps(self.sent[0], ensure_ascii=False)
        self.assertNotIn('cards.json', visible)
        self.assertIn('입력 토큰: 1 → 2 USD / 100만 토큰', visible)

    def test_record_cards_of_the_run_are_not_sent(self):
        with patch('operations.fetch_latest', return_value=self.cards_source(
                lambda document: document['cards'][0].update(action='RECORD'))):
            result = operations.cycle('send', self.config, self.state, self.settings)
        self.assertEqual((result['status'], result['sent_messages'], result['cards'], result['events']), ('completed', 0, 0, 0))
        self.assertIsNone(result['cards_warning'])
        self.assertEqual(self.sent, [])

    def test_missing_cards_json_sends_by_the_bot_rules_and_says_so(self):
        source = dict(self.source, cards_path=None, cards_error='pages_artifact_missing')
        with patch('operations.fetch_latest', return_value=source):
            result = operations.cycle('send', self.config, self.state, self.settings)
        self.assertEqual((result['status'], result['sent_messages'], result['events']), ('completed', 1, 2))
        self.assertIn('pages_artifact_missing', result['cards_warning'])
        visible = json.dumps(self.sent[0], ensure_ascii=False)
        self.assertIn('대시보드 판정(cards.json) 없음 — 기존 규칙으로 발송 (pages_artifact_missing)', visible)
        self.assertIn('pages_artifact_missing', (self.state / 'send-preview.txt').read_text(encoding='utf-8'))
        self.assertIn('pages_artifact_missing', json.loads((self.state / 'send-result.json').read_text(encoding='utf-8'))['report']['notice'])

    def test_fallback_still_stops_alerts_eight_days_after_the_deadline(self):
        # The fixed clock is 2026-10-28: a deadline on 10-21 is 7 days past (sent), 10-20 is 8 days past (not sent).
        self.document['events'][0]['dates']['effective_from'] = '2026-10-21'
        self.document['events'][1]['dates']['effective_from'] = '2026-10-20'
        self.source_file.write_text(json.dumps(self.document), encoding='utf-8')
        with patch('operations.fetch_latest', return_value=dict(self.source, cards_path=None, cards_error='pages_artifact_missing')):
            result = operations.cycle('send', self.config, self.state, self.settings)
        self.assertEqual((result['sent_messages'], result['events']), (1, 1))
        with closing(connect(self.state / 'alerts.sqlite')) as db:
            delivered = [row[0] for row in db.execute('SELECT event_id FROM delivered_events')]
        self.assertEqual(delivered, ['demo-input'])

    def test_unreadable_or_invalid_cards_json_falls_back_and_names_the_reason(self):
        self.seed()
        cases = [(self.cards_source(content='{broken', filename='broken.json'), 'unreadable_cards'),
                 (self.cards_source(lambda document: document.update(schema=2), filename='schema.json'), 'invalid_cards:schema'),
                 (self.cards_source(lambda document: document['cards'][1]['events'].append('demo-input'), filename='twice.json'),
                  'invalid_cards:duplicate_event')]
        for source, reason in cases:
            with self.subTest(reason), patch('operations.fetch_latest', return_value=source):
                result = operations.cycle('preview', self.config, self.state, self.settings)
                self.assertIn(reason, result['cards_warning'])
                self.assertEqual(result['events'], 2)

    def test_failed_collection_download_also_reports_that_no_judgement_was_used(self):
        self.seed()
        with patch('operations.fetch_latest', side_effect=SourceError('network')):
            result = operations.cycle('preview', self.config, self.state, self.settings)
        self.assertEqual(result['source_error'], 'network')
        self.assertIn('no_source', result['cards_warning'])

    def test_cards_json_travels_from_the_pages_artifact_to_the_message(self):
        """The real fetch_latest, cache and cycle with only GitHub faked: what github_source returns is what the cycle reads."""
        cards = json.loads((ROOT / 'fixtures_cards.json').read_text(encoding='utf-8'))
        site = [('index.html', b'<!doctype html>'), ('cards.json', json.dumps(cards, ensure_ascii=False).encode('utf-8'))]
        self.config['dashboard_url'] = 'https://dashboard.example/LETSUR/'
        for label, pages, warning in (('with the Pages artifact', pages_archive(site), None),
                                      ('without it', None, 'pages_artifact_missing')):
            with self.subTest(label):
                client = FakeClient([run(42)], blob=archive(42, events=self.document['events']), pages=pages)

                def fetch(cache_dir, token, max_age_hours):  # a cache of its own: a cached cards.json would be used as it is
                    return real_fetch_latest(self.state / ('github-cache-' + str(bool(pages))), token, client=client,
                                             now=GITHUB_NOW, max_age_hours=max_age_hours)

                with patch('operations.fetch_latest', fetch):
                    result = operations.cycle('preview', self.config, self.state, self.settings)
                self.assertEqual(result['events'], 2)
                if warning:
                    self.assertIn(warning, result['cards_warning'])
                else:
                    self.assertIsNone(result['cards_warning'])
                report = json.loads((self.state / 'preview-result.json').read_text(encoding='utf-8'))['report']
                self.assertEqual(report['cards'][0]['items'][0]['dashboard_card'], 'g_0000000000a1' if pages else None)
                self.assertEqual(report['names'].get('items', {}).get('input'), '입력 토큰' if pages else None)


if __name__ == '__main__':
    unittest.main()
