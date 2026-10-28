"""Azure Retail Prices API GPT meters (2026-10-28): the trimmed real koreacentral response, fail-closed meter checks, and
the run read back into events and the 공급사 비교 표."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.collectors import parse_source
from scripts.market_collection.dashboard import compare
from scripts.market_collection.events import evidence
from scripts.market_collection.events.catalog import Catalog
from scripts.market_collection.events.engine import Builder
from scripts.market_collection.sources import source_specs
from tests.market_collection.retired_sources import RETIRED_SOURCE_KEYS

F = Path(__file__).parent / "fixtures" / "azure_retail"
BODY = (F / "gpt-koreacentral.json").read_bytes()
SPECS = {s.model_id: s for s in source_specs() if s.parser == "azure_llm_retail"}
# Global Standard short-context rates of the fixture; each equals OpenAI direct's 10/28 rate for the same item.
EXPECTED = {
    "gpt-6-astra": {"input": "10", "cache_read": "1", "cache_write": "12.5", "output": "50"},
    "gpt-6-sol": {"input": "2", "cache_read": "0.2", "cache_write": "2.5", "output": "10"},
    "gpt-6.1-sol": {"input": "2", "cache_read": "0.1", "cache_write": "2.5", "output": "10"},
    "gpt-6-luna": {"input": "0.1", "cache_read": "0.01", "cache_write": "0.125", "output": "0.5"},
    "gpt-5.6-sol": {"input": "4", "cache_read": "0.4", "output": "20"},
    "gpt-5.6-terra": {"input": "2", "cache_read": "0.2", "output": "12"},
    "gpt-5.6-luna": {"input": "0.2", "cache_read": "0.02", "output": "1.2"},
}


def parse(model, body=BODY):
    spec = SPECS[model]
    result = parse_source(spec, body, "test-source")
    core._validate_parse(spec, "test-source", *result)
    return result


def changed(mutate):
    data = json.loads(BODY)
    mutate(data)
    return json.dumps(data).encode()


def meter(data, name):
    return next(row for row in data["Items"] if row["meterName"] == name)


def test_seven_specs_share_one_fixed_page_and_replace_the_catalog_pages():
    assert list(SPECS) == list(EXPECTED)
    start = source_specs().index(next(iter(SPECS.values())))
    assert source_specs()[start:start + 7] == list(SPECS.values())
    assert {(s.route, s.task, s.url) for s in SPECS.values()} == {("azure_foundry", "text_generation", SPECS["gpt-6-sol"].url)}
    assert "armRegionName%20eq%20'koreacentral'" in SPECS["gpt-6-sol"].url
    old = {"azure_" + s.source_key.removeprefix("azure_retail_") for s in SPECS.values()}
    assert old <= RETIRED_SOURCE_KEYS and not old & {s.source_key for s in source_specs()}
    assert sum(s.route == "azure_foundry" and s.model_id == "gpt-6-sol" for s in source_specs()) == 1
    p = json.loads((F / "provenance.json").read_text())[0]
    assert hashlib.sha256(BODY).hexdigest() == p["fixture_sha256"] and p["source_url"] == SPECS["gpt-6-sol"].url


@pytest.mark.parametrize("model", list(EXPECTED))
def test_exact_global_standard_short_context_rates(model):
    prices, conditions = parse(model)
    assert [(p["billing_item"], p["amount"]) for p in prices] == list(EXPECTED[model].items())
    assert all(p["currency"] == "USD" and p["unit"] == "1M tokens" and p["upstream_provider"] == "OpenAI"
               and p["provider_model_id"] == model and p["evidence_status"] == "direct" and "Std Gl 1M Tokens" in p["locator"]
               for p in prices)
    by_set = {}
    for c in conditions:
        assert c["status"] == "stated"
        by_set.setdefault(c["condition_set_id"], {})[c["condition_key"]] = c["value"]
    assert {p["condition_set_id"] for p in prices} == set(by_set)
    assert all(group == {"service_tier": "standard", "meter_scope": "Global (Gl)", "context_band": "short context (ShortCo)",
                         "price_basis": "Azure retailPrice; Consumption; koreacentral"} for group in by_set.values())


def test_other_deployments_tiers_contexts_and_models_are_not_read():
    def bump(data):
        for row in data["Items"]:
            name, row["unitPrice"] = row["meterName"], 999
            if "ShortCo" not in name or "Std Gl" not in name or (name.startswith("5.6") and " Cd Wr " in name):
                row["retailPrice"] = 999
    for model in EXPECTED:
        assert parse(model, changed(bump)) == parse(model)


@pytest.mark.parametrize("mutate", [
    lambda d: d["Items"].remove(meter(d, "6-sol ShortCo Opt Std Gl 1M Tokens")),
    lambda d: d["Items"].append(dict(meter(d, "6-sol ShortCo Inp Std Gl 1M Tokens"))),
    lambda d: d["Items"].append(dict(meter(d, "6-sol ShortCo Inp Std Gl 1M Tokens"), meterId="another")),
    lambda d: meter(d, "6-sol ShortCo Inp Std Gl 1M Tokens").update(unitOfMeasure="1K"),
    lambda d: meter(d, "6-sol ShortCo Inp Std Gl 1M Tokens").update(meterName="6-sol ShortCo Inp Std Gl 1K Tokens"),
    lambda d: meter(d, "6-sol ShortCo Inp Std Gl 1M Tokens").update(skuName="6-sol ShortCo Inp Std DZ"),
    lambda d: d.update(NextPageLink="https://prices.azure.com/api/retail/prices?$skip=1000"),
    lambda d: d.pop("NextPageLink"),
    lambda d: d.update(BillingCurrency="EUR"),
], ids=["missing", "duplicate", "duplicate_sku", "unit", "unit_name", "sku", "next_page", "no_page_field", "currency"])
def test_missing_duplicate_unit_and_page_fail_closed(mutate):
    with pytest.raises(ValueError):
        parse("gpt-6-sol", changed(mutate))


def test_a_missing_meter_of_one_model_leaves_the_others_readable():
    body = changed(lambda d: d["Items"].remove(meter(d, "6-sol ShortCo Opt Std Gl 1M Tokens")))
    for model in EXPECTED:
        if model != "gpt-6-sol":  # locators keep the item index, which the removal shifts
            assert [(p["billing_item"], p["amount"]) for p in parse(model, body)[0]] == list(EXPECTED[model].items())


@pytest.mark.parametrize("field,value", [("currencyCode", "KRW"), ("armRegionName", "eastus"), ("type", "Reservation"),
    ("productName", "Azure OpenAI GPT5"), ("serviceName", "Azure OpenAI"), ("tierMinimumUnits", 100), ("meterId", ""),
    ("retailPrice", -1), ("retailPrice", "2.0"), ("retailPrice", None), ("retailPrice", True), ("retailPrice", float("nan"))])
def test_wrong_identity_region_type_or_rate_fail_closed(field, value):
    with pytest.raises(ValueError):
        parse("gpt-6-sol", changed(lambda d: meter(d, "6-sol ShortCo Cd Inp Std Gl 1M Tokens").update({field: value})))


def test_first_numeric_rows_are_baselines_and_one_compared_row_per_model(tmp_path):
    _, adapters = core.registry()
    runs = [core.live(tmp_path, specs=list(SPECS.values()), adapters=adapters, fetcher=lambda s: (BODY, s.url, 200)) for _ in range(2)]
    builder = Builder(Catalog())
    builder.build([evidence.load_run(run) for run in runs])
    assert builder.state["events"] == {} and builder.state["not_comparable"] == []
    assert len(builder.state["baselines"]) == sum(map(len, EXPECTED.values()))
    tables = compare.tables(tmp_path, list(EXPECTED), [], [], None, None, None, "2026-10-28")
    for model, rates in EXPECTED.items():
        rows = [(name, row) for name, members in tables[model]["groups"] for row in members] + [("ex", r) for r in tables[model]["ex"]]
        assert [(name, row["w"], row["n"]) for name, row in rows] == [
            ("비공개 · Standard", ["Microsoft", "azure_foundry"], "글로벌 배포 기준(렛서 배포 방식 미확인)")]
        assert [cell[0] for cell in rows[0][1]["c"]] == [compare.money(compare.number(rates[i])) for i in ("input", "output", "cache_read")]
