import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from alerts import connect
from delivery import DefiniteFailure
from runtime import run_cycle
from runtime_socket import initialize, process_payload, repair_displays

ROOT = Path(__file__).resolve().parent
DAY = date(2026, 10, 27)
NOW = datetime(2026, 10, 27, 3, tzinfo=timezone.utc)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'output').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'output')
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / 'state.sqlite')
        self.document = json.loads((ROOT / 'fixtures.json').read_text(encoding='utf-8'))
        self.document['events'] = self.document['events'][:2]
        self.calls = []

    def send(self, payload, identity):
        self.calls.append(identity)
        return {'channel': payload['channel'], 'ts': '100.' + str(len(self.calls))}

    def interaction(self):
        with closing(connect(self.db)) as db:
            row = db.execute("SELECT payload,slack_ts FROM delivery_outbox WHERE state='sent' LIMIT 1").fetchone()
            original = json.loads(row['payload'])
            button = next(b['elements'][0] for b in original['blocks'] if b['type'] == 'actions')
            return {'type': 'block_actions', 'api_app_id': 'ATEST', 'team': {'id': 'TTEST'},
                    'channel': {'id': 'CTEST'}, 'user': {'id': 'UTEST'},
                    'container': {'message_ts': row['slack_ts']}, 'actions': [button]}

    def ack(self, payload=None):
        initialize(self.db)
        return process_payload(self.db, payload or self.interaction(), team_id='TTEST',
            channel_id='CTEST', app_id='ATEST', now=NOW)

    def test_preview_never_stages_delivery(self):
        result = run_cycle(self.db, self.document, DAY, 'CTEST')
        self.assertEqual(result['report']['event_count'], 2)
        with closing(connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM delivery_outbox').fetchone()[0], 0)

    def test_notice_is_stored_with_payload_and_safe_plain_text(self):
        notice = '수집 갱신 실패 · 기존 미확인 사건 재알림 <!channel>'
        result = run_cycle(self.db, self.document, DAY, 'CTEST', self.send, notice=notice)
        self.assertEqual(result['report']['notice'], notice)
        with closing(connect(self.db)) as db:
            payload = json.loads(db.execute('SELECT payload FROM delivery_outbox').fetchone()[0])
        self.assertEqual(payload['blocks'][0]['text'], {'type': 'plain_text', 'text': notice})
        self.assertLessEqual(len(payload['blocks']), 40)

    def test_subprocess_restart_preserves_send_and_ack(self):
        document = str(Path(self.temp.name) / 'events.json')
        Path(document).write_text(json.dumps(self.document), encoding='utf-8')
        receipts = str(Path(self.temp.name) / 'accepts.txt')
        script = '''import sys,json
from pathlib import Path
from datetime import date
from runtime import run_cycle
def sender(payload,identity):
    with Path(sys.argv[3]).open('a') as f: f.write(identity+'\\n')
    return {'channel':payload['channel'],'ts':'100.1'}
print(json.dumps(run_cycle(sys.argv[1],json.loads(Path(sys.argv[2]).read_text()),date.fromisoformat(sys.argv[4]),'CTEST',sender)))
'''
        def cycle(day):
            return subprocess.run([sys.executable, '-c', script, self.db, document, receipts, day],
                cwd=ROOT, capture_output=True, text=True, check=True)
        cycle('2026-10-27')
        cycle('2026-10-27')
        self.assertEqual(len(Path(receipts).read_text().splitlines()), 1)
        self.assertEqual(self.ack()[0], 200)
        cycle('2026-10-28')
        self.assertEqual(len(Path(receipts).read_text().splitlines()), 1)
        with closing(connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM acknowledgements').fetchone()[0], 2)

    def test_process_death_after_remote_acceptance_does_not_resend(self):
        document = str(Path(self.temp.name) / 'events.json')
        Path(document).write_text(json.dumps(self.document), encoding='utf-8')
        script = '''import sys,json,os
from pathlib import Path
from datetime import date
from runtime import run_cycle
def sender(payload,identity): os._exit(7)
run_cycle(sys.argv[1],json.loads(Path(sys.argv[2]).read_text()),date(2026,10,27),'CTEST',sender)
'''
        result = subprocess.run([sys.executable, '-c', script, self.db, document], cwd=ROOT)
        self.assertEqual(result.returncode, 7)
        for day in (DAY, DAY + timedelta(days=1)):
            result = run_cycle(self.db, self.document, day, 'CTEST', self.send)
            self.assertEqual(result['held_uncertain_versions'], 2)
        self.assertEqual(self.calls, [])

    def test_retry_after_and_new_event_do_not_bypass_staged_wait(self):
        failures = []
        def throttled(payload, identity):
            failures.append(identity)
            raise DefiniteFailure('rate_limited', retryable=True, retry_after_seconds=60)
        run_cycle(self.db, self.document, DAY, 'CTEST', throttled, now=NOW)
        extra = dict(self.document['events'][0], event_id='new', model_id='new')
        self.document['events'].append(extra)
        run_cycle(self.db, self.document, DAY, 'CTEST', self.send, now=NOW + timedelta(seconds=1))
        self.assertEqual(len(self.calls), 1)
        with closing(connect(self.db)) as db:
            delivered = [r[0] for r in db.execute('SELECT event_id FROM delivered_events')]
        self.assertEqual(delivered, ['new'])
        run_cycle(self.db, self.document, DAY, 'CTEST', self.send, now=NOW + timedelta(seconds=61))
        self.assertEqual(len(self.calls), 2)

    def test_permanent_failure_stays_visible_without_blind_retry(self):
        def fail(payload, identity):
            raise DefiniteFailure('not_in_channel')
        run_cycle(self.db, self.document, DAY, 'CTEST', fail)
        result = run_cycle(self.db, self.document, DAY, 'CTEST', self.send)
        self.assertEqual(result['outcomes'][0]['state'], 'failed')
        self.assertEqual(self.calls, [])

    def test_ack_scope_and_stale_version(self):
        run_cycle(self.db, self.document, DAY, 'CTEST', self.send)
        payload = self.interaction()
        for key in ('api_app_id',):
            wrong = dict(payload, **{key: 'WRONG'})
            self.assertEqual(self.ack(wrong)[0], 403)
        wrong = dict(payload, container={'message_ts': '999.1'})
        self.assertEqual(self.ack(wrong)[0], 403)
        wrong = dict(payload, channel={'id': 'COTHER'})
        self.assertEqual(self.ack(wrong)[0], 403)
        self.document['events'][0]['after']['amount'] = '3'
        run_cycle(self.db, self.document, DAY, 'CTEST')
        self.assertEqual(self.ack(payload)[0], 409)
        with closing(connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM acknowledgements').fetchone()[0], 0)

    def test_display_update_failure_preserves_ack_and_repairs_once(self):
        run_cycle(self.db, self.document, DAY, 'CTEST', self.send)
        self.assertEqual(self.ack()[0], 200)
        class API:
            calls = 0
            def call(_, method, payload):
                _.calls += 1
                if _.calls == 1:
                    raise TimeoutError('must not leak')
                self.assertEqual(method, 'chat.update')
                self.assertFalse(any(b['type'] == 'actions' for b in payload['blocks']))
        api = API()
        self.assertEqual(repair_displays(self.db, api, 'CTEST')[0]['status'], 'display_retry_pending')
        self.assertEqual(run_cycle(self.db, self.document, DAY + timedelta(days=1), 'CTEST')['report']['card_count'], 0)
        self.assertEqual(repair_displays(self.db, api, 'CTEST')[0]['status'], 'display_updated')
        self.assertEqual(repair_displays(self.db, api, 'CTEST'), [])
        self.assertEqual(api.calls, 2)

    def test_unconfirmed_daily_and_material_change(self):
        run_cycle(self.db, self.document, DAY, 'CTEST', self.send)
        run_cycle(self.db, self.document, DAY + timedelta(days=1), 'CTEST', self.send)
        self.assertEqual(len(self.calls), 2)
        # Use same second day for acknowledgement; avoid logical time regression.
        initialize(self.db)
        self.assertEqual(process_payload(self.db, self.interaction(), team_id='TTEST', channel_id='CTEST',
            app_id='ATEST', now=NOW + timedelta(days=1))[0], 200)
        self.document['events'][0]['after']['amount'] = '3'
        changed = run_cycle(self.db, self.document, DAY + timedelta(days=1), 'CTEST', self.send)
        self.assertEqual(changed['report']['event_count'], 1)
        self.assertEqual(len(self.calls), 3)


if __name__ == '__main__':
    unittest.main()
