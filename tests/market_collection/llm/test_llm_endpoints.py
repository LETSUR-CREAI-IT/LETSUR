import copy
import json

import pytest

from scripts.market_collection.collectors import llm_endpoints as L
from scripts.market_collection.core import SourceSpec, _validate_parse
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


def test_registry_adds_36_distinct_sources_for_18_models_after_existing_ones():
    specs = llm_endpoint_source_specs()
    assert len(specs) == 36 and len({s.source_key for s in specs}) == 36
    assert len({s.model_id for s in specs}) == 18
    assert source_specs()[-43:-7] == specs  # the seven Azure retail GPT specs (2026-10-28) come after them
    assert {s.route for s in specs} == {"openrouter", "vercel_ai_gateway"}


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
