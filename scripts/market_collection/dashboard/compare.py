"""Per-model supplier comparison table ('공급사 비교 표') for the card detail, from the 10/25~27 review decisions.

One table per LLM model the newest run observed, with or without a change card (2026-10-28). A row is one offer: a direct
route, or a gateway (OpenRouter, Vercel) and one upstream endpoint of it (OpenRouter tag, Vercel provider). Rows are
compared only inside a group of the same precision (fp4 takes mxfp4·nvfp4, fp8 takes mxfp8, closed-weight models are
'비공개'), tier, region and context band; a row whose conditions do not place it in a group is '비교 제외' with the reason
and is listed under the table.

Inputs, all read-only:
- the newest core run under <root>/runs the engine processed: amounts of every observation, the excluded
  (not_comparable) ones included, which state.json keeps without amounts;
- that run's Vercel endpoint listings (raw bodies; the run loader checks their hashes): in-row tier, region and peak
  prices, which the collector records only as unknown conditions;
- state.json (baselines, not_comparable, series) for first observation and rows that dropped out, events.json for price
  changes (적용 기간, 가격 변동 큼), Vercel discount notices and Vercel endpoint end dates (deprecated_at).

build.py passes the result to the page as COMPARE; template.html draws it in compareTable(). Rules: docs/dashboard.md.
"""

from __future__ import annotations

import json
import re
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.market_collection.collectors import llm_endpoints
from scripts.market_collection.core import SourceSpec
from scripts.market_collection.events import evidence
from scripts.market_collection.events.catalog import ROUTE_PUBLISHERS, Catalog, maker
from scripts.market_collection.models import MODEL_MODALITIES

KST = timezone(timedelta(hours=9))
ITEMS = ("input", "output", "cache_read")  # price columns; cache writes stay out (their TTL is mostly unstated)
ITEM_KO = {"input": "입력", "output": "출력", "cache_read": "캐시 읽기"}
GATEWAYS = ("openrouter", "vercel_ai_gateway")
TIERS = ("Standard", "Priority", "Ultrafast", "Flex", "Batch")
TIER_OF = {"standard": "Standard", "default": "Standard", "priority": "Priority", "ultrafast": "Ultrafast", "flex": "Flex", "batch": "Batch"}
PRIORITY_LABELS = ("fast", "turbo", "highspeed", "prime", "priority")  # endpoint labels of the priority tier (decisions 2, 14)
REGION_OF = {"us": "US", "eu": "EU", "europe": "EU", "au": "AU", "jp": "JP"}  # by the label's first part: us-east-1 is US (2, 15)
PRECISION_GROUP = {"mxfp4": "fp4", "nvfp4": "fp4", "mxfp8": "fp8"}  # int4 stays its own group (2)
PRECISION_ORDER = ("fp4", "fp8", "int4", "int8", "fp6", "fp16", "bf16", "fp32", "비공개")
CLOSED = "비공개"
OPEN_WEIGHT = ("deepseek", "glm", "kimi")  # model families with public weights; the data has no such field (decision 24)
# OpenRouter endpoint status (decision 8, analysis or-endpoint-status-20261027.md): -1 and -2 stay compared, the rest are excluded.
STATUS = {-1: ("우선순위 낮춤", ""), -2: ("성능 저하", ""), -3: ("순위 하향", "일시적으로 순위가 내려가 대체 경로로만 쓰임"),
          -5: ("중단", "지금 요청이 실패하고 있어 대체 경로로만 쓰임"), -10: ("사용 중지", "품질 문제로 사용 중지, 어떤 요청에도 쓰이지 않음")}
REPRESENTATIVE = ("model_page_rate", "listed_model_rate", "prices_from_minimum")  # a gateway's headline price, no upstream named
# Conditions that tell rows apart or describe them without deciding the group; any other stated key keeps a row out.
QUIET = {"endpoint_tag", "endpoint_provider", "endpoint_model_slug", "context_length", "price_basis", "endpoint_discount", "endpoint_status",
         "quantization", "service_tier", "prompt_tokens", "utc_start", "utc_end", "utc_days", "context_tier_min_tokens",
         "context_tier_max_tokens", "rate_period", "input_token_band", "context_band", "inference_option", "region", "region_group",
         "input_modalities", "currency_basis", "long_context_multiplier", "promotion_note", "promotion_label", "publisher", "hosting",
         "endpoint_scope", "cross_geography_routing", "billing_channel", "meter_scope", "model_version"}
ITEM_LEVEL = {"cache_write_ttl", "cache_duration", *evidence.MUTABLE_CONDITIONS}  # differ between the items of one offer
# Unknown conditions a decision settles: OpenRouter discounts are compared at the shown price (6); Vercel's untiered price
# is Standard and its tier and region prices become rows of their own (10, 18).
SETTLED = {"discount_application", "region_applicability", "service_tier_applicability", "service_tier"}
# One upstream under its gateway spellings, case and punctuation aside (decision 4-8): OpenRouter tag provider, Vercel provider.
# spacexai: Vercel owns xAI's models as spacexai and OpenRouter names them SpaceXAI (10/28 catalogs); both are the maker xAI.
ALIAS = {"togetherai": "together", "bedrock": "amazonbedrock", "vertex": "googlevertex", "vertexanthropic": "googlevertex",
         "google": "googleaistudio", "claudeaws": "claudeonaws", "spacexai": "xai"}
# OpenRouter upstream -> the direct route whose announced change can date its discount (decision 6 exception).
DIRECT_OF = {"googleaistudio": "google_direct", "googlevertex": "google_vertex"}
# Serving precision the provider's own material states for a model, checked by hand on 10/26 (decision 5, analysis
# quant-unknown-rows-20261026.md). price: the documented input/output; a row priced otherwise, or material without a price,
# makes it an estimate. variants: an OpenRouter tag variant with its own note, or None when the material does not cover it.
DOC_PRECISION = {
    ("deepseek-v4.1-flash", "together"): {"value": "fp8", "price": ("0.3", "1.2"), "doc": "Together 문서 표"},
    ("glm-5.2", "together"): {"value": "fp4", "price": ("1.4", "4.4"), "doc": "Together 문서 표·모델 페이지"},
    ("glm-5.3-flash", "together"): {"value": "fp4", "price": ("0.15", "0.5"), "doc": "Together 문서 표"},
    ("kimi-k3", "together"): {"value": "fp4", "price": ("3", "15"), "doc": "Together 모델 페이지"},
    ("kimi-k3", "fireworks"): {"value": "mxfp4", "price": ("3", "15"), "doc": "Fireworks 모델 페이지",
                               "variants": {"fast": "mxfp4 추정 · 기본 행 문서 기준", "us": "mxfp4 추정 · 기본 행 문서 기준", "spot": None}},
    ("kimi-k3", "digitalocean"): {"value": "mxfp4", "price": None, "doc": "DigitalOcean 블로그"},
    ("deepseek-v4.1-flash", "relace"): {"value": "fp4", "price": None, "doc": "Relace API 문서", "note": "fp4 추정 · Relace API 문서 예시 기준 (9/10)"},
    ("glm-5.3-flash", "novita"): {"value": "fp8", "price": ("0.15", "0.5"), "doc": "Novita 모델 페이지"},
    ("deepseek-v4.1-flash", "morph"): {"value": "fp8", "price": ("0.12", "0.8"), "doc": "Morph 모델 피드"},
    ("glm-5.3-flash", "morph"): {"value": "fp8", "price": ("0.2", "0.7"), "doc": "Morph 모델 피드"},
}
# Upstreams whose material is ambiguous: their rows without a stated precision are excluded with the reason (decision 5).
DOC_UNCLEAR = {("deepseek-v4.1-flash", "fireworks"): "체크포인트 설명만", ("glm-5.3-flash", "fireworks"): "공급사 전체 문구만",
               ("glm-5.2", "streamlake"): "공급사 전체 문구만", ("glm-5.3-flash", "streamlake"): "공급사 전체 문구만",
               ("glm-5.2", "cloudflare"): "혼합 형식: 생성 INT4 · 입력 처리 FP8"}
# A note the review attached to one row (decision 7: Particle's peak prices are in its documents, not in the listing).
DOC_NOTE = {("deepseek-v4.1-flash", "vercel_ai_gateway", "particle"): "피크 요금은 공급사 문서에만 있음 · 가격 확인 필요"}
# Azure retail meters priced for one deployment; LETSUR has not said which Azure deployment it uses (2026-10-28).
SCOPE_NOTE = {"Global (Gl)": "글로벌 배포 기준(렛서 배포 방식 미확인)"}
SUSPECT = ("parser_artifact", "csv_raw_mismatch", "parser_fingerprint_mismatch")  # engine gaps: the raw did not confirm the value
WEEKDAY_KO = "월화수목금토일"
WEEKDAY_ISO = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 7}
KEEP_DAYS = 7  # dropped rows and ended discounts stay this many days after the last day (decisions 9, 22)
SPAN_DAYS, SPAN_RATIO = 7, Decimal(2)  # 가격 변동 큼: within the last 7 days the highest is at least twice the lowest (11)
ROUTE_NOTE = ("공개 가중치 모델이라 OpenRouter의 Priority 행은 등급을 지정하지 않은 요청에도 쓰일 수 있습니다. 등급을 standard(default)로 "
              "고정하면 쓰이지 않고, 청구는 실제로 처리한 엔드포인트의 가격을 따릅니다 (OpenRouter Service Tiers 문서, 10/26 조회).")


def number(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def round6(value: Decimal | None) -> Decimal | None:
    """Six significant digits, so 0.61306 / (1 - 0.5621) reads 1.4, not 1.39999…"""
    if not value:
        return value
    return round(value, 5 - value.adjusted()).normalize()


def plain(value: Decimal | None) -> str:
    """0.0341, 1.4, 15: a number as the source writes it."""
    if value is None:
        return "–"
    text = format(value.normalize(), "f")
    return "0" if text == "-0" else text


def money(value: Decimal | None) -> str:
    """'$1.40', as the page's other tables write USD ('$0' when free)."""
    if value is None:
        return "—"
    if value == 0:
        return "$0"
    whole, _, frac = plain(value).partition(".")
    return f"${whole}.{frac.ljust(2, '0')}"


def kst(value) -> datetime | None:
    if not isinstance(value, str) or len(value) <= 10:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(KST)


def run_time(run_id) -> datetime | None:
    try:
        return datetime.strptime(str(run_id)[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(KST)
    except ValueError:
        return None


def md(day) -> str:
    day = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
    return f"{day.month}/{day.day}"


def norm(name) -> str:
    """One upstream under its spellings: z-ai and zai, togetherai and together (decision 4-8)."""
    key = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    return ALIAS.get(key, key)


def precision_group(value: str) -> str:
    return PRECISION_GROUP.get(value, value)


def open_weight(model_id: str) -> bool:
    return model_id.startswith(OPEN_WEIGHT)


def per_million(value) -> Decimal | None:
    try:
        return Decimal(llm_endpoints._per_million(value, "pricing"))
    except ValueError:
        return None


def band_of(tokens) -> str:
    """'272K' for prices above 272,000 input tokens; one-token apart spellings (271999, 272000) are one band."""
    amount = number(tokens)
    return f"{int((amount / 1000).to_integral_value())}K" if amount and amount > 0 else ""


def triple(prices: dict, items: tuple) -> str:
    return " / ".join(plain(prices.get(item)) for item in items)


def same_price(a: dict, b: dict) -> bool:
    """Equal input and output, and equal cache reads where both list one."""
    if any(a.get(item) is None or b.get(item) is None or a[item] != b[item] for item in ("input", "output")):
        return False
    return a.get("cache_read") is None or b.get("cache_read") is None or a["cache_read"] == b["cache_read"]


# ---- reading ---------------------------------------------------------------------------------------------------------

def latest_run(root: Path, report: dict | None) -> Path | None:
    """The newest core run the engine processed (any core run when report.json is absent)."""
    folder = root / "runs"
    if not folder.is_dir():
        return None
    processed = set((report or {}).get("processed_runs") or [])
    runs = sorted(path for path in folder.iterdir()
                  if path.is_dir() and (path / "sources.csv").exists() and (report is None or path.name in processed))
    return runs[-1] if runs else None


def observed_models(root: Path, report: dict | None) -> list[str]:
    """Every model the newest processed core run observed, with or without a change card (2026-10-28: a model starts with
    baselines and no events, yet the 모델별 가격 list must have it). Empty when no run can be read: the page then keeps the
    models with cards."""
    run_dir = latest_run(root, report)
    if run_dir is None:
        return []
    try:
        run = evidence.load_run(run_dir)
        if run["kind"] != "core":
            return []
        return sorted({obs["model_id"] for obs in evidence.core_observations(run, Catalog())[0] if obs.get("model_id")})
    except evidence.RunRejected:
        return []
    except Exception:  # the page without the extra models beats a failure page
        traceback.print_exc()
        return []


def unknown_values(run: dict) -> dict:
    """observation_id -> {condition key: raw value} of its unknown conditions (the engine keeps only the keys)."""
    by_set: dict[tuple, dict] = {}
    for row in run["conditions"]:
        if row["status"] != "stated" or not row["value"]:
            by_set.setdefault((row["source_id"], row["condition_set_id"]), {})[row["condition_key"]] = row["value"]
    return {row["observation_id"]: by_set.get((row["source_id"], row["condition_set_id"]), {}) for row in run["prices"]}


def usable(obs: dict) -> bool:
    return (MODEL_MODALITIES.get(obs.get("model_id")) == "llm" and obs.get("currency") in ("USD", "") and obs.get("unit") in ("1M tokens", "")
            and obs.get("billing_item") in ITEMS and obs.get("price_kind") in ("published_rate", "announced_future_rate"))


def offers(observations: list[dict], unknown: dict) -> list[dict]:
    """Rows: observations of one source, route and conditions (item, rate period and item-level policies aside);
    current prices per item, announced future prices beside them."""
    rows: dict[tuple, dict] = {}
    for obs in observations:
        stated = {k: v for k, v in obs["conditions"].items() if k not in ITEM_LEVEL}
        key = (obs["source_key"], obs["route"], json.dumps({k: v for k, v in stated.items() if k != "rate_period"}, sort_keys=True))
        row = rows.setdefault(key, {"model": obs["model_id"], "route": obs["route"], "source_key": obs["source_key"], "conditions": stated,
                                    "unknown": {}, "prices": {}, "future": {}, "obs": {}, "until": set(), "start": set(), "origin": "listing"})
        row["unknown"].update({k: v for k, v in unknown.get(obs["observation_id"], {}).items() if k not in ITEM_LEVEL})
        amount = number(obs["amount"])
        if obs["price_kind"] == "announced_future_rate":
            if amount is not None and obs.get("effective_from"):
                row["future"][obs["billing_item"]] = amount
                row["start"].add(obs["effective_from"])
            continue
        row["obs"][obs["billing_item"]] = obs
        row["conditions"] = stated
        if amount is not None:
            row["prices"][obs["billing_item"]] = amount
            if obs.get("effective_until"):
                row["until"].add(obs["effective_until"])
    return [row for row in rows.values() if row["obs"]]


# ---- Vercel in-row prices ----------------------------------------------------------------------------------------------

def vercel_prices(pricing: dict) -> tuple[dict, dict, str]:
    """(prices of the first context band, prices above it, that band) from one Vercel pricing object, per million tokens."""
    base, long, band = {}, {}, ""
    for field, item in (("prompt", "input"), ("completion", "output"), ("input_cache_read", "cache_read")):
        tiers = pricing.get(field + "_tiers")
        if isinstance(tiers, list) and tiers:
            for tier in (t for t in tiers if isinstance(t, dict)):
                low = number(tier.get("min", 0)) or 0
                if low == 0:
                    base[item] = per_million(tier.get("cost"))
                else:
                    long[item], band = per_million(tier.get("cost")), band_of(low - 1)
        elif pricing.get(field) is not None:
            base[item] = per_million(pricing[field])
        extra = pricing.get("long_context")
        if isinstance(extra, dict) and extra.get(field) is not None:
            long[item], band = per_million(extra[field]), band_of((number(extra.get("threshold")) or 1) - 1)
    return {k: v for k, v in base.items() if v is not None}, {k: v for k, v in long.items() if v is not None}, band


def peak_text(windows: list, multiplier, base: dict, calendar: str = "") -> str:
    """'평일 한국 시각 10–13시·15–19시에 2배 (0.3 / 1.2 / 0.006)' (decision 7). windows: [(start, end) minutes UTC, ISO weekdays]."""
    spans, days = [], None
    for start, end, week in windows:
        shift = 1 if start + 540 >= 1440 else 0  # the KST start falls on the next day
        a, b = (start + 540) % 1440, (end + 540) % 1440 or 1440
        spans.append("–".join(f"{m // 60}" if m % 60 == 0 else f"{m // 60}:{m % 60:02d}" for m in (a, b)) + "시")
        if week:
            days = sorted({(int(d) - 1 + shift) % 7 + 1 for d in week})
    when = "매일" if days is None else "평일" if days == [1, 2, 3, 4, 5] else "주말" if days == [6, 7] else "·".join(WEEKDAY_KO[d - 1] for d in days)
    rate = number(multiplier)
    text = f"{when} 한국 시각 {'·'.join(spans)}"
    if rate is not None:
        text += f"에 {plain(rate)}배 ({triple({i: v * rate for i, v in base.items()}, ITEMS)})"
    return text + (f" · 휴일 달력 {calendar}" if calendar else "")


def vercel_extras(run: dict, models: set) -> tuple[list[dict], dict, list[tuple[str, str]]]:
    """Rows for the tier and region prices inside Vercel endpoint records, peak texts by (model, upstream), and (model, what could
    not be read)."""
    rows, peaks, problems = [], {}, []
    for item in run["manifest"]["sources"]:
        spec, source = item["spec"], item["source"]
        if spec.get("parser") != "vercel_endpoints_llm" or spec.get("model_id") not in models or source["fetch_status"] != "ok" \
                or source["parse_status"] != "ok" or not source["raw_path"]:
            continue
        try:
            data = llm_endpoints._load((run["dir"] / source["raw_path"]).read_bytes(), llm_endpoints._expected_id(SourceSpec(**spec)))
        except (OSError, TypeError, ValueError) as error:
            problems.append((spec["model_id"], f"{spec.get('source_key')} ({type(error).__name__})"))
            continue
        for endpoint in (e for e in data["endpoints"] if isinstance(e, dict) and isinstance(e.get("pricing"), dict)):
            try:
                found, peak = endpoint_extras(spec, endpoint)
            except (TypeError, ValueError, AttributeError) as error:  # an undocumented field in an unexpected shape: skip that endpoint
                problems.append((spec["model_id"], f"{spec.get('source_key')} {endpoint.get('provider_name')} ({type(error).__name__})"))
                continue
            rows += found
            if peak:
                peaks[(spec["model_id"], norm(endpoint.get("provider_name")))] = peak
    return rows, peaks, problems


def endpoint_extras(spec: dict, endpoint: dict) -> tuple[list[dict], str]:
    """One Vercel endpoint's service_tiers and inference_regions prices as rows, and its peak_pricing as text."""
    provider, pricing = str(endpoint.get("provider_name") or ""), endpoint["pricing"]
    base, base_long, _ = vercel_prices(pricing)
    quantization = endpoint.get("quantization") if endpoint.get("quantization") in llm_endpoints.KNOWN_QUANTIZATIONS else None
    common = {"model": spec["model_id"], "route": "vercel_ai_gateway", "source_key": spec["source_key"], "who": ["Vercel", provider],
              "provider": norm(provider), "precision": quantization, "status": endpoint["status"] if type(endpoint.get("status")) is int else None}
    rows = []
    tiers = pricing.get("service_tiers")
    for name, tier in (tiers.items() if isinstance(tiers, dict) else ()):
        if isinstance(tier, dict) and str(name).lower() in TIER_OF:
            short, long, band = vercel_prices(tier)
            rows += [dict(common, origin="vercel_tier", tier=TIER_OF[str(name).lower()], region="", band=b, prices=p)
                     for b, p in (("", short), (band, long)) if p]
    for region in endpoint.get("inference_regions") or []:
        if isinstance(region, dict) and isinstance(region.get("pricing"), dict) and region.get("geo_region"):
            short, long, band = vercel_prices(region["pricing"])
            label = str(region["geo_region"])
            rows += [dict(common, origin="vercel_region", tier="Standard", region=REGION_OF.get(label.lower().split("-")[0], label.upper()),
                          band=b, prices=p, base=m) for b, p, m in (("", short, base), (band, long, base_long)) if p]
    peak = endpoint.get("peak_pricing") or pricing.get("peak_pricing")
    if not isinstance(peak, dict):
        return rows, ""
    windows = [(int(w.get("start_minute_utc", 0)), int(w.get("end_minute_utc", 0)), w.get("days_of_week"))
               for w in peak.get("windows") or [] if isinstance(w, dict)]
    return rows, peak_text(windows, peak.get("multiplier"), base, str(peak.get("holiday_calendar") or ""))


# ---- one row --------------------------------------------------------------------------------------------------------------

def place(row: dict) -> None:
    """Who, tier, region and context band of a listing row, its stated precision and status, and the source's own labels."""
    c, route = row["conditions"], row["route"]
    row.update(tier="Standard", region="", band="", labels=[], notes=[], why=[], provider="", variant="", status=None, discount=None,
               precision=c.get("quantization") or None)
    raw_status = c.get("endpoint_status", row["unknown"].get("endpoint_status"))
    row["status"] = int(raw_status) if re.fullmatch(r"-?\d+", str(raw_status or "")) else None
    if c.get("price_basis") in REPRESENTATIVE:
        row["who"] = [ROUTE_PUBLISHERS.get(route, route), "모델 페이지 대표가"]
        row["why"].append("모델 페이지 대표가 (하위 공급처·조건 미지정)")
        return
    if "endpoint_tag" in c:  # OpenRouter: provider[/variant][/region][/tier]
        provider, *parts = c["endpoint_tag"].split("/")
        row.update(who=["OpenRouter", c["endpoint_tag"]], provider=norm(provider), discount=number(c.get("endpoint_discount")),
                   tier=TIER_OF.get(str(c.get("service_tier") or "standard").lower(), "Standard"),
                   variant="/".join(p for p in parts if p.lower() not in llm_endpoints.KNOWN_QUANTIZATIONS))
        for part in parts:
            low = part.lower()
            head = low.split("-")[0]
            if low in llm_endpoints.KNOWN_QUANTIZATIONS or low in ("flex", "ultrafast") or (low in PRIORITY_LABELS and row["tier"] != "Priority"):
                continue
            if head in REGION_OF and low != "global":
                row["region"] = REGION_OF[head]
                if low != REGION_OF[head].lower():
                    row["labels"].append(f"표기 {part}")
            else:  # a priority label (fast, turbo …), global, zdr, spot: kept as the source writes it
                row["labels"].append(f"표기 {part}")
        row["band"] = band_of(c.get("prompt_tokens"))
        row["window"] = bool(c.get("utc_start") or c.get("utc_end") or c.get("utc_days"))
    elif "endpoint_provider" in c:  # Vercel endpoint listing
        row.update(who=["Vercel", c["endpoint_provider"]], provider=norm(c["endpoint_provider"]))
        if c.get("context_tier_min_tokens") not in (None, "", "0"):
            row["band"] = band_of((number(c["context_tier_min_tokens"]) or 1) - 1)
    else:  # a direct route
        row["who"] = [ROUTE_PUBLISHERS.get(route, route), route]
        tier = str(c.get("service_tier") or "standard")
        row["tier"] = TIER_OF.get(tier.lower(), tier)
        found = re.search(r"(?:>|more than)\s*(\d+)\s*K", c.get("input_token_band") or c.get("context_band") or "", re.I)
        row["band"] = f"{found.group(1)}K" if found else ""
        label = c.get("region")
        if label and label.lower() != "global":
            row["region"] = REGION_OF.get(label.lower().split("-")[0], "")
            if not row["region"]:
                row["why"].append(f"지역 단위 미정 (표기 {label})")
        if label and label.lower() != row["region"].lower():
            row["labels"].append(f"표기 {label}")
        option = c.get("inference_option")
        if option:  # Bedrock: Global CRIS is unpinned, US CRIS and US East regional are US, In-Region names no region
            row["who"][1] = f"{route} · {option}"
            low = option.lower()
            if low.startswith("us ") or "us east" in low or "us west" in low:
                row["region"] = "US"
            elif low.startswith("eu ") or "europe" in low:
                row["region"] = "EU"
            elif not low.startswith("global"):
                row["why"].append(f"지역 고정 가격 · 지역 단위 미정 (표기 {option})")
        if c.get("region_group") not in (None, "Commercial Regions"):
            row["why"].append(f"조건 맞추기 전 (region_group {c['region_group']})")
        if c.get("billing_channel"):
            row["labels"].append(f"표기 {c['billing_channel']}")
        if c.get("meter_scope"):
            row["labels"].append(SCOPE_NOTE.get(c["meter_scope"], f"표기 {c['meter_scope']}"))
        if c.get("model_version"):  # Azure bills some models per version (2026-10-28); the row is still that model's price
            row["labels"].append(f"버전 {c['model_version']}")
    for key in sorted(set(c) - QUIET):
        row["why"].append(f"조건 맞추기 전 ({key} {c[key]})")


def vercel_row(extra: dict) -> dict:
    return dict(extra, conditions={}, unknown={}, future={}, obs={}, until=set(), start=set(), labels=[], notes=[], why=[], variant="",
                discount=None)


def precision_of(row: dict) -> None:
    """prec_kind: closed (비공개), stated, doc (the provider's material, same price), estimate, or None (unknown)."""
    row["prec_kind"], row["prec_note"] = None, ""
    if not open_weight(row["model"]):
        row["precision"], row["prec_kind"] = CLOSED, "closed"
        return
    if row["precision"]:
        row["prec_kind"] = "stated"
        return
    entry = DOC_PRECISION.get((row["model"], row["provider"])) if row["route"] in GATEWAYS and row["origin"] == "listing" else None
    if not entry:
        return
    variants = entry.get("variants") or {}
    if row["variant"] in variants:
        if variants[row["variant"]]:
            row.update(precision=entry["value"], prec_kind="estimate", prec_note=variants[row["variant"]])
        return
    price = entry["price"]
    row["precision"] = entry["value"]
    if entry.get("note"):
        row.update(prec_kind="estimate", prec_note=entry["note"])
    elif price and same_price(row["prices"], {"input": Decimal(price[0]), "output": Decimal(price[1])}):
        row.update(prec_kind="doc", prec_note=f"{entry['value']} · 공급사 문서 기준 ({entry['doc']})")
    else:
        row.update(prec_kind="estimate", prec_note=f"{entry['value']} 추정 · 공급사 자료 기준 ({'가격 다름' if price else '가격 없음'})")


def settle_precision(rows: list[dict]) -> None:
    """Source conflicts first (decision 5: same upstream, model, tier and price put in two precision groups by two sources ->
    all of them out; two endpoints of one gateway are two products, not a conflict), then borrowing: a row without one takes the
    stated precision of the same upstream on the other gateway at the same price and tier, as an estimate ('추정 (다른 경로 기준)').
    A priority row never lends to a standard one."""
    pool = [r for r in rows if r["route"] in GATEWAYS and r["provider"] and r["prices"] and not r["why"] and r["prec_kind"] != "closed"]
    for row in pool:
        peers = [r for r in pool if r["provider"] == row["provider"] and r["tier"] == row["tier"] and same_price(r["prices"], row["prices"])]
        claims = {("doc" if r["prec_kind"] == "doc" else r["route"], precision_group(r["precision"])) for r in peers if r["prec_kind"] in ("stated", "doc")}
        entry = DOC_PRECISION.get((row["model"], row["provider"]))
        if entry and entry["price"] and same_price(row["prices"], {"input": Decimal(entry["price"][0]), "output": Decimal(entry["price"][1])}):
            claims.add(("doc", precision_group(entry["value"])))
        if any(w1 != w2 and g1 != g2 for w1, g1 in claims for w2, g2 in claims):
            order = {"doc": (0, "공급사 문서"), "openrouter": (1, "OpenRouter"), "vercel_ai_gateway": (2, "Vercel")}
            sides = sorted(claims, key=lambda claim: (order[claim[0]][0], claim[1]))
            text = f"출처 상충 ({', '.join(f'{order[where][1]} {group}' for where, group in sides)}) · 직접 확인 필요"
            for peer in peers:
                peer["conflict"] = text
    for row in pool:
        if row["prec_kind"] or row.get("conflict") or row.get("origin") != "listing":
            continue
        lenders = [r for r in pool if r["provider"] == row["provider"] and r["route"] != row["route"] and r["tier"] == row["tier"]
                   and r["prec_kind"] == "stated" and not r.get("conflict") and same_price(r["prices"], row["prices"])]
        groups = {precision_group(r["precision"]) for r in lenders}
        if len(groups) == 1:
            lender = lenders[0]
            where = "OpenRouter" if lender["route"] == "openrouter" else "Vercel"
            row.update(precision=lender["precision"], prec_kind="estimate",
                       prec_note=f"{lender['precision']} 추정 · 다른 경로 기준 ({where} 표기 {lender['precision']})")


def exclusions(row: dict, peaks: dict) -> None:
    """Why a row stays out of the comparison (decisions 5, 7, 8, 24), most decisive first."""
    why, unknown = row["why"], row["unknown"]
    if not row["prices"]:
        why.insert(0, "가격 미게시")
    if any(text.startswith(("모델 페이지 대표가", "가격 미게시")) for text in why):
        return
    if row["status"] in STATUS and STATUS[row["status"]][1]:
        why.append(f"OpenRouter 상태: {STATUS[row['status']][0]}, {STATUS[row['status']][1]}")
    elif row["status"] not in (None, 0) and row["route"] != "openrouter":
        why.append(row.get("ends") or f"상태 {row['status']} (뜻 미확인)")
    elif row["status"] is None and "endpoint_status" in unknown:
        why.append("상태 미표기")
    if row.get("ends") and row["ends"] not in why:  # a compared row still shows its stated end
        row["notes"].append(row["ends"])
    if row.get("conflict"):
        why.append(row["conflict"])
    peak = peaks.get((row["model"], row["provider"])) if row["route"] == "vercel_ai_gateway" else row.get("peak")
    if peak or "peak_pricing_applicability" in unknown:
        why.append(f"피크 요금: {peak}" if peak else "피크 요금 (시간대 미확인)")
    if row["prec_kind"] is None and not row.get("conflict"):
        unclear = DOC_UNCLEAR.get((row["model"], row["provider"]))
        why.append(f"직접 확인 필요 ({unclear})" if unclear else "정밀도 미상 · 직접 확인 필요")
    if "discount_application" in unknown and row["route"] != "openrouter":
        why.append("할인 표기의 뜻 미확인 (Vercel 문서에 없음)")
    if "service_tier" in unknown and row["route"] != "vercel_ai_gateway":
        why.append("등급 미상")
    rest = sorted(set(unknown) - SETTLED - {"quantization", "endpoint_status", "peak_pricing_applicability"})
    if rest:
        why.append("조건 미상 (" + ", ".join(rest) + ")")


# ---- 한시 할인 columns ------------------------------------------------------------------------------------------------------

def channel_notices(events: list[dict], models: set) -> list[dict]:
    """Vercel limited-discount notices. A re-announced discount (one group_key) counts once, at its latest notice; the
    upstream is read from the quote ('routed through Novita'), none named means the whole route (decision 22)."""
    latest: dict[str, dict] = {}
    for event in events:
        if event.get("event_type") != "limited_discount" or event.get("route") != "vercel_ai_gateway" or event.get("model_id") not in models:
            continue
        key = event.get("group_key") or event.get("event_id")
        published = str((event.get("dates") or {}).get("published") or "")[:10]
        if key not in latest or published > latest[key]["published"]:
            latest[key] = {"event": event, "published": published}
    out = []
    for found in latest.values():
        event = found["event"]
        quote = " ".join(str(s.get("quote") or "") for s in event.get("sources") or [] if isinstance(s, dict))
        named = re.search(r"(?:routed through|served by|on the) ([A-Z][\w.-]*(?: [A-Z][\w.-]*)?)", quote)
        only = re.search(r"\bfor (\w+) (?:agents|users)\b", quote)
        change, period = event.get("change") or {}, event.get("period_end") or {}
        end = period.get("date") or (event.get("dates") or {}).get("end_date")
        pct = number(change.get("discount_pct")) if change.get("discount_pct_basis") == "stated" else None
        out.append({"model": event["model_id"], "route": event["route"], "provider": norm(named.group(1)) if named else "",
                    "only": only.group(1) if only else "", "pct": plain(pct) if pct is not None else "",
                    "end": date.fromisoformat(end) if end and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) else None,
                    "year_guess": period.get("basis") == "derived_from_publication", "ended": event.get("period_state") == "ended",
                    "published": found["published"], "who": ROUTE_PUBLISHERS.get(event["route"], event["route"])})
    return out


def endpoint_ends(events: list[dict], models: set, today: date) -> dict:
    """(model, route, upstream) -> what the row says about the end date a Vercel endpoint listing states in deprecated_at
    (route_change remove events with an endpoint_id, 2026-10-28). The field is not in Vercel's API documentation."""
    out: dict = {}
    for event in events:
        day = str((event.get("dates") or {}).get("effective_from") or "")
        if event.get("event_type") != "route_change" or "remove" not in (event.get("subtags") or []) or not event.get("endpoint_id") \
                or event.get("model_id") not in models or not str(event.get("source_key") or "").startswith("vercel_endpoints") \
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        end = date.fromisoformat(day)
        key = (event["model_id"], event.get("route"), norm(event["endpoint_id"]))
        if key not in out or end < out[key]:
            out[key] = end
    return {key: f"{md(end)} {'종료 예정' if end >= today else '종료일 지남'}(Vercel API, 문서 미기재 필드)" for key, end in out.items()}


def announced(row: dict, rows: list[dict], after: dict) -> dict | None:
    """The direct route's announced change a discounted gateway row runs out into (decision 6 exception, 16): the same
    upstream, tier, region and band, today's price the same, and the price without the discount the announced one."""
    direct = DIRECT_OF.get(row["provider"])
    return next((other for other in rows if direct and other["route"] == direct
                 and (other["tier"], other["region"], other["band"]) == (row["tier"], row["region"], row["band"])
                 and other["future"] and other["start"] and same_price(other["prices"], row["prices"]) and after
                 and all(after.get(i) == round6(v) for i, v in other["future"].items() if i in after)), None)


def discount_cells(row: dict, rows: list[dict], items: tuple, notices: list[dict], today: date) -> None:
    """할인가 · 종료일 · 종료 후 가격 and the note: OpenRouter discounts (decision 6), announced changes (16), promotion end
    dates (4-2), channel notices (22). Estimates, unknowns and lower bounds are never highlighted (3)."""
    c = row["conditions"]
    if row["route"] == "openrouter" and row["discount"]:
        rate = row["discount"]
        after = {item: round6(value / (1 - rate)) for item, value in row["prices"].items()} if rate < 1 else {}
        end = ["미확인", "gy"]
        other = announced(row, rows, after)
        if other:
            end = [f"{md(date.fromisoformat(min(other['start'])) - timedelta(days=1))} (추정 · 공급사 문서 기준)", "est"]
            row["promo"] = {"direct": other["route"], "start": min(other["start"]), "after": after, "rate": rate}
        row["disc"] = [[triple(row["prices"], items), ""], end, [f"{triple(after, items)} (추정)", "est"] if after else ["미확인", "gy"]]
        row["notes"].append(f"할인 {plain(rate * 100)}% · 기간 미공지")
        return
    if row["future"] and row["until"]:
        until = min(row["until"])
        rising = [row["future"].get(i, v) >= v for i, v in row["prices"].items()]
        word = "인상" if all(rising) else "인하" if not any(rising) else "변경"
        row["disc"] = [[triple(row["prices"], items), ""], [md(until), "date"], [triple(row["future"], items), ""]]
        row["end"] = until
        row["notes"].append(f"예정 {word} · 공급사 문서")
        return
    found = re.search(r"at least through ([A-Z][a-z]+ \d{1,2}, \d{4})", str(c.get("promotion_note") or ""))
    if found:
        low = datetime.strptime(found.group(1), "%B %d, %Y").date()
        row["disc"] = [[triple(row["prices"], items), ""], [f"최소 {md(low)}", "min"], ["미확인", "gy"]]
        row["notes"].append("프로모션 가격 · 공급사 문서")
        return
    mine = [n for n in notices if (n["model"], n["route"]) == (row["model"], row["route"]) and (not n["provider"] or n["provider"] == row["provider"])]
    for notice in (n for n in mine if n["only"]):
        row["notes"].append(f"{notice['only']} 전용 {notice['pct']}% 할인 · 할인 칸에 넣지 않음")
    mine = [n for n in mine if not n["only"]]
    ended = sorted((n for n in mine if n["ended"] and n["end"] and (today - n["end"]).days <= KEEP_DAYS), key=lambda n: n["end"])
    live = [n for n in mine if not n["ended"]]
    if not (live or ended):
        return
    notice = (live or ended)[-1]
    end = (f"{md(notice['end'])} (연도 추정)" if notice["year_guess"] else md(notice["end"])) if notice["end"] else "미확인"
    if notice["ended"]:
        row["disc"] = [["종료", "gy"], [end, "gy"], ["미확인", "gy"]]
    else:
        # a notice that states only its rate: the shown price taken as the discounted one, its undiscounted price is an
        # estimate only when it is the upstream's own announced next price (2026-10-27, gemini-3.8-flash on Vercel)
        rate = number(notice["pct"]) / 100 if notice["pct"] else None
        after = {item: round6(value / (1 - rate)) for item, value in row["prices"].items()} if rate and rate < 1 else {}
        other = announced(row, rows, after)
        row["disc"] = [[f"{notice['pct']}% 할인 (공개 안 됨 · 추정 수치)" if notice["pct"] else "할인 (폭 미공개)", ""],
                       [end, "date" if notice["end"] else "gy"],
                       [f"{triple(after, items)} (추정 · 공급사 문서 기준)", "est"] if other else ["미확인", "gy"]]
        if other:
            row["promo"] = {"direct": other["route"], "start": min(other["start"]), "after": after, "rate": rate}
        if notice["end"]:
            row["end"] = notice["end"].isoformat()
    row["notes"].append(f"한시 할인{' ' + notice['pct'] + '%' if notice['pct'] else ''} · {notice['who']} 공지"
                        + (f" {md(notice['published'])}" if notice["published"] else ""))
    row["notes"] += [f"끝난 할인 {n['pct']}% · {md(n['end'])} 종료" for n in ended if n is not notice]


# ---- history ------------------------------------------------------------------------------------------------------------

def condition_key(conditions: dict) -> str:
    return json.dumps({k: v for k, v in (conditions or {}).items() if k not in evidence.MUTABLE_CONDITIONS}, sort_keys=True, ensure_ascii=False)


def spot(source_key, item, conditions) -> tuple:
    """One endpoint's price slot whatever its status, discount or precision label: a gateway row by upstream, tier and band,
    a direct row by its conditions."""
    c = conditions or {}
    who = c.get("endpoint_tag") or c.get("endpoint_provider")
    if who:
        return source_key, item, who, c.get("service_tier", ""), c.get("prompt_tokens", ""), c.get("context_tier_min_tokens", ""), c.get("utc_start", "")
    return source_key, item, condition_key(c)


class History:
    """When each endpoint was first observed (baselines and not_comparable in state.json, whichever comes first, so a status
    or discount change does not restart it) and the price changes of each series (events.json)."""

    def __init__(self, state: dict | None, events: list[dict]):
        state = state if isinstance(state, dict) else {}
        self.first, self.source_first, self.changes = {}, {}, {}
        runs = sorted(state.get("processed_runs") or [])
        self.start = run_time(runs[0]) if runs else None
        for item in (x for key in ("baselines", "not_comparable") for x in state.get(key) or [] if isinstance(x, dict) and x.get("run_id")):
            where = spot(item.get("source_key"), item.get("billing_item"), item.get("conditions"))
            self.first[where] = min(self.first.get(where, item["run_id"]), item["run_id"])
            self.source_first[item.get("source_key")] = min(self.source_first.get(item.get("source_key"), item["run_id"]), item["run_id"])
        for event in events:
            if event.get("event_type") == "price_change" and isinstance(event.get("after"), dict) and isinstance(event.get("before"), dict):
                key = (event.get("source_key"), event.get("billing_item"), condition_key(event.get("conditions")))
                self.changes.setdefault(key, []).append(event)

    def series(self, obs: dict) -> tuple:
        return obs["source_key"], obs["billing_item"], condition_key(obs["conditions"])

    def period(self, row: dict) -> list[str]:
        """['10/24부터', '변경 관측'] / '첫 관측' / '수집 시작일' (decision 1)."""
        changed = [kst(e["after"].get("observed_at_utc")) for obs in row["obs"].values() for e in self.changes.get(self.series(obs), [])]
        changed = [moment for moment in changed if moment]
        if changed:
            return [f"{md(max(changed).date())}부터", "변경 관측"]
        firsts = [self.first.get(spot(o["source_key"], o["billing_item"], o["conditions"])) for o in row["obs"].values()]
        firsts = [run_time(first) for first in firsts if first and run_time(first)]
        if not firsts:
            return ["—", ""]
        first = min(firsts)
        if self.start and first - self.start <= timedelta(hours=24):
            return [f"{md(self.start.date())}부터", "수집 시작일"]  # the whole first collection day (10/16 and that night) is the start
        if first == run_time(self.source_first.get(row["source_key"])):
            return [f"{md(first.date())}부터", "수집 시작일"]  # a source added later: its rows start with it
        return [f"{md(first.date())}부터", "첫 관측"]

    def span(self, row: dict, now: datetime) -> list[tuple[str, Decimal, Decimal]]:
        """Items whose highest price in the last 7 days is at least twice the lowest, with that range (decision 11)."""
        # by collection day (one run a day): the window opens at KST midnight 7 days back, so 10/20 counts on 10/27
        since, out = datetime.combine(now.date() - timedelta(days=SPAN_DAYS), datetime.min.time(), KST), []
        for item, obs in row["obs"].items():
            points = [(kst(obs.get("observed_at_utc")), number(obs.get("amount")))]
            for event in self.changes.get(self.series(obs), []):
                points += [(kst(side.get("observed_at_utc")), number(side.get("amount"))) for side in (event["before"], event["after"])]
            values = [value for moment, value in points if moment and value is not None and moment >= since]
            low, high = (min(values), max(values)) if values else (None, None)
            if values and ((low == 0 and high > 0) or (low > 0 and high >= low * SPAN_RATIO)):
                out.append((item, low, high))
        return out


def dropped(state: dict | None, run_id: str, run_obs: list[dict], statuses: list[dict], models: set, today: date) -> tuple[list, list]:
    """Rows of a stored observation the newest run lacks (decision 9): (rows of a source that failed this run, kept with their
    last values; rows missing from a received source, kept KEEP_DAYS as 관측 끊김). A source no longer in the run is left out."""
    series = (state or {}).get("series")
    status = {s["source_key"]: s["status"] for s in statuses}
    seen = {spot(o["source_key"], o["billing_item"], o["conditions"]) for o in run_obs}
    old = []
    for value in (series.values() if isinstance(series, dict) else ()):
        obs = value.get("obs") if isinstance(value, dict) else None
        if isinstance(obs, dict) and obs.get("model_id") in models and obs.get("run_id") != run_id and usable(obs) \
                and spot(obs.get("source_key"), obs.get("billing_item"), obs.get("conditions") or {}) not in seen:
            old.append(dict(obs, conditions=obs.get("conditions") or {}))
    failed, gone = [], []
    for row in offers(old, {}):
        seen_at = [moment for moment in (kst(o.get("observed_at_utc")) for o in row["obs"].values()) if moment]
        if not seen_at:
            continue
        row["last"] = max(seen_at).date()
        if status.get(row["source_key"]) == "failed":
            failed.append(dict(row, failed=True))
        elif status.get(row["source_key"]) == "ok" and (today - row["last"]).days <= KEEP_DAYS:
            gone.append(dict(row, gone=True))
    return failed, gone


# ---- assembly ----------------------------------------------------------------------------------------------------------

RECORD_CHIP = {"settings": "렛서 설정", "observed": "호출 확인"}  # by the record's basis, as on the cards (build.py route_basis)


def assumed_routes(model_id: str, routes: dict | None) -> list[tuple[str, str, str | None]]:
    """(route, endpoint, basis) of every record that says LETSUR uses a route for the model; none when it has no record or is not offered."""
    return [(r["route_id"], r.get("endpoint_id") or "", r.get("basis")) for r in (routes or {}).get("records", [])
            if r.get("model_id") == model_id and r.get("route_id")]


def is_assumed(row: dict, target: tuple[str, str] | None) -> bool:
    """decision 17: on a gateway, the maker's own upstream at Standard ('Vercel · 제조사'); on a direct route, its Standard row."""
    if not target or row["route"] != target[0] or row["tier"] != "Standard" or row["region"] or row["band"] or row.get("origin") != "listing" \
            or row["who"][1] == "모델 페이지 대표가":
        return False
    if target[1]:
        return row["who"][1] == target[1]
    return row["route"] not in GATEWAYS or (bool(row["provider"]) and row["provider"] == norm(maker(row["model"])))


def assumed_chip(row: dict, targets: list[tuple[str, str, str | None]], demo: bool) -> str:
    """The chip of the first record whose route the row is (is_assumed), "" when none: the record's basis, else 가정 in a demo file,
    else 사용 경로. Every record marks its own route, so gpt-6-astra's OpenAI direct row and its Azure row each say their own basis."""
    for route, endpoint, basis in targets:
        if is_assumed(row, (route, endpoint)):
            return RECORD_CHIP.get(basis) or ("가정" if demo else "사용 경로")
    return ""


def group_name(precision: str, region: str, tier: str, band: str, subtypes: list[str]) -> str:
    """'fp4 · Standard (mxfp4·nvfp4 포함)', 'fp8 · US', 'fp4 · US · Priority', '비공개 · Standard · 입력 272K 초과' (decisions 2, 10, 13)."""
    parts = [precision, *([region] if region else []), *([tier] if tier != "Standard" or not region else []), *([f"입력 {band} 초과"] if band else [])]
    return " · ".join(parts) + (f" ({'·'.join(subtypes)} 포함)" if subtypes else "")


def group_order(key: tuple) -> tuple:
    """Precision, then Standard, Priority, Ultrafast, other tiers, then each region with its tiers right behind (13); bands last."""
    precision, region, tier, band = key
    return (PRECISION_ORDER.index(precision) if precision in PRECISION_ORDER else len(PRECISION_ORDER), precision,
            ("", "US", "EU").index(region) if region in ("", "US", "EU") else 3, region,
            TIERS.index(tier) if tier in TIERS else len(TIERS), tier, band)


def row_order(row: dict) -> tuple:
    rank = 0 if row["route"] not in GATEWAYS else 1 if row["route"] == "openrouter" else 2
    return rank, row["route"], str(row["who"][1]).lower(), row.get("origin", ""), row["band"]


def highlight(rows: list[dict], items: tuple) -> None:
    """Cheapest cell per column, ties all (decisions 1, 4-6); '유일' when the group compares one row (2, 4-7); the latest end date (1).
    Rows of a source not received this run show their last values but take no part (9)."""
    live = [r for r in rows if not r.get("failed")]
    for item in items:
        values = [r["prices"][item] for r in live if r["prices"].get(item) is not None]
        for row in live:
            if row["prices"].get(item) is not None and (len(live) == 1 or row["prices"][item] == min(values)):
                row["mark"][item] = "un" if len(live) == 1 else "lo"
    ends = [r["end"] for r in live if r.get("end")]
    for row in live:
        if row.get("end") and row["end"] == max(ends):
            row["disc"][1][1] = "un" if len(live) == 1 else "lo"


def ratio_text(prices: dict, base: dict) -> str:
    """'기본 가격 대비 1.1배' or '기본 가격과 같음' for a Vercel region price (decision 10)."""
    ratios = {i: round6(prices[i] / base[i]) for i in prices if base.get(i)}
    if not ratios:
        return "기본 가격 미게시"
    if set(ratios.values()) == {1}:
        return "기본 가격과 같음"
    if len(set(ratios.values())) == 1:
        return f"기본 가격 대비 {plain(next(iter(ratios.values())))}배"
    return "기본 가격 대비 " + " · ".join(f"{ITEM_KO[i]} {plain(r)}배" for i, r in ratios.items())


def describe(row: dict, rows: list[dict], history: History, now: datetime) -> None:
    """Note, chips, fill-only flag and 적용 기간 of one row."""
    notes, chips = [], []
    if row.get("assumed"):
        chips.append([row["assumed"], "as"])
    if row["why"] or row.get("gone"):  # listed under the table without a group title: say which conditions it carries
        notes.append(" · ".join(filter(None, (row["tier"] if row["tier"] != "Standard" else "", row["region"],
                                              f"입력 {row['band']} 초과" if row["band"] else ""))))
    if row.get("gone"):
        notes.append(f"관측 끊김 · 마지막 관측 {md(row['last'])}")
    if row.get("failed"):
        chips.append(["수집 실패", "fail"])
        notes.append(f"수집 실패 · 마지막 관측 {md(row['last'])}")
    if row["why"]:
        notes.append("비교 제외 · " + " · ".join(dict.fromkeys(row["why"])))
    if row["prec_note"] and not row.get("conflict"):
        notes.append(row["prec_note"])
    elif row["prec_kind"] == "stated" and precision_group(row["precision"]) != row["precision"]:
        notes.append(row["precision"])  # mxfp4 in the fp4 group (2)
    notes += row["labels"]
    if row.get("origin") == "vercel_tier":
        notes.append("등급을 지정한 요청에만 적용")
    elif row.get("origin") == "vercel_region":
        notes.append(f"{row['region']} 지역을 고정한 요청에만 적용 · {ratio_text(row['prices'], row['base'])}")
    notes += row["notes"]
    if row["prec_kind"] == "estimate":
        chips.append(["추정", "est"])
    if row["status"] in (-1, -2):
        chips.append([STATUS[row["status"]][0], "st"])
    spans = history.span(row, now) if row.get("origin") == "listing" and not row.get("gone") else []
    if spans:
        chips.append(["가격 변동 큼", "vol"])
        notes.append("7일 범위 " + ", ".join(f"{ITEM_KO[i]} {plain(low)}~{plain(high)}" for i, low, high in spans))
    if row.get("conflict"):
        chips.append(["출처 상충", "con"])
    if row.get("suspect"):
        chips.append(["원본 재확인 실패", "sus"])
    if DOC_NOTE.get((row["model"], row["route"], row["provider"])):
        notes.append(DOC_NOTE[(row["model"], row["route"], row["provider"])])
    row["note"], row["chips"], row["mark"] = " · ".join(dict.fromkeys(filter(None, notes))), chips, {}
    row["fill"] = row["prec_kind"] in ("estimate", "closed") or row["status"] in (-1, -2) or bool(spans)  # 3: fill only, no border
    if row.get("origin") == "listing":
        row["period"] = history.period(row) if not row.get("gone") else ["—", ""]
    else:
        base = next((r for r in rows if r.get("origin") == "listing" and r["route"] == row["route"] and r["provider"] == row["provider"]
                     and not r["band"] and r.get("period")), None)
        row["period"] = [base["period"][0], "기본 행 기준"] if base and base["period"][0] != "—" else ["—", ""]


def serialize(row: dict, items: tuple, disc: bool) -> dict:
    out = {"w": row["who"], "c": [[money(row["prices"].get(i)), row["mark"].get(i, "")] for i in items], "p": row["period"], "n": row["note"]}
    if disc:
        out["d"] = row.get("disc") or [["", ""], ["", ""], ["", ""]]
    if row["chips"]:
        out["t"] = row["chips"]
    if row["fill"]:
        out["f"] = 1
    return out


def model_rows(model_id: str, current: list[dict], unknown: dict, extras: list[dict], peaks: dict, suspect: set,
               failed: list[dict], gone: list[dict], ends: dict | None = None) -> list[dict]:
    listing = offers([o for o in current if o["model_id"] == model_id], unknown) + [r for r in failed if r["model"] == model_id]
    for row in listing:
        place(row)
        row["suspect"] = any(o["observation_id"] in suspect for o in row["obs"].values())
    folded = windows(listing)
    listing = [r for r in listing if r not in folded]
    inner = [vercel_row(e) for e in extras if e["model"] == model_id]
    rows = listing + inner
    for row in rows:
        precision_of(row)
        row["ends"] = (ends or {}).get((row["model"], row["route"], row["provider"]), "")
    settle_precision(rows)
    for row in listing:
        exclusions(row, peaks)
    for row in inner:  # decision 10: the Vercel row's own precision as stated; without one it is excluded like a base row
        base = next((r for r in listing if r["route"] == "vercel_ai_gateway" and r["provider"] == row["provider"] and not r["band"]), None)
        if base is not None and base.get("conflict"):
            row["conflict"] = row.get("conflict") or base["conflict"]
        exclusions(row, peaks)
    old = [r for r in gone if r["model"] == model_id]
    for row in old:
        place(row)
        precision_of(row)
        row["why"] = []
    return rows + old


def windows(rows: list[dict]) -> list[dict]:
    """OpenRouter time-of-day prices (UTC window overrides): every row of that endpoint is a peak-priced offer (decision 7);
    the window rows themselves fold into the note. Returns the folded rows."""
    by_tag: dict[tuple, list] = {}
    for row in rows:
        if row["route"] == "openrouter" and row["conditions"].get("endpoint_tag"):
            by_tag.setdefault((row["source_key"], row["conditions"]["endpoint_tag"]), []).append(row)
    folded = []
    for members in by_tag.values():
        timed = sorted((r for r in members if r.get("window") and r["prices"].get("input")), key=lambda r: r["prices"]["input"])
        if not timed:
            continue
        high, low = timed[-1], timed[0]
        c = high["conditions"]
        days = [WEEKDAY_ISO[d.strip().lower()[:3]] for d in str(c.get("utc_days") or "").split(",") if d.strip().lower()[:3] in WEEKDAY_ISO]
        rate = round6(high["prices"]["input"] / low["prices"]["input"]) if len(timed) > 1 else None
        text = peak_text([(hhmm(c.get("utc_start")), hhmm(c.get("utc_end")), days or None)], rate, low["prices"])
        for row in members:
            row["peak"] = text
        folded += timed
    return folded


def hhmm(value) -> int:
    """OpenRouter utc_start/utc_end (HHMM) in minutes after midnight."""
    text = str(value or "0").zfill(4)
    return int(text[:-2]) * 60 + int(text[-2:])


def tables(root: Path, model_ids: list[str], events: list[dict], gaps: list, state: dict | None, report: dict | None,
           routes: dict | None, today_iso: str) -> dict:
    """COMPARE: one table per LLM model in model_ids, keyed by model id; empty when no processed core run can be read.
    An error here leaves the tables out (traceback on stderr) instead of turning the whole daily page into a failure page."""
    try:
        return build_tables(root, model_ids, events, gaps, state, report, routes, today_iso)
    except Exception:  # the page without tables beats a failure page
        traceback.print_exc()
        return {}


def build_tables(root: Path, model_ids: list[str], events: list[dict], gaps: list, state: dict | None, report: dict | None,
                 routes: dict | None, today_iso: str) -> dict:
    models = {m for m in model_ids if MODEL_MODALITIES.get(m) == "llm"}
    run_dir = latest_run(root, report) if models else None
    if run_dir is None:
        return {}
    try:
        run = evidence.load_run(run_dir)
    except evidence.RunRejected:
        return {}
    if run["kind"] != "core":
        return {}
    observations, statuses = evidence.core_observations(run, Catalog())
    run_obs = [o for o in observations if o["model_id"] in models]
    current = [o for o in run_obs if usable(o)]
    unknown = unknown_values(run)
    suspect = {g.get("after_observation_id") for g in gaps or [] if isinstance(g, dict) and g.get("kind") in SUSPECT} - {None, ""}
    extras, peaks, problems = vercel_extras(run, models)
    history, notices, today = History(state, events or []), channel_notices(events or [], models), date.fromisoformat(today_iso)
    ends = endpoint_ends(events or [], models, today)
    now = run_time(run_dir.name)
    failed, gone = dropped(state, run_dir.name, run_obs, statuses, models, today)
    demo = bool((routes or {}).get("is_demo"))
    out = {}
    for model_id in sorted(models):
        rows = model_rows(model_id, current, unknown, extras, peaks, suspect, failed, gone, ends)
        if not rows:
            continue
        targets = assumed_routes(model_id, routes)
        for row in rows:
            row["assumed"] = assumed_chip(row, targets, demo)
            discount_cells(row, rows, ITEMS, notices, today)
        for row in rows:
            describe(row, rows, history, now)
        items = tuple(i for i in ITEMS if any(r["prices"].get(i) is not None for r in rows)) or ITEMS
        compared = [r for r in rows if not r["why"] and not r.get("gone")]
        excluded = sorted((r for r in rows if r["why"] or r.get("gone")), key=row_order)
        disc = any(r.get("disc") for r in rows)
        groups: dict[tuple, list] = {}
        for row in compared:
            groups.setdefault((precision_group(row["precision"]), row["region"], row["tier"], row["band"]), []).append(row)
        named = []
        for key in sorted(groups, key=group_order):
            members = sorted(groups[key], key=row_order)
            highlight(members, items)
            subtypes = sorted({r["precision"] for r in members if r["precision"] not in (key[0], CLOSED)})
            named.append([group_name(*key, subtypes), [serialize(r, items, disc) for r in members]])
        seen = [kst(o.get("observed_at_utc")) for r in rows if not (r.get("failed") or r.get("gone")) for o in r["obs"].values()]
        at = min(filter(None, seen), default=now)
        table = {"at": f"{md(at.date())} {at:%H:%M}", "unit": "USD / 100만 토큰", "items": [ITEM_KO[i] for i in items], "disc": disc,
                 "groups": named, "ex": [serialize(r, items, disc) for r in excluded]}
        if open_weight(model_id) and any(r["route"] == "openrouter" and r["tier"] == "Priority" for r in compared):
            table["special"] = ROUTE_NOTE  # decision 2: open-weight models with an OpenRouter Priority group
        unread = [text for model, text in problems if model == model_id]
        if unread:
            table["foot"] = ["Vercel 엔드포인트 원문을 읽지 못해 행 안 등급·지역 가격을 뺀 출처: " + ", ".join(unread)]
        promo = [{"route": r["route"], "direct": r["promo"]["direct"], "start": r["promo"]["start"],
                  "standard": r["tier"] == "Standard" and not r["region"] and not r["band"],
                  "after": " / ".join(money(r["promo"]["after"].get(i)) for i in items), "times": plain(round(1 / (1 - r["promo"]["rate"]), 2))}
                 for r in rows if r.get("promo")]
        if promo:
            table["promo"] = promo  # build.py takes it out for the cards (link_promotions) before the page gets the table
        out[model_id] = table
    return out
