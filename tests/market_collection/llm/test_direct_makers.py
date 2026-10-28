"""Anthropic and OpenAI pages of the models LETSUR calls on the makers' own routes (2026-10-28): the trimmed real pages,
fail-closed row checks, and the registered sources run offline through core.live and read back into events and the 공급사 비교 표."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.collectors import parse_source
from scripts.market_collection.dashboard import compare
from scripts.market_collection.events import evidence
from scripts.market_collection.events.catalog import Catalog
from scripts.market_collection.events.engine import Builder
from scripts.market_collection.models import ANTHROPIC_DIRECT_MODELS
from scripts.market_collection.sources import maker_direct_source_specs, source_specs

F = Path(__file__).parent / "fixtures" / "direct"
SPECS = {s.model_id: s for s in maker_direct_source_specs() if s.modality == "llm"}
ANTHROPIC_PAGE = "anthropic-pricing.md"
PAGE = {"gpt-5.5-pro": "openai-gpt-5.5-pro.md", "gpt-5.2-pro": "openai-gpt-5.2-pro.md", "gpt-6.1-terra": "openai-gpt-6.1-terra.md",
        "gpt-5.5-pro-2026-04-23": "openai-gpt-5.5-pro.md", "gpt-5.5-2026-04-23": "openai-gpt-5.5.md",
        "gpt-5.6": "openai-gpt-5.6-sol.md",  # gpt-5.6.md redirects to the GPT-5.6 Sol page
        **{m: f"openai-{m}.md" for m in ("text-embedding-3-large", "text-embedding-3-small", "text-embedding-ada-002")}}
# input, 5m cache write, 1h cache write, cache hit, output (USD per 1M tokens) of the "Model pricing" rows
ANTHROPIC = {
    "claude-opus-4-6": ("5", "6.25", "10", "0.50", "25"),
    "claude-opus-5": ("5", "6.25", "10", "0.50", "25"),
    "claude-fable-5": ("10", "12.50", "20", "1", "50"),
    "claude-opus-4-8": ("5", "6.25", "10", "0.50", "25"),
    "claude-opus-4-7": ("5", "6.25", "10", "0.50", "25"),
    "claude-sonnet-4-6": ("3", "3.75", "6", "0.30", "15"),
    "claude-opus-4-5-20251101": ("5", "6.25", "10", "0.50", "25"),
    "claude-sonnet-4-5-20250929": ("3", "3.75", "6", "0.30", "15"),
}
ITEMS = ("input", "cache_write", "cache_write", "cache_read", "output")
HAIKU = "claude-haiku-5-5"
UP_TO, OVER = "Claude Haiku 5.5 (for prompts up to 100,000 tokens)", "Claude Haiku 5.5 (for prompts over 100,000 tokens)"
HAIKU_RATES = {UP_TO: ("0.10", "0.125", "0.20", "0.01", "0.50"), OVER: ("0.50", "0.625", "1", "0.05", "2.50")}
TITLES = {**dict(ANTHROPIC_DIRECT_MODELS), HAIKU: UP_TO}  # the row a plain edit of a model's price touches
OPENAI = {
    "gpt-5.5-pro": {"input": "30", "output": "180"},
    "gpt-5.2-pro": {"input": "21", "output": "168"},
    "gpt-6.1-terra": {"input": "1", "cache_read": "0.05", "cache_write": "1.25", "output": "5"},
    "gpt-5.5-pro-2026-04-23": {"input": "30", "output": "180"},
    "gpt-5.5-2026-04-23": {"input": "5", "cache_read": "0.5", "output": "30"},
    "gpt-5.6": {"input": "4", "cache_read": "0.4", "output": "20"},  # GPT-5.6 Sol's promotional rate; LETSUR lists 5/30
}
EMBEDDINGS = {"text-embedding-3-large": "0.13", "text-embedding-3-small": "0.02", "text-embedding-ada-002": "0.1"}


def raw(model):
    return (F / (PAGE.get(model) or ANTHROPIC_PAGE)).read_bytes()


def parse(model, content=None):
    spec = SPECS[model]
    result = parse_source(spec, raw(model) if content is None else content, "test-source")
    core._validate_parse(spec, "test-source", *result)
    return result


def edit(model, old, new, count=1):
    text = raw(model).decode()
    assert text.count(old) == count, old
    return text.replace(old, new).encode()


def edit_row(model, title, change):
    """The page with this table row changed ("" drops the row, line and all)."""
    line = next(line for line in raw(model).decode().splitlines() if line.startswith(f"| {title} "))
    new = change(line)
    return edit(model, line + "\n", new + "\n" if new else "")


def test_fixtures_are_the_recorded_pages():
    for record in json.loads((F / "provenance.json").read_text()):
        assert hashlib.sha256((F / record["fixture"]).read_bytes()).hexdigest() == record["fixture_sha256"]


def test_eighteen_llm_sources_follow_the_existing_ones_on_the_makers_routes():
    specs = maker_direct_source_specs()
    start = source_specs().index(specs[0])
    assert source_specs()[start:start + 19] == specs and len(SPECS) == 18 and specs[-1].model_id == "gpt-image-2"
    core.validate_registry(source_specs())  # no source key repeats an existing one
    catalog = Catalog()
    for spec in SPECS.values():
        assert spec.source_key == spec.parser == ("anthropic_" if spec.route == "anthropic_direct" else "openai_") + spec.model_id.replace("-", "_").replace(".", "_")
        assert catalog.registered(asdict(spec)) and catalog.price_source(asdict(spec))["role"] == "pricing"
    assert {(s.route, s.task, s.url) for s in SPECS.values() if s.route == "anthropic_direct"} == {
        ("anthropic_direct", "text_generation", "https://platform.claude.com/docs/en/about-claude/pricing.md")}
    assert {m: s.url.rsplit("/", 1)[1] for m, s in SPECS.items() if m in OPENAI} == {
        "gpt-5.5-pro": "gpt-5.5-pro.md", "gpt-5.2-pro": "gpt-5.2-pro.md", "gpt-6.1-terra": "gpt-6.1-terra.md",
        "gpt-5.5-pro-2026-04-23": "gpt-5.5-pro.md", "gpt-5.5-2026-04-23": "gpt-5.5.md", "gpt-5.6": "gpt-5.6.md"}
    assert {(s.route, s.task, s.url.rsplit("/", 1)[1]) for m, s in SPECS.items() if m in EMBEDDINGS} == {
        ("openai_direct", "embedding", f"{m}.md") for m in EMBEDDINGS}
    assert catalog.price_source(asdict(SPECS["claude-opus-5"]))["authority"] == catalog.price_source(asdict(SPECS["gpt-5.5-pro"]))["authority"] == "manufacturer_official"


@pytest.mark.parametrize("model", list(ANTHROPIC))
def test_anthropic_rows_are_exact_and_stay_standard(model):
    prices, conditions = parse(model)
    assert [(p["billing_item"], p["amount"]) for p in prices] == list(zip(ITEMS, ANTHROPIC[model]))
    assert all(p["provider_model_id"] == model and p["upstream_provider"] == "Anthropic" and p["unit"] == "1M tokens" and p["currency"] == "USD"
               and p["task"] == "text_generation" and p["price_kind"] == "published_rate" and f"Model pricing > {TITLES[model]} >" in p["locator"] for p in prices)
    assert {(c["condition_key"], c["value"]) for c in conditions} == {("service_tier", "standard"), ("cache_duration", "5m"), ("cache_duration", "1h")}


def test_haiku_5_5_keeps_both_prompt_length_rows_apart():
    prices, conditions = parse(HAIKU)
    assert [(p["billing_item"], p["amount"]) for p in prices] == [(item, amount) for rates in HAIKU_RATES.values() for item, amount in zip(ITEMS, rates)]
    assert len({p["condition_set_id"] for p in prices}) == 10
    bands = {c["condition_set_id"]: c["value"] for c in conditions if c["condition_key"] == "input_token_band"}
    assert [bands[p["condition_set_id"]] for p in prices] == ["<=100K"] * 5 + [">100K"] * 5
    assert all(f"Model pricing > {UP_TO} >" in p["locator"] for p in prices[:5]) and all(f"Model pricing > {OVER} >" in p["locator"] for p in prices[5:])


@pytest.mark.parametrize("model", list(ANTHROPIC) + [HAIKU])
def test_a_missing_row_is_never_filled_from_a_neighbour(model):
    with pytest.raises(ValueError):
        parse(model, edit_row(model, TITLES[model], lambda line: ""))


@pytest.mark.parametrize("change", [
    lambda line: line + "\n" + line,
    lambda line: line.replace("Claude Opus 4.6", "Claude Opus 4.6 (retired)", 1),
    lambda line: line.replace("$5 / MTok", "$5 / 1K tokens", 1),
    lambda line: line.replace("$25 / MTok", "$25", 1),
    lambda line: line.replace("$0.50 / MTok", "$0.50 / 1K tokens", 1),
    lambda line: line.replace("$6.25 / MTok", "n/a", 1),
], ids=["duplicate", "renamed", "input_unit", "output_unit", "cache_hit_unit", "cache_write_cell"])
def test_duplicate_renamed_or_reformatted_rows_fail_closed(change):
    with pytest.raises(ValueError):
        parse("claude-opus-4-6", edit_row("claude-opus-4-6", "Claude Opus 4.6", change))


@pytest.mark.parametrize("old,new", [
    ("## Model pricing", "## Pricing of models"), ("| Cache hits and refreshes", "| Cache reads"), ("| Output tokens ", "| Output ")])
def test_changed_section_or_header_fails_closed(old, new):
    for model in ("claude-opus-4-6", HAIKU):
        with pytest.raises(ValueError):
            parse(model, edit(model, old, new))


@pytest.mark.parametrize("title,change", [
    (OVER, lambda line: ""),
    (UP_TO, lambda line: line + "\n" + line),
    (UP_TO, lambda line: line.replace(" (for prompts up to 100,000 tokens)", "", 1)),  # a plain title is not a band
    (OVER, lambda line: line.replace("$0.50 / MTok", "$0.50 / 1K tokens", 1)),
], ids=["no_over_row", "up_to_twice", "plain_title", "over_unit"])
def test_haiku_needs_both_rows_exactly_once(title, change):
    with pytest.raises(ValueError):
        parse(HAIKU, edit_row(HAIKU, title, change))


@pytest.mark.parametrize("model", list(OPENAI))
def test_openai_text_rates_and_the_conditions_the_page_states(model):
    prices, conditions = parse(model)
    assert [(p["billing_item"], p["amount"]) for p in prices] == list(OPENAI[model].items())
    assert all(p["provider_model_id"] == model and p["upstream_provider"] == "OpenAI" and p["unit"] == "1M tokens" and p["task"] == "text_generation"
               and p["evidence_status"] == "direct" for p in prices)
    by_key = {}
    for c in conditions:
        assert c["status"] == "stated"
        by_key.setdefault(c["condition_key"], set()).add(c["value"])
    assert by_key["service_tier"] == {"standard"}
    assert ("promotion_note" in by_key) == (model == "gpt-5.6")
    assert ("long_context_multiplier" in by_key) == (model in ("gpt-6.1-terra", "gpt-5.5-2026-04-23", "gpt-5.6"))
    identity = {c["condition_set_id"] for c in conditions if c["condition_key"] in ("model_snapshot", "snapshot_of", "model_alias_of")}
    assert not identity & {p["condition_set_id"] for p in prices}  # identity facts never become a condition the compare table groups by


def test_the_272k_rule_is_kept_as_each_page_words_it():
    notes = {m: {c["value"] for c in parse(m)[1] if c["condition_key"] == "long_context_multiplier"} for m in ("gpt-6.1-terra", "gpt-5.5-2026-04-23", "gpt-5.6")}
    assert notes == {
        "gpt-6.1-terra": {"Prompts with more than 272K input tokens are priced at 2x input and cache rates and 1.5x output for the full request."},
        "gpt-5.5-2026-04-23": {"For GPT-5.5, prompts with >272K input tokens are priced at 2x input and 1.5x output for the full session for standard, batch, and flex."},
        "gpt-5.6": {"Prompts with >272K input tokens are priced at 2x input and 1.5x output for the full request."}}
    with pytest.raises(ValueError):  # two such rules: which one applies is not stated
        parse("gpt-6.1-terra", edit("gpt-6.1-terra", "- Batch and Flex prices", "- Prompts above 272K input tokens cost more.\n- Batch and Flex prices"))


def test_gpt_5_6_carries_the_promotion_the_sol_page_states():
    promo = [c for c in parse("gpt-5.6")[1] if c["condition_key"] == "promotion_note"]
    assert {c["value"] for c in promo} == {"promotional pricing is available at least through November 21, 2026."} and len(promo) == 3
    assert not any(c["condition_key"] == "promotion_note" for c in parse("gpt-5.5-2026-04-23")[1])


def test_a_snapshot_and_an_alias_are_tied_to_the_page_by_its_own_words():
    for model, base in (("gpt-5.5-pro-2026-04-23", "gpt-5.5-pro"), ("gpt-5.5-2026-04-23", "gpt-5.5")):
        facts = {c["condition_key"]: c for c in parse(model)[1] if c["condition_key"] in ("model_snapshot", "snapshot_of")}
        assert (facts["model_snapshot"]["value"], facts["snapshot_of"]["value"]) == (model, base)
        assert f"Snapshots: - `{model}`" in facts["model_snapshot"]["locator"]
    alias = next(c for c in parse("gpt-5.6")[1] if c["condition_key"] == "model_alias_of")
    assert alias["value"] == "gpt-5.6-sol" and "The `gpt-5.6` alias routes requests to GPT-5.6 Sol." in alias["locator"]
    assert not [c for c in parse("gpt-5.5-pro")[1] if c["condition_key"] in ("model_snapshot", "snapshot_of", "model_alias_of")]


@pytest.mark.parametrize("model,old,new", [
    ("gpt-5.5-pro-2026-04-23", "- `gpt-5.5-pro-2026-04-23`\n\n## Rate limits", "- `gpt-5.5-pro-2025-12-01`\n\n## Rate limits"),
    ("gpt-5.5-2026-04-23", "## Snapshots", "## Versions"),
    ("gpt-5.6", "The `gpt-5.6` alias routes requests to GPT-5.6 Sol.", "The `gpt-5.6` alias routes requests to GPT-5.6 Terra."),
    ("gpt-5.6", "The `gpt-5.6` alias routes requests to GPT-5.6 Sol.\n", ""),
    ("gpt-5.5-pro", "Model ID: `gpt-5.5-pro`", "Model ID: `gpt-5.5-pro-2026-04-23`"),
], ids=["snapshot_not_listed", "no_snapshots_section", "alias_other_target", "alias_sentence_gone", "plain_id_differs"])
def test_a_model_the_page_does_not_state_fails_closed(model, old, new):
    with pytest.raises(ValueError):
        parse(model, edit(model, old, new))


@pytest.mark.parametrize("model,old,new", [
    ("gpt-5.5-pro", "# GPT-5.5 Pro", "# GPT-5.5"),
    ("gpt-5.5-pro", "Model ID: `gpt-5.5-pro`", "Model ID: `gpt-5.5`"),
    ("gpt-5.2-pro", "Model ID: `gpt-5.2-pro`", "Model ID: `gpt-5.2-pro`\nModel ID: `gpt-5.5-pro`"),
    ("gpt-5.5-pro", "| Input | $30 | 1M tokens |", "| Input | $30 | 1K tokens |"),
    ("gpt-5.5-pro", "| Input | $30 | 1M tokens |", "| Input | 30 USD | 1M tokens |"),
    ("gpt-5.5-pro", "| Output | $180 | 1M tokens |\n", ""),
    ("gpt-5.5-pro", "| Input | $30 | 1M tokens |", "| Input | $30 | 1M tokens |\n| Input | $31 | 1M tokens |"),
    ("gpt-5.5-pro", "### Text tokens", "### Other tokens"),
    ("gpt-5.5-pro", "## Pricing", "## Prices"),
    ("gpt-5.5-2026-04-23", "| Cached input | $0.5 | 1M tokens |", "| Cached input | $0.5 | 1K tokens |"),
    ("gpt-6.1-terra", "| Cache writes | $1.25 | 1M tokens |", "| Cache writes | $1.25 | 1M tokens | extra |"),
], ids=["h1", "model_id", "second_model_id", "input_unit", "input_cell", "output_missing", "input_twice", "section_renamed", "pricing_renamed", "cached_unit", "cache_write_cells"])
def test_identity_units_and_rows_fail_closed_never_dropping_a_row_silently(model, old, new):
    with pytest.raises(ValueError):
        parse(model, edit(model, old, new))


@pytest.mark.parametrize("model", list(EMBEDDINGS))
def test_embedding_cost_is_an_input_rate_with_the_embedding_task(model):
    prices, conditions = parse(model)
    assert [(p["billing_item"], p["amount"], p["unit"], p["task"], p["provider_model_id"], p["upstream_provider"]) for p in prices] == [
        ("input", EMBEDDINGS[model], "1M tokens", "embedding", model, "OpenAI")]
    assert [(c["condition_key"], c["value"]) for c in conditions] == [("service_tier", "standard")]
    assert SPECS[model].task == "embedding" and SPECS[model].modality == "llm"


@pytest.mark.parametrize("old,new", [
    ("| Cost | $0.13 | 1M tokens |", "| Cost | $0.13 | 1K tokens |"),
    ("| Cost | $0.13 | 1M tokens |", "| Cost | 0.13 | 1M tokens |"),
    ("| Cost | $0.13 | 1M tokens |\n", ""),
    ("| Cost | $0.13 | 1M tokens |", "| Cost | $0.13 | 1M tokens |\n| Cost | $0.065 | 1M tokens |"),
    ("### Embeddings", "### Embedding costs"),
    ("# text-embedding-3-large", "# text-embedding-3-small"),
    ("Model ID: `text-embedding-3-large`", "Model ID: `text-embedding-3-small`"),
], ids=["unit", "cell", "missing", "twice", "section", "h1", "model_id"])
def test_embedding_pages_fail_closed(old, new):
    with pytest.raises(ValueError):
        parse("text-embedding-3-large", edit("text-embedding-3-large", old, new))


def test_each_page_belongs_to_its_own_model_only():
    for model in OPENAI | EMBEDDINGS:
        for other in set(PAGE.values()) - {PAGE[model]}:
            with pytest.raises(ValueError):
                parse(model, (F / other).read_bytes())
    with pytest.raises(ValueError):  # Anthropic's page for an OpenAI source, and the other way round
        parse("gpt-5.5-pro", (F / ANTHROPIC_PAGE).read_bytes())
    with pytest.raises(ValueError):
        parse("claude-opus-4-6", (F / "openai-gpt-5.5-pro.md").read_bytes())


def test_live_run_over_the_pages_reads_every_source_into_events_and_the_compare_table(tmp_path):
    _, adapters = core.registry()
    runs = [core.live(tmp_path, specs=list(SPECS.values()), adapters=adapters, fetcher=lambda spec: (raw(spec.model_id), spec.url, 200)) for _ in range(2)]
    sources = core._read_csv(runs[0] / "sources.csv", core.SOURCE_FIELDS)
    assert len(sources) == 18 and {(s["fetch_status"], s["parse_status"], s["error"]) for s in sources} == {("ok", "ok", "")}
    builder = Builder(Catalog())
    builder.build([evidence.load_run(run) for run in runs])
    assert builder.state["events"] == {} and builder.state["not_comparable"] == [] and builder.state["gaps"] == []
    assert len(builder.state["baselines"]) == 8 * 5 + 10 + sum(map(len, OPENAI.values())) + 3
    tables = compare.tables(tmp_path, list(SPECS), [], [], None, None, None, "2026-10-28")
    money = lambda amounts: [compare.money(compare.number(x)) for x in amounts]
    for model, spec in SPECS.items():
        who = ["Anthropic" if spec.route == "anthropic_direct" else "OpenAI", spec.route]
        groups = {name: members for name, members in tables[model]["groups"]}
        assert not tables[model]["ex"]  # no row is left out of the comparison for an unplaced condition
        if model == HAIKU:  # the over-100K prompts are their own group, not a price beside the standard one
            assert list(groups) == ["비공개 · Standard", "비공개 · Standard · 입력 100K 초과"]
            assert [[cell[0] for cell in groups[name][0]["c"]] for name in groups] == [money(("0.10", "0.50", "0.01")), money(("0.50", "2.50", "0.05"))]
        else:
            assert list(groups) == ["비공개 · Standard"] and [row["w"] for row in groups["비공개 · Standard"]] == [who]
