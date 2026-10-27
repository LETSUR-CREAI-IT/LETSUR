"""Local operating commands. `send` and `watch` can publish market alerts to Slack."""
import argparse
import json
import logging
import os
import sys
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from alerts import connect, render
from github_credentials import github_token
from github_source import fetch_latest, SourceError
from runtime import run_cycle
from slack_adapter import KST
from slack_connect import SlackClient, load_settings

ROOT = Path(__file__).resolve().parent


def atomic_json(path, data):
    temporary = path.with_suffix('.tmp-' + str(os.getpid()))
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def configuration():
    config = json.loads((ROOT / 'operations.json').read_text(encoding='utf-8'))
    state = (ROOT / config['state_directory']).resolve()
    if not state.is_relative_to(ROOT):
        raise ValueError('state_directory must stay within the project')
    state.mkdir(parents=True, exist_ok=True)
    return config, state


def status(state):
    result = {'listener_running': False}
    locked = False
    lockpath = state / 'listener.lock'
    if lockpath.exists():
        import msvcrt
        with lockpath.open('r+b') as handle:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                locked = True
    path = state / 'listener.json'
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
        # A recent live heartbeat plus connectivity is evidence, a stale PID is not.
        age = time.time() - data['heartbeat_epoch']
        result.update({'listener_running': locked and age < 20 and data.get('connected', False),
                       'listener_pid': data.get('pid'), 'heartbeat_age_seconds': round(age, 1)})
    dbpath = state / 'alerts.sqlite'
    if dbpath.exists():
        with closing(connect(dbpath)) as db:
            result['events'] = db.execute('SELECT count(*) FROM events').fetchone()[0]
            result['acknowledged'] = db.execute('SELECT count(*) FROM events WHERE ack_signature=signature').fetchone()[0]
    return result


def listen(config, state, settings):
    import msvcrt
    from runtime_socket import start_socket
    logging.disable(logging.CRITICAL)
    # OS byte-range lock is released on exit/crash; lock-file presence is irrelevant.
    lock = (state / 'listener.lock').open('a+b')
    lock.seek(0)
    if lock.read(1) == b'':
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return {'status': 'listener_already_running'}

    def log(data):
        with (state / 'listener.log').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), **data}, ensure_ascii=False) + '\n')

    socket = None
    try:
        while True:
            if socket is None:
                try:
                    socket = start_socket(state / 'alerts.sqlite', settings,
                        SlackClient(settings['SLACK_BOT_TOKEN']), app_id=config['app_id'], log=log)
                except Exception:
                    log({'event': 'connection_retry_in_30_seconds'})
                    time.sleep(30)
                    continue
            atomic_json(state / 'listener.json', {'pid': os.getpid(), 'heartbeat_epoch': time.time(),
                                                'connected': socket.is_connected()})
            time.sleep(5)
    finally:
        if socket:
            socket.close()
        atomic_json(state / 'listener.json', {'pid': os.getpid(), 'heartbeat_epoch': time.time(), 'connected': False})
        lock.close()


def cycle(command, config, state, settings):
    clock = datetime.now(KST)
    today = clock.date()
    fallback_due = clock.strftime('%H:%M') >= config.get('missing_collection_fallback_kst', '23:00')
    document, source = None, None
    source_error = None
    try:
        source = fetch_latest(ROOT / 'output' / 'github-cache', github_token(),
                              max_age_hours=config['max_source_age_hours'])
        atomic_json(state / 'source-status.json', source)
        if source['fresh']:
            document = json.loads(Path(source['events_path']).read_text(encoding='utf-8'))
        else:
            source_error = ','.join(source['stale_reasons'])
    except SourceError as error:
        source_error = error.code
    source_key = None if source is None else '-'.join(str(source['metadata'][key])
                    for key in ('run_id', 'run_attempt', 'artifact_id'))
    marker_path = state / 'processed-source.json'
    if command == 'watch' and not source_error and marker_path.exists():
        marker = json.loads(marker_path.read_text(encoding='utf-8'))
        if marker.get('source_key') == source_key:
            if marker.get('processed_day') == today.isoformat() or not fallback_due:
                return {'status': 'no_new_collection', 'run_id': source['metadata']['run_id'], 'sent_messages': 0}
            document, source_error = None, 'no_collection_today'
    if command == 'watch' and source_error and 'newer_run_pending' in source_error and not fallback_due:
        return {'status': 'collection_in_progress', 'sent_messages': 0}
    notice = '테스트 워크스페이스 · 공개 수집 자료'
    if source_error:
        notice += ' · 수집 갱신 실패/지연 — 기존 미확인 사건 재알림'
    elif source:
        created = datetime.fromisoformat(source['metadata']['run_created_at'].replace('Z', '+00:00')).astimezone(KST)
        notice += ' · 수집 ' + created.strftime('%Y-%m-%d %H:%M KST')
    dbpath = state / ('preview.sqlite' if command == 'preview' else 'alerts.sqlite')
    live = state / 'alerts.sqlite'
    if document is None:
        existing_events = 0
        if live.exists():
            with closing(connect(live)) as db:
                existing_events = db.execute('SELECT count(*) FROM events').fetchone()[0]
        if not existing_events:
            return {'status': 'source_unavailable', 'source_error': source_error, 'sent_messages': 0}
    if command == 'preview':
        # Preview current confirmations/delivery history without changing the live DB.
        with tempfile.TemporaryDirectory(dir=state) as temporary:
            snapshot = Path(temporary) / 'snapshot.sqlite'
            with closing(connect(snapshot)) as destination:
                if live.exists():
                    with closing(sqlite3.connect(live)) as origin:
                        origin.backup(destination)
                destination.execute('PRAGMA journal_mode=DELETE')
            os.replace(snapshot, dbpath)
    sender = None
    if command in ('send', 'watch'):
        if not status(state)['listener_running']:
            return {'status': 'listener_required', 'sent_messages': 0}
        api = SlackClient(settings['SLACK_BOT_TOKEN'])
        api.authenticate(settings['SLACK_TEAM_ID'])
        sender = api.send
    result = run_cycle(dbpath, document, today, settings['SLACK_CHANNEL_ID'], sender,
        config.get('dashboard_url'), config['urgent_days'], notice=notice)
    report = result['report']
    atomic_json(state / (command + '-result.json'), result)
    (state / (command + '-preview.txt')).write_text(notice + '\n' + render(report), encoding='utf-8')
    with closing(connect(dbpath)) as db:
        total = db.execute('SELECT count(*) FROM events').fetchone()[0]
    needs_attention = bool(source_error or result['held_uncertain_versions'] or
                           any(x['state'] != 'sent' for x in result['outcomes']))
    if command in ('send', 'watch') and not needs_attention and source_key:
        atomic_json(marker_path, {'source_key': source_key, 'processed_day': today.isoformat(),
                                 'processed_at': datetime.now(timezone.utc).isoformat()})
    return {'status': 'needs_attention' if needs_attention else 'completed', 'source_error': source_error,
            'run_id': source['metadata']['run_id'] if source else None,
            'source_events': total, 'cards': report['card_count'], 'events': report['event_count'],
            'sent_messages': result['sent_messages'], 'outcomes': result['outcomes'],
            'held_uncertain_versions': result['held_uncertain_versions']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preview', 'send', 'watch', 'listen', 'status'])
    args = parser.parse_args()
    config, state = configuration()
    if args.command == 'status':
        result = status(state)
    else:
        settings = load_settings(ROOT / '.env')
        result = listen(config, state, settings) if args.command == 'listen' else cycle(args.command, config, state, settings)
    print(json.dumps(result, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never log library exception strings: they may include secrets or signed URLs.
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(1)
