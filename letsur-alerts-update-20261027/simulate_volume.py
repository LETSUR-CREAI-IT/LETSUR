"""Offline volume replay using supplied snapshots; never opens credentials or network."""
import argparse
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile

from alerts import acknowledge, connect, deadline, event_eligible
from runtime import run_cycle

ROOT = Path(__file__).resolve().parent
CHANNEL = 'C_OFFLINE_VOLUME'


def classify(document, today):
    categories = Counter()
    for event in document['events']:
        day, _, _ = deadline(event)
        if not event_eligible(event):
            categories['quality_gate_excluded'] += 1
        elif day is None:
            categories['eligible_but_no_deadline'] += 1
        elif (day - today).days > 5:
            categories['eligible_deadline_after_five_days'] += 1
        elif day < today:
            categories['eligible_overdue'] += 1
        else:
            categories['eligible_due_within_five_days'] += 1
    return {'total': len(document['events']), 'by_status': dict(Counter(e.get('status') for e in document['events'])),
            'by_type': dict(Counter(e.get('event_type') for e in document['events'])),
            'classification': dict(categories)}


def scenario(snapshots, folder, *, ack_after_first=False, future_days=4):
    database = folder / ('ack-all.sqlite' if ack_after_first else 'unacknowledged.sqlite')
    rows = []
    sent_payloads = []
    def fake_sender(payload, job):
        sent_payloads.append(payload)
        return {'channel': payload['channel'], 'ts': 'offline.' + str(len(sent_payloads))}

    timeline = [(day, document, 'actual_snapshot') for day, document, _ in snapshots]
    last_day, last_document, _ = snapshots[-1]
    timeline += [(last_day + timedelta(days=i), None, 'frozen_input_scenario')
                 for i in range(1, future_days + 1)]
    for index, (day, document, kind) in enumerate(timeline):
        now = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        first = run_cycle(database, document, day, CHANNEL, fake_sender, now=now,
                          notice='오프라인 재생 · 실제 Slack 발송 없음')
        again = run_cycle(database, document, day, CHANNEL, fake_sender, now=now,
                          notice='오프라인 재생 · 실제 Slack 발송 없음')
        row = {'day': day.isoformat(), 'evidence_kind': kind,
               'candidate_cards': first['report']['card_count'], 'candidate_events': first['report']['event_count'],
               'simulated_messages': first['sent_messages'], 'same_day_repeat_messages': again['sent_messages']}
        rows.append(row)
        if index == 0 and ack_after_first:
            with closing(connect(database)) as db:
                for card in first['report']['cards']:
                    acknowledge(db, card, 'OFFLINE_TESTER', day, timestamp=now)
    return {'rows': rows, 'simulated_message_total': len(sent_payloads), 'actual_slack_calls': 0}


def synthetic_capacity(document, day, folder, count=100):
    template = next(e for e in document['events']
                    if event_eligible(e) and deadline(e)[0] and (deadline(e)[0] - day).days <= 5)
    events = []
    for number in range(count):
        event = deepcopy(template)
        event['event_id'] = f'synthetic-capacity-{number}'
        event['model_id'] = f'[합성 부하 시험] 모델 {number}'
        events.append(event)
    parts = []
    def fake_sender(payload, job):
        parts.append(sum(b.get('type') == 'actions' for b in payload['blocks']))
        return {'channel': payload['channel'], 'ts': 'synthetic.' + str(len(parts))}
    result = run_cycle(folder / 'capacity.sqlite', {'events': events}, day, CHANNEL, fake_sender,
                       notice='합성 부하 시험 · 실제 시장 발생량이 아님')
    return {'evidence_kind': 'synthetic_capacity_not_market_forecast', 'cards': result['report']['card_count'],
            'simulated_messages': result['sent_messages'], 'displayed_parts_per_message': parts,
            'daily_message_cap_exists': False, 'actual_slack_calls': 0}


def known_schedule_growth(document, day, folder):
    """Advance only to dates already explicit in the last actual snapshot."""
    boundaries = sorted({deadline(e)[0] - timedelta(days=5) for e in document['events']
                         if event_eligible(e) and deadline(e)[0] and deadline(e)[0] - timedelta(days=5) > day})
    rows = []
    calls = []
    def fake_sender(payload, job):
        calls.append(job)
        return {'channel': payload['channel'], 'ts': 'schedule.' + str(len(calls))}
    database = folder / 'known-schedule.sqlite'
    for index, current in enumerate([day] + boundaries):
        result = run_cycle(database, document if index == 0 else None, current, CHANNEL, fake_sender,
                           notice='최종 자료 고정 · 이미 명시된 미래 일정 진입 시나리오')
        rows.append({'day': current.isoformat(), 'cards': result['report']['card_count'],
                     'events': result['report']['event_count'], 'simulated_messages': result['sent_messages']})
    known = Counter((str(deadline(e)[0]), e.get('event_type')) for e in document['events']
                    if event_eligible(e) and deadline(e)[0] and deadline(e)[0] > day + timedelta(days=5))
    return {'evidence_kind': 'frozen_input_known_schedules_not_actual_future_runs', 'rows': rows,
            'already_stated_future_events': [{'deadline': key[0], 'event_type': key[1], 'events': count}
                                            for key, count in sorted(known.items())],
            'actual_slack_calls': 0}


def render_report(result):
    lines = ['# 실제 자료 기반 알림량 검증', '',
             f"실제 자료 {len(result['snapshots'])}시점의 재생과 이후 동일 입력 유지 시나리오를 구분한다. 모든 전송은 가짜 전송 함수이며 실제 Slack 호출은 0회이다.", '',
             '| 자료 날짜 | 전체 이벤트 | confirmed | candidate | 기한 지난 발송 적격 이벤트 |',
             '|---|---:|---:|---:|---:|']
    for row in result['snapshots']:
        counts = row['counts']
        lines.append(f"| {row['day']} | {counts['total']} | {counts['by_status'].get('confirmed', 0)} | {counts['by_status'].get('candidate', 0)} | {counts['classification'].get('eligible_overdue', 0)} |")
    lines += ['', '| 기준일 | 입력 근거 | 전부 미확인: 카드/이벤트/메시지 | 첫날 전부 확인 후 메시지 | 같은 날 재실행 메시지 |',
              '|---|---|---|---:|---:|']
    for left, right in zip(result['unacknowledged']['rows'], result['acknowledged_after_first']['rows']):
        kind = '실제 해당 날짜 자료' if left['evidence_kind'] == 'actual_snapshot' else '최종 자료 고정 가정'
        lines.append(f"| {left['day']} | {kind} | {left['candidate_cards']}/{left['candidate_events']}/{left['simulated_messages']} | {right['simulated_messages']} | {left['same_day_repeat_messages']} |")
    capacity = result['synthetic_capacity']
    lines += ['', '판단: 확인한 실제 자료에서는 3카드를 묶은 1메시지 수준이므로 현재 알림량은 적다. ',
              '다만 두 시점만으로 장기 발생량을 보장할 수 없다. 미확인 사건을 자동 만료하지 않으므로 쌓이면 매일 재알림 목록도 늘어난다.',
              f"합성 부하 시험에서는 서로 다른 긴급 카드 {capacity['cards']}개가 {capacity['simulated_messages']}메시지로 분할됐다. 일반적인 짧은 카드는 출처 안내 포함 메시지당 12개이며 일일 총량 상한은 없다. 긴 카드는 추가 분할될 수 있다.", '',
              '날짜가 없는 가격 변경·모델 출시·경로 추가는 이 긴급 기준에서 제외되므로 적은 알림량을 전체 사건이 적다는 뜻으로 해석하면 안 된다.',
              '미확인 상태의 미래 결과는 수집이 계속 같은 내용을 제공하거나 기존 DB를 재평가한다는 가정이다. 실제 미래 수집 결과나 미래 예약 실행 성공을 측정한 것이 아니다.', '']
    lines += ['## 이미 자료에 적힌 미래 일정까지 유지한 경우', '',
              '최종 실제 입력에 이미 적힌 미래 일정만 사용한다. 새 수집·확인·취소·정정이 전혀 없다는 가정으로 D-5 진입일을 계산했다.', '',
              '| 가정 기준일 | 미확인 카드 | 이벤트 | 모의 메시지 |', '|---|---:|---:|---:|']
    for row in result['known_schedule_growth']['rows']:
        lines.append(f"| {row['day']} | {row['cards']} | {row['events']} | {row['simulated_messages']} |")
    lines += ['', '현재 1메시지라는 결과가 향후 항상 1메시지라는 보장은 아니다. 위 수치는 이미 확인된 일정만 계산했으며 시장 발생량 예측은 아니다.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', required=True, metavar='YYYY-MM-DD=EVENTS_PATH')
    parser.add_argument('--future-days', type=int, default=4)
    parser.add_argument('--output', default='output/share-volume-report')
    args = parser.parse_args()
    snapshots = []
    metadata = []
    for value in args.source:
        raw_day, separator, raw_path = value.partition('=')
        if not separator:
            parser.error('--source requires YYYY-MM-DD=EVENTS_PATH')
        day, path = date.fromisoformat(raw_day), Path(raw_path)
        raw = path.read_bytes()
        document = json.loads(raw)
        snapshots.append((day, document, path))
        metadata.append({'day': day.isoformat(), 'filename': path.name,
                         'sha256': hashlib.sha256(raw).hexdigest(), 'counts': classify(document, day)})
    snapshots.sort(key=lambda row: row[0])
    metadata.sort(key=lambda row: row['day'])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        folder = Path(temporary)
        result = {'mode': 'OFFLINE_ONLY', 'snapshots': metadata,
                  'unacknowledged': scenario(snapshots, folder, future_days=args.future_days),
                  'acknowledged_after_first': scenario(snapshots, folder, ack_after_first=True, future_days=args.future_days),
                  'synthetic_capacity': synthetic_capacity(snapshots[-1][1], snapshots[-1][0], folder),
                  'known_schedule_growth': known_schedule_growth(snapshots[-1][1], snapshots[-1][0], folder)}
    output.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    output.with_suffix('.md').write_text(render_report(result), encoding='utf-8')
    print(json.dumps({'actual_slack_calls': 0, 'snapshots': metadata,
                      'unacknowledged': result['unacknowledged'],
                      'acknowledged_after_first': result['acknowledged_after_first'],
                      'synthetic_capacity': result['synthetic_capacity'],
                      'known_schedule_growth': result['known_schedule_growth']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
