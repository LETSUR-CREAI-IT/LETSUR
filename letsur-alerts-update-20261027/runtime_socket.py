"""Authenticated Socket Mode receipt with durable acknowledgement and UI repair."""
import json
import logging
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread

from alerts import connect, digest
from delivery import setup
from slack_adapter import ACTION_ID, handle_authenticated_payload


def open_existing(path):
    db = sqlite3.connect(path, timeout=0.5)
    db.row_factory = sqlite3.Row
    return db


def initialize(path):
    with closing(connect(path)) as db:
        setup(db)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS slack_display_state (
            job_id TEXT PRIMARY KEY, displayed_signature TEXT NOT NULL)''')
        db.commit()


def render_confirmations(db, original):
    result = json.loads(json.dumps(original))
    count = 0
    for index, block in enumerate(result.get('blocks', [])):
        if block.get('type') != 'actions':
            continue
        button = next((x for x in block.get('elements', []) if x.get('action_id') == ACTION_ID), None)
        if not button:
            continue
        row = db.execute('SELECT card_json FROM slack_ack_tokens WHERE token=?', (button.get('value'),)).fetchone()
        if not row:
            continue
        items = json.loads(row[0])['items']
        if items and all(db.execute('SELECT 1 FROM acknowledgements WHERE event_id=? AND signature=?',
                                    (item['event_id'], item['signature'])).fetchone() for item in items):
            result['blocks'][index] = {'type': 'section', 'text': {'type': 'plain_text',
                'text': '확인됨 · 이 버전의 공용 재알림 중단'}}
            count += 1
    if count:
        result['text'] = original['text'] + ' · 확인 상태 갱신'
    return result, count


def repair_displays(dbpath, api, channel):
    """Idempotent updates may be retried; immutable source payloads remain in outbox."""
    results = []
    with closing(open_existing(dbpath)) as db:
        rows = db.execute("SELECT id,payload,slack_ts FROM delivery_outbox WHERE channel=? AND state='sent'",
                          (channel,)).fetchall()
        for row in rows:
            original = json.loads(row['payload'])
            rendered, count = render_confirmations(db, original)
            if not count:
                continue
            signature = digest(rendered)
            old = db.execute('SELECT displayed_signature FROM slack_display_state WHERE job_id=?', (row['id'],)).fetchone()
            if old and old[0] == signature:
                continue
            try:
                api.call('chat.update', {'channel': channel, 'ts': row['slack_ts'],
                    'text': rendered['text'], 'blocks': rendered['blocks']})
            except Exception:
                results.append({'job_id': row['id'], 'status': 'display_retry_pending'})
                continue
            with db:
                db.execute('INSERT OR REPLACE INTO slack_display_state VALUES(?,?)', (row['id'], signature))
            results.append({'job_id': row['id'], 'status': 'display_updated'})
    return results


def process_payload(dbpath, payload, *, team_id, channel_id, app_id, now=None):
    """Call ONLY for a payload received over the authenticated Slack SDK connection."""
    now = now or datetime.now(timezone.utc)
    try:
        if not isinstance(payload, dict) or payload.get('api_app_id') != app_id:
            return 403, {'text': '허용된 앱의 요청이 아닙니다.'}
        ts = (payload.get('container') or {}).get('message_ts')
        with closing(open_existing(dbpath)) as db:
            row = db.execute("SELECT payload FROM delivery_outbox WHERE channel=? AND slack_ts=? AND state='sent'",
                             (channel_id, ts)).fetchone()
            if not row:
                return 403, {'text': '발송 기록을 확인할 수 없는 메시지입니다.'}
            actions = payload.get('actions') or []
            original = json.loads(row['payload'])
            allowed = {(e.get('action_id'), e.get('value')) for b in original.get('blocks', [])
                       for e in b.get('elements', []) if e.get('type') == 'button'}
            if len(actions) != 1 or (actions[0].get('action_id'), actions[0].get('value')) not in allowed:
                return 403, {'text': '이 메시지에 표시된 확인 버튼이 아닙니다.'}
            return handle_authenticated_payload(db, payload, team_id, channel_id, now)
    except sqlite3.OperationalError:
        return 503, {'text': '확인 기록이 잠시 사용 중입니다. 잠시 후 다시 눌러 주세요.'}
    except (KeyError, TypeError, ValueError, AttributeError):
        return 400, {'text': '잘못된 버튼 요청입니다.'}


def start_socket(dbpath, settings, api, *, app_id, log=None, repair_interval=30):
    """Authenticate and start receipt+repair workers; return a handle with close()."""
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / '.runtime'))
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk.web import WebClient
    token = settings.get('SLACK_APP_TOKEN', '')
    if not token.startswith('xapp-') or any(c.isspace() for c in token) or not app_id:
        raise ValueError('Socket Mode 설정이 필요합니다')
    api.authenticate(settings['SLACK_TEAM_ID'])
    initialize(dbpath)
    logger = logging.getLogger('letsur.socket.silent')
    logger.disabled = True
    log = log or (lambda value: None)
    stopping, repair = Event(), Event()
    client = SocketModeClient(app_token=token, logger=logger,
        web_client=WebClient(token=settings['SLACK_BOT_TOKEN'], logger=logger), concurrency=1)

    def receive(socket, request):
        if request.type != 'interactive':
            socket.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
            return
        status, response = process_payload(dbpath, request.payload, team_id=settings['SLACK_TEAM_ID'],
            channel_id=settings['SLACK_CHANNEL_ID'], app_id=app_id)
        socket.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id,
            payload=response if request.accepts_response_payload else None))
        log({'event': 'interaction', 'status': status})
        if status == 200:
            repair.set()

    def worker():
        while not stopping.is_set():
            try:
                for result in repair_displays(dbpath, api, settings['SLACK_CHANNEL_ID']):
                    log(result)
            except Exception:
                log({'event': 'display_repair_pending'})
            repair.wait(repair_interval)
            repair.clear()

    client.socket_mode_request_listeners.append(receive)
    try:
        client.connect()
    except Exception:
        client.close()
        raise RuntimeError('Socket Mode 연결 실패; 비밀값과 연결 주소는 출력하지 않았습니다') from None
    thread = Thread(target=worker, name='slack-display-repair', daemon=True)
    thread.start()
    log({'event': 'socket_connected'})

    class Handle:
        def close(self):
            stopping.set()
            repair.set()
            client.close()
            thread.join(timeout=25)

        def is_connected(self):
            return client.is_connected()

    return Handle()
