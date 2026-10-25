"""Fill the dashboard template with the events engine output.

    python -m scripts.market_collection.dashboard --root data/market_collection --output dashboard-site
        [--internal-routes samples/market_collection/demo_internal_routes.json]

Reads <root>/events/events.json and, when present, report.json, continuity.json and
state.json next to it, and writes <output>/index.html: template.html (a copy of the
사용자뷰 1011 page) with its data slots filled. The page needs no network and opens
from file://. Only facts the engine wrote are shown: judgement fields the engine does
not make (headline, risk, suggestion text) stay empty, and the template's own rules
compute the rest. LETSUR's route for each card, and for each model in MODELS, comes only
from --internal-routes, a file of the routes LETSUR uses per model; without it, or when
the file cannot be used, every card says the route is unconfirmed and no model gets one.
Cards and models decided by a file with is_demo true are marked as assumed. MODELS is
page data only for now: the template reads it in renderModels(), which nothing calls.
When events.json is missing or unreadable, or the build or the
continuity step failed, a page is still written and says today's build failed, so
yesterday's page never stays up as if it were current. Mapping rules: docs/dashboard.md.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.market_collection.events.catalog import ROUTE_PUBLISHERS, SIGNAL_PUBLISHERS, authority as route_authority, maker
from scripts.market_collection.events.evidence import MUTABLE_CONDITIONS
from scripts.market_collection.models import MODEL_MODALITIES

KST = timezone(timedelta(hours=9))
TEMPLATE = Path(__file__).with_name("template.html")
SLOT = re.compile(r"/\*DATA:(\w+)\*/|<!--STATUS-->")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
T7 = {"price_change": "가격 변경", "scheduled_change": "예정 변경", "limited_discount": "한시 할인", "condition_change": "조건 변경",
      "model_release": "모델 출시", "model_end": "제공 종료", "route_change": "공급 경로 변경"}
CONDITION_GROUPS = {
    "billing": ("과금", (("cache", "캐시"), ("batch", "Batch"), ("unit", "단위"), ("context_threshold", "구간"), ("tier", "구간"),
                        ("surcharge", "할증"))),
    "support": ("지원", (("resolution", "해상도"), ("input_format", "입력 형식"), ("region", "지역"), ("context_limit", "컨텍스트 한도"),
                        ("license", "라이선스"), ("alias", "alias"))),
}
ITEM_ORDER = ("input", "output", "cache_read", "cache_write")
ITEM_KO = {"input": "입력 토큰", "output": "출력 토큰", "cache_read": "캐시 읽기", "cache_write": "캐시 쓰기"}  # the template's own row names
MEASURE_KO = {"input": "입력", "output": "출력", "cache_read": "캐시 읽기", "cache_write": "캐시 쓰기"}
UNIT_KO = {"1M tokens": "100만 토큰", "1000 tokens": "1,000 토큰"}
CONDITION_KO = {"service_tier": "등급", "cache_read_multiplier": "캐시 읽기 배율"}
KIND_KO = {"llm": "LLM", "video": "영상", "image": "이미지"}
TAG = {"manufacturer_official": "sup", "channel_official": "ch"}  # anything else is an external source
MARK = {"confirmed": "ok", "candidate": "unk", "conflicted": "con"}
IGNORED = {"rate_period", *MUTABLE_CONDITIONS}  # the rate window and stated multipliers are not what is compared
# What a gateway listing states about the endpoint serving a price (collectors/llm_endpoints.py), not the condition it is priced
# under: it tells lines apart, never trend tabs.
ENDPOINT = {"endpoint_tag", "endpoint_provider", "endpoint_model_slug", "context_length", "quantization", "endpoint_status",
            "endpoint_discount", "discount_application", "price_basis"}
SUSPECT_GAPS = ("parser_artifact", "csv_raw_mismatch", "parser_fingerprint_mismatch")
MAX_LINES = 6  # the template colours six lines (s1~s6)


def kst(value) -> datetime | None:
    """An ISO timestamp in KST (no zone means UTC); a bare date or anything else is None."""
    if not isinstance(value, str) or len(value) <= 10:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(KST)


def iso(value) -> str | None:
    """value when it is a real YYYY-MM-DD date."""
    if not isinstance(value, str) or not ISO_DATE.fullmatch(value):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def part(event: dict, name: str) -> dict:
    value = event.get(name)
    return value if isinstance(value, dict) else {}


def published_day(event: dict) -> str | None:
    return iso(str(part(event, "dates").get("published") or "")[:10])


def md(day: str) -> str:
    return f"{int(day[5:7])}/{int(day[8:10])}"


def day_of(moment: datetime) -> str:
    return moment.date().isoformat()


def minute(moment: datetime) -> str:
    return f"{moment:%Y-%m-%d %H:%M} KST"


def run_time(run_id) -> datetime | None:
    """Start time encoded in a collector run id (20261004T062258Z-<hex>), in KST."""
    try:
        moment = datetime.strptime(str(run_id)[:16], "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc).astimezone(KST)


def number(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def money(amount, currency="USD") -> str | None:
    """'$1.50' for USD, the form the template's money() reads ('$0' when free); otherwise '<amount> <currency>'."""
    value = number(amount)
    if value is None:
        return None
    if currency != "USD":
        return f"{format(value, 'f')} {currency or ''}".strip()
    if value == 0:
        return "$0"
    whole, _, frac = format(value, "f").partition(".")
    return f"${whole}.{frac.ljust(2, '0')}"


def unknown(text: str) -> dict:
    return {"unk": 1, "t": text}


def js(value) -> str:
    """JSON for a <script> block: <, >, &, U+2028 and U+2029 escaped so data cannot end the script."""
    text = json.dumps(value, ensure_ascii=False)
    for char in "<>&\u2028\u2029":
        text = text.replace(char, f"\\u{ord(char):04x}")
    return text


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def cap(value) -> str:
    text = str(value)
    return text[:1].upper() + text[1:]


def unit_ko(unit) -> str:
    return UNIT_KO.get(unit, unit) if unit else ""


def item_ko(item: str) -> str:
    return ITEM_KO.get(item) or (f"{item} 단가" if item else "단가")


def condition_name(key: str) -> str:
    return CONDITION_KO.get(key) or (f"조건 {key}" if key else "조건")


def rank(item: str) -> int:
    return ITEM_ORDER.index(item) if item in ITEM_ORDER else len(ITEM_ORDER)


def route_label(route: str) -> str:
    name = ROUTE_PUBLISHERS.get(route)
    return f"{name} · {route}" if name else route or "경로 표기 없음"


def compared(conditions) -> dict:
    return {key: value for key, value in (conditions or {}).items() if key not in IGNORED}


def endpoint(conditions) -> tuple:
    """The stated endpoint facts of a price; they are part of the trend line's key."""
    return tuple(sorted((key, str(value)) for key, value in (conditions or {}).items() if key in ENDPOINT))


def endpoint_label(facts: tuple) -> str:
    """The endpoint's name (OpenRouter tag or Vercel provider), plus its quantization unless the name already says it."""
    stated = dict(facts)
    name, quantization = stated.get("endpoint_tag") or stated.get("endpoint_provider") or "", stated.get("quantization") or ""
    return " · ".join(part for part in (name, "" if quantization in name else quantization) if part)


def card_date(event: dict) -> str:
    """Effective or end date, else publication day (UTC), else the day it was observed (KST)."""
    dates = part(event, "dates")
    for value in (dates.get("effective_from"), dates.get("end_date"), part(event, "period_end").get("date"), dates.get("route_added_date")):
        if iso(value):
            return value
    if published_day(event):
        return published_day(event)
    for value in (dates.get("observed_after_utc"), part(event, "after").get("observed_at_utc"), dates.get("first_observed_utc")):
        moment = kst(value)
        if moment:
            return day_of(moment)
    return ""


def last_observed_day(members: list[dict]) -> str:
    """Newest day (KST) a member of a grouped card was observed, the day its rows show as 관측 M/D; empty for a single event or when none was.

    card_date follows the representative alone, yet cards seen up to GROUP_WINDOW_DAYS after the group's first card join the same card.
    """
    moments = filter(None, (kst(part(e, "after").get("observed_at_utc")) for e in members))
    return max(map(day_of, moments), default="") if len(members) > 1 else ""


def card_key(event: dict) -> list:
    if event.get("group_key"):
        return ["group", event["group_key"], event.get("route") or ""]
    return ["event", event.get("model_id") or "", event.get("route") or "", event.get("event_type") or "",
            sorted(map(str, event.get("subtags") or [])), card_date(event)]


def representative(members: list[dict]) -> dict:
    return next((event for event in members if event.get("group_role") == "representative"), members[0])


def relation(rep: dict, routes: dict | None) -> dict:
    """relKey, relShort and relDemo for the card's model and route from the --internal-routes file.

    A model with no record stays unconfirmed: no record does not mean LETSUR does not use it.
    endpoint_id only adds detail when the route matches."""
    records = [record for record in (routes or {}).get("records", []) if record["model_id"] == rep.get("model_id")]
    if not records:
        return {"relKey": "unk", "relShort": "사용 경로 확인 필요", "relDemo": False}
    route, used = rep.get("route") or "", list(dict.fromkeys(record["route_id"] for record in records if record["route_id"]))
    if route in used:
        endpoints = dict.fromkeys(record["endpoint_id"] for record in records if record["route_id"] == route and record.get("endpoint_id"))
        key, short = "use", " · ".join(["이 경로로 사용 중", *(f"endpoint {endpoint}" for endpoint in endpoints)])
    elif used:
        key, short = "other", f"모델 제공 중 · {', '.join(map(route_label, used))} 경로로 사용"
    else:
        key, short = "none", "미도입"
    return {"relKey": key, "relShort": short + (" (가정)" if routes["is_demo"] else ""), "relDemo": routes["is_demo"]}


def model_use(model_id: str, routes: dict | None) -> dict:
    """MODELS[model].use from the --internal-routes file: every route LETSUR uses for the model, whatever the card's
    route, in relation()'s words. A model with no record gets no use, left to the template's default."""
    records = [record for record in (routes or {}).get("records", []) if record["model_id"] == model_id]
    if not records:
        return {}
    used = list(dict.fromkeys(record["route_id"] for record in records if record["route_id"]))
    endpoints = dict.fromkeys(record["endpoint_id"] for record in records if record["route_id"] and record.get("endpoint_id"))
    use = " · ".join([f"{', '.join(map(route_label, used))} 경로로 사용", *(f"endpoint {endpoint}" for endpoint in endpoints)]) if used else "미도입"
    return {"use": use + (" (가정)" if routes["is_demo"] else "")}


def undecided_release(event: dict) -> bool:
    """No type yet, only 'model release or new route': a channel added the model and the maker's release date is unknown."""
    return not event.get("event_type") and sorted(event.get("type_candidates") or []) == ["model_release", "route_change"]


def observed(event: dict) -> bool:
    """Built from collector observations; claims carry no observation_id (claim_kind cannot tell: absorbed events keep it)."""
    return bool(part(event, "after").get("observation_id"))


def source_authority(event: dict, source_key: str) -> str:
    for source in event.get("sources") or []:
        if isinstance(source, dict) and source.get("source_key") == source_key and source.get("authority"):
            return source["authority"]
    return route_authority(event.get("model_id") or "", event.get("route") or "")


def publisher(source_key: str, authority: str, model_id: str, route: str) -> str:
    if source_key in SIGNAL_PUBLISHERS:
        return SIGNAL_PUBLISHERS[source_key][0]
    if authority == "manufacturer_official" and maker(model_id) != "unknown":
        return maker(model_id)
    if authority == "channel_official" and route in ROUTE_PUBLISHERS:
        return ROUTE_PUBLISHERS[route]
    return source_key


class Sources:
    """SRC entries, one per (source_key, run_id), numbered s1, s2, ... in first-use order."""

    def __init__(self):
        self.ids: dict[tuple[str, str], str] = {}
        self.facts: dict[str, dict] = {}

    def add(self, event: dict, source_key: str, run_id: str, url: str = "", seen=None, published=None) -> str:
        sid = self.ids.setdefault((source_key, run_id), f"s{len(self.ids) + 1}")
        facts = self.facts.setdefault(sid, {"source_key": source_key, "run_id": run_id, "authority": source_authority(event, source_key),
                                            "model_id": event.get("model_id") or "", "route": event.get("route") or "",
                                            "url": "", "seen": [], "published": []})
        if not facts["url"] and isinstance(url, str) and url.startswith(("https://", "http://")):
            facts["url"] = url
        if kst(seen):
            facts["seen"].append(kst(seen))
        if published:
            facts["published"].append(published)
        return sid

    def entries(self) -> dict:
        out = {}
        for sid, facts in self.facts.items():
            name = publisher(facts["source_key"], facts["authority"], facts["model_id"], facts["route"])
            when = [f"게시 {min(facts['published'])}"] if facts["published"] else []
            if facts["seen"]:
                when.append(f"조회 {minute(min(facts['seen']))}")
            elif run_time(facts["run_id"]):
                when.append(f"수집 {minute(run_time(facts['run_id']))}")
            elif facts["run_id"]:
                when.append(f"run {facts['run_id']}")
            entry = {"short": sid.upper(), "nm": name if name == facts["source_key"] else f"{name} · {facts['source_key']}",
                     "t": " · ".join(when), "tag": TAG.get(facts["authority"], "ext")}
            if facts["url"]:
                entry["url"] = facts["url"]
            out[sid] = entry
        return out


def register(event: dict, sources: Sources) -> list[tuple[dict, str]]:
    """(ref, SRC key) for each evidence ref, in order."""
    sides = {side["observation_id"]: side for side in (part(event, "before"), part(event, "after")) if side.get("observation_id")}
    refs = [ref for ref in event.get("evidence_refs") or [] if isinstance(ref, dict)]
    keys = {ref.get("source_key") or event.get("source_key") or "" for ref in refs}
    published = published_day(event) if len(keys) == 1 else None
    return [(ref, sources.add(event, ref.get("source_key") or event.get("source_key") or "", ref.get("run_id") or "", ref.get("url") or "",
                              sides.get(ref.get("observation_id") or None, {}).get("observed_at_utc"), published)) for ref in refs]


def quotes(event: dict, refs: list[tuple[dict, str]]) -> list[list]:
    """Quotes for the 출처 section: a claim's own words, or the collector's locator text for the two sides it compared."""
    quoted: dict[str, list[str]] = {}
    for source in event.get("sources") or []:
        if isinstance(source, dict) and source.get("quote"):
            quoted.setdefault(source.get("source_key") or "", []).append(source["quote"])
    out, used, located = [], {}, {}
    for ref, sid in refs:
        if ref.get("observation_id"):
            located[ref["observation_id"]] = (sid, ref.get("locator") or "")
            continue
        key = ref.get("source_key") or event.get("source_key") or ""
        k = used[key] = used.get(key, -1) + 1
        text = quoted[key][k] if k < len(quoted.get(key, [])) else ref.get("locator")
        out.append([sid, f"“{text}”" if text else None])
    labels = ("현재가", "예정가") if event.get("event_type") == "scheduled_change" else ("이전", "현재")
    ids = (part(event, "before").get("observation_id"), part(event, "after").get("observation_id"))
    found = [(*located[oid], label) for oid, label in zip(ids, labels) if oid in located]
    if len(found) == 2 and found[0][0] == found[1][0] and found[0][1] and found[1][1] and labels[0] == "이전":
        out.append([found[0][0], [found[0][1], found[1][1]]])
    else:
        for sid in dict.fromkeys(f[0] for f in found):
            said = " · ".join(f"{label} “{text}”" for s, text, label in found if s == sid and text)
            out.append([sid, said or None])
    out += [[sid, f"“{text}”" if text else None] for oid, (sid, text) in located.items() if oid not in ids]
    return out


def delta_cell(delta, pct, before_amount, currency) -> list | None:
    """[signed amount, '인상 100.00%'] from the engine's own delta and pct; '무료 종료' when the old price was 0."""
    value = number(delta)
    if value is None or value == 0:
        return None
    word, rate = ("인상" if value > 0 else "인하"), number(pct)
    label = f"{word} {format(abs(rate), 'f')}%" if rate is not None else "무료 종료" if number(before_amount) == 0 else word
    return [("+" if value > 0 else "−") + money(abs(value), currency), label]


def stated_discount(event: dict) -> Decimal | None:
    change = part(event, "change")
    return number(change.get("discount_pct")) if change.get("discount_pct_basis") == "stated" else None


def rule_value(side) -> str | dict:
    """A stated condition value: '0.1 x base input price' (collector), '0.1x' (notice), multiplier 0.1 -> '0.1x' (legacy)."""
    side = side if isinstance(side, dict) else {}
    if side.get("value") not in (None, ""):
        return f"{side['value']} {side.get('unit') or ''}".strip()
    if side.get("multiplier") not in (None, ""):
        return f"{side['multiplier']}x"
    return unknown("미확인")


def conflict_value(event: dict, sources: Sources) -> dict:
    """{con: [[amount, SRC key], ...]}: each registered source's own value in the after run, side by side, never one number."""
    after = part(event, "after")
    values = event.get("values_by_source") if isinstance(event.get("values_by_source"), dict) else {}
    return {"con": [[money(amount, after.get("currency") or "USD") or str(amount), sources.add(event, key, after.get("run_id") or "")]
                    for key, amount in sorted(values.items())]}


def event_rows(event: dict, t7: str | None, eff: str, refs: list, sources: Sources) -> list[dict]:
    """Rows this event adds to the 바뀐 내용 table; events that only carry a date add none."""
    sids = list(dict.fromkeys(sid for _, sid in refs))
    mark, item = MARK.get(event.get("status"), "unk"), event.get("billing_item") or ""
    before, after, change = part(event, "before"), part(event, "after"), part(event, "change")
    currency = after.get("currency") or before.get("currency") or "USD"
    b, a = money(before.get("amount"), before.get("currency") or currency), money(after.get("amount"), currency)
    moment = kst(after.get("observed_at_utc"))
    seen = f"관측 {md(day_of(moment))}" if moment else ""

    def row(old, new, moved, when, label="", order=1):
        return {"cond": compared(event.get("conditions")), "item": item, "label": label, "order": order, "same": False,
                "row": [None, old, new, moved, when, mark, sids]}

    if t7 == "예정 변경":
        start = iso(part(event, "dates").get("effective_from")) or iso(after.get("effective_from"))
        moved = delta_cell(change.get("delta"), change.get("pct"), before.get("amount"), currency) if b and a else None
        return [row(b or unknown("현재가 미확인"), a or unknown("예정가 미확인"), moved, f"{md(start)}부터" if start else "")] if b or a else []
    if t7 == "한시 할인":
        rate, a = stated_discount(event), money(change.get("discounted_amount")) or a
        if not (b or a or rate is not None):
            return []
        return [row(b or unknown("공개 안 됨"), a or unknown("할인가 공개 안 됨"),
                    [f"{format(rate, 'f')}%", "공지"] if rate is not None else None, f"~{md(eff)}" if eff else "")]
    if t7 == "조건 변경":
        rule = part(event, "condition")
        if rule:  # observed: the stated rule on both sides, then the item's own price
            key = rule.get("key") or change.get("condition_key") or ""
            first = row(rule_value(rule.get("before")), rule_value(rule.get("after")), None, seen, condition_name(key), 0)
            moved = delta_cell(change.get("price_delta"), change.get("price_pct"), before.get("amount"), currency)
        else:  # claim: key from the subtag, values as stated
            key = next((str(tag).partition(".")[2] for tag in event.get("subtags") or [] if "." in str(tag)), "")
            seen = f"{md(eff)}부터" if eff else ""
            first = row(rule_value(before), rule_value(after), None, seen, condition_name(key), 0)
            moved = delta_cell(change.get("delta"), change.get("pct"), before.get("amount"), currency)
        return [first] + ([row(b, a, moved, seen)] if b and a else [])
    if t7 in ("가격 변경", None):
        conflicted = event.get("status") == "conflicted"
        if conflicted:
            a = conflict_value(event, sources)
        moved = None if conflicted else delta_cell(change.get("delta"), change.get("pct"), before.get("amount"), currency)
        return [row(b, a, moved, seen)] if b and a else []
    return []


def unchanged_rows(event: dict, refs: list) -> list[dict]:
    """The same source's other prices that stayed put, as grey 그대로 rows."""
    after = part(event, "after")
    own = [sid for ref, sid in refs if ref.get("observation_id") and ref.get("observation_id") == after.get("observation_id")]
    sids = own[:1] or list(dict.fromkeys(sid for _, sid in refs))
    moment = kst(after.get("observed_at_utc"))
    rows = []
    for item in event.get("unchanged_items") or []:
        if isinstance(item, dict):
            amount = money(item.get("amount"), after.get("currency") or "USD")
            rows.append({"cond": compared(item.get("conditions")), "item": item.get("billing_item") or "", "label": "", "order": 1,
                         "same": True, "oid": item.get("observation_id"),
                         "row": [None, amount, amount, None, f"관측 {md(day_of(moment))}" if moment else "", "ok", sids]})
    return rows


def eff_rule(t7: str | None, remove: bool, added: bool) -> tuple[str, tuple[str, ...]]:
    """The date a card is filed under, and its label."""
    if t7 == "한시 할인":
        return "할인 종료일", ("end_date", "period_end")
    if t7 == "모델 출시" or added:
        return "제공일", ("route_added_date",)
    if t7 == "제공 종료":
        return "제공 종료일", ("effective_from",)
    if remove:
        return "경로 중단일", ("effective_from",)
    return "적용일", ("effective_from",)


def member_eff(event: dict, fields: tuple[str, ...]) -> tuple[str, str] | None:
    """(date, note) for one event; the note says when the year was read from the notice's publication date."""
    dates = part(event, "dates")
    for field in fields:
        if field != "period_end":
            if iso(dates.get(field)):
                return dates[field], ""
            continue
        end = part(event, "period_end")
        if iso(end.get("date")):
            published = published_day(event)
            note = ""
            if end.get("basis") == "derived_from_publication":
                note = f"공지 게시일({md(published)}) 기준으로 연도를 해석한 날짜" if published else "공지 게시일 기준으로 연도를 해석한 날짜"
            return end["date"], note
    return None


def condition_label(subtags: list[str]) -> str:
    """'과금 · 캐시' from billing.cache_read_multiplier; a key with no label is shown as is."""
    groups: dict[str, list[str]] = {}
    for tag in subtags:
        group, _, key = tag.partition(".")
        if group in CONDITION_GROUPS and key:
            name, table = CONDITION_GROUPS[group]
            groups.setdefault(name, []).extend([ko for token, ko in table if token in key] or [key])
    return " · ".join(f"{name} · {'/'.join(dict.fromkeys(labels))}" for name, labels in groups.items())


def sub_label(t7, members, subtags, remove, added) -> str:
    if t7 == "조건 변경":
        return condition_label(subtags)
    if t7 == "공급 경로 변경":
        return "경로 중단" if remove else "경로 추가" if added else ""
    if t7 == "한시 할인":
        states = {event.get("period_state") for event in members}
        return {"active": "진행 중", "ended": "종료"}.get(states.pop(), "") if len(states) == 1 else ""
    return ""


def flag_text(members: list[dict]) -> str:
    statuses = {event.get("status") for event in members}
    flags = [text for status, text in (("conflicted", "출처 상충"), ("candidate", "공식 확인 전")) if status in statuses]
    kinds = {kind for event in members if not event.get("event_type") for kind in event.get("type_candidates") or []}
    if kinds:
        order = list(T7)
        kinds = sorted(kinds, key=lambda kind: (order.index(kind) if kind in order else len(order), kind))
        flags.append("유형 미정: " + " 또는 ".join(T7.get(kind, kind) for kind in kinds))
    return " · ".join(flags)


def direction(deltas: list) -> str:
    if deltas and all(d is not None and d > 0 for d in deltas):
        return "인상"
    if deltas and all(d is not None and d < 0 for d in deltas):
        return "인하"
    return "변경"


def summary_text(t7, members, eff, sub, q1, remove, added) -> str:
    """List and headline text, made only of model, route, type and date facts."""
    when = md(eff) if eff else ""
    if t7 == "예정 변경":
        priced = [e for e in members if number(part(e, "after").get("amount")) is not None]
        word = direction([number(part(e, "change").get("delta")) for e in members])
        if priced:
            return f"{len(priced)}개 항목 단가 {when}부터 {word} 예정" if eff else f"{len(priced)}개 항목 단가 {word} 예정 · 적용일 미확인"
        return f"단가 {when}부터 변경 예정 공지" if eff else "단가 변경 예정 공지 · 적용일 미확인"
    if t7 == "가격 변경":
        seen = [e for e in members if observed(e)]
        if seen:
            return f"{len(seen)}개 항목 단가 {direction([number(part(e, 'change').get('delta')) for e in seen])} 관측"
        kinds = {e.get("claim_kind") for e in members}
        word = "인상" if kinds == {"price_increase"} else "인하" if kinds == {"price_decrease"} else "변경"
        return f"단가 {word} 언급 · 관측값 확인 전"
    if t7 == "한시 할인":
        rate = next((r for r in map(stated_discount, members) if r is not None), None)
        prices = {money(part(e, "change").get("discounted_amount")) for e in members} - {None}
        parts = [f"{format(rate, 'f')}% 할인" if rate is not None else "할인"]
        if len(prices) == 1:
            parts.append(f"할인가 {prices.pop()}")
        return " · ".join(parts + [f"{when}까지" if eff else "종료일 미확인"])
    if t7 == "조건 변경":
        verb = "관측" if any(observed(e) for e in members) else "언급"
        return f"{sub} 조건 변경 {verb}" if sub else f"조건 변경 {verb}"
    if t7 == "모델 출시":
        return f"모델 출시 · {when} 제공" if eff else "모델 출시 · 제공일 미확인"
    if t7 == "제공 종료":
        return f"모델 제공 종료 {when}" if eff else "모델 제공 종료 · 날짜 미확인"
    if q1:
        return f"경로 추가 {when} · 모델 첫 출시일 미확인" if eff else "경로 추가 · 날짜 미확인 · 모델 첫 출시일 미확인"
    if t7 == "공급 경로 변경":
        what = "경로 중단" if remove else "경로 추가" if added else "경로 변경"
        return f"{what} {when}" if eff else f"{what} · 날짜 미확인"
    return "유형 미분류 이벤트"


def table_head(t7, members) -> list[str]:
    if t7 == "예정 변경":
        until = {iso(part(e, "dates").get("current_rate_until")) for e in members}
        start = {iso(part(e, "dates").get("effective_from")) for e in members}
        return ["항목", f"현재 ({md(min(until))}까지)" if len(until) == 1 and None not in until else "현재",
                f"예정 ({md(min(start))}부터)" if len(start) == 1 and None not in start else "예정", "변동", "적용", "출처"]
    if t7 == "한시 할인":
        return ["항목", "할인 전", "할인가", "할인율", "기간", "출처"]
    return ["항목", "이전", "현재", "변동", "적용", "출처"]


def compare_line(members, rep, ch, t7) -> list[list[str]]:
    """비교 조건: currency, unit, the compared conditions, route, when it was read, and the two observation days."""
    sides = [side for e in members for side in (part(e, "before"), part(e, "after")) if side.get("currency")]
    line = []
    currencies = list(dict.fromkeys(side["currency"] for side in sides))
    units = list(dict.fromkeys(unit_ko(side.get("unit")) for side in sides if side.get("unit")))
    if currencies:
        line.append(["통화", " / ".join(currencies)])
    if units:
        line.append(["단위", " / ".join(units)])
    values: dict[str, set] = {}
    for e in members:
        for key, value in compared(e.get("conditions")).items():
            values.setdefault(key, set()).add(cap(value))
    line += [[CONDITION_KO.get(key, key), " / ".join(sorted(values[key]))] for key in sorted(values)]
    line.append(["경로", ch])
    if part(rep, "announced_via"):
        line.append(["공지 경로", route_label(part(rep, "announced_via").get("route") or "")])
    seen = [m for m in (kst(side.get("observed_at_utc")) for side in sides) if m]
    if seen:
        line.append(["조회", minute(max(seen))])
    pairs = [(kst(part(e, "before").get("observed_at_utc")), kst(part(e, "after").get("observed_at_utc"))) for e in members if observed(e)]
    pairs = [(b, a) for b, a in pairs if b and a]
    if t7 in ("가격 변경", "조건 변경") and pairs:
        line.append(["관측", f"{md(day_of(min(b for b, _ in pairs)))} → {md(day_of(max(a for _, a in pairs)))}"])
    return line


def build_card(card_id: str, members: list[dict], sources: Sources, routes: dict | None) -> dict:
    """One EVENTS entry; members share model, route, type, subtags and date (or a group_key)."""
    rep = representative(members)
    q1 = undecided_release(rep)
    t7 = T7.get(rep.get("event_type")) or ("공급 경로 변경" if q1 else None)
    subtags = list(dict.fromkeys(str(tag) for e in members for tag in e.get("subtags") or []))
    remove, added = "remove" in subtags, q1 or "add" in subtags
    label, fields = eff_rule(t7, remove, added)
    dated = sorted(filter(None, (member_eff(e, fields) for e in members)))
    eff, qual = dated[0] if dated else ("", "")
    ch = route_label(rep.get("route") or "")
    changed, unchanged, said = [], {}, []
    for index, event in enumerate(members):
        refs = register(event, sources)
        said.append(quotes(event, refs))
        changed += [dict(row, eid=index) for row in event_rows(event, t7, eff, refs, sources)]
        for row in unchanged_rows(event, refs):
            unchanged.setdefault(row["oid"] or (row["item"], json.dumps(row["cond"], sort_keys=True)), dict(row, eid=index))
    taken = {(row["item"], json.dumps(row["cond"], sort_keys=True)) for row in changed}
    rows = changed + [row for row in unchanged.values() if (row["item"], json.dumps(row["cond"], sort_keys=True)) not in taken]
    varying = [key for key in sorted({key for row in rows for key in row["cond"]})
               if len({json.dumps(row["cond"].get(key), sort_keys=True) for row in rows}) > 1]
    rows.sort(key=lambda row: (row["same"], [cap(row["cond"].get(key, "")) for key in varying], row["order"], rank(row["item"]), row["item"]))
    for row in rows:
        row["row"][0] = html.escape(" · ".join([cap(row["cond"][key]) for key in varying if key in row["cond"]]
                                               + [row["label"] or item_ko(row["item"])]))
    ev, kept = [], set()
    for index in dict.fromkeys([row["eid"] for row in rows if not row["same"]] + list(range(len(members)))):
        for sid, text in said[index]:
            if (sid, json.dumps(text, ensure_ascii=False)) not in kept:
                kept.add((sid, json.dumps(text, ensure_ascii=False)))
                ev.append([sid, text])
    sub = sub_label(t7, members, subtags, remove, added)
    pairs = dict.fromkeys((side["currency"], unit_ko(side.get("unit")))
                          for e in members for side in (part(e, "before"), part(e, "after")) if side.get("currency"))
    card = {"id": card_id, "t7": t7, "kind": T7.get(rep.get("event_type")) or "유형 미정", "m": rep.get("model_id") or "모델 표기 없음", "mid": rep.get("model_id") or "", "ch": ch,
            "unit": " · ".join(f"{c} / {u}" if u else c for c, u in pairs), "sum": summary_text(t7, members, eff, sub, q1, remove, added),
            **relation(rep, routes), "act": "pending", "real": True,
            "chgHead": table_head(t7, members), "chg": [row["row"] for row in rows], "cond": compare_line(members, rep, ch, t7), "ev": ev}
    phrase = next((part(e, "dates")["end_phrase"] for e in members if part(e, "dates").get("end_phrase")), None) if t7 == "한시 할인" else None
    # A limited discount stays in the list until its end day; card_date (start day first) still keys the card id and the order.
    cd = eff if t7 == "한시 할인" and eff else card_date(rep)
    for key, value in (("effLabel", label if t7 else ""), ("sub", sub), ("flag", flag_text(members)), ("eff", eff), ("effQual", qual),
                       ("effNote", f"원문 “{phrase}”" if phrase and not eff else ""), ("cd", cd), ("ld", last_observed_day(members))):
        if value:
            card[key] = value
    rate = next((r for r in map(stated_discount, members) if r is not None), None)
    if rate is not None:
        card["discPct"] = int(rate) if rate == rate.to_integral_value() else float(rate)
    return card


def trend(cards: list[dict], groups: dict[str, list[dict]], state: dict | None, gaps: list) -> dict:
    """TREND for each model with events: tabs are price condition sets, lines are routes and their gateway endpoints, measures are billing items."""
    series = state.get("series") if isinstance(state, dict) and isinstance(state.get("series"), dict) else None
    suspect = {gap.get("after_observation_id") for gap in gaps if isinstance(gap, dict) and gap.get("kind") in SUSPECT_GAPS} - {None, ""}
    out = {}
    for model_id in dict.fromkeys(card["mid"] for card in cards if card["mid"]):
        entry = model_trend(model_id, [card for card in cards if card["mid"] == model_id], groups, series, suspect)
        if entry:
            out[model_id] = entry
    return out


def model_trend(model_id: str, cards: list[dict], groups: dict[str, list[dict]], series: dict | None, suspect: set) -> dict | None:
    """One TREND entry: observed prices per run on each route line, then the announced future prices that continue them."""
    count = dict.fromkeys(("currency", "suspect", "ambiguous", "unplaced", "unobserved"), 0)
    seen, memo, touched, lines = set(), set(), set(), {}

    def usable(oid, currency) -> bool:
        if oid:
            if oid in seen:
                return False
            seen.add(oid)
        if oid in suspect:
            count["suspect"] += 1
            return False
        if currency != "USD":
            count["currency"] += 1
            return False
        return True

    def tab_key(conditions, unit) -> tuple[str, str]:
        priced_under = {key: value for key, value in compared(conditions).items() if key not in ENDPOINT}
        return json.dumps(priced_under, sort_keys=True, ensure_ascii=False), unit or ""

    def line(tab, key) -> dict:
        return lines.setdefault(tab, {}).setdefault(key, {"runs": {}, "planned": {}, "event": False})

    for value in (series or {}).values():  # the engine stores only published_rate observations here
        obs = value.get("obs") if isinstance(value, dict) else None
        if not isinstance(obs, dict) or obs.get("model_id") != model_id:
            continue
        moment, amount = kst(obs.get("observed_at_utc")), number(obs.get("amount"))
        if moment and amount is not None and usable(obs.get("observation_id"), obs.get("currency")):
            # a line is a route, source and gateway endpoint (what an event names), then upstream, model id and task
            key = (obs.get("route") or "", obs.get("source_key") or "", endpoint(obs.get("conditions")), obs.get("upstream_provider") or "",
                   obs.get("provider_model_id") or "", obs.get("task") or "")
            line(tab_key(obs.get("conditions"), obs.get("unit")), key)["runs"].setdefault(obs.get("run_id") or "", []).append(
                (moment, obs.get("billing_item") or "", float(amount), obs.get("effective_until")))

    loose, planned = [], []
    for card in cards:
        for event in groups[card["id"]]:
            if not observed(event):
                continue
            kind, where = event.get("event_type"), (event.get("route") or "", event.get("source_key") or "", endpoint(event.get("conditions")))
            before, after = part(event, "before"), part(event, "after")
            touched.add((tab_key(event.get("conditions"), after.get("unit") or before.get("unit")), where))
            for side in (before,) if kind == "scheduled_change" else (before, after):
                moment, amount = kst(side.get("observed_at_utc")), number(side.get("amount"))
                if side.get("observation_id") and moment and amount is not None and usable(side["observation_id"], side.get("currency")):
                    loose.append((tab_key(event.get("conditions"), side.get("unit")), where, side.get("run_id") or "",
                                  (moment, event.get("billing_item") or "", float(amount), side.get("effective_until"))))
            start = iso(after.get("effective_from")) or iso(part(event, "dates").get("effective_from"))
            amount = number(after.get("amount"))
            if kind == "scheduled_change" and start and amount is not None:
                if after.get("currency") == "USD":
                    planned.append((tab_key(event.get("conditions"), after.get("unit")), where, (card["id"], start),
                                    event.get("billing_item") or "", float(amount)))
                else:
                    count["currency"] += 1
    for tab, where, run_id, row in loose:
        found = [key for key in lines.get(tab, {}) if key[:3] == where]
        if len(found) > 1:
            count["ambiguous"] += 1
        else:
            line(tab, found[0] if found else where + ("", "", ""))["runs"].setdefault(run_id, []).append(row)
    for tab, where, slot, item, value in planned:
        found = [key for key in lines.get(tab, {}) if key[:3] == where]
        if len(found) == 1:
            lines[tab][found[0]]["planned"].setdefault(slot, {})[item] = value
        else:
            count["unplaced"] += 1
            memo.add(slot[0])
    if not lines:
        return None
    for tab, keyed in lines.items():
        for key, entry in keyed.items():
            entry["event"] = (tab, key[:3]) in touched

    names, tabs, dropped = tab_names(list(lines)), {}, []
    for tab in sorted(lines, key=lambda key: names[key]):
        labels = line_names(list(lines[tab]))
        order = sorted(lines[tab], key=lambda key: (not lines[tab][key]["event"], labels[key]))
        for key in order[MAX_LINES:]:
            dropped.append(f"{names[tab]}: {labels[key]}")
            for slot, values in lines[tab][key]["planned"].items():
                memo.add(slot[0])
                count["unplaced"] += len(values)
        tabs[tab] = [(labels[key], lines[tab][key]) for key in sorted(order[:MAX_LINES], key=lambda key: labels[key])]
    measures = sorted({row[1] for kept in tabs.values() for _, entry in kept for rows in entry["runs"].values() for row in rows},
                      key=lambda item: (rank(item), item))
    drawn = {}
    for tab, kept in tabs.items():
        drawn[names[tab]] = []
        for label, entry in kept:
            pts, until = points(entry, measures, count, memo)
            drawn[names[tab]].append({"name": html.escape(label), "pts": pts, **({"until": until} if until else {})})
    facts = ["수집기 관측 단가(state.json의 최신 관측과 이벤트의 이전·이후 관측)와 공지된 예정 단가(점선)만 그렸습니다. 첫 관측 전 값은 기록이 없어 그리지 않았습니다."
             if series is not None else "state.json을 쓰지 못해 이벤트의 이전·이후 관측만 그렸습니다."]
    for name, text in (("currency", "USD가 아닌 값 {n}건은 그리지 않았습니다."), ("suspect", "검증되지 않은 관측 {n}건(파서 의심)은 뺐습니다."),
                       ("unplaced", "관측 선에 잇지 못한 예정 단가 {n}건은 날짜 메모로 남겼습니다."),
                       ("unobserved", "관측 기록이 없는 항목의 예정 단가 {n}건은 그리지 않았습니다."),
                       ("ambiguous", "연결할 선이 여럿이라 뺀 이벤트 값이 {n}건 있습니다.")):
        if count[name]:
            facts.append(text.format(n=count[name]))
    if dropped:
        facts.append(f"선이 {MAX_LINES}개를 넘어 뺀 선 {len(dropped)}개: {', '.join(dropped)}.")
    notes = sorted([card["eff"], f"{card.get('effLabel') or '적용일'} · {card['ch']}", card["id"]]
                   for card in cards if card["id"] in memo and card.get("eff"))
    entry = {"name": model_id, "unit": " · ".join(dict.fromkeys(f"USD / {unit_ko(tab[1])}" if tab[1] else "USD" for tab in tabs)),
             "real": True, "note": " ".join(facts), "badge": [f"변화 {len(cards)}건", ""],
             "measures": [html.escape(MEASURE_KO.get(item, item)) for item in measures], "notes": notes}
    if len(drawn) == 1:
        entry["series"] = next(iter(drawn.values()))
    else:
        entry["tiers"] = drawn
    return entry


def points(line, measures, count, memo) -> tuple[list, str | None]:
    """Observed points (one per run, values carried forward per item), then the planned ones that can join them."""
    pts, values, last_rows = [], [None] * len(measures), []
    for rows in sorted(line["runs"].values(), key=lambda rows: min(row[0] for row in rows)):
        for _, item, value, _ in sorted(rows, key=lambda row: row[0]):
            values[measures.index(item)] = value
        pts.append([f"{min(row[0] for row in rows):%Y-%m-%dT%H:%M}", list(values)])
        last_rows = rows
    last_day, last, drawn = pts[-1][0][:10], pts[-1][1], False
    for (card_id, start), planned in sorted(line["planned"].items(), key=lambda pair: pair[0][1]):
        if start > last_day and all(value is None or item in planned for item, value in zip(measures, last)):
            pts.append([start, [planned.get(item) if value is not None else None for item, value in zip(measures, last)], card_id])
            count["unobserved"] += sum(1 for item in planned if item not in measures or last[measures.index(item)] is None)
            drawn = True
        else:
            memo.add(card_id)
            count["unplaced"] += len(planned)
    untils = {iso(row[3]) for row in last_rows}
    return pts, (min(untils) if not drawn and len(untils) == 1 and None not in untils else None)


def tab_names(keys) -> dict:
    """'Standard' for {service_tier: standard}; names that would clash spell out keys and unit instead."""
    def short(key):
        conditions = json.loads(key[0])
        return " · ".join(cap(conditions[name]) for name in sorted(conditions)) or "조건 없음"

    names = {key: short(key) for key in keys}
    clash = {name for name in names.values() if list(names.values()).count(name) > 1}
    for key, name in names.items():
        if name in clash:
            conditions = json.loads(key[0])
            names[key] = " · ".join([f"{k} {cap(conditions[k])}" for k in sorted(conditions)] + [unit_ko(key[1]) or "단위 없음"])
    return names


def line_names(keys) -> dict:
    """Route label and gateway endpoint per line; lines still alike add the first key parts (source, upstream, model id, task) that differ."""
    names = {}
    for key in keys:
        who = endpoint_label(key[2])
        same = [other for other in keys if other[0] == key[0] and endpoint_label(other[2]) == who]
        parts = [route_label(key[0]), *([who] if who else [])]
        for index in (1, 3, 4, 5):
            if len(same) == 1:
                break
            if len({other[index] for other in same}) > 1:
                parts.append(key[index] or "-")
                same = [other for other in same if other[index] == key[index]]
        names[key] = " · ".join(parts)
    return names


def collected(report: dict | None) -> dict:
    """COLLECTED.runs: [KST start time, run id] for each run the engine has processed, oldest first."""
    runs = [[f"{run_time(run_id):%Y-%m-%dT%H:%M}", str(run_id)] for run_id in (report or {}).get("processed_runs") or [] if run_time(run_id)]
    return {"runs": sorted(runs)}


def models(cards: list[dict], routes: dict | None) -> dict:
    return {mid: {"prov": maker(mid) if maker(mid) != "unknown" else "", "kind": KIND_KO.get(MODEL_MODALITIES.get(mid), ""),
                  **model_use(mid, routes)}
            for mid in dict.fromkeys(card["mid"] for card in cards if card["mid"])}


def banner(kind: str, title: str, detail: str) -> str:
    return f'<div class="gen-status {kind}"><b>{html.escape(title)}</b> · {html.escape(detail)}</div>'


def warning(continuity: dict | None) -> str:
    kind = (continuity or {}).get("continuity")
    if kind == "continued_with_gaps":
        missed = continuity.get("unprocessed_runs") or []
        return banner("warn", f"처리하지 못한 이전 run {len(missed)}개", "그 run의 변화는 이 화면에 없을 수 있습니다 (continuity continued_with_gaps)")
    if kind in ("broken", "baseline"):
        reason = f" · reason {continuity['reason']}" if continuity.get("reason") else ""
        title = "이전 이력이 끊겼습니다" if kind == "broken" else "기준선 run"
        return banner("warn", title, f"이전 상태 없이 이번 run만으로 만들었습니다 (continuity {kind}{reason})")
    return ""


def internal_routes(path: Path) -> tuple[dict | None, str]:
    """The --internal-routes file, or None and a warning banner when it cannot be used. The page is still written
    with every card unconfirmed, so a bad file never stops the daily page."""
    doc = read_json(path)
    if not path.exists():
        problem = f"{path.name} 없음"
    elif doc is None:
        problem = f"{path.name}을 읽을 수 없음"
    elif not isinstance(doc.get("is_demo"), bool):
        problem = f"{path.name} 형식이 다름 (is_demo가 true·false가 아님)"
    elif not isinstance(doc.get("records"), list):
        problem = f"{path.name} 형식이 다름 (records 목록 없음)"
    else:
        problem = next((f"{path.name} 형식이 다름 (records {number}번째 {reason})"
                        for number, reason in enumerate(map(record_problem, doc["records"]), 1) if reason), "")
    if problem:
        return None, banner("warn", "렛서 사용 경로 파일을 쓰지 못했습니다", f"{problem} · 모든 카드를 사용 경로 확인 필요로 두었습니다")
    return doc, ""


def record_problem(record) -> str:
    """Why one --internal-routes record cannot be used, or "". route_id must be there (null: the model is not
    offered); endpoint_id may be left out."""
    if not isinstance(record, dict) or not isinstance(record.get("model_id"), str) or not record["model_id"]:
        return "model_id 없음"
    if "route_id" not in record:
        return "route_id 없음"
    for key in ("route_id", "endpoint_id"):
        if record.get(key) is not None and not (isinstance(record[key], str) and record[key]):
            return f"{key}가 문자열이나 null이 아님"
    return ""


def empty(today: str, runs: dict) -> dict:
    return {"TODAY": today, "SRC": {}, "ID": {}, "EVENTS": [], "MODELS": {}, "TREND": {}, "COLLECTED": runs}


def render(template: str, data: dict, status: str) -> str:
    return SLOT.sub(lambda m: status if m.group(0) == "<!--STATUS-->" else js(data[m.group(1)]), template)


def collect(root: Path, today: str, routes: dict | None) -> tuple[dict, str]:
    """Page data and the status banner for <root>/events."""
    folder = root / "events"
    doc, report, continuity, state = (read_json(folder / f"{name}.json") for name in ("events", "report", "continuity", "state"))
    problems = []
    if not (folder / "events.json").exists():
        problems.append("events.json 없음")
    elif doc is None:
        problems.append("events.json을 읽을 수 없음")
    elif not isinstance(doc.get("events"), list):
        problems.append("events.json 형식이 다름 (events 목록 없음)")
    for name, data in (("continuity", continuity), ("report", report)):
        if data is None and (folder / f"{name}.json").exists():
            problems.append(f"{name}.json을 읽을 수 없음")
    if continuity is not None and continuity.get("status") != "ok":
        detail = " · ".join(f"{key} {continuity[key]}" for key in ("continuity", "reason", "error") if continuity.get(key))
        problems.append(f"continuity.json status {continuity.get('status')}" + (f" ({detail})" if detail else ""))
    if report is not None and report.get("status") != "ok":
        errors = "; ".join(map(str, report.get("errors") or []))
        problems.append(f"report.json status {report.get('status')}" + (f" (errors {errors})" if errors else ""))
    if problems:
        return empty(today, collected(report)), banner("fail", "오늘 생성 실패", " · ".join(problems))
    groups: dict[str, list[dict]] = {}
    for event in sorted((e for e in doc["events"] if isinstance(e, dict)), key=lambda e: str(e.get("event_id") or "")):
        cid = "g_" + hashlib.sha256(json.dumps(card_key(event), ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
        groups.setdefault(cid, []).append(event)

    def order_key(cid):
        rep = representative(groups[cid])
        return card_date(rep), rep.get("model_id") or "", rep.get("route") or "", rep.get("event_type") or "", cid

    order = sorted(groups, key=order_key)
    sources = Sources()
    cards = [build_card(cid, groups[cid], sources, routes) for cid in order]
    data = {"TODAY": today, "SRC": sources.entries(), "ID": {cid: [e.get("event_id") for e in groups[cid]] for cid in order},
            "EVENTS": cards, "MODELS": models(cards, routes), "TREND": trend(cards, groups, state, doc.get("gaps") or []),
            "COLLECTED": collected(report)}
    return data, warning(continuity)


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.market_collection.dashboard",
                                     description="Write the dashboard page from the events engine output.")
    parser.add_argument("--root", default="data/market_collection")
    parser.add_argument("--output", required=True)
    parser.add_argument("--internal-routes")
    args = parser.parse_args(argv)
    root, out = Path(args.root), Path(args.output)
    if out.exists():
        print(f"output exists: {out}", file=sys.stderr)
        return 2
    today = (now or datetime.now(timezone.utc)).astimezone(KST).date().isoformat()
    template = TEMPLATE.read_text(encoding="utf-8")
    code, cards = 0, 0
    try:
        routes, note = internal_routes(Path(args.internal_routes)) if args.internal_routes else (None, "")
        data, status = collect(root, today, routes)
        page, cards = render(template, data, status + note), len(data["EVENTS"])
    except Exception as error:  # the page must still say today's build failed
        traceback.print_exc()
        page, code = render(template, empty(today, {"runs": []}), banner("fail", "오늘 생성 실패", f"대시보드 생성 중 오류: {type(error).__name__}")), 1
    out.mkdir(parents=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    print(f"wrote {out / 'index.html'}: {cards} cards")
    return code
