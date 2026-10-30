"""An unstated serving precision on a gateway listing (2026-10-30, evidence.blocking_unknowns).

- a closed model's row that leaves only the precision unstated is compared (its provider serves the maker's own weights);
- a model with public weights (decision 24) still is not;
- two rows of one listing told apart only by an unstated precision cannot be paired run to run.
"""

from __future__ import annotations

import json

from scripts.market_collection import core
from scripts.market_collection.collectors.llm_endpoints import parse_openrouter
from scripts.market_collection.events import evidence
from scripts.market_collection.sources import source_specs

from test_catalog_and_diff import RUN_A, RUN_B, ok_build, write_run, _spec


def _key(model: str) -> str:
    return next(s.source_key for s in source_specs() if s.source_key == "openrouter_llm_endpoints_" + model.replace("-", "_").replace(".", "_"))


def entry(model: str, provider_id: str, prompts: list[str]) -> dict:
    """One OpenRouter listing with an endpoint per prompt price, all of the same provider and none stating a precision."""
    endpoints = [{"name": f"xAI | {provider_id}", "context_length": 262144, "provider_name": "xAI", "tag": "xai",
                  "service_tier": None, "quantization": None, "status": 0,
                  "pricing": {"prompt": prompt, "completion": "0.0000025", "discount": 0}} for prompt in prompts]
    spec = _spec(_key(model))
    raw = json.dumps({"data": {"id": provider_id, "endpoints": endpoints}}).encode()
    return {"spec": spec, "body": raw, "rows": parse_openrouter(core.SourceSpec(**spec), raw)}


def test_only_an_open_weight_model_needs_its_precision_stated():
    row = lambda model, unknown: {"model_id": model, "unknown_conditions": unknown}
    assert evidence.blocking_unknowns(row("grok-4.3", ["quantization"])) == []
    assert evidence.blocking_unknowns(row("grok-4.3", ["quantization", "service_tier"])) == ["service_tier"]
    assert evidence.blocking_unknowns(row("glm-5.2", ["quantization"])) == ["quantization"]
    assert evidence.open_weight("deepseek-v4-pro") and not evidence.open_weight("qwen3.7-max")


def test_a_closed_models_gateway_price_without_a_precision_gets_a_baseline_and_then_its_change(tmp_path):
    a = write_run(tmp_path, RUN_A, "2026-10-29T00:17:00Z", [entry("grok-4.3", "x-ai/grok-4.3", ["0.00000125"])])
    b = write_run(tmp_path, RUN_B, "2026-10-30T00:17:00Z", [entry("grok-4.3", "x-ai/grok-4.3", ["0.0000015"])])
    first = ok_build(tmp_path / "first", a)
    assert {(x["billing_item"], x["source_key"]) for x in first["baselines"]} >= {("input", _key("grok-4.3"))}
    assert not [n for n in first["not_comparable"] if n["source_key"] == _key("grok-4.3")]
    moved = ok_build(tmp_path / "moved", a, b)
    [event] = [e for e in moved["events"] if e["event_type"] == "price_change" and e["billing_item"] == "input"]
    assert (event["model_id"], event["before"]["amount"], event["after"]["amount"]) == ("grok-4.3", "1.25", "1.5")


def test_two_rows_told_apart_only_by_an_unstated_precision_are_not_paired(tmp_path):
    # The OpenRouter parser refuses a listing with a repeated endpoint tag, so the pair is made from one parsed endpoint: its rows
    # again under other condition set ids, with another price, are the same series as far as anything stated goes.
    one = entry("grok-4.3", "x-ai/grok-4.3", ["0.00000125"])
    prices, conditions = one["rows"]
    twin = {row["condition_set_id"]: row["condition_set_id"] + "-twin" for row in conditions}
    one["rows"] = (prices + [dict(row, amount="1" if row["billing_item"] == "input" else row["amount"], condition_set_id=twin[row["condition_set_id"]])
                             for row in prices if row["condition_set_id"] in twin],
                   conditions + [dict(row, condition_set_id=twin[row["condition_set_id"]]) for row in conditions])
    a = write_run(tmp_path, RUN_A, "2026-10-29T00:17:00Z", [one])
    built = ok_build(tmp_path / "built", a)
    reasons = {(n["reason"], n["billing_item"]) for n in built["not_comparable"] if n["source_key"] == _key("grok-4.3")}
    assert ("precision_pair_not_unique", "input") in reasons
    assert not [x for x in built["baselines"] if x["source_key"] == _key("grok-4.3") and x["billing_item"] == "input"]
