"""Slack payloads and signed button processing; no network calls or message sending."""
import hashlib
import hmac
import json
import secrets
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from alerts import acknowledge, digest, encode
from dashboard_cards import condition_value, name

ACTION_ID = "ack_card"
MAX_BODY_BYTES = 128 * 1024
KST = timezone(timedelta(hours=9))


def _token_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS slack_ack_tokens (
        token TEXT PRIMARY KEY, snapshot_key TEXT NOT NULL UNIQUE,
        channel TEXT NOT NULL, card_json TEXT NOT NULL
    )""")


def _snapshot_token(db, channel, card):
    """Token is an immutable server-side reference, not client-provided event JSON."""
    items = [{"event_id": item["event_id"], "signature": item["signature"]}
             for item in card["items"]]
    snapshot = {"card_id": card["card_id"], "items": items}
    key = digest({"channel": channel, "snapshot": snapshot})
    existing = db.execute("SELECT token FROM slack_ack_tokens WHERE snapshot_key=?", (key,)).fetchone()
    if existing:
        return existing[0]
    token = secrets.token_urlsafe(24)
    db.execute("INSERT INTO slack_ack_tokens VALUES(?,?,?,?)", (token, key, channel, encode(snapshot)))
    return token


def _clip(value, limit):
    value = str(value)
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _schedule(card):
    days, label = card.get("days"), card.get("schedule_label", "일정")
    if days is None:
        return "일정 재확인 필요 · 기존 긴급 상태 유지"
    if days < 0:
        return f"{label}일 {abs(days)}일 경과"
    return f"오늘 {label}" if days == 0 else f"{label}까지 {days}일"


def _dashboard_cards(card):
    """Ids of the dashboard cards that hold this card's events, in the order the events list them."""
    return list(dict.fromkeys(item["dashboard_card"] for item in card["items"] if item.get("dashboard_card")))


def _card_text(card, names):
    lines = [f"{_clip(card.get('model', '모델 미확인'), 180)} / {_clip(card.get('route', '경로 미확인'), 180)}",
             _schedule(card)]
    conditions = card.get("conditions") or {}
    shown = []
    for key in ("endpoint_tag", "service_tier", "region", "input_token_band", "endpoint_status"):
        value = condition_value(names, key, conditions[key]) if key in conditions else ""
        if value != "":
            shown.append(f"{_clip(name(names, 'conditions', key), 40)}: {_clip(value, 100)}")
    if shown:
        lines.append("조건 · " + " / ".join(shown))
    if card.get("date_basis") == "derived_from_publication":
        lines.append("종료 연도는 게시일에서 파생")
    for item in card["items"]:
        before, after = item.get("before") or {}, item.get("after") or {}
        label = name(names, "items", item.get("billing_item"))
        if before.get("amount") is not None and after.get("amount") is not None:
            lines.append(f"{_clip(label or '가격', 80)}: {_clip(before['amount'], 40)} → {_clip(after['amount'], 40)} {_clip(after.get('currency', ''), 20)} / {_clip(name(names, 'units', after.get('unit', '')), 50)}")
        elif (item.get("change") or {}).get("discount_pct") is not None:
            lines.append(f"할인율: {_clip(item['change']['discount_pct'], 30)}%")
        else:
            lines.append("변경 항목: " + _clip(label or item.get("event_type") or item["event_id"], 100))
    if any(i.get("delivery_kind") == "changed" for i in card["items"]):
        lines.append("변경된 미확인 사건")
    if any(i.get("status") != "confirmed" for i in card["items"]):
        lines.append("긴급 확인 필요 · 미확정")
        if any(i.get("status") == "conflicted" for i in card["items"]):
            lines.append("출처 간 정보 충돌 · 어느 주장도 확정하지 않음")
        fields = {gap.get("field") for i in card["items"] for gap in i.get("gaps", [])}
        descriptions = {
            "raw_check": "과거 원문 재검증 필요", "account_actual_price": "계정 적용 조건 확인 필요",
            "discount_period_or_target": "할인 기간·대상 확인 필요", "sub_provider": "하위 공급자 적용 범위 확인 필요",
            "observed_price": "동일 조건의 실제 가격 대조 필요", "scope": "종료·변경 범위 확인 필요",
            "letsur_usage": "렛서 사용 여부 미확인",
        }
        shown = [descriptions.get(field, str(field)) for field in sorted(fields, key=str) if field]
        if shown:
            lines.append("미확정 사유: " + _clip(" / ".join(shown), 350))
    if any(i.get("missing_in_current_input") for i in card["items"]):
        lines.append("최신 수집에서 미관측 · 취소로 간주하지 않음")
    others = len(_dashboard_cards(card)) - 1
    if others > 0:
        lines.append(f"대시보드 카드 외 {others}장")
    return "\n".join(lines)


def _display_parts(cards, names):
    """Keep every acknowledged item visible; long cards get multiple buttons."""
    result = []
    for card in cards:
        current = deepcopy(card)
        current["items"] = []
        for item in card["items"]:
            trial = dict(current, items=current["items"] + [item])
            if current["items"] and len(_card_text(trial, names)) > 2800:
                result.append(current)
                current = dict(card, items=[])
            current["items"].append(item)
            if len(_card_text(current, names)) > 2800:
                raise ValueError("단일 이벤트 표시 내용이 Slack 제한을 넘습니다")
        if current["items"]:
            result.append(current)
    return result


def _https_url(url):
    if url is None:
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("상세 링크는 사용자 정보가 없는 HTTPS 주소여야 합니다")
    if any(ch.isspace() for ch in url):
        raise ValueError("상세 링크에 공백을 포함할 수 없습니다")
    return url


def _card_url(url, card):
    """The dashboard opened at the first dashboard card that holds the card's events, else the plain address (decision 26-6)."""
    ids = _dashboard_cards(card)
    return (url.split("#", 1)[0] + "#" + ids[0]) if ids else url


def message_batches(db, report, *, dashboard_url=None, max_blocks=40):
    """Return (Slack payload, displayed card snapshots) batches; never sends."""
    if not 4 <= max_blocks <= 40:
        raise ValueError("max_blocks는 4~40이어야 합니다")
    url = _https_url(dashboard_url)
    names = report.get("names") or {}
    cards = _display_parts(report.get("cards") or [], names)
    if not cards:
        return []
    per_message = (max_blocks - 1) // 3
    messages = []
    with db:
        _token_table(db)
        for start in range(0, len(cards), per_message):
            part = cards[start:start + per_message]
            header = f"렛서 긴급 알림 · {report['as_of']} · 미확인 사건 {len(report['cards'])}개"
            blocks = [{"type": "section", "text": {"type": "plain_text", "text": header}}]
            for card in part:
                token = _snapshot_token(db, report["channel"], card)
                elements = [{"type": "button", "text": {"type": "plain_text", "text": "확인함"},
                             "action_id": ACTION_ID, "value": token}]
                if url:
                    elements.append({"type": "button", "text": {"type": "plain_text", "text": "대시보드"},
                                     "action_id": "open_dashboard", "url": _card_url(url, card)})
                blocks.extend([
                    {"type": "section", "text": {"type": "plain_text", "text": _card_text(card, names)}},
                    {"type": "actions", "block_id": "ack_" + secrets.token_hex(12), "elements": elements},
                    {"type": "divider"},
                ])
            payload = {"channel": report["channel"], "text": header,
                       "blocks": blocks, "parse": "none", "link_names": False,
                       "unfurl_links": False, "unfurl_media": False}
            messages.append((payload, part))
    return messages


def build_messages(db, report, *, dashboard_url=None, max_blocks=40):
    """Return only chat.postMessage argument dictionaries, without batch metadata."""
    return [payload for payload, _ in message_batches(db, report, dashboard_url=dashboard_url, max_blocks=max_blocks)]


def _epoch(now):
    if isinstance(now, datetime):
        if now.tzinfo is None:
            raise ValueError("now에는 시간대가 필요합니다")
        return now.timestamp()
    return float(now)


def verify_signature(headers, raw_body, signing_secret, now):
    """Verify against the exact raw bytes before decoding/parsing a form body."""
    if not isinstance(raw_body, bytes) or not signing_secret or len(raw_body) > MAX_BODY_BYTES:
        return False
    lower = {str(k).lower(): str(v) for k, v in headers.items()}
    try:
        timestamp = lower["x-slack-request-timestamp"]
        if abs(_epoch(now) - int(timestamp)) > 300:
            return False
        expected = "v0=" + hmac.new(signing_secret.encode(), b"v0:" + timestamp.encode() + b":" + raw_body,
                                    hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, lower.get("x-slack-signature", ""))
    except (KeyError, TypeError, ValueError):
        return False


def handle_interaction(db, headers, raw_body, signing_secret, expected_team_id, expected_channel_id, now):
    """Return (HTTP status, JSON response), without response_url or network access.

    An HTTP host must return this response within Slack's 3 second acknowledgement
    deadline; production hosting/worker integration is a separate deployment step.
    """
    def response(status, text):
        return status, {"response_type": "ephemeral", "text": text}

    if not verify_signature(headers, raw_body, signing_secret, now):
        return response(401, "요청 서명을 확인할 수 없습니다")
    if not expected_team_id or not expected_channel_id:
        return response(503, "워크스페이스와 채널 설정이 필요합니다")
    try:
        form = parse_qs(raw_body.decode("utf-8"), strict_parsing=True)
        if len(form.get("payload", [])) != 1:
            raise ValueError("payload")
        payload = json.loads(form["payload"][0])
    except (ValueError, UnicodeError):
        return response(400, "잘못된 버튼 요청입니다")
    return handle_authenticated_payload(db, payload, expected_team_id, expected_channel_id, now)


def handle_authenticated_payload(db, payload, expected_team_id, expected_channel_id, now):
    """Only call after HTTP signature verification or authenticated Socket Mode receipt."""
    def response(status, text):
        return status, {"response_type": "ephemeral", "text": text}

    if not expected_team_id or not expected_channel_id:
        return response(503, "워크스페이스와 채널 설정이 필요합니다")
    try:
        if payload.get("type") != "block_actions":
            raise ValueError("type")
        if (payload.get("team") or {}).get("id") != expected_team_id:
            return response(403, "허용된 워크스페이스가 아닙니다")
        if (payload.get("channel") or {}).get("id") != expected_channel_id:
            return response(403, "허용된 채널이 아닙니다")
        actor = (payload.get("user") or {}).get("id")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor")
        actions = payload.get("actions") or []
        if len(actions) != 1:
            raise ValueError("actions")
        action = actions[0]
        if action.get("action_id") == "open_dashboard":
            return response(200, "대시보드에서 상세 내용을 확인하세요")
        if action.get("action_id") != ACTION_ID or not isinstance(action.get("value"), str):
            raise ValueError("action")
        _token_table(db)
        record = db.execute("SELECT channel,card_json FROM slack_ack_tokens WHERE token=?", (action["value"],)).fetchone()
        if record is None or record["channel"] != expected_channel_id:
            return response(403, "유효한 확인 버튼이 아닙니다")
        card = json.loads(record["card_json"])
        today = datetime.fromtimestamp(_epoch(now), timezone.utc).astimezone(KST).date()
        acknowledge(db, card, actor, today, timestamp=datetime.fromtimestamp(_epoch(now), timezone.utc))
        return response(200, "확인 처리했습니다. 해당 내용의 공용 재알림을 중단합니다.")
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        if "내용이 변경" in str(exc):
            return response(409, "내용이 변경되었습니다. 최신 알림을 확인하세요")
        return response(400, "잘못된 버튼 요청입니다")
