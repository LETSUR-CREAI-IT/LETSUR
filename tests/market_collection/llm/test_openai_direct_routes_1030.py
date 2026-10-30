"""OpenAI's own route for the seven models LETSUR was seen sending there beside Azure on 2026-10-30 (letsur_routes.py, 16
samples, Responses answers carrying OpenAI's billing). Six are read from their model pages by openai_models_more.py; o3's page
lists two Text tokens tables without a tier name, so its Standard price comes from the pricing page (openai_pricing_standard.py).
Fixtures are the pages as served (fixtures/direct/provenance.json, checked in test_direct_makers.py)."""
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.collectors import openai_models_more, openai_pricing_standard, parse_source, parser_module
from scripts.market_collection.events.catalog import Catalog
from scripts.market_collection.sources import route_gap_source_specs, source_specs

F = Path(__file__).parent / "fixtures" / "direct"
SPECS = {s.model_id: s for s in route_gap_source_specs() if s.route == "openai_direct"}
PAGES = {  # model: (input, cached input, output) per 1M tokens on its page, 10/30
    "gpt-4.1": ("2", "0.5", "8"), "gpt-4.1-mini": ("0.4", "0.1", "1.6"), "gpt-4o": ("2.5", "1.25", "10"),
    "gpt-4o-mini": ("0.15", "0.075", "0.6"), "gpt-5": ("1.25", "0.125", "10"), "gpt-5-mini": ("0.25", "0.025", "2"),
}
PRICING = (F / "openai-pricing.md").read_bytes()


def parse(model, body):
    spec = SPECS[model]
    prices, conditions = parse_source(spec, body, "test-source")
    core._validate_parse(spec, "test-source", prices, conditions)
    return {price["billing_item"]: price["amount"] for price in prices}, conditions


def test_the_seven_sources_are_on_openais_route_and_priced():
    assert set(SPECS) == {*PAGES, "o3", "o3-pro"} and all(spec in source_specs() for spec in SPECS.values())
    catalog = Catalog()
    for model, spec in SPECS.items():
        assert (spec.modality, spec.task, spec.source_key) == ("llm", "text_generation", "openai_" + model.replace("-", "_").replace(".", "_"))
        assert parser_module(spec) is (openai_pricing_standard if model == "o3" else openai_models_more)
        assert catalog.price_source(core_spec(spec))["role"] == "pricing"
    assert SPECS["o3"].url == "https://developers.openai.com/api/docs/pricing.md"


def core_spec(spec):
    from dataclasses import asdict
    return asdict(spec)


@pytest.mark.parametrize("model", sorted(PAGES))
def test_each_model_page_gives_its_standard_text_token_rates(model):
    rates, conditions = parse(model, (F / f"openai-{model}.md").read_bytes())
    assert (rates["input"], rates["cache_read"], rates["output"]) == PAGES[model]
    assert {c["value"] for c in conditions if c["condition_key"] == "service_tier"} == {"standard"}


def test_o3_is_read_from_the_pricing_pages_standard_table():
    rates, conditions = parse("o3", PRICING)
    assert rates == {"input": "2.00", "output": "8.00", "cache_read": "0.50"}
    assert all(c["value"] == "standard" for c in conditions if c["condition_key"] == "service_tier")


@pytest.mark.parametrize(("change", "error"), [
    (lambda text: text.replace("| o3 | $2.00 | $8.00 | $0.50 |\n", ""), "0 rows for o3"),
    (lambda text: text.replace("| o3 | $2.00 | $8.00 | $0.50 |\n", "| o3 | $2.00 | $8.00 | $0.50 |\n| o3 | $3.00 | $9.00 | $0.60 |\n"), "2 rows for o3"),
    (lambda text: text.replace("| o3 | $2.00 | $8.00 | $0.50 |", "| o3 | 2.00 USD | $8.00 | $0.50 |"), "not a dollar amount"),
    (lambda text: text.replace("### Standard pricing data", "### Standard data"), "no Standard pricing data section"),
])
def test_o3_fails_closed_when_the_table_changes(change, error):
    with pytest.raises(ValueError, match=error):
        parse("o3", change(PRICING.decode()).encode())
