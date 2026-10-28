"""공급사 비교 표 (dashboard/compare.py): collector runs made by core.live with the registered endpoint parsers on
synthetic OpenRouter and Vercel listings, read back into the per-model comparison table of the 10/25~27 decisions."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.dashboard import build, compare
from scripts.market_collection.events.__main__ import main as events_main
from scripts.market_collection.events.engine import base_event
from scripts.market_collection.sources import source_specs

GLM, ASTRA = "glm-5.2", "gpt-6-astra"
KEYS = {GLM: ("openrouter_llm_endpoints_glm_5_2", "vercel_llm_endpoints_glm_5_2"),
        ASTRA: ("openrouter_llm_endpoints_gpt_6_astra", "vercel_llm_endpoints_gpt_6_astra")}
IDS = {GLM: ("z-ai/glm-5.2", "zai/glm-5.2"), ASTRA: ("openai/gpt-6-astra", "openai/gpt-6-astra")}
DEMO = {"is_demo": True, "records": [{"model_id": GLM, "route_id": "vercel_ai_gateway"}, {"model_id": ASTRA, "route_id": "vercel_ai_gateway"}]}


def _token(per_million) -> str:
    return format(Decimal(str(per_million)) / 1000000, "f")


def _prices(prompt, completion, cache=None, **extra) -> dict:
    out = {"prompt": _token(prompt), "completion": _token(completion), **extra}
    if cache is not None:
        out["input_cache_read"] = _token(cache)
    return out


def _or(tag, prices, quant="fp8", tier=None, status=0, discount=0, overrides=None) -> dict:
    pricing = dict(prices, discount=discount, **({"overrides": overrides} if overrides else {}))
    return {"provider_name": tag.split("/")[0].title(), "tag": tag, "name": f"{tag.split('/')[0]} | model-20260616", "pricing": pricing,
            "service_tier": tier, "quantization": quant, "context_length": 202752, "status": status}


def _vercel(provider, prices, quant=None, **extra) -> dict:
    return {"provider_name": provider, "pricing": dict(prices, discount=0), "context_length": 200000, "quantization": quant, "status": 0, **extra}


def _run(root: Path, model: str, openrouter: list | None, vercel: list | str | None) -> Path:
    """One collector run of the model's two endpoint listings: None leaves a source out of the run, "fail" answers HTTP 500."""
    bodies = dict(zip(KEYS[model], (openrouter, vercel)))
    specs = [spec for spec in source_specs() if bodies.get(spec.source_key) is not None]
    listing = {key: {"data": {"id": model_id, "endpoints": bodies[key]}} for key, model_id in zip(KEYS[model], IDS[model])}
    _, adapters = core.registry()
    return core.live(root, specs=specs, adapters=adapters, fetcher=lambda spec: (json.dumps(listing[spec.source_key]).encode(), spec.url,
                                                                                    500 if bodies[spec.source_key] == "fail" else 200))


def _run_models(root: Path, lists: dict) -> Path:
    """One collector run of several models' endpoint listings, {model: (openrouter, vercel)}."""
    bodies = {key: body for model, pair in lists.items() for key, body in zip(KEYS[model], pair)}
    listing = {key: {"data": {"id": model_id, "endpoints": bodies[key]}} for model in lists for key, model_id in zip(KEYS[model], IDS[model])}
    specs = [spec for spec in source_specs() if bodies.get(spec.source_key) is not None]
    _, adapters = core.registry()
    return core.live(root, specs=specs, adapters=adapters, fetcher=lambda spec: (json.dumps(listing[spec.source_key]).encode(), spec.url, 200))


GLM_OR = [
    _or("alpha/fp4", _prices(1, 4, 0.1), quant="fp4"),
    _or("beta/mxfp4", _prices(1, 3, 0.2), quant="mxfp4"),
    _or("gamma/nvfp4/us-east-1", _prices(1.1, 4.4, 0.11), quant="nvfp4"),
    _or("delta/fp8", _prices(2, 6, 0.2)),
    _or("eps/fp8/global", _prices(2, 5, 0.3)),
    _or("zeta/fast", _prices(3, 9, 0.3), tier="priority"),
    _or("eta/fp8/us/fast", _prices(3.3, 9.9, 0.33), tier="priority"),
    _or("theta", _prices(0.5, 1, 0.05), quant=None),
    _or("iota/fp8", _prices(0.4, 1, 0.04), status=-5),
    _or("kappa/fp8", _prices(2.5, 7, 0.25), status=-2),
    _or("lam/fp8", _prices(1.5, 4.5, 0.15), discount=0.25),
    _or("omicron/fp4", _prices(0.8, 2, 0.08), quant="fp4"),
    _or("rho/fp8", _prices(0.4, 1.6, 0.04), overrides=[dict(_prices(0.4, 1.6, 0.04), utc_start=0, utc_end=1400),
                                                         dict(_prices(0.2, 0.8, 0.02), utc_start=1400, utc_end=0)]),
]
GLM_VERCEL = [
    _vercel("alpha", _prices(1, 4, 0.1)),
    _vercel("zai", _prices(2, 6, 0.2), quant="fp8"),
    _vercel("nu", _prices(2.2, 6.6, 0.22), quant="fp8",
            inference_regions=[{"scope": "zone", "geo_region": "us", "pricing": _prices(2.42, 7.26, 0.242)}]),
    _vercel("xi", _prices(0.15, 0.6, 0.003), quant="fp8",
            peak_pricing={"multiplier": 2, "windows": [{"start_minute_utc": 60, "end_minute_utc": 240, "days_of_week": [1, 2, 3, 4, 5]}]}),
    _vercel("omicron", _prices(0.8, 2, 0.08), quant="fp8"),
]


def _rows(table: dict) -> dict:
    """'route · upstream' -> (group title or '비교 제외', row) for every row; the first row wins on a repeated name."""
    out = {}
    for name, rows in table["groups"]:
        for row in rows:
            out.setdefault(" · ".join(row["w"]), (name, row))
    for row in table["ex"]:
        out.setdefault(" · ".join(row["w"]), ("비교 제외", row))
    return out


@pytest.fixture(scope="module")
def glm(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("glm")
    _run(root, GLM, GLM_OR, GLM_VERCEL)
    return compare.tables(root, [GLM, "seedance-2.5"], [], [], None, None, DEMO, "2026-10-27")


def test_one_table_per_llm_model_with_the_observation_time_above(glm):
    assert list(glm) == [GLM]  # seedance-2.5 is a video model: no table
    table = glm[GLM]
    assert re.fullmatch(r"\d{1,2}/\d{1,2} \d{2}:\d{2}", table["at"]) and table["unit"] == "USD / 100만 토큰"
    assert table["items"] == ["입력", "출력", "캐시 읽기"] and table["disc"] is True


def test_groups_by_precision_tier_and_region_in_the_decided_order(glm):
    assert [name for name, _ in glm[GLM]["groups"]] == [
        "fp4 · Standard (mxfp4 포함)", "fp4 · US (nvfp4 포함)", "fp8 · Standard", "fp8 · Priority", "fp8 · US", "fp8 · US · Priority"]
    rows = _rows(glm[GLM])
    assert rows["OpenRouter · gamma/nvfp4/us-east-1"][1]["n"] == "nvfp4 · 표기 us-east-1"
    assert rows["OpenRouter · eps/fp8/global"] == ("fp8 · Standard", rows["OpenRouter · eps/fp8/global"][1])
    assert rows["OpenRouter · eps/fp8/global"][1]["n"] == "표기 global"
    assert rows["OpenRouter · zeta/fast"][0] == "fp8 · Priority" and rows["OpenRouter · zeta/fast"][1]["n"] == "표기 fast"
    assert rows["OpenRouter · eta/fp8/us/fast"][0] == "fp8 · US · Priority"
    assert rows["OpenRouter · beta/mxfp4"][1]["n"] == "mxfp4"


def test_cheapest_cell_per_column_ties_all_and_a_lone_row_is_unique(glm):
    rows = _rows(glm[GLM])
    marks = lambda name: [cell[1] for cell in rows[name][1]["c"]]
    assert marks("OpenRouter · alpha/fp4") == ["lo", "", "lo"]  # input ties beta and the Vercel row
    assert marks("OpenRouter · beta/mxfp4") == ["lo", "lo", ""]
    assert marks("Vercel · alpha") == ["lo", "", "lo"]
    assert marks("OpenRouter · gamma/nvfp4/us-east-1") == ["un", "un", "un"]
    assert marks("OpenRouter · delta/fp8") == ["", "", ""] and marks("OpenRouter · lam/fp8") == ["lo", "lo", "lo"]  # the discount row's shown price
    assert rows["OpenRouter · alpha/fp4"][1]["c"][0][0] == "$1.00"
    # border (no f) for a stated precision, fill only for an estimate and a status -2 row (decision 3)
    assert "f" not in rows["OpenRouter · alpha/fp4"][1] and rows["Vercel · alpha"][1]["f"] == 1 and rows["OpenRouter · kappa/fp8"][1]["f"] == 1


def test_estimate_from_the_other_gateway_and_status_chips(glm):
    rows = _rows(glm[GLM])
    name, row = rows["Vercel · alpha"]
    assert name.startswith("fp4 · Standard") and row["n"] == "fp4 추정 · 다른 경로 기준 (OpenRouter 표기 fp4)" and ["추정", "est"] in row["t"]
    assert rows["OpenRouter · kappa/fp8"][0] == "fp8 · Standard" and ["성능 저하", "st"] in rows["OpenRouter · kappa/fp8"][1]["t"]


def test_excluded_rows_carry_their_reason(glm):
    rows = _rows(glm[GLM])
    assert rows["OpenRouter · theta"] == ("비교 제외", rows["OpenRouter · theta"][1])
    assert rows["OpenRouter · theta"][1]["n"] == "비교 제외 · 정밀도 미상 · 직접 확인 필요"
    assert rows["OpenRouter · iota/fp8"][1]["n"] == "비교 제외 · OpenRouter 상태: 중단, 지금 요청이 실패하고 있어 대체 경로로만 쓰임"
    assert rows["Vercel · xi"][1]["n"] == "비교 제외 · 피크 요금: 평일 한국 시각 10–13시에 2배 (0.3 / 1.2 / 0.006)"
    assert rows["OpenRouter · rho/fp8"][1]["n"] == "비교 제외 · 피크 요금: 매일 한국 시각 9–23시에 2배 (0.4 / 1.6 / 0.04)"
    assert len([r for r in glm[GLM]["ex"] if r["w"] == ["OpenRouter", "rho/fp8"]]) == 1  # the window rows fold into it
    for name in ("OpenRouter · omicron/fp4", "Vercel · omicron"):
        assert rows[name][0] == "비교 제외" and ["출처 상충", "con"] in rows[name][1]["t"]
        assert rows[name][1]["n"] == "비교 제외 · 출처 상충 (OpenRouter fp4, Vercel fp8) · 직접 확인 필요"
    assert all(cell[1] == "" for row in glm[GLM]["ex"] for cell in row["c"])


def test_openrouter_discount_is_compared_at_the_shown_price(glm):
    row = _rows(glm[GLM])["OpenRouter · lam/fp8"][1]
    assert row["d"] == [["1.5 / 4.5 / 0.15", ""], ["미확인", "gy"], ["2 / 6 / 0.2 (추정)", "est"]]
    assert row["n"] == "할인 25% · 기간 미공지"


def test_vercel_region_price_is_its_own_row(glm):
    table = glm[GLM]
    region = dict(table["groups"])["fp8 · US"]
    assert [r["w"] for r in region] == [["Vercel", "nu"]]
    assert region[0]["n"] == "US 지역을 고정한 요청에만 적용 · 기본 가격 대비 1.1배" and region[0]["p"] == ["—", ""]  # no history given
    assert [c[1] for c in region[0]["c"]] == ["un", "un", "un"]


def test_assumed_row_and_route_note(glm):
    name, row = _rows(glm[GLM])["Vercel · zai"]
    assert name == "fp8 · Standard" and ["가정", "as"] in row["t"]
    assert sum(1 for _, rows in glm[GLM]["groups"] for r in rows if ["가정", "as"] in r.get("t", [])) == 1
    assert glm[GLM]["special"].startswith("공개 가중치 모델이라 OpenRouter의 Priority 행은")


@pytest.fixture(scope="module")
def astra_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("astra")
    long = [{"min_prompt_tokens": 272000, **_prices(20, 75, 2)}]
    _run(root, ASTRA, [
        _or("openai", _prices(10, 50, 1), quant="unknown", overrides=long),
        _or("openai/fast", _prices(20, 100, 2), quant="unknown", tier="priority"),
        _or("openai/ultrafast", _prices(60, 300, 6), quant="unknown", tier="ultrafast"),
        _or("azure/us", _prices(11, 55, 1.1), quant="unknown"),
    ], [
        _vercel("openai", _prices(10, 50, 1, service_tiers={"flex": _prices(5, 25, 0.5), "priority": _prices(20, 100, 2)}),
                inference_regions=[{"scope": "zone", "geo_region": "eu", "pricing": _prices(11, 55, 1.1)}]),
    ])
    return root


@pytest.fixture(scope="module")
def astra(astra_root) -> dict:
    return compare.tables(astra_root, [ASTRA], [], [], None, None, {"is_demo": False, "records": [{"model_id": ASTRA, "route_id": "vercel_ai_gateway"}]},
                          "2026-10-27")


def test_closed_model_rows_are_비공개_and_vercel_tiers_split_out(astra):
    table = astra[ASTRA]
    groups = dict(table["groups"])
    assert list(groups) == ["비공개 · Standard", "비공개 · Standard · 입력 272K 초과", "비공개 · Priority", "비공개 · Ultrafast",
                            "비공개 · Flex", "비공개 · US", "비공개 · EU"]
    assert [r["w"] for r in groups["비공개 · Priority"]] == [["OpenRouter", "openai/fast"], ["Vercel", "openai"]]
    assert all(c[1] == "lo" for r in groups["비공개 · Priority"] for c in r["c"])  # the Vercel tier price ties OpenRouter's
    assert groups["비공개 · Flex"][0]["n"] == "등급을 지정한 요청에만 적용" and groups["비공개 · Flex"][0]["c"][0] == ["$5.00", "un"]
    assert all(r.get("f") == 1 for rows in groups.values() for r in rows)  # no precision is written for a closed-weight model
    assert ["사용 경로", "as"] in groups["비공개 · Standard"][1]["t"]  # is_demo false: LETSUR's recorded route, not an assumption
    assert "special" not in table


def _chips(table: dict) -> dict:
    """'route · upstream' of every row with a route chip -> the chip's text."""
    return {" · ".join(row["w"]): chip[0] for _, rows in table["groups"] for row in rows for chip in row.get("t", []) if chip[1] == "as"}


def test_every_record_marks_its_own_route_with_its_own_basis(astra_root):
    """2026-10-28: the chip follows each record's basis (settings: 렛서 설정, observed: 호출 확인), and every record of the model
    marks its Standard row, not only the first record."""
    def chips(records, is_demo=False):
        routes = {"is_demo": is_demo, "records": [{"model_id": ASTRA, **record} for record in records]}
        return _chips(compare.tables(astra_root, [ASTRA], [], [], None, None, routes, "2026-10-27")[ASTRA])
    both = chips([{"route_id": "openrouter", "basis": "settings"}, {"route_id": "vercel_ai_gateway", "basis": "observed"}])
    assert both == {"OpenRouter · openai": "렛서 설정", "Vercel · openai": "호출 확인"}
    assert chips([{"route_id": "vercel_ai_gateway", "basis": "observed"}, {"route_id": "openrouter", "basis": "settings"}]) == both  # not the first record only
    assert chips([{"route_id": "openrouter"}, {"route_id": "vercel_ai_gateway", "basis": "observed"}], is_demo=True) == {
        "OpenRouter · openai": "가정", "Vercel · openai": "호출 확인"}  # a record without basis keeps the old wording
    assert chips([{"route_id": "openrouter"}, {"route_id": "vercel_ai_gateway", "basis": "settings"}]) == {
        "OpenRouter · openai": "사용 경로", "Vercel · openai": "렛서 설정"}
    assert chips([{"route_id": None, "basis": "observed"}]) == {}  # not offered: no row


def test_astra_openai_direct_row_says_settings_and_its_azure_row_says_observed():
    """2026-10-28: gpt-6-astra has a registered route (OpenAI direct) and the route one API call went through (Azure); each of the
    two Standard rows says its own basis. Which row a record marks is still decision 17's rule (Standard, no region or band)."""
    records = [{"model_id": ASTRA, "route_id": "openai_direct", "basis": "settings"},
               {"model_id": ASTRA, "route_id": "azure_foundry", "basis": "observed"}]
    targets = compare.assumed_routes(ASTRA, {"is_demo": False, "records": records})
    assert targets == [("openai_direct", "", "settings"), ("azure_foundry", "", "observed")]

    def chip(route, publisher, **fields):
        row = _row(model=ASTRA, route=route, who=[publisher, route], **{"origin": "listing", **fields})
        return compare.assumed_chip(row, targets, False)
    assert chip("openai_direct", "OpenAI") == "렛서 설정" and chip("azure_foundry", "Microsoft") == "호출 확인"
    assert chip("azure_foundry", "Microsoft", tier="Priority") == "" and chip("openai_direct", "OpenAI", region="US") == ""
    assert chip("openai_direct", "OpenAI", band="272K") == "" and chip("azure_foundry", "Microsoft", origin="vercel_tier") == ""
    gateway = _row(model=ASTRA, route="vercel_ai_gateway", provider="openai", origin="listing", who=["Vercel", "openai"])
    assert compare.assumed_chip(gateway, targets, False) == ""  # no record on that route


def test_a_record_with_an_endpoint_marks_only_that_endpoint_and_a_record_without_basis_keeps_the_old_words():
    records = {"records": [{"model_id": GLM, "route_id": "vercel_ai_gateway", "endpoint_id": "nu", "basis": "observed"}]}
    targets = compare.assumed_routes(GLM, dict(records, is_demo=False))
    nu = _row(route="vercel_ai_gateway", provider="nu", origin="listing", who=["Vercel", "nu"])
    zai = _row(route="vercel_ai_gateway", provider="zai", origin="listing", who=["Vercel", "zai"])
    assert (compare.assumed_chip(nu, targets, False), compare.assumed_chip(zai, targets, False)) == ("호출 확인", "")
    plain = compare.assumed_routes(GLM, {"records": [{"model_id": GLM, "route_id": "vercel_ai_gateway"}]})
    assert plain == [("vercel_ai_gateway", "", None)]
    assert (compare.assumed_chip(zai, plain, True), compare.assumed_chip(zai, plain, False)) == ("가정", "사용 경로")  # maker's own upstream
    assert compare.assumed_routes(GLM, {"records": [{"model_id": "other", "route_id": "openrouter"}]}) == [] and compare.assumed_routes(GLM, None) == []


@pytest.mark.parametrize(("maker", "spellings"), [
    ("Z.ai", ["Z.AI", "z-ai", "zai"]),
    ("Moonshot AI", ["Moonshot AI", "moonshotai"]),
    ("xAI", ["xAI", "x-ai", "xai", "SpaceXAI", "spacexai"]),
    ("MiniMax", ["MiniMax", "minimax"]),
    ("Meta", ["Meta", "meta"]),
    ("Alibaba", ["Alibaba", "alibaba"]),
])
def test_a_makers_name_and_its_spellings_on_the_gateways_are_one_upstream(maker, spellings):
    """2026-10-28: OpenRouter writes 'Z.AI', 'xAI', 'Meta', 'Alibaba', 'MiniMax', 'Moonshot AI' (and names xAI's models SpaceXAI) and
    Vercel the lowercase ids ('zai', 'spacexai', 'meta', 'moonshotai' ...); norm() makes each spelling the maker's own name."""
    assert {compare.norm(spelling) for spelling in spellings} == {compare.norm(maker)}


@pytest.mark.parametrize(("model", "maker", "route", "conditions"), [
    ("glm-4.6", "Z.ai", "openrouter", {"endpoint_tag": "z-ai/fp8"}),
    ("glm-5.1", "Z.ai", "vercel_ai_gateway", {"endpoint_provider": "zai"}),
    ("kimi-k2.6", "Moonshot AI", "openrouter", {"endpoint_tag": "moonshotai"}),
    ("kimi-k2.5", "Moonshot AI", "vercel_ai_gateway", {"endpoint_provider": "moonshotai"}),
    ("grok-4.20", "xAI", "openrouter", {"endpoint_tag": "xai"}),
    ("grok-4.6", "xAI", "openrouter", {"endpoint_tag": "spacexai/fast"}),
    ("grok-4.3", "xAI", "vercel_ai_gateway", {"endpoint_provider": "spacexai"}),
    ("grok-4.3", "xAI", "vercel_ai_gateway", {"endpoint_provider": "xai"}),
    ("minimax-m2.7", "MiniMax", "openrouter", {"endpoint_tag": "minimax/fp8"}),
    ("minimax-m3", "MiniMax", "vercel_ai_gateway", {"endpoint_provider": "minimax"}),
    ("muse-spark-1.2", "Meta", "openrouter", {"endpoint_tag": "meta"}),
    ("muse-spark-1.3", "Meta", "vercel_ai_gateway", {"endpoint_provider": "meta"}),
    ("qwen3.7-flash", "Alibaba", "openrouter", {"endpoint_tag": "alibaba"}),
    ("qwen3.7-max", "Alibaba", "vercel_ai_gateway", {"endpoint_provider": "alibaba"}),
])
def test_the_route_chip_lands_on_the_makers_own_upstream_row_of_each_gateway(monkeypatch, model, maker, route, conditions):
    """2026-10-28 (decision 17): on a gateway the chip marks the maker's own upstream at Standard. The row is placed from what its
    source states (OpenRouter tag, Vercel provider); the maker is set here, so this checks the spelling match and not the
    FAMILY_MAKERS of events/catalog.py."""
    monkeypatch.setattr(compare, "maker", lambda _model: maker)
    targets = [(route, "", "observed")]

    def placed(**stated) -> dict:
        row = _row(model=model, route=route, conditions=stated, unknown={}, origin="listing")
        compare.place(row)
        return row
    assert compare.assumed_chip(placed(**conditions), targets, False) == "호출 확인"
    assert compare.assumed_chip(placed(**{key: "deepinfra" for key in conditions}), targets, False) == ""  # another upstream of the model


def _row(**fields) -> dict:
    row = {"model": GLM, "route": "openrouter", "provider": "", "tier": "Standard", "region": "", "band": "", "discount": None,
           "prices": {}, "future": {}, "start": set(), "until": set(), "conditions": {}, "notes": []}
    row.update(fields)
    return row


def D(*values) -> dict:
    return dict(zip(("input", "output", "cache_read"), map(Decimal, values)))


def test_discount_end_is_dated_only_by_the_upstreams_own_announced_change():
    studio = _row(provider="googleaistudio", discount=Decimal("0.5"), prices=D("0.75", "3.75", "0.075"))
    vertex = _row(provider="googlevertex", tier="Priority", discount=Decimal("0.5"), prices=D("1.35", "6.75", "0.135"))
    direct = _row(route="google_direct", prices=D("0.75", "3.75", "0.075"), future=D("1.50", "7.50", "0.15"), start={"2027-01-01"},
                  until={"2026-12-31"})
    rows = [studio, vertex, direct]
    for row in rows:
        compare.discount_cells(row, rows, compare.ITEMS, [], date(2026, 10, 27))
    assert studio["disc"] == [["0.75 / 3.75 / 0.075", ""], ["12/31 (추정 · 공급사 문서 기준)", "est"], ["1.5 / 7.5 / 0.15 (추정)", "est"]]
    assert vertex["disc"][1] == ["미확인", "gy"]  # no announced change of that upstream matches
    assert direct["disc"] == [["0.75 / 3.75 / 0.075", ""], ["12/31", "date"], ["1.5 / 7.5 / 0.15", ""]] and direct["end"] == "2026-12-31"
    assert direct["notes"] == ["예정 인상 · 공급사 문서"]
    free = _row(provider="x", discount=Decimal("1"), prices=D("0", "0", "0"))
    compare.discount_cells(free, [free], compare.ITEMS, [], date(2026, 10, 27))
    assert free["disc"][2] == ["미확인", "gy"] and free["notes"] == ["할인 100% · 기간 미공지"]


def test_promotion_with_only_a_lower_bound_date():
    row = _row(route="openai_direct", prices=D("4", "20", "0.4"),
               conditions={"promotion_note": "promotional pricing is available at least through November 21, 2026."})
    compare.discount_cells(row, [row], compare.ITEMS, [], date(2026, 10, 27))
    assert row["disc"] == [["4 / 20 / 0.4", ""], ["최소 11/21", "min"], ["미확인", "gy"]]


def _notice(event_id, pct, end, state, published, quote, group=None) -> dict:
    event = base_event(event_id, "limited_discount", GLM, "vercel_ai_gateway", "confirmed")
    event.update(change={"discount_pct": pct, "discount_pct_basis": "stated", "discounted_amount": None}, period_state=state,
                 period_end={"as_of": "2026-10-27", "basis": "derived_from_publication", "date": end},
                 dates={"end_date": None, "published": published}, sources=[{"quote": quote}], group_key=group)
    return event


def test_channel_notices_end_dates_recent_endings_and_named_upstreams():
    events = [_notice("e1", "50", "2026-12-31", "active", "2026-09-02T00:00:00Z", "The model is 50% off through December 31st."),
              _notice("e2", "35", "2026-10-22", "ended", "2026-10-01T00:00:00Z", "35% off through October 22 when routed through Novita."),
              _notice("e3", "100", "2026-08-27", "ended", "2026-08-13T00:00:00Z", "free for eve agents through August 27, served by Blackbox AI.")]
    notices = compare.channel_notices(events, {GLM})
    whole, novita = _row(route="vercel_ai_gateway", provider="zai"), _row(route="vercel_ai_gateway", provider="novita")
    for row in (whole, novita):
        compare.discount_cells(row, [whole, novita], compare.ITEMS, notices, date(2026, 10, 27))
    assert whole["disc"] == [["50% 할인 (공개 안 됨 · 추정 수치)", ""], ["12/31 (연도 추정)", "date"], ["미확인", "gy"]]
    assert whole["notes"] == ["한시 할인 50% · Vercel 공지 9/2"]
    assert novita["disc"][1] == ["12/31 (연도 추정)", "date"]  # the live whole-route notice wins over the ended one in the cell
    assert novita["notes"] == ["한시 할인 50% · Vercel 공지 9/2", "끝난 할인 35% · 10/22 종료"]
    ended = _row(route="vercel_ai_gateway", provider="novita")
    compare.discount_cells(ended, [ended], compare.ITEMS, [n for n in notices if n["provider"] == "novita"], date(2026, 10, 29))
    assert ended["disc"] == [["종료", "gy"], ["10/22 (연도 추정)", "gy"], ["미확인", "gy"]]
    gone = _row(route="vercel_ai_gateway", provider="novita")
    compare.discount_cells(gone, [gone], compare.ITEMS, [n for n in notices if n["provider"] == "novita"], date(2026, 10, 30))
    assert "disc" not in gone  # 8 days after the end it leaves the table (decision 22)
    blackbox = _row(route="vercel_ai_gateway", provider="blackboxai")
    compare.discount_cells(blackbox, [blackbox], compare.ITEMS, notices, date(2026, 8, 20))
    assert blackbox["notes"][0] == "eve 전용 100% 할인 · 할인 칸에 넣지 않음" and blackbox["disc"][0][0].startswith("50%")


def test_vercel_notice_runs_out_into_the_upstreams_announced_price():
    """2026-10-27 결정(1번 가안): a rate-only Vercel notice on a row priced like its upstream today, whose undiscounted price is
    the upstream's announced next price, gets that price as an estimate; any other row keeps 미확인."""
    notices = compare.channel_notices([_notice("e1", "50", "2026-12-31", "active", "2026-09-02T00:00:00Z",
                                               "The model is 50% off through December 31st.")], {GLM})
    vercel = _row(route="vercel_ai_gateway", provider="googleaistudio", prices=D("0.75", "3.75", "0.075"))
    other = _row(route="vercel_ai_gateway", provider="novita", prices=D("0.75", "3.75", "0.075"))
    direct = _row(route="google_direct", prices=D("0.75", "3.75", "0.075"), future=D("1.50", "7.50", "0.15"), start={"2027-01-01"},
                  until={"2026-12-31"})
    rows = [vercel, other, direct]
    for row in rows:
        compare.discount_cells(row, rows, compare.ITEMS, notices, date(2026, 10, 27))
    assert vercel["disc"] == [["50% 할인 (공개 안 됨 · 추정 수치)", ""], ["12/31 (연도 추정)", "date"],
                              ["1.5 / 7.5 / 0.15 (추정 · 공급사 문서 기준)", "est"]]
    assert (vercel["promo"]["direct"], vercel["promo"]["start"], vercel["promo"]["rate"]) == ("google_direct", "2027-01-01", Decimal("0.5"))
    assert other["disc"][2] == ["미확인", "gy"] and "promo" not in other


def test_period_and_large_swing_from_state_and_events():
    conditions = {"endpoint_tag": "x/fp8", "service_tier": "standard", "quantization": "fp8"}
    state = {"processed_runs": ["20261016T070232Z-a", "20261022T045858Z-b"],
             "baselines": [{"source_key": "k", "billing_item": "input", "conditions": conditions, "run_id": "20261016T155906Z-a2"},
                           {"source_key": "k", "billing_item": "input", "conditions": dict(conditions, endpoint_tag="y/fp8"),
                            "run_id": "20261022T045858Z-b"}],
             "not_comparable": [{"source_key": "k", "billing_item": "input", "conditions": dict(conditions, endpoint_tag="z/fp8"),
                                 "run_id": "20261019T045101Z-c", "_key": "d"}]}

    def change(before_at, before, after_at, after, tag="x/fp8") -> dict:
        return {"event_type": "price_change", "source_key": "k", "billing_item": "input", "conditions": dict(conditions, endpoint_tag=tag),
                "before": {"amount": before, "observed_at_utc": before_at}, "after": {"amount": after, "observed_at_utc": after_at}}

    history = compare.History(state, [change("2026-10-23T05:00:00Z", "0.4", "2026-10-24T05:00:00Z", "1.0"),
                                      change("2026-10-17T05:00:00Z", "0.1", "2026-10-18T05:00:00Z", "1.0", tag="y/fp8")])

    def row(tag) -> dict:
        obs = {"source_key": "k", "billing_item": "input", "conditions": dict(conditions, endpoint_tag=tag), "amount": "1.0",
               "observed_at_utc": "2026-10-27T05:14:00Z"}
        return {"source_key": "k", "obs": {"input": obs}}

    assert history.period(row("x/fp8")) == ["10/24부터", "변경 관측"]
    assert history.period(row("y/fp8")) == ["10/18부터", "변경 관측"]
    assert history.period(row("z/fp8")) == ["10/19부터", "첫 관측"]
    assert history.period(dict(row("x/fp8"), obs={"input": dict(row("x/fp8")["obs"]["input"], billing_item="output")})) == ["—", ""]
    first_night = compare.History({"processed_runs": state["processed_runs"], "baselines": state["baselines"][:1]}, [])
    assert first_night.period(row("x/fp8")) == ["10/16부터", "수집 시작일"]  # 10/17 00:59 KST is the first collection night
    now = datetime(2026, 10, 27, 14, 14, tzinfo=compare.KST)
    assert history.span(row("x/fp8"), now) == [("input", Decimal("0.4"), Decimal("1.0"))]
    assert history.span(row("y/fp8"), now) == []  # its swing was 9 days ago


def _stored(source_key, tag, amount, days_ago, run_time) -> dict:
    at = (run_time - timedelta(days=days_ago)).astimezone(timezone.utc)
    obs = {"observation_id": f"{tag}-{days_ago}", "run_id": f"{at:%Y%m%dT%H%M%SZ}-old", "observed_at_utc": f"{at:%Y-%m-%dT%H:%M:%S}Z",
           "model_id": GLM, "route": "openrouter" if source_key.startswith("openrouter") else "vercel_ai_gateway", "source_key": source_key,
           "billing_item": "input", "amount": amount, "currency": "USD", "unit": "1M tokens", "price_kind": "published_rate",
           "effective_until": "", "conditions": {("endpoint_tag" if source_key.startswith("openrouter") else "endpoint_provider"): tag,
                                                 "service_tier": "standard", "quantization": "fp8", "endpoint_status": "0"}}
    return {"obs": obs}


def test_rows_missing_from_the_newest_run(tmp_path):
    run = _run(tmp_path, GLM, [_or("delta/fp8", _prices(2, 6, 0.2)), _or("eps/fp8", _prices(3, 7, 0.3))], "fail")
    moment = compare.run_time(run.name)
    state = {"series": {"a": _stored(KEYS[GLM][0], "old/fp8", "1.0", 3, moment), "b": _stored(KEYS[GLM][0], "older/fp8", "1.0", 10, moment),
                        "c": _stored(KEYS[GLM][1], "vv", "0.5", 1, moment), "d": _stored("vercel_retired_source", "ww", "0.5", 1, moment)}}
    table = compare.tables(tmp_path, [GLM], [], [], state, None, None, f"{moment.date():%Y-%m-%d}")[GLM]
    rows = _rows(table)
    assert rows["OpenRouter · old/fp8"][0] == "비교 제외"
    assert rows["OpenRouter · old/fp8"][1]["n"] == f"관측 끊김 · 마지막 관측 {compare.md((moment - timedelta(days=3)).date())}"
    assert "OpenRouter · older/fp8" not in rows  # more than 7 days ago
    name, failed = rows["Vercel · vv"]  # its source was not read this run: last value kept, never highlighted
    assert name == "fp8 · Standard" and ["수집 실패", "fail"] in failed["t"] and failed["n"].startswith("수집 실패 · 마지막 관측")
    assert failed["c"][0] == ["$0.50", ""] and rows["OpenRouter · delta/fp8"][1]["c"][0] == ["$2.00", "lo"]
    assert "Vercel · ww" not in rows  # a source the run no longer reads


def test_no_table_without_a_processed_run(tmp_path):
    run = _run(tmp_path, GLM, [_or("delta/fp8", _prices(2, 6, 0.2))], None)
    assert compare.tables(tmp_path, [GLM], [], [], None, {"processed_runs": []}, None, "2026-10-27") == {}
    assert GLM in compare.tables(tmp_path, [GLM], [], [], None, {"processed_runs": [run.name]}, None, "2026-10-27")
    assert compare.tables(tmp_path / "nothing", [GLM], [], [], None, None, None, "2026-10-27") == {}


def test_group_names_and_unit_rules():
    assert compare.group_name("fp4", "", "Standard", "", ["mxfp4", "nvfp4"]) == "fp4 · Standard (mxfp4·nvfp4 포함)"
    assert compare.group_name("fp4", "US", "Standard", "", []) == "fp4 · US"
    assert compare.group_name("비공개", "", "Priority", "272K", []) == "비공개 · Priority · 입력 272K 초과"
    assert compare.band_of(271999) == compare.band_of(272000) == "272K"
    assert compare.norm("z-ai") == compare.norm("ZAI") and compare.norm("togetherai") == compare.norm("together")
    assert compare.round6(Decimal("0.61306") / (1 - Decimal("0.5621"))) == Decimal("1.4")
    assert compare.money(Decimal("0.0058")) == "$0.0058" and compare.money(Decimal("15")) == "$15.00"


def test_page_carries_the_tables(tmp_path):
    run = _run(tmp_path / "root", GLM, [_or("delta/fp8", _prices(2, 6, 0.2)), _or("eps/fp8", _prices(3, 7, 0.3))], None)
    event = base_event("e1", "price_change", GLM, "openrouter", "confirmed")
    folder = tmp_path / "root" / "events"
    folder.mkdir()
    (folder / "events.json").write_text(json.dumps({"schema_version": 1, "events": [event], "gaps": []}), encoding="utf-8")
    (folder / "report.json").write_text(json.dumps({"status": "ok", "processed_runs": [run.name]}), encoding="utf-8")
    assert build.main(["--root", str(tmp_path / "root"), "--output", str(tmp_path / "site")]) == 0
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    data = json.loads(re.search(r"^const COMPARE = (.*);$", page, re.M).group(1))
    assert [name for name, _ in data[GLM]["groups"]] == ["fp8 · Standard"] and "function compareTable(mid)" in page
    # 2026-10-28 가안: the table is 현재 가격 표 on the 모델별 가격 screen, and a card's detail only links to it
    assert "function renderTable(){" in page and "${compareTable(trendModel)}" in page
    assert 'parts.push(sec("공급사 비교 표"' not in page and 'data-tview="table">공급사 비교 표 보기 → <small>${compareMeta(e.mid)}</small>' in page
    # the excluded rows stay folded once more (2026-10-27 박건 요청)
    assert '<details class="same-rows"><summary>비교 제외 ${T.ex.length}행 보기' in page
    (folder / "events.json").write_text("{", encoding="utf-8")
    assert build.main(["--root", str(tmp_path / "root"), "--output", str(tmp_path / "failed")]) == 0
    failed = (tmp_path / "failed" / "index.html").read_text(encoding="utf-8")
    assert re.search(r"^const COMPARE = (.*);$", failed, re.M).group(1) == "{}"


def test_an_error_in_the_table_leaves_the_page_without_tables(tmp_path, monkeypatch, capsys):
    def broken(*args):
        raise KeyError("x")

    monkeypatch.setattr(compare, "build_tables", broken)
    assert compare.tables(tmp_path, [GLM], [], [], None, None, None, "2026-10-27") == {}
    assert "KeyError" in capsys.readouterr().err


CLOSED_OR = [_or("openai", _prices(10, 50, 1), quant="unknown")]
CLOSED_VERCEL = [_vercel("openai", _prices(10, 50, 1))]


def test_observed_models_are_the_newest_runs_whatever_a_card_says(tmp_path):
    run = _run_models(tmp_path, {GLM: (GLM_OR, GLM_VERCEL), ASTRA: (CLOSED_OR, CLOSED_VERCEL)})
    assert compare.observed_models(tmp_path, {"processed_runs": [run.name]}) == [GLM, ASTRA]
    assert compare.observed_models(tmp_path, {"processed_runs": []}) == []  # the engine has not processed it
    assert compare.observed_models(tmp_path / "nothing", None) == []


def test_a_model_without_cards_still_gets_a_trend_a_table_and_a_list_entry(tmp_path):
    """2026-10-28: the 모델별 가격 list covers every model the newest run observed, not only models with change cards. A new model
    has baselines and no events: glm-5.2 has series to draw (TREND, badge 변화 없음), gpt-6-astra's observations all have unknown
    conditions, so it has only the table; both are in the list. The cards and cards.json do not change."""
    root = tmp_path / "root"
    run = _run_models(root, {GLM: (GLM_OR, GLM_VERCEL), ASTRA: (CLOSED_OR, CLOSED_VERCEL)})
    assert events_main(["build", "--run", str(run), "--output", str(root / "events")]) == 0  # the engine's own state, events and report

    def built(name):
        assert build.main(["--root", str(root), "--output", str(tmp_path / name)]) == 0
        page = (tmp_path / name / "index.html").read_text(encoding="utf-8")
        slot = lambda key: json.loads(re.search(rf"^const {key} = (.*);$", page, re.M).group(1))
        return page, slot, json.loads((tmp_path / name / "cards.json").read_text(encoding="utf-8"))["cards"]
    page, slot, judged = built("site")
    assert (slot("EVENTS"), judged) == ([], [])
    assert set(slot("COMPARE")) == {GLM, ASTRA} and ASTRA not in slot("TREND")  # nothing to draw, the table has its rows
    assert slot("COMPARE")[ASTRA]["groups"]
    trend = slot("TREND")[GLM]
    assert (trend["name"], trend["badge"]) == (GLM, ["변화 없음", ""]) and (trend.get("tiers") or trend.get("series"))
    # the page lists TREND and COMPARE models alike, #m/<model> opens either, and an empty trend points at the table
    assert "const MODEL_IDS = [...new Set([...Object.keys(TREND), ...Object.keys(COMPARE)])];" in page
    assert "MODEL_IDS.includes(model) && model !== trendModel" in page and "현재 가격은 아래 표에 있습니다." in page
    # a card of one model does not touch the others: its badge counts it, the card model keeps the only card
    folder = root / "events"
    events = json.loads((folder / "events.json").read_text(encoding="utf-8"))
    (folder / "events.json").write_text(json.dumps(dict(events, events=[base_event("e1", "price_change", GLM, "openrouter", "confirmed")])), encoding="utf-8")
    page, slot, judged = built("with-card")
    assert [card["mid"] for card in slot("EVENTS")] == [GLM] and len(judged) == 1
    assert (slot("TREND")[GLM]["badge"], set(slot("COMPARE")), ASTRA in slot("TREND")) == (["변화 1건", ""], {GLM, ASTRA}, False)


def _listing(model, route, conditions, prices=("1", "2", "0.1"), **fields) -> dict:
    row = {"model": model, "route": route, "conditions": conditions, "unknown": fields.pop("unknown", {}), "prices": D(*prices),
           "future": {}, "start": set(), "until": set(), "obs": {}, "origin": "listing"}
    compare.place(row)
    row.update(fields)
    return row


def test_provider_material_decides_precision_where_the_listing_is_silent():
    def precision(model, tag, prices):
        row = _listing(model, "openrouter", {"endpoint_tag": tag, "service_tier": "standard"}, prices)
        compare.precision_of(row)
        return row["prec_kind"], row["precision"], row["prec_note"]

    assert precision("kimi-k3", "fireworks", ("3", "15", "0.3")) == ("doc", "mxfp4", "mxfp4 · 공급사 문서 기준 (Fireworks 모델 페이지)")
    assert precision("kimi-k3", "fireworks/fast", ("4.5", "22.5", "0.45")) == ("estimate", "mxfp4", "mxfp4 추정 · 기본 행 문서 기준")
    assert precision("kimi-k3", "fireworks/spot", ("2.1", "10.5", "0.21"))[0] is None
    assert precision("glm-5.2", "together", ("1.5", "4.4", "0.26")) == ("estimate", "fp4", "fp4 추정 · 공급사 자료 기준 (가격 다름)")
    assert precision("kimi-k3", "digitalocean", ("2.55", "12.95", "0.255"))[2] == "mxfp4 추정 · 공급사 자료 기준 (가격 없음)"
    unclear = _listing("deepseek-v4.1-flash", "openrouter", {"endpoint_tag": "fireworks/us", "service_tier": "standard"})
    compare.precision_of(unclear)
    compare.exclusions(unclear, {})
    assert unclear["why"] == ["직접 확인 필요 (체크포인트 설명만)"]


def test_direct_route_conditions_map_to_tier_region_and_band():
    bedrock = lambda option, band="272K input tokens or fewer": _listing(
        "gpt-6-astra", "aws_bedrock", {"service_tier": "Standard", "region_group": "Commercial Regions", "inference_option": option, "context_band": band})
    assert (bedrock("Global CRIS")["region"], bedrock("Global CRIS")["why"]) == ("", [])
    assert bedrock("US CRIS (bedrock-runtime)")["region"] == "US" and bedrock("Regional (bedrock-mantle in US East (N. Virginia))")["region"] == "US"
    assert bedrock("In-Region")["why"] == ["지역 고정 가격 · 지역 단위 미정 (표기 In-Region)"]
    assert bedrock("Global CRIS", "more than 272K input tokens")["band"] == "272K"
    assert bedrock("Global CRIS")["who"] == ["AWS", "aws_bedrock · Global CRIS"] and bedrock("Global CRIS")["tier"] == "Standard"
    vertex = _listing("gemini-3.8-flash", "google_vertex", {"region": "Global", "service_tier": "standard", "input_token_band": ">200K"})
    assert (vertex["region"], vertex["band"], vertex["labels"]) == ("", "200K", ["표기 Global"])
    azure = lambda scope: _listing("gpt-6-sol", "azure_foundry", {"service_tier": "standard", "meter_scope": scope,
                                                                  "context_band": "short context (ShortCo)"})
    assert (azure("Global (Gl)")["region"], azure("Global (Gl)")["band"], azure("Global (Gl)")["why"]) == ("", "", [])
    assert azure("Global (Gl)")["labels"] == ["글로벌 배포 기준(렛서 배포 방식 미확인)"] and azure("Data Zone (DZ)")["labels"] == ["표기 Data Zone (DZ)"]
    page = _listing(GLM, "openrouter", {"price_basis": "model_page_rate"})
    assert page["who"] == ["OpenRouter", "모델 페이지 대표가"] and page["why"] == ["모델 페이지 대표가 (하위 공급처·조건 미지정)"]
    odd = _listing(GLM, "fal", {"resolution": "1080p"})
    assert odd["why"] == ["조건 맞추기 전 (resolution 1080p)"]


def test_an_unreadable_vercel_record_is_skipped_and_said(tmp_path):
    _run(tmp_path, GLM, [_or("delta/fp8", _prices(2, 6, 0.2))],
         [_vercel("zai", _prices(2, 6, 0.2), quant="fp8", peak_pricing={"multiplier": 2, "windows": [{"start_minute_utc": "late"}]})])
    table = compare.tables(tmp_path, [GLM], [], [], None, None, None, "2026-10-27")[GLM]
    assert table["foot"] == ["Vercel 엔드포인트 원문을 읽지 못해 행 안 등급·지역 가격을 뺀 출처: vercel_llm_endpoints_glm_5_2 zai (ValueError)"]
    assert _rows(table)["Vercel · zai"][1]["n"] == "비교 제외 · 피크 요금 (시간대 미확인)"
