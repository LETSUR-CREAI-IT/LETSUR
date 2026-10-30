"""AWS Bedrock model card of gpt-6.1-terra, which LETSUR started to call there (2026-10-30), read by its own collector module.

The fixture is a trimmed projection of the real card (provenance in fixtures/bedrock/provenance.json); the other cases change
single cells of it.
"""

from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.market_collection import sources
from scripts.market_collection.adapters import llm
from scripts.market_collection.collectors import aws_bedrock, aws_bedrock_models, parser_module
from scripts.market_collection.core import _validate_parse, validate_registry
from scripts.market_collection.events.evidence import parser_fingerprint

CARD = (Path(__file__).parent / "fixtures" / "bedrock" / "latest-gpt_6_1_terra.html").read_text(encoding="utf-8")
SPEC = next(spec for spec in sources.bedrock_more_source_specs() if spec.model_id == "gpt-6.1-terra")


def parse(body: str, spec=SPEC):
    rows = llm.parse_source(spec, body.encode(), "source-1")
    _validate_parse(spec, "source-1", *rows)
    return rows


def test_the_card_is_one_more_registered_source_and_the_older_bedrock_sources_are_untouched():
    assert [(s.source_key, s.model_id, s.route, s.modality, s.task, s.parser) for s in sources.bedrock_more_source_specs()] == [
        ("bedrock_gpt_6_1_terra", "gpt-6.1-terra", "aws_bedrock", "llm", "text_generation", "cloud_bedrock_gpt_6_1_terra")]
    assert SPEC.url == "https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-6-1-terra.html"
    registry = sources.source_specs()
    validate_registry(registry)
    assert SPEC in registry and SPEC not in sources.cloud_source_specs() and len(sources.cloud_source_specs()) == 14
    assert parser_module(SPEC) is aws_bedrock_models
    assert all(parser_module(spec) is aws_bedrock for spec in sources.cloud_source_specs() if spec.route == "aws_bedrock")


def test_the_new_module_has_a_fingerprint_of_its_own_and_the_older_bedrock_ones_stay_apart_from_it():
    old = next(spec for spec in sources.cloud_source_specs() if spec.model_id == "gpt-6.1-sol")
    assert len({parser_fingerprint(asdict(old)), parser_fingerprint(asdict(SPEC))} - {None}) == 2


def test_the_real_card_gives_every_standard_rate_of_every_option_and_context_band():
    prices, conditions = parse(CARD)
    assert len(prices) == 24 and {p["provider_model_id"] for p in prices} == {"openai.gpt-6.1-terra"} and {p["unit"] for p in prices} == {"1M tokens"}
    rates = {}
    for price in prices:
        rate = {c["condition_key"]: c["value"] for c in conditions if c["condition_set_id"] == price["condition_set_id"]}
        rates[(rate["context_band"], rate["inference_option"], price["billing_item"])] = price["amount"]
    short, long = "272K input tokens or fewer", "more than 272K input tokens"
    assert rates[(short, "In-Region", "input")] == "1.10" and rates[(short, "In-Region", "cache_write")] == "1.375"
    assert rates[(short, "Geo CRIS", "cache_read")] == "0.055" and rates[(short, "Geo CRIS", "output")] == "5.50"
    assert [rates[(short, "Global CRIS", item)] for item in ("input", "cache_write", "cache_read", "output")] == ["1.00", "1.25", "0.05", "5.00"]
    assert [rates[(long, "Global CRIS", item)] for item in ("input", "cache_write", "cache_read", "output")] == ["2.00", "2.50", "0.10", "7.50"]
    assert rates[(long, "In-Region", "output")] == "8.25"
    assert {c["value"] for c in conditions if c["condition_key"] == "inference_option"} == {"In-Region", "Geo CRIS", "Global CRIS"}
    assert {c["value"] for c in conditions if c["condition_key"] == "service_tier"} == {"Standard"}
    assert {c["value"] for c in conditions if c["condition_key"] == "region_group"} == {"Commercial Regions"}
    assert [c["value"] for c in conditions if c["condition_key"] == "cache_write_ttl"] == ["30m"] * 6


def test_a_card_for_another_model_or_without_the_price_unit_gives_nothing():
    assert parse(CARD.replace("openai.gpt-6.1-terra", "openai.gpt-6.1-sol")) == ([], [])  # the Model ID cell is the identity
    assert parse(CARD.replace("<h1>GPT-6.1 Terra</h1>", "<h1>GPT-6.1 Sol</h1>")) == ([], [])
    assert parse(CARD.replace("USD per 1 million tokens", "USD per request")) == ([], [])


def test_an_unknown_option_or_a_missing_cell_leaves_that_row_out_only():
    prices, _ = parse(CARD.replace("<td>Geo CRIS</td>", "<td>Somewhere CRIS</td>"))
    assert len(prices) == 16
    prices, _ = parse(CARD.replace("<td>$1.375</td>", "<td>Contact sales</td>", 1))
    assert len(prices) == 20


def test_pricing_tables_without_a_single_parseable_price_raise():
    with pytest.raises(ValueError, match="scoped Standard Commercial pricing"):
        parse(CARD.replace("<td>$", "<td>Contact sales "))


@pytest.mark.parametrize("old, new", [("<th>Input — 30-minute cache write</th>", "<th>Input — maybe cache write</th>"),
                                      ("<th>Inference option</th>", "<th>Option</th>")])
def test_a_reformatted_header_row_is_not_guessed_at(old, new):
    with pytest.raises(ValueError, match="scoped Standard Commercial pricing"):
        parse(CARD.replace(old, new))
