"""AWS Price List API offer file of Bedrock in us-east-1 (2026-10-30), read by aws_bedrock_pricelist.py for GLM 4.7 and GLM 5, whose
Bedrock model cards state no price. The fixture keeps the real file's 48 GLM products and their terms (provenance.json); the other
cases change single fields of it."""
import copy
import hashlib
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.collectors import aws_bedrock, aws_bedrock_pricelist, parse_source, parser_module
from scripts.market_collection.dashboard import compare
from scripts.market_collection.events import evidence
from scripts.market_collection.events.catalog import Catalog, source_role
from scripts.market_collection.events.engine import Builder
from scripts.market_collection.events.evidence import parser_fingerprint
from scripts.market_collection.sources import cloud_source_specs, route_gap_source_specs, source_specs

F = Path(__file__).parent / "fixtures" / "bedrock_pricelist"
BODY = (F / "us-east-1-glm.json").read_bytes()
SPECS = {s.model_id: s for s in source_specs() if s.parser == "aws_bedrock_pricelist_api"}
URL = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/current/us-east-1/index.json"
# Per 1M tokens from the file's per-1K rates (0.0006/0.0022 and 0.0010/0.0032); Z.ai's own page has the same (test_zai.py).
EXPECTED = {"glm-4.7": {"input": "0.6", "output": "2.2"}, "glm-5": {"input": "1", "output": "3.2"}}
STEM = {"glm-4.7": "zai.glm-4.7", "glm-5": "zai.glm5"}


def parse(model, body=BODY):
    spec = SPECS[model]
    result = parse_source(spec, body, "test-source")
    core._validate_parse(spec, "test-source", *result)
    return result


def changed(mutate):
    data = json.loads(BODY)
    mutate(data)
    return json.dumps(data).encode()


def sku(data, usagetype):
    return next(s for s, p in data["products"].items() if p["attributes"]["usagetype"] == usagetype)


def dimension(data, usagetype):
    (term,) = data["terms"]["OnDemand"][sku(data, usagetype)].values()
    (found,) = term["priceDimensions"].values()
    return found


def test_two_specs_read_one_fixed_offer_file_as_api_sources():
    assert [(s.source_key, s.model_id, s.route, s.task, s.url) for s in SPECS.values()] == [
        ("bedrock_pricelist_glm_4_7", "glm-4.7", "aws_bedrock", "text_generation", URL),
        ("bedrock_pricelist_glm_5", "glm-5", "aws_bedrock", "text_generation", URL)]
    assert list(SPECS.values()) == route_gap_source_specs()[3:5] and source_role("aws_bedrock_pricelist_api") == "api"
    assert all(parser_module(s) is aws_bedrock_pricelist for s in SPECS.values())
    card = next(s for s in cloud_source_specs() if s.route == "aws_bedrock")
    assert parser_module(card) is aws_bedrock and parser_fingerprint(asdict(card)) != parser_fingerprint(asdict(SPECS["glm-5"]))
    assert len(BODY) < core.MAX_BODY_BYTES
    (p,) = json.loads((F / "provenance.json").read_text())
    assert (p["source_url"], p["fixture_sha256"], p["publicationDate"]) == (URL, hashlib.sha256(BODY).hexdigest(), json.loads(BODY)["publicationDate"])
    assert p["raw_bytes"] < core.MAX_BODY_BYTES  # the whole file of 10/30 (about 1.5 MB) is under the fetch limit


@pytest.mark.parametrize("model", list(EXPECTED))
def test_standard_rates_per_1m_tokens_with_their_meter_and_conditions(model):
    prices, conditions = parse(model)
    assert [(p["billing_item"], p["amount"]) for p in prices] == list(EXPECTED[model].items())
    assert all((p["provider_model_id"], p["upstream_provider"], p["unit"], p["currency"], p["price_kind"], p["evidence_status"], p["effective_from"])
               == (model, "Z.ai", "1M tokens", "USD", "published_rate", "direct", "2026-10-01T00:00:00Z") for p in prices)
    by_set = {}
    for c in conditions:
        assert c["status"] == "stated"
        by_set.setdefault(c["condition_set_id"], {})[c["condition_key"]] = c["value"]
    assert {p["condition_set_id"] for p in prices} == set(by_set) and all(
        group == {"service_tier": "Standard", "region": "us-east-1", "price_basis": "AWS Price List API; OnDemand"} for group in by_set.values())
    for price, meter in zip(prices, ("input-tokens", "output-tokens")):
        assert f"usagetype=USE1-{STEM[model]}-{meter};" in price["locator"] and "regionCode=us-east-1" in price["locator"]
    assert [p["locator"].rsplit(": ", 1)[1] for p in prices] == [
        f"pricePerUnit USD {dimension(json.loads(BODY), f'USE1-{STEM[model]}-{meter}')['pricePerUnit']['USD']} per 1K tokens"
        for meter in ("input-tokens", "output-tokens")]


def test_letsurs_gateway_cost_of_a_glm_4_7_call_is_these_rates():
    rates = {p["billing_item"]: Decimal(p["amount"]) for p in parse("glm-4.7")[0]}
    assert (9 * rates["input"] + 2 * rates["output"]) / 1000000 == Decimal("0.0000098")  # 9 prompt + 2 completion tokens, 10/30


def test_other_tiers_the_mantle_endpoint_and_glm_4_7_flash_are_not_read():
    data = json.loads(BODY)
    read = {f"USE1-{stem}-{meter}" for stem in STEM.values() for meter in ("input-tokens", "output-tokens")}
    others = [p["attributes"]["usagetype"] for p in data["products"].values() if p["attributes"]["usagetype"] not in read]
    assert len(others) == 44 and all(any(part in u for part in ("-batch", "-flex", "-priority", "-mantle-", "glm-4.7-flash")) for u in others)

    def bump(data):
        for usagetype in others:
            dimension(data, usagetype)["pricePerUnit"]["USD"] = "9.9900000000"
    for model in EXPECTED:
        assert parse(model, changed(bump)) == parse(model)


@pytest.mark.parametrize("mutate", [
    lambda d: d["products"].pop(sku(d, "USE1-zai.glm5-input-tokens")),
    lambda d: d["products"].update(COPY=dict(copy.deepcopy(d["products"][sku(d, "USE1-zai.glm5-input-tokens")]), sku="COPY")),
    lambda d: d["products"][sku(d, "USE1-zai.glm5-input-tokens")]["attributes"].update(model="GLM 5.1"),
    lambda d: d["products"][sku(d, "USE1-zai.glm5-input-tokens")]["attributes"].update(regionCode="us-west-2"),
    lambda d: d["products"][sku(d, "USE1-zai.glm5-input-tokens")]["attributes"].update(provider="Someone"),
    lambda d: d["terms"]["OnDemand"].pop(sku(d, "USE1-zai.glm5-output-tokens")),
    lambda d: dimension(d, "USE1-zai.glm5-input-tokens").update(unit="1M tokens"),
    lambda d: dimension(d, "USE1-zai.glm5-input-tokens").update(unit="1k tokens"),
    lambda d: dimension(d, "USE1-zai.glm5-input-tokens").update(beginRange="1000000"),
    lambda d: dimension(d, "USE1-zai.glm5-input-tokens").update(pricePerUnit={"CNY": "0.0070000000"}),
    lambda d: dimension(d, "USE1-zai.glm5-input-tokens").update(pricePerUnit={"USD": "0.0010000000", "CNY": "0.0070000000"}),
    lambda d: dimension(d, "USE1-zai.glm5-input-tokens").update(pricePerUnit={"USD": "0.0000000000"}),
    lambda d: dimension(d, "USE1-zai.glm5-input-tokens").update(pricePerUnit={"USD": "-0.001"}),
    lambda d: dimension(d, "USE1-zai.glm5-input-tokens").update(pricePerUnit={"USD": "n/a"}),
    lambda d: dimension(d, "USE1-zai.glm5-input-tokens").update(pricePerUnit={"USD": 0.001}),
    lambda d: next(iter(d["terms"]["OnDemand"][sku(d, "USE1-zai.glm5-input-tokens")].values()))["priceDimensions"].update(
        SECOND=dict(dimension(d, "USE1-zai.glm5-input-tokens"))),
    lambda d: d["terms"]["OnDemand"][sku(d, "USE1-zai.glm5-input-tokens")].update(
        SECOND=next(iter(d["terms"]["OnDemand"][sku(d, "USE1-zai.glm5-input-tokens")].values()))),
], ids=["missing", "duplicate", "other_model", "other_region", "unknown_provider", "no_term", "unit_1m", "unit_lowercase", "volume_tier",
        "other_currency", "second_currency", "zero", "negative", "not_a_number", "float", "two_dimensions", "two_terms"])
def test_a_missing_doubled_or_changed_meter_fails_closed_for_its_model_only(mutate):
    with pytest.raises(ValueError):
        parse("glm-5", changed(mutate))
    assert parse("glm-4.7", changed(mutate)) == parse("glm-4.7")


@pytest.mark.parametrize("mutate", [lambda d: d.update(offerCode="AmazonSageMaker"), lambda d: d["terms"].pop("OnDemand"),
                                    lambda d: d.update(products=[])], ids=["other_offer", "no_on_demand", "products_not_a_map"])
def test_another_offer_file_fails_closed(mutate):
    for model in EXPECTED:
        with pytest.raises(ValueError):
            parse(model, changed(mutate))


def test_runs_read_back_as_baselines_and_one_bedrock_row_per_model(tmp_path):
    _, adapters = core.registry()
    runs = [core.live(tmp_path, specs=list(SPECS.values()), adapters=adapters, fetcher=lambda s: (BODY, s.url, 200)) for _ in range(2)]
    builder = Builder(Catalog())
    builder.build([evidence.load_run(run) for run in runs])
    assert builder.state["events"] == {} and builder.state["not_comparable"] == [] and len(builder.state["baselines"]) == 4
    tables = compare.tables(tmp_path, list(EXPECTED), [], [], None, None, None, "2026-10-30")
    for model, rates in EXPECTED.items():
        rows = [row for _, members in tables[model]["groups"] for row in members] + tables[model]["ex"]
        assert [row["w"] for row in rows] == [["AWS", "aws_bedrock"]]
        assert [cell[0] for cell in rows[0]["c"][:2]] == [compare.money(compare.number(rates[i])) for i in ("input", "output")]
