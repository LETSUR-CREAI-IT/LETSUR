"""Dashboard page: events engine output filled into the data slots of the 사용자뷰 1011 template.

Events start from the engine's own base_event and carry the fields each kind of
event has in events.json. The built page's data slots are read back as JSON.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.market_collection.dashboard import build
from scripts.market_collection.events.engine import base_event

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)  # 2026-10-21 00:00 KST
RUN1 = "20261004T062258Z-af1a11a5b11a4260956c51559ca448ff"  # 2026-10-04 15:22 KST
RUN2 = "20261005T073538Z-72ba62e3895042e4b9b844c3896936a9"  # 2026-10-05 16:35 KST
AT1 = "2026-10-04T06:22:58.627763Z"
AT2 = "2026-10-05T07:35:38.120000Z"
GOOGLE = "google_gemini_38_flash"
URL = "https://ai.google.dev/gemini-api/docs/pricing"
REPORT = {"status": "ok", "processed_runs": [RUN1, RUN2]}
SLOTS = ("SRC", "ID", "EVENTS", "MODELS", "TREND", "COLLECTED", "SUPPLY")
DEMO_ROUTES = Path(__file__).resolve().parents[2] / "samples/market_collection/demo_internal_routes.json"


def _side(amount: str, run_id: str, observation_id: str, observed_at: str, **extra: str) -> dict:
    return {"amount": amount, "currency": "USD", "unit": "1M tokens", "observation_id": observation_id, "run_id": run_id,
            "observed_at_utc": observed_at, "effective_from": "", "effective_until": "", **extra}


def _ref(source_key: str, run_id: str, observation_id: str = "", locator: str = "", url: str = "") -> dict:
    return {"run_id": run_id, "source_id": "", "observation_id": observation_id, "raw_path": "", "raw_sha256": "",
            "locator": locator, "source_key": source_key, "url": url}


def _source(source_key: str, authority: str, quote: str = "") -> dict:
    return {"source_key": source_key, "authority": authority, "role": "pricing", "scope": [], **({"quote": quote} if quote else {})}


def _event(event_id: str, event_type: str | None, status: str = "confirmed", model_id: str = "gemini-3.8-flash",
           route: str = "google_direct", **fields) -> dict:
    event = base_event(event_id, event_type, model_id, route, status)
    event.update(fields)
    return event


def _observed(event_id: str, event_type: str, before: str, after: str, item: str = "input", status: str = "confirmed", **fields) -> dict:
    """A change between two collector observations of GOOGLE: before in RUN1, after in RUN2."""
    return _event(event_id, event_type, status, source_key=GOOGLE, billing_item=item, conditions={"service_tier": "standard"},
                  before=_side(before, RUN1, f"{event_id}-1", AT1), after=_side(after, RUN2, f"{event_id}-2", AT2),
                  dates={"observed_before_utc": AT1, "observed_after_utc": AT2, "published": None, "effective_from": None, "note": ""},
                  evidence_refs=[_ref(GOOGLE, RUN1, f"{event_id}-1", f"{item} {before}"), _ref(GOOGLE, RUN2, f"{event_id}-2", f"{item} {after}")],
                  sources=[_source(GOOGLE, "manufacturer_official")], **fields)


def _scheduled(event_id: str, item: str, now: str, later: str, delta: str) -> dict:
    """Today's rate and the announced one, both read from GOOGLE in RUN1."""
    return _event(event_id, "scheduled_change", source_key=GOOGLE, billing_item=item, conditions={"service_tier": "standard"},
                  before=_side(now, RUN1, f"{event_id}-now", AT1, effective_until="2026-12-31"),
                  after=_side(later, RUN1, f"{event_id}-later", AT1, effective_from="2027-01-01"),
                  change={"delta": delta, "pct": "100.00"},
                  dates={"current_rate_until": "2026-12-31", "effective_from": "2027-01-01", "first_observed_utc": AT1,
                         "published": None, "note": ""},
                  evidence_refs=[_ref(GOOGLE, RUN1, f"{event_id}-now", f"{item} now", URL),
                                 _ref(GOOGLE, RUN1, f"{event_id}-later", f"{item} later", URL)],
                  sources=[_source(GOOGLE, "manufacturer_official")])


def _claim(event_id: str, event_type: str | None, source_key: str, quote: str, status: str = "candidate", **fields) -> dict:
    """An event from a notice: one ref without an observation id and the notice's own words."""
    return _event(event_id, event_type, status, source_key=source_key, evidence_refs=[_ref(source_key, RUN2, locator="item 1")],
                  sources=[dict(_source(source_key, "channel_official", quote), lineage="l1")], **fields)


def _stored(observation_id: str, item: str, amount: str) -> dict:
    """A state.json series entry: the latest observation, read in RUN2."""
    return {"obs": {"observation_id": observation_id, "run_id": RUN2, "observed_at_utc": AT2, "model_id": "gemini-3.8-flash",
                    "route": "google_direct", "source_key": GOOGLE, "upstream_provider": "Google", "provider_model_id": "gemini-3.8-flash",
                    "task": "text_generation", "billing_item": item, "amount": amount, "currency": "USD", "unit": "1M tokens",
                    "conditions": {"rate_period": "through", "service_tier": "standard"}, "effective_until": "2026-12-31"}}


def _files(tmp_path: Path, **files) -> None:
    """<tmp>/root/events/<name>.json; a str is written as is."""
    folder = tmp_path / "root" / "events"
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder / f"{name}.json").write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write(tmp_path: Path, events: list[dict], gaps: list | None = None, **files) -> None:
    _files(tmp_path, events={"schema_version": 1, "policy": "all_verified", "events": events, "gaps": gaps or []}, **files)


def _routes(tmp_path: Path, doc) -> list[str]:
    """--internal-routes for <tmp>/routes.json; a str is written as is, None writes nothing."""
    path = tmp_path / "routes.json"
    if doc is not None:
        path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return ["--internal-routes", str(path)]


def _build(tmp_path: Path, now: datetime = NOW, args: list[str] | None = None) -> tuple[int, str, dict]:
    out = tmp_path / "site"
    code = build.main(["--root", str(tmp_path / "root"), "--output", str(out), *(args or [])], now=now)
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "/*DATA:" not in page and "<!--STATUS-->" not in page
    data = {name: json.loads(re.search(rf"^const {name} = (.*);$", page, re.M).group(1)) for name in SLOTS}
    data["TODAY"] = re.search(r'\|\| "(\d{4}-\d{2}-\d{2})";$', page, re.M).group(1)
    status = re.search(r'<div class="gen-status[^"]*">.*?</div>', page)
    data["STATUS"] = status.group(0) if status else ""
    assert all("t7" in card for card in data["EVENTS"])
    return code, page, data


def test_scheduled_change_fills_every_slot(tmp_path: Path) -> None:
    events = [_scheduled("e2", "output", "3.00", "6.00", "3.00"), _scheduled("e1", "input", "0.75", "1.50", "0.75")]
    state = {"series": {"in": _stored("in-2", "input", "0.75"), "out": _stored("out-2", "output", "3.00")}}
    _write(tmp_path, events, report=REPORT, continuity={"status": "ok", "continuity": "continued"}, state=state)
    code, _, data = _build(tmp_path)
    assert (code, data["TODAY"], data["STATUS"]) == (0, "2026-10-21", "")
    [card] = data["EVENTS"]
    assert data["ID"] == {card["id"]: ["e1", "e2"]}
    assert {key: card.get(key) for key in ("t7", "m", "ch", "unit", "sum", "eff", "effLabel", "flag", "sub", "relKey", "relDemo", "act")} == {
        "t7": "예정 변경", "m": "gemini-3.8-flash", "ch": "Google · google_direct", "unit": "USD / 100만 토큰",
        "sum": "2개 항목 단가 1/1부터 인상 예정", "eff": "2027-01-01", "effLabel": "적용일", "flag": None, "sub": None,
        "relKey": "unk", "relDemo": False, "act": "pending"}
    assert card["chgHead"] == ["항목", "현재 (12/31까지)", "예정 (1/1부터)", "변동", "적용", "출처"]
    assert card["chg"] == [["입력 토큰", "$0.75", "$1.50", ["+$0.75", "인상 100.00%"], "1/1부터", "ok", ["s1"]],
                           ["출력 토큰", "$3.00", "$6.00", ["+$3.00", "인상 100.00%"], "1/1부터", "ok", ["s1"]]]
    assert card["cond"] == [["통화", "USD"], ["단위", "100만 토큰"], ["등급", "Standard"], ["경로", "Google · google_direct"],
                            ["조회", "2026-10-04 15:22 KST"]]
    assert card["ev"] == [["s1", "현재가 “input now” · 예정가 “input later”"], ["s1", "현재가 “output now” · 예정가 “output later”"]]
    assert data["SRC"] == {"s1": {"short": "S1", "nm": "Google · google_gemini_38_flash", "t": "조회 2026-10-04 15:22 KST", "tag": "sup",
                                  "url": URL}}
    assert data["MODELS"] == {"gemini-3.8-flash": {"prov": "Google", "kind": "LLM"}}
    assert data["COLLECTED"] == {"runs": [["2026-10-04T15:22", RUN1], ["2026-10-05T16:35", RUN2]]}
    trend = data["TREND"]["gemini-3.8-flash"]
    assert (trend["unit"], trend["measures"], trend["badge"], trend["notes"]) == ("USD / 100만 토큰", ["입력", "출력"], ["변화 1건", ""], [])
    assert trend["series"] == [{"name": "Google · google_direct", "pts": [
        ["2026-10-04T15:22", [0.75, 3.0]], ["2026-10-05T16:35", [0.75, 3.0]], ["2027-01-01", [1.5, 6.0], card["id"]]]}]


def test_no_events_is_an_empty_day(tmp_path: Path) -> None:
    _write(tmp_path, [], report={"status": "ok", "processed_runs": [RUN1]})
    code, _, data = _build(tmp_path)
    assert (code, data["STATUS"]) == (0, "")
    assert (data["EVENTS"], data["SRC"], data["ID"], data["MODELS"], data["TREND"]) == ([], {}, {}, {}, {})
    assert data["COLLECTED"] == {"runs": [["2026-10-04T15:22", RUN1]]}


def test_conflicted_price_shows_each_source_value(tmp_path: Path) -> None:
    values = {"or_api": "1.20", "or_page": "1.25"}
    event = _event("c1", "price_change", "conflicted", route="openrouter", source_key="or_api", billing_item="input",
                   before=_side("1.00", RUN1, "c-1", AT1), after=_side("1.20", RUN2, "c-2", AT2),
                   change={"delta": "0.20", "pct": "20.00", "direction": "up", "note": ""},
                   dates={"observed_before_utc": AT1, "observed_after_utc": AT2, "published": None, "effective_from": None, "note": ""},
                   evidence_refs=[_ref("or_api", RUN1, "c-1"), _ref("or_api", RUN2, "c-2")], sources=[_source("or_api", "channel_official")],
                   values_by_source=values, conflicts=[{"kind": "registered_sources_disagree", "values_by_source": values}])
    _write(tmp_path, [event])
    _, _, data = _build(tmp_path)
    [card] = data["EVENTS"]
    assert card["flag"] == "출처 상충"
    # each value points at its own source in the after run, never the before run's entry
    assert card["chg"] == [["입력 토큰", "$1.00", {"con": [["$1.20", "s2"], ["$1.25", "s3"]]}, None, "관측 10/5", "con", ["s1", "s2"]]]
    assert data["SRC"]["s2"]["t"] == "조회 2026-10-05 16:35 KST"
    assert data["SRC"]["s3"] == {"short": "S3", "nm": "OpenRouter · or_page", "t": "수집 2026-10-05 16:35 KST", "tag": "ch"}


def test_price_claim_waits_for_an_observation(tmp_path: Path) -> None:
    event = _claim("p1", "price_change", "openai_news", "Prices go up next month.", model_id="gpt-6-sol", route="openai_direct",
                   claim_kind="price_increase", dates={"published": "2026-10-03T09:00:00Z", "effective_from": None, "note": ""})
    event["sources"][0]["authority"] = "manufacturer_official"
    event["evidence_refs"][0]["url"] = "https://example.com/news"
    _write(tmp_path, [event])
    _, _, data = _build(tmp_path)
    [card] = data["EVENTS"]
    assert (card["t7"], card["sum"], card["flag"], card["chg"]) == ("가격 변경", "단가 인상 언급 · 관측값 확인 전", "공식 확인 전", [])
    assert card["ev"] == [["s1", "“Prices go up next month.”"]]
    assert data["SRC"] == {"s1": {"short": "S1", "nm": "OpenAI · openai_news", "t": "게시 2026-10-03 · 수집 2026-10-05 16:35 KST",
                                  "tag": "sup", "url": "https://example.com/news"}}
    assert data["TREND"] == {}


def test_scheduled_claim_without_a_date(tmp_path: Path) -> None:
    event = _claim("f1", "scheduled_change", "vercel_changelog", "Output price changes to $2.00.", route="vercel_ai_gateway",
                   claim_kind="announced_future_rate", billing_item="output", after={"amount": "2.00", "currency": "USD", "unit": "1M tokens"},
                   dates={"published": "2026-10-03", "effective_from": None, "note": ""})
    _write(tmp_path, [event])
    _, _, data = _build(tmp_path)
    [card] = data["EVENTS"]
    assert (card["sum"], card["flag"], card["effLabel"], card.get("eff")) == ("1개 항목 단가 변경 예정 · 적용일 미확인", "공식 확인 전", "적용일", None)
    assert card["chgHead"] == ["항목", "현재", "예정", "변동", "적용", "출처"]
    assert card["chg"] == [["출력 토큰", {"unk": 1, "t": "현재가 미확인"}, "$2.00", None, "", "unk", ["s1"]]]
    assert data["SRC"]["s1"]["nm"] == "Vercel · vercel_changelog"
    assert data["TREND"] == {}


@pytest.mark.parametrize(("effective_from", "eff", "summary"), [
    ("2026-11-15", "2026-11-15", "경로 중단 11/15"),
    (None, None, "경로 중단 · 날짜 미확인"),
    ("2026-11-15T00:00:00Z", None, "경로 중단 · 날짜 미확인"),  # not a bare date: no date is made up from it
])
def test_route_removal_date(tmp_path: Path, effective_from: str | None, eff: str | None, summary: str) -> None:
    event = _claim("r1", "route_change", "openrouter_endpoints", "Endpoint expires.", status="confirmed", route="openrouter",
                   subtags=["remove"], claim_kind="deprecation", dates={"effective_from": effective_from, "published": None, "note": ""})
    _write(tmp_path, [event])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert (card["t7"], card["sub"], card["effLabel"], card["sum"], card.get("eff"), card["chg"]) == (
        "공급 경로 변경", "경로 중단", "경로 중단일", summary, eff, [])


def _upstream_end(event_id: str, model_id: str, endpoint_id: str = "minimax") -> dict:
    """The engine's card of a Vercel endpoint deprecated_at (2026-10-28): one upstream of the route, a candidate."""
    key = "vercel_endpoints_" + re.sub(r"[^a-z0-9]+", "_", model_id)
    return _claim(event_id, "route_change", key, f"{endpoint_id}: deprecated_at=1794960000000 (2026-11-18T00:00:00Z), status=1; "
                  "deprecated_at is absent from Vercel's API documentation, which defines only status 0 (active)",
                  model_id=model_id, route="vercel_ai_gateway", subtags=["remove"], claim_kind="endpoint_deprecation", endpoint_id=endpoint_id,
                  dates={"effective_from": "2026-11-18", "published": None, "note": "deprecated_at of one upstream endpoint on this route"},
                  gaps=[{"field": "undocumented_field", "class": "check_first", "reason": "", "next_check": ""},
                        {"field": "shutdown_confirmed", "class": "limits_value", "reason": "", "next_check": ""}])


def test_one_upstreams_end_names_the_upstream_and_follows_the_routes_file(tmp_path: Path) -> None:
    """2026-10-28: the minimax upstream's end on Vercel is one supplier of the route, not 경로 중단. Since the 10/30 recheck
    LETSUR's routes file has Vercel and OpenRouter for all four models, so the end applies to each."""
    models = ("minimax-m2", "minimax-m2.1", "minimax-m2.5", "minimax-m2.7")
    _write(tmp_path, [_upstream_end(f"v{index}", model) for index, model in enumerate(models)])
    _, _, data = _build(tmp_path, args=["--internal-routes", str(DEMO_ROUTES)])
    cards = {card["mid"]: card for card in data["EVENTS"]}
    card = cards["minimax-m2"]
    assert (card["t7"], card["sub"], card["sum"], card["eff"], card["effLabel"], card["st"], card["cd"]) == (
        "공급 경로 변경", "공급 1곳 중단", "Vercel · minimax 공급 11/18 종료 예정", "2026-11-18", "minimax 공급 종료일", "candidate", "2026-11-18")
    assert "중단" in card["sub"] and "경로" not in card["sub"]  # the template and judge() read '중단' as an end, never an addition
    assert card["chk"] == [["first", build.GAP_KO["undocumented_field"]], ["limit", build.GAP_KO["shutdown_confirmed"]]]
    judged = [build.judge(cards[model], data["TODAY"]) for model in models]
    assert [item["applicability"] for item in judged] == ["APPLIES", "APPLIES", "APPLIES", "APPLIES"]
    assert judged[0]["action"] == "MONITOR" and cards["minimax-m2.7"]["relShort"] == "이 경로로 사용 중"


def test_a_route_end_with_a_whole_route_member_keeps_the_route_wording(tmp_path: Path) -> None:
    whole = _claim("r1", "route_change", "vercel_changelog", "MiniMax M2 will be removed from AI Gateway on November 18, 2026.",
                   status="confirmed", model_id="minimax-m2", route="vercel_ai_gateway", subtags=["remove"], claim_kind="route_removal",
                   dates={"effective_from": "2026-11-18", "published": "2026-10-20", "note": ""}, group_key="g1", group_role="representative")
    upstream = dict(_upstream_end("v1", "minimax-m2"), group_key="g1", group_role="member")
    _write(tmp_path, [whole, upstream])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert (card["sub"], card["sum"], card["effLabel"]) == ("경로 중단", "경로 중단 11/18", "경로 중단일")
    assert build.upstreams([upstream, dict(upstream, endpoint_id="bedrock")]) == ["minimax", "bedrock"]


def _retirement(event_id: str, model_id: str, route: str, day: str, key: str, version: str = "", replacement: str | None = None,
                basis: str = "stated", text: str = "", gaps: tuple[str, ...] = ("shutdown_confirmed",)) -> dict:
    """The engine's card of a retirement schedule row (2026-10-29): a confirmed model_end on the page owner's route."""
    return _claim(event_id, "model_end", key, f"row of {model_id}", status="confirmed", model_id=model_id, route=route, claim_kind="retirement",
                  conditions={"model_version": version} if version else {}, change={"replacement": replacement},
                  dates={"effective_from": day, "published": None, "date_basis": basis, "date_text": text or day, "note": ""},
                  gaps=[{"field": field, "class": "check_first" if field == "deployment_version" else "limits_value", "reason": "",
                         "next_check": ""} for field in gaps])


def test_a_retirement_schedule_row_is_a_dated_end_card_with_its_replacement(tmp_path: Path) -> None:
    """2026-10-29: Azure retires o3 on 2026-11-19 (replacement gpt-5.6-sol), the Gemini table gives gemini-3.1-flash-lite an earliest
    date, Azure's two claude-haiku-4-5 versions end the same day. LETSUR's routes file says Azure for o3, Google for Gemini 3.1 Flash
    Lite and Anthropic for Claude."""
    _write(tmp_path, [
        _retirement("r1", "o3", "azure_foundry", "2026-11-19", "azure_retirement_schedule", "2025-04-16", "gpt-5.6-sol"),
        _retirement("r2", "gemini-3.1-flash-lite", "google_direct", "2027-05-07", "google_deprecations", replacement="gemini-3.5-flash-lite",
                    basis="not_before", text="May 7, 2027", gaps=("earliest_date", "shutdown_confirmed")),
        _retirement("r3", "claude-haiku-4-5", "azure_foundry", "2026-11-15", "azure_retirement_schedule", "1", gaps=("deployment_version", "shutdown_confirmed")),
        _retirement("r4", "claude-haiku-4-5", "azure_foundry", "2026-11-15", "azure_retirement_schedule", "2", gaps=("deployment_version", "shutdown_confirmed"))])
    _, _, data = _build(tmp_path, args=["--internal-routes", str(DEMO_ROUTES)])
    cards = {card["mid"]: card for card in data["EVENTS"]}
    o3, lite, haiku = cards["o3"], cards["gemini-3.1-flash-lite"], cards["claude-haiku-4-5"]
    assert (o3["t7"], o3["ch"], o3["sum"], o3["eff"], o3["effLabel"], o3.get("effQual")) == (
        "제공 종료", "Microsoft · azure_foundry", "모델 제공 종료 11/19 · 버전 2025-04-16", "2026-11-19", "제공 종료일", None)
    assert o3["chg"] == [["대체 모델", "o3", "gpt-5.6-sol", None, "11/19부터", "ok", ["s1"]]]  # the template's 전환 대상 row
    assert ["버전", "2025-04-16"] in o3["cond"]
    assert (lite["sum"], lite["eff"], lite["effLabel"]) == ("모델 제공 종료 5/7 이후", "2027-05-07", "제공 종료 가능일")
    assert lite["effQual"] == "가장 이른 날짜(이 날짜 전에는 끝나지 않음) · 원문 “May 7, 2027”"
    assert lite["chk"][0] == ["limit", build.GAP_KO["earliest_date"]] and lite["chg"][0][:3] == ["대체 모델", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]
    assert data["ID"][haiku["id"]] == ["r3", "r4"] and haiku["sum"] == "모델 제공 종료 11/15 · 버전 1, 2"
    assert haiku["chk"][0] == ["first", build.GAP_KO["deployment_version"]]
    judged = {model: build.judge(card, data["TODAY"]) for model, card in cards.items()}
    assert {model: (item["applicability"], item["action"]) for model, item in judged.items()} == {
        "o3": ("APPLIES", "REVIEW"), "gemini-3.1-flash-lite": ("APPLIES", "REVIEW"), "claude-haiku-4-5": ("NOT APPLIES", "RECORD")}


def test_discount_end_year_read_from_publication(tmp_path: Path) -> None:
    event = _claim("d1", "limited_discount", "openrouter_endpoints", "20% off until Oct 31", status="confirmed", route="openrouter",
                   claim_kind="discount", billing_item="input",
                   change={"discount_pct": "20", "discount_pct_basis": "stated", "discounted_amount": "0.80"},
                   dates={"published": "2026-10-03", "end_phrase": "until Oct 31", "end_date": None, "note": ""},
                   period_end={"date": "2026-10-31", "basis": "derived_from_publication"}, period_state="active")
    _write(tmp_path, [event])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert {key: card.get(key) for key in ("eff", "effQual", "effLabel", "effNote", "sub", "discPct", "sum")} == {
        "eff": "2026-10-31", "effQual": "공지 게시일(10/3) 기준으로 연도를 해석한 날짜", "effLabel": "할인 종료일", "effNote": None,
        "sub": "진행 중", "discPct": 20, "sum": "20% 할인 · 할인가 $0.80 · 10/31까지"}
    assert card["chgHead"] == ["항목", "할인 전", "할인가", "할인율", "기간", "출처"]
    assert card["chg"] == [["입력 토큰", {"unk": 1, "t": "공개 안 됨"}, "$0.80", ["20%", "공지"], "~10/31", "ok", ["s1"]]]


def test_discount_with_only_an_end_phrase(tmp_path: Path) -> None:
    event = _claim("d2", "limited_discount", "vercel_changelog", "Half price for a limited time.", route="vercel_ai_gateway",
                   claim_kind="discount", change={"discount_pct": "50", "discount_pct_basis": "stated", "discounted_amount": None},
                   dates={"published": "2026-10-03", "end_phrase": "for a limited time", "end_date": None, "note": ""})
    _write(tmp_path, [event])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert {key: card.get(key) for key in ("eff", "effNote", "sub", "discPct", "sum")} == {
        "eff": None, "effNote": "원문 “for a limited time”", "sub": None, "discPct": 50, "sum": "50% 할인 · 종료일 미확인"}
    assert card["chg"] == [["단가", {"unk": 1, "t": "공개 안 됨"}, {"unk": 1, "t": "할인가 공개 안 됨"}, ["50%", "공지"], "", "unk", ["s1"]]]


def test_openrouter_endpoint_discount_signals_are_not_cards(tmp_path: Path) -> None:
    """2026-10-27 결정(가안): OpenRouter's per-endpoint pricing.discount makes no card (the 공급사 비교 표 shows today's rate);
    a discount from a channel notice still does."""
    signal = _claim("od1", "limited_discount", "openrouter_endpoints_glm_5_2",
                    "StreamLake (streamlake/fp8): pricing.discount=0.5; input 0.3 / output 0.9 USD per 1M tokens (discount applied)",
                    route="openrouter", claim_kind="discount")
    signal["evidence_refs"][0]["locator"] = "data.endpoints[3].pricing.discount"
    notice = _claim("d2", "limited_discount", "vercel_changelog", "Half price for a limited time.", route="vercel_ai_gateway",
                    claim_kind="discount", change={"discount_pct": "50", "discount_pct_basis": "stated", "discounted_amount": None})
    _write(tmp_path, [signal, notice])
    cards = _build(tmp_path)[2]["EVENTS"]
    assert [card["ch"].split(" · ")[0] for card in cards] == ["Vercel"]
    assert build.endpoint_discount(signal) and not build.endpoint_discount(notice)


def test_group_key_makes_one_card_per_route(tmp_path: Path) -> None:
    def member(event_id: str, route: str, role: str, **fields) -> dict:
        return _event(event_id, "limited_discount", route=route, claim_kind="discount", group_key="gk-1", group_role=role,
                      change={"discount_pct": "30", "discount_pct_basis": "stated"},
                      dates={"published": "2026-10-03", "end_date": "2026-10-31", "note": ""}, **fields)

    events = [member("g1", "google_vertex", "member"), member("g3", "openrouter", "member"),
              member("g2", "google_vertex", "representative", announced_via={"route": "google_direct"})]
    _write(tmp_path, events)
    data = _build(tmp_path)[2]
    assert sorted(data["ID"].values()) == [["g1", "g2"], ["g3"]]
    cards = {card["ch"]: card for card in data["EVENTS"]}
    # the representative, not the first member, supplies the card's own fields
    assert cards["Google Cloud · google_vertex"]["cond"] == [["경로", "Google Cloud · google_vertex"], ["공지 경로", "Google · google_direct"]]
    assert cards["OpenRouter · openrouter"]["cond"] == [["경로", "OpenRouter · openrouter"]]
    assert {card["eff"] for card in cards.values()} == {"2026-10-31"}


@pytest.mark.parametrize(("candidates", "subtags", "expected"), [
    (["route_change", "model_release"], ["add"],
     {"t7": "공급 경로 변경", "sub": "경로 추가", "effLabel": "제공일", "eff": "2026-10-12", "sum": "경로 추가 10/12 · 모델 첫 출시일 미확인",
      "flag": "공식 확인 전 · 유형 미정: 모델 출시 또는 공급 경로 변경"}),
    (["limited_discount", "price_change"], [],
     {"t7": None, "sub": None, "effLabel": None, "eff": None, "sum": "유형 미분류 이벤트", "flag": "공식 확인 전 · 유형 미정: 가격 변경 또는 한시 할인"}),
])
def test_undecided_type(tmp_path: Path, candidates: list[str], subtags: list[str], expected: dict) -> None:
    event = _claim("u1", None, "openrouter_endpoints", "Now available.", route="openrouter", subtags=subtags, type_candidates=candidates,
                   claim_kind="availability", dates={"maker_first_release_date": None, "route_added_date": "2026-10-12", "note": ""})
    _write(tmp_path, [event])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert {key: card.get(key) for key in expected} == expected


@pytest.mark.parametrize(("event_type", "kind"), [
    ("price_change", "가격 변경"), ("scheduled_change", "예정 변경"), ("limited_discount", "한시 할인"), ("condition_change", "조건 변경"),
    ("model_release", "모델 출시"), ("model_end", "제공 종료"), ("route_change", "공급 경로 변경")])
def test_card_kind_is_its_event_type(tmp_path: Path, event_type: str, kind: str) -> None:
    _write(tmp_path, [_claim("k1", event_type, "openrouter_endpoints", "Notice.", route="openrouter", dates={"published": "2026-10-03", "note": ""})])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert (card["kind"], card["t7"]) == (kind, kind)


@pytest.mark.parametrize(("event_type", "candidates", "t7"), [
    (None, ["route_change", "model_release"], "공급 경로 변경"),  # shown as a route addition, still undecided
    (None, ["limited_discount", "price_change"], None),
    (None, [], None),  # no candidates either: the card still gets a chip to switch it off with
    ("price_change", ["limited_discount", "price_change"], "가격 변경"),  # decided: a leftover candidate list changes nothing
])
def test_undecided_card_kind(tmp_path: Path, event_type: str | None, candidates: list[str], t7: str | None) -> None:
    event = _claim("u1", event_type, "openrouter_endpoints", "Now available.", route="openrouter", subtags=["add"], type_candidates=candidates,
                   claim_kind="availability", dates={"maker_first_release_date": None, "route_added_date": "2026-10-12", "note": ""})
    _write(tmp_path, [event])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert (card["t7"], card["kind"]) == (t7, t7 if event_type else "유형 미정")
    assert ("유형 미정" in card.get("flag", "")) == (event_type is None and bool(candidates))


def test_list_filter_chips_are_the_types_the_builder_emits(tmp_path: Path) -> None:
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    chips = json.loads(re.search(r"^const T7GROUP = (\{.*\});$", page, re.M).group(1))
    assert list(chips) == list(build.T7.values())


@pytest.mark.parametrize(("event", "cd"), [
    (_scheduled("s1", "input", "0.75", "1.50", "0.75"), "2027-01-01"),
    (_claim("d1", "limited_discount", "openrouter_endpoints", "20% off until Oct 31", route="openrouter", claim_kind="discount",
            change={"discount_pct": "20", "discount_pct_basis": "stated"},
            dates={"published": "2026-10-03", "end_phrase": "until Oct 31", "end_date": "2026-10-31", "note": ""}), "2026-10-31"),
    (_claim("a1", "route_change", "openrouter_endpoints", "Now available.", route="openrouter", subtags=["add"], claim_kind="availability",
            dates={"maker_first_release_date": None, "route_added_date": "2026-10-12", "published": "2026-10-03", "note": ""}), "2026-10-12"),
    (_claim("p1", "price_change", "openai_news", "Prices go up.", model_id="gpt-6-sol", route="openai_direct", claim_kind="price_increase",
            dates={"published": "2026-10-03T09:00:00Z", "effective_from": None, "note": ""}), "2026-10-03"),
    (_observed("o1", "price_change", "1.00", "1.20", change={"delta": "0.20", "pct": "20.00", "direction": "up", "note": ""}), "2026-10-05"),
    # no date of its own and no feed entry to date it: the last day its evidence was collected (2026-10-27 decision 19)
    (_claim("n1", "scheduled_change", "vercel_changelog", "Prices change.", route="vercel_ai_gateway", claim_kind="announced_future_rate",
            dates={"published": None, "effective_from": None, "note": ""}), "2026-10-05"),
])
def test_card_date_is_the_date_the_card_is_filed_under(tmp_path: Path, event: dict, cd: str | None) -> None:
    _write(tmp_path, [event])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert card.get("cd") == cd


def test_group_card_date_follows_the_representative(tmp_path: Path) -> None:
    def member(event_id: str, role: str) -> dict:
        return _event(event_id, "limited_discount", route="openrouter", claim_kind="discount", group_key="gk-1", group_role=role,
                      change={"discount_pct": "30", "discount_pct_basis": "stated"},
                      dates={"published": "2026-10-03", "end_date": "2026-10-31", "note": ""})

    _write(tmp_path, [member("g1", "member"), member("g2", "representative")])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert (card["kind"], card["cd"]) == ("한시 할인", "2026-10-31")


def _group_change(event_id: str, observed_at: str, role: str, item: str = "input", **fields) -> dict:
    """A price change of group gk-1 whose after observation was made at observed_at."""
    event = _observed(event_id, "price_change", "1.00", "1.20", item=item, group_key="gk-1", group_role=role,
                      change={"delta": "0.20", "pct": "20.00", "direction": "up", "note": ""}, **fields)
    event["after"]["observed_at_utc"] = event["dates"]["observed_after_utc"] = observed_at
    return event


def _group_discount(event_id: str, role: str) -> dict:
    """A limited discount claim of group gk-1: dated by its notice, never observed."""
    return _event(event_id, "limited_discount", route="openrouter", claim_kind="discount", group_key="gk-1", group_role=role,
                  change={"discount_pct": "30", "discount_pct_basis": "stated"},
                  dates={"published": "2026-10-03", "end_date": "2026-10-31", "note": ""})


def test_group_card_carries_the_last_observed_day_of_its_members(tmp_path: Path) -> None:
    """cd stays the representative's day; ld is the newest day a member was observed, in KST like the rows' 관측 M/D."""
    events = [_group_change("g1", "2026-10-21T03:00:00Z", "representative"),
              _group_change("g2", "2026-10-23T16:00:00Z", "member", item="output")]  # 10/24 01:00 KST
    _write(tmp_path, events)
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert (card["cd"], card["ld"]) == ("2026-10-21", "2026-10-24")
    assert [row[4] for row in card["chg"]] == ["관측 10/21", "관측 10/24"]


def test_context_rows_do_not_move_the_last_observed_day(tmp_path: Path) -> None:
    """A grey 그대로 row is stamped with the observation of its own event, and the day the page is built is no observation."""
    grey = {"billing_item": "output", "conditions": {"service_tier": "standard"}, "amount": "4.00", "observation_id": "g1-3"}
    events = [_group_change("g1", "2026-10-21T03:00:00Z", "representative", unchanged_items=[grey]),
              _group_change("g2", "2026-10-22T03:00:00Z", "member", item="cache_read")]
    _write(tmp_path, events)
    [card] = _build(tmp_path, now=datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc))[2]["EVENTS"]  # built on 10/24
    assert [row[4] for row in card["chg"] if row[1] == row[2]] == ["관측 10/21"]
    assert (card["cd"], card["ld"]) == ("2026-10-21", "2026-10-22")


def test_a_member_nobody_observed_adds_no_day(tmp_path: Path) -> None:
    claim = _event("g3", "price_change", claim_kind="price_increase", group_key="gk-1", group_role="member",
                   dates={"published": "2026-10-03", "note": ""})
    _write(tmp_path, [_group_change("g1", "2026-10-22T03:00:00Z", "representative"), claim])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert (card["cd"], card["ld"]) == ("2026-10-22", "2026-10-22")


@pytest.mark.parametrize("events", [
    [_observed("o1", "price_change", "1.00", "1.20", change={"delta": "0.20", "pct": "20.00", "direction": "up", "note": ""})],
    [_group_change("g1", "2026-10-24T03:00:00Z", "representative")],
    [_group_discount("g1", "representative"), _group_discount("g2", "member")],
], ids=["one event", "group of one event", "group of claims nobody observed"])
def test_card_without_two_observed_members_has_no_last_observed_day(tmp_path: Path, events: list[dict]) -> None:
    _write(tmp_path, events)
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert "ld" not in card


def test_template_files_a_card_as_past_by_its_last_observed_day(tmp_path: Path) -> None:
    """The test environment has no JS engine, so the rule is read off the built page; the browser check covers how it behaves."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    [rule] = re.findall(r"^const isPast = (.*?);(?: //.*)?$", page, re.M)
    # past once the later of cd and ld is 7 or more days before the page's base day (ld only on groups; 2026-10-27 decision 19);
    # a card with no date at all counts as past
    assert rule == "e => { const day = [e.cd, e.ld].filter(Boolean).sort().pop(); return !day || days(day) <= -PAST_DAYS; }"
    assert "const PAST_DAYS = 7;" in page


def test_template_recommended_order_puts_letsur_routes_first(tmp_path: Path) -> None:
    """2026-10-28 가안: 추천 순 follows LETSUR's order of judgement as the 3rd team's report states it. Read off the built page
    like the rule above; the browser check covers the order on real data."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    tier = page[page.index("function tierOf(e){"):page.index("function recCmp")]
    # RECORD last, then LETSUR's route: a stop of selling, a cost change, the rest; then unknown, other routes, unused models
    assert 'if (actionKey(e) === "record") return 7;' in tier
    assert ('if (r === "use") return t === "제공 종료" || (t === "공급 경로 변경" && /중단/.test(EV7SUB[e.id] || "")) ? 0 : '
            '["가격 변경", "예정 변경", "한시 할인", "조건·가격 변경"].includes(t) ? 1 : 2;') in tier
    assert 'if (r === "unk") return 3;' in tier and 'if (r === "other") return dirOf(e) === "good" ? 4 : 5;' in tier
    assert "function recCmp(a, b){ return tierOf(a) - tierOf(b) || cmpU(a, b) || actOrder(a) - actOrder(b); }" in page
    assert ': recCmp(a, b); }' in page and ".slice().sort(recCmp)[0]; state.sel = top && top.vg ? top.vg : top?.id;" in page


def test_frequent_price_changes_of_one_route_share_a_bundle() -> None:
    """2026-10-28 가안 (변동 잦음): 3 or more 가격 변경 cards of one model and route within 7 days of each other share a vg;
    every 가격 변경 card of that route joins, and other types, routes and route-less notes stay apart."""
    def card(day, t7="가격 변경", model="glm-5.3-flash", route="OpenRouter · openrouter"):
        return {"t7": t7, "mid": model, "cd": day, "ch": route}
    busy = [card("2026-10-17"), card("2026-10-17"), card("2026-10-20"), card("2026-10-30")]
    calm = [card("2026-10-17", model="kimi-k3"), card("2026-10-24", model="kimi-k3"), card("2026-10-30", model="kimi-k3")]
    others = [card("2026-10-18", t7="예정 변경"), card("2026-10-18", route=build.route_label("")),
              card("2026-10-18", route=build.route_label("")), card("2026-10-18", route=build.route_label(""))]
    cards = busy + calm + others
    build.mark_volatile(cards)
    assert len({c["vg"] for c in busy}) == 1 and busy[0]["vg"].startswith("v_")  # 10/30 joins: the bundle is the whole route
    assert not any("vg" in c for c in calm + others)  # 7 days apart each; another type; no route
    again = busy[:3]
    build.mark_volatile(again)
    assert again[0]["vg"] == busy[0]["vg"]  # the id comes from model and route, so links stay put


def _makers(page: str) -> list[list[str]]:
    """template.html's MAKERS as the built page carries it: [model id prefix, company] in the order written."""
    return json.loads(re.search(r"^const MAKERS = (.*);$", page, re.M).group(1))


def _maker_of(makers: list[list[str]], model_id: str) -> str:
    """template.html's makerOf: the company of the first prefix the id starts with, else 기타."""
    return next((company for prefix, company in makers if model_id.startswith(prefix)), "기타")


def test_template_bundles_the_list_and_moves_the_table_to_the_model_view(tmp_path: Path) -> None:
    """2026-10-28 가안: the list shows one row per 변동 잦음 bundle at its first member's place, a bundle opens its own detail
    (#vg) with links to 모델별 가격, and the page has two screens, 변화 and 모델별 가격 (model list by maker, 가격 추이, 현재 가격
    표). The browser check covers behaviour."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert "const VG = {}; EVENTS.forEach(e => { if (e.vg) (VG[e.vg] = VG[e.vg] || []).push(e); });" in page
    assert 'list.flatMap(e => !e.vg ? [e] : seen.has(e.vg) ? [] : (seen.add(e.vg), [{vg: e.vg}]))' in page
    assert "bundled(list.slice().sort(queueCmp)).map(rowOf)" in page and "bundled(l).map(rowOf)" in page
    assert "const e = VG[state.sel] ? vgCard(state.sel) : EVENTS.find(x => x.id === state.sel)," in page
    # two screens: the tabs live in the header, body[data-page] picks what shows, #m/<model> opens 모델별 가격
    assert '<button type="button" role="tab" data-ptab="changes" aria-selected="true">변화</button>' in page
    assert 'body:not([data-page="models"]) .mpage{display:none}' in page and 'if (h.startsWith("m/")) startModel' in page
    assert '<section class="card trend" id="trend" aria-label="가격 추이"></section>' in page and 'id="ptable"' in page
    assert 'setPage("models", tr.dataset.trend, tr.dataset.tview || "chart");' in page
    # models grouped by maker (from the id), makers and models in name order (the table is written in company-name order too)
    companies = [company for _, company in _makers(page)]
    assert companies == sorted(companies, key=str.lower)
    assert "modelName(a).localeCompare(modelName(b), undefined, {numeric: true})" in page
    # every company group starts with its icon and is set off by a line (2026-10-30 박건 요청); the icons are the page's own symbols
    icons = json.loads(re.search(r"^const MAKER_ICONS = (.*);$", page, re.M).group(1))
    assert set(icons) == set(companies)
    assert all(f'<symbol id="mk-{icon}" viewBox=' in page for icon in icons.values())
    assert ".mgrp+.mgrp{margin-top:12px;padding-top:12px;border-top:1px solid var(--line-strong)}" in page
    sprite = re.search(r'<svg class="sprite".*?</svg>\n', page, re.S).group(0)
    assert not re.search(r"<script|\son\w+=|href=|<image|<foreignObject", sprite)  # shapes only


def test_observed_condition_change(tmp_path: Path) -> None:
    def stated(value: str) -> dict:
        return {"value": value, "unit": "x base input price", "basis": "stated", "locator": f"cache {value}"}

    event = _observed("k1", "condition_change", "0.25", "0.10", item="cache_read", subtags=["billing.cache_read_multiplier"],
                      change={"condition_key": "cache_read_multiplier", "before_value": "0.25", "after_value": "0.1",
                              "price_delta": "-0.15", "price_pct": "-60.00", "price_direction": "down", "note": ""},
                      condition={"key": "cache_read_multiplier", "basis": "stated", "before": stated("0.25"), "after": stated("0.1")})
    _write(tmp_path, [event])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert (card["sub"], card["sum"], card.get("eff")) == ("과금 · 캐시", "과금 · 캐시 조건 변경 관측", None)
    assert card["chg"] == [["캐시 읽기 배율", "0.25 x base input price", "0.1 x base input price", None, "관측 10/5", "ok", ["s1", "s2"]],
                           ["캐시 읽기", "$0.25", "$0.10", ["−$0.15", "인하 60.00%"], "관측 10/5", "ok", ["s1", "s2"]]]
    assert card["cond"][-2:] == [["조회", "2026-10-05 16:35 KST"], ["관측", "10/4 → 10/5"]]
    assert card["ev"] == [["s1", "이전 “cache_read 0.25”"], ["s2", "현재 “cache_read 0.10”"]]


def test_observed_price_change_keeps_unchanged_rows_grey(tmp_path: Path) -> None:
    event = _observed("o1", "price_change", "1.00", "1.20", change={"delta": "0.20", "pct": "20.00", "direction": "up", "note": ""},
                      unchanged_items=[{"billing_item": "output", "conditions": {"service_tier": "standard"}, "amount": "4.00",
                                        "observation_id": "o1-3"}])
    _write(tmp_path, [event])
    data = _build(tmp_path)[2]
    [card] = data["EVENTS"]
    assert card["sum"] == "1개 항목 단가 인상 관측"
    assert card["chg"] == [["입력 토큰", "$1.00", "$1.20", ["+$0.20", "인상 20.00%"], "관측 10/5", "ok", ["s1", "s2"]],
                           ["출력 토큰", "$4.00", "$4.00", None, "관측 10/5", "ok", ["s2"]]]
    trend = data["TREND"]["gemini-3.8-flash"]
    assert trend["note"] == "state.json을 쓰지 못해 이벤트의 이전·이후 관측만 그렸습니다."
    assert trend["series"] == [{"name": "Google · google_direct", "pts": [["2026-10-04T15:22", [1.0]], ["2026-10-05T16:35", [1.2]]]}]


EMPTY = {"schema_version": 1, "events": [], "gaps": []}


@pytest.mark.parametrize(("files", "reason"), [
    ({}, "events.json 없음"),
    ({"events": "{"}, "events.json을 읽을 수 없음"),
    ({"events": {"events": {}}}, "events.json 형식이 다름 (events 목록 없음)"),
    ({"events": EMPTY, "continuity": "{"}, "continuity.json을 읽을 수 없음"),
    ({"events": EMPTY, "continuity": {"status": "failed", "continuity": "failed", "reason": "events_failed", "error": "exit 2"}},
     "continuity.json status failed (continuity failed · reason events_failed · error exit 2)"),
    ({"report": {"status": "rejected", "errors": ["run a: no manifest", "run b: bad csv"]}},
     "events.json 없음 · report.json status rejected (errors run a: no manifest; run b: bad csv)"),
])
def test_failed_day_still_writes_a_page(tmp_path: Path, files: dict, reason: str) -> None:
    _files(tmp_path, **files)
    code, _, data = _build(tmp_path)
    assert code == 0
    assert data["STATUS"] == f'<div class="gen-status fail"><b>오늘 생성 실패</b> · {reason}</div>'
    assert (data["EVENTS"], data["TREND"]) == ([], {})


@pytest.mark.parametrize(("continuity", "banner"), [
    ({"status": "ok", "continuity": "continued_with_gaps", "unprocessed_runs": [RUN1, RUN2]},
     '<div class="gen-status warn"><b>처리하지 못한 이전 run 2개</b> · 그 run의 변화는 이 화면에 없을 수 있습니다 (continuity continued_with_gaps)</div>'),
    ({"status": "ok", "continuity": "broken", "reason": "no_previous_state"},
     '<div class="gen-status warn"><b>이전 이력이 끊겼습니다</b> · 이전 상태 없이 이번 run만으로 만들었습니다 (continuity broken · reason no_previous_state)</div>'),
])
def test_continuity_warning_keeps_the_cards(tmp_path: Path, continuity: dict, banner: str) -> None:
    _write(tmp_path, [_scheduled("e1", "input", "0.75", "1.50", "0.75")], continuity=continuity)
    code, _, data = _build(tmp_path)
    assert (code, data["STATUS"], len(data["EVENTS"])) == (0, banner, 1)


def _record(route_id: str | None, model_id: str = "gemini-3.8-flash", **fields) -> dict:
    return {"model_id": model_id, "route_id": route_id, **fields}


@pytest.mark.parametrize(("records", "relation"), [
    (None, ("unk", "사용 경로 확인 필요", False)),  # no --internal-routes
    ([_record("google_direct")], ("use", "이 경로로 사용 중 (가정)", True)),
    ([_record("google_direct", endpoint_id="ep-1"), _record("vercel_ai_gateway", endpoint_id="ep-2")],
     ("use", "이 경로로 사용 중 : endpoint ep-1 (가정)", True)),
    ([_record("vercel_ai_gateway", endpoint_id=None), _record("openrouter")],
     ("other", "모델 제공 중 : Vercel · vercel_ai_gateway, OpenRouter · openrouter 경로로 사용 (가정)", True)),
    ([_record(None, endpoint_id=None)], ("none", "미도입 (가정)", True)),
    ([_record("google_direct", model_id="claude-sonnet-5-5")], ("unk", "사용 경로 확인 필요", False)),  # no record is not "not used"
])
def test_internal_routes_decide_the_letsur_relation(tmp_path: Path, records: list | None, relation: tuple) -> None:
    _write(tmp_path, [_scheduled("e1", "input", "0.75", "1.50", "0.75")])
    args = _routes(tmp_path, {"is_demo": True, "purpose": "test", "records": records}) if records is not None else None
    code, _, data = _build(tmp_path, args=args)
    [card] = data["EVENTS"]
    assert (code, data["STATUS"], (card["relKey"], card["relShort"], card["relDemo"])) == (0, "", relation)


@pytest.mark.parametrize(("records", "use"), [
    (None, {}),  # no --internal-routes: no use
    ([_record("google_direct")], {"use": "Google · google_direct 경로로 사용 (가정)"}),
    ([_record("vercel_ai_gateway"), _record("openrouter")], {"use": "Vercel · vercel_ai_gateway, OpenRouter · openrouter 경로로 사용 (가정)"}),
    ([_record("vercel_ai_gateway", endpoint_id="ep-2")], {"use": "Vercel · vercel_ai_gateway 경로로 사용 · endpoint ep-2 (가정)"}),
    ([_record("google_direct", endpoint_id="ep-1"), _record("vercel_ai_gateway", endpoint_id="ep-2")],
     {"use": "Google · google_direct, Vercel · vercel_ai_gateway 경로로 사용 · endpoint ep-1 · endpoint ep-2 (가정)"}),
    ([_record(None, endpoint_id=None)], {"use": "미도입 (가정)"}),
    ([_record("vercel_ai_gateway"), _record(None, endpoint_id="ep-1")], {"use": "Vercel · vercel_ai_gateway 경로로 사용 (가정)"}),
    ([_record("google_direct", model_id="claude-sonnet-5-5")], {}),  # no record for this model: no use, as on the card
])
def test_internal_routes_fill_the_models_table(tmp_path: Path, records: list | None, use: dict) -> None:
    """MODELS gets every route the file gives the model, whatever the card's route (google_direct here)."""
    _write(tmp_path, [_scheduled("e1", "input", "0.75", "1.50", "0.75")])
    args = _routes(tmp_path, {"is_demo": True, "purpose": "test", "records": records}) if records is not None else None
    code, _, data = _build(tmp_path, args=args)
    assert (code, data["STATUS"], data["MODELS"]) == (0, "", {"gemini-3.8-flash": {"prov": "Google", "kind": "LLM", **use}})


def test_routes_that_are_not_a_demo_are_not_marked_as_assumed(tmp_path: Path) -> None:
    _write(tmp_path, [_scheduled("e1", "input", "0.75", "1.50", "0.75")])
    _, _, data = _build(tmp_path, args=_routes(tmp_path, {"is_demo": False, "records": [_record("vercel_ai_gateway")]}))
    [card] = data["EVENTS"]
    assert (card["relKey"], card["relShort"], card["relDemo"]) == ("other", "모델 제공 중 : Vercel · vercel_ai_gateway 경로로 사용", False)
    assert data["MODELS"]["gemini-3.8-flash"]["use"] == "Vercel · vercel_ai_gateway 경로로 사용"


def test_committed_demo_routes(tmp_path: Path) -> None:
    """The file the workflow passes: Gemini 3.8 Flash takes LETSUR's registered routes from its 9/23 QnA answer
    (Google direct, OpenRouter, Vercel; 2026-10-28 박건 결정), so a Google direct change applies, with no mark of how the route
    is known since 2026-10-30."""
    _write(tmp_path, [_scheduled("e1", "input", "0.75", "1.50", "0.75")])
    _, _, data = _build(tmp_path, args=["--internal-routes", str(DEMO_ROUTES)])
    [card] = data["EVENTS"]
    assert (card["relKey"], card["relShort"], card["relDemo"]) == ("use", "이 경로로 사용 중", False)
    assert data["MODELS"] == {"gemini-3.8-flash": {"prov": "Google", "kind": "LLM",
                                                   "use": "Google · google_direct, OpenRouter · openrouter, Vercel · vercel_ai_gateway 경로로 사용"}}


@pytest.mark.parametrize(("records", "relation"), [
    ([_record("google_direct", basis="settings"), _record("vercel_ai_gateway", basis="settings")], ("use", "이 경로로 사용 중", False)),
    ([_record("vercel_ai_gateway", basis="settings")], ("other", "모델 제공 중 : Vercel · vercel_ai_gateway 경로로 사용", False)),
    # one record without basis keeps the whole model an assumption
    ([_record("google_direct", basis="settings"), _record("vercel_ai_gateway")], ("use", "이 경로로 사용 중 (가정)", True)),
    ([_record("google_direct", basis="observed"), _record("vercel_ai_gateway")], ("use", "이 경로로 사용 중 (가정)", True)),
])
def test_registered_routes_are_marked_as_letsur_settings(tmp_path: Path, records: list, relation: tuple) -> None:
    """2026-10-28 박건 결정: records with basis settings are LETSUR's registered routes (9/23 QnA answer), not assumptions.
    2026-10-30 (박건: 사용이면 사용): the card no longer says which basis; a demo file's record without basis still says 가정."""
    _write(tmp_path, [_scheduled("e1", "input", "0.75", "1.50", "0.75")])
    _, _, data = _build(tmp_path, args=_routes(tmp_path, {"is_demo": True, "records": records}))
    [card] = data["EVENTS"]
    assert (card["relKey"], card["relShort"], card["relDemo"]) == relation
    assert not {"relSet", "relObs", "relMan", "relInf"} & set(card)
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'const relMark = e => relDemoOf(e) ? {chip: "가정", note: "시연용 가정"} : null;' in page


def _astra_records() -> dict:
    """gpt-6-astra as the committed file has it: a registered route (settings) and the route one API call went through."""
    return {"is_demo": False, "records": [_record("openai_direct", model_id="gpt-6-astra", basis="settings"),
                                          _record("azure_foundry", model_id="gpt-6-astra", basis="observed")]}


@pytest.mark.parametrize(("route", "relation"), [
    ("azure_foundry", ("use", "이 경로로 사용 중")),  # its own record: observed
    ("openai_direct", ("use", "이 경로로 사용 중")),  # its own record: settings
    ("vercel_ai_gateway", ("other", "모델 제공 중 : OpenAI · openai_direct, Microsoft · azure_foundry 경로로 사용")),
])
def test_a_route_with_a_record_reads_the_same_whatever_its_basis(route: str, relation: tuple) -> None:
    """2026-10-30 (박건: 사용이면 사용): a settings record (9/23 answer) and an observed one (API calls) both read as LETSUR's
    route, with no mark of which; the 2026-10-28 marks (렛서 설정, 호출 확인) are gone from cards and MODELS."""
    found = build.relation({"model_id": "gpt-6-astra", "route": route}, _astra_records())
    assert found == {"relKey": relation[0], "relShort": relation[1], "relDemo": False}
    assert build.model_use("gpt-6-astra", _astra_records()) == {"use": "OpenAI · openai_direct, Microsoft · azure_foundry 경로로 사용"}


@pytest.mark.parametrize(("records", "relation"), [
    ([_record("openrouter", basis="settings"), _record("vercel_ai_gateway", basis="observed")],
     ("other", "모델 제공 중 : OpenRouter · openrouter, Vercel · vercel_ai_gateway 경로로 사용")),
    ([_record(None, basis="observed")], ("none", "미도입")),  # not adopted
])
def test_a_route_without_a_record_names_the_models_routes(records: list, relation: tuple) -> None:
    found = build.relation({"model_id": "gemini-3.8-flash", "route": "google_direct"}, {"is_demo": False, "records": records})
    assert found == {"relKey": relation[0], "relShort": relation[1], "relDemo": False}


def test_known_bases_are_valid_and_any_other_is_not() -> None:
    """2026-10-29: manual (an admin set it in the 공급로 현황 tab) joins settings and observed. 2026-10-30: inferred (a route
    LETSUR's calls only suggested) is gone (박건: 애초에 모호한 게 있으면 안 됨), so a file holding one is refused as a whole."""
    for basis in (None, "settings", "observed", "manual"):
        assert build.record_problem(_record("google_direct", basis=basis)) == ""
    for basis in ("inferred", "confirmed", "Observed", "", 1):
        assert build.record_problem(_record("google_direct", basis=basis)) == "basis가 settings·observed·manual이나 null이 아님"


def test_notes_and_check_days_are_accepted_and_bad_ones_rejected() -> None:
    assert build.record_problem(_record("openai", basis="observed", note="응답에 Azure 필드 있음", checked_at="2026-10-29")) == ""
    assert build.record_problem(_record("openai", basis="observed", note=None, checked_at=None)) == ""
    for note in ("", 3, ["a"]):
        assert build.record_problem(_record("openai", note=note)) == "note가 문자열이나 null이 아님"
    for day in ("2026-10-32", "10/29", "2026-10-29T00:00", 20261029):
        assert build.record_problem(_record("openai", checked_at=day)) == "checked_at이 YYYY-MM-DD 날짜나 null이 아님"


BEDROCK_1030 = ("gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra", "gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol", "gpt-6.1-terra")


def test_committed_routes_are_the_10_30_recheck_and_hold_no_guess() -> None:
    """2026-10-30 (박건: 애초에 모호한 게 있으면 안 됨): the file is LETSUR's 9/23 settings plus the route tool's recheck
    (letsur_routes.py: every model asked several times, the Gemini API told from Vertex by a field only Vertex takes, Bedrock
    by its own field, Z.ai by the request_id it gives back). No inferred record; a model it could not tell is only listed under
    unresolved with why, and a route not seen again (GPT-6/5.6's Azure of 10/28) is gone."""
    doc = json.loads(DEMO_ROUTES.read_text(encoding="utf-8"))
    assert doc["is_demo"] is False and {record.get("basis") for record in doc["records"]} == {"settings", "observed"}
    assert not any(build.record_problem(record) for record in doc["records"]) and build.internal_routes(DEMO_ROUTES) == (doc, "")
    assert all(record.get("checked_at") for record in doc["records"] if record["basis"] == "observed")
    have = {(record["model_id"], record["route_id"]) for record in doc["records"]}
    assert all((model, "anthropic_direct") in have for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"))
    assert all((model, "aws_bedrock") in have for model in BEDROCK_1030)
    assert {model for model in BEDROCK_1030 if (model, "azure_foundry") in have} == {"gpt-6.1-sol"}  # the only Azure seen again
    assert all((model, "google_direct") in have for model in ("gemini-3.5-flash", "gemini-3.7-flash", "gemini-2.5-flash-lite"))
    assert not any(route == "google_vertex" for _, route in have)  # every Gemini answer from Google was the Gemini API's
    assert {("glm-4.7", "aws_bedrock"), ("glm-5", "aws_bedrock"), ("glm-5.3", "zai_direct"), ("qwen3.5-plus", "openrouter"),
            ("gemini-3-flash-preview", "vercel_ai_gateway")} <= have
    assert all(isinstance(why, str) and why for why in doc["unresolved"].values())  # a deployment it could not tell says why
    for model in ("text-embedding-3-large", "kling-v3-text-to-video", "gpt-image-2"):
        assert model in doc["unresolved"] and not any(record["model_id"] == model for record in doc["records"])


# LETSUR calls them through Google's Gemini API, which publishes no price for these deprecated preview ids: its deprecations
# page names replacements but, unlike gemini-3.5/3.7-flash, does not say requests go to them (asked of LETSUR, 10/30).
NO_PUBLISHED_PRICE = {("gemini-3-pro-image-preview", "google_direct"), ("gemini-3.1-flash-image-preview", "google_direct")}


def test_every_route_letsur_uses_has_a_collected_price() -> None:
    """2026-10-30 박건: '렛서가 쓰는 가격 수집 안하는 것도 존재해서는 안되고'. Every route the committed file says LETSUR uses is
    collected at that publisher by the registry (sources.py), the 공급로 현황 tab's enabled boxes, but for the two ids no one
    prices."""
    from scripts.market_collection.events.catalog import ROUTE_PUBLISHERS
    from scripts.market_collection.sources import source_specs
    collected: dict[str, set] = {}
    for spec in source_specs():
        collected.setdefault(spec.model_id, set()).add(ROUTE_PUBLISHERS.get(spec.route, spec.route))
    records = json.loads(DEMO_ROUTES.read_text(encoding="utf-8"))["records"]
    assert {(r["model_id"], r["route_id"]) for r in records if r["route_id"]
            and ROUTE_PUBLISHERS.get(r["route_id"], r["route_id"]) not in collected.get(r["model_id"], set())} == NO_PUBLISHED_PRICE


def test_a_card_on_another_route_than_the_observed_one_says_which_route_is() -> None:
    """grok-4.20 answered only through OpenRouter on 10/30, so its Vercel card reads 'another route' and does not claim the
    Vercel route is the one in use; deepseek-v4-flash answered through both gateways."""
    doc = json.loads(DEMO_ROUTES.read_text(encoding="utf-8"))

    def card(model: str, route: str) -> tuple:
        found = build.relation({"model_id": model, "route": route}, doc)
        return found["relKey"], found["relShort"]
    assert card("grok-4.20", "vercel_ai_gateway") == ("other", "모델 제공 중 : OpenRouter · openrouter 경로로 사용")
    assert card("deepseek-v4-flash", "vercel_ai_gateway") == card("deepseek-v4-flash", "openrouter") == ("use", "이 경로로 사용 중")
    assert build.model_use("grok-4.20", doc) == {"use": "OpenRouter · openrouter 경로로 사용"}


@pytest.mark.parametrize(("records", "relation"), [
    ([_record("google_direct", basis="manual", checked_at="2026-10-29")], ("use", "이 경로로 사용 중")),
    ([_record("openrouter", basis="manual")], ("other", "모델 제공 중 : OpenRouter · openrouter 경로로 사용")),
    ([_record("google_direct", basis="settings"), _record("openrouter", basis="manual")], ("use", "이 경로로 사용 중")),
])
def test_a_route_an_admin_set_counts_as_use(records: list, relation: tuple) -> None:
    """2026-10-29: manual (set in the 공급로 현황 tab) is LETSUR's route like settings and observed."""
    found = build.relation({"model_id": "gemini-3.8-flash", "route": "google_direct"}, {"is_demo": False, "records": records})
    assert (found["relKey"], found["relShort"]) == relation


def test_cards_json_keeps_a_model_without_a_record_unknown_and_applies_an_admins_route(tmp_path: Path) -> None:
    routes = {"is_demo": False, "records": [_record("google_direct", basis="manual", checked_at="2026-10-29")]}
    _write(tmp_path, [_observed("e0", "price_change", "1.00", "2.00", model_id="gpt-image-2", route="openai"),
                      _observed("e1", "price_change", "1.00", "2.00", model_id="gemini-3.8-flash", route="google_direct")])
    _build(tmp_path, args=_routes(tmp_path, routes))
    judged = {tuple(card["events"]): card for card in json.loads((tmp_path / "site" / "cards.json").read_text(encoding="utf-8"))["cards"]}
    assert [judged[(event,)][key] for event in ("e0", "e1") for key in ("applicability", "action", "usage")] == [
        "UNKNOWN", "VERIFY", "사용 경로 확인 필요", "APPLIES", "REVIEW", "이 경로로 사용 중"]


def test_supply_block_carries_the_registry_routes_and_the_whole_routes_file(tmp_path: Path) -> None:
    """2026-10-29 공급로 현황: SUPPLY has a row per registered model (sources.py) with the routes it collects a price on, the
    supplier columns (one per publisher, so openai_direct·openai and vercel_ai_gateway·vercel share one; makers' own APIs,
    clouds, then gateways and fal) and the routes file as it is, which the tab's editor writes back in the same format."""
    _write(tmp_path, [])
    supply = _build(tmp_path, args=["--internal-routes", str(DEMO_ROUTES)])[2]["SUPPLY"]
    assert supply["routes"] == json.loads(DEMO_ROUTES.read_text(encoding="utf-8"))
    assert [name for name, _ in supply["cols"]] == ["Anthropic", "Google", "Kling AI", "OpenAI", "Z.ai", "AWS", "BytePlus", "Databricks",
                                                    "Google Cloud", "Microsoft", "OpenRouter", "Vercel", "fal"]
    assert (dict(supply["cols"])["OpenAI"], dict(supply["cols"])["Vercel"]) == (["openai_direct", "openai"], ["vercel_ai_gateway", "vercel"])
    assert len(supply["models"]) == 132 and sorted(supply["models"]["gpt-image-2"]) == ["openai", "openrouter", "vercel_ai_gateway"]
    # qwen3.5-plus went through OpenRouter, which lists it only under dated ids: since 2026-10-30 the one LETSUR's call cost is read
    assert supply["models"]["qwen3.5-plus"] == ["openrouter", "vercel_ai_gateway"]


def test_supply_block_without_a_routes_file_and_for_models_we_do_not_collect(tmp_path: Path) -> None:
    _write(tmp_path / "none", [])
    supply = _build(tmp_path / "none")[2]["SUPPLY"]
    assert supply["routes"] is None and len(supply["models"]) == 132 and len(supply["cols"]) == 13
    routes = {"is_demo": False, "records": [_record("friendli", model_id="glm-9", basis="manual")], "unresolved": {"glm-10": "응답 형식으로 못 가림"}}
    _write(tmp_path / "own", [])
    supply = _build(tmp_path / "own", args=_routes(tmp_path / "own", routes))[2]["SUPPLY"]
    assert (supply["models"]["glm-9"], supply["models"]["glm-10"], supply["cols"][-1]) == ([], [], ["friendli", ["friendli"]])
    # a failed day still carries it: the tab needs no events
    _files(tmp_path / "fail")
    supply = _build(tmp_path / "fail", args=_routes(tmp_path / "fail", routes))[2]["SUPPLY"]
    assert supply["routes"] == routes and len(supply["models"]) == 134


def test_model_list_groups_the_gateway_models_under_their_makers_and_stays_a_box_of_its_own_height(tmp_path: Path) -> None:
    """2026-10-28: grok, minimax, muse and qwen models get xAI, MiniMax, Meta and Alibaba in the 모델별 가격 list instead of 기타;
    every model of the committed routes file has a company, and no prefix hides a later one (the first match wins)."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    makers = _makers(page)
    assert dict(makers) == {"qwen": "Alibaba", "claude": "Anthropic", "seedance": "ByteDance", "byteplus-seedance": "ByteDance",
                            "deepseek": "DeepSeek", "gemini": "Google", "kling": "Kling AI", "muse": "Meta", "minimax": "MiniMax",
                            "kimi": "Moonshot AI", "gpt": "OpenAI", "o1": "OpenAI", "o3": "OpenAI", "o4": "OpenAI",
                            "text-embedding": "OpenAI", "jev": "TypeSafe AI", "grok": "xAI", "glm": "Z.AI"}
    assert all(_maker_of(makers, f"{prefix}-x") == company for prefix, company in makers)
    assert [_maker_of(makers, model) for model in ("grok-4.20", "minimax-m2.5", "muse-wonder-1.0", "qwen3.8-max")] == ["xAI", "MiniMax", "Meta", "Alibaba"]
    models = {record["model_id"] for record in json.loads(DEMO_ROUTES.read_text(encoding="utf-8"))["records"]}
    assert [model for model in sorted(models) if _maker_of(makers, model) == "기타"] == []
    # about 55 models: the sticky list scrolls inside itself (a taller one pushes the chart off screen), and so does the phone's list
    assert ".mlist{padding:12px;position:sticky;top:12px;max-height:calc(100vh - 24px);overflow-y:auto}" in page
    assert ".mlist{position:static;display:flex;flex-wrap:wrap;gap:4px;max-height:max(240px,40vh)}" in page


@pytest.mark.parametrize(("records", "risk"), [
    ([{"model_id": "m", "route_id": "vercel_ai_gateway", "basis": "observed"}],
     "렛서 경로(Vercel)도 12/31에 할인이 끝나 1/1부터 같은 가격이 될 수 있음(추정)"),
    # OpenRouter is the first of the model's routes the change reaches
    ([{"model_id": "m", "route_id": "vercel_ai_gateway", "basis": "settings"}, {"model_id": "m", "route_id": "openrouter", "basis": "observed"}],
     "렛서 경로(OpenRouter)도 12/31에 할인이 끝나 1/1부터 같은 가격이 될 수 있음(추정)"),
    ([{"model_id": "m", "route_id": "vercel_ai_gateway", "basis": "manual"}],
     "렛서 경로(Vercel)도 12/31에 할인이 끝나 1/1부터 같은 가격이 될 수 있음(추정)"),
    ([{"model_id": "m", "route_id": "google_direct", "basis": "observed"}], None),  # no LETSUR route on a gateway: no such warning
])
def test_the_discount_warning_names_the_route_it_is_about(records: list, risk: str) -> None:
    """The basis of the route is not written since 2026-10-30 (박건: 사용이면 사용)."""
    cards = [{"id": "up", "mid": "m", "t7": "예정 변경", "eff": "2027-01-01", "sum": "12개 항목 단가 1/1부터 인상 예정"},
             {"id": "vercel", "mid": "m", "t7": "한시 할인", "eff": "2026-12-31", "sum": "50% 할인 · 12/31까지"},
             {"id": "or", "mid": "m", "t7": "한시 할인", "sum": "할인 · 종료일 미확인"}]
    groups = {"up": [{"route": "google_direct"}], "vercel": [{"route": "vercel_ai_gateway"}], "or": [{"route": "openrouter"}]}
    promo = [{"route": route, "direct": "google_direct", "start": "2027-01-01", "standard": True, "after": "$1.50 / $7.50 / $0.15",
              "times": "2"} for route in ("openrouter", "vercel_ai_gateway")]
    build.link_promotions(cards, groups, {"m": {"groups": [], "promo": promo}}, {"records": records, "is_demo": False})
    assert cards[0].get("risk") == risk


def test_template_marks_a_called_route_like_a_registered_one(tmp_path: Path) -> None:
    """2026-10-28: relObs (the route one API call went through) got the same marks as relSet. 2026-10-30 (박건: 사용이면
    사용): the badge chip, 현재 사용 경로 tag and 제안 근거 note no longer say how the route is known; only a demo file's
    assumption (relDemo) keeps its 가정 mark."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert 'const relMark = e => relDemoOf(e) ? {chip: "가정", note: "시연용 가정"} : null;' in page
    assert '현재 사용 경로${relDemoOf(e) ? ` <span class="chip">가정</span>` : ""}</div>' in page
    assert '&& relDemoOf(e) ? " (렛서 관계는 시연용 가정)" : ""}</div></div>`;' in page
    assert not re.search(r"relSet|relObs|relMan|relInf|호출 추정", page)


def test_template_has_the_supply_tab_and_its_editor(tmp_path: Path) -> None:
    """2026-10-29 박건 요청 '공급로 현황': a fourth screen tab with its own #supply address, a matrix built from SUPPLY, and an
    editor that writes the whole routes file back (download, copy, GitHub edit link) with a draft kept only in this browser.
    2026-10-30 박건 요청: every cell is one checkbox (checked = LETSUR uses the route, disabled = no price collected there, so no
    candidate), and the tab writes no basis, date or count beyond 모델 n개."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    # 2026-10-30 박건 요청: 공급로 현황이 수집 상태 앞(세 번째 탭)이고, 상자를 바꾸기 전에 묻는다
    assert ('<button type="button" role="tab" data-ptab="supply" aria-selected="false">공급로 현황</button>'
            '<button type="button" role="tab" data-ptab="health" aria-selected="false">수집 상태</button></nav>') in page
    assert 'if (confirm(`${m}의 ${c[0]} 공급로${on ? "에서 해제" : "를 추가"}하시겠습니까?`)) {' in page
    assert '<section class="spage" id="supply" aria-label="공급로 현황">' in page and 'if (h === "supply") startSupply = true;' in page
    assert 'if (p === "supply") { renderSupply(); try { history.replaceState(null, "", "#supply"); } catch (err) {} return; }' in page
    assert 'const SP_FILE = "https://github.com/LETSUR-CREAI-IT/LETSUR/edit/develop/samples/market_collection/demo_internal_routes.json";' in page
    assert 'const spJson = () => JSON.stringify(spDoc(), null, 2) + "\\n";' in page and 'link.download = "demo_internal_routes.json";' in page
    # the draft: every storage call is inside try, so a browser that blocks storage still gets the page
    assert "function spSave(){ try { " in page and "try { const d = JSON.parse(localStorage.getItem(SP_DRAFT)" in page
    assert page.count("localStorage") == 8  # four for the supply draft, four for 확인 처리 and its time
    # one checkbox per cell: checked from the file (plus the draft), disabled where no price is collected; a second click undoes
    assert '<input type="checkbox" class="spx${use && !got ? " warn" : ""}" data-sp="cell"' in page
    assert '${use ? " checked" : ""}${got && SUPPLY.routes ? "" : " disabled"}></td>' in page
    assert "if (spEdits[k]) delete spEdits[k]; else spEdits[k] = {on: !spCell(SUPPLY.routes, m, c).length, at: spToday()}; }" in page
    assert '<h2>공급로 현황</h2><span>모델 ${modelQ || spLens ? `${shown.reduce((s, [, l]) => s + l.length, 0)}/` : ""}${count}개${spLens ? ` · ${esc(spLens.l)}만` : ""}</span>' in page
    for gone in ("spBasis", "spChip", "sppanel", "spun", "호출 추정", "렛서가 쓰는데 가격 수집 안 함"):
        assert gone not in page[page.index("// ---- 공급로 현황"):page.index("// ---- 상호작용 ----")]
    assert "@media (max-width:640px){.ptabs{flex-wrap:wrap;row-gap:4px}.ptabs button{padding:0 8px}}" in page


@pytest.mark.parametrize(("doc", "reason"), [
    (None, "routes.json 없음"),
    ("{", "routes.json을 읽을 수 없음"),
    ({"records": [_record("google_direct")]}, "routes.json 형식이 다름 (is_demo가 true·false가 아님)"),
    ({"is_demo": True, "records": {}}, "routes.json 형식이 다름 (records 목록 없음)"),
    ({"is_demo": True, "records": [{"route_id": "google_direct"}]}, "routes.json 형식이 다름 (records 1번째 model_id 없음)"),
    ({"is_demo": True, "records": [{"model_id": "gemini-3.8-flash"}]}, "routes.json 형식이 다름 (records 1번째 route_id 없음)"),
    ({"is_demo": True, "records": [_record("google_direct"), _record("", model_id="gpt-6.1-sol")]},
     "routes.json 형식이 다름 (records 2번째 route_id가 문자열이나 null이 아님)"),
    ({"is_demo": True, "records": [_record(None, endpoint_id=3)]}, "routes.json 형식이 다름 (records 1번째 endpoint_id가 문자열이나 null이 아님)"),
    ({"is_demo": True, "records": [_record("google_direct", basis="confirmed")]},
     "routes.json 형식이 다름 (records 1번째 basis가 settings·observed·manual이나 null이 아님)"),
    ({"is_demo": False, "records": [_record("google_direct", basis="observed", checked_at="10/29")]},
     "routes.json 형식이 다름 (records 1번째 checked_at이 YYYY-MM-DD 날짜나 null이 아님)"),
    ({"is_demo": False, "records": [_record("google_direct", basis="inferred", note={"why": "x"})]},
     "routes.json 형식이 다름 (records 1번째 note가 문자열이나 null이 아님)"),
    ({"is_demo": False, "records": [], "unresolved": ["glm-5.2"]}, "routes.json 형식이 다름 (unresolved가 모델 id와 메모 문자열의 묶음이 아님)"),
    ({"is_demo": False, "records": [], "unresolved": {"glm-5.2": ""}}, "routes.json 형식이 다름 (unresolved가 모델 id와 메모 문자열의 묶음이 아님)"),
    ({"is_demo": False, "records": [], "unresolved": {"": "모름"}}, "routes.json 형식이 다름 (unresolved가 모델 id와 메모 문자열의 묶음이 아님)"),
])
def test_unusable_routes_file_still_writes_the_page(tmp_path: Path, doc, reason: str) -> None:
    _write(tmp_path, [_scheduled("e1", "input", "0.75", "1.50", "0.75")])
    code, _, data = _build(tmp_path, args=_routes(tmp_path, doc))
    [card] = data["EVENTS"]
    assert (code, card["relKey"], card["relShort"], card["relDemo"]) == (0, "unk", "사용 경로 확인 필요", False)
    assert data["MODELS"] == {"gemini-3.8-flash": {"prov": "Google", "kind": "LLM"}}
    assert data["STATUS"] == f'<div class="gen-status warn"><b>렛서 사용 경로 파일을 쓰지 못했습니다</b> · {reason} · 모든 카드를 사용 경로 확인 필요로 두었습니다</div>'


def test_existing_output_is_left_alone(tmp_path: Path) -> None:
    _write(tmp_path, [])
    out = tmp_path / "site"
    out.mkdir()
    (out / "keep.txt").write_text("old", encoding="utf-8")
    assert build.main(["--root", str(tmp_path / "root"), "--output", str(out)], now=NOW) == 2
    assert [path.name for path in out.iterdir()] == ["keep.txt"]


def test_unexpected_error_still_writes_a_failure_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(root: Path, today: str, routes: dict | None) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(build, "collect", broken)
    _write(tmp_path, [])
    code, _, data = _build(tmp_path)
    assert code == 1
    assert data["STATUS"] == '<div class="gen-status fail"><b>오늘 생성 실패</b> · 대시보드 생성 중 오류: RuntimeError</div>'
    assert (data["EVENTS"], data["COLLECTED"]) == ([], {"runs": []})


@pytest.mark.parametrize(("now", "today"), [
    (datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc), "2026-10-21"),
    (datetime(2026, 10, 20, 14, 59, 59, tzinfo=timezone.utc), "2026-10-20"),
])
def test_today_is_the_kst_build_date(tmp_path: Path, now: datetime, today: str) -> None:
    _write(tmp_path, [])
    assert _build(tmp_path, now)[2]["TODAY"] == today


def test_data_cannot_end_the_script(tmp_path: Path) -> None:
    hostile = "m</script><script>alert(1)</script>"
    quote = "a </script> b \u2028 c & d"
    event = _claim("x1", "price_change", "openrouter_endpoints", quote, model_id=hostile, route="openrouter", claim_kind="price_decrease",
                   dates={"published": "2026-10-03", "effective_from": None, "note": ""})
    _write(tmp_path, [event])
    _, page, data = _build(tmp_path)
    assert "<script>alert(1)" not in page and "\u2028" not in page
    [card] = data["EVENTS"]
    assert (card["m"], card["ev"], list(data["MODELS"])) == (hostile, [["s1", f"“{quote}”"]], [hostile])


def test_trend_leaves_out_suspect_observations_and_claims(tmp_path: Path) -> None:
    event = _observed("t1", "price_change", "0.75", "7.50", status="candidate",
                      change={"delta": "6.75", "pct": "900.00", "direction": "up", "note": ""})
    claim = _claim("t2", "price_change", "openrouter_endpoints", "Now $9.99.", route="google_direct", claim_kind="price_increase",
                   after={"amount": "9.99", "currency": "USD", "unit": "1M tokens"}, dates={"published": "2026-10-03", "note": ""})
    gap = {"kind": "parser_fingerprint_mismatch", "source_key": GOOGLE, "billing_item": "input", "before_observation_id": "t1-1",
           "after_observation_id": "t1-2", "note": ""}
    _write(tmp_path, [event, claim], gaps=[gap], state={"series": {"in": _stored("t1-2", "input", "7.50")}})
    trend = _build(tmp_path)[2]["TREND"]["gemini-3.8-flash"]
    assert trend["series"] == [{"name": "Google · google_direct", "pts": [["2026-10-04T15:22", [0.75]]]}]
    assert "검증되지 않은 관측 1건(파서 의심)은 뺐습니다." in trend["note"]
    assert trend["badge"] == ["변화 2건", ""]


OPENROUTER, VERCEL = "openrouter_llm_endpoints_deepseek_v4_pro", "vercel_llm_endpoints_deepseek_v4_pro"
# What the gateway listings state about the endpoint itself (llm_endpoints.py); none of it is a price condition.
ALIBABA = {"price_basis": "endpoint_default_conditions", "endpoint_tag": "alibaba/fp8/us-east-1", "context_length": "1000000",
           "quantization": "fp8", "endpoint_status": "0", "endpoint_discount": "0", "endpoint_model_slug": "deepseek/deepseek-v4-pro-20260423"}
NOVITA = {"price_basis": "endpoint_default_conditions", "endpoint_tag": "novita/fp4", "context_length": "1048576",
          "quantization": "fp4", "endpoint_status": "0", "endpoint_discount": "0"}
DEEPINFRA = {"price_basis": "endpoint_listed_rate", "endpoint_provider": "DeepInfra", "context_length": "1048576",
             "quantization": "fp8", "endpoint_status": "0"}


def _gateway(observation_id: str, route: str, source_key: str, provider: str, amount: str, **conditions: str) -> dict:
    """A state.json series entry of a gateway endpoint listing; the endpoint states who serves it as conditions."""
    entry = _stored(observation_id, "input", amount)
    entry["obs"].update(model_id="deepseek-v4-pro", route=route, source_key=source_key, upstream_provider=provider,
                        provider_model_id="deepseek/deepseek-v4-pro", conditions={"service_tier": "standard", **conditions}, effective_until="")
    return entry


def _gateway_change(event_id: str, endpoint: dict, before: str, after: str) -> dict:
    """The input price of one OpenRouter endpoint changed between RUN1 and RUN2."""
    return _event(event_id, "price_change", model_id="deepseek-v4-pro", route="openrouter", source_key=OPENROUTER, billing_item="input",
                  conditions={"service_tier": "standard", **endpoint}, before=_side(before, RUN1, f"{event_id}-1", AT1),
                  after=_side(after, RUN2, f"{event_id}-2", AT2), change={"delta": "-0.10", "pct": "-33.33", "direction": "down", "note": ""},
                  dates={"observed_before_utc": AT1, "observed_after_utc": AT2, "published": None, "effective_from": None, "note": ""},
                  evidence_refs=[_ref(OPENROUTER, RUN1, f"{event_id}-1", f"input {before}"), _ref(OPENROUTER, RUN2, f"{event_id}-2", f"input {after}")],
                  sources=[_source(OPENROUTER, "channel_official")])


@pytest.mark.parametrize(("conditions", "band"), [
    ({"prompt_tokens": "200000", "service_tier": "Standard"}, "> 200K"),  # OpenRouter override: above 200,000 input tokens
    ({"context_tier_min_tokens": "200001"}, "> 200K"),  # Vercel's upper tier
    ({"context_tier_min_tokens": "0", "context_tier_max_tokens": "200001"}, "<= 200K"),  # Vercel's base tier
    ({"context_band": "prompts > 200k tokens", "service_tier": "Standard"}, "> 200K"),  # Google
    ({"input_token_band": ">100K"}, "> 100K"),  # Anthropic
])
def test_token_bands_read_as_one_input_band(conditions: dict, band: str) -> None:
    """2026-10-30: the trend tab of a token band read '200001 · 0' or '200000 · Standard'; every route's way of stating it is now
    input_band, which the tab names 입력 200K 초과/이하, and the same band on every route shares one tab."""
    found = build.input_band(conditions)
    assert found["input_band"] == band and not {"prompt_tokens", "context_tier_min_tokens", "context_tier_max_tokens", "context_band",
                                                "input_token_band"} & set(found)
    assert {key: value for key, value in found.items() if key != "input_band"} == {k: v for k, v in conditions.items() if k == "service_tier"}
    assert build.tab_value("input_band", band) == ("입력 200K 초과" if band == "> 200K" else "입력 200K 이하" if band == "<= 200K" else "입력 100K 초과")
    assert build.input_band({"service_tier": "Standard"}) == {"service_tier": "Standard"}


def test_endpoints_split_into_tabs_by_precision_and_region(tmp_path: Path) -> None:
    """2026-10-27 decision 21: tabs are the comparison table's groups (precision, region, tier), so the lines of one tab are
    the same condition whoever serves them."""
    state = {"series": {"a": _gateway("g1-2", "openrouter", OPENROUTER, "Alibaba", "0.20", **ALIBABA),
                        "n": _gateway("n-2", "openrouter", OPENROUTER, "Novita", "0.25", **NOVITA),
                        "d": _gateway("d-2", "vercel_ai_gateway", VERCEL, "DeepInfra", "0.30", **DEEPINFRA)}}
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")], state=state)
    trend = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]
    assert {tab: [(line["name"], [pt[1] for pt in line["pts"]]) for line in lines] for tab, lines in trend["tiers"].items()} == {
        "fp8 · US": [("OpenRouter · openrouter · alibaba/fp8/us-east-1", [[0.3], [0.2]])],
        "fp4 · Standard": [("OpenRouter · openrouter · novita/fp4", [[0.25]])],
        "fp8 · Standard": [("Vercel · vercel_ai_gateway · DeepInfra · fp8", [[0.3]])]}
    assert "연결할 선이 여럿이라" not in trend["note"]


def test_the_letsur_line_is_the_one_the_price_table_marks(tmp_path: Path) -> None:
    """2026-10-30 C안 가격 추이: the line LETSUR uses is marked by the 현재 가격 표's own rule (compare.is_assumed, decision 17),
    so the graph and the table never point at different lines. A gateway record without an endpoint marks the maker's own
    upstream only (DeepInfra serves deepseek here, so none). The model's routes are listed under the graph; how each is known
    is not said (2026-10-30 박건: 사용이면 사용)."""
    state = {"series": {"a": _gateway("g1-2", "openrouter", OPENROUTER, "Alibaba", "0.20", **ALIBABA),
                        "n": _gateway("n-2", "openrouter", OPENROUTER, "Novita", "0.25", **NOVITA),
                        "d": _gateway("d-2", "vercel_ai_gateway", VERCEL, "DeepInfra", "0.30", **DEEPINFRA)}}
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")], state=state)
    records = [{"model_id": "deepseek-v4-pro", "route_id": "openrouter", "endpoint_id": "novita/fp4", "basis": "manual", "checked_at": "2026-10-29"},
               {"model_id": "deepseek-v4-pro", "route_id": "vercel_ai_gateway", "basis": "observed", "checked_at": "2026-10-30",
                "note": "이번 호출의 하위 공급자 DeepInfra(요청마다 바뀔 수 있음)"}]
    trend = _build(tmp_path, args=_routes(tmp_path, {"is_demo": False, "records": records}))[2]["TREND"]["deepseek-v4-pro"]
    assert {tab: [(line["name"], line.get("letsur")) for line in lines] for tab, lines in trend["tiers"].items()} == {
        "fp8 · US": [("OpenRouter · openrouter · alibaba/fp8/us-east-1", None)],
        "fp4 · Standard": [("OpenRouter · openrouter · novita/fp4", True)],
        "fp8 · Standard": [("Vercel · vercel_ai_gateway · DeepInfra · fp8", None)]}
    assert trend["letsurRoutes"] == ["OpenRouter · openrouter", "Vercel · vercel_ai_gateway · 이번 호출의 하위 공급자 DeepInfra(요청마다 바뀔 수 있음)"]


def test_a_direct_route_line_is_letsurs(tmp_path: Path) -> None:
    """A direct route's record marks that route's line, as the table marks its Standard row; without a routes file no line is
    marked (test_trend_leaves_out_suspect_observations_and_claims)."""
    _write(tmp_path, [_observed("t1", "price_change", "0.75", "0.50")], state={"series": {"in": _stored("t1-2", "input", "0.50")}})
    record = {"model_id": "gemini-3.8-flash", "route_id": "google_direct", "basis": "settings"}
    trend = _build(tmp_path, args=_routes(tmp_path, {"is_demo": False, "records": [record]}))[2]["TREND"]["gemini-3.8-flash"]
    assert [(line["name"], line.get("letsur")) for line in trend["series"]] == [("Google · google_direct", True)]
    assert trend["letsurRoutes"] == ["Google · google_direct"]


def test_a_different_service_tier_still_gets_its_own_tab(tmp_path: Path) -> None:
    state = {"series": {"a": _gateway("g1-2", "openrouter", OPENROUTER, "Alibaba", "0.20", **ALIBABA),
                        "n": _gateway("n-2", "openrouter", OPENROUTER, "Novita", "0.25", **NOVITA),
                        "p": _gateway("p-2", "openrouter", OPENROUTER, "Alibaba", "0.60", service_tier="priority", **ALIBABA)}}
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")], state=state)
    tiers = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]["tiers"]
    assert {tab: [line["name"] for line in lines] for tab, lines in tiers.items()} == {
        "fp8 · US": ["OpenRouter · openrouter · alibaba/fp8/us-east-1"], "fp4 · Standard": ["OpenRouter · openrouter · novita/fp4"],
        "fp8 · US · Priority": ["OpenRouter · openrouter · alibaba/fp8/us-east-1"]}



def test_a_model_page_headline_price_is_not_drawn(tmp_path: Path) -> None:
    """2026-10-30 (박건: 애매한 경우가 없어야): a gateway price that names no endpoint is its model page's headline (the 공급사 비교
    표's 모델 페이지 대표가, now also the image pages' displayed model quote and a page price stating no basis), so it is no
    line: the gateway's endpoint lines stand for it, and the note says how many were left out."""
    state = {"series": {"a": _gateway("g1-2", "openrouter", OPENROUTER, "Alibaba", "0.20", **ALIBABA),
                        "p": _gateway("p-2", "openrouter", OPENROUTER, "Alibaba", "0.60", service_tier="priority", **ALIBABA),
                        "v": _gateway("v-2", "vercel", "vercel_models", "Vercel", "0.15", price_basis="displayed model quote"),
                        "o": _gateway("o-2", "openrouter", "openrouter_model_page", "OpenRouter", "0.14")}}
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")], state=state)
    trend = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]
    assert {tab: [line["name"] for line in lines] for tab, lines in trend["tiers"].items()} == {
        "fp8 · US": ["OpenRouter · openrouter · alibaba/fp8/us-east-1"], "fp8 · US · Priority": ["OpenRouter · openrouter · alibaba/fp8/us-east-1"]}
    assert not any(line.get("ref") for lines in trend["tiers"].values() for line in lines)
    assert "하위 공급자를 적지 않은 중개사 모델 페이지 대표가 선 2개는 그리지 않았습니다" in trend["note"]
    assert trend["units"] == {"fp8 · US": "USD / 100만 토큰", "fp8 · US · Priority": "USD / 100만 토큰"}


def test_a_tier_the_source_leaves_unsaid_is_the_tables(tmp_path: Path) -> None:
    """2026-10-30: the trend groups token prices as the 공급사 비교 표 does. Vercel's untiered price is Standard (decision 18), and so
    is a direct route's price that names no tier, so both join Google's Standard line instead of a tab called 조건 없음."""
    vercel = _stored("v-2", "input", "0.50")
    vercel["obs"].update(route="vercel_ai_gateway", source_key="vercel_llm_endpoints_gemini_3_8_flash", upstream_provider="google",
                         conditions={"price_basis": "endpoint_listed_rate", "endpoint_provider": "google", "endpoint_status": "0"},
                         unknown_conditions=["quantization", "service_tier"])
    untiered = _stored("u-2", "input", "0.50")
    untiered["obs"].update(source_key="google_gemini_38_flash_page", conditions={})
    state = {"series": {"in": _stored("t1-2", "input", "0.50"), "v": vercel, "u": untiered}}
    _write(tmp_path, [_observed("t1", "price_change", "0.75", "0.50")], state=state)
    trend = _build(tmp_path)[2]["TREND"]["gemini-3.8-flash"]
    assert "tiers" not in trend and list(trend["conds"]) == ["Standard"]
    assert len(trend["series"]) == 3 and not any(line.get("ref") for line in trend["series"])
    assert "Vercel · vercel_ai_gateway · google" in [line["name"] for line in trend["series"]]


def test_a_line_of_unknown_precision_stands_dotted_beside_both_precisions(tmp_path: Path) -> None:
    """2026-10-30: the table leaves an open-weight model's line out when no source states its precision (정밀도 미상, decision 5).
    Here it is a 비교 제외 line, drawn in every tab it would be compared in (fp4 and fp8 of its tier) and never compared; the
    tab of its own goes."""
    unknown = {**NOVITA, "endpoint_tag": "example", "quantization": ""}
    state = {"series": {"a": _gateway("g1-2", "openrouter", OPENROUTER, "Alibaba", "0.20", **ALIBABA),
                        "n": _gateway("n-2", "openrouter", OPENROUTER, "Novita", "0.25", **NOVITA),
                        "d": _gateway("d-2", "vercel_ai_gateway", VERCEL, "DeepInfra", "0.30", **DEEPINFRA),
                        "x": _gateway("x-2", "openrouter", OPENROUTER, "Example", "0.18", **unknown)}}
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")], state=state)
    trend = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]
    assert {tab: [(line["name"], line.get("ref")) for line in lines] for tab, lines in trend["tiers"].items()} == {
        "fp8 · US": [("OpenRouter · openrouter · alibaba/fp8/us-east-1", None)],
        "fp4 · Standard": [("OpenRouter · openrouter · novita/fp4", None), ("OpenRouter · openrouter · example", "정밀도 미상")],
        "fp8 · Standard": [("Vercel · vercel_ai_gateway · DeepInfra · fp8", None), ("OpenRouter · openrouter · example", "정밀도 미상")]}
    assert trend["tiers"]["fp4 · Standard"][1]["refWhy"] == "정밀도 미상 · 직접 확인 필요"
    assert "비교 표가 비교에서 빼는 선 1개(정밀도 미상)는 점선으로 두고 그래프 안 최저와 숫자 칸에서 뺐습니다." in trend["note"]


def test_status_lines_follow_the_comparison_table(tmp_path: Path) -> None:
    """Decision 8: the 공급사 비교 표 compares OpenRouter's -1 and -2 with a chip and leaves -3, -5 and -10 out, so only those are
    비교 제외 lines here, with the table's reason (2026-10-30: always drawn, there is no 엄격 비교·전체 관측 switch)."""
    state = {"series": {"a": _gateway("g1-2", "openrouter", OPENROUTER, "Alibaba", "0.20", **ALIBABA),
                        "b": _gateway("b-2", "openrouter", OPENROUTER, "Baidu", "0.25",
                                      **{**ALIBABA, "endpoint_tag": "baidu/fp8/us-east-1", "endpoint_status": "-2"}),
                        "c": _gateway("c-2", "openrouter", OPENROUTER, "Crusoe", "0.15",
                                      **{**ALIBABA, "endpoint_tag": "crusoe/fp8/us-east-1", "endpoint_status": "-5"})}}
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")], state=state)
    lines = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]["series"]  # one tab (fp8 · US), so no tiers
    assert {line["name"].rsplit(" · ", 1)[-1]: line.get("ref") for line in lines} == {
        "alibaba/fp8/us-east-1": None, "baidu/fp8/us-east-1": None, "crusoe/fp8/us-east-1": "중단"}
    assert [line["refWhy"] for line in lines if line.get("ref")] == ["OpenRouter 상태: 중단, 지금 요청이 실패하고 있어 대체 경로로만 쓰임"]


def test_the_image_reviews_own_reading_is_not_drawn() -> None:
    """2026-10-30: the image review reads OpenRouter's images catalog per token, its endpoint a hashed profile id, and those
    tabs were named by a rate line's raw JSON. The catalog's price source draws the same rates per 1M tokens, so the review's
    observations are left out."""
    def obs(oid: str, source: str, unit: str, amount: str, **conditions: str) -> dict:
        return {"obs": {"observation_id": oid, "run_id": RUN2, "observed_at_utc": AT2, "model_id": "gpt-image-2.5-flare", "route": "openrouter",
                        "source_key": source, "upstream_provider": "OpenAI", "provider_model_id": "openai/gpt-image-2.5-flare",
                        "task": "text-to-image", "billing_item": "image_output", "amount": amount, "currency": "USD", "unit": unit,
                        "conditions": conditions}}
    series = {"r": obs("r-1", "image25_openrouter_flare_endpoints", "1 token", "0.00003", billable="output_image",
                       pricing_line='{"billable":"output_image","unit":"token","cost_usd":0.00003}'),
              "c": obs("c-1", "openrouter_image_catalog_endpoints_gpt_image_2_5_flare", "1M tokens", "30", endpoint_tag="openai",
                       price_basis="endpoint_listed_rate")}
    review = {spec.source_key for spec in build.source_specs() if spec.parser in build.REVIEW_PARSERS}
    entry = build.model_trend("gpt-image-2.5-flare", [], {}, series, set(), None, review)
    assert [line["name"] for line in entry["series"]] == ["OpenRouter · openrouter · openai"] and list(entry["conds"]) == ["Standard"]
    assert "이미지 검토용 출처의 토큰 단가 관측 1건은 그리지 않았습니다" in entry["note"]


def test_two_names_of_one_condition_are_one_tab_condition() -> None:
    """2026-10-30: OpenRouter's and Bedrock's cache_write_ttl is Anthropic's cache_duration, and OpenAI's image pages state their
    tier as processing; a stated tier is never overwritten."""
    assert build.one_name({"cache_write_ttl": "1h", "service_tier": "Standard"}) == {"service_tier": "Standard", "cache_duration": "1h"}
    assert build.one_name({"processing": "Batch"}) == {"service_tier": "Batch"}
    assert build.one_name({"processing": "Batch", "service_tier": "Flex"}) == {"processing": "Batch", "service_tier": "Flex"}


def test_tabs_named_alike_add_the_unit_then_korean_condition_names() -> None:
    """2026-10-30: two tabs of one name read 'service_tier Standard · 1 image' and 'service_tier Standard · 100만 토큰'."""
    standard = json.dumps({"service_tier": "Standard"})
    keys = [(standard, "1M tokens"), (standard, "1 image")]
    assert build.tab_names(keys) == {keys[0]: "Standard · 100만 토큰", keys[1]: "Standard · 이미지 1장"}
    quiet = json.dumps({"region_group": "Commercial Regions", "service_tier": "Standard"}, sort_keys=True)
    assert build.tab_names([(standard, "1M tokens"), (quiet, "1M tokens")])[(standard, "1M tokens")] == "등급 Standard · 100만 토큰"
    assert build.tab_value("promotion_note", "promotional pricing is available at least through November 21, 2026.") == "프로모션 가격 11/21까지"


def _series(oid: str, model: str, route: str, source: str, item: str, amount: str, unit: str = "1M tokens", task: str = "text-to-image",
            **conditions: str) -> dict:
    return {"obs": {"observation_id": oid, "run_id": RUN2, "observed_at_utc": AT2, "model_id": model, "route": route, "source_key": source,
                    "upstream_provider": "", "provider_model_id": "", "task": task, "billing_item": item, "amount": amount,
                    "currency": "USD", "unit": unit, "conditions": conditions}}


def test_openais_image_page_items_are_the_gateways_items() -> None:
    """2026-10-30: OpenAI's image pages name their rates 'Image tokens / Output' and so on, the gateways and Google image_output,
    input and cache_read, so the same rate of two routes was two items never compared. Vercel's output stays output: its
    listing does not say whether it is the image or the text rate."""
    series = {"o": _series("o-1", "gpt-image-2", "openai", "openai_gpt_image_2", "Image tokens / Output", "30", service_tier="standard"),
              "t": _series("t-1", "gpt-image-2", "openai", "openai_gpt_image_2", "Text tokens / Input", "5", service_tier="standard"),
              "c": _series("c-1", "gpt-image-2", "openrouter", "openrouter_image_catalog_endpoints_gpt_image_2", "image_output", "30",
                           endpoint_tag="openai", price_basis="endpoint_listed_rate"),
              "v": _series("v-1", "gpt-image-2", "vercel_ai_gateway", "vercel_image_endpoints_gpt_image_2", "output", "30",
                           endpoint_provider="openai", endpoint_status="0", price_basis="endpoint_listed_rate")}
    entry = build.model_trend("gpt-image-2", [], {}, series, set())
    assert entry["measures"] == ["입력", "출력", "이미지 출력"] and list(entry["conds"]) == ["Standard"]
    outputs = {line["name"]: line["pts"][-1][1][2] for line in entry["series"]}
    assert outputs == {"OpenAI · openai": 30.0, "OpenRouter · openrouter · openai": 30.0, "Vercel · vercel_ai_gateway · openai": None}


def test_video_tabs_name_their_conditions_and_vercel_metadata_splits_no_tab() -> None:
    """2026-10-30: a video tab read 'on · pro · off'; a condition whose value does not say what it is now carries its Korean name
    and, by 박건's '한국어로 바꿔줘', a Korean value, as the cards do. Vercel's video metadata (provider, supported lists, an
    operation that is the model's own task) tells lines apart, not tabs, so its line shares fal's tab."""
    fal = _series("f-1", "kling-v3-text-to-video", "fal", "fal_kling_v3_t2v", "generated_video", "0.168", unit="1 second", task="text-to-video",
                  audio="off", mode="pro")
    vercel = _series("v-1", "kling-v3-text-to-video", "vercel_ai_gateway", "vercel_kling_v3_t2v", "generated_video", "0.168", unit="1 second",
                     task="text-to-video", audio="off", mode="pro", operation="text-to-video", provider="klingai",
                     supportedFps="[24]", supportedOperations='["text-to-video"]')
    entry = build.model_trend("kling-v3-text-to-video", [], {}, {"f": fal, "v": vercel}, set())
    assert list(entry["conds"]) == ["오디오 끔 · 모드 프로"] and entry["measures"] == ["생성 영상"] and len(entry["series"]) == 2
    named = (json.dumps({"billed_duration": "input plus output length (minimum 2× the output)", "input_video": "yes", "resolution": "1080p"}), "1 second")
    vague = (json.dumps({"audio": "supported; rate has no audio distinction", "generateAudio": "true", "input_video": "none", "resolution": "720p"}), "1M tokens")
    assert build.tab_names([named, vague]) == {named: "청구 길이 입력+출력(최소 출력의 2배) · 입력 영상 있음 · 1080p",
                                               vague: "오디오 구분 없음 · 오디오 생성 켬 · 입력 영상 없음 · 720p"}
    assert build.tab_conditions([vague])[vague] == build.tab_names([vague])[vague]  # the comparison line says the same
    # a card's row label names them too; a value the table of Korean values does not know stays as written
    assert [build.condition_value(key, value) for key, value in (("audio", "on (default)"), ("operation", "generate"), ("mode", "turbo"),
                                                                 ("resolution", "1080p"))] == ["오디오 켬(기본)", "작업 생성", "모드 turbo", "1080p"]


def test_letsurs_line_is_never_the_one_past_the_limit(tmp_path: Path) -> None:
    """A tab draws six lines; LETSUR's line comes first, then a line with a change, then the rest by name (2026-10-30: more lines
    share a tab now that untiered lines join their tier)."""
    endpoints = {f"p{n}": {**NOVITA, "endpoint_tag": f"p{n}/fp8", "quantization": "fp8"} for n in range(8)}
    state = {"series": {tag: _gateway(f"{tag}-2" if tag != "p7" else "g1-2", "openrouter", OPENROUTER, tag, "0.20", **endpoint)
                        for tag, endpoint in endpoints.items()}}
    _write(tmp_path, [_gateway_change("g1", endpoints["p7"], "0.30", "0.20")], state=state)
    records = [{"model_id": "deepseek-v4-pro", "route_id": "openrouter", "endpoint_id": "p6/fp8", "basis": "observed", "checked_at": "2026-10-30"}]
    trend = _build(tmp_path, args=_routes(tmp_path, {"is_demo": False, "records": records}))[2]["TREND"]["deepseek-v4-pro"]
    assert [(line["name"], line.get("letsur")) for line in trend["series"]] == [
        *((f"OpenRouter · openrouter · p{n}/fp8", None) for n in (0, 1, 2, 3)), ("OpenRouter · openrouter · p6/fp8", True),
        ("OpenRouter · openrouter · p7/fp8", None)]
    assert "선이 6개를 넘어 뺀 선 2개: fp8 · Standard: OpenRouter · openrouter · p4/fp8, fp8 · Standard: OpenRouter · openrouter · p5/fp8." in trend["note"]


def test_template_has_no_comparison_mode_and_draws_excluded_lines_dotted() -> None:
    """2026-10-30 (박건: 애매한 경우가 없어야): the 엄격 비교·전체 관측 switch is gone. A 비교 제외 line is always drawn, dotted in the
    graph and the legend, and kept out of the lowest, the number tiles and the tooltip's comparison."""
    html = Path(build.__file__).with_name("template.html").read_text(encoding="utf-8")
    assert not any(word in html for word in ("data-cmp", "cmpmode", "trendCompare", "comparableSeries"))
    assert ".tplot .ln.ref line{stroke-dasharray:2 5}" in html and ".lg .ref i{border-top-style:dotted}" in html
    assert "M.series.filter(s => !s.old && !s.ref)" in html and 'o.se.ref ? "비교 제외"' in html


def test_lines_over_the_limit_say_how_many_were_left_out(tmp_path: Path) -> None:
    endpoints = {f"p{n}": {**NOVITA, "endpoint_tag": f"p{n}/fp8", "quantization": "fp8"} for n in range(8)}
    state = {"series": {tag: _gateway(f"{tag}-2" if tag != "p7" else "g1-2", "openrouter", OPENROUTER, tag, "0.20", **endpoint)
                        for tag, endpoint in endpoints.items()}}
    _write(tmp_path, [_gateway_change("g1", endpoints["p7"], "0.30", "0.20")], state=state)
    trend = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]
    assert [line["name"] for line in trend["series"]] == [f"OpenRouter · openrouter · p{n}/fp8" for n in (0, 1, 2, 3, 4, 7)]  # the changed endpoint stays
    assert ("선이 6개를 넘어 뺀 선 2개: fp8 · Standard: OpenRouter · openrouter · p5/fp8, fp8 · Standard: OpenRouter · openrouter · p6/fp8."
            in trend["note"])


def test_event_without_optional_fields(tmp_path: Path) -> None:
    event = {"event_id": "b1", "event_type": "limited_discount", "model_id": "seedance-2.5", "route": "fal", "status": "candidate",
             "dates": None, "change": None, "before": None, "after": None, "evidence_refs": None, "sources": None}
    _write(tmp_path, [event])
    code, _, data = _build(tmp_path)
    [card] = data["EVENTS"]
    assert (code, card["t7"], card["chg"], card["sum"], card["flag"], data["SRC"]) == (0, "한시 할인", [], "할인 · 종료일 미확인", "공식 확인 전", {})


# ---- 상세페이지: 종류별 정교화 (status, checks, type candidates, clue, grouped rows) ----

def test_card_carries_status_and_checks(tmp_path: Path) -> None:
    claim = _claim("p9", "price_change", "openai_news", "Prices go up.", model_id="gpt-6-sol", route="openai_direct", claim_kind="price_increase",
                   dates={"published": "2026-10-03", "note": ""},
                   gaps=[{"field": "observed_price", "class": "check_first", "reason": "x", "next_check": "y"},
                         {"field": "letsur_usage", "class": "check_first", "reason": "no LETSUR usage or contract input"}])
    _write(tmp_path, [claim])
    card = _build(tmp_path)[2]["EVENTS"][0]
    assert card["st"] == "candidate" and card["clue"] is True
    assert card["chk"] == [["first", build.GAP_KO["observed_price"]]]  # letsur_usage is said once elsewhere, not per card


def test_observed_change_is_not_a_clue_and_counts_rows(tmp_path: Path) -> None:
    event = _observed("o9", "price_change", "1.00", "1.20", unchanged_items=[
        {"billing_item": "output", "amount": "5.00", "conditions": {"service_tier": "standard"}, "observation_id": "u1"}],
        next_checks=["confirm the official source still shows the new value on the next run"])
    _write(tmp_path, [event])
    card = _build(tmp_path)[2]["EVENTS"][0]
    assert card["st"] == "confirmed" and "clue" not in card
    assert card["n"] == [1, 1]
    assert ["next", build.NEXT_KO["confirm the official source still shows the new value on the next run"]] in card["chk"]


def test_undecided_type_lists_its_candidates(tmp_path: Path) -> None:
    claim = _claim("a9", None, "vercel_changelog", "Now available on AI Gateway.", route="vercel_ai_gateway", subtags=["add"],
                   type_candidates=["route_change", "model_release"], claim_kind="availability",
                   dates={"maker_first_release_date": None, "route_added_date": "2026-10-12", "published": "2026-10-12", "note": ""})
    _write(tmp_path, [claim])
    card = _build(tmp_path)[2]["EVENTS"][0]
    assert card["t7"] == "공급 경로 변경" and card["tc"] == ["모델 출시", "공급 경로 변경"]


def test_condition_price_change_has_its_own_type(tmp_path: Path) -> None:
    event = _observed("c9", "condition_price_change", "0.20", "0.10", item="cache_read",
                      condition={"key": "cache_read_multiplier", "before": {"value": "0.1", "unit": "x base input price"},
                                 "after": {"value": "0.05", "unit": "x base input price"}},
                      change={"delta": "-0.10", "pct": "-50.00", "price_delta": "-0.10", "price_pct": "-50.00"},
                      subtags=["billing.cache_read_multiplier"])
    _write(tmp_path, [event])
    card = _build(tmp_path)[2]["EVENTS"][0]
    assert card["t7"] == "조건·가격 변경" and card["sub"] == "과금 · 캐시"
    assert [row[0] for row in card["chg"]] == ["캐시 읽기 배율", "캐시 읽기"]


def test_rows_are_grouped_by_condition_and_deduplicated(tmp_path: Path) -> None:
    def endpoint(event_id, before, after, length, tag):
        event = _observed(event_id, "price_change", before, after, route="openrouter")
        event["conditions"] = {"service_tier": "standard", "context_length": length, "endpoint_tag": tag}
        return event
    events = [endpoint("e1", "2.50", "1.20", "1048576", "Decart/fast"), endpoint("e2", "0.69", "0.55", "262144", "Inceptron/fp4")]
    twin = endpoint("e3", "2.50", "1.20", "1048576", "Decart/fast")  # the same price read again
    _write(tmp_path, events + [twin])
    cards = _build(tmp_path)[2]["EVENTS"]
    card = next(c for c in cards if len(c["chg"]) >= 2)
    assert len(card["chg"]) == 2
    assert [group for group, _ in card["cg"]] == ["컨텍스트 1M · Decart/fast", "컨텍스트 256K · Inceptron/fp4"]


def test_size_ko() -> None:
    assert [build.size_ko(v) for v in ("1048576", "262144", "131072", "4096", "1000")] == ["1M", "256K", "128K", "4K", "1000"]


def test_condition_values_keep_source_text_except_tier_and_region():
    """2026-10-27 decision 26-1: values as the source wrote them; tiers and regions get names; status 0 and price basis hidden."""
    assert build.value_text("quantization", "fp4") == "fp4"
    assert build.value_text("endpoint_tag", "inference-net/fast") == "inference-net/fast"
    assert build.value_text("endpoint_model_slug", "z-ai/glm-5.2-20260616") == "z-ai/glm-5.2-20260616"
    assert build.value_text("service_tier", "priority") == "Priority"
    assert build.value_text("region", "us") == "US"
    assert build.value_text("endpoint_status", "0") == ""
    assert build.value_text("endpoint_status", "-2") == "성능 저하"
    assert build.value_text("price_basis", "endpoint_default_conditions") == ""
    assert build.condition_name("region") == "지역"


def test_undated_feed_card_takes_its_article_day_and_signal_cards_their_last_seen_day(tmp_path):
    """2026-10-27 decisions 19 and 26-3: a feed card with no date takes its entry's publication day; a signal the newest
    signals run still shows was last observed that run's day."""
    run = tmp_path / "signals" / "runs" / "20261027T051505Z-abc"
    run.mkdir(parents=True)
    (run / "signals.csv").write_text(
        "source_key,entry_url,entry_title,published_at_utc,quote\n"
        "modelpricewatch_news,https://modelpricewatch.com/models/openai-gpt-5-6-luna/,"
        "GPT-5.6 Luna (OpenAI) — price down: $2.25 → $0.45/Mtok blended,2026-08-01T00:00:00Z,x\n"
        "openrouter_endpoints_glm_5_2,,,,\"DeepInfra (deepinfra/fp4): pricing.discount=0.25; input 0.45 / output 1.44\"\n",
        encoding="utf-8")
    feed = build.feed_days(tmp_path)
    clue = {"sources": [{"quote": "GPT-5.6 Luna (OpenAI) - price down: $2.25 → $0.45/Mtok blended"}],
            "evidence_refs": [{"url": "https://modelpricewatch.com/models/openai-gpt-5-6-luna/", "run_id": "20261016T070332Z-x"}]}
    assert build.article_day(clue, feed) == "2026-08-01"
    discount = {"source_key": "openrouter_endpoints_glm_5_2",
                "sources": [{"source_key": "openrouter_endpoints_glm_5_2",
                             "quote": "DeepInfra (deepinfra/fp4): pricing.discount=0.25; input 0.50 / output 1.60"}],
                "evidence_refs": [{"run_id": "20261016T070332Z-x"}]}
    assert build.seen_day([discount], feed) == "2026-10-27"
    gone = dict(discount, sources=[{"source_key": "openrouter_endpoints_glm_5_2", "quote": "Other (other/fp8): pricing.discount=0.1"}])
    assert build.seen_day([gone], feed) == "2026-10-16"


def test_undated_card_shows_its_basis_in_the_list_and_the_whole_reason_on_hover_and_in_the_detail(tmp_path):
    """2026-10-25 decision 12 wording, 10/27 placement: the 58px list cell shows 관측 (or 게시) over the card's day; the
    whole '적용일 미공지 · M/D 관측' is the cell's tooltip and the detail's date."""
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")])
    _, page, data = _build(tmp_path)
    [card] = data["EVENTS"]
    assert "eff" not in card
    assert (card["effSeen"], card["effShort"]) == ("관측", f"적용일 미공지 · {build.md(card['cd'])} 관측")
    assert '<span class="dl2"${!e.eff && e.effShort ? ` title="${esc(e.effShort)}"` : ""}>' in page
    assert ': e.effSeen ? `<small class="tn">${md(e.cd)}</small>` : ""' in page
    assert 'esc([e.effShort, e.effNote].filter(Boolean).join(" · ") || "미확인")' in page


def test_team_feedback_of_1103_legend_korean_labels_switch_draft_and_search(tmp_path: Path) -> None:
    """2026-11-03 팀 피드백 1~5. The browser check covers behaviour; this pins the page's own words and wiring."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    # 1. no colour legend under the calendar
    assert "callg" not in page and "dkey" not in page and "drvk" not in page
    # 5. the list says what to do and whether LETSUR uses the route in Korean; only the Slack bot's cards.json keeps the English values
    assert '<span class="chip ${k}" title="${QACTION[k].tip}">${QACTION[k].ko}</span>' in page and "${a.ko}${m && a !== APPSTAT.unk" in page
    assert 'en:"' not in page and "${QACTION[actionKey(e)].ko} · 제안: " in page
    for word in ("렛서 사용", "렛서 미사용", "사용 미확인", "관측 유지", "확인 필요"):
        assert f'ko:"{word}"' in page
    # 3. a switch with its state written on it, and a settings button that opens the 공급로 현황 tab without being a tab itself
    assert 'lm.firstElementChild.textContent = letsurOnly ? "ON" : "OFF";' in page and 'lm.setAttribute("aria-checked", letsurOnly);' in page
    assert 'class="lgear" data-ptab="supply" aria-label="공급로 현황에서 렛서 경로 설정"' in page
    assert 'document.querySelectorAll(".ptabs [data-ptab]").forEach(b => b.setAttribute("aria-selected"' in page
    # 4. the routes draft reaches the change page: a model with a draft is judged again, every other model keeps its built value,
    # and a model with every route released gets the 미도입 record relation() reads, not no record at all
    assert "function relOf(e){ const d = relDraft(e); if (d) return d.key; if (e.relKey) return e.relKey;" in page
    assert 'doc.records.push({model_id: m, route_id: null, endpoint_id: null, basis: "manual", checked_at: at});' in page
    assert 'if (p !== "models") { renderMeta(); renderCalendar(); renderQueue(); renderDetail();' in page
    # 2. one search box in the header for all four tabs; spacing and punctuation do not count
    assert 'type="search" id="msq" placeholder="모델 검색"' in page
    assert "@media (max-width:1000px){.top{flex-wrap:wrap;row-gap:8px;padding-block:8px}.top .base{margin-left:auto}.msearch{order:3;width:100%;margin-left:0}}" in page  # the header must not push the page wider
    # the header's right end says when the data was last collected; the tool's name is gone (2026-11-04 박건)
    assert '`최신 업데이트 : ${HEALTH.run ? HEALTH.run.at.replace(/ KST$/, "") : "기록 없음"}`' in page
    assert "모델 가격·조건 모니터" not in page and "(고정)" not in page
    assert 'const searchKey = s => String(s || "").toLowerCase().replace(/[^0-9a-z가-힣]+/g, "");' in page
    for filtered in ('ks.filter(k => modelHit(k, modelName(k)))', 'l.filter(m => modelHit(m, modelName(m)) && spLensHit(D, m))', 'l.filter(x => modelHit(x.model, x.model))', '!isPast(e) && modelHit(e.mid, e.m)'):
        assert filtered in page
    assert 'if (modelQ && l.length && !l.some(e => e.id === state.sel || e.vg === state.sel))' in page


def test_a_model_released_on_the_supply_tab_is_read_as_not_offered() -> None:
    """2026-11-03 팀 피드백 4: the record spDoc() writes when every route of a model is released is a valid record, and relation() reads it
    as 미도입 (hidden by 렛서 경로만), where a model with no record stays unconfirmed (kept by 렛서 경로만)."""
    released = {"model_id": "gemini-4-argon", "route_id": None, "endpoint_id": None, "basis": "manual", "checked_at": "2026-11-03"}
    assert build.record_problem(released) == ""
    routes = {"is_demo": False, "records": [released]}
    for route in ("google_direct", ""):
        assert build.relation({"model_id": "gemini-4-argon", "route": route}, routes) == {"relKey": "none", "relShort": "미도입", "relDemo": False}
    assert build.relation({"model_id": "gemini-4-argon", "route": "google_direct"}, {"is_demo": False, "records": []})["relKey"] == "unk"
    assert build.model_use("gemini-4-argon", routes) == {"use": "미도입"}
    assert "gemini-4-argon" in build.supply(routes)["models"]


def _volatile_card(card_id: str, day: str, rows: list[tuple], latest: list[tuple] = ()) -> dict:
    """rows are (group, item, before, after, delta); a delta of None is a price that stayed."""
    all_rows = [*rows, *latest]
    return {"id": card_id, "cd": day, "chg": [[f"{g} · {i}", b, a, d, f"관측 {int(day[5:7])}/{int(day[8:10])}", "ok", [f"s_{card_id}"]] for g, i, b, a, d in all_rows],
            "cg": [[g, i] for g, i, *_ in all_rows], "ev": [[f"s_{card_id}", f"{card_id} 근거"]],
            "cond": [["통화", "USD"], ["엔드포인트", "relace/fp4"], ["경로", "OpenRouter · openrouter"], ["관측", "지난 값"]]}


def test_a_volatile_bundle_reads_as_one_change_from_the_first_price_to_the_latest() -> None:
    """2026-11-04 박건: a 변동 잦음 detail has the single-change format and a small graph instead of a list of cards. Prices are compared
    first to latest, and a price that came back says so rather than counting as a change."""
    up, down = ["+$0.05", "인상 5.56%"], ["−$0.05", "인하 5.26%"]
    wide = "컨텍스트 1M · relace/fp4 · fp4"
    cards = [_volatile_card("c3", "2026-10-28", [("컨텍스트 1M · siliconflow/fp8 · fp8", "입력 토큰", "$1.50", "$1.00", ["−$0.50", "인하 33.33%"])], [(wide, "캐시 읽기", "$0.25", "$0.25", None)]),
             _volatile_card("c1", "2026-10-17", [(wide, "입력 토큰", "$0.90", "$0.95", up), (wide, "캐시 읽기", "$0.16", "$0.20", ["+$0.04", "인상 25.00%"])]),
             _volatile_card("c2", "2026-10-18", [(wide, "입력 토큰", "$0.95", "$0.90", down), (wide, "캐시 읽기", "$0.20", "$0.25", ["+$0.05", "인상 25.00%"])])]
    s = build.volatile_summary(cards)
    assert (s["from"], s["to"], s["cards"], s["up"], s["down"], s["back"], s["n"], s["others"]) == ("2026-10-17", "2026-10-28", 3, 1, 1, 1, [3, 0], 0)
    rows = {row[0]: row for row in s["chg"]}
    # the supplier that moved most comes first, and a line is named by the supplier alone
    assert list(rows) == ["relace/fp4 · 입력 토큰", "relace/fp4 · 캐시 읽기", "siliconflow/fp8 · 입력 토큰"]
    assert rows["relace/fp4 · 입력 토큰"][1:5] == ["$0.90", "$0.90", ["되돌아감", ""], "2번 · 10/18"]
    assert rows["relace/fp4 · 캐시 읽기"][1:5] == ["$0.16", "$0.25", ["+$0.09", "인상 56.25%"], "2번 · 10/18"]
    assert rows["siliconflow/fp8 · 입력 토큰"][1:5] == ["$1.50", "$1.00", ["−$0.50", "인하 33.33%"], "1번 · 10/28"]
    assert s["cg"][0] == ["relace/fp4", "입력 토큰"]
    assert s["lines"][0] == {"s": "relace/fp4", "item": "입력 토큰", "pts": [["2026-10-17", "$0.90", "$0.95", "c1"], ["2026-10-18", "$0.95", "$0.90", "c2"]]}
    # conditions of one observation do not stand for the whole bundle, and the evidence is the first card's and the latest's
    assert s["cond"] == [["통화", "USD"], ["경로", "OpenRouter · openrouter"], ["관측", "10/17 → 10/28"]]
    assert s["ev"] == [["s_c1", "c1 근거"], ["s_c3", "c3 근거"]]


def test_a_volatile_bundle_draws_only_the_suppliers_that_moved_most() -> None:
    change = ["+$0.10", "인상 10.00%"]
    cards = [_volatile_card(f"c{n}", f"2026-10-{10 + n}", [(f"s{k}/fp8", "입력 토큰", "$1.00", "$1.10", change) for k in range(5) if n <= k]) for n in range(5)]
    s = build.volatile_summary(cards)
    assert [line["s"] for line in s["lines"]] == ["s4/fp8", "s3/fp8", "s2/fp8"] and s["others"] == 2 and len(s["chg"]) == 5


@pytest.mark.parametrize("group,short", [("컨텍스트 1M · relace/fp4 · fp4", "relace/fp4"), ("컨텍스트 1048575 · gmicloud", "gmicloud"),
                                         ("inference-net/fast · fp4 · Priority", "inference-net/fast · fp4 · Priority"), ("morph/fp8 · fp8 · Standard", "morph/fp8"),
                                         ("컨텍스트 1000000 · alibaba/fp8/us-east-1 · fp8", "alibaba/fp8/us-east-1"), ("", "")])
def test_a_price_line_is_named_by_its_supplier(group: str, short: str) -> None:
    assert build.short_supplier(group) == short


def test_template_bundle_detail_uses_the_single_detail_and_next_schedule_is_a_tidy_grid(tmp_path: Path) -> None:
    """2026-11-04 박건. The browser check covers behaviour; this pins the wiring."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert "renderBundle" not in page and "vgnote" not in page
    assert "if (e.vgs) return vgKey(e);" in page and "const c = {...last, id, vgs: s, vg: undefined," in page
    assert 'parts.push(sec("변동 내역", `${VG[e.id].length}건`' in page  # the cards are still there, folded
    assert 'preserveAspectRatio="none"' in page and "vector-effect:non-scaling-stroke" in page  # the graph follows the width without a redraw
    # one row per item, no dots: side by side with dots the lines could not be read (박건); a stretch of the line tells its values on hover
    assert ".vgc-grid{display:flex;flex-direction:column;gap:16px;margin-top:12px}" in page and "vgc-d" not in page
    assert '<span class="vgc-h" data-t1=' in page
    # next schedule: equal cells on the calendar's width, four, then two, then one; only when, what, which model, which supplier
    assert ".cngrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}" in page
    assert "@media (max-width:1100px){.cngrid{grid-template-columns:repeat(2,minmax(0,1fr))}}" in page
    assert "@media (max-width:560px){.cngrid{grid-template-columns:minmax(0,1fr)}}" in page
    assert '${next.length ? `<div class="calnext"><h3>다음 일정</h3><div class="cngrid">${next.map(nextCell).join("")}</div></div>` : ""}' in page


def test_type_menu_under_the_sort_menu_shows_one_type(tmp_path):
    """2026-11-03 팀 피드백 6: the type menu sits under the sort menu instead of a row of chips; the type picked is the only one shown,
    전체 shows all, and its counts apply only the date filter."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert ('<select id="qsort"><option value="recommended">추천 순</option><option value="date">적용일 순</option><option value="type">유형별</option></select></div>'
            '<div><label for="qkind">유형</label><select id="qkind"></select></div>') in page
    assert 'let listKind = "";\nconst kindShown = e => !listKind || e.kind === listKind;' in page
    assert 'menu.innerHTML = `<option value="">전체 ${byDay.length}</option>`' in page and "menu.value = listKind;" in page
    assert 'ev.target.id === "qkind") { listKind = ev.target.value; refilter(); }' in page
    assert "data-kind" not in page and "listOn" not in page


def test_next_step_lines_read_label_colon_text(tmp_path):
    """2026-10-27 박건 request: '확인 : …' and '대응 : …' under 다음 할 일, not '먼저 확인 · …' and '대응 방향 · …'."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert "subs.push(`확인 : ${x}`)" in page and "subs.push(`대응 : ${g.pre" in page
    assert "먼저 확인 · ${x}" not in page and "대응 방향 · ${g.pre" not in page
    # the bold line too, and 현재 사용 경로 (relation() in build.py) the same way
    assert "`현재 경로 유지 : ${e.ch} 새 단가는 비교용으로 기록`" in page and '"이미 쓰는 경로 : 기록만"' in page
    assert build.relation({"model_id": "m", "route": ""}, {"records": [{"model_id": "m", "route_id": "openrouter"}], "is_demo": False})[
        "relShort"] == "경로 표기 없음 : 사용 경로 확인 필요"


def test_a_discount_that_runs_out_into_an_announced_price_links_the_cards():
    """2026-10-27 결정(1번 가안): the announced change and the discounts link to each other; on LETSUR's route the discount
    card gets the price after it as an estimate and a 주의 line, and so does the announced change."""
    cards = [{"id": "up", "mid": "m", "t7": "예정 변경", "eff": "2027-01-01", "sum": "12개 항목 단가 1/1부터 인상 예정"},
             {"id": "vercel", "mid": "m", "t7": "한시 할인", "eff": "2026-12-31", "sum": "50% 할인 · 12/31까지"},
             {"id": "or", "mid": "m", "t7": "한시 할인", "sum": "할인 · 종료일 미확인"},
             {"id": "other", "mid": "n", "t7": "한시 할인", "sum": "x"}]
    groups = {"up": [{"route": "google_direct"}], "vercel": [{"route": "vercel_ai_gateway"}], "or": [{"route": "openrouter"}],
              "other": [{"route": "vercel_ai_gateway"}]}
    promo = [{"route": route, "direct": "google_direct", "start": "2027-01-01", "standard": True, "after": "$1.50 / $7.50 / $0.15",
              "times": "2"} for route in ("openrouter", "vercel_ai_gateway")]
    tables = {"m": {"groups": [], "promo": promo}}
    routes = {"records": [{"model_id": "m", "route_id": "vercel_ai_gateway"}], "is_demo": True}
    build.link_promotions(cards, groups, tables, routes)
    up, vercel, other_route, other = cards
    assert "promo" not in tables["m"]
    assert up["link"] == ["vercel", "50% 할인 · 12/31까지"] and vercel["link"] == other_route["link"] == ["up", "12개 항목 단가 1/1부터 인상 예정"]
    assert up["risk"] == "렛서 가정 경로(Vercel)도 12/31에 할인이 끝나 1/1부터 같은 가격이 될 수 있음(추정)"
    assert vercel["postEst"] == "할인 끝난 뒤 Standard $1.50 / $7.50 / $0.15 (추정 · Google 1/1 가격 기준)"
    assert vercel["risk"] == "1/1부터 지금 가격의 두 배가 될 수 있음(추정)"
    assert "postEst" not in other_route and "risk" not in other_route and "link" not in other


def test_list_rows_lead_with_the_model(tmp_path):
    """2026-10-27 박건 request: each list row says which model it is about before what changed."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert '<b><span class="subj">${logoOf(e.mid)}${esc(e.m)}</span> ${esc(e.sum)}' in page
    assert '${esc(e.m)} · ${esc(e.ch)}</span></span>' not in page


def test_tab_names_are_short_and_the_comparison_line_keeps_the_source_words():
    """2026-10-27 박건 request: long Bedrock and OpenAI wording pushed the tab row out of the panel."""
    def key(**conditions):
        return json.dumps(conditions, sort_keys=True, ensure_ascii=False), "1M tokens"
    low = key(context_band="272K input tokens or fewer", inference_option="Global CRIS", region_group="Commercial Regions",
              service_tier="standard", cache_write_ttl="30m")
    rule = key(long_context_multiplier="Prompts with >272K input tokens are priced at 2x input and 1.5x output for the full request.",
               service_tier="standard")
    assert build.tab_names([low, rule]) == {low: "캐시 30분 · 입력 272K 이하 · Global CRIS · Standard",
                                            rule: "272K 초과 요청 입력 2배·출력 1.5배 · Standard"}
    assert "Commercial Regions" in build.tab_conditions([low])[low]
    assert [build.tab_value("input_token_band", v) for v in ("<=200K", ">200K", "more than 272K input tokens")] == [
        "입력 200K 이하", "입력 200K 초과", "입력 272K 초과"]


def test_template_says_when_a_linked_card_is_not_on_the_page(tmp_path):
    """2026-10-27 decision 26-6: a #card id the page lacks opens the top card with a notice, cleared once a card is chosen."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    # a 변동 잦음 bundle id (#v_…, 2026-10-28 가안) is on the page too
    assert "if (!EVENTS.some(e => e.id === state.sel) && !VG[state.sel]) missingLink = state.sel;" in page
    assert "링크한 카드(#${esc(missingLink)})를 이 페이지에서 찾지 못했습니다." in page
    assert "state.sel = id; missingLink = \"\";" in page


def test_each_feed_entry_gets_its_own_source_chip():
    """2026-10-27 decision 20: one chip per feed entry with its own link; one chip per run showed one notice on every card."""
    def notice(url: str, locator: str) -> dict:
        return {"model_id": "gpt-5.6-sol", "route": "vercel_ai_gateway", "source_key": "vercel_changelog",
                "evidence_refs": [{"source_key": "vercel_changelog", "run_id": "20261016T070332Z-x", "url": url, "locator": locator}]}
    sources = build.Sources()
    first = build.register(notice("https://vercel.com/changelog/a", "feed/entry[1]"), sources)[0][1]
    second = build.register(notice("https://vercel.com/changelog/b", "feed/entry[2]"), sources)[0][1]
    again = build.register(notice("https://vercel.com/changelog/a", "feed/entry[1]"), sources)[0][1]
    entries = sources.entries()
    assert first != second and again == first
    assert entries[first]["url"].endswith("/a") and entries[second]["url"].endswith("/b")


def test_a_tracker_note_joins_the_route_card_of_its_group_only_when_days_apart_at_most_three():
    """2026-10-27 decision 26-3: a route-less note joins its group's one routed card when their days are close; a note the
    engine grouped two months away stays on its own card. The merged card keeps the routed member as representative."""
    def note(eid, day, route="", role="member"):
        return {"event_id": eid, "group_key": "grp_x", "group_role": role, "route": route, "dates": {"published": day}}
    groups = {"routed": [note("v", "2026-07-30", "vercel_ai_gateway")], "near": [note("m", "2026-08-01", role="representative")]}
    build.merge_notes(groups, {})
    assert list(groups) == ["routed"] and [e["event_id"] for e in groups["routed"]] == ["v", "m"]
    assert build.representative(groups["routed"])["event_id"] == "v"
    groups = {"routed": [note("o", "2026-10-17", "openrouter")], "far": [note("m", "2026-08-16", role="representative")]}
    build.merge_notes(groups, {})
    assert set(groups) == {"routed", "far"}


@pytest.mark.parametrize(("card", "action", "applicability"), [
    # an ended discount on the route LETSUR uses: 기록만
    ({"t7": "한시 할인", "st": "confirmed", "ps": "ended", "eff": "2026-09-18", "relKey": "use", "chg": []}, "RECORD", "APPLIES"),
    # a confirmed observed decrease on another route: compare the route
    ({"t7": "가격 변경", "st": "confirmed", "relKey": "other", "chg": [["입력 토큰", "$0.30", "$0.20"]]}, "REVIEW", "NOT APPLIES"),
    # the same increase on another route: keep the current route
    ({"t7": "가격 변경", "st": "confirmed", "relKey": "other", "chg": [["입력 토큰", "$0.20", "$0.30"]]}, "RECORD", "NOT APPLIES"),
    # a note with no values and no route: check first
    ({"t7": "가격 변경", "st": "candidate", "clue": True, "relKey": "unk", "chg": []}, "VERIFY", "UNKNOWN"),
    # a candidate with values on the route LETSUR uses: keep watching
    ({"t7": "가격 변경", "st": "candidate", "relKey": "use", "chg": [["입력 토큰", "$0.30", "$0.20"]]}, "MONITOR", "APPLIES"),
])
def test_cards_judgement_matches_the_pages_rules(card, action, applicability):
    """2026-10-27 decision 26-4: cards.json repeats template.html's actionKey and applicabilityOf for the Slack bot."""
    judged = build.judge(card, "2026-10-27")
    assert (judged["action"], judged["applicability"]) == (action, applicability)


def test_build_writes_cards_json_next_to_the_page(tmp_path):
    _write(tmp_path, [_scheduled("s1", "input", "0.75", "1.50", "0.75")])
    _, _, data = _build(tmp_path, args=["--internal-routes", str(DEMO_ROUTES)])
    doc = json.loads((tmp_path / "site" / "cards.json").read_text(encoding="utf-8"))
    [card] = doc["cards"]
    assert doc["schema"] == 1 and doc["base_day"] == data["TODAY"]
    assert card["id"] == data["EVENTS"][0]["id"] and card["events"] == data["ID"][card["id"]]
    assert card["action"] in ("REVIEW", "VERIFY", "MONITOR", "RECORD") and card["deadline"] == data["EVENTS"][0].get("eff")
    assert doc["names"]["items"]["input"] == "입력 토큰" and doc["names"]["conditions"]["quantization"] == "정밀도"


def test_a_price_series_that_went_back_resolves_the_change(tmp_path: Path) -> None:
    """2026-10-28 (결정 기록 29절 렛서 답변: 원래 가격으로 돌아온 건은 지금도 바뀐 것처럼 보이지 않게). A change whose series,
    read in observation order, last stands at the price it started from is reverted; a card whose changed prices all went
    back is RECORD in cards.json (judge) as in the page (actionKey)."""
    up = _observed("e-up", "price_change", "1.00", "2.00")
    back = _observed("e-back", "price_change", "2.00", "1.00")
    back["dates"]["observed_after_utc"] = "2026-10-06T07:00:00Z"
    again = _observed("e-again", "price_change", "1.00", "2.00")
    again["dates"]["observed_after_utc"] = "2026-10-07T07:00:00Z"
    other = _observed("e-other", "price_change", "3.00", "4.00", item="output")
    assert build.reverted([up, back, other]) == {"e-up"}  # the last change of a series is never reverted
    assert build.reverted([up, back, again]) == {"e-back"}  # up's price is in effect again; back's is not
    _write(tmp_path, [up, back])
    _, page, data = _build(tmp_path)
    cards = {data["ID"][card["id"]][0]: card for card in data["EVENTS"]}
    assert (cards["e-up"].get("rv"), cards["e-up"].get("rn")) == (1, 1) and "rv" not in cards["e-back"]
    judged = {card["events"][0]: card for card in json.loads((tmp_path / "site" / "cards.json").read_text(encoding="utf-8"))["cards"]}
    assert judged["e-up"]["action"] == "RECORD" and judged["e-back"]["action"] != "RECORD"
    assert "const isReverted = e => !!e.rv && e.rv === e.rn;" in page and 'if (isReverted(e)) return "record";' in page


def test_health_reads_the_newest_run_and_the_failure_gaps(tmp_path: Path) -> None:
    """수집 상태 (2026-10-28): failing now comes from the newest run (the gaps' active flag is stale), the streak from the
    source_failed gaps of the runs before it; earlier failures say whether the source is fine again or left the registry."""
    runs = ["20261020T050000Z-a", "20261021T050000Z-b", "20261022T050000Z-c"]
    folder = tmp_path / "root" / "runs" / runs[-1]
    folder.mkdir(parents=True)
    with (folder / "sources.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["run_id", "source_key", "model_id", "route", "requested_url", "fetch_status", "parse_status", "error"])
        writer.writeheader()
        writer.writerow({"run_id": runs[-1], "source_key": "down", "model_id": "m1", "route": "openrouter", "requested_url": "https://x.example/a",
                         "fetch_status": "ok", "parse_status": "failed", "error": "ValueError: layout changed"})
        for key in ("fine", "back"):
            writer.writerow({"run_id": runs[-1], "source_key": key, "model_id": "m0", "route": "openrouter", "requested_url": "",
                             "fetch_status": "ok", "parse_status": "ok", "error": ""})
    gaps = [{"kind": "source_failed", "source_key": "down", "run_id": runs[1], "model_id": "m1", "route": "openrouter", "error": "x", "active": True},
            {"kind": "source_failed", "source_key": "back", "run_id": runs[0], "model_id": "m2", "route": "openrouter", "error": "old", "active": True},
            {"kind": "source_failed", "source_key": "gone", "run_id": runs[1], "model_id": "m3", "route": "fal", "error": "gone err", "active": True},
            {"kind": "parser_fingerprint_mismatch", "source_key": "fine", "billing_item": "cache_read", "after_observation_id": f"{runs[2]}-s001-p0001"},
            {"kind": "parser_fingerprint_mismatch", "source_key": "fine", "billing_item": "input", "after_observation_id": f"{runs[1]}-s001-p0002"}]
    health = build.health(tmp_path / "root", {"status": "ok", "processed_runs": runs}, {"processed_runs": runs, "gaps": gaps}, "2026-10-22")
    assert (health["total"], health["ok"], health["run"]["id"]) == (3, 2, runs[-1])
    # 2026-11-06: failRuns and failDays count the source's failed runs of the last 14 days, this one included
    assert health["failing"] == [{"key": "down", "model": "m1", "route": "openrouter", "ch": build.route_label("openrouter"),
                                  "error": "ValueError: layout changed", "since": "2026-10-21", "sinceRun": runs[1], "runs": 2,
                                  "kind": "format", "label": "수집기 수정 필요", "action": "원문을 열어 바뀐 형식에 맞게 수집기를 고치세요", "fix": True,
                                  "url": "https://x.example/a", "failRuns": 2, "failDays": ["2026-10-21", "2026-10-22"]}]
    assert [(r["key"], r["now"], r["first"], r["last"], r["runs"]) for r in health["recent"]] == [
        ("gone", "gone", "2026-10-21", "2026-10-21", 1), ("back", "ok", "2026-10-20", "2026-10-20", 1)]
    # 2026-11-06: the day the earliest unchecked price was recorded and which items, in Korean, in the order first seen
    assert health["recheck"] == [{"key": "fine", "model": "m0", "ch": build.route_label("openrouter"), "n": 2, "since": "2026-10-21", "items": ["캐시 읽기", "입력"]}]


def test_health_judges_each_failure_and_puts_the_ones_to_act_on_first(tmp_path: Path) -> None:
    """수집 상태 (2026-10-29 박건: 수집기를 고쳐야 하는 실패를 가려 알린다): kind, 판단, action and fix per failing source; a
    temporary error needs action only from its second failed run on; the sources to act on come first, then the file order."""
    runs = ["20261020T050000Z-a", "20261021T050000Z-b", "20261022T050000Z-c"]
    folder = tmp_path / "root" / "runs" / runs[-1]
    folder.mkdir(parents=True)
    failures = [("blip", "error", "not_run", "", "TimeoutError: The read operation timed out"),  # first failed run
                ("slow", "error", "not_run", "", "TimeoutError: The read operation timed out"),  # second in a row
                ("busy", "http_error", "not_run", "503", ""),
                ("moved", "http_error", "not_run", "404", ""),
                ("layout", "ok", "error", "200", "ValueError: n prose/schema constraints disagree"),
                ("fine", "ok", "ok", "200", "")]
    with (folder / "sources.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["run_id", "source_key", "model_id", "route", "requested_url", "http_status", "fetch_status", "parse_status", "error"])
        writer.writeheader()
        for key, fetch, parse, http, error in failures:
            writer.writerow({"run_id": runs[-1], "source_key": key, "model_id": "m", "route": "openrouter", "requested_url": "",
                             "http_status": http, "fetch_status": fetch, "parse_status": parse, "error": error})
    gaps = [{"kind": "source_failed", "source_key": "slow", "run_id": runs[1], "model_id": "m", "route": "openrouter", "error": "x", "active": True}]
    health = build.health(tmp_path / "root", {"status": "ok", "processed_runs": runs}, {"processed_runs": runs, "gaps": gaps}, "2026-10-22")
    assert [(f["key"], f["kind"], f["label"], f["runs"], f["fix"]) for f in health["failing"]] == [
        ("slow", "temporary", "일시 오류", 2, True), ("moved", "access", "접근 문제", 1, True), ("layout", "format", "수집기 수정 필요", 1, True),
        ("blip", "temporary", "일시 오류", 1, False), ("busy", "temporary", "일시 오류", 1, False)]
    actions = {f["key"]: f["action"] for f in health["failing"]}
    assert actions["blip"] == "다음 수집에서 다시 시도합니다. 2회 연속이면 출처를 확인하세요"
    assert actions["slow"] == "연속으로 실패했습니다. 출처가 열리는지, 주소나 접근이 바뀌었는지 확인하세요"
    assert {f["key"]: f["error"] for f in health["failing"]}["moved"] == "HTTP 404"  # the status when the source gave no error text


def test_cards_json_carries_usage_check_links_and_failing_sources() -> None:
    """2026-10-28 (결정 기록 29절 렛서 답변: 알림에 현재 이용 중인 경로인지, 근거 링크와 추가 확인 사항)."""
    data = {"TODAY": "2026-10-28", "ID": {"g1": ["e1"]}, "SRC": {"s1": {"url": "https://src.example/p"}, "s2": {"url": "http://plain.example"}},
            "EVENTS": [{"id": "g1", "t7": "가격 변경", "relKey": "use", "relShort": "이 경로로 사용 중 (가정)", "m": "m", "ch": "c",
                        "chk": [["first", "렛서 계정에서 확인"]], "ev": [["s1", "q"], ["s2", "q"], ["s1", "q2"]], "chg": []}],
            "HEALTH": {"run": {"id": "r", "at": "t"}, "failing": [{"key": "k", "model": "m", "route": "r", "ch": "R", "error": "e", "since": "2026-10-28",
                                                                  "sinceRun": "r1", "runs": 1, "kind": "format", "label": "수집기 수정 필요",
                                                                  "action": "원문을 열어 바뀐 형식에 맞게 수집기를 고치세요", "fix": True,
                                                                  "url": "https://u.example"}]}}
    judged = build.judgements(data, {"g1": []})
    card = judged["cards"][0]
    assert (card["usage"], card["check"], card["links"]) == ("이 경로로 사용 중 (가정)", "렛서 계정에서 확인", ["https://src.example/p"])
    assert judged["health"]["failing"] == [{"key": "k", "model": "m", "route": "r", "ch": "R", "error": "e", "since": "2026-10-28",
                                            "sinceRun": "r1", "url": "https://u.example", "runs": 1, "kind": "format", "label": "수집기 수정 필요",
                                            "action": "원문을 열어 바뀐 형식에 맞게 수집기를 고치세요", "fix": True}]


def test_template_has_the_health_screen(tmp_path: Path) -> None:
    """수집 상태 화면 (2026-10-28 박건 제안): a third header tab, #health opens it, failing sources show on 변화 too."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert '<button type="button" role="tab" data-ptab="health" aria-selected="false">수집 상태</button>' in page
    assert 'if (h === "health") startHealth = true;' in page and "function renderHealth(){" in page
    assert re.search(r"^const HEALTH = \{.*\};$", page, re.M) and 'body:not([data-page="health"]) .hpage{display:none}' in page
    assert '<button type="button" data-ptab="health">수집 상태 보기 →</button>' in page
    # 2026-10-29: each failing source carries its 판단 and the section says how many sources need action
    assert '`<span class="chip ${tone(x)}">${esc(x.label)}</span>' in page and "수집기 확인 필요 ${todo}곳" in page


def test_the_health_screen_has_item_tabs_and_folds_repeated_rows(tmp_path: Path) -> None:
    """2026-11-06 박건 '수집 상태 탭의 가독성이 조금 안좋아보이는데 ... 세부 알림 항목별로 페이지 내부에 탭을 만드는 건 어때?', then '숫자 칸 탭으로'
    and '숫자를 강조하지 말고, 뭐에 대한 항목인지를 강조해줘': four tabs named by what they hold (name 14·650 ink, count 12 under it,
    two by two on a phone), the failing sources open first and the 수집 상태 shortcuts outside the header open them again, rows instead of
    tables (the long source address only behind '원문 열기'), and repeated rows folded into one: past failures with the same route, error
    and state whatever the day (the daily Azure price API failures become one row), registry candidates with the same change, date and
    route (11/5 data: 30 → 7 and 23 → 11 rows). The browser check covers the groups, the model filter, the links, the dark theme and 375px."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert 'let healthTab = "now";' in page and '<div class="htabs" role="tablist" aria-label="수집 상태 항목">' in page
    for name in ('"지금 실패 중인 출처"', "`최근 ${H.days || 14}일 실패 기록`", '"원문 재검증 필요"', '"등록부 점검 후보"'):
        assert name in page
    assert ".htabs b{font-size:14px;font-weight:650;line-height:1.3;color:var(--ink)}" in page
    assert ".htabs span{font-size:12px;line-height:1.3;color:var(--body)}" in page
    assert "@media (max-width:640px){.htabs{grid-template-columns:repeat(2,minmax(0,1fr))}}" in page
    assert 'if (pg.dataset.ptab === "health" && !pg.closest(".ptabs")) healthTab = "now";' in page
    assert 'const ht = ev.target.closest("[data-htab]"); if (ht) { healthShow(ht.dataset.htab); return; }' in page
    assert 'groupBy(H.recent, x => [x.ch, x.error, x.now].join("\\t"))' in page
    assert 'e => [EV7[e.id], EV7SUB[e.id] || "", e.eff || e.effShort || "", e.ch].join("\\t")).sort((a, b) => cmpU(a[0], b[0]))' in page
    assert ">원문 열기</a>" in page and 'class="hsec"' not in page and 'table(["출처"' not in page
    # 2026-11-06 박건 '각 탭의 이런 메시지 없애줘': the rows come right under the tabs, with no explanatory sentence above them
    assert '${panel("now", fail)}${panel("past", recent)}${panel("recheck", recheck)}${panel("registry", regRows)}' in page
    assert 'class="hmore"' not in page and "최근 수집에서 받지 못했거나 읽지 못한 출처입니다" not in page
    # 2026-11-06 박건 '저 위에 문구를 점으로 나누지 않고, 상세페이지처럼 칩으로': the summary and the failing count are big chips
    assert '<div class="hsum"><span class="chip big">마지막 수집 ' in page and '<span class="chip big ok">정상 ${H.ok}곳</span>' in page
    assert '<div class="hcount"><span class="chip big${todo ? " bad" : ""}">수집기 확인 필요 ${todo}곳</span>' in page
    assert ".hsum{display:flex;flex-wrap:wrap;gap:4px;margin:8px 0 0}" in page and "정상 ${H.ok}곳 · 실패" not in page


def test_health_rows_say_what_they_are_about_and_label_each_fact(tmp_path: Path) -> None:
    """2026-11-06 박건 '분류한 것 자체는 좋은데 각 페이지에서 보여주는 각 항목에서 주는 정보의 가독성이 좀 떨어지는 것 같아', then '전부 만들면 되는데':
    each row puts what it is about in bold (model, supplier or change) and its facts under named labels (원인·기간·할 일·실패·모델·가격·내용·
    등록부), with the date on the left where the date matters and the route id and source key in small text at the bottom. The error
    text gets a plain reading beside it (the 판단 stays core.failures'), a failing source says how often it failed in the last 14 days,
    the unchecked prices say which items and since when, grouped by route and day, and a registry candidate says what to do in the
    registry from whether the page collects that route's price now (SUPPLY.models). The browser check covers the five readings of
    the 11/5 errors, the Azure row, the registry actions (20 to remove after the date, 3 with nothing to do), 375px and the dark theme."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert "const dl = pairs => `<dl class=\"hdl\">${pairs.filter(p => p[1]).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join(\"\")}</dl>`;" in page
    assert ".hdl{display:grid;grid-template-columns:48px minmax(0,1fr);gap:4px 8px;margin:8px 0 0;font-size:13px;line-height:1.5}" in page
    assert '.hrows>li.hd{grid-template-columns:72px minmax(0,1fr) auto;grid-template-areas:"d m s"}' in page
    assert ".htitle b{font-size:14px;font-weight:650;color:var(--ink)}" in page
    # plain readings of the error text, the raw text kept beside it
    assert 'const causeOf = err => !err ? "오류 내용이 남지 않음" : (CAUSES.find(([re]) => re.test(err)) || [null, "원인 미상"])[1];' in page
    for reading in ("요청이 많아 잠시 거절됨", "가격표 주소가 다른 사이트로 옮겨짐", "응답 시간 초과", "페이지에서 항목을 찾지 못함", "페이지 형식이 바뀌어 값을 읽지 못함"):
        assert f'"{reading}"' in page
    for label in ('["원인", cause(x.error)]', '["할 일", esc(x.action)]', '["등록부", regTodo(l)]', '["모델", names(l)]'):
        assert label in page
    assert "최근 ${H.days || 14}일 동안 ${x.failRuns}번째" in page
    assert 'groupBy(H.recheck, x => [x.ch, x.since || ""].join("\\t"))' in page and "기록 ${l.reduce((a, y) => a + y.n, 0)}건" in page
    assert "const kept = e => ((SUPPLY.models || {})[e.mid] || []).includes(e.rt);" in page
    assert '"가격표에서 빠지면 출처 빼기"' in page and '"이 경로 가격은 모으지 않아 할 일 없음"' in page


def test_the_header_logo_is_a_little_smaller(tmp_path: Path) -> None:
    """2026-11-06 박건 '좌측 상단 LETSUR 아이콘 크기 좀만 줄이자': 19px → 16px high, its size hints to match (the image is 186×24)."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert ".top img{height:16px;width:auto;display:block}" in page and 'alt="LETSUR" width="124" height="16">' in page


def test_the_supply_tab_opens_with_a_summary_of_letsur_routes(tmp_path: Path) -> None:
    """2026-11-05 박건 '이런식의 뷰는 못 만드는거야? 저런걸 공급로 현황 탭에 넣으면 좋을 것 같긴 한데' (박기범's draft PR #40 keeps the same card on the
    home screen): three states, suppliers as a donut and a list, companies as a list. It counts models, never usage, from the same columns
    and the same draft as the table under it, and a pressed row filters that table. The browser check covers the numbers against a separate
    count from the page data, the filter, the draft, the dark theme and 375px."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    # the address #supply lands on the top of the tab, not on the table under the summary
    assert '<section class="spage" id="supply" aria-label="공급로 현황">\n    <div class="card" id="spsum" hidden></div>\n    <div class="card" id="sptable"></div>' in page
    # the same columns and draft as the table, and no card without a routes file
    assert "const spUses = (D, m) => SUPPLY.cols.filter(c => spCell(D, m, c).length).map(c => c[0]);" in page
    assert 'sumHtml = spSummary(D, false); sum.innerHTML = sumHtml; sum.hidden = !sumHtml;' in page and 'if (!ids.length || !SUPPLY.routes) return "";' in page
    # 변화 counts what the list shows for a route LETSUR uses: recent or coming, not yet handled
    assert 'EVENTS.filter(e => !isPast(e) && !isAcked(e)).forEach(e => { const r = relOf(e), c = SUPPLY.cols.find(x => x[1].includes(e.rt)); if (r === "use" && c) on[c[0]]' in page
    # colour and order follow the supplier's name, so a change in counts repaints nothing and keeps the validated neighbours
    assert 'const SUM_HUE = ["OpenRouter", "Vercel", "Microsoft", "Google", "OpenAI", "Anthropic"];' in page
    assert "sort((a, b) => hue(a) - hue(b) || count[b] - count[a] || a.localeCompare(b))" in page
    for words in ("렛서 공급 경로", "대체 경로 없는 모델", "경로 기록 없음", "수집 실패", "공급사별 렛서 사용 모델", "회사별 렛서 경로 수", '숫자는 모델 수(사용량 아님) · 행을 누르면 ${home ? "캘린더·목록을" : "아래 표를"} 거름'):
        assert words in page
    # a pressed row or state filters the table, again or '전체 보기' clears it, and the row keeps the focus
    assert "modelHit(m, modelName(m)) && spLensHit(D, m)" in page and 'data-sp="lens" data-lk="off">전체 보기</button>' in page
    assert 'spLens = t.dataset.lk === "off" || (spLens && spLens.k === t.dataset.lk && spLens.v === t.dataset.lv) ? null :' in page
    # text wears text tokens, and the new rules bring no colour of their own
    css = page[page.index(".spage{display:flex"):page.index(".spsnote{")]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css) and ".spv.solo{font-weight:650;color:var(--amber)}" in css and ".spv.dual{font-weight:650;color:var(--down-ink)}" in css


def test_the_solo_route_switch_sits_beside_letsur_only(tmp_path: Path) -> None:
    """2026-11-05 박건 '렛서 경로만 쓰는 토글이 있으니까 그 옆에 대체 경로 없는 모델 토글을 붙이면' (박기범's draft PR #40 has it as a chip on the
    calendar): it keeps the changes of a route LETSUR uses for a model on that route alone, across the summary, the calendar and the list,
    counted from the same columns and draft as the 공급로 현황 table. It starts off and is not saved. The browser check covers the
    numbers against a separate count, the draft and the widths."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    # left of the other switch, in the same shape, so the gear ends the row (박건 '저 톱니바퀴 버튼이 맨 오른쪽이면 좋겠어'); the pair stays at the right edge, also on two lines
    assert 'data-t1="대체 경로 없는 모델만"><span>OFF</span></button></div>' in page and '<label for="ssw">대체 경로 없는 모델만</label><button type="button" id="ssw" class="lswitch" role="switch" data-single aria-checked="false"' in page
    assert page.index('<div class="lmodes">') < page.index('id="ssw"') < page.index('id="lsw"') < page.index('class="lgear"') and ".lmodes{display:flex;flex-wrap:wrap;justify-content:flex-end;" in page
    # on a phone each pair of words and switch takes its own line from the left, and the gear stays at the right end
    phone = page[page.index("@media (max-width:480px){\n  .lmodes{"):]
    phone = phone[:phone.index("\n}\n")]
    assert ".lmodes{position:relative;flex-direction:column;align-items:flex-start;" in phone and ".lgear{position:absolute;top:50%;right:0" in phone
    # a change of a route LETSUR uses, for a model with one supplier column; the draft empties the count
    assert 'const singleRoute = e => relOf(e) === "use" && spUseN(e.mid) === 1;' in page and "[m, spUses(D, m).length]" in page and "spUseCount = null; spSave(); renderSupply();" in page
    # it filters like 렛서 경로만, starts off, and its names and count follow
    assert "let singleOnly = false" in page and "singleOnly = !singleOnly; renderMeta(); renderCalendar(); refilter();" in page
    assert 'singleOnly ? "대체 경로 없는 모델 변화" : letsurOnly ? "렛서 경로 변화" : "전체 변화"' in page and 'singleOnly ? "대체 경로 없는 모델의 " : letsurOnly ? "렛서 경로의 " : ""' in page
    assert 'so.firstElementChild.textContent = singleOnly ? "ON" : "OFF";' in page and "한 곳은 경로 파일 기록 기준이라 아직 못 본 경로가 더 있을 수 있습니다." in page


def test_the_changes_tab_has_the_same_route_card_and_its_rows_filter_the_changes(tmp_path: Path) -> None:
    """2026-11-05 박건 '변화 탭에도 공급로 현황 그래프를 똑같이', '공급사 줄의 빨간 표시도 반영해줘' (박기범's draft PR #40): one renderer draws the card in both
    places. On the changes tab a pressed row filters the summary, the calendar and the list, and a supplier row leaves exactly what its 변화
    number counts. The browser check covers the numbers against a separate count, the failure mark on both tabs and the widths."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert '<div id="cal"></div>\n    </div>\n    <div class="card" id="hsum" hidden></div>' in page
    assert "function spSummary(D, home){" in page and "spSummary(spDoc() || {records: []}, true)" in page and "renderHsum();\n" in page
    # the rows filter what the page lists; a handled change or the draft redraws the numbers
    assert "(!singleOnly || singleRoute(e)) && chLensHit(e) && modelHit(e.mid, e.m)" in page and "const ackSave = () => { renderHsum();" in page
    assert 'const c = SUPPLY.cols.find(x => x[1].includes(e.rt));\n  return relOf(e) === "use" && !!c && names.includes(c[0]);' in page
    assert 'if (chLens.k === "maker") return names.includes(makerOf(e.mid || ""));' in page and 'if (chLens.k === "none") return relOf(e) === "unk" && !spUseN(e.mid);' in page
    assert 'const cl = ev.target.closest("[data-cl]"); if (cl) { chAct(cl, ev.detail === 0); return; }' in page
    assert 'k === "solo" ? singleOnly :' in page and 'if (k === "solo") singleOnly = !singleOnly;' in page  # that state is the switch above it
    assert '<span>${esc(chLens.l)}만 · 목록 ${listed().length}건</span><button type="button" class="mchip" data-cl="off">전체 보기</button>' in page
    # a pattern in a hidden tab paints nothing, so each card has its own
    assert 'hatchId = home ? "hsumhatch" : "sphatch"' in page and '<pattern id="${hatchId}"' in page
    # a supplier with a failing source says so in its row and is not folded away
    assert ".spnm.bad{font-weight:650;color:var(--up-ink)}" in page and '<span class="spnm${r.fail ? " bad" : ""}">${esc(r.l)}${r.fail ? " !" : ""}</span>' in page
    assert "count[g] === 1 && !on[g] && !failCol[g]" in page and "if (c) failCol[c[0]] = (failCol[c[0]] || 0) + 1;" in page
    # beside the calendar on a wide screen, under it below 1200px, and on a phone only on the supply tab
    assert ".hero.withsum{grid-template-columns:minmax(0,1fr) minmax(280px,340px);align-items:start}" in page and "@media (max-width:1200px){.hero.withsum{grid-template-columns:minmax(0,1fr)}}" in page
    assert "@media (max-width:640px){#hsum{display:none}}" in page and "@media (min-width:1201px){\n  #hsum .spstats>*{padding:8px}" in page


def test_template_css_keeps_to_the_design_scales() -> None:
    """docs/design-system.md (2026-10-30): corners are role tokens (or 0 and 50%), font sizes the eight-step scale, and
    padding, margin and gap the 4px grid (2px for the narrowest gaps)."""
    css = Path(build.__file__).with_name("template.html").read_text(encoding="utf-8")
    css = "\n".join(line for line in css[css.index("<style>"):css.index("</style>")].splitlines() if not line.startswith("@font-face"))
    token = r"(0|50%|var\(--r-(xs|s|m|l|xl|pill)\))"
    assert [r for r in re.findall(r"border-radius:([^;}]+)", css) if not re.fullmatch(rf"{token}( {token})*", r.strip())] == []
    assert set(re.findall(r"font-size:(\d+(?:\.\d+)?)px", css)) <= {"11", "12", "13", "14", "15", "18", "22", "26"}
    spacing = re.findall(r"(?:^|[;{\s])(?:padding|margin|gap|row-gap|column-gap)(?:-[a-z-]+)?:([^;}]+)", css)
    assert sorted({v for s in spacing for v in re.findall(r"(-?\d+(?:\.\d+)?)px", s) if abs(float(v)) not in (0, 2) and abs(float(v)) % 4}) == []


def test_team_feedback_icons_calendar_words_title_and_letsur_mode(tmp_path: Path) -> None:
    """2026-10-31 팀 상호 피드백 1~4 (박건 '네, 1~4 반영'): the company icon also stands before the model name in the change list,
    the detail, the calendar, 다음 일정 and 수집 상태 and follows the text colour; a calendar chip names the change ('할인 종료', not
    '종료'); the big page title is for screen readers only; '렛서 경로만' hides the cards on routes LETSUR does not use from the
    summary, the calendar and the list. The browser check covers behaviour."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    # icons: one class of their own (the old .mk status marks are round) that takes the text colour, then every model name
    assert '`<svg class="logo" aria-hidden="true"><use href="#mk-${MAKER_ICONS[g]}"/></svg>`' in page
    assert ".logo{width:14px;height:14px;flex:none;fill:currentColor}" in page and 'class="mk"' not in page
    assert '<span class="subj">${logoOf(e0.mid)}${esc(e0.m)}</span>' in page and '<span class="who">${logoOf(e.mid)}${esc(e.m)}</span>' in page
    assert '${calWord(x)}</span>${logoOf(e.mid)}<span>${esc(c[0])}</span>' in page and '${calWord(x)}</span></span><span class="cn2">${logoOf(x.e.mid)}<b>${esc(x.e.m)}</b>' in page
    assert 'who = x => `<b>${logoOf(x.model)}${esc(x.model || "모델 표기 없음")}</b>' in page
    # calendar chips, 다음 일정 and the tooltip use the change's name; the colour still follows the date kind
    assert "DATE_KIND[x.kind]}</span>" not in page and "${md(x.date)}(${wd(x.date)}) · ${calWord(x)} · ${x.label}" in page
    for word in ("공급 중단", "경로 추가", "할인 종료", "기존가 종료", "인상 적용", "인하 적용"):
        assert f'"{word}"' in page
    assert 'return x.kind === "next_check" ? `${w} 확인` : w;' in page
    # the title is still the page's heading for screen readers
    assert '<h1 id="ptitle">변화</h1>' in page and ".title h1{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)" in page
    assert "확인할 변화" not in page
    # 렛서 경로만: off when the page opens, keeps 렛서 경로를 모르는 cards, filters the summary, the calendar and the list
    assert 'class="lswitch" role="switch" data-lmode aria-checked="false"' in page and "let letsurOnly = false;" in page
    assert 'const letsurRoute = e => ["use", "unk"].includes(relOf(e));' in page and "const modeShown = e => (!letsurOnly || letsurRoute(e)) && (!singleOnly || singleRoute(e)) && chLensHit(e) && modelHit(e.mid, e.m);" in page
    assert "const shown = EVENTS.filter(modeShown)" in page and "const entries = CAL_ENTRIES.filter(x => modeShown(x.e));" in page
    assert "const listed = () => EVENTS.filter(e => modeShown(e) && kindShown(e) && dayShown(e));" in page
    assert "letsurOnly = !letsurOnly; renderMeta(); renderCalendar(); refilter();" in page


def test_the_detail_head_and_every_display_chip_share_one_shape(tmp_path: Path) -> None:
    """2026-11-04 박건 '첫줄에 모델명, 공급 경로를 두고 그 아랫줄에 나머지를 넣고 크기와 디자인 성격을 통일', then '칩 통일부터': the first
    line of a detail says what the card is about and the second holds its chips. Everything that only shows a state is one .chip, 20px
    (24px in the detail head), radius 6, no border, and differs only in colour; what can be pressed is a pill, so the shape tells them
    apart. The browser check covers the sizes, the contrast in light and dark, and 375px."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert ('<div class="dident"><span class="who">${logoOf(e.mid)}${esc(e.m)}</span><span>${esc(e.ch)}</span></div>\n'
            '    <div class="dmeta">${typeHead(e)}${costChip(net)}<span class="chip big">${e.real ? "실제 · 공개 출처" : "시연 데이터"}</span>') in page
    css = page[page.index("<style>"):page.index("</style>")]
    shape = re.search(r"\.chip\{([^}]+)\}", css).group(1)
    assert all(rule in shape for rule in ("min-height:20px", "font-size:11px", "font-weight:650", "border-radius:var(--r-s)")) and "border:" not in shape
    assert ".chip.big{min-height:24px;padding:2px 8px;font-size:12px;white-space:normal}" in css
    # a colour variant changes the colours and nothing else
    colours = re.findall(r"^\.chip\.[a-z_-]+(?:,\.chip\.[a-z0-9_-]+)*\{([^}]+)\}", css, re.M)
    colours = [c for c in colours if "color:" in c and "min-height" not in c and "margin" not in c]
    assert len(colours) == 8 and all(set(re.findall(r"([a-z-]+):", re.sub(r"(color-mix\([^)]*\)|var\([^)]*\))", "", c))) <= {"background", "color"} for c in colours)
    for old in ('class="qchip', 'class="qs', 'class="dd', 'class="tg', 'class="hst', 'class="stag', "sct-tag", 'class="dc', 'class="qt"', 'class="ref', 'class="pill', "cchip"):
        assert old not in page
    # what can be pressed stays a pill
    for pressable in (".mchip{", ".tlink{", ".seg{", ".spbar button,.spbar a{"):
        assert re.search(rf"^{re.escape(pressable)}[^}}]*border-radius:var\(--r-pill\)", css, re.M)
    # 인상·인하 text is mixed with --ink: --up and --down alone were 3.7:1 on their fill; chips stay visible on a mint background
    assert "--up-ink:color-mix(in srgb, var(--up) 72%, var(--ink))" in css and ".chip.down{background:color-mix(in srgb, var(--down) 14%, var(--surface));color:var(--down-ink)}" in css
    assert '.row[aria-current="true"] .chip:not(.now),.decide .next .chip{background:var(--surface)}' in css
    # .unknown is the page's "nothing here" line, which made the old 사용 미확인 chip 13px with a 12px top margin
    assert 'cls:"unk"}' in page and ".chip.unk" in css and 'cls:"unknown"' not in page
    # .row .t>span turned .qbadges into a block and took away the 4px between chips
    assert ".row .t>.qbadges{display:flex;overflow:visible}" in css
    # inside a calendar bar the change's name is coloured text: the bar is the box
    assert ".dkind{flex:none;font-size:11px;font-weight:650}" in css and '<span class="dkind ${x.kind}">${calWord(x)}</span>${logoOf(e.mid)}' in page


def test_a_card_can_be_marked_handled_and_comes_back_when_it_changes(tmp_path: Path) -> None:
    """2026-11-04 박건: 대응이 끝났거나 필요 없다고 본 알림이 목록을 가리니 '확인 처리'로 숨기고, '확인한 알림'으로 다시 본다. The page has no server, so
    this browser keeps it. A card id survives collections but a card can gain changes under it, so a card whose item count, status or date
    moved comes back. A 변동 잦음 bundle would then be back almost daily, so it keeps its prices at the check (박건 '10% 이상일 때만', '중간 것들의
    변화도 인지는 해야'): LETSUR's own row or an item's lowest price moving 10% puts it back in the list; any other row moving 10% leaves it hidden
    and shows on the 확인한 알림 button, its row and its detail. Any-row would bring back 7 or 8 of 9 bundles within a day, this rule 1 or 2. The list
    and 다음 일정 hide a handled card (박건 '빼줘': it shouldn't hold one of the four cells); the calendar and the summary keep it. The browser check
    covers behaviour."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert 'const ackSig = e => [e.sum, e.st, e.eff].join("|");' in page
    assert "const isAcked = e => e.vg ? !!acked[e.vg] && !vgMajor(e.vg, acked[e.vg]) : acked[e.id] === ackSig(e);" in page
    assert "const vgPrices = id => Object.fromEntries(VGS[id].chg.map(r => [r[0], r[2]]));" in page
    # 10% either way; a row that wasn't there at the check against its first price; only prices are compared
    assert "const tenPct = (a, b) => a !== null && b !== null && a !== b && (!a || Math.abs(b - a) / a >= .1 - 1e-9);" in page
    assert "const vgDrift = (id, was) => VGS[id].chg.filter(r => tenPct(money(wasPrice(r, was)), money(r[2])));" in page
    # back in the list: an item's lowest price or LETSUR's own row; hidden with a mark: any other row
    assert "return Object.keys(now).some(k => k in low && tenPct(low[k], now[k])) || vgDrift(id, was).some(r => mine.includes(" in page
    assert 'box.querySelector(".ackn").textContent = moved ? `새 변동 ${moved}` : "";' in page
    assert "체크 뒤 ${drift}줄 변동" in page and "체크한 뒤 10% 이상 달라진 줄 ${d.length}개" in page and "${BACK_CHIP}" not in page and "BACK_CHIP" in page
    assert 'acked[ag.dataset.ackAgain] = vgPrices(ag.dataset.ackAgain); ackedAt[ag.dataset.ackAgain] = new Date().toISOString(); ackSave(); renderCalendar(); renderQueue(); renderDetail();' in page
    assert "acked[id] = VG[id] ? vgPrices(id) : ackSig(e); ackedAt[id] = new Date().toISOString();" in page
    # 2026-11-04 박건 '언제 처리했는지 처리 시간도 기록': shown in KST on handled rows and in the detail; a check saved before this has no time and keeps working
    assert "delete acked[id]; delete ackedAt[id];" in page and 'const ACK_KEY = "letsur-acked", ACK_AT_KEY = "letsur-acked-at";' in page
    assert "const ackWhen = id => { const iso = new Date(Date.parse(ackedAt[id]) + 9 * 36e5).toISOString();" in page
    assert "const ackChip = (id, big) => ackedAt[id] ? " in page and "${ackWhen(id)} 확인</span>" in page
    assert "${!e.vg && acked[e.id] ? (isAcked(e) ? ackChip(e.id) : BACK_CHIP) : \"\"}" in page and "? ackChip(e.id, true) : \"\"}</div>" in page
    assert "const dayShown = e => ackOnly ? isAcked(e) : !isAcked(e) && (showPast || !isPast(e));" in page
    assert "const listed = () => EVENTS.filter(e => modeShown(e) && kindShown(e) && dayShown(e));" in page
    # saved in this browser only, and the page runs without it
    assert 'try { acked = JSON.parse(localStorage.getItem(ACK_KEY) || "{}") || {}; ackedAt = JSON.parse(localStorage.getItem(ACK_AT_KEY) || "{}") || {}; } catch (err) {}' in page
    assert "try { localStorage.setItem(ACK_KEY, JSON.stringify(acked)); localStorage.setItem(ACK_AT_KEY, JSON.stringify(ackedAt)); } catch (err) {}" in page
    # the check sits beside the row's button, never inside it, and only rows of the change list have it
    assert "return `<li>${vg ? vgRow(x) : queueRow(x)}${ackBox(vg ? x.vg : x.id)}</li>`;" in page
    # 2026-11-06 박건 'B로 하는게 나을 것 같은데 ... 체크박스 크기가 체크여부에 따라 달라지는게 별로야': one 28px round check, no text,
    # the same size before and after; the real checkbox lies over it, invisible, so clicks, the keyboard and screen readers still use it
    assert '<label class="ack" title="${on ? "확인함" : "확인 처리"}"><input type="checkbox" class="spx" data-ack-id="${esc(id)}"' in page
    assert "확인 처리</label>" not in page  # the 28px target itself is checked below
    assert ".ack .spx{position:absolute;inset:0;width:100%;height:100%;margin:0;opacity:0;cursor:pointer}" in page
    assert ".ack .spx:checked+.ackic{border-color:var(--mint-ink);background:var(--mint-ink);color:var(--surface)}" in page
    # the same day '원말고 모서리가 둥근 네모': a square with the --r-s corners rather than a circle
    assert "border:1px solid var(--line-strong);border-radius:var(--r-s);background:var(--surface);color:var(--muted);pointer-events:none}" in page
    # then '클릭되는 범위는 그대로 두되, 체크 표시가 시각적으로 보이는건 좀 줄여': the box shows at the chips' 20px in the middle of the 28px target
    assert ".ack{position:absolute;top:8px;right:8px;display:flex;align-items:center;justify-content:center;width:28px;height:28px;cursor:pointer}" in page
    assert "justify-content:center;width:20px;height:20px;border:1px solid var(--line-strong)" in page and ".ack .ackic svg{width:12px;height:12px}" in page
    assert "#queue .row .t>.qbadges{padding-right:40px}" in page
    # (2026-11-06: 수집 상태 folds its registry rows and opens a card from a model button, so only the detail still lists plain rows)
    assert "const plainRow = e => `<li>${queueRow(e)}</li>`;" in page and ".map(queueRow)" not in page and page.count(".map(plainRow)") == 1
    # the filter sits beside 지난 변화 보기 and shows every handled card, past or not
    assert '<button type="button" class="mchip" data-past>지난 변화 보기</button><button type="button" class="mchip" data-ack>확인한 알림 <small></small><small class="ackn"></small></button>' in page
    assert 'if (ab) { ackOnly = !ackOnly; refilter(); return; }' in page and "showPast = !showPast; ackOnly = false;" in page
    # 다음 일정 leaves out a handled card; the calendar and the summary don't use the list's filter
    assert "const next = entries.filter(x => days(x.date) >= 0 && !isAcked(x.e)).sort(" in page
    assert "const entries = CAL_ENTRIES.filter(x => modeShown(x.e));" in page and "const shown = EVENTS.filter(modeShown)" in page
    # checking the card being read moves on to the next row and keeps the list where it was
    assert "if (state.sel === id && nextId) select(nextId); else { renderCalendar(); renderQueue(); }\n  q.scrollTop = y;" in page
