"""Gemini API prices read per model from one section of Google's pricing page (2026-10-28): the trimmed real page, exact
rates of the twelve models, fail-closed section and cell checks, and the run read back into events and the 공급사 비교 표."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.core import SourceSpec
from scripts.market_collection.collectors import google, google_models, parse_source, parser_module
from scripts.market_collection.dashboard import compare
from scripts.market_collection.events import evidence
from scripts.market_collection.events.catalog import Catalog, source_role
from scripts.market_collection.events.engine import Builder
from scripts.market_collection.models import GOOGLE_DIRECT_MODELS, GOOGLE_IMAGE_MODELS, MODEL_MODALITIES
from scripts.market_collection.sources import _key, source_specs

F = Path(__file__).parent / "fixtures" / "google_models"
BODY = (F / "pricing.html").read_bytes()
SPECS = {s.model_id: s for s in source_specs() if s.parser == "google_model_pricing"}
T, I = "1M tokens", "1 image"
LE, GT = {"context_band": "prompts <= 200k tokens"}, {"context_band": "prompts > 200k tokens"}
THROUGH, STARTING = {"rate_period": "through"}, {"rate_period": "starting"}
AUDIO = {"input_modality": "audio"}
STORAGE = ("cache_storage", "", "", {})
CONTRIBUTOR = ("gemini-4-argon", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview")  # sections that carry the invite-only tier


def tok(item, amount, **conditions):
    return (item, amount, T, conditions)


def img(item, amount, **conditions):
    return (item, amount, I, conditions)


# Standard tier, in page order: (billing item, amount as printed, unit, conditions besides service_tier).
STANDARD = {
    "gemini-4-argon": [tok("input", "2.00", **THROUGH), tok("input", "4.00", **STARTING), tok("output", "10.00", **THROUGH), tok("output", "20.00", **STARTING),
                       tok("cache_read", "0.10", **THROUGH), tok("cache_read", "0.20", **STARTING)],
    "gemini-3.1-pro-preview": [tok("input", "2.00", **LE), tok("input", "4.00", **GT), tok("output", "12.00", **LE), tok("output", "18.00", **GT),
                               tok("cache_read", "0.20", **LE), tok("cache_read", "0.40", **GT), STORAGE],
    "gemini-3.1-flash-lite": [tok("input", "0.25"), tok("input", "0.50", **AUDIO), tok("output", "1.50"), tok("cache_read", "0.025"), tok("cache_read", "0.05", **AUDIO), STORAGE],
    "gemini-3.5-flash-lite": [tok("input", "0.30"), tok("output", "2.50"), tok("cache_read", "0.03"), STORAGE],
    "gemini-3.6-flash": [tok("input", "0.75", **THROUGH), tok("input", "1.50", **STARTING), tok("output", "3.75", **THROUGH), tok("output", "7.50", **STARTING),
                         tok("cache_read", "0.075", **THROUGH), tok("cache_read", "0.15", **STARTING), ("cache_storage", "", "", THROUGH), ("cache_storage", "", "", STARTING)],
    "gemini-3-flash-preview": [tok("input", "0.50"), tok("input", "1.00", **AUDIO), tok("output", "3.00"), tok("cache_read", "0.05"), tok("cache_read", "0.10", **AUDIO), STORAGE],
    "gemini-2.5-flash": [tok("input", "0.30"), tok("input", "1.00", **AUDIO), tok("output", "2.50"), tok("cache_read", "0.03"), tok("cache_read", "0.1", **AUDIO), STORAGE],
    "gemini-2.5-pro": [tok("input", "1.25", **LE), tok("input", "2.50", **GT), tok("output", "10.00", **LE), tok("output", "15.00", **GT),
                       tok("cache_read", "0.125", **LE), tok("cache_read", "0.25", **GT), STORAGE],
    "gemini-nano-banana-2.1": [tok("input", "1.50"), tok("output", "7.50"), tok("image_output", "30.00"), img("image_output", "0.0336", resolution="1K"),
                               img("image_output", "0.0504", resolution="2K"), img("image_output", "0.113", resolution="4K")],
    "gemini-2.5-flash-image": [tok("input", "0.30"), img("image_output", "0.039")],
    "gemini-3-pro-image": [tok("input", "2.00"), img("image_input", "0.0011"), tok("output", "12.00"), tok("image_output", "120.00"),
                           img("image_output", "0.134", resolution="1K/2K"), img("image_output", "0.24", resolution="4K")],
    "gemini-3.1-flash-image": [tok("input", "0.50"), tok("output", "3"), tok("image_output", "60.00"), img("image_output", "0.045", resolution="0.5K"),
                               img("image_output", "0.067", resolution="1K"), img("image_output", "0.101", resolution="2K"), img("image_output", "0.151", resolution="4K")],
}
# Headline (text input, text output) of every tier the model's section has; the Contributor tier is never read.
TIER_RATES = {
    "gemini-4-argon": {"standard": ("2.00", "10.00"), "batch": ("1.00", "5.00"), "flex": ("1.00", "5.00"), "priority": ("3.60", "18.00")},
    "gemini-3.1-pro-preview": {"standard": ("2.00", "12.00"), "batch": ("1.00", "6.00"), "flex": ("1.00", "6.00"), "priority": ("3.60", "21.60")},
    "gemini-3.1-flash-lite": {"standard": ("0.25", "1.50"), "batch": ("0.125", "0.75"), "flex": ("0.125", "0.75"), "priority": ("0.45", "2.70")},
    "gemini-3.5-flash-lite": {"standard": ("0.30", "2.50"), "batch": ("0.15", "1.25"), "flex": ("0.15", "1.25"), "priority": ("0.54", "4.50")},
    "gemini-3.6-flash": {"standard": ("0.75", "3.75"), "batch": ("0.375", "1.875"), "flex": ("0.375", "1.875"), "priority": ("1.35", "6.75")},
    "gemini-3-flash-preview": {"standard": ("0.50", "3.00"), "batch": ("0.25", "1.50"), "flex": ("0.25", "1.50"), "priority": ("0.90", "5.40")},
    "gemini-2.5-flash": {"standard": ("0.30", "2.50"), "batch": ("0.15", "1.25"), "flex": ("0.15", "1.25"), "priority": ("0.54", "4.50")},
    "gemini-2.5-pro": {"standard": ("1.25", "10.00"), "batch": ("0.625", "5.00"), "flex": ("0.625", "5.00"), "priority": ("2.25", "18.00")},
    "gemini-nano-banana-2.1": {"standard": ("1.50", "7.50"), "batch": ("0.75", "3.75")},
    "gemini-2.5-flash-image": {"standard": ("0.30", None), "batch": ("0.15", None), "flex": ("0.15", None), "priority": ("0.54", None)},
    "gemini-3-pro-image": {"standard": ("2.00", "12.00"), "batch": ("1.00", "6.00"), "flex": ("1.00", "6.00"), "priority": ("3.60", "21.60")},
    "gemini-3.1-flash-image": {"standard": ("0.50", "3"), "batch": ("0.25", "1.50")},
}


def headline(table, item):
    """First plain token rate of an item: not the band above 200k, an audio variant or an announced future rate."""
    return next((amount for i, amount, unit, c in table if i == item and unit == T and "input_modality" not in c
                 and c.get("context_band") != GT["context_band"] and c.get("rate_period") != "starting"), None)


def parse(model, body=BODY):
    spec = SPECS[model]
    result = parse_source(spec, body, "test-source")
    core._validate_parse(spec, "test-source", *result)
    return result


def rows(model, tier="standard", body=BODY):
    """(billing item, amount, unit, conditions besides service_tier) of one tier in page order."""
    prices, conditions = parse(model, body)
    by_set = {}
    for c in conditions:
        by_set.setdefault(c["condition_set_id"], {})[c["condition_key"]] = c["value"]
    return [(p["billing_item"], p["amount"], p["unit"], {k: v for k, v in by_set[p["condition_set_id"]].items() if k != "service_tier"})
            for p in prices if p["condition_set_id"] and by_set[p["condition_set_id"]]["service_tier"] == tier]


def section(model, body=BODY):
    text = body.decode()
    start = text.index(f'<h2 id="{model}"')
    end = text.find("<h2 ", start + 1)
    return text, start, len(text) if end < 0 else end


def edit(model, old, new, body=BODY):
    """The body with the first `old` inside the model's own section replaced."""
    text, start, end = section(model, body)
    part = text[start:end]
    assert old in part
    return (text[:start] + part.replace(old, new, 1) + text[end:]).encode()


def test_one_spec_per_model_on_the_page_gemini_38_flash_already_reads():
    models = [model for model, _ in GOOGLE_DIRECT_MODELS + GOOGLE_IMAGE_MODELS]
    assert list(SPECS) == models == list(STANDARD) and len(models) == 12
    old = next(s for s in source_specs() if s.source_key == "google_gemini_38_flash")
    text_models = {model for model, _ in GOOGLE_DIRECT_MODELS}
    for model, spec in SPECS.items():
        assert (spec.source_key, spec.route, spec.url, spec.parser) == (f"google_{_key(model)}", "google_direct", old.url, "google_model_pricing")
        assert (spec.modality, spec.task) == (("llm", "text_generation") if model in text_models else ("image", "text-to-image"))
        assert MODEL_MODALITIES[model] == spec.modality
    assert len({s.source_key for s in source_specs()}) == len(source_specs())
    all_specs = source_specs()
    at = all_specs.index(next(iter(SPECS.values())))
    assert all_specs[at:at + 12] == list(SPECS.values())  # appended after every older source
    assert source_role("google_model_pricing") == "pricing"
    assert parser_module(next(iter(SPECS.values()))) is google_models and parser_module(old) is google
    p = json.loads((F / "provenance.json").read_text())[0]
    assert hashlib.sha256(BODY).hexdigest() == p["fixture_sha256"] and p["source_url"] == old.url


@pytest.mark.parametrize("model", list(STANDARD))
def test_standard_rates_conditions_and_units(model):
    assert rows(model) == STANDARD[model]
    prices, conditions = parse(model)
    spec = SPECS[model]
    assert all(p["task"] == spec.task and p["provider_model_id"] == model and p["upstream_provider"] == "Google" and p["evidence_status"] == "direct" for p in prices)
    assert all(p["currency"] == "USD" and p["unit"] in (T, I) for p in prices if p["amount"])
    assert all(p["unknown_reason"] for p in prices if not p["amount"]) and all(c["status"] == "stated" for c in conditions)
    assert all(re.search(r" > (Standard|Batch|Flex|Priority) > (Input price|Output price|Output price \(including thinking tokens\)|Context caching price) > Paid Tier", p["locator"]) for p in prices)


@pytest.mark.parametrize("model", list(TIER_RATES))
def test_every_tier_headline_and_no_contributor_tier(model):
    found = {tier: (headline(rows(model, tier), "input"), headline(rows(model, tier), "output")) for tier in google_models.TIERS if rows(model, tier)}
    assert found == TIER_RATES[model]
    text, start, end = section(model)  # the invite-only Contributor table is on the page but never read
    assert ("Contributor" in text[start:end]) == (model in CONTRIBUTOR)
    assert {c["value"] for c in parse(model)[1] if c["condition_key"] == "service_tier"} == set(TIER_RATES[model])


def test_generalises_the_existing_gemini_38_flash_parser():
    old = next(s for s in source_specs() if s.source_key == "google_gemini_38_flash")
    mine = SourceSpec("x", "llm", old.model_id, old.route, old.task, old.url, "google_model_pricing")
    assert google_models.parse_source(mine, BODY, "s") == google.parse_source(old, BODY, "s")


def test_prompt_bands_are_separate_condition_sets_with_one_stated_value():
    # The page writes the upper band as "prompts > 200k tokens" in one row and "prompts > 200k" in the next.
    for model in ("gemini-3.1-pro-preview", "gemini-2.5-pro"):
        prices, conditions = parse(model)
        assert {c["value"] for c in conditions if c["condition_key"] == "context_band"} == {"prompts <= 200k tokens", "prompts > 200k tokens"}
        high = {p["billing_item"]: p["condition_set_id"] for p in prices if p["condition_set_id"].startswith("google_standard") and "gt_200k" in p["condition_set_id"]}
        assert set(high) == {"input", "output", "cache_read"} and len(set(high.values())) == 3
    assert [p["locator"].rsplit(": ", 1)[1] for p in parse("gemini-3.1-pro-preview")[0][:4]] == [
        "$2.00, prompts <= 200k tokens", "$4.00, prompts > 200k tokens", "$12.00, prompts <= 200k tokens", "$18.00, prompts > 200k"]


def test_future_rates_carry_their_dates_as_in_the_existing_parser():
    prices, _ = parse("gemini-4-argon")
    assert [(p["billing_item"], p["amount"], p["price_kind"], p["effective_from"], p["effective_until"]) for p in prices[:4]] == [
        ("input", "2.00", "published_rate", "", "2026-11-30"), ("input", "4.00", "announced_future_rate", "2026-12-01", ""),
        ("output", "10.00", "published_rate", "", "2026-11-30"), ("output", "20.00", "announced_future_rate", "2026-12-01", "")]
    storage = [p for p in parse("gemini-3.6-flash")[0] if p["billing_item"] == "cache_storage" and p["condition_set_id"].startswith("google_standard")]
    assert [(p["price_kind"], p["amount"], p["effective_from"], p["effective_until"]) for p in storage] == [
        ("published_rate", "", "", "2026-12-31"), ("announced_future_rate", "", "2027-01-01", "")]
    assert all("token-hour" in p["unknown_reason"] for p in storage)


def test_audio_rates_are_rows_of_their_own_and_never_the_text_price():
    prices, conditions = parse("gemini-3.1-flash-lite")
    text = [p for p in prices if p["condition_set_id"] == "google_standard_input"]
    audio = [p for p in prices if p["condition_set_id"] == "google_standard_input_audio"]
    assert [p["amount"] for p in text] == ["0.25"] and [p["amount"] for p in audio] == ["0.50"]
    assert [(c["condition_key"], c["value"]) for c in conditions if c["condition_set_id"] == "google_standard_input_audio"] == [("service_tier", "standard"), ("input_modality", "audio")]
    assert "(text / image / video)" in text[0]["locator"] and "(audio)" in audio[0]["locator"]
    # "(text / image / video / audio)" is one rate for all of them: no variant, no extra condition.
    assert rows("gemini-3.5-flash-lite")[0] == tok("input", "0.30")


def test_image_models_state_tokens_and_images_with_their_own_units_and_resolutions():
    for model, spec in SPECS.items():
        if spec.modality == "image":
            assert {p["task"] for p in parse(model)[0]} == {"text-to-image"}
    per_image = [r for r in rows("gemini-3.1-flash-image") if r[2] == I]
    assert [(r[1], r[3]["resolution"]) for r in per_image] == [("0.045", "0.5K"), ("0.067", "1K"), ("0.101", "2K"), ("0.151", "4K")]
    # Batch of gemini-3-pro-image: image output has per-image prices only; the image input "$0.0006 (image)*" has no unit of its own.
    assert [r for r in rows("gemini-3-pro-image", "batch") if r[0] == "image_output"] == [img("image_output", "0.067", resolution="1K/2K"), img("image_output", "0.12", resolution="4K")]
    unstated = [p for p in parse("gemini-3-pro-image")[0] if "unit not stated" in p["unknown_reason"]]
    assert [(p["billing_item"], p["amount"], p["unit"]) for p in unstated] == [("input", "", "")] * 2
    assert all("$0.0006 (image)*" in p["locator"] for p in unstated) and not any(p["amount"] == "0.0006" for p in parse("gemini-3-pro-image")[0])
    with pytest.raises(ValueError):  # an image source needs its source id
        parse_source(SPECS["gemini-3-pro-image"], BODY, "")


@pytest.mark.parametrize("model", list(STANDARD))
def test_other_sections_are_never_read(model):
    text, start, end = section(model)
    money = lambda part: re.sub(r"\$\d+(?:\.\d+)?", "$999", part)
    assert parse(model, (money(text[:start]) + text[start:end] + money(text[end:])).encode()) == parse(model)


@pytest.mark.parametrize("model", ["gemini-3.5-flash", "gemini-3.7-flash", "gemini-3-pro-image-preview", "gemini-3.1-flash-image-preview"])
def test_a_model_without_its_own_section_is_not_read_here_or_borrowed_from_a_neighbour(model):
    # The live page has no section for these four, only for gemini-3.5-flash-lite and the two image models without "-preview".
    # Since 2026-10-30 gemini-3.5-flash and gemini-3.7-flash are priced from the sections of the models Google routes their
    # requests to, by a parser of their own (test_google_models_more.py); the two image previews have no Google source.
    routed = model in ("gemini-3.5-flash", "gemini-3.7-flash")
    assert model not in SPECS and [s.parser for s in source_specs() if s.model_id == model and s.route == "google_direct"] == (["google_routed_pricing"] if routed else [])
    spec = SourceSpec("x", "llm", model, "google_direct", "text_generation", SPECS["gemini-2.5-pro"].url, "google_model_pricing")
    with pytest.raises(ValueError, match="0 sections"):
        google_models.parse_source(spec, BODY, "s")


def test_missing_and_repeated_section_fail_closed():
    with pytest.raises(ValueError, match="0 sections"):
        parse("gemini-2.5-pro", BODY.replace(b'<h2 id="gemini-2.5-pro"', b'<h2 id="gemini-2.5-pro-v2"'))
    with pytest.raises(ValueError, match="2 sections"):
        parse("gemini-2.5-pro", BODY.replace(b'<h2 id="gemini-2.5-flash"', b'<h2 id="gemini-2.5-pro"'))
    with pytest.raises(ValueError, match="0 sections"):
        parse("gemini-2.5-pro", b"<h1>Gemini API pricing</h1>")


@pytest.mark.parametrize("old,new", [
    ("Paid Tier, per 1M tokens in USD", "Paid Tier, per 1K tokens in USD"),
    ("Paid Tier, per 1M tokens in USD", "Paid Tier"),
    ("Free Tier", "Trial Tier"),
], ids=["unit", "no_unit", "free_tier"])
def test_changed_column_header_fails_closed(old, new):
    with pytest.raises(ValueError, match="header changed"):
        parse("gemini-3.1-pro-preview", edit("gemini-3.1-pro-preview", old, new))


def test_a_cell_that_cannot_be_read_exactly_is_one_amount_less_row_never_a_partial_price():
    page = "gemini-3.1-pro-preview"
    old = "$2.00, prompts <= 200k tokens<br>$4.00, prompts > 200k tokens"
    for cell in ("$2.00, prompts <= 200k tokens<br>$4.00, prompts > 1M tokens", "Contact sales", "Same as Standard", "$2.00 for prompts <= 200k tokens"):
        body = edit(page, old, cell)
        unread = [p for p in parse(page, body)[0] if p["locator"] == "Gemini 3.1 Pro Preview > Standard > Input price > Paid Tier"]
        assert [(p["billing_item"], p["amount"], p["unit"], p["unknown_reason"]) for p in unread] == [("input", "", "", "paid rate or period missing/ambiguous")]
        assert [r for r in rows(page, body=body) if r[0] == "input"] == []  # not the $2.00 line of the same cell
        assert [r for r in rows(page, body=body) if r[0] != "input"] == [r for r in STANDARD[page] if r[0] != "input"]
    # A date that is no date, or in another format, is unknown too.
    for date in ("Nov 30th, 2026", "Foobar 30, 2026"):
        prices, _ = parse("gemini-4-argon", edit("gemini-4-argon", "$2.00 through November 30, 2026.", f"$2.00 through {date}."))
        assert [p["amount"] for p in prices[:2]] == ["", "10.00"] and prices[0]["unknown_reason"]


def test_missing_repeated_or_malformed_rows_fail_closed():
    page = "gemini-3.5-flash-lite"
    with pytest.raises(ValueError, match="lacks"):
        parse(page, edit(page, "<td>Output price (including thinking tokens)</td>", "<td>Output price</td>"))
    with pytest.raises(ValueError, match="lacks"):
        parse(page, edit(page, "<td>Input price</td>", "<td>Input cost</td>"))
    with pytest.raises(ValueError, match="repeated or malformed"):
        parse(page, edit(page, "<td>Context caching price</td>", "<td>Input price</td>"))
    with pytest.raises(ValueError, match="repeated or malformed"):
        parse(page, edit(page, "<td>Free of charge</td>\n      <td>$0.30 (text / image / video / audio)</td>", "<td>$0.30 (text / image / video / audio)</td>"))
    with pytest.raises(ValueError, match="repeated or header changed"):  # two Standard tables
        parse(page, edit(page, 'data-text="Batch" tabindex="-1">Batch</h3>', 'data-text="Standard" tabindex="-1">Standard</h3>'))
    with pytest.raises(ValueError, match="no Standard table"):
        parse(page, edit(page, 'data-text="Standard" tabindex="-1">Standard</h3>', 'data-text="Default" tabindex="-1">Default</h3>'))
    # An image model's row labels are its own: the text model's label for the output row is not accepted.
    with pytest.raises(ValueError, match="lacks"):
        parse("gemini-nano-banana-2.1", edit("gemini-nano-banana-2.1", "<td>Output price</td>", "<td>Output price (including thinking tokens)</td>"))


def test_two_statements_for_one_condition_set_fail_closed():
    with pytest.raises(ValueError, match="twice"):
        parse("gemini-3.1-pro-preview", edit("gemini-3.1-pro-preview", "$4.00, prompts > 200k tokens", "$4.00, prompts <= 200k tokens"))


def test_run_is_read_back_into_events_and_the_comparison_table(tmp_path):
    _, adapters = core.registry()
    runs = [core.live(tmp_path, specs=list(SPECS.values()), adapters=adapters, fetcher=lambda s: (BODY, s.url, 200)) for _ in range(2)]
    assert {(s["fetch_status"], s["parse_status"]) for s in core._validated_run(runs[0])[1]} == {("ok", "ok")}
    builder = Builder(Catalog())
    builder.build([evidence.load_run(run) for run in runs])
    # The two announced doublings (gemini-4-argon, gemini-3.6-flash) are the only changes; the rows with no amount are gaps, not prices.
    assert {(e["model_id"], e["event_type"]) for e in builder.state["events"].values()} == {("gemini-4-argon", "scheduled_change"), ("gemini-3.6-flash", "scheduled_change")}
    assert {n["reason"] for n in builder.state["not_comparable"]} == {"price_missing"}
    assert {(n["billing_item"]) for n in builder.state["not_comparable"]} == {"cache_storage", "input"}
    text_models = [model for model, _ in GOOGLE_DIRECT_MODELS]
    tables = compare.tables(tmp_path, text_models, [], [], None, None, None, "2026-10-28")
    standard = {model: [[cell[0] for cell in row["c"]] for name, members in table["groups"] if name == "비공개 · Standard" for row in members] for model, table in tables.items()}
    assert standard == {
        "gemini-4-argon": [["$2.00", "$10.00", "$0.10"]], "gemini-3.1-pro-preview": [["$2.00", "$12.00", "$0.20"]],
        "gemini-3.1-flash-lite": [["$0.25", "$1.50", "$0.025"]], "gemini-3.5-flash-lite": [["$0.30", "$2.50", "$0.03"]],
        "gemini-3.6-flash": [["$0.75", "$3.75", "$0.075"]], "gemini-3-flash-preview": [["$0.50", "$3.00", "$0.05"]],
        "gemini-2.5-flash": [["$0.30", "$2.50", "$0.03"]], "gemini-2.5-pro": [["$1.25", "$10.00", "$0.125"]]}
    # The over-200k band is its own group, and the audio-only rows stay out of the comparison with their reason.
    assert [name for name, _ in tables["gemini-2.5-pro"]["groups"]][:2] == ["비공개 · Standard", "비공개 · Standard · 입력 200K 초과"]
    assert tables["gemini-3.1-flash-lite"]["ex"] and all("input_modality audio" in row["n"] for row in tables["gemini-3.1-flash-lite"]["ex"])
    assert not tables["gemini-2.5-pro"]["ex"]
