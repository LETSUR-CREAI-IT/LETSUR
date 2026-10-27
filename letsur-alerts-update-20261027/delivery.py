"""Durable delivery bookkeeping with an injected sender. No network implementation."""
import json
from datetime import datetime, timezone

from alerts import digest, encode


class DefiniteFailure(Exception):
    """Transport knows Slack did not accept this message (e.g. rate limiting)."""

    def __init__(self, message, retryable=False, retry_after_seconds=0):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = max(0, retry_after_seconds)


def setup(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS delivery_outbox (
        id TEXT PRIMARY KEY, channel TEXT NOT NULL, day TEXT NOT NULL,
        payload TEXT NOT NULL, members TEXT NOT NULL, state TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, retryable INTEGER NOT NULL DEFAULT 0,
        retry_at REAL NOT NULL DEFAULT 0, slack_ts TEXT, last_error TEXT,
        updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS delivery_attempts (
        id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, attempt INTEGER NOT NULL,
        outcome TEXT NOT NULL, happened_at TEXT NOT NULL, detail TEXT
      );
      CREATE TABLE IF NOT EXISTS delivered_events (
        channel TEXT NOT NULL, day TEXT NOT NULL, event_id TEXT NOT NULL,
        signature TEXT NOT NULL, job_id TEXT NOT NULL,
        PRIMARY KEY(channel, day, event_id, signature)
      );
      CREATE TABLE IF NOT EXISTS delivery_claims (
        channel TEXT NOT NULL, day TEXT NOT NULL, event_id TEXT NOT NULL,
        signature TEXT NOT NULL, job_id TEXT NOT NULL,
        PRIMARY KEY(channel,day,event_id,signature)
      );
    """)


def utc_now():
    return datetime.now(timezone.utc)


def pending_report(db, report):
    """Remove previously delivered versions, retaining daily reminder semantics."""
    setup(db)
    result = json.loads(encode(report))
    for card in result["cards"]:
        card["items"] = [item for item in card["items"] if not db.execute(
            "SELECT 1 FROM delivered_events WHERE channel=? AND day=? AND event_id=? AND signature=?",
            (report["channel"], report["as_of"], item["event_id"], item["signature"])
        ).fetchone()]
        for item in card["items"]:
            previous = db.execute("SELECT signature FROM delivered_events WHERE channel=? AND event_id=?",
                                  (report["channel"], item["event_id"])).fetchall()
            if any(row["signature"] != item["signature"] for row in previous):
                item["delivery_kind"] = "changed"
            elif previous:
                item["delivery_kind"] = "reminder"
    result["cards"] = [card for card in result["cards"] if card["items"]]
    result["card_count"] = len(result["cards"])
    result["event_count"] = sum(len(card["items"]) for card in result["cards"])
    return result


def enqueue(db, report, payload, cards, now=None):
    """Freeze exact payload and exact acknowledgement snapshots on first staging."""
    setup(db)
    now = now or utc_now()
    if not cards or payload.get("channel") != report["channel"]:
        raise ValueError("발송 대상과 채널을 확인하세요")
    members = sorted((item["event_id"], item["signature"]) for card in cards for item in card["items"])
    if len(members) != len(set(members)):
        raise ValueError("중복 발송 멤버")
    job_id = digest({"channel": report["channel"], "day": report["as_of"], "members": members})
    with db:
        db.execute("""INSERT OR IGNORE INTO delivery_outbox
          (id,channel,day,payload,members,state,updated_at) VALUES(?,?,?,?,?,'pending',?)""",
                   (job_id, report["channel"], report["as_of"], encode(payload), encode(members), now.isoformat()))
    return job_id


def attempt(db, job_id, sender, now=None, max_attempts=3):
    """Claim atomically, send once, then commit success. Unknown outcomes require reconciliation."""
    now = now or utc_now()
    setup(db)
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("SELECT * FROM delivery_outbox WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("발송 작업 없음")
        if row["state"] == "sent":
            db.commit()
            return "sent"
        if row["state"] in ("sending", "unknown"):
            db.commit()
            return "needs_reconciliation"
        if row["state"] == "failed" and not row["retryable"]:
            db.commit()
            return "failed"
        if row["attempts"] >= max_attempts:
            db.commit()
            return "exhausted"
        if row["retry_at"] > now.timestamp():
            db.commit()
            return "retry_later"
        # A staged report must not send content acknowledged or changed since staging.
        for identity, signature in json.loads(row["members"]):
            current = db.execute("SELECT signature,ack_signature FROM events WHERE id=?", (identity,)).fetchone()
            if current is None or current["signature"] != signature or current["ack_signature"] == signature:
                db.execute("UPDATE delivery_outbox SET state='superseded',retryable=0,updated_at=? WHERE id=?",
                           (now.isoformat(), job_id))
                db.commit()
                return "rebuild_required"
            if db.execute("SELECT 1 FROM delivered_events WHERE channel=? AND day=? AND event_id=? AND signature=?",
                          (row["channel"], row["day"], identity, signature)).fetchone():
                db.execute("UPDATE delivery_outbox SET state='superseded',retryable=0,updated_at=? WHERE id=?",
                           (now.isoformat(), job_id))
                db.commit()
                return "rebuild_required"
            owner = db.execute("SELECT job_id FROM delivery_claims WHERE channel=? AND day=? AND event_id=? AND signature=?",
                               (row["channel"], row["day"], identity, signature)).fetchone()
            if owner and owner["job_id"] != job_id:
                db.commit()
                return "needs_reconciliation"
        if row["state"] == "superseded":
            db.commit()
            return "rebuild_required"
        number = row["attempts"] + 1
        for identity, signature in json.loads(row["members"]):
            db.execute("INSERT OR IGNORE INTO delivery_claims VALUES(?,?,?,?,?)",
                       (row["channel"], row["day"], identity, signature, job_id))
        db.execute("UPDATE delivery_outbox SET state='sending',attempts=?,updated_at=? WHERE id=?",
                   (number, now.isoformat(), job_id))
        db.execute("INSERT INTO delivery_attempts(job_id,attempt,outcome,happened_at) VALUES(?,?,'started',?)",
                   (job_id, number, now.isoformat()))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    try:
        response = sender(json.loads(row["payload"]), job_id)
        if not isinstance(response, dict) or not isinstance(response.get("ts"), str) or not response["ts"]:
            raise RuntimeError("성공 응답에 메시지 식별자가 없음")
        if response.get("channel") != row["channel"]:
            raise RuntimeError("응답 채널 불일치")
    except DefiniteFailure as error:
        retry_at = now.timestamp() + max(error.retry_after_seconds, min(60, 2 ** number))
        with db:
            db.execute("DELETE FROM delivery_claims WHERE job_id=?", (job_id,))
            db.execute("""UPDATE delivery_outbox SET state='failed',retryable=?,retry_at=?,
                        last_error=?,updated_at=? WHERE id=?""",
                       (int(error.retryable), retry_at, str(error), now.isoformat(), job_id))
            db.execute("UPDATE delivery_attempts SET outcome='failed',detail=? WHERE job_id=? AND attempt=?",
                       (str(error), job_id, number))
        return "retry_later" if error.retryable and number < max_attempts else "failed"
    except Exception:
        # Do not persist exception text: transports may include credentials in errors.
        with db:
            db.execute("UPDATE delivery_outbox SET state='unknown',last_error='outcome_unknown',updated_at=? WHERE id=?",
                       (now.isoformat(), job_id))
            db.execute("UPDATE delivery_attempts SET outcome='unknown' WHERE job_id=? AND attempt=?", (job_id, number))
        return "needs_reconciliation"
    with db:
        db.execute("UPDATE delivery_outbox SET state='sent',slack_ts=?,last_error=NULL,updated_at=? WHERE id=?",
                   (response["ts"], now.isoformat(), job_id))
        for identity, signature in json.loads(row["members"]):
            db.execute("INSERT OR IGNORE INTO delivered_events VALUES(?,?,?,?,?)",
                       (row["channel"], row["day"], identity, signature, job_id))
        db.execute("UPDATE delivery_attempts SET outcome='sent' WHERE job_id=? AND attempt=?", (job_id, number))
    return "sent"
