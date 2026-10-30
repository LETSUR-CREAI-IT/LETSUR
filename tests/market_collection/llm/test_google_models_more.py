"""Gemini sources google_models.py cannot read (2026-10-30), read from the same pricing page by google_models_more.py: the trimmed
real page of 10/30 (provenance.json, second record), gemini-2.5-flash-lite's input row labelled by its modalities, gemini-3.5-flash and
gemini-3.7-flash priced from the sections of the models Google routes their requests to, fail-closed checks, and the run read back
into events and the 공급사 비교 표."""
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.collectors import google_models, google_models_more, parse_source, parser_module
from scripts.market_collection.core import SourceSpec
from scripts.market_collection.dashboard import compare
from scripts.market_collection.events import evidence
from scripts.market_collection.events.catalog import Catalog, source_role
from scripts.market_collection.events.engine import Builder
from scripts.market_collection.events.evidence import parser_fingerprint
from scripts.market_collection.models import GOOGLE_DIRECT_MODELS, GOOGLE_ROUTED_MODELS
from scripts.market_collection.sources import route_gap_source_specs, source_specs

F = Path(__file__).parent / "fixtures" / "google_models"
BODY = (F / "pricing-20261030.html").read_bytes()
OLD = (F / "pricing.html").read_bytes()  # the 10/28 page of test_google_models.py
SPECS = {s.model_id: s for s in source_specs() if s.parser in google_models_more.PARSERS}
URL = "https://ai.google.dev/gemini-api/docs/pricing"
T = "1M tokens"
AUDIO = {"input_modality": "audio"}
THROUGH, STARTING = {"rate_period": "through"}, {"rate_period": "starting"}
# gemini-2.5-flash-lite per tier, in page order: (item, amount as printed, unit, conditions besides service_tier).
LITE = {tier: [("input", i, T, {}), ("input", ia, T, AUDIO), ("output", o, T, {}), ("cache_read", c, T, {}), ("cache_read", ca, T, AUDIO),
               ("cache_storage", "", "", {})]
        for tier, (i, ia, o, c, ca) in {"standard": ("0.10", "0.30", "0.40", "0.01", "0.03"), "batch": ("0.05", "0.15", "0.20", "0.01", "0.03"),
                                        "flex": ("0.05", "0.15", "0.20", "0.01", "0.03"), "priority": ("0.18", "0.54", "0.72", "0.018", "0.054")}.items()}
# The Standard rows of gemini-3.6-flash and gemini-3.8-flash on 10/30: the current rate through 2026-12-31, then double.
SERVED = [("input", "0.75", T, THROUGH), ("input", "1.50", T, STARTING), ("output", "3.75", T, THROUGH), ("output", "7.50", T, STARTING),
          ("cache_read", "0.075", T, THROUGH), ("cache_read", "0.15", T, STARTING), ("cache_storage", "", "", THROUGH), ("cache_storage", "", "", STARTING)]


def parse(model, body=BODY):
    spec = SPECS[model]
    result = parse_source(spec, body, "test-source")
    core._validate_parse(spec, "test-source", *result)
    return result


def rows(model, tier="standard", body=BODY):
    """(item, amount, unit, conditions besides service_tier) of one tier in page order."""
    prices, conditions = parse(model, body)
    by_set = {}
    for c in conditions:
        by_set.setdefault(c["condition_set_id"], {})[c["condition_key"]] = c["value"]
    return [(p["billing_item"], p["amount"], p["unit"], {k: v for k, v in by_set[p["condition_set_id"]].items() if k != "service_tier"})
            for p in prices if p["condition_set_id"] and by_set[p["condition_set_id"]]["service_tier"] == tier]


def edit(section, old, new, body=BODY):
    """The body with the first `old` inside the given model's section replaced."""
    text = body.decode()
    start = text.index(f'<h2 id="{section}"')
    end = text.find("<h2 ", start + 1)
    end = len(text) if end < 0 else end
    assert old in text[start:end]
    return (text[:start] + text[start:end].replace(old, new, 1) + text[end:]).encode()


def test_three_specs_read_the_page_google_models_reads_with_parsers_of_their_own():
    assert [(s.source_key, s.model_id, s.parser) for s in SPECS.values()] == [
        ("google_gemini_2_5_flash_lite", "gemini-2.5-flash-lite", "google_model_pricing_more"),
        ("google_gemini_3_5_flash", "gemini-3.5-flash", "google_routed_pricing"),
        ("google_gemini_3_7_flash", "gemini-3.7-flash", "google_routed_pricing")]
    assert all((s.modality, s.route, s.task, s.url) == ("llm", "google_direct", "text_generation", URL) for s in SPECS.values())
    gaps = route_gap_source_specs()
    assert source_specs()[-len(gaps):] == gaps and gaps[:3] == list(SPECS.values())  # appended after every older source
    assert {source_role(s.parser) for s in SPECS.values()} == {"pricing"}
    own = next(s for s in source_specs() if s.parser == "google_model_pricing")
    assert all(parser_module(s) is google_models_more for s in SPECS.values()) and parser_module(own) is google_models
    assert parser_fingerprint(asdict(own)) not in {parser_fingerprint(asdict(s)) for s in SPECS.values()}
    p = json.loads((F / "provenance.json").read_text())[1]
    assert (p["fixture"], p["source_url"], p["fixture_sha256"]) == ("pricing-20261030.html", URL, hashlib.sha256(BODY).hexdigest())
    assert p["sections"] == ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash-lite"]


@pytest.mark.parametrize("tier", list(LITE))
def test_gemini_2_5_flash_lite_rates_of_every_tier(tier):
    assert rows("gemini-2.5-flash-lite", tier) == LITE[tier]
    prices, conditions = parse("gemini-2.5-flash-lite")
    assert {(p["provider_model_id"], p["upstream_provider"], p["task"], p["evidence_status"]) for p in prices} == {
        ("gemini-2.5-flash-lite", "Google", "text_generation", "direct")}
    assert all(p["unknown_reason"] for p in prices if not p["amount"]) and all(c["status"] == "stated" for c in conditions)


def test_the_modality_labelled_input_row_is_read_and_audio_stays_a_row_of_its_own():
    prices, _ = parse("gemini-2.5-flash-lite")
    inputs = [p["locator"] for p in prices if p["billing_item"] == "input" and p["condition_set_id"].startswith("google_standard")]
    assert inputs == ["Gemini 2.5 Flash-Lite > Standard > Input price (text, image, video) > Paid Tier: $0.10 (text / image / video)",
                      "Gemini 2.5 Flash-Lite > Standard > Input price (text, image, video) > Paid Tier: $0.30 (audio)"]
    spec = SourceSpec("x", "llm", "gemini-2.5-flash-lite", "google_direct", "text_generation", URL, "google_model_pricing")
    with pytest.raises(ValueError, match="lacks"):  # why google_models.py cannot read this section
        google_models.parse_source(spec, BODY, "s")


@pytest.mark.parametrize("model", [model for model, _ in GOOGLE_DIRECT_MODELS])
def test_a_section_google_models_reads_is_read_the_same_way(model):
    old = next(s for s in source_specs() if s.parser == "google_model_pricing" and s.model_id == model)
    mine = replace(SPECS["gemini-2.5-flash-lite"], model_id=model)
    assert google_models_more.parse_source(mine, OLD, "s") == google_models.parse_source(old, OLD, "s")


@pytest.mark.parametrize("model,served", GOOGLE_ROUTED_MODELS)
def test_a_routed_model_reads_exactly_the_rows_of_the_model_serving_it(model, served):
    prices, conditions = parse(model)
    own = SourceSpec("x", "llm", served, "google_direct", "text_generation", URL, "google_model_pricing")
    assert (prices, conditions) == google_models.parse_source(own, BODY, "s")
    assert rows(model) == SERVED and {p["provider_model_id"] for p in prices} == {served}
    title = {"gemini-3.6-flash": "Gemini 3.6 Flash", "gemini-3.8-flash": "Gemini 3.8 Flash"}[served]
    assert all(p["locator"].startswith(f"{title} > ") for p in prices)
    assert {(p["price_kind"], p["effective_from"], p["effective_until"]) for p in prices if p["amount"]} == {
        ("published_rate", "", "2026-12-31"), ("announced_future_rate", "2027-01-01", "")}


def test_only_the_section_of_the_serving_model_is_read():
    changed = edit("gemini-3.6-flash", "$0.75 through", "$0.80 through")
    assert parse("gemini-3.7-flash", changed) == parse("gemini-3.7-flash")  # served by gemini-3.8-flash
    assert rows("gemini-3.5-flash", body=changed)[0] == ("input", "0.80", T, THROUGH)
    assert parse("gemini-2.5-flash-lite", changed) == parse("gemini-2.5-flash-lite")


@pytest.mark.parametrize("model,served", GOOGLE_ROUTED_MODELS)
def test_a_routed_model_with_a_section_of_its_own_or_a_missing_successor_fails_closed(model, served):
    with pytest.raises(ValueError, match="section of its own"):
        parse(model, BODY.replace(b"</body>", f'<h2 id="{model}">Gemini</h2></body>'.encode()))
    with pytest.raises(ValueError, match="0 sections"):
        parse(model, BODY.replace(f'<h2 id="{served}"'.encode(), f'<h2 id="{served}-old"'.encode()))
    with pytest.raises(ValueError, match="2 sections"):
        parse(model, BODY.replace(b'<h2 id="gemini-2.5-flash-lite"', f'<h2 id="{served}"'.encode()))


@pytest.mark.parametrize("old,new,match", [
    ("<td>Output price (including thinking tokens)</td>", "<td>Input price</td>", "repeated or malformed"),  # a second input row
    ("<td>Input price (text, image, video)</td>", "<td>Input price (text, image)</td>", "lacks"),
    ("<td>Output price (including thinking tokens)</td>", "<td>Output price</td>", "lacks"),
    ("Paid Tier, per 1M tokens in USD", "Paid Tier, per 1K tokens in USD", "header changed"),
    ('data-text="Batch" tabindex="-1">Batch</h3>', 'data-text="Standard" tabindex="-1">Standard</h3>', "repeated or header changed"),
    ('data-text="Standard" tabindex="-1">Standard</h3>', 'data-text="Default" tabindex="-1">Default</h3>', "no Standard table"),
], ids=["second_input_row", "input_label", "output_label", "unit", "two_standard_tables", "no_standard_table"])
def test_gemini_2_5_flash_lite_rows_and_tables_fail_closed(old, new, match):
    with pytest.raises(ValueError, match=match):
        parse("gemini-2.5-flash-lite", edit("gemini-2.5-flash-lite", old, new))
    with pytest.raises(ValueError, match="0 sections"):
        parse("gemini-2.5-flash-lite", BODY.replace(b'<h2 id="gemini-2.5-flash-lite"', b'<h2 id="gemini-2.5-flash-lite-001"'))


def test_run_is_read_back_into_events_and_the_comparison_table(tmp_path):
    _, adapters = core.registry()
    runs = [core.live(tmp_path, specs=list(SPECS.values()), adapters=adapters, fetcher=lambda s: (BODY, s.url, 200)) for _ in range(2)]
    assert {(s["fetch_status"], s["parse_status"]) for s in core._validated_run(runs[0])[1]} == {("ok", "ok")}
    builder = Builder(Catalog())
    builder.build([evidence.load_run(run) for run in runs])
    # The announced doubling of the serving models is the routed models' change too; the storage rows have no amount.
    assert {(e["model_id"], e["event_type"]) for e in builder.state["events"].values()} == {
        ("gemini-3.5-flash", "scheduled_change"), ("gemini-3.7-flash", "scheduled_change")}
    assert {(n["reason"], n["billing_item"]) for n in builder.state["not_comparable"]} == {("price_missing", "cache_storage")}
    tables = compare.tables(tmp_path, list(SPECS), [], [], None, None, None, "2026-10-30")
    standard = {model: [[cell[0] for cell in row["c"]] for name, members in table["groups"] if name == "비공개 · Standard" for row in members]
                for model, table in tables.items()}
    assert standard == {"gemini-2.5-flash-lite": [["$0.10", "$0.40", "$0.01"]], "gemini-3.5-flash": [["$0.75", "$3.75", "$0.075"]],
                        "gemini-3.7-flash": [["$0.75", "$3.75", "$0.075"]]}
    assert tables["gemini-2.5-flash-lite"]["ex"] and all("input_modality audio" in row["n"] for row in tables["gemini-2.5-flash-lite"]["ex"])
