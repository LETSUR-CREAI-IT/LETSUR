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
SLOTS = ("SRC", "ID", "EVENTS", "MODELS", "TREND", "COLLECTED")
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
    """2026-10-28: the minimax upstream's end on Vercel is one supplier of the route, not 경로 중단. LETSUR's routes file says
    Vercel for minimax-m2, m2.1 and m2.5 and OpenRouter for minimax-m2.7."""
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
    assert [item["applicability"] for item in judged] == ["APPLIES", "APPLIES", "APPLIES", "NOT APPLIES"]
    assert judged[0]["action"] == "MONITOR" and cards["minimax-m2.7"]["relShort"].startswith("모델 제공 중 : OpenRouter")


def test_a_route_end_with_a_whole_route_member_keeps_the_route_wording(tmp_path: Path) -> None:
    whole = _claim("r1", "route_change", "vercel_changelog", "MiniMax M2 will be removed from AI Gateway on November 18, 2026.",
                   status="confirmed", model_id="minimax-m2", route="vercel_ai_gateway", subtags=["remove"], claim_kind="route_removal",
                   dates={"effective_from": "2026-11-18", "published": "2026-10-20", "note": ""}, group_key="g1", group_role="representative")
    upstream = dict(_upstream_end("v1", "minimax-m2"), group_key="g1", group_role="member")
    _write(tmp_path, [whole, upstream])
    [card] = _build(tmp_path)[2]["EVENTS"]
    assert (card["sub"], card["sum"], card["effLabel"]) == ("경로 중단", "경로 중단 11/18", "경로 중단일")
    assert build.upstreams([upstream, dict(upstream, endpoint_id="bedrock")]) == ["minimax", "bedrock"]


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
    assert "if (VG[state.sel]) { renderBundle(state.sel); return; }" in page
    # two screens: the tabs live in the header, body[data-page] picks what shows, #m/<model> opens 모델별 가격
    assert '<button type="button" role="tab" data-ptab="changes" aria-selected="true">변화</button>' in page
    assert 'body:not([data-page="models"]) .mpage{display:none}' in page and 'if (h.startsWith("m/")) startModel' in page
    assert '<section class="card trend" id="trend" aria-label="가격 추이"></section>' in page and 'id="ptable"' in page
    assert 'setPage("models", tr.dataset.trend, tr.dataset.tview || "chart");' in page
    # models grouped by maker (from the id), makers and models in name order (the table is written in company-name order too)
    companies = [company for _, company in _makers(page)]
    assert companies == sorted(companies, key=str.lower)
    assert "modelName(a).localeCompare(modelName(b), undefined, {numeric: true})" in page


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
    (Google direct, OpenRouter, Vercel; 2026-10-28 박건 결정), so a Google direct change applies, marked as LETSUR settings."""
    _write(tmp_path, [_scheduled("e1", "input", "0.75", "1.50", "0.75")])
    _, _, data = _build(tmp_path, args=["--internal-routes", str(DEMO_ROUTES)])
    [card] = data["EVENTS"]
    assert (card["relKey"], card["relShort"], card["relDemo"], card["relSet"]) == ("use", "이 경로로 사용 중 (렛서 설정)", False, True)
    assert data["MODELS"] == {"gemini-3.8-flash": {"prov": "Google", "kind": "LLM",
                                                   "use": "Google · google_direct, OpenRouter · openrouter, Vercel · vercel_ai_gateway 경로로 사용 (렛서 설정)"}}


@pytest.mark.parametrize(("records", "relation"), [
    ([_record("google_direct", basis="settings"), _record("vercel_ai_gateway", basis="settings")],
     ("use", "이 경로로 사용 중 (렛서 설정)", False, True)),
    ([_record("vercel_ai_gateway", basis="settings")],
     ("other", "모델 제공 중 : Vercel · vercel_ai_gateway 경로로 사용 (렛서 설정)", False, True)),
    # one record without basis keeps the whole model an assumption
    ([_record("google_direct", basis="settings"), _record("vercel_ai_gateway")], ("use", "이 경로로 사용 중 (가정)", True, False)),
    ([_record("google_direct", basis="observed"), _record("vercel_ai_gateway")], ("use", "이 경로로 사용 중 (가정)", True, False)),
])
def test_registered_routes_are_marked_as_letsur_settings(tmp_path: Path, records: list, relation: tuple) -> None:
    """2026-10-28 박건 결정: records with basis settings are LETSUR's registered routes (9/23 QnA answer), not assumptions."""
    _write(tmp_path, [_scheduled("e1", "input", "0.75", "1.50", "0.75")])
    _, _, data = _build(tmp_path, args=_routes(tmp_path, {"is_demo": True, "records": records}))
    [card] = data["EVENTS"]
    assert (card["relKey"], card["relShort"], card["relDemo"], card.get("relSet", False)) == relation
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'e.relSet ? {chip: "설정", note: "렛서 설정 경로, 9/23 답변 · 실제 트래픽 미확인"}' in page


def _astra_records() -> dict:
    """gpt-6-astra as the committed file has it: a registered route (settings) and the route one API call went through."""
    return {"is_demo": False, "records": [_record("openai_direct", model_id="gpt-6-astra", basis="settings"),
                                          _record("azure_foundry", model_id="gpt-6-astra", basis="observed")]}


@pytest.mark.parametrize(("route", "relation"), [
    ("azure_foundry", ("use", "이 경로로 사용 중 (호출 확인)", False, False, True)),  # its own record: observed (relObs, no relSet)
    ("openai_direct", ("use", "이 경로로 사용 중 (렛서 설정)", False, True, False)),  # its own record: settings
    ("vercel_ai_gateway", ("other", "모델 제공 중 : OpenAI · openai_direct, Microsoft · azure_foundry 경로로 사용 (렛서 설정·호출 확인)", False, False, False)),
])
def test_a_route_with_a_record_shows_that_records_mark(route: str, relation: tuple) -> None:
    """2026-10-28 박건 결정: a card on a route with a record takes that record's mark (settings: 렛서 설정, observed: 호출 확인);
    a card on any other route takes the mark of all the model's records, both when they differ. relation is relKey, relShort,
    relDemo, relSet and relObs: each flag is there for exactly its own basis, so a mix has neither."""
    found = build.relation({"model_id": "gpt-6-astra", "route": route}, _astra_records())
    assert (found["relKey"], found["relShort"], found["relDemo"], found.get("relSet", False), found.get("relObs", False)) == relation
    assert build.model_use("gpt-6-astra", _astra_records()) == {
        "use": "OpenAI · openai_direct, Microsoft · azure_foundry 경로로 사용 (렛서 설정·호출 확인)"}


@pytest.mark.parametrize(("records", "relation"), [
    ([_record("openrouter", basis="settings"), _record("vercel_ai_gateway", basis="settings")],
     ("other", "모델 제공 중 : OpenRouter · openrouter, Vercel · vercel_ai_gateway 경로로 사용 (렛서 설정)", True, False)),
    ([_record("openrouter", basis="observed"), _record("vercel_ai_gateway", basis="observed")],
     ("other", "모델 제공 중 : OpenRouter · openrouter, Vercel · vercel_ai_gateway 경로로 사용 (호출 확인)", False, True)),
    ([_record("openrouter", basis="settings"), _record("vercel_ai_gateway", basis="observed")],
     ("other", "모델 제공 중 : OpenRouter · openrouter, Vercel · vercel_ai_gateway 경로로 사용 (렛서 설정·호출 확인)", False, False)),
    ([_record(None, basis="observed")], ("none", "미도입 (호출 확인)", False, True)),  # not adopted: the model level too
])
def test_a_route_without_a_record_shows_the_mark_of_all_the_models_records(records: list, relation: tuple) -> None:
    """relation is relKey, relShort, relSet and relObs (the 설정 and 호출 확인 chips: only when every record of the model has that basis)."""
    found = build.relation({"model_id": "gemini-3.8-flash", "route": "google_direct"}, {"is_demo": False, "records": records})
    assert (found["relKey"], found["relShort"], found["relDemo"], "relSet" in found, "relObs" in found) == (
        relation[0], relation[1], False, relation[2], relation[3])


def test_observed_is_a_valid_basis_and_any_other_is_not() -> None:
    for basis in (None, "settings", "observed"):
        assert build.record_problem(_record("google_direct", basis=basis)) == ""
    for basis in ("confirmed", "Observed", "", 1):
        assert build.record_problem(_record("google_direct", basis=basis)) == "basis가 settings·observed나 null이 아님"


def test_committed_routes_mark_each_route_and_leave_glm_52_unknown(tmp_path: Path) -> None:
    """2026-10-28: the committed file holds LETSUR's registered routes (settings) and the route one API call per model went
    through (observed) and no assumption, so each record says which; glm-5.2's route could not be told and has no record."""
    doc = json.loads(DEMO_ROUTES.read_text(encoding="utf-8"))
    assert doc["is_demo"] is False and {record.get("basis") for record in doc["records"]} == {"settings", "observed"}
    assert not any(record["model_id"] == "glm-5.2" for record in doc["records"])
    cards = [("gpt-6-astra", "azure_foundry"), ("gpt-6-astra", "openai_direct"), ("gpt-6-astra", "vercel_ai_gateway"),
             ("gpt-5.6-sol", "azure_foundry"), ("claude-opus-5-5", "anthropic_direct"), ("claude-opus-5-5", "vercel_ai_gateway"),
             ("claude-fable-5-1", "anthropic_direct"), ("glm-5.2", "vercel_ai_gateway")]
    _write(tmp_path, [_observed(f"e{n}", "price_change", "1.00", "2.00", model_id=model, route=route) for n, (model, route) in enumerate(cards)])
    _, _, data = _build(tmp_path, args=["--internal-routes", str(DEMO_ROUTES)])
    assert data["STATUS"] == ""
    found = {(card["m"], card["ch"].split(" · ")[-1]): card for card in data["EVENTS"]}
    assert {key: (card["relKey"], card["relShort"], card["relDemo"], card.get("relSet", False), card.get("relObs", False))
            for key, card in found.items()} == {
        ("gpt-6-astra", "azure_foundry"): ("use", "이 경로로 사용 중 (호출 확인)", False, False, True),
        ("gpt-6-astra", "openai_direct"): ("use", "이 경로로 사용 중 (렛서 설정)", False, True, False),
        ("gpt-6-astra", "vercel_ai_gateway"): ("other", "모델 제공 중 : OpenAI · openai_direct, Microsoft · azure_foundry 경로로 사용 (렛서 설정·호출 확인)", False, False, False),
        ("gpt-5.6-sol", "azure_foundry"): ("use", "이 경로로 사용 중 (호출 확인)", False, False, True),
        ("claude-opus-5-5", "anthropic_direct"): ("use", "이 경로로 사용 중 (호출 확인)", False, False, True),
        ("claude-opus-5-5", "vercel_ai_gateway"): ("other", "모델 제공 중 : Anthropic · anthropic_direct 경로로 사용 (호출 확인)", False, False, True),
        ("claude-fable-5-1", "anthropic_direct"): ("use", "이 경로로 사용 중 (렛서 설정)", False, True, False),
        ("glm-5.2", "vercel_ai_gateway"): ("unk", "사용 경로 확인 필요", False, False, False),
    }
    assert data["MODELS"]["gpt-6-astra"]["use"] == "OpenAI · openai_direct, Microsoft · azure_foundry 경로로 사용 (렛서 설정·호출 확인)"
    assert data["MODELS"]["claude-opus-5-5"]["use"] == "Anthropic · anthropic_direct 경로로 사용 (호출 확인)"
    assert "use" not in data["MODELS"]["glm-5.2"]
    judged = {tuple(card["events"]): card for card in json.loads((tmp_path / "site" / "cards.json").read_text(encoding="utf-8"))["cards"]}
    azure = judged[("e0",)]  # what the Slack bot writes after 렛서 사용 :
    assert (azure["usage"], azure["applicability"]) == ("이 경로로 사용 중 (호출 확인)", "APPLIES")


GATEWAY_MODELS = {  # the 34 models one LETSUR API call on 2026-10-28 reached through OpenRouter or Vercel, by the gateway it went through
    "openrouter": ("deepseek-v4-flash-0731 deepseek-v4-pro-0813 glm-4.5 glm-4.5-air glm-4.6 glm-5-turbo grok-4.20 grok-4.6 kimi-k2-thinking "
                   "kimi-k2.6 kimi-k3.1-pro minimax-m2.7 muse-spark-1.2 qwen3-coder-flash qwen3.7-flash qwen3.5-plus").split(),
    "vercel_ai_gateway": ("deepseek-v4-flash deepseek-v4.1-flash-1017 gemini-2.5-flash-lite glm-5.1 grok-4.3 kimi-k2.5 kimi-k2.7-code minimax-m2 "
                          "minimax-m2.1 minimax-m2.5 minimax-m3 muse-spark-1.3 muse-wonder-1.0 qwen3-coder-next qwen3.6-plus qwen3.7-max "
                          "qwen3.7-plus qwen3.8-max").split(),
}


def test_committed_routes_hold_one_observed_record_for_each_of_the_34_gateway_models() -> None:
    """2026-10-28: 25 records became 59; each added model has one record, observed, on the gateway its call went through.
    (The 46 direct-route records added afterwards are checked below.)"""
    records = json.loads(DEMO_ROUTES.read_text(encoding="utf-8"))["records"]
    bases = [record.get("basis") for record in records]
    assert (len(records), bases.count("settings"), bases.count("observed")) == (105, 10, 95)
    assert not any(build.record_problem(record) for record in records)
    expected = sorted((model, route) for route, models in GATEWAY_MODELS.items() for model in models)
    assert len(expected) == 34 and [len(models) for models in GATEWAY_MODELS.values()] == [16, 18]
    added = [record for record in records if record["model_id"] in dict(expected)]
    assert sorted((record["model_id"], record["route_id"]) for record in added) == expected  # one each: a repeated model would show twice
    assert all(record["endpoint_id"] is None and record["basis"] == "observed" for record in added)


DIRECT_MODELS = {  # the 46 models one LETSUR API call on 2026-10-28 reached through Azure or a maker's own API, by route
    "azure_foundry": ("gpt-4.1 gpt-4.1-mini gpt-4.1-nano gpt-4o gpt-4o-mini gpt-5 gpt-5-codex gpt-5-mini gpt-5-nano gpt-5.1 gpt-5.1-codex "
                      "gpt-5.1-codex-mini gpt-5.2 gpt-5.4 gpt-5.4-2026-03-05 gpt-5.4-mini gpt-5.4-nano gpt-5.5 o1 o3 o3-mini o3-pro o4-mini").split(),
    "google_direct": ("gemini-2.5-flash gemini-2.5-pro gemini-3-flash-preview gemini-3.1-flash-lite gemini-3.1-pro-preview gemini-3.5-flash "
                      "gemini-3.5-flash-lite gemini-3.6-flash gemini-3.7-flash gemini-4-argon").split(),
    "anthropic_direct": ("claude-fable-5 claude-haiku-5-5 claude-opus-4-5-20251101 claude-opus-4-6 claude-opus-4-7 claude-opus-4-8 "
                         "claude-opus-5 claude-sonnet-4-5-20250929 claude-sonnet-4-6").split(),
    "openai_direct": "gpt-5.2-pro gpt-5.5-pro gpt-5.5-pro-2026-04-23 gpt-6.1-terra".split(),
}


def test_committed_routes_hold_one_observed_record_for_each_of_the_46_direct_route_models() -> None:
    """2026-10-28 (박건 '70개도 모두 보도록'): the calls that went to Azure, Google, Anthropic or OpenAI directly are recorded
    too. Calls whose route was only inferred (gpt-5.6, gpt-5.5-2026-04-23) or not found (GLM, embeddings) have no record."""
    records = json.loads(DEMO_ROUTES.read_text(encoding="utf-8"))["records"]
    expected = sorted((model, route) for route, models in DIRECT_MODELS.items() for model in models)
    assert [len(models) for models in DIRECT_MODELS.values()] == [23, 10, 9, 4]
    added = [record for record in records if record["model_id"] in dict(expected)]
    assert sorted((record["model_id"], record["route_id"]) for record in added) == expected
    assert all(record["endpoint_id"] is None and record["basis"] == "observed" and not build.record_problem(record) for record in added)
    unknown = {"gpt-5.6", "gpt-5.5-2026-04-23", "glm-4.7", "glm-5", "glm-5.2", "glm-5.3", "glm-5.3-flashx",
               "text-embedding-3-large", "text-embedding-3-small", "text-embedding-ada-002"}
    assert not unknown & {record["model_id"] for record in records}


def test_a_card_on_another_route_than_the_observed_one_says_which_route_is() -> None:
    """qwen3.5-plus went through OpenRouter, which has no endpoint listing under that id, so its Vercel card reads 'another
    route' and does not claim the Vercel route is the one in use; deepseek-v4-flash went through Vercel."""
    doc = json.loads(DEMO_ROUTES.read_text(encoding="utf-8"))

    def card(model: str, route: str) -> tuple:
        found = build.relation({"model_id": model, "route": route}, doc)
        return found["relKey"], found["relShort"], found.get("relObs", False)
    assert card("qwen3.5-plus", "vercel_ai_gateway") == ("other", "모델 제공 중 : OpenRouter · openrouter 경로로 사용 (호출 확인)", True)
    assert card("deepseek-v4-flash", "vercel_ai_gateway") == ("use", "이 경로로 사용 중 (호출 확인)", True)
    assert card("deepseek-v4-flash", "openrouter") == ("other", "모델 제공 중 : Vercel · vercel_ai_gateway 경로로 사용 (호출 확인)", True)
    assert build.model_use("qwen3.5-plus", doc) == {"use": "OpenRouter · openrouter 경로로 사용 (호출 확인)"}


def test_model_list_groups_the_gateway_models_under_their_makers_and_stays_a_box_of_its_own_height(tmp_path: Path) -> None:
    """2026-10-28: grok, minimax, muse and qwen models get xAI, MiniMax, Meta and Alibaba in the 모델별 가격 list instead of 기타;
    every model of the committed routes file has a company, and no prefix hides a later one (the first match wins)."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    makers = _makers(page)
    assert dict(makers) == {"qwen": "Alibaba", "claude": "Anthropic", "seedance": "ByteDance", "byteplus-seedance": "ByteDance",
                            "deepseek": "DeepSeek", "gemini": "Google", "kling": "Kling AI", "muse": "Meta", "minimax": "MiniMax",
                            "kimi": "Moonshot AI", "gpt": "OpenAI", "o1": "OpenAI", "o3": "OpenAI", "o4": "OpenAI",
                            "text-embedding": "OpenAI", "grok": "xAI", "glm": "Z.AI"}
    assert all(_maker_of(makers, f"{prefix}-x") == company for prefix, company in makers)
    assert [_maker_of(makers, model) for model in ("grok-4.20", "minimax-m2.5", "muse-wonder-1.0", "qwen3.8-max")] == ["xAI", "MiniMax", "Meta", "Alibaba"]
    models = {record["model_id"] for record in json.loads(DEMO_ROUTES.read_text(encoding="utf-8"))["records"]}
    assert [model for model in sorted(models) if _maker_of(makers, model) == "기타"] == []
    # about 55 models: the sticky list scrolls inside itself (a taller one pushes the chart off screen), and so does the phone's list
    assert ".mlist{padding:10px;position:sticky;top:12px;max-height:calc(100vh - 24px);overflow-y:auto}" in page
    assert ".mlist{position:static;display:flex;flex-wrap:wrap;gap:4px;max-height:max(240px,40vh)}" in page


@pytest.mark.parametrize(("records", "risk"), [
    ([{"model_id": "m", "route_id": "vercel_ai_gateway", "basis": "observed"}],
     "렛서 호출 확인 경로(Vercel)도 12/31에 할인이 끝나 1/1부터 같은 가격이 될 수 있음(추정)"),
    # OpenRouter is the first of the model's routes the change reaches: its own basis, not both of the model's
    ([{"model_id": "m", "route_id": "vercel_ai_gateway", "basis": "settings"}, {"model_id": "m", "route_id": "openrouter", "basis": "observed"}],
     "렛서 호출 확인 경로(OpenRouter)도 12/31에 할인이 끝나 1/1부터 같은 가격이 될 수 있음(추정)"),
    ([{"model_id": "m", "route_id": "vercel_ai_gateway", "basis": "settings"}],
     "렛서 설정 경로(Vercel)도 12/31에 할인이 끝나 1/1부터 같은 가격이 될 수 있음(추정)"),
])
def test_the_discount_warning_names_the_basis_of_the_route_it_is_about(records: list, risk: str) -> None:
    cards = [{"id": "up", "mid": "m", "t7": "예정 변경", "eff": "2027-01-01", "sum": "12개 항목 단가 1/1부터 인상 예정"},
             {"id": "vercel", "mid": "m", "t7": "한시 할인", "eff": "2026-12-31", "sum": "50% 할인 · 12/31까지"},
             {"id": "or", "mid": "m", "t7": "한시 할인", "sum": "할인 · 종료일 미확인"}]
    groups = {"up": [{"route": "google_direct"}], "vercel": [{"route": "vercel_ai_gateway"}], "or": [{"route": "openrouter"}]}
    promo = [{"route": route, "direct": "google_direct", "start": "2027-01-01", "standard": True, "after": "$1.50 / $7.50 / $0.15",
              "times": "2"} for route in ("openrouter", "vercel_ai_gateway")]
    build.link_promotions(cards, groups, {"m": {"groups": [], "promo": promo}}, {"records": records, "is_demo": False})
    assert cards[0]["risk"] == risk


def test_template_marks_a_called_route_like_a_registered_one(tmp_path: Path) -> None:
    """2026-10-28: relObs (the route one API call went through) gets the same badge chip, 현재 사용 경로 tag and 제안 근거 note as
    relSet, and the models table header no longer calls the routes a demo assumption (relDemo keeps its own wording)."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert 'e.relObs ? {chip: "호출 확인", note: "10/28 렛서 API 호출 1회로 확인한 경로 · 다른 경로도 쓸 수 있음"}' in page
    assert 'e.relObs ? " (렛서 관계는 10/28 렛서 API 호출로 확인한 경로 기준)"' in page
    assert '<span class="tg" title="10/28 렛서 API 호출 1회로 확인한 경로 · 다른 경로도 쓸 수 있음">호출 확인</span>' in page
    assert "<span>렛서 사용 경로는 9/23 설정 답변과 10/28 호출 확인 기준</span>" in page and "렛서 사용 여부는 시연용 가정" not in page
    assert 'e.relDemo ? {chip: "가정", note: "시연용 가정"}' in page and '" (렛서 관계는 시연용 가정)"' in page


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
    ({"is_demo": True, "records": [_record("google_direct", basis="confirmed")]}, "routes.json 형식이 다름 (records 1번째 basis가 settings·observed나 null이 아님)"),
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


def test_a_different_service_tier_still_gets_its_own_tab(tmp_path: Path) -> None:
    state = {"series": {"a": _gateway("g1-2", "openrouter", OPENROUTER, "Alibaba", "0.20", **ALIBABA),
                        "n": _gateway("n-2", "openrouter", OPENROUTER, "Novita", "0.25", **NOVITA),
                        "p": _gateway("p-2", "openrouter", OPENROUTER, "Alibaba", "0.60", service_tier="priority", **ALIBABA)}}
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")], state=state)
    tiers = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]["tiers"]
    assert {tab: [line["name"] for line in lines] for tab, lines in tiers.items()} == {
        "fp8 · US": ["OpenRouter · openrouter · alibaba/fp8/us-east-1"], "fp4 · Standard": ["OpenRouter · openrouter · novita/fp4"],
        "fp8 · US · Priority": ["OpenRouter · openrouter · alibaba/fp8/us-east-1"]}



def test_only_unsettled_lines_are_reference_lines(tmp_path: Path) -> None:
    """2026-10-27 decision 26-2: endpoint lines compare inside their tab; a model-level display price states no precision or
    region, so it is a reference line in every tab of its tier and its own tab goes; each tab names its unit."""
    state = {"series": {"a": _gateway("g1-2", "openrouter", OPENROUTER, "Alibaba", "0.20", **ALIBABA),
                        "p": _gateway("p-2", "openrouter", OPENROUTER, "Alibaba", "0.60", service_tier="priority", **ALIBABA),
                        "v": _gateway("v-2", "vercel", "vercel_models", "Vercel", "0.15", price_basis="displayed model quote")}}
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")], state=state)
    trend = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]
    tiers = trend["tiers"]
    assert set(tiers) == {"fp8 · US", "fp8 · US · Priority"}
    assert [bool(line.get("ref")) for line in tiers["fp8 · US"]] == [False, True]
    assert not any(line.get("ref") for line in tiers["fp8 · US · Priority"])
    assert trend["units"] == {"fp8 · US": "USD / 100만 토큰", "fp8 · US · Priority": "USD / 100만 토큰"}


def test_status_lines_follow_the_comparison_table(tmp_path: Path) -> None:
    """Decisions 8 and 26-2: the 공급사 비교 표 compares OpenRouter's -1 and -2 with a chip and leaves -3, -5 and -10 out, so
    only those are reference lines here."""
    state = {"series": {"a": _gateway("g1-2", "openrouter", OPENROUTER, "Alibaba", "0.20", **ALIBABA),
                        "b": _gateway("b-2", "openrouter", OPENROUTER, "Baidu", "0.25",
                                      **{**ALIBABA, "endpoint_tag": "baidu/fp8/us-east-1", "endpoint_status": "-2"}),
                        "c": _gateway("c-2", "openrouter", OPENROUTER, "Crusoe", "0.15",
                                      **{**ALIBABA, "endpoint_tag": "crusoe/fp8/us-east-1", "endpoint_status": "-5"})}}
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")], state=state)
    lines = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]["series"]  # one tab (fp8 · US), so no tiers
    assert {line["name"].rsplit(" · ", 1)[-1]: bool(line.get("ref")) for line in lines} == {
        "alibaba/fp8/us-east-1": False, "baidu/fp8/us-east-1": False, "crusoe/fp8/us-east-1": True}


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


def test_type_chips_show_only_the_picked_types(tmp_path):
    """2026-10-27 박건 request: a pressed type chip shows only its type (more chips add types); none pressed shows all."""
    _write(tmp_path, [])
    page = _build(tmp_path)[1]
    assert "const kindShown = e => !listOn.size || listOn.has(e.kind);" in page
    assert "listOn.has(k) ? listOn.delete(k) : listOn.add(k);" in page
    assert 'b.setAttribute("aria-pressed", listOn.has(b.dataset.kind));' in page
    assert "listOff" not in page


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
    assert '<b><span class="subj">${esc(e.m)}</span> ${esc(e.sum)}' in page
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
            {"kind": "parser_fingerprint_mismatch", "source_key": "fine"}, {"kind": "parser_fingerprint_mismatch", "source_key": "fine"}]
    health = build.health(tmp_path / "root", {"status": "ok", "processed_runs": runs}, {"processed_runs": runs, "gaps": gaps}, "2026-10-22")
    assert (health["total"], health["ok"], health["run"]["id"]) == (3, 2, runs[-1])
    assert health["failing"] == [{"key": "down", "model": "m1", "route": "openrouter", "ch": build.route_label("openrouter"),
                                  "error": "ValueError: layout changed", "since": "2026-10-21", "sinceRun": runs[1], "runs": 2,
                                  "kind": "format", "label": "수집기 수정 필요", "action": "원문을 열어 바뀐 형식에 맞게 수집기를 고치세요", "fix": True,
                                  "url": "https://x.example/a"}]
    assert [(r["key"], r["now"], r["first"], r["last"], r["runs"]) for r in health["recent"]] == [
        ("gone", "gone", "2026-10-21", "2026-10-21", 1), ("back", "ok", "2026-10-20", "2026-10-20", 1)]
    assert health["recheck"] == [{"key": "fine", "model": "m0", "ch": build.route_label("openrouter"), "n": 2}]


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
    # 2026-10-29: the failing table has a 판단 column and the section says how many sources need action
    assert 'table(["출처", "판단", "오류", "실패"]' in page and "수집기 확인 필요 ${todo}곳" in page
