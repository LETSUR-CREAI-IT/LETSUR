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
import csv
import hashlib
import html
import json
import re
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.market_collection.dashboard import compare
from scripts.market_collection.events.catalog import ROUTE_PUBLISHERS, SIGNAL_PUBLISHERS, authority as route_authority, maker
from scripts.market_collection.events.evidence import MUTABLE_CONDITIONS
from scripts.market_collection.models import MODEL_MODALITIES
from scripts.market_collection.signals.parsers import SignalParseError, atom_entries, rss_entries, utc_text

KST = timezone(timedelta(hours=9))
TEMPLATE = Path(__file__).with_name("template.html")
SLOT = re.compile(r"/\*DATA:(\w+)\*/|<!--STATUS-->")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
T7 = {"price_change": "가격 변경", "scheduled_change": "예정 변경", "limited_discount": "한시 할인", "condition_change": "조건 변경",
      "condition_price_change": "조건·가격 변경",  # PR #21, 2026-10-23 decision: conditions and price change together
      "model_release": "모델 출시", "model_end": "제공 종료", "route_change": "공급 경로 변경"}
CONDITIONED = ("조건 변경", "조건·가격 변경")
# What the detail page says for an event's gaps and next_checks; fields not listed fall back to the engine's own reason.
GAP_KO = {
    "observed_price": "같은 조건의 관측값이 아직 없음 · 등록된 가격 출처의 다음 수집에서 확인",
    "discount_period_or_target": "할인 기간·대상이 이 경로의 공식 출처에 없음 · 채널의 공식 할인 조건 확인",
    "end_date_year": "종료 문구에 연도가 없음 · 채널 할인 조건에서 연도 확인",
    "sub_provider": "채널 전체가 아니라 안쪽 제공자 한 곳의 할인 · 렛서가 어느 제공자로 연결되는지 확인",
    "account_actual_price": "렛서 계정·등급·지역의 실제 가격은 미확인 · 렛서 계정에서 확인",
    "shutdown_confirmed": "실제 호출 중단은 확인 안 됨 · 날짜가 지나면 다시 확인",
    "scope": "모델 전체·endpoint·경로 중 무엇이 끝나는지 미확인 · 공지 원문에서 범위와 날짜 확인",
    "before": "같은 조건의 현재가가 하나로 정해지지 않음",
    "effective_from_or_rate": "적용일과 새 가격이 둘 다 있어야 확정",
    "raw_check": "원문을 다시 읽은 결과가 맞지 않음 · 원문 대조 필요",
    "negated_statement": "원문에 부정·범위 축소 표현이 있음 · 원문 대조 필요",
    "model_attribution": "한 문장에 모델이 여럿이라 어느 모델 이야기인지 미확정 · 원문 확인",
    "maker_first_release_date": "제조사의 공식 첫 출시일이 없음 · 출시일이 7일 안이면 모델 출시, 아니면 경로 추가",
}
SKIP_GAPS = {"letsur_usage"}  # true of every event until LETSUR gives usage; the page says it once in 현재 사용 경로
NEXT_KO = {
    "confirm the official source still shows the new value on the next run": "다음 수집에서 공식 출처가 새 값을 그대로 보여 주는지 확인",
    "confirm the official source still shows the new condition on the next run": "다음 수집에서 공식 출처가 새 조건을 그대로 보여 주는지 확인",
    "re-observe right after the effective date to confirm the new rate is applied": "적용일 직후 다시 관측해 새 가격이 적용됐는지 확인",
}
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
CONDITION_KO = {"service_tier": "등급", "cache_read_multiplier": "캐시 읽기 배율", "context_length": "컨텍스트", "endpoint_tag": "엔드포인트",
                "endpoint_provider": "제공자", "endpoint_model_slug": "모델 slug", "quantization": "정밀도", "endpoint_status": "상태",
                "endpoint_discount": "엔드포인트 할인", "discount_application": "할인 적용", "price_basis": "가격 기준",
                "region": "지역", "input_token_band": "입력 구간", "precision": "정밀도"}
# Condition values stay as the source wrote them; only tiers and regions get the dashboard's names (2026-10-27 decision 26-1).
TIER_NAMES = {"standard": "Standard", "priority": "Priority", "ultrafast": "Ultrafast", "flex": "Flex", "batch": "Batch"}
REGION_NAMES = {"us": "US", "eu": "EU"}
STATUS_KO = {"-1": "우선순위 낮춤", "-2": "성능 저하", "-3": "순위 하향", "-5": "중단", "-10": "사용 중지"}  # OpenRouter; 0 is normal and not shown
HIDDEN_CONDITIONS = {"price_basis"}  # how the collector read the listing, not a condition a reader compares
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


def size_ko(value) -> str:
    """1048576 -> 1M, 262144 -> 256K: context lengths as people say them."""
    amount = number(value)
    if amount is None or amount <= 0 or amount != amount.to_integral_value():
        return str(value)
    for step, mark in ((1 << 20, "M"), (1 << 10, "K")):
        if amount >= step and amount % (step // 4 or 1) == 0:
            return f"{format((amount / step).normalize(), 'f')}{mark}"
    return format(amount, "f")


def value_text(key: str, value) -> str:
    """A condition value as the source wrote it, except tier and region names; an OpenRouter status in Korean, 0 as nothing."""
    text = str(value)
    if key == "service_tier":
        return TIER_NAMES.get(text.lower(), cap(text))
    if key == "region":
        return REGION_NAMES.get(text.lower(), text)
    if key == "endpoint_status":
        return "" if text == "0" else STATUS_KO.get(text, text)
    return "" if key in HIDDEN_CONDITIONS else text


def condition_value(key: str, value) -> str:
    """One condition value as a row label part; a bare number gets the name of what it counts."""
    if key == "context_length":
        return f"컨텍스트 {size_ko(value)}"
    return value_text(key, value)


def compared(conditions) -> dict:
    return {key: value for key, value in (conditions or {}).items() if key not in IGNORED}


def endpoint(conditions) -> tuple:
    """The stated endpoint facts of a price; they are part of the trend line's key."""
    return tuple(sorted((key, str(value)) for key, value in (conditions or {}).items() if key in ENDPOINT))


DISPLAY_BASIS = {"displayed model quote", "starting listing quote"}  # a model-level 'from' price: no endpoint, tier or precision stated


def precision_group(value) -> str:
    """fp4 also takes mxfp4 and nvfp4, fp8 takes mxfp8 (decision 2); other formats stay as written, unknown is none."""
    text = str(value or "").lower()
    for group in ("fp4", "fp8"):
        if text in (group, f"mx{group}", f"nv{group}"):
            return group
    return "" if text in ("", "unknown") else text


def region_group(conditions) -> str:
    """US or EU from a stated region or an endpoint tag's parts (us, us-east-1 to US; eu, eu-west-1, europe to EU); global is none."""
    stated = conditions or {}
    for part in [str(stated.get("region") or "").lower(), *str(stated.get("endpoint_tag") or "").lower().split("/")[1:]]:
        if part == "us" or part.startswith("us-"):
            return "US"
        if part in ("eu", "europe") or part.startswith("eu-"):
            return "EU"
    return ""


def tab_facts(conditions) -> dict:
    """What splits trend tabs beyond the priced-under conditions: precision group and region (2026-10-27 decisions 21, 26-2)."""
    facts = {"precision": precision_group((conditions or {}).get("quantization")), "region": region_group(conditions)}
    return {key: value for key, value in facts.items() if value}


def reference_line(route: str, facts: tuple) -> bool:
    """A line whose conditions are not settled, as the 공급사 비교 표 leaves it out: a model-level display price, or an endpoint
    whose status is not 0 (OpenRouter's -1 and -2 stay compared there with a chip, decision 8). The strict comparison leaves
    it out and 전체 관측 shows it (2026-10-27 decision 26-2); endpoint facts alone no longer make one."""
    stated = dict(facts)
    status = str(stated.get("endpoint_status") or "0")
    compared = status == "0" or (route == "openrouter" and status in ("-1", "-2"))
    return stated.get("price_basis") in DISPLAY_BASIS or not compared


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


def plain_title(text: str) -> str:
    """Feed titles and the quotes cut from them, compared regardless of dash style, spacing and case."""
    return re.sub(r"\s+", " ", re.sub(r"\s*[—–-]\s*", " - ", text)).strip().lower()


def said_key(quote: str) -> str:
    """What a signal says, without the prices that follow ';' in an endpoint discount line."""
    return plain_title(str(quote or "").split(";")[0])


def feed_days(root: Path) -> dict:
    """The newest signals run: publication day of each feed entry by URL ("days": [(plain title, day)]), what it still
    says ("seen": {(source_key, said_key)}) and the day it ran ("day").

    Events made from a feed entry before the engine kept dates.published carry no date at all; the feed still lists
    the entry with its date, and the newest signals run keeps it (2026-10-27 decision: a feed card's date is its article's).
    A signal the newest run still shows, such as a running endpoint discount, was last observed that day.
    """
    out: dict = {"days": {}, "seen": set(), "day": ""}
    runs = sorted((root / "signals" / "runs").glob("*/signals.csv"))
    if not runs:
        return out
    folder = runs[-1].parent
    moment = run_time(folder.name)
    out["day"] = day_of(moment) if moment else ""
    with runs[-1].open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            day = iso((row.get("published_at_utc") or "")[:10])
            if day and row.get("entry_url"):
                out["days"].setdefault(row["entry_url"], []).append((plain_title(row.get("entry_title") or ""), day))
            out["seen"].add((row.get("source_key") or "", said_key(row.get("quote") or "")))
    # signals.csv keeps only entries in its time window and matched to a model; the run's raw feeds list every entry, read
    # with the collector's own parsers (which refuse DOCTYPE and ENTITY), so older notices get their day too.
    listed = folder / "signal_sources.csv"
    if listed.is_file():
        with listed.open(encoding="utf-8", newline="") as handle:
            raws = [folder / row["raw_path"] for row in csv.DictReader(handle) if row.get("raw_path")]
        for raw in raws:
            try:
                body = raw.read_bytes()
                if not body.lstrip()[:1] == b"<":
                    continue
                entries = atom_entries(body) if b"<feed" in body[:4096] else rss_entries(body)
            except (OSError, SignalParseError):
                continue
            for entry in entries:
                day = iso(utc_text(entry.published)[:10]) if entry.published else None
                if day and entry.url:
                    out["days"].setdefault(entry.url, []).append((plain_title(entry.title), day))
    return out


def article_day(event: dict, feed: dict) -> str:
    """The publication day of the feed entry an undated event was made from: its URL alone, or URL and title."""
    said = [plain_title(str(s.get("quote") or "")) for s in event.get("sources") or [] if isinstance(s, dict)]
    for ref in event.get("evidence_refs") or []:
        entries = feed.get("days", {}).get(ref.get("url") or "", []) if isinstance(ref, dict) else []
        days = {day for _, day in entries}
        if len(days) == 1:
            return days.pop()
        for title, day in entries:
            if title and any(quote.startswith(title) for quote in said):
                return day
    return ""


def seen_day(members: list[dict], feed: dict) -> str:
    """The last observation day of a card with no date of its own: the newest signals run when it still says what a member
    said, else the newest day a member's evidence was collected (KST)."""
    if feed.get("day") and any((s.get("source_key") or e.get("source_key") or "", said_key(s.get("quote") or "")) in feed["seen"]
                               for e in members for s in e.get("sources") or [] if isinstance(s, dict)):
        return feed["day"]
    moments = filter(None, (run_time(ref.get("run_id")) for e in members for ref in e.get("evidence_refs") or [] if isinstance(ref, dict)))
    return max(map(day_of, moments), default="")


def card_key(event: dict) -> list:
    if event.get("group_key"):
        return ["group", event["group_key"], event.get("route") or ""]
    return ["event", event.get("model_id") or "", event.get("route") or "", event.get("event_type") or "",
            sorted(map(str, event.get("subtags") or [])), card_date(event)]


def representative(members: list[dict]) -> dict:
    """The engine's representative, taken among members with a route when a card holds route-less notes too (merge_notes)."""
    pool = [event for event in members if event.get("route")] or members
    return next((event for event in pool if event.get("group_role") == "representative"), pool[0])


def endpoint_discount(event: dict) -> bool:
    """OpenRouter's per-endpoint 'discounted right now' value (pricing.discount in its endpoints API): no start, end or earlier
    price, and the rate moves day to day. Not a card; the 공급사 비교 표 shows today's rate on that endpoint's row
    (2026-10-27 결정, 가안: 12 such cards said only '할인 · 종료일 미확인')."""
    return event.get("event_type") == "limited_discount" and any(
        str(ref.get("locator") or "").endswith("pricing.discount") for ref in event.get("evidence_refs") or [] if isinstance(ref, dict))


GROUP_WINDOW_DAYS = 3  # the engine's grouping window (events/engine.py, 2026-10-19 decision)


def merge_notes(groups: dict[str, list[dict]], feed: dict) -> None:
    """A route-less card the engine grouped with exactly one routed card joins it when their days are at most
    GROUP_WINDOW_DAYS apart: a tracker's note of the same change becomes evidence on the route's card (2026-10-27
    decision 26-3). Notes taken from feeds before the engine kept dates were grouped without one, so the group alone is
    not proof of one change (an 8/16 note sat with a 10/17 observation)."""
    def days(cid):
        found = (card_date(event) or article_day(event, feed) for event in groups[cid])
        return [date.fromisoformat(day) for day in found if day and iso(day)]

    by_group: dict[str, list[str]] = {}
    for cid, members in groups.items():
        if members[0].get("group_key"):
            by_group.setdefault(members[0]["group_key"], []).append(cid)
    for cids in by_group.values():
        routed = [cid for cid in cids if any(event.get("route") for event in groups[cid])]
        if len(routed) != 1:
            continue
        there = days(routed[0])
        for cid in cids:
            here = days(cid) if cid not in routed else []
            if here and there and min(abs((a - b).days) for a in here for b in there) <= GROUP_WINDOW_DAYS:
                groups[routed[0]].extend(groups.pop(cid))


VOLATILE_DAYS, VOLATILE_CARDS = 7, 3  # 변동 잦음 (2026-10-28 가안)


def mark_volatile(cards: list[dict]) -> None:
    """변동 잦음 묶음 (2026-10-28 가안): when the 가격 변경 cards of one model and route number VOLATILE_CARDS or more within
    VOLATILE_DAYS days of each other (by card date), each of them gets the same vg and the list shows them as one row that
    leads to the model view. OpenRouter endpoint prices go up and down every few days, and each run made a pair of cards.
    Scheduled changes, discounts and route-less notes stay separate cards."""
    series: dict[tuple[str, str], list[dict]] = {}
    for card in cards:
        if card["t7"] == "가격 변경" and card["mid"] and card.get("cd") and card["ch"] != route_label(""):
            series.setdefault((card["mid"], card["ch"]), []).append(card)
    for (model_id, route), members in series.items():
        days = sorted(date.fromisoformat(card["cd"][:10]) for card in members)
        if any(sum(1 for later in days[i:] if (later - start).days < VOLATILE_DAYS) >= VOLATILE_CARDS for i, start in enumerate(days)):
            vg = "v_" + hashlib.sha256(json.dumps([model_id, route], ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
            for card in members:
                card["vg"] = vg


def reverted(events: list[dict]) -> set[str]:
    """되돌아간 가격 (2026-10-28, 결정 기록 29절 렛서 답변: 원래 가격으로 돌아온 건은 지금도 바뀐 것처럼 보이지 않게): ids of
    observed price changes whose series, read in observation order, last stands at the price the change started from.
    A series is one model, route, source, billing item and compared conditions; the last change of a series is never
    reverted (nothing came after it), and a change that was changed again to another price is not reverted."""
    series: dict[str, list[dict]] = {}
    for event in events:
        before, after = number(part(event, "before").get("amount")), number(part(event, "after").get("amount"))
        when = part(event, "dates").get("observed_after_utc")
        if event.get("event_type") != "price_change" or before is None or after is None or before == after or not when:
            continue
        key = json.dumps([event.get("model_id"), event.get("route"), event.get("source_key"), event.get("billing_item"),
                          compared(event.get("conditions"))], sort_keys=True, ensure_ascii=False)
        series.setdefault(key, []).append(event)
    out: set[str] = set()
    for changes in series.values():
        changes.sort(key=lambda event: str(part(event, "dates")["observed_after_utc"]))
        last = number(part(changes[-1], "after").get("amount"))
        out.update(str(event.get("event_id")) for event in changes[:-1] if number(part(event, "before").get("amount")) == last)
    return out


def mark_reverted(cards: list[dict], groups: dict[str, list[dict]], gone: set[str]) -> None:
    """rv/rn on a card: how many of its changed prices went back to where they started, of how many changed. A card whose
    prices all went back is 해소됨 (template actionKey and judge() make it RECORD); some is 일부 해소."""
    for card in cards:
        priced = [event for event in groups[card["id"]] if event.get("event_type") == "price_change"
                  and number(part(event, "before").get("amount")) is not None and number(part(event, "after").get("amount")) is not None
                  and number(part(event, "before").get("amount")) != number(part(event, "after").get("amount"))]
        back = sum(1 for event in priced if str(event.get("event_id")) in gone)
        if back:
            card["rv"], card["rn"] = back, len(priced)


def relation(rep: dict, routes: dict | None) -> dict:
    """relKey, relShort and relDemo for the card's model and route from the --internal-routes file.

    A model with no record stays unconfirmed: no record does not mean LETSUR does not use it.
    A card with no route stays unconfirmed too: its source never said which route changed (2026-10-27 decision).
    endpoint_id only adds detail when the route matches."""
    records = [record for record in (routes or {}).get("records", []) if record["model_id"] == rep.get("model_id")]
    if not records:
        return {"relKey": "unk", "relShort": "사용 경로 확인 필요", "relDemo": False}
    route, used = rep.get("route") or "", list(dict.fromkeys(record["route_id"] for record in records if record["route_id"]))
    if not route and used:
        return {"relKey": "unk", "relShort": "경로 표기 없음 : 사용 경로 확인 필요", "relDemo": False}
    if route in used:
        endpoints = dict.fromkeys(record["endpoint_id"] for record in records if record["route_id"] == route and record.get("endpoint_id"))
        key, short = "use", "이 경로로 사용 중" + (" : " + ", ".join(f"endpoint {endpoint}" for endpoint in endpoints) if endpoints else "")
    elif used:
        key, short = "other", f"모델 제공 중 : {', '.join(map(route_label, used))} 경로로 사용"
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
    """SRC entries, one per (source_key, run_id) and, for a feed, per entry, numbered s1, s2, ... in first-use order.

    A feed run holds many notices; one chip per run showed one notice's link and date on every card (2026-10-27 decision 20),
    so each feed entry (its locator) gets its own chip with its own link and publication day.
    """

    def __init__(self):
        self.ids: dict[tuple[str, str, str], str] = {}
        self.facts: dict[str, dict] = {}

    def add(self, event: dict, source_key: str, run_id: str, url: str = "", seen=None, published=None, entry: str = "") -> str:
        sid = self.ids.setdefault((source_key, run_id, entry), f"s{len(self.ids) + 1}")
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


def register(event: dict, sources: Sources, feed: dict | None = None) -> list[tuple[dict, str]]:
    """(ref, SRC key) for each evidence ref, in order; a feed entry's ref gets its own chip dated by its publication day."""
    sides = {side["observation_id"]: side for side in (part(event, "before"), part(event, "after")) if side.get("observation_id")}
    refs = [ref for ref in event.get("evidence_refs") or [] if isinstance(ref, dict)]
    keys = {ref.get("source_key") or event.get("source_key") or "" for ref in refs}
    published = (published_day(event) or article_day(event, feed or {}) or None) if len(keys) == 1 else None
    entry = lambda ref: str(ref.get("locator") or "") if str(ref.get("locator") or "").startswith("feed/") else ""
    return [(ref, sources.add(event, ref.get("source_key") or event.get("source_key") or "", ref.get("run_id") or "", ref.get("url") or "",
                              sides.get(ref.get("observation_id") or None, {}).get("observed_at_utc"), published, entry(ref))) for ref in refs]


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
    if t7 in CONDITIONED:
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
    if t7 in CONDITIONED:
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
    if t7 in CONDITIONED:
        verb = "관측" if any(observed(e) for e in members) else "언급"
        return f"{sub} {t7} {verb}" if sub else f"{t7} {verb}"
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
            text = size_ko(value) if key == "context_length" else value_text(key, value)
            if text:
                values.setdefault(key, set()).add(text)
    line += [[CONDITION_KO.get(key, key), " / ".join(sorted(values[key]))] for key in sorted(values)]
    line.append(["경로", ch])
    if part(rep, "announced_via"):
        line.append(["공지 경로", route_label(part(rep, "announced_via").get("route") or "")])
    seen = [m for m in (kst(side.get("observed_at_utc")) for side in sides) if m]
    if seen:
        line.append(["조회", minute(max(seen))])
    pairs = [(kst(part(e, "before").get("observed_at_utc")), kst(part(e, "after").get("observed_at_utc"))) for e in members if observed(e)]
    pairs = [(b, a) for b, a in pairs if b and a]
    if t7 in ("가격 변경", *CONDITIONED) and pairs:
        line.append(["관측", f"{md(day_of(min(b for b, _ in pairs)))} → {md(day_of(max(a for _, a in pairs)))}"])
    return line


def unique_rows(rows: list[dict]) -> list[dict]:
    """One row per condition set, item and values: the same price read from two sources or runs is one row."""
    seen, out = set(), []
    for row in rows:
        key = (json.dumps(row["cond"], sort_keys=True), row["item"], row["label"], row["same"],
               json.dumps(row["row"][1:4], ensure_ascii=False, sort_keys=True))
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def card_status(members: list[dict]) -> str:
    statuses = {event.get("status") for event in members}
    return "conflicted" if "conflicted" in statuses else "confirmed" if statuses == {"confirmed"} else "candidate"


def checks(members: list[dict]) -> list[list[str]]:
    """[class, text] for what to confirm: check_first gaps and next_checks first, then what limits a value's meaning."""
    out: dict[str, str] = {}
    for event in members:
        for gap in event.get("gaps") or []:
            if not isinstance(gap, dict) or gap.get("field") in SKIP_GAPS:
                continue
            field = str(gap.get("field") or "")
            text = GAP_KO.get(field) or GAP_KO.get(field.rpartition(".")[0]) or gap.get("reason") or field
            out.setdefault(text, "first" if gap.get("class") == "check_first" else "limit")
        for step in event.get("next_checks") or []:
            out.setdefault(NEXT_KO.get(step, step), "next")
    order = {"first": 0, "next": 1, "limit": 2}
    return [[kind, text] for text, kind in sorted(out.items(), key=lambda pair: order[pair[1]])]


def has_values(members: list[dict]) -> bool:
    """Something to read beyond words: an observed or stated price, or a stated discount."""
    return any(observed(e) or number(part(e, "after").get("amount")) is not None or number(part(e, "before").get("amount")) is not None
               or stated_discount(e) is not None or number(part(e, "change").get("discounted_amount")) is not None for e in members)


def build_card(card_id: str, members: list[dict], sources: Sources, routes: dict | None, feed: dict | None = None) -> dict:
    """One EVENTS entry; members share model, route, type, subtags and date (or a group_key)."""
    rep = representative(members)
    feed = feed or {}
    # A card with no date of its own is dated by its feed entry's publication day, else by its last observation day
    # (2026-10-27 decisions 19 and 26-3); the list filter reads cd.
    pub = published_day(rep) or article_day(rep, feed)
    when = card_date(rep) or max((day for day in (article_day(e, feed) for e in members) if day), default="") or seen_day(members, feed)
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
        refs = register(event, sources, feed)
        said.append(quotes(event, refs))
        changed += [dict(row, eid=index) for row in event_rows(event, t7, eff, refs, sources)]
        for row in unchanged_rows(event, refs):
            unchanged.setdefault(row["oid"] or (row["item"], json.dumps(row["cond"], sort_keys=True)), dict(row, eid=index))
    taken = {(row["item"], json.dumps(row["cond"], sort_keys=True)) for row in changed}
    rows = changed + [row for row in unchanged.values() if (row["item"], json.dumps(row["cond"], sort_keys=True)) not in taken]
    varying = [key for key in sorted({key for row in rows for key in row["cond"]})
               if len({json.dumps(row["cond"].get(key), sort_keys=True) for row in rows}) > 1]
    rows.sort(key=lambda row: (row["same"], [cap(row["cond"].get(key, "")) for key in varying], row["order"], rank(row["item"]), row["item"]))
    rows = unique_rows(rows)
    for row in rows:
        group = " · ".join(filter(None, (condition_value(key, row["cond"][key]) for key in varying if key in row["cond"])))
        name = row["label"] or item_ko(row["item"])
        row["row"][0] = html.escape(" · ".join(filter(None, (group, name))))
        row["parts"] = [html.escape(group), html.escape(name)]
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
    for key, value in (("effLabel", label if t7 else ""), ("sub", sub), ("flag", flag_text(members)), ("eff", eff), ("effQual", qual),
                       ("effNote", f"원문 “{phrase}”" if phrase and not eff else ""), ("cd", when), ("ld", last_observed_day(members))):
        if value:
            card[key] = value
    rate = next((r for r in map(stated_discount, members) if r is not None), None)
    if rate is not None:
        card["discPct"] = int(rate) if rate == rate.to_integral_value() else float(rate)
    # Detail page facts the engine already wrote: status, what to confirm, type candidates, the discount's period, row counts
    card["st"] = card_status(members)
    found = checks(members)
    if found:
        card["chk"] = found
    if q1 or not t7:
        candidates = sorted({kind for e in members for kind in e.get("type_candidates") or []}, key=lambda k: (list(T7).index(k) if k in T7 else 99, k))
        if candidates:
            card["tc"] = [T7.get(kind, kind) for kind in candidates]
    if not has_values(members):
        card["clue"] = True
    states = {e.get("period_state") for e in members} - {None}
    if t7 == "한시 할인" and len(states) == 1:
        card["ps"] = states.pop()
    if pub:
        card["pub"] = pub
    if not eff and when:  # no date (decision 12, 10/25): the list cell shows effSeen over cd, effShort its tooltip and the detail (10/27)
        card["effSeen"] = "게시" if when == pub else "관측"
        card["effShort"] = f"{label or '적용일'} 미공지 · {md(when)} {card['effSeen']}"
    card["n"] = [sum(1 for row in rows if not row["same"]), sum(1 for row in rows if row["same"])]
    if any(row["parts"][0] for row in rows):
        card["cg"] = [row["parts"] for row in rows]  # [condition group, item] per chg row, for the grouped 바뀐 내용 table
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
        priced_under.update(tab_facts(conditions))
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
        tabs[tab] = [(labels[key], lines[tab][key], reference_line(key[0], key[2])) for key in sorted(order[:MAX_LINES], key=lambda key: labels[key])]
    # A reference line also joins every tab of its unit whose conditions include all it states; its own tab goes when nothing
    # else was drawn there (2026-10-27 decision 26-2).
    for tab, kept in list(tabs.items()):
        refs, rest, stated = [drawn for drawn in kept if drawn[2]], [drawn for drawn in kept if not drawn[2]], json.loads(tab[0])
        homes = [other for other, there in tabs.items() if other != tab and other[1] == tab[1]
                 and any(not drawn[2] for drawn in there) and all(json.loads(other[0]).get(k) == v for k, v in stated.items())]
        if refs and homes:
            for other in homes:
                tabs[other] = tabs[other] + refs
            if rest:
                tabs[tab] = rest
            else:
                del tabs[tab]
    measures = sorted({row[1] for kept in tabs.values() for _, entry, _ in kept for rows in entry["runs"].values() for row in rows},
                      key=lambda item: (rank(item), item))
    # 그래프 안 최저 counts only lines the newest collection still has (decision 9); older ones are marked old.
    latest = max((run for kept in tabs.values() for _, entry, _ in kept for run in entry["runs"]), default="")
    drawn = {}
    for tab, kept in tabs.items():
        drawn[names[tab]] = []
        for label, entry, ref in kept:
            pts, until = points(entry, measures, count, memo)
            old = bool(latest) and max(entry["runs"], default="") < latest
            drawn[names[tab]].append({"name": html.escape(label), "pts": pts, **({"until": until} if until else {}),
                                      **({"ref": True} if ref else {}), **({"old": True} if old else {})})
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
    full = tab_conditions(list(tabs))
    entry["conds"] = {names[tab]: full[tab] for tab in tabs}  # tab name -> its conditions in the source's words
    if len(drawn) == 1:
        entry["series"] = next(iter(drawn.values()))
    else:
        entry["tiers"] = drawn
        entry["units"] = {names[tab]: f"USD / {unit_ko(tab[1])}" if tab[1] else "USD" for tab in tabs}
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


TAB_QUIET = {"currency_basis", "input_modalities", "region_group", "rate_period"}  # scope notes: only the comparison line says them


def tab_order(conditions: dict) -> list[str]:
    """Precision, region, the other conditions, then tier."""
    rest = sorted(name for name in conditions if name not in ("precision", "region", "service_tier"))
    return [name for name in ("precision", "region") if name in conditions] + rest + (["service_tier"] if "service_tier" in conditions else [])


def tab_text(conditions: dict, name: str) -> str:
    return str(conditions[name]) if name == "precision" else value_text(name, conditions[name])


def tab_value(name: str, text: str) -> str:
    """Short words for the long values direct routes state (Bedrock, OpenAI, Vertex, Anthropic), so the tab row fits the
    panel (2026-10-27 박건 요청); the comparison line above the graph keeps the source's words (tab_conditions)."""
    if found := re.fullmatch(r"(\d+K) input tokens or fewer|<=\s*(\d+K)", text, re.I):
        return f"입력 {found.group(1) or found.group(2)} 이하"
    if found := re.fullmatch(r"more than (\d+K) input tokens|>\s*(\d+K)", text, re.I):
        return f"입력 {found.group(1) or found.group(2)} 초과"
    if name in ("cache_write_ttl", "cache_duration") and (found := re.fullmatch(r"(\d+)(m|h)", text)):
        return f"캐시 {found.group(1)}{'분' if found.group(2) == 'm' else '시간'}"
    if found := re.search(r">\s*(\d+K) input tokens are priced at ([\d.]+)x input and ([\d.]+)x output", text):
        return f"{found.group(1)} 초과 요청 입력 {found.group(2)}배·출력 {found.group(3)}배"
    return text


def tab_conditions(keys) -> dict:
    """Each tab's conditions in the source's words, for the comparison line above the graph and the tab's tooltip."""
    full = {}
    for key in keys:
        conditions = json.loads(key[0])
        full[key] = " · ".join(part for part in (tab_text(conditions, name) for name in tab_order(conditions)) if part) or "조건 없음"
    return full


def tab_names(keys) -> dict:
    """'fp4 · Standard', 'fp8 · US' (a region tab drops Standard), 'Standard' alone where no precision is stated: precision,
    region, other conditions, then tier, written as the comparison table writes them (2026-10-27 decisions 21, 26-1).
    Scope notes (TAB_QUIET) stay out and long source wording is shortened (tab_value). Names that would clash spell out
    keys and unit instead."""
    def short(key):
        conditions = json.loads(key[0])
        regional = tab_text(conditions, "region") in REGION_NAMES.values() if "region" in conditions else False
        parts = [tab_value(name, tab_text(conditions, name)) for name in tab_order(conditions) if name not in TAB_QUIET
                 and not (name == "service_tier" and regional and tab_text(conditions, name) == "Standard")]
        return " · ".join(part for part in parts if part) or "조건 없음"

    names = {key: short(key) for key in keys}
    clash = {name for name in names.values() if list(names.values()).count(name) > 1}
    for key, name in names.items():
        if name in clash:
            conditions = json.loads(key[0])
            names[key] = " · ".join([f"{k} {tab_text(conditions, k)}" for k in tab_order(conditions)] + [unit_ko(key[1]) or "단위 없음"])
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


HEALTH_DAYS = 14  # 수집 상태 탭의 최근 실패 기록 기간


def health(root: Path, report: dict | None, state: dict | None, today: str) -> dict:
    """수집 상태 (2026-10-28 박건 제안, 결정 기록 29절 렛서 답변: 수집 실패는 '변화 없음'과 구분하고 아침에 먼저 보이게). From the
    newest processed run's sources.csv: when it ran, how many sources, which fail now. The engine's source_failed gaps
    (one per failed run; their active flag is not kept up to date) give how long a failure has lasted: it started at the
    earliest run of the unbroken failed runs that ends at the newest run. Failures of the last HEALTH_DAYS days that
    are not failing now say whether the source is fine again or left the registry (not in the newest run). Gaps of
    parser_fingerprint_mismatch count, per source, prices not yet checked again against their raw page (원문 재검증 필요)."""
    out = {"run": None, "days": HEALTH_DAYS, "total": 0, "ok": 0, "failing": [], "recent": [], "recheck": []}
    run = compare.latest_run(root, report)
    rows: list[dict] = []
    if run:
        with (run / "sources.csv").open(encoding="utf-8", newline="") as handle:
            rows = [row for row in csv.DictReader(handle) if row.get("source_key")]
        out["run"] = {"id": run.name, "at": minute(run_time(run.name)) if run_time(run.name) else run.name}
    runs = sorted({str(r) for r in ((state or {}).get("processed_runs") or []) + ((report or {}).get("processed_runs") or [])} | ({run.name} if run else set()))
    gaps = [gap for gap in (state or {}).get("gaps") or [] if isinstance(gap, dict)]
    failed: dict[str, dict[str, dict]] = {}
    for gap in gaps:
        if gap.get("kind") == "source_failed" and gap.get("source_key") and gap.get("run_id"):
            failed.setdefault(str(gap["source_key"]), {})[str(gap["run_id"])] = gap
    newest = {row["source_key"]: row for row in rows}
    bad = lambda row: row.get("fetch_status") != "ok" or row.get("parse_status") != "ok"
    day = lambda run_id: day_of(run_time(run_id)) if run_time(run_id) else ""
    out["total"], out["ok"] = len(rows), sum(1 for row in rows if not bad(row))
    for row in rows:
        if not bad(row):
            continue
        streak = [run.name]
        for earlier in reversed(runs[:runs.index(run.name)]):
            if earlier not in failed.get(row["source_key"], {}):
                break
            streak.append(earlier)
        out["failing"].append({"key": row["source_key"], "model": row.get("model_id") or "", "route": row.get("route") or "", "ch": route_label(row.get("route") or ""),
                               "error": row.get("error") or f"{row.get('fetch_status')} / {row.get('parse_status')}",
                               "since": day(streak[-1]), "sinceRun": streak[-1], "runs": len(streak), "url": row.get("requested_url") or ""})
    start = (date.fromisoformat(today) - timedelta(days=HEALTH_DAYS)).isoformat()
    for key, by_run in failed.items():
        if key in newest and bad(newest[key]):
            continue
        recent = sorted(run_id for run_id in by_run if day(run_id) >= start)
        if recent:
            last = by_run[recent[-1]]
            out["recent"].append({"key": key, "model": last.get("model_id") or "", "route": last.get("route") or "", "ch": route_label(last.get("route") or ""),
                                  "error": last.get("error") or "", "first": day(recent[0]), "last": day(recent[-1]),
                                  "runs": len(recent), "now": "ok" if key in newest else "gone"})
    out["recent"].sort(key=lambda item: (item["last"], item["key"]), reverse=True)
    recheck: dict[str, int] = {}
    for gap in gaps:
        if gap.get("kind") == "parser_fingerprint_mismatch" and gap.get("source_key"):
            recheck[str(gap["source_key"])] = recheck.get(str(gap["source_key"]), 0) + 1
    out["recheck"] = [{"key": key, "model": (newest.get(key) or {}).get("model_id") or "", "ch": route_label((newest.get(key) or {}).get("route") or ""),
                       "n": n} for key, n in sorted(recheck.items(), key=lambda pair: (-pair[1], pair[0]))]
    return out


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
    return {"TODAY": today, "SRC": {}, "ID": {}, "EVENTS": [], "MODELS": {}, "TREND": {}, "COLLECTED": runs, "COMPARE": {},
            "HEALTH": {"run": None, "total": 0, "ok": 0, "failing": [], "recent": [], "recheck": []}}


def render(template: str, data: dict, status: str) -> str:
    return SLOT.sub(lambda m: status if m.group(0) == "<!--STATUS-->" else js(data[m.group(1)]), template)


# ---- Card judgement for the Slack bot (2026-10-27 decisions 26-4, 26-6, 26-7) ----
# template.html decides 추천 행동 (actionKey) and 적용 배지 (applicabilityOf) in the browser. The bot cannot run the page, so
# the same rules run here once more and cards.json carries them with the names the page uses; change both together.
MATRIX = {"use": {"good": "disc", "bad": "route", "neutral": "keep"}, "other": {"good": "route", "bad": "keep", "neutral": "keep"},
          "none": {"good": "route", "bad": "keep", "neutral": "keep"}}
PRICE_WORDS = re.compile("단가|토큰|가격")
APPLICABILITY = {"use": "APPLIES", "unk": "UNKNOWN"}  # other and none: NOT APPLIES


def js_text(value) -> str:
    """String(value) as the template's JS writes it, for the text tests its rules make."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join("" if item is None else js_text(item) for item in value)
    return "[object Object]" if isinstance(value, dict) else str(value)


def page_money(value) -> float | None:
    """The template's money(): a '$1,234.5' cell as a number, anything else none."""
    match = re.fullmatch(r"\$([\d,]+(?:\.\d+)?)", value) if isinstance(value, str) else None
    return float(match.group(1).replace(",", "")) if match else None


def judge(card: dict, today: str) -> dict:
    """추천 행동 (REVIEW, VERIFY, MONITOR, RECORD) and 적용 배지 of one EVENTS card, as template.html's actionKey,
    suggestOf, dirOf and applicabilityOf decide them."""
    t7, sub, rows, eff, st = card.get("t7"), card.get("sub") or "", card.get("chg") or [], card.get("eff") or "", card.get("st")
    flagged = lambda cell: isinstance(cell, dict) and bool(cell.get("unk") or cell.get("con"))
    days = lambda day: (date.fromisoformat(day[:10]) - date.fromisoformat(today)).days
    changed = [row for row in rows if (len(row) > 3 and row[3]) or row[1] != row[2]]
    clue = bool(card.get("clue")) and t7 not in ("공급 경로 변경", "모델 출시", "제공 종료")
    ended = t7 == "한시 할인" and (card.get("ps") == "ended" or (card.get("ps") != "active" and bool(eff) and days(eff) < 0))
    gone = bool(card.get("rv")) and card.get("rv") == card.get("rn")  # template isReverted: every changed price went back
    added = (t7 == "공급 경로 변경" and "중단" not in sub) or t7 == "모델 출시"
    price_unk = any(PRICE_WORDS.search(re.sub(r"<[^>]+>", "", js_text(row[0]))) and flagged(row[2]) for row in rows)
    rel = card.get("relKey") or "unk"

    def direction() -> str:
        priced = [row for row in changed if page_money(row[1]) is not None and page_money(row[2]) is not None]
        total = sum(page_money(row[2]) - page_money(row[1]) for row in priced)
        if t7 == "한시 할인":
            return "bad" if eff and days(eff) < 0 else "good"
        if t7 in ("모델 출시", "제공 종료"):
            return "good" if t7 == "모델 출시" else "bad"
        if t7 == "공급 경로 변경":
            return "bad" if "중단" in sub else "good"
        if priced:
            return "bad" if total > 0 else "good" if total < 0 else "neutral"
        if any(page_money(row[1]) is not None and flagged(row[2]) for row in rows):
            return "unk"
        said = [re.sub(r"<[^>]+>", "", js_text(row[2])) for row in changed]
        return "bad" if any("인상" in text for text in said) else "good" if any("인하" in text for text in said) else "neutral"

    def response() -> str:
        if t7 is None:
            return "hold"
        way = direction()
        resp = "hold" if way == "unk" else MATRIX["use" if rel == "unk" else rel][way]
        if clue:
            return "hold"
        if ended:
            return "keep"
        if added:
            return "keep" if rel == "use" else "route"
        if t7 == "한시 할인" and card.get("discPct") is None and not any(page_money(row[2]) is not None for row in changed):
            return "hold"
        return "hold" if st and st != "confirmed" and resp == "disc" else resp

    if t7 is None or clue or (card.get("flag") and not st) or st == "conflicted" or price_unk:
        action = "VERIFY"
    elif ended or gone:
        action = "RECORD"
    elif rel == "unk":
        action = "VERIFY"
    elif st == "candidate":
        action = "MONITOR"
    else:
        action = {"hold": "VERIFY", "keep": "RECORD"}.get(response(), "REVIEW")
    return {"action": action, "applicability": APPLICABILITY.get(rel, "NOT APPLIES"), "assumed": bool(card.get("relDemo")) and rel != "unk"}


def judgements(data: dict, groups: dict[str, list[dict]]) -> dict:
    """cards.json: per card its events, 추천 행동, 적용 배지, deadline and display names, plus the name tables the page
    uses, so the Slack bot filters (decision 26-4), links to the card (26-6) and writes names as the page does (26-7).
    2026-10-28 (결정 기록 29절 렛서 답변: 알림에 현재 이용 중인 경로인지, 근거 링크와 추가 확인 사항): each card also carries
    usage (현재 사용 경로 as the page writes it), check (its first 확인할 것) and links (up to two https source pages), and
    health.failing lists the sources failing in the newest run for the bot's 수집 점검 alert."""
    routes = {e.get("route"): route_label(e.get("route")) for members in groups.values() for e in members if e.get("route")}

    def links(card: dict) -> list[str]:
        found = (data["SRC"].get(sid, {}).get("url") or "" for sid, _ in card.get("ev") or [])
        return list(dict.fromkeys(url for url in found if url.startswith("https://")))[:2]

    return {"schema": 1, "base_day": data["TODAY"],
            "names": {"routes": routes, "items": ITEM_KO, "conditions": CONDITION_KO, "tiers": TIER_NAMES, "regions": REGION_NAMES,
                      "status": STATUS_KO, "units": UNIT_KO},
            "cards": [{"id": card["id"], "events": data["ID"].get(card["id"], []), **judge(card, data["TODAY"]),
                       "deadline": card.get("eff") or None, "deadline_label": card.get("effLabel") or None,
                       "model": card.get("m") or card.get("mid"), "route": card.get("ch"), "type": card.get("t7"),
                       "summary": card.get("sum"), "conditions": card.get("cond") or [],
                       "usage": card.get("relShort") or card.get("rel") or None, "check": (card.get("chk") or [[None, None]])[0][1],
                       "links": links(card)} for card in data["EVENTS"]],
            "health": {"run": (data.get("HEALTH") or {}).get("run"),
                       "failing": [{key: item[key] for key in ("key", "model", "route", "ch", "error", "since", "sinceRun", "url")}
                                   for item in (data.get("HEALTH") or {}).get("failing") or []]}}


def link_promotions(cards: list[dict], groups: dict[str, list[dict]], tables: dict, routes: dict | None) -> None:
    """A gateway discount that runs out into its upstream's announced price (the 공급사 비교 표 match of decision 16): the
    announced change's cards and the discount cards link to each other ('함께 볼 이벤트'), and on the route LETSUR uses the
    discount card gets the price after it as an estimate and a 주의 line, and so does the announced change (2026-10-27
    결정 1번 가안, gemini-3.8-flash). Each table's promo list is taken out here; the page does not carry it."""
    for model_id, table in tables.items():
        matches = sorted(table.pop("promo", None) or [], key=lambda m: (m["direct"], m["route"], not m["standard"]))
        mine = [card for card in cards if card["mid"] == model_id]
        route = {card["id"]: representative(groups[card["id"]]).get("route") or "" for card in mine}
        changes = {route[card["id"]]: card for card in mine if card["t7"] == "예정 변경" and card.get("eff")}
        sales: dict[str, list[dict]] = {}
        for card in mine:
            if card["t7"] == "한시 할인" and card.get("ps") != "ended":
                sales.setdefault(route[card["id"]], []).append(card)
        pairs = [(m, changes[m["direct"]]) for m in matches
                 if m["direct"] in changes and changes[m["direct"]]["eff"] == m["start"] and m["route"] in sales]
        used = {record["route_id"] for record in (routes or {}).get("records", []) if record["model_id"] == model_id}
        mark = " 가정" if (routes or {}).get("is_demo") else ""
        for gateway in dict.fromkeys(m["route"] for m, _ in pairs):
            change = next(c for m, c in pairs if m["route"] == gateway)
            for sale in sales[gateway]:
                sale["link"] = [change["id"], change["sum"]]
        for change in {c["id"]: c for _, c in pairs}.values():
            gateways = [m["route"] for m, c in pairs if c is change]
            gateway = next((g for g in gateways if g in used), gateways[0])
            change["link"] = [sales[gateway][0]["id"], sales[gateway][0]["sum"]]
            if gateway in used:
                end = sales[gateway][0].get("eff") or (date.fromisoformat(change["eff"]) - timedelta(days=1)).isoformat()
                change["risk"] = (f"렛서{mark} 경로({ROUTE_PUBLISHERS.get(gateway, gateway)})도 {md(end)}에 할인이 끝나 "
                                  f"{md(change['eff'])}부터 같은 가격이 될 수 있음(추정)")
        for gateway in dict.fromkeys(m["route"] for m, _ in pairs if m["route"] in used):
            on = [(m, c) for m, c in pairs if m["route"] == gateway]
            m, change = next(((m, c) for m, c in on if m["standard"]), on[0])  # the Standard row, else the first one
            times = "두 배" if m["times"] == "2" else f"{m['times']}배"
            for sale in sales[gateway]:
                sale["postEst"] = (f"할인 끝난 뒤 {'Standard ' if m['standard'] else ''}{m['after']} "
                                   f"(추정 · {ROUTE_PUBLISHERS.get(m['direct'], m['direct'])} {md(change['eff'])} 가격 기준)")
                sale["risk"] = f"{md(change['eff'])}부터 지금 가격의 {times}가 될 수 있음(추정)"


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
    for event in sorted((e for e in doc["events"] if isinstance(e, dict) and not endpoint_discount(e)), key=lambda e: str(e.get("event_id") or "")):
        cid = "g_" + hashlib.sha256(json.dumps(card_key(event), ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
        groups.setdefault(cid, []).append(event)
    feed = feed_days(root)
    merge_notes(groups, feed)

    def order_key(cid):
        rep = representative(groups[cid])
        return card_date(rep), rep.get("model_id") or "", rep.get("route") or "", rep.get("event_type") or "", cid

    order = sorted(groups, key=order_key)
    sources = Sources()
    cards = [build_card(cid, groups[cid], sources, routes, feed) for cid in order]
    mark_volatile(cards)
    mark_reverted(cards, groups, reverted([e for e in doc["events"] if isinstance(e, dict)]))
    data = {"TODAY": today, "SRC": sources.entries(), "ID": {cid: [e.get("event_id") for e in groups[cid]] for cid in order},
            "EVENTS": cards, "MODELS": models(cards, routes), "TREND": trend(cards, groups, state, doc.get("gaps") or []),
            "COLLECTED": collected(report), "HEALTH": health(root, report, state, today)}
    # 공급사 비교 표 per LLM model with a card (compare.py; reads the newest processed run under <root>/runs)
    data["COMPARE"] = compare.tables(root, [card["mid"] for card in cards], [e for e in doc["events"] if isinstance(e, dict)],
                                     doc.get("gaps") or [], state, report, routes, today)
    link_promotions(cards, groups, data["COMPARE"], routes)
    data["JUDGED"] = judgements(data, groups)  # not a page slot: main writes it to cards.json
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
    judged = {"schema": 1, "base_day": today, "names": {}, "cards": []}
    try:
        routes, note = internal_routes(Path(args.internal_routes)) if args.internal_routes else (None, "")
        data, status = collect(root, today, routes)
        page, cards = render(template, data, status + note), len(data["EVENTS"])
        judged = data.get("JUDGED") or judged
    except Exception as error:  # the page must still say today's build failed
        traceback.print_exc()
        page, code = render(template, empty(today, {"runs": []}), banner("fail", "오늘 생성 실패", f"대시보드 생성 중 오류: {type(error).__name__}")), 1
    out.mkdir(parents=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    # The Slack bot's copy of each card's judgement (decision 26-4); published next to the page and in the Pages artifact.
    (out / "cards.json").write_text(json.dumps(judged, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out / 'index.html'}: {cards} cards")
    return code
