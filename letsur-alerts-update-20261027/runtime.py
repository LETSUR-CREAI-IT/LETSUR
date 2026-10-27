"""Persistent alert cycles. A sender must be explicitly supplied to send anything."""
import json
from contextlib import closing
from datetime import datetime, timezone

from alerts import connect, prepare, sync
from delivery import attempt, enqueue, pending_report, setup
from slack_adapter import message_batches


def _without_uncertain(db, report):
    """An interrupted/ambiguous send remains ambiguous after midnight or restart."""
    held = set()
    for row in db.execute("SELECT members FROM delivery_outbox WHERE channel=? AND state IN ('sending','unknown')",
                          (report['channel'],)):
        held.update(tuple(item) for item in json.loads(row['members']))
    result = json.loads(json.dumps(report))
    for card in result['cards']:
        card['items'] = [item for item in card['items'] if (item['event_id'], item['signature']) not in held]
    result['cards'] = [card for card in result['cards'] if card['items']]
    result['card_count'] = len(result['cards'])
    result['event_count'] = sum(len(card['items']) for card in result['cards'])
    return result, len(held)


def _without_staged(db, report):
    """A changed batch size must not bypass a staged job's Retry-After/attempt cap."""
    held = set()
    for row in db.execute("""SELECT members FROM delivery_outbox WHERE channel=? AND day=?
        AND state IN ('pending','failed','sending','unknown')""", (report['channel'], report['as_of'])):
        held.update(tuple(item) for item in json.loads(row['members']))
    result = json.loads(json.dumps(report))
    for card in result['cards']:
        card['items'] = [item for item in card['items'] if (item['event_id'], item['signature']) not in held]
    result['cards'] = [card for card in result['cards'] if card['items']]
    result['card_count'] = len(result['cards'])
    result['event_count'] = sum(len(card['items']) for card in result['cards'])
    return result


def run_cycle(dbpath, document, today, channel, sender=None, dashboard_url=None, urgent_days=5, *, now=None, notice=None, judged=None):
    """Sync and preview, or send with an injected sender(payload, job_id).

    Each call opens/closes its own connection. Confirmation and successful deliveries
    survive process restart. document=None reevaluates the existing state only.
    Previous-day known-unsent jobs are replaced by today's freshly rendered report;
    unknown sends are held for reconciliation rather than blindly repeated.
    judged is the run's cards.json as dashboard_cards.parse returns it; None sends by the bot's own rules only.
    """
    now = now or datetime.now(timezone.utc)
    if notice is not None and (not isinstance(notice, str) or len(notice) > 2000):
        raise ValueError('notice must be text of at most 2000 characters')
    outcomes = []
    with closing(connect(dbpath)) as db:
        setup(db)
        if document is not None:
            sync(db, document, today, urgent_days)
        initial = pending_report(db, prepare(db, today, channel, urgent_days, judged))
        preview, uncertain = _without_uncertain(db, initial)
        if notice:
            preview['notice'] = notice
        if sender is None:
            return {'mode': 'preview', 'report': preview, 'held_uncertain_versions': uncertain,
                    'outcomes': [], 'sent_messages': 0}
        with db:
            db.execute("""UPDATE delivery_outbox SET state='superseded', retryable=0, updated_at=?
                WHERE channel=? AND day<? AND (state='pending' OR (state='failed' AND retryable=1))""",
                       (now.isoformat(), channel, today.isoformat()))
        existing = db.execute("""SELECT id FROM delivery_outbox WHERE channel=? AND day=?
            AND (state='pending' OR (state='failed' AND retryable=1)) ORDER BY updated_at,id""",
                              (channel, today.isoformat())).fetchall()
        attempted = set()
        for row in existing:
            attempted.add(row['id'])
            outcomes.append({'job_id': row['id'], 'state': attempt(db, row['id'], sender, now)})
        fresh, uncertain = _without_uncertain(db, pending_report(db, prepare(db, today, channel, urgent_days, judged)))
        fresh = _without_staged(db, fresh)
        for payload, cards in message_batches(db, fresh, dashboard_url=dashboard_url, max_blocks=37 if notice else 40):
            if notice:
                payload['blocks'].insert(0, {'type': 'section', 'text': {'type': 'plain_text', 'text': notice}})
                payload['text'] = notice + '\n' + payload['text']
            job = enqueue(db, fresh, payload, cards, now)
            if job not in attempted:
                attempted.add(job)
                outcomes.append({'job_id': job, 'state': attempt(db, job, sender, now)})
        # Permanent failures stay visible to the scheduler; do not mark an unsent
        # source processed just because no further automatic attempt is permitted.
        for row in db.execute("SELECT id FROM delivery_outbox WHERE channel=? AND day=? AND state='failed' AND retryable=0",
                              (channel, today.isoformat())):
            if row['id'] not in attempted:
                outcomes.append({'job_id': row['id'], 'state': 'failed'})
        remaining, uncertain = _without_uncertain(db, pending_report(db, prepare(db, today, channel, urgent_days, judged)))
        return {'mode': 'send', 'report': preview, 'remaining': remaining,
                'held_uncertain_versions': uncertain, 'outcomes': outcomes,
                'sent_messages': sum(item['state'] == 'sent' for item in outcomes)}
