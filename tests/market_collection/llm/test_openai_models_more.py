"""OpenAI's o3-pro page (2026-10-30), read by openai_models_more.py with openai_models.py's Text tokens reading. o3-pro is a row of
OPENAI_DIRECT_MORE_MODELS, not of OPENAI_DIRECT_MODELS, so the sources openai_models.py parses keep their parser fingerprints. The
fixture is the page as served (fixtures/direct/provenance.json, checked in test_direct_makers.py)."""
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.collectors import openai_models, openai_models_more, parse_source, parser_module
from scripts.market_collection.dashboard import compare
from scripts.market_collection.events import evidence
from scripts.market_collection.events.catalog import Catalog
from scripts.market_collection.events.engine import Builder
from scripts.market_collection.events.evidence import parser_fingerprint
from scripts.market_collection.models import OPENAI_DIRECT_MODELS
from scripts.market_collection.sources import maker_direct_source_specs, route_gap_source_specs, source_specs

F = Path(__file__).parent / "fixtures" / "direct"
BODY = (F / "openai-o3-pro.md").read_bytes()
SPEC = next(s for s in source_specs() if s.source_key == "openai_o3_pro")


def parse(body=BODY):
    result = parse_source(SPEC, body, "test-source")
    core._validate_parse(SPEC, "test-source", *result)
    return result


def edit(old, new):
    text = BODY.decode()
    assert text.count(old) == 1, old
    return text.replace(old, new).encode()


def test_one_more_source_on_openais_route_with_a_module_of_its_own():
    assert (SPEC.modality, SPEC.model_id, SPEC.route, SPEC.task, SPEC.parser) == ("llm", "o3-pro", "openai_direct", "text_generation", "openai_o3_pro")
    assert SPEC.url == "https://developers.openai.com/api/docs/models/o3-pro.md" and SPEC in route_gap_source_specs()
    assert SPEC not in maker_direct_source_specs() and "o3-pro" not in {row[0] for row in OPENAI_DIRECT_MODELS}
    assert parser_module(SPEC) is openai_models_more and SPEC.parser not in openai_models.PARSERS
    older = next(s for s in maker_direct_source_specs() if s.model_id == "gpt-5.5-pro")
    assert parser_module(older) is openai_models and parser_fingerprint(asdict(older)) != parser_fingerprint(asdict(SPEC))
    info = Catalog().price_source(asdict(SPEC))
    assert (info["role"], info["authority"]) == ("pricing", "manufacturer_official")


def test_the_page_gives_input_and_output_at_the_standard_tier():
    prices, conditions = parse()
    assert [(p["billing_item"], p["amount"], p["unit"]) for p in prices] == [("input", "20", "1M tokens"), ("output", "80", "1M tokens")]
    assert all((p["provider_model_id"], p["upstream_provider"], p["task"], p["price_kind"], p["evidence_status"]) ==
               ("o3-pro", "OpenAI", "text_generation", "published_rate", "direct") for p in prices)
    assert [(c["condition_key"], c["value"]) for c in conditions] == [("service_tier", "standard")] * 2  # no 272K rule, no promotion
    assert [p["locator"] for p in prices] == ["o3-pro > Pricing > Text tokens > Input: $20 / 1M tokens", "o3-pro > Pricing > Text tokens > Output: $80 / 1M tokens"]


@pytest.mark.parametrize("old,new", [
    ("# o3-pro\n", "# o3\n"),
    ("Model ID: `o3-pro`", "Model ID: `o3`"),
    ("Model ID: `o3-pro`", "Model ID: `o3-pro`\nModel ID: `o3`"),
    ("| Input | $20 | 1M tokens |", "| Input | $20 | 1K tokens |"),
    ("| Input | $20 | 1M tokens |", "| Input | 20 USD | 1M tokens |"),
    ("| Output | $80 | 1M tokens |\n", ""),
    ("| Input | $20 | 1M tokens |", "| Input | $20 | 1M tokens |\n| Input | $21 | 1M tokens |"),
    ("### Text tokens", "### Other tokens"),
    ("## Pricing\n", "## Prices\n"),
], ids=["h1", "model_id", "second_model_id", "input_unit", "input_cell", "output_missing", "input_twice", "section_renamed", "pricing_renamed"])
def test_identity_units_and_rows_fail_closed(old, new):
    with pytest.raises(ValueError):
        parse(edit(old, new))


def test_another_openai_page_is_not_read_as_o3_pro():
    with pytest.raises(ValueError):
        parse((F / "openai-gpt-5.5-pro.md").read_bytes())


def test_runs_read_back_as_baselines_and_one_openai_row(tmp_path):
    _, adapters = core.registry()
    runs = [core.live(tmp_path, specs=[SPEC], adapters=adapters, fetcher=lambda s: (BODY, s.url, 200)) for _ in range(2)]
    builder = Builder(Catalog())
    builder.build([evidence.load_run(run) for run in runs])
    assert builder.state["events"] == {} and builder.state["not_comparable"] == [] and len(builder.state["baselines"]) == 2
    groups = dict(compare.tables(tmp_path, ["o3-pro"], [], [], None, None, None, "2026-10-30")["o3-pro"]["groups"])
    assert list(groups) == ["비공개 · Standard"] and [(row["w"], [cell[0] for cell in row["c"]]) for row in groups["비공개 · Standard"]] == [
        (["OpenAI", "openai_direct"], ["$20.00", "$80.00"])]
