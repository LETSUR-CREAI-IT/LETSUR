"""Azure Retail Prices API meters of the other Azure OpenAI models LETSUR serves (2026-10-28): trimmed real koreacentral
responses of two fixed pages, 1K-token meters read as exact 1M-token rates, fail-closed meter checks, and the run read back."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.collectors import azure_retail_models, parse_source
from scripts.market_collection.dashboard import compare
from scripts.market_collection.events import evidence
from scripts.market_collection.events.catalog import Catalog, source_role
from scripts.market_collection.events.engine import Builder
from scripts.market_collection.sources import _AZURE_GPT_PRICING, _AZURE_OPENAI_PRICING, _key, source_specs

F = Path(__file__).parent / "fixtures" / "azure_retail"
BODIES = {_AZURE_GPT_PRICING: (F / "gpt5-koreacentral.json").read_bytes(),
          _AZURE_OPENAI_PRICING: (F / "openai-koreacentral.json").read_bytes()}
SPECS = {s.model_id: s for s in source_specs() if s.parser == "azure_models_retail"}
# Global Standard rates of the fixtures per 1M tokens; each input/output equals LETSUR's listed rate (10/28).
EXPECTED = {
    "gpt-5": {"input": "1.25", "cache_read": "0.125", "output": "10"},
    "gpt-5-mini": {"input": "0.25", "cache_read": "0.025", "output": "2"},
    "gpt-5-nano": {"input": "0.05", "cache_read": "0.005", "output": "0.4"},
    "gpt-5-codex": {"input": "1.25", "cache_read": "0.125", "output": "10"},
    "gpt-5.1": {"input": "1.25", "cache_read": "0.125", "output": "10"},
    "gpt-5.1-codex": {"input": "1.25", "cache_read": "0.125", "output": "10"},
    "gpt-5.1-codex-mini": {"input": "0.25", "cache_read": "0.025", "output": "2"},
    "gpt-5.2": {"input": "1.75", "cache_read": "0.175", "output": "14"},
    "gpt-5.4": {"input": "2.5", "cache_read": "0.25", "output": "15"},
    "gpt-5.4-2026-03-05": {"input": "2.5", "cache_read": "0.25", "output": "15"},
    "gpt-5.4-mini": {"input": "0.75", "cache_read": "0.075", "output": "4.5"},
    "gpt-5.4-nano": {"input": "0.2", "cache_read": "0.02", "output": "1.25"},
    "gpt-5.5": {"input": "5", "cache_read": "0.5", "output": "30"},
    "gpt-4o": {"input": "2.5", "cache_read": "1.25", "output": "10"},
    "gpt-4o-mini": {"input": "0.15", "cache_read": "0.075", "output": "0.6"},
    "gpt-4.1": {"input": "2", "cache_read": "0.5", "output": "8"},
    "gpt-4.1-mini": {"input": "0.4", "cache_read": "0.1", "output": "1.6"},
    "gpt-4.1-nano": {"input": "0.1", "cache_read": "0.025", "output": "0.4"},
    "o1": {"input": "15", "cache_read": "7.5", "output": "60"},
    "o3": {"input": "2", "cache_read": "0.5", "output": "8"},
    "o3-mini": {"input": "1.1", "cache_read": "0.55", "output": "4.4"},
    "o3-pro": {"input": "20", "output": "80"},
    "o4-mini": {"input": "1.1", "cache_read": "0.275", "output": "4.4"},
}
VERSION = {"gpt-4o": "2024-11-20", "gpt-4o-mini": "2024-07-18", "o1": "2024-12-17", "o3": "2025-04-16", "o3-mini": "2025-01-31",
           "o4-mini": "2025-04-16"}
BAND = {"gpt-5.4": "short context (meter without longco)", "gpt-5.4-2026-03-05": "short context (meter without longco)",
        "gpt-5.5": "short context (ShortCo)"}


def parse(model, body=None):
    spec = SPECS[model]
    result = parse_source(spec, BODIES[spec.url] if body is None else body, "test-source")
    core._validate_parse(spec, "test-source", *result)
    return result


def changed(model, mutate):
    data = json.loads(BODIES[SPECS[model].url])
    mutate(data)
    return json.dumps(data).encode()


def meter(data, name):
    return next(row for row in data["Items"] if row["meterName"] == name)


def test_twenty_three_specs_on_two_fixed_pages_after_the_existing_ones():
    assert list(SPECS) == list(EXPECTED) and set(azure_retail_models.METERS) == set(EXPECTED)
    start = source_specs().index(next(iter(SPECS.values())))
    assert source_specs()[start:start + 23] == list(SPECS.values())
    assert all(s.source_key == "azure_retail_" + _key(s.model_id) and (s.route, s.task) == ("azure_foundry", "text_generation")
               for s in SPECS.values())
    assert [s.url for s in SPECS.values()] == [_AZURE_GPT_PRICING] * 13 + [_AZURE_OPENAI_PRICING] * 10
    assert "armRegionName%20eq%20'koreacentral'" in _AZURE_OPENAI_PRICING and source_role("azure_models_retail") == "api"
    provenance = json.loads((F / "provenance.json").read_text())[1:]
    assert [(p["fixture"], p["source_url"]) for p in provenance] == [
        ("gpt5-koreacentral.json", _AZURE_GPT_PRICING), ("openai-koreacentral.json", _AZURE_OPENAI_PRICING)]
    assert all(hashlib.sha256((F / p["fixture"]).read_bytes()).hexdigest() == p["fixture_sha256"] for p in provenance)


def test_every_listed_meter_is_once_on_its_page():
    for model, (product, unit, _, _, skus) in azure_retail_models.METERS.items():
        items = json.loads(BODIES[SPECS[model].url])["Items"]
        for sku in skus.values():
            assert [(r["productName"], r["unitOfMeasure"]) for r in items if r["skuName"] == sku] == [(product, unit)], (model, sku)


@pytest.mark.parametrize("model", list(EXPECTED))
def test_exact_global_standard_rates_per_1m_tokens(model):
    prices, conditions = parse(model)
    assert [(p["billing_item"], p["amount"]) for p in prices] == list(EXPECTED[model].items())
    assert all(p["currency"] == "USD" and p["unit"] == "1M tokens" and p["upstream_provider"] == "OpenAI" and p["task"] == "text_generation"
               and p["provider_model_id"] == model and p["evidence_status"] == "direct" for p in prices)
    by_set = {}
    for c in conditions:
        assert c["status"] == "stated"
        by_set.setdefault(c["condition_set_id"], {})[c["condition_key"]] = c["value"]
    assert {p["condition_set_id"] for p in prices} == set(by_set)
    expected = {"service_tier": "standard", "meter_scope": "Global (Gl)", "price_basis": "Azure retailPrice; Consumption; koreacentral"}
    expected.update({"model_version": VERSION[model]} if model in VERSION else {})
    expected.update({"context_band": BAND[model]} if model in BAND else {})
    assert all(group == expected for group in by_set.values())


def test_locators_keep_the_quoted_rate_and_unit():
    tails = {model: [p["locator"].split("; ", 2)[2] for p in parse(model)[0]] for model in ("gpt-4.1-nano", "gpt-5.4")}
    assert tails == {
        "gpt-4.1-nano": ["meterName=gpt 4.1 nano Inp glbl Tokens; unitOfMeasure=1K; retailPrice=0.0001",
                         "meterName=gpt 4.1 nano cached Inp glbl Tokens; unitOfMeasure=1K; retailPrice=0.000025",
                         "meterName=gpt 4.1 nano Outp glbl Tokens; unitOfMeasure=1K; retailPrice=0.0004"],
        "gpt-5.4": ["meterName=5.4 inp Gl 1M Tokens; unitOfMeasure=1M; retailPrice=2.5",
                    "meterName=5.4 cd inp Gl 1M Tokens; unitOfMeasure=1M; retailPrice=0.25",
                    "meterName=5.4 opt Gl 1M Tokens; unitOfMeasure=1M; retailPrice=15.0"]}


def test_other_versions_deployments_tiers_contexts_and_models_are_not_read():
    listed = {sku for *_, skus in azure_retail_models.METERS.values() for sku in skus.values()}

    def bump(data):
        for row in data["Items"]:
            row["unitPrice"] = 999
            if row["skuName"] not in listed:
                row["retailPrice"] = 999
    for model in EXPECTED:
        assert parse(model, changed(model, bump)) == parse(model)


OTHER_UNIT = {"1K": ("1M", " 1M Tokens"), "1M": ("1K", " Tokens")}


def reformat(data, name):
    row = meter(data, name)
    unit, suffix = OTHER_UNIT[row["unitOfMeasure"]]
    row.update(unitOfMeasure=unit, meterName=row["skuName"] + suffix)


@pytest.mark.parametrize("model,name", [("gpt-4o", "gpt 4o 1120 Inp glbl Tokens"), ("gpt-5-codex", "gpt-5-codex-out-glbl Tokens"),
                                        ("gpt-5.4", "5.4 inp Gl 1M Tokens")])
@pytest.mark.parametrize("mutate", [
    lambda d, n: d["Items"].remove(meter(d, n)),
    lambda d, n: d["Items"].append(dict(meter(d, n))),
    lambda d, n: d["Items"].append(dict(meter(d, n), meterId="another")),
    lambda d, n: meter(d, n).update(unitOfMeasure=OTHER_UNIT[meter(d, n)["unitOfMeasure"]][0]),
    lambda d, n: meter(d, n).update(meterName=meter(d, n)["skuName"] + OTHER_UNIT[meter(d, n)["unitOfMeasure"]][1]),
    reformat,
    lambda d, n: meter(d, n).update(skuName=meter(d, n)["skuName"] + " DZ"),
    lambda d, n: d.update(NextPageLink="https://prices.azure.com/api/retail/prices?$skip=1000"),
    lambda d, n: d.pop("NextPageLink"),
    lambda d, n: d.update(BillingCurrency="EUR"),
], ids=["missing", "duplicate", "duplicate_sku", "unit", "unit_name", "reformatted_unit", "sku", "next_page", "no_page_field",
        "currency"])
def test_missing_duplicate_unit_and_page_fail_closed(model, name, mutate):
    with pytest.raises(ValueError):
        parse(model, changed(model, lambda d: mutate(d, name)))


def test_a_missing_meter_of_one_model_leaves_the_others_readable():
    body = changed("o3", lambda d: d["Items"].remove(meter(d, "o3 0416 Outp glbl Tokens")))
    for model in EXPECTED:
        if SPECS[model].url == _AZURE_OPENAI_PRICING and model != "o3":
            assert [(p["billing_item"], p["amount"]) for p in parse(model, body)[0]] == list(EXPECTED[model].items())


@pytest.mark.parametrize("field,value", [("currencyCode", "KRW"), ("armRegionName", "eastus"), ("type", "DevTestConsumption"),
    ("productName", "Azure OpenAI"), ("serviceName", "Azure OpenAI"), ("tierMinimumUnits", 100), ("meterId", ""),
    ("retailPrice", -1), ("retailPrice", "0.000275"), ("retailPrice", None), ("retailPrice", True), ("retailPrice", float("nan"))])
def test_wrong_identity_region_type_or_rate_fail_closed(field, value):
    with pytest.raises(ValueError):
        parse("o4-mini", changed("o4-mini", lambda d: meter(d, "o4-mini 0416 cached Inp glbl Tokens").update({field: value})))


def test_runs_read_back_as_baselines_and_one_azure_row_per_model(tmp_path):
    _, adapters = core.registry()
    runs = [core.live(tmp_path, specs=list(SPECS.values()), adapters=adapters, fetcher=lambda s: (BODIES[s.url], s.url, 200))
            for _ in range(2)]
    builder = Builder(Catalog())
    builder.build([evidence.load_run(run) for run in runs])
    assert builder.state["events"] == {} and builder.state["not_comparable"] == []
    assert len(builder.state["baselines"]) == sum(map(len, EXPECTED.values()))
    tables = compare.tables(tmp_path, list(EXPECTED), [], [], None, None, None, "2026-10-28")
    for model, rates in EXPECTED.items():
        rows = [row for _, members in tables[model]["groups"] for row in members] + tables[model]["ex"]
        assert [row["w"] for row in rows] == [["Microsoft", "azure_foundry"]]
        assert [cell[0] for cell in rows[0]["c"]] == [compare.money(compare.number(rates[i])) for i in ("input", "output", "cache_read")
                                                      if i in rates]
