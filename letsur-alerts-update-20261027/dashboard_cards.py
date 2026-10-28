"""The dashboard's cards.json (schema 1). Standard library only; the file is read from disk, never fetched here.

The bot follows the dashboard's judgement and its names; the rules and the name tables stay in the dashboard build
(decisions 26-4, 26-6, 26-7 of 2026-10-27).
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA = 1
CARD_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # becomes the #fragment of the dashboard link
TABLES = ("routes", "items", "conditions", "tiers", "regions", "status", "units")
# Which name table spells a condition's values; every other value is shown as the source wrote it.
VALUE_TABLES = {"service_tier": "tiers", "region": "regions", "endpoint_status": "status"}


class CardsError(ValueError):
    pass


def _text(value):
    return value if isinstance(value, str) and value else None


def _links(value):
    """https pages without credentials or spaces, at most two: they become the message's 근거 button."""
    out = []
    for link in value if isinstance(value, list) else []:
        if isinstance(link, str) and link.startswith("https://") and not any(ch.isspace() for ch in link):
            parts = urlsplit(link)
            if parts.hostname and not parts.username and not parts.password:
                out.append(link)
    return out[:2]


def parse(document):
    """Index a cards.json document by event id. Raises CardsError when it cannot be trusted: wrong schema, a
    malformed card, or an event in two cards (the dashboard puts each event in at most one)."""
    if not isinstance(document, dict) or type(document.get("schema")) is not int or document["schema"] != SCHEMA:
        raise CardsError("schema")
    if not isinstance(document.get("cards"), list):
        raise CardsError("cards")
    events, identities = {}, set()
    for card in document["cards"]:
        if not isinstance(card, dict):
            raise CardsError("card")
        identity, action, members = card.get("id"), card.get("action"), card.get("events")
        if (not isinstance(identity, str) or not CARD_ID.fullmatch(identity) or identity in identities or
                not isinstance(action, str) or not isinstance(members, list) or
                any(not isinstance(member, str) or not member for member in members)):
            raise CardsError("card")
        identities.add(identity)
        for member in members:
            if member in events:
                raise CardsError("duplicate_event")
            # usage, check and links: 2026-10-28 (결정 기록 29절 렛서 답변), shown in the message when present
            events[member] = {"card": identity, "action": action, "applicability": _text(card.get("applicability")),
                              "model": _text(card.get("model")), "route": _text(card.get("route")),
                              "usage": _text(card.get("usage")), "check": _text(card.get("check")), "links": _links(card.get("links"))}
    tables = document.get("names", {})
    if not isinstance(tables, dict) or any(not isinstance(tables.get(table, {}), dict) for table in TABLES):
        raise CardsError("names")
    names = {table: {key: value for key, value in tables.get(table, {}).items() if _text(value)} for table in TABLES}
    # health.failing: sources failing in the newest run, for the 수집 점검 alert (2026-10-28). Optional; bad items are skipped.
    health = document.get("health") if isinstance(document.get("health"), dict) else {}
    failing = [{"key": item["key"], "sinceRun": item["sinceRun"], "model": _text(item.get("model")) or "",
                "route": _text(item.get("route")) or "", "error": _text(item.get("error")) or "", "since": _text(item.get("since")) or "",
                "url": (_links([item.get("url")]) or [""])[0]}
               for item in health.get("failing") or [] if isinstance(item, dict) and _text(item.get("key")) and _text(item.get("sinceRun"))]
    return {"base_day": document.get("base_day"), "names": names, "events": events, "health": failing}


def name(names, table, key):
    """The dashboard's name for key in one of its name tables, or key itself when the tables do not have it."""
    if isinstance(key, str):
        return ((names or {}).get(table) or {}).get(key) or key
    return key


def condition_value(names, key, value):
    """A condition value as the dashboard writes it: tier, region and OpenRouter status by name, the rest as given.
    Empty when the dashboard shows nothing for it (status 0 is the normal state)."""
    if key == "endpoint_status" and str(value) == "0":
        return ""
    table = VALUE_TABLES.get(key)
    if table is None:
        return value
    return ((names or {}).get(table) or {}).get(str(value).lower()) or value


def from_source(source):
    """(parsed cards.json, None) for the run github_source.fetch_latest returned, else (None, a warning saying why).

    A missing or unreadable file never stops the alerts: the bot falls back to its own rules plus the 7-day limit and
    says so in the Slack header and the cycle result. The team decides this fallback later (decision 26-4).
    """
    if source is None:
        reason = "no_source"
    elif not source.get("cards_path"):
        reason = source.get("cards_error") or "cards_missing"
    else:
        try:
            return parse(json.loads(Path(source["cards_path"]).read_text(encoding="utf-8"))), None
        except CardsError as error:
            reason = f"invalid_cards:{error}"
        except (OSError, ValueError):
            reason = "unreadable_cards"
    return None, f"대시보드 판정(cards.json) 없음 — 기존 규칙으로 발송 ({reason})"
