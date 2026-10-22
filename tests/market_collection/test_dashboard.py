"""Dashboard page: events engine output filled into the data slots of the 사용자뷰 1011 template.

Events start from the engine's own base_event and carry the fields each kind of
event has in events.json. The built page's data slots are read back as JSON.
"""

from __future__ import annotations

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
     ("use", "이 경로로 사용 중 · endpoint ep-1 (가정)", True)),
    ([_record("vercel_ai_gateway", endpoint_id=None), _record("openrouter")],
     ("other", "모델 제공 중 · Vercel · vercel_ai_gateway, OpenRouter · openrouter 경로로 사용 (가정)", True)),
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
    assert (card["relKey"], card["relShort"], card["relDemo"]) == ("other", "모델 제공 중 · Vercel · vercel_ai_gateway 경로로 사용", False)
    assert data["MODELS"]["gemini-3.8-flash"]["use"] == "Vercel · vercel_ai_gateway 경로로 사용"


def test_committed_demo_routes(tmp_path: Path) -> None:
    """The file the workflow passes: the demo assumes LETSUR takes Gemini 3.8 Flash through Vercel."""
    _write(tmp_path, [_scheduled("e1", "input", "0.75", "1.50", "0.75")])
    _, _, data = _build(tmp_path, args=["--internal-routes", str(DEMO_ROUTES)])
    [card] = data["EVENTS"]
    assert (card["relKey"], card["relShort"], card["relDemo"]) == ("other", "모델 제공 중 · Vercel · vercel_ai_gateway 경로로 사용 (가정)", True)
    assert data["MODELS"] == {"gemini-3.8-flash": {"prov": "Google", "kind": "LLM", "use": "Vercel · vercel_ai_gateway 경로로 사용 (가정)"}}


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


def test_endpoints_of_one_price_condition_are_lines_of_one_tab(tmp_path: Path) -> None:
    state = {"series": {"a": _gateway("g1-2", "openrouter", OPENROUTER, "Alibaba", "0.20", **ALIBABA),
                        "n": _gateway("n-2", "openrouter", OPENROUTER, "Novita", "0.25", **NOVITA),
                        "d": _gateway("d-2", "vercel_ai_gateway", VERCEL, "DeepInfra", "0.30", **DEEPINFRA)}}
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")], state=state)
    trend = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]
    assert "tiers" not in trend
    assert [(line["name"], [pt[1] for pt in line["pts"]]) for line in trend["series"]] == [
        ("OpenRouter · openrouter · alibaba/fp8/us-east-1", [[0.3], [0.2]]),
        ("OpenRouter · openrouter · novita/fp4", [[0.25]]),
        ("Vercel · vercel_ai_gateway · DeepInfra · fp8", [[0.3]])]
    assert "연결할 선이 여럿이라" not in trend["note"]


def test_a_different_service_tier_still_gets_its_own_tab(tmp_path: Path) -> None:
    state = {"series": {"a": _gateway("g1-2", "openrouter", OPENROUTER, "Alibaba", "0.20", **ALIBABA),
                        "n": _gateway("n-2", "openrouter", OPENROUTER, "Novita", "0.25", **NOVITA),
                        "p": _gateway("p-2", "openrouter", OPENROUTER, "Alibaba", "0.60", service_tier="priority", **ALIBABA)}}
    _write(tmp_path, [_gateway_change("g1", ALIBABA, "0.30", "0.20")], state=state)
    tiers = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]["tiers"]
    assert {tab: [line["name"] for line in lines] for tab, lines in tiers.items()} == {
        "Standard": ["OpenRouter · openrouter · alibaba/fp8/us-east-1", "OpenRouter · openrouter · novita/fp4"],
        "Priority": ["OpenRouter · openrouter · alibaba/fp8/us-east-1"]}


def test_lines_over_the_limit_say_how_many_were_left_out(tmp_path: Path) -> None:
    endpoints = {f"p{n}": {**NOVITA, "endpoint_tag": f"p{n}/fp8", "quantization": "fp8"} for n in range(8)}
    state = {"series": {tag: _gateway(f"{tag}-2" if tag != "p7" else "g1-2", "openrouter", OPENROUTER, tag, "0.20", **endpoint)
                        for tag, endpoint in endpoints.items()}}
    _write(tmp_path, [_gateway_change("g1", endpoints["p7"], "0.30", "0.20")], state=state)
    trend = _build(tmp_path)[2]["TREND"]["deepseek-v4-pro"]
    assert [line["name"] for line in trend["series"]] == [f"OpenRouter · openrouter · p{n}/fp8" for n in (0, 1, 2, 3, 4, 7)]  # the changed endpoint stays
    assert ("선이 6개를 넘어 뺀 선 2개: Standard: OpenRouter · openrouter · p5/fp8, Standard: OpenRouter · openrouter · p6/fp8."
            in trend["note"])


def test_event_without_optional_fields(tmp_path: Path) -> None:
    event = {"event_id": "b1", "event_type": "limited_discount", "model_id": "seedance-2.5", "route": "fal", "status": "candidate",
             "dates": None, "change": None, "before": None, "after": None, "evidence_refs": None, "sources": None}
    _write(tmp_path, [event])
    code, _, data = _build(tmp_path)
    [card] = data["EVENTS"]
    assert (code, card["t7"], card["chg"], card["sum"], card["flag"], data["SRC"]) == (0, "한시 할인", [], "할인 · 종료일 미확인", "공식 확인 전", {})
