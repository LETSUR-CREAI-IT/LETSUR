"""Offline alert prototype. Standard library only; never contacts Slack."""
import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dashboard_cards import name

# Decision 26-4 (2026-10-27): an unacknowledged alert is last sent 7 days after its deadline, from the 8th day on it stops.
OVERDUE_DAYS = 7
# 2026-10-28 가안: a change first seen more than 7 days ago gets no new-change alert, so a new rule or a fresh database
# does not send old changes all at once.
NEW_DAYS = 7
KST = timezone(timedelta(hours=9))


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def numeric_identity(value):
    """Formatting-only differences such as 1.0 versus 1 are not new facts."""
    if value is None or isinstance(value, bool):
        return value
    try:
        number = Decimal(str(value))
        return format(number.normalize(), "f") if number.is_finite() else value
    except (InvalidOperation, ValueError):
        return value


def material(event):
    """Exclude run/observation IDs and editorial notes from acknowledgement identity."""
    result = {key: event.get(key) for key in (
        "event_type", "model_id", "route", "billing_item", "conditions", "status", "subtags"
    )}
    result["subtags"] = sorted(event.get("subtags") or [])
    for side in ("before", "after"):
        value = event.get(side) or {}
        result[side] = {k: value.get(k) for k in (
            "amount", "currency", "unit", "effective_from", "effective_until"
        )}
        result[side]["amount"] = numeric_identity(value.get("amount"))
    result["change"] = {k: v for k, v in (event.get("change") or {}).items() if k != "note"}
    for key in ("pct", "delta", "discount_pct", "discounted_amount"):
        if key in result["change"]:
            result["change"][key] = numeric_identity(result["change"][key])
    dates = event.get("dates") or {}
    result["dates"] = {k: dates.get(k) for k in (
        "effective_from", "current_rate_until", "end_date", "end_phrase",
        "retirement_date", "removal_date"
    )}
    end = event.get("period_end") or {}
    result["period_end"] = {k: end.get(k) for k in ("date", "basis")}
    result["conflicts"] = event.get("conflicts") or []
    return result


def deadline(event):
    """Use only event schedule fields, never collection/publication dates."""
    dates = event.get("dates") or {}
    kind = event.get("event_type")
    if kind == "limited_discount":
        end = event.get("period_end") or {}
        raw = end.get("date") or dates.get("end_date")
        basis = end.get("basis") or ("stated" if raw else None)
        label = "할인 종료"
    elif kind in ("scheduled_change", "condition_change"):
        raw = dates.get("effective_from") or (event.get("after") or {}).get("effective_from")
        basis, label = "stated", "적용"
    elif kind == "model_end":
        raw = dates.get("effective_from") or dates.get("end_date") or dates.get("retirement_date")
        basis, label = "stated", "모델 종료"
    elif kind == "route_change" and "remove" in (event.get("subtags") or []):
        raw = dates.get("effective_from") or dates.get("end_date") or dates.get("removal_date")
        basis, label = "stated", "경로 종료"
    else:
        return None, "일정", None
    try:
        return date.fromisoformat(raw) if raw else None, label, basis
    except (ValueError, TypeError):
        return None, label, None


def event_eligible(event):
    """A narrow warning path may notify an official, scheduled candidate."""
    if event.get("status") == "confirmed":
        return True
    if event.get("status") != "candidate" or not event.get("model_id"):
        return False
    if event.get("conflicts"):
        return False
    if not event.get("route") and event.get("event_type") != "model_end":
        return False
    day, _, basis = deadline(event)
    if day is None or basis != "stated":
        return False
    sources = event.get("sources") or []
    if not sources or any(source.get("authority") not in (
        "manufacturer_official", "channel_official"
    ) for source in sources):
        return False
    gaps = event.get("gaps") or []
    if not gaps:
        return False
    for gap in gaps:
        if gap.get("field") not in ("letsur_usage", "account_actual_price", "raw_check"):
            return False
        if gap.get("field") == "raw_check" and gap.get("reason") not in (
            "parser_fingerprint_mismatch", "signal_raw_unparsable"
        ):
            return False
    return True


def stage(event):
    """Place in LETSUR's order of judgement as the 3rd team's written report states it (2026-10-28 가안, LETSUR's own
    answer not seen yet): 0 selling stops (model end, route removal), 1 the cost changes, 2 anything else."""
    kind = event.get("event_type")
    if kind == "model_end" or (kind == "route_change" and "remove" in (event.get("subtags") or [])):
        return 0
    return 1 if kind in ("price_change", "scheduled_change", "limited_discount", "condition_price_change") else 2


def observed(event):
    """KST day a change was first seen: its first observation after the change, else the notice's publication day."""
    dates = event.get("dates") or {}
    raw = dates.get("observed_after_utc") or dates.get("published")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        if len(raw) == 10:
            return date.fromisoformat(raw)
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(KST).date()
    except ValueError:
        return None


def new_change(event, today):
    """2026-10-28 가안: a change sent once when it is first seen, before any deadline: a stop of selling the urgent
    path would warn about later, or a confirmed cost change without a schedule date (already happened, never urgent)."""
    if stage(event) == 0:
        wanted = event_eligible(event)
    else:
        wanted = stage(event) == 1 and event.get("status") == "confirmed" and deadline(event)[0] is None
    seen = observed(event)
    return wanted and seen is not None and 0 <= (today - seen).days <= NEW_DAYS


def delivered(db, channel, event_id, signature):
    """Whether this version of an event already went out on some day: a local preview marked sent, or a real Slack send
    (delivery.py's delivered_events, absent until delivery.setup runs)."""
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return any(db.execute(f"SELECT 1 FROM {table} WHERE channel=? AND event_id=? AND signature=?",
                          (channel, event_id, signature)).fetchone()
               for table in ("simulated_deliveries", "delivered_events") if table in tables)


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, payload TEXT NOT NULL, signature TEXT NOT NULL,
          urgent INTEGER NOT NULL DEFAULT 0, ack_signature TEXT,
          ack_user TEXT, ack_day TEXT, seen_day TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS history (
          id INTEGER PRIMARY KEY, event_id TEXT NOT NULL,
          signature TEXT NOT NULL, payload TEXT NOT NULL, observed_day TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS simulated_deliveries (
          channel TEXT NOT NULL, day TEXT NOT NULL, event_id TEXT NOT NULL,
          signature TEXT NOT NULL, PRIMARY KEY(channel,day,event_id,signature)
        );
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS acknowledgements (
          id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, signature TEXT NOT NULL,
          user TEXT NOT NULL, logical_day TEXT NOT NULL,
          acknowledged_at_utc TEXT, source TEXT NOT NULL
        );
    """)
    columns = {row["name"] for row in db.execute("PRAGMA table_info(events)")}
    if "ack_at_utc" not in columns:
        db.execute("ALTER TABLE events ADD COLUMN ack_at_utc TEXT")
    # Carry acknowledgement/delivery identity across formatting normalization.
    if not db.execute("SELECT 1 FROM metadata WHERE key='signature_schema'").fetchone():
        mapping = {}
        for row in db.execute("SELECT id,payload,signature FROM events").fetchall():
            new_signature = digest(material(json.loads(row["payload"])))
            mapping[(row["id"], row["signature"])] = new_signature
            db.execute("""UPDATE events SET signature=?,ack_signature=CASE
                WHEN ack_signature=? THEN ? ELSE ack_signature END WHERE id=?""",
                       (new_signature, row["signature"], new_signature, row["id"]))
        for row in db.execute("SELECT id,event_id,payload,signature FROM history").fetchall():
            new_signature = digest(material(json.loads(row["payload"])))
            mapping[(row["event_id"], row["signature"])] = new_signature
            db.execute("UPDATE history SET signature=? WHERE id=?", (new_signature, row["id"]))
        for (event_id, old_signature), new_signature in mapping.items():
            if old_signature == new_signature:
                continue
            db.execute("""INSERT OR IGNORE INTO simulated_deliveries(channel,day,event_id,signature)
                SELECT channel,day,event_id,? FROM simulated_deliveries WHERE event_id=? AND signature=?""",
                       (new_signature, event_id, old_signature))
            db.execute("DELETE FROM simulated_deliveries WHERE event_id=? AND signature=?",
                       (event_id, old_signature))
            db.execute("UPDATE acknowledgements SET signature=? WHERE event_id=? AND signature=?",
                       (new_signature, event_id, old_signature))
        db.execute("INSERT INTO metadata VALUES('signature_schema','2')")
    # Preserve existing acknowledgements, without inventing a clock time for them.
    if not db.execute("SELECT 1 FROM metadata WHERE key='ack_audit_migrated'").fetchone():
        db.execute("""INSERT INTO acknowledgements(event_id,signature,user,logical_day,acknowledged_at_utc,source)
            SELECT id,ack_signature,COALESCE(ack_user,'unknown'),ack_day,ack_at_utc,'legacy'
            FROM events WHERE ack_signature IS NOT NULL AND ack_day IS NOT NULL""")
        db.execute("INSERT INTO metadata VALUES('ack_audit_migrated','1')")
    # Old local files already contain dated observations/delivery records.
    previous = db.execute("""SELECT MAX(day) FROM (
        SELECT seen_day AS day FROM events UNION ALL SELECT ack_day FROM events
        UNION ALL SELECT observed_day FROM history UNION ALL SELECT day FROM simulated_deliveries
    )""").fetchone()[0]
    if previous:
        db.execute("""INSERT INTO metadata(key,value) VALUES('as_of',?)
            ON CONFLICT(key) DO UPDATE SET value=MAX(value,excluded.value)""", (previous,))
    db.commit()
    return db


def check_day(db, today):
    if type(today) is not date:
        raise ValueError("기준일은 date 값이어야 합니다")
    previous = db.execute("SELECT value FROM metadata WHERE key='as_of'").fetchone()
    if previous and today.isoformat() < previous["value"]:
        raise ValueError("기존 처리일보다 과거로 실행할 수 없습니다. 과거 재생에는 별도 DB를 사용하세요")


def remember_day(db, today):
    db.execute("""INSERT INTO metadata(key,value) VALUES('as_of',?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (today.isoformat(),))


def check_urgent_days(urgent_days):
    if type(urgent_days) is not int or urgent_days < 0:
        raise ValueError("urgent_days는 0 이상의 정수여야 합니다")


def validate_events(document):
    if not isinstance(document, dict) or not isinstance(document.get("events"), list):
        raise ValueError("events 배열이 있는 이벤트 문서가 필요합니다")
    items = document["events"]
    ids = [e.get("event_id") for e in items if isinstance(e, dict)]
    if len(ids) != len(items) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError("모든 이벤트에 event_id가 필요합니다")
    if len(set(ids)) != len(ids):
        raise ValueError("중복 event_id: 입력을 수정한 뒤 다시 실행하세요")
    for event in items:
        for key in ("conditions", "before", "after", "change", "dates", "period_end"):
            if event.get(key) is not None and not isinstance(event[key], dict):
                raise ValueError(f"{event['event_id']}: {key}는 객체 또는 null이어야 합니다")
        if event.get("subtags") is not None and (
            not isinstance(event["subtags"], list) or
            any(not isinstance(tag, str) for tag in event["subtags"])
        ):
            raise ValueError(f"{event['event_id']}: subtags는 문자열 배열이어야 합니다")
        if event.get("conflicts") is not None and not isinstance(event["conflicts"], list):
            raise ValueError(f"{event['event_id']}: conflicts는 배열이어야 합니다")
        for key in ("gaps", "sources"):
            if event.get(key) is not None and (
                not isinstance(event[key], list) or
                any(not isinstance(item, dict) for item in event[key])
            ):
                raise ValueError(f"{event['event_id']}: {key}는 객체 배열이어야 합니다")
    return items


def sync(db, document, today, urgent_days):
    items = validate_events(document)
    check_urgent_days(urgent_days)
    check_day(db, today)
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        check_day(db, today)
        remember_day(db, today)
        for event in items:
            identity = event["event_id"]
            signature = digest(material(event))
            old = db.execute("SELECT * FROM events WHERE id=?", (identity,)).fetchone()
            day, _, _ = deadline(event)
            qualifies = event_eligible(event) and day is not None and (day - today).days <= urgent_days
            urgent = bool(qualifies or (old and old["urgent"]))
            if old is None or old["payload"] != encode(event):
                db.execute("INSERT INTO history(event_id,signature,payload,observed_day) VALUES(?,?,?,?)",
                           (identity, signature, encode(event), today.isoformat()))
            db.execute("""INSERT INTO events(id,payload,signature,urgent,seen_day)
                VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                payload=excluded.payload,signature=excluded.signature,
                urgent=excluded.urgent,seen_day=excluded.seen_day""",
                       (identity, encode(event), signature, int(urgent), today.isoformat()))


def grouping(event):
    day, _, basis = deadline(event)
    # Do not trust upstream group_key: it may merge different routes/conditions.
    return {"model": event.get("model_id"), "route": event.get("route"),
            "type": event.get("event_type"), "subtags": sorted(event.get("subtags") or []),
            "conditions": event.get("conditions") or {},
            "deadline": day.isoformat() if day else None, "basis": basis}


def prepare(db, today, channel, urgent_days=5, judged=None):
    """Cards of the unacknowledged urgent events and new changes. judged is the run's cards.json as dashboard_cards.parse
    returns it; None means no dashboard judgement, only the rules here (and the 7-day limit), and no new-change alerts
    since only the dashboard knows whether LETSUR uses the route."""
    check_urgent_days(urgent_days)
    check_day(db, today)
    # No collection is needed for the clock to move from D-6 to D-5.
    with db:
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        check_day(db, today)
        remember_day(db, today)
        for row in db.execute("SELECT id,payload FROM events WHERE urgent=0").fetchall():
            event = json.loads(row["payload"])
            day, _, _ = deadline(event)
            if event_eligible(event) and day is not None and (day - today).days <= urgent_days:
                db.execute("UPDATE events SET urgent=1 WHERE id=?", (row["id"],))
    names = judged["names"] if judged else {}
    groups = {}
    for row in db.execute("SELECT * FROM events ORDER BY id").fetchall():
        if row["ack_signature"] == row["signature"]:
            continue
        event = json.loads(row["payload"])
        match = (judged["events"].get(row["id"]) if judged else None) or {}
        # 2026-10-28 가안: besides the urgent ones, a new change on a route LETSUR uses (or may use) goes out once.
        if row["urgent"]:
            alert = "urgent"
        elif (match.get("applicability") in ("APPLIES", "UNKNOWN") and new_change(event, today) and
              not delivered(db, channel, row["id"], row["signature"])):
            alert = "new"
        else:
            continue
        day, label, basis = deadline(event)
        # Decision 26-4: a dashboard RECORD card is not an alert, and an alert stops on the 8th day after its deadline.
        # 2026-10-28 가안: nor is a NOT APPLIES card, a change on a route LETSUR does not use (26-4 still sent those).
        if ((day is not None and (today - day).days > OVERDUE_DAYS) or match.get("action") == "RECORD" or
                match.get("applicability") == "NOT APPLIES"):
            continue
        key = digest(grouping(event))
        card = groups.setdefault(key, {"card_id": key, "model": match.get("model") or event.get("model_id"),
            "route": match.get("route") or name(names, "routes", event.get("route")),
            "conditions": event.get("conditions") or {},
            "deadline": day.isoformat() if day else None, "schedule_label": label,
            "date_basis": basis, "days": (day - today).days if day else None,
            "stage": stage(event), "alert": alert, "items": []})
        if alert == "urgent":
            card["alert"] = "urgent"
        sent = db.execute("""SELECT 1 FROM simulated_deliveries
            WHERE channel=? AND day=? AND event_id=? AND signature=?""",
            (channel, today.isoformat(), row["id"], row["signature"])).fetchone()
        previous = db.execute("SELECT 1 FROM simulated_deliveries WHERE channel=? AND event_id=?",
                              (channel, row["id"])).fetchone()
        changed = db.execute("SELECT 1 FROM simulated_deliveries WHERE channel=? AND event_id=? AND signature<>?",
                            (channel, row["id"], row["signature"])).fetchone()
        card["items"].append({"event_id": row["id"], "signature": row["signature"],
            "revision": event.get("revision"), "status": event.get("status"),
            "billing_item": event.get("billing_item"), "before": event.get("before"),
            "after": event.get("after"), "change": event.get("change") or {},
            "sources": event.get("sources") or [], "evidence_refs": event.get("evidence_refs") or [],
            "gaps": event.get("gaps") or [], "next_checks": event.get("next_checks") or [],
            "missing_in_current_input": row["seen_day"] != today.isoformat(), "dashboard_card": match.get("card"),
            "delivery_kind": "changed" if changed else "reminder" if previous else "initial",
            "alert": alert, "already_simulated_today": bool(sent)})
    cards = list(groups.values())
    for card in cards:
        card["items"] = [item for item in card["items"] if not item["already_simulated_today"]]
    cards = [card for card in cards if card["items"]]
    # 2026-10-28 가안: in LETSUR's order, a stop of selling before a cost change, then the nearer deadline.
    cards.sort(key=lambda c: (c["stage"], c["days"] is None, c["days"] or 0, c["card_id"]))
    return {"mode": "OFFLINE_ONLY", "as_of": today.isoformat(), "channel": channel,
            "card_count": len(cards), "event_count": sum(len(c["items"]) for c in cards), "names": names, "cards": cards}


def render(report):
    names = report.get("names") or {}
    lines = ["【로컬 미리보기 · Slack 미발송】",
             f"기준일 {report['as_of']} | 사건 카드 {report['card_count']}개 / 이벤트 {report['event_count']}건", ""]
    if not report["cards"]:
        return "\n".join(lines + ["발송 대상 없음. Slack에서는 메시지를 생략합니다.", ""])
    for number, card in enumerate(report["cards"], 1):
        days = card["days"]
        new = "새로 확인된 변화 · 한 번만 알림" if card.get("alert") == "new" else None
        schedule = (new or "일정 재확인 필요 · 기존 긴급 상태 유지") if days is None else (
            f"{card['schedule_label']}일 {abs(days)}일 경과" if days < 0 else
            f"오늘 {card['schedule_label']}" if days == 0 else f"{card['schedule_label']}까지 {days}일")
        if new and days is not None:
            schedule += " · " + new
        lines.extend([f"{number}. {card['model']} / {card['route']}", schedule])
        if card["date_basis"] == "derived_from_publication":
            lines.append("※ 종료 연도는 게시일에서 파생한 날짜입니다.")
        if card["conditions"]:
            lines.append("조건: " + encode(card["conditions"]))
        for item in card["items"]:
            before, after = item["before"] or {}, item["after"] or {}
            if before.get("amount") is not None and after.get("amount") is not None:
                lines.append(f"  {name(names, 'items', item['billing_item'])}: {before['amount']} → {after['amount']} {after.get('currency','')} / {name(names, 'units', after.get('unit',''))}")
            elif item["change"].get("discount_pct") is not None:
                lines.append(f"  할인율: {item['change']['discount_pct']}%")
            lines.append(f"  {item['event_id']} / revision {item['revision']} / {item['status']}")
            if item["delivery_kind"] == "changed":
                lines.append("  변경된 미확인 사건 · 이전 발송 내용과 핵심 정보가 달라졌습니다.")
            if item["status"] != "confirmed":
                lines.append("  긴급 확인 필요 · 미확정 (확정된 변경으로 취급하지 않음)")
                for gap in item.get("gaps", []):
                    lines.append(f"  미확정 사유: {gap.get('field', 'unknown')} / {gap.get('reason', 'unknown')}")
                    if gap.get("next_check"):
                        lines.append(f"  확인할 내용: {gap['next_check']}")
                for check in item.get("next_checks", []):
                    lines.append(f"  확인할 내용: {check}")
            if item["missing_in_current_input"]:
                lines.append("  최신 입력에서 미관측 · 취소로 간주하지 않음")
        lines.extend(["[확인함: 로컬 명령으로 모의 처리] [상세·변경 이력: 팀원 연결 협의 필요]", ""])
    return "\n".join(lines + ["공개 정보 기반 · 실제 사업 임팩트 미평가", ""])


def acknowledge(db, card, user, today, timestamp=None):
    if not isinstance(user, str) or not user.strip():
        raise ValueError("확인자 식별자가 필요합니다")
    if not isinstance(card, dict) or not isinstance(card.get("items"), list) or not card["items"]:
        raise ValueError("확인할 이벤트가 없는 카드입니다")
    for item in card["items"]:
        if not isinstance(item, dict) or not item.get("event_id") or not item.get("signature"):
            raise ValueError("유효한 이벤트 식별자와 핵심 내용 식별자가 필요합니다")
    check_day(db, today)
    clock = datetime.now(timezone.utc) if timestamp is None else timestamp
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("확인 시각에는 시간대 정보가 필요합니다")
    recorded_at = clock.astimezone(timezone.utc).isoformat(timespec="microseconds")
    with db:
        # Acquire the write lock before reading signatures to serialize click/update races.
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        check_day(db, today)
        # Validate the entire card before modifying any member.
        for item in card["items"]:
            current = db.execute("SELECT signature FROM events WHERE id=?", (item["event_id"],)).fetchone()
            if current is None or current["signature"] != item["signature"]:
                raise ValueError("내용이 변경되었습니다. 최신 알림을 확인하세요")
        remember_day(db, today)
        for item in card["items"]:
            current = db.execute("SELECT ack_signature FROM events WHERE id=?", (item["event_id"],)).fetchone()
            if current["ack_signature"] == item["signature"]:
                continue
            db.execute("UPDATE events SET ack_signature=?,ack_user=?,ack_day=?,ack_at_utc=? WHERE id=?",
                       (item["signature"], user.strip(), today.isoformat(), recorded_at, item["event_id"]))
            db.execute("""INSERT INTO acknowledgements(event_id,signature,user,logical_day,acknowledged_at_utc,source)
                VALUES(?,?,?,?,?,'explicit')""",
                       (item["event_id"], item["signature"], user.strip(), today.isoformat(), recorded_at))


def simulated_success(db, report):
    """Explicit test action only; never used to claim real Slack delivery."""
    today = date.fromisoformat(report["as_of"])
    check_day(db, today)
    with db:
        remember_day(db, today)
        for card in report["cards"]:
            for item in card["items"]:
                current = db.execute("SELECT signature FROM events WHERE id=?", (item["event_id"],)).fetchone()
                if current is None or current["signature"] != item["signature"]:
                    raise ValueError("보고 생성 후 내용이 변경되었습니다")
                db.execute("INSERT OR IGNORE INTO simulated_deliveries VALUES(?,?,?,?)",
                           (report["channel"], report["as_of"], item["event_id"], item["signature"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preview", "ack", "simulate-success"))
    parser.add_argument("--db", required=True)
    parser.add_argument("--as-of", required=True, help="명시적인 YYYY-MM-DD 기준일 (KST 달력 날짜)")
    parser.add_argument("--events")
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    parser.add_argument("--output", help="출력 접두 경로: .json과 .txt를 생성")
    parser.add_argument("--report", help="이전에 생성한 보고 JSON")
    parser.add_argument("--card", type=int, default=1)
    parser.add_argument("--user")
    args = parser.parse_args()
    today = date.fromisoformat(args.as_of)
    with connect(args.db) as db:
        if args.command == "preview":
            config = json.loads(Path(args.config).read_text(encoding="utf-8"))
            if type(config["urgent_days"]) is not int or config["urgent_days"] < 0:
                raise ValueError("urgent_days는 0 이상의 정수여야 합니다")
            if args.events:
                sync(db, json.loads(Path(args.events).read_text(encoding="utf-8")), today, config["urgent_days"])
            report = prepare(db, today, config["channel_id"], config["urgent_days"])
            if args.output:
                base = Path(args.output)
                base.parent.mkdir(parents=True, exist_ok=True)
                base.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                base.with_suffix(".txt").write_text(render(report), encoding="utf-8")
            print(encode({"cards": report["card_count"], "events": report["event_count"], "slack_sent": False}))
        else:
            report = json.loads(Path(args.report).read_text(encoding="utf-8"))
            if report.get("mode") != "OFFLINE_ONLY":
                raise ValueError("로컬 모의 보고만 입력할 수 있습니다")
            if args.command == "ack":
                if not args.user or args.card < 1 or args.card > len(report["cards"]):
                    raise ValueError("확인자와 유효한 카드 번호가 필요합니다")
                acknowledge(db, report["cards"][args.card - 1], args.user, today)
            else:
                if report["as_of"] != today.isoformat():
                    raise ValueError("보고 기준일과 모의 발송일이 다릅니다")
                simulated_success(db, report)
            print("로컬 모의 처리 완료 · 실제 Slack 연결 없음")


if __name__ == "__main__":
    main()
