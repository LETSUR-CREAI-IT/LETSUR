import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.market_collection.collectors import llm_endpoints as L, parse_source
from scripts.market_collection.core import SourceSpec, _validate_parse
from scripts.market_collection.events.catalog import maker
from scripts.market_collection.models import BROKER_API_MODELS, GATEWAY_EMBEDDING_MODELS, GATEWAY_MORE_MODELS
from scripts.market_collection.sources import llm_endpoint_source_specs, source_specs

OR_SPEC = SourceSpec("openrouter_llm_endpoints_x", "llm", "glm-5.2", "openrouter", "text_generation",
                     "https://openrouter.ai/api/v1/models/z-ai/glm-5.2/endpoints", "openrouter_endpoints_llm")
VC_SPEC = SourceSpec("vercel_llm_endpoints_x", "llm", "glm-5.2", "vercel_ai_gateway", "text_generation",
                     "https://ai-gateway.vercel.sh/v1/models/zai/glm-5.2/endpoints", "vercel_endpoints_llm")


def or_body(**endpoint):
    base = {"name": "Relace | z-ai/glm-5.2-20260616", "context_length": 1048576, "provider_name": "Relace", "tag": "relace",
            "service_tier": None, "quantization": "unknown", "status": 0,
            "pricing": {"prompt": "0.0000000351", "completion": "0.000006", "input_cache_read": "0.0000000351", "discount": 0}}
    base.update(endpoint)
    return json.dumps({"data": {"id": "z-ai/glm-5.2", "endpoints": [base]}}).encode()


def vc_body(pricing=None, **endpoint):
    base = {"name": "alibaba | zai/glm-5.2", "context_length": 1000000, "provider_name": "alibaba", "quantization": None, "status": 0,
            "pricing": pricing or {"prompt": "0.0000014", "completion": "0.0000044", "input_cache_read": "0.00000028", "discount": 0}}
    base.update(endpoint)
    return json.dumps({"data": {"id": "zai/glm-5.2", "endpoints": [base]}}).encode()


def by_set(conditions):
    grouped = {}
    for row in conditions:
        grouped.setdefault(row["condition_set_id"], {})[row["condition_key"]] = row
    return grouped


def test_registry_adds_197_distinct_sources_for_103_models_after_existing_ones():
    specs = llm_endpoint_source_specs()
    assert len(specs) == 197 and len({s.source_key for s in specs}) == 197
    # The first 36 (2026-10-17) cover the 18 page-sourced LLM models; the 34 gateway-only models and 51 more (2026-10-28) follow.
    assert len({s.model_id for s in specs[:36]}) == 18 and len({s.model_id for s in specs}) == 103
    registered = source_specs()
    first = registered.index(specs[0])
    assert registered[first:first + len(specs)] == specs  # one block, in this order
    assert {s.route for s in specs} == {"openrouter", "vercel_ai_gateway"}


def test_gateway_only_models_have_a_source_only_on_each_gateway_that_lists_them():
    specs = llm_endpoint_source_specs()[36:98]
    assert {s.model_id for s in specs} == {row[0] for row in BROKER_API_MODELS} and len(BROKER_API_MODELS) == 34
    assert [s.route for s in specs].count("openrouter") == 31 and [s.route for s in specs].count("vercel_ai_gateway") == 31
    assert {(s.route, s.parser, s.task) for s in specs} == {("openrouter", "openrouter_endpoints_llm", "text_generation"),
                                                             ("vercel_ai_gateway", "vercel_endpoints_llm", "text_generation")}
    routes = {}
    for s in specs:
        routes.setdefault(s.model_id, set()).add(s.route)
    # OpenRouter lists no muse-wonder-1.0 or qwen3.8-max, and only dated qwen3.5-plus ids (LETSUR's one is unknown).
    assert {m for m, r in routes.items() if r == {"vercel_ai_gateway"}} == {"muse-wonder-1.0", "qwen3.8-max", "qwen3.5-plus"}
    assert {m for m, r in routes.items() if r == {"openrouter"}} == {"grok-4.20", "kimi-k2-thinking", "qwen3-coder-flash"}
    urls = {s.source_key: s.url for s in specs}
    for key, url in (
        ("openrouter_llm_endpoints_deepseek_v4_1_flash_1017", "https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash-1017/endpoints"),
        ("vercel_llm_endpoints_gemini_2_5_flash_lite", "https://ai-gateway.vercel.sh/v1/models/google/gemini-2.5-flash-lite/endpoints"),
        ("openrouter_llm_endpoints_glm_4_5_air", "https://openrouter.ai/api/v1/models/z-ai/glm-4.5-air/endpoints"),
        ("vercel_llm_endpoints_glm_4_5_air", "https://ai-gateway.vercel.sh/v1/models/zai/glm-4.5-air/endpoints"),
        ("openrouter_llm_endpoints_grok_4_3", "https://openrouter.ai/api/v1/models/x-ai/grok-4.3/endpoints"),
        ("vercel_llm_endpoints_grok_4_3", "https://ai-gateway.vercel.sh/v1/models/spacexai/grok-4.3/endpoints"),
        ("openrouter_llm_endpoints_kimi_k2_thinking", "https://openrouter.ai/api/v1/models/moonshotai/kimi-k2-thinking/endpoints"),
        ("vercel_llm_endpoints_minimax_m2_5", "https://ai-gateway.vercel.sh/v1/models/minimax/minimax-m2.5/endpoints"),
        ("openrouter_llm_endpoints_muse_spark_1_3", "https://openrouter.ai/api/v1/models/meta/muse-spark-1.3/endpoints"),
        ("vercel_llm_endpoints_muse_wonder_1_0", "https://ai-gateway.vercel.sh/v1/models/meta/muse-wonder-1.0/endpoints"),
        ("openrouter_llm_endpoints_qwen3_7_max", "https://openrouter.ai/api/v1/models/qwen/qwen3.7-max/endpoints"),
        ("vercel_llm_endpoints_qwen3_7_max", "https://ai-gateway.vercel.sh/v1/models/alibaba/qwen3.7-max/endpoints"),
        ("vercel_llm_endpoints_qwen3_5_plus", "https://ai-gateway.vercel.sh/v1/models/alibaba/qwen3.5-plus/endpoints"),
    ):
        assert urls[key] == url
    # Endpoint APIs only: no catalog-page (missing_*) or other source for these models.
    assert [s for s in source_specs() if s.model_id in routes] == specs


def test_more_gateway_models_have_a_source_only_on_each_gateway_that_lists_them():
    specs = llm_endpoint_source_specs()[98:]
    embeddings = {row[0] for row in GATEWAY_EMBEDDING_MODELS}
    assert len(GATEWAY_MORE_MODELS) == 48 and embeddings == {"text-embedding-3-large", "text-embedding-3-small", "text-embedding-ada-002"}
    assert {s.model_id for s in specs} == {row[0] for row in GATEWAY_MORE_MODELS} | embeddings
    assert [s.route for s in specs].count("openrouter") == 50 and [s.route for s in specs].count("vercel_ai_gateway") == 49
    assert {(s.route, s.parser) for s in specs} == {("openrouter", "openrouter_endpoints_llm"), ("vercel_ai_gateway", "vercel_endpoints_llm")}
    # Embeddings read task "embedding" on both gateways; every other model text_generation.
    assert {(s.route, s.task) for s in specs if s.model_id in embeddings} == {("openrouter", "embedding"), ("vercel_ai_gateway", "embedding")}
    assert {s.task for s in specs if s.model_id not in embeddings} == {"text_generation"}
    routes = {}
    for s in specs:
        routes.setdefault(s.model_id, set()).add(s.route)
    # OpenRouter does not list gpt-5-codex; Vercel lists neither gpt-5.1 nor gemini-3-flash-preview.
    assert {m for m, r in routes.items() if r == {"vercel_ai_gateway"}} == {"gpt-5-codex"}
    assert {m for m, r in routes.items() if r == {"openrouter"}} == {"gpt-5.1", "gemini-3-flash-preview"}
    urls = {s.source_key: s.url for s in specs}
    for key, url in (
        # LETSUR's dated Claude ids are read through the gateways' undated models
        ("openrouter_llm_endpoints_claude_opus_4_5_20251101", "https://openrouter.ai/api/v1/models/anthropic/claude-opus-4.5/endpoints"),
        ("vercel_llm_endpoints_claude_sonnet_4_5_20250929", "https://ai-gateway.vercel.sh/v1/models/anthropic/claude-sonnet-4.5/endpoints"),
        ("vercel_llm_endpoints_claude_haiku_5_5", "https://ai-gateway.vercel.sh/v1/models/anthropic/claude-haiku-5.5/endpoints"),
        ("openrouter_llm_endpoints_o3_pro", "https://openrouter.ai/api/v1/models/openai/o3-pro/endpoints"),
        ("openrouter_llm_endpoints_gpt_5_1", "https://openrouter.ai/api/v1/models/openai/gpt-5.1/endpoints"),
        ("vercel_llm_endpoints_gpt_5_codex", "https://ai-gateway.vercel.sh/v1/models/openai/gpt-5-codex/endpoints"),
        ("openrouter_llm_endpoints_gemini_3_flash_preview", "https://openrouter.ai/api/v1/models/google/gemini-3-flash-preview/endpoints"),
        ("openrouter_llm_endpoints_glm_5_3_flashx", "https://openrouter.ai/api/v1/models/z-ai/glm-5.3-flashx/endpoints"),
        ("vercel_llm_endpoints_glm_5_3_flashx", "https://ai-gateway.vercel.sh/v1/models/zai/glm-5.3-flashx/endpoints"),
        ("vercel_llm_endpoints_text_embedding_ada_002", "https://ai-gateway.vercel.sh/v1/models/openai/text-embedding-ada-002/endpoints"),
        ("openrouter_llm_endpoints_text_embedding_3_large", "https://openrouter.ai/api/v1/models/openai/text-embedding-3-large/endpoints"),
    ):
        assert urls[key] == url
    # The two endpoint APIs are the only gateway sources of these models; their other routes belong to other sources.
    assert [s for s in source_specs() if s.model_id in routes and s.parser.endswith("endpoints_llm")] == specs


def test_gateway_only_model_families_have_makers():
    assert {model: maker(model) for model in ("deepseek-v4-flash", "gemini-2.5-flash-lite", "glm-4.5", "grok-4.20", "kimi-k2-thinking",
                                              "minimax-m3", "muse-wonder-1.0", "qwen3.8-max")} == {
        "deepseek-v4-flash": "DeepSeek", "gemini-2.5-flash-lite": "Google", "glm-4.5": "Z.ai", "grok-4.20": "xAI",
        "kimi-k2-thinking": "Moonshot AI", "minimax-m3": "MiniMax", "muse-wonder-1.0": "Meta", "qwen3.8-max": "Alibaba"}
    assert "unknown" not in {maker(row[0]) for row in BROKER_API_MODELS}


def test_openrouter_endpoint_rows_are_per_upstream_and_stated():
    prices, conditions = L.parse_openrouter(OR_SPEC, or_body())
    _validate_parse(OR_SPEC, "s", prices, conditions)
    assert {(p["billing_item"], p["amount"], p["upstream_provider"]) for p in prices} == {
        ("input", "0.0351", "Relace"), ("output", "6", "Relace"), ("cache_read", "0.0351", "Relace")}
    for p in prices:
        group = by_set(conditions)[p["condition_set_id"]]
    
        assert group["service_tier"]["value"] == "standard"
        assert group["quantization"]["status"] == "unknown" and "unknown" in group["quantization"]["value"]
        assert group["endpoint_discount"]["value"] == "0"
        assert group["endpoint_model_slug"]["value"] == "z-ai/glm-5.2-20260616"


def test_openrouter_cache_write_ttl_is_unknown_but_1h_field_is_stated():
    body = json.loads(or_body())
    body["data"]["endpoints"][0]["pricing"].update(input_cache_write="0.000001", input_cache_write_1h="0.000002")
    prices, conditions = L.parse_openrouter(OR_SPEC, json.dumps(body).encode())
    writes = {p["amount"]: by_set(conditions)[p["condition_set_id"]]["cache_write_ttl"] for p in prices if p["billing_item"] == "cache_write"}
    assert writes["1"]["status"] == "unknown" and writes["2"]["status"] == "stated" and writes["2"]["value"] == "1h"


def test_openrouter_override_keeps_threshold_and_base_separate():
    body = json.loads(or_body())
    body["data"]["endpoints"][0]["pricing"]["overrides"] = [{"min_prompt_tokens": 200000, "prompt": "0.000002"}]
    prices, conditions = L.parse_openrouter(OR_SPEC, json.dumps(body).encode())
    _validate_parse(OR_SPEC, "s", prices, conditions)
    over = [p for p in prices if "override" in p["condition_set_id"]]
    assert len(over) == 1 and over[0]["amount"] == "2"
    group = by_set(conditions)[over[0]["condition_set_id"]]
    assert group["prompt_tokens"]["operator"] == "gt" and group["prompt_tokens"]["value"] == "200000"
    base = [p for p in prices if p["billing_item"] == "input" and "override" not in p["condition_set_id"]]
    assert "prompt_tokens" not in by_set(conditions)[base[0]["condition_set_id"]]


def test_openrouter_service_tier_is_stated_not_defaulted():
    prices, conditions = L.parse_openrouter(OR_SPEC, or_body(service_tier="priority"))
    assert by_set(conditions)[prices[0]["condition_set_id"]]["service_tier"]["value"] == "priority"


@pytest.mark.parametrize("change", [
    {"service_tier": "turbo"}, {"tag": ""}, {"provider_name": ""}, {"context_length": "1M"},
    {"pricing": {"prompt": "-1", "discount": 0}}, {"pricing": {"prompt": "x", "discount": 0}},
    {"status": 3}, {"status": True}, {"status": "0"}, {"status": None}, {"pricing": {"prompt": "1", "discount": "x"}},
    {"pricing": {"prompt": "1", "discount": 2}}, {"pricing": {"prompt": "1", "discount": -0.1}},
])
def test_openrouter_invalid_or_missing_values_fail_closed(change):
    with pytest.raises(ValueError):
        L.parse_openrouter(OR_SPEC, or_body(**change))


def test_openrouter_wrong_model_duplicate_tag_and_duplicate_json_field_fail():
    with pytest.raises(ValueError):
        L.parse_openrouter(OR_SPEC, json.dumps({"data": {"id": "other/model", "endpoints": []}}).encode())
    doc = json.loads(or_body())
    doc["data"]["endpoints"].append(copy.deepcopy(doc["data"]["endpoints"][0]))
    with pytest.raises(ValueError):
        L.parse_openrouter(OR_SPEC, json.dumps(doc).encode())
    with pytest.raises(ValueError):
        L.parse_openrouter(OR_SPEC, b'{"data":{"id":"z-ai/glm-5.2","endpoints":[],"endpoints":[]}}')


def test_absent_price_field_creates_no_row_and_empty_price_is_not_zero():
    prices, _ = L.parse_openrouter(OR_SPEC, or_body(pricing={"prompt": "0.000001", "discount": 0}))
    assert [p["billing_item"] for p in prices] == ["input"]


def test_vercel_endpoint_rows_and_undocumented_fields_stay_unknown():
    prices, conditions = L.parse_vercel(VC_SPEC, vc_body())
    _validate_parse(VC_SPEC, "s", prices, conditions)
    assert {(p["billing_item"], p["amount"]) for p in prices} == {("input", "1.4"), ("output", "4.4"), ("cache_read", "0.28")}
    assert {r["condition_key"] for r in conditions if r["status"] != "stated"} == {"quantization"}
    assert {r["value"] for r in conditions if r["condition_key"] == "endpoint_status"} == {"0"}
    assert {r["value"] for r in conditions if r["condition_key"] == "service_tier"} == {"standard"}
    prices, conditions = L.parse_vercel(VC_SPEC, vc_body(service_tiers=["flex"], inference_regions=["us"]))
    statuses = {r["condition_key"]: r["status"] for r in conditions}
    assert statuses["service_tier_applicability"] == "unknown" and statuses["region_applicability"] == "unknown"


def test_vercel_cache_write_ttl_and_nonzero_discount_are_unknown():
    prices, conditions = L.parse_vercel(VC_SPEC, vc_body({"prompt": "0.000001", "input_cache_write": "0.000002", "discount": 0.5}))
    statuses = {(r["condition_set_id"], r["condition_key"]): r["status"] for r in conditions}
    write = next(p for p in prices if p["billing_item"] == "cache_write")
    assert statuses[(write["condition_set_id"], "cache_write_ttl")] == "unknown"
    assert statuses[(write["condition_set_id"], "discount_application")] == "unknown"


def test_vercel_tiers_keep_each_range_and_base_conflict_is_preserved():
    pricing = {"prompt": "0.000003", "prompt_tiers": [{"min": 0, "max": 200000, "cost": "0.000001"}, {"min": 200000, "cost": "0.000002"}], "discount": 0}
    prices, conditions = L.parse_vercel(VC_SPEC, vc_body(pricing))
    _validate_parse(VC_SPEC, "s", prices, conditions)
    assert sorted(p["amount"] for p in prices) == ["1", "2", "3"]
    conflict = [r for r in conditions if r["status"] == "conflicted"]
    assert len(conflict) == 1 and "differs" in conflict[0]["value"]
    agree = {"prompt": "0.000001", "prompt_tiers": [{"min": 0, "cost": "0.000001"}], "discount": 0}
    _, conditions = L.parse_vercel(VC_SPEC, vc_body(agree))
    assert not [r for r in conditions if r["status"] == "conflicted"]


def test_vercel_wrong_model_duplicate_provider_bad_price_fail():
    with pytest.raises(ValueError):
        L.parse_vercel(VC_SPEC, json.dumps({"data": {"id": "zai/other", "endpoints": []}}).encode())
    doc = json.loads(vc_body())
    doc["data"]["endpoints"].append(copy.deepcopy(doc["data"]["endpoints"][0]))
    with pytest.raises(ValueError):
        L.parse_vercel(VC_SPEC, json.dumps(doc).encode())
    with pytest.raises(ValueError):
        L.parse_vercel(VC_SPEC, vc_body({"prompt": True}))


def test_endpoint_rows_never_share_a_series_with_headline_rows():
    prices, _ = L.parse_openrouter(OR_SPEC, or_body())
    assert all(p["upstream_provider"] and p["condition_set_id"].startswith(OR_SPEC.source_key) for p in prices)


@pytest.mark.parametrize("value,status", [("fp16", "stated"), ("bf16", "stated"), ("nvfp4", "stated"), ("unknown", "unknown"), (None, "unknown"),
                                          ("auto", "unknown"), ("default", "unknown"), ("none", "unknown"), ("", "unknown"), ("FP16", "unknown")])
def test_quantization_only_known_levels_are_stated_on_both_gateways(value, status):
    _, conditions = L.parse_openrouter(OR_SPEC, or_body(quantization=value))
    assert {r["status"] for r in conditions if r["condition_key"] == "quantization"} == {status}
    _, conditions = L.parse_vercel(VC_SPEC, vc_body(quantization=value))
    assert {r["status"] for r in conditions if r["condition_key"] == "quantization"} == {status}


def test_openrouter_missing_quantization_key_is_unknown_not_stated():
    body = json.loads(or_body())
    del body["data"]["endpoints"][0]["quantization"]
    _, conditions = L.parse_openrouter(OR_SPEC, json.dumps(body).encode())
    assert {r["status"] for r in conditions if r["condition_key"] == "quantization"} == {"unknown"}


def test_vercel_service_tier_is_stated_only_where_docs_say_tiers_do_not_apply():
    for provider, status in (("alibaba", "stated"), ("bedrock", "stated"), ("openai", "unknown"), ("azure", "unknown"), ("google", "unknown"),
                             ("vertex", "unknown"), ("vertexAnthropic", "unknown"), ("anthropic", "stated")):
        _, conditions = L.parse_vercel(VC_SPEC, vc_body(provider_name=provider, quantization="fp8"))
        assert {r["status"] for r in conditions if r["condition_key"] == "service_tier"} == {status}, provider


def keyed(conditions, price):
    return {r["condition_key"]: r for r in conditions if r["condition_set_id"] == price["condition_set_id"]}


def test_openrouter_discount_zero_is_stated_nonzero_and_missing_are_unknown_without_touching_the_price():
    for discount, expected in ((0, None), ("0", None), (0.25, "0.25"), ("0.6032", "0.6032")):
        prices, conditions = L.parse_openrouter(OR_SPEC, or_body(pricing={"prompt": "0.000001", "completion": "0.000002", "discount": discount}))
        _validate_parse(OR_SPEC, "s", prices, conditions)
        assert {p["billing_item"]: p["amount"] for p in prices} == {"input": "1", "output": "2"}
        group = keyed(conditions, prices[0])
        if expected is None:
            assert group["endpoint_discount"]["value"] == "0" and group["endpoint_discount"]["status"] == "stated"
            assert "discount_application" not in group
        else:
            assert group["endpoint_discount"]["value"] == expected
            assert group["discount_application"]["status"] == "unknown" and "before or after" in group["discount_application"]["value"]
    prices, conditions = L.parse_openrouter(OR_SPEC, or_body(pricing={"prompt": "0.000001"}))
    group = keyed(conditions, prices[0])
    assert prices[0]["amount"] == "1" and group["discount_application"]["status"] == "unknown" and "endpoint_discount" not in group


def test_endpoint_status_zero_is_active_other_codes_and_absence_are_unknown_both_gateways():
    for parse, spec, make in ((L.parse_openrouter, OR_SPEC, or_body), (L.parse_vercel, VC_SPEC, vc_body)):
        prices, conditions = parse(spec, make(status=0))
        assert keyed(conditions, prices[0])["endpoint_status"]["status"] == "stated" and keyed(conditions, prices[0])["endpoint_status"]["value"] == "0"
        prices, conditions = parse(spec, make(status=-5))
        row = keyed(conditions, prices[0])["endpoint_status"]
        assert row["status"] == "unknown" and row["value"] == "-5"
        body = json.loads(make())
        del body["data"]["endpoints"][0]["status"]
        prices, conditions = parse(spec, json.dumps(body).encode())
        assert keyed(conditions, prices[0])["endpoint_status"]["status"] == "unknown"
    with pytest.raises(ValueError):
        L.parse_openrouter(OR_SPEC, or_body(status=-4))


def test_openrouter_cache_write_ttl_reason_is_provider_dependent_not_undocumented():
    body = json.loads(or_body())
    body["data"]["endpoints"][0]["pricing"]["input_cache_write"] = "0.000001"
    prices, conditions = L.parse_openrouter(OR_SPEC, json.dumps(body).encode())
    write = next(p for p in prices if p["billing_item"] == "cache_write")
    row = keyed(conditions, write)["cache_write_ttl"]
    assert row["status"] == "unknown" and "provider dependent" in row["value"] and "names no TTL" not in row["value"]


ENDPOINT_FIXTURES = Path(__file__).parent / "fixtures" / "endpoints"


def real(name):
    """A real endpoint response (tests/market_collection/llm/fixtures/endpoints/provenance.json) and its registered spec."""
    record = next(p for p in json.loads((ENDPOINT_FIXTURES / "provenance.json").read_text()) if p["fixture"] == name)
    body = (ENDPOINT_FIXTURES / name).read_bytes()
    assert hashlib.sha256(body).hexdigest() == record["raw_sha256"] and len(body) == record["raw_bytes"]
    spec = next(s for s in source_specs() if s.source_key == record["source_key"])
    assert spec.url == record["source_url"]
    return spec, _validate_parse(spec, "s", *parse_source(spec, body, "s"))


def test_real_openrouter_day_and_hour_overrides_stay_separate_conditions():
    """deepseek-v4-pro-0813, 2026-10-28: DeepSeek's own endpoint prices weekends and weekday UTC hours by utc_days overrides."""
    _, (prices, conditions) = real("openrouter-deepseek-v4-pro-0813.json")
    groups = by_set(conditions)
    windows = {}
    for p in prices:
        group = groups[p["condition_set_id"]]
        if p["upstream_provider"] == "DeepSeek" and p["billing_item"] == "input" and "utc_days" in group:
            windows[(group["utc_days"]["value"], group.get("utc_start", {}).get("value"), group.get("utc_end", {}).get("value"))] = p["amount"]
    weekdays = "monday,tuesday,wednesday,thursday,friday"
    assert windows == {("saturday,sunday", None, None): "0.66", (weekdays, "0", "100"): "0.66", (weekdays, "100", "400"): "1.32",
                       (weekdays, "400", "600"): "0.66", (weekdays, "600", "1000"): "1.32", (weekdays, "1000", "0"): "0.66"}
    base = [p for p in prices if p["upstream_provider"] == "DeepSeek" and p["billing_item"] == "input" and "override" not in p["condition_set_id"]]
    assert [p["amount"] for p in base] == ["1.32"] and "utc_days" not in groups[base[0]["condition_set_id"]]
    # A non-zero discount stays a raw condition; whether the listed price is before or after it is unknown.
    novita = next(p for p in prices if p["upstream_provider"] == "Novita" and p["billing_item"] == "input")
    assert novita["amount"] == "0.99" and groups[novita["condition_set_id"]]["endpoint_discount"]["value"] == "0.25"
    assert groups[novita["condition_set_id"]]["discount_application"]["status"] == "unknown"


def test_real_vercel_per_call_search_fields_and_tier_prices_are_not_token_rows():
    """grok-4.3, 2026-10-28: xai also lists web_search, x_search_post/x_search_user per call and a priority price in service_tiers."""
    _, (prices, conditions) = real("vercel-grok-4.3.json")
    assert {p["billing_item"] for p in prices} == {"input", "output", "cache_read"}
    xai = [p for p in prices if p["upstream_provider"] == "xai"]
    assert sorted(p["amount"] for p in xai if p["billing_item"] == "input") == ["1.25", "2.5"]  # the two context tiers only
    groups = by_set(conditions)
    assert all(groups[p["condition_set_id"]]["service_tier"]["status"] == "unknown" for p in prices)
    assert all(groups[p["condition_set_id"]]["service_tier_applicability"]["status"] == "unknown" for p in xai)


@pytest.mark.parametrize("name,providers,context", [("vercel-text-embedding-3-small.json", ("azure", "openai"), "0"),
                                                      ("openrouter-text-embedding-3-small.json", ("Azure", "OpenAI"), "8192")])
def test_real_embedding_listings_keep_the_prompt_rate_beside_the_gateways_zero_completion(name, providers, context):
    """text-embedding-3-small, 2026-10-28: input-only pricing. Both gateways list completion "0"; Vercel lists context_length 0.
    Each is kept as written, and the sources read it as task "embedding"."""
    spec, (prices, conditions) = real(name)
    assert spec.task == "embedding" and {p["task"] for p in prices} == {"embedding"} and {p["unit"] for p in prices} == {"1M tokens"}
    assert {(p["upstream_provider"], p["billing_item"], p["amount"]) for p in prices} == {
        (provider, item, amount) for provider in providers for item, amount in (("input", "0.02"), ("output", "0"))}
    groups = by_set(conditions)
    assert {groups[p["condition_set_id"]]["context_length"]["value"] for p in prices} == {context}


def test_real_vercel_endpoint_with_nonzero_status_keeps_prices_but_status_unknown():
    """minimax-m2, 2026-10-28: the only upstream reports status 1 (and a deprecated_at, which the parser does not read)."""
    _, (prices, conditions) = real("vercel-minimax-m2.json")
    assert {(p["billing_item"], p["amount"]) for p in prices} == {("input", "0.3"), ("output", "1.2"), ("cache_read", "0.03"), ("cache_write", "0.375")}
    assert {(r["value"], r["status"]) for r in conditions if r["condition_key"] == "endpoint_status"} == {("1", "unknown")}
