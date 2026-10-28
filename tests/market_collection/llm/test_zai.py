"""Z.ai direct token prices (2026-10-28): the real Markdown pricing page, exact row reading, fail-closed table checks."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.collectors import parse_source
from scripts.market_collection.events import evidence
from scripts.market_collection.events.catalog import Catalog
from scripts.market_collection.events.engine import Builder
from scripts.market_collection.sources import source_specs

F = Path(__file__).parent / "fixtures" / "zai"
BODY = (F / "pricing.md").read_bytes()
SPECS = {s.model_id: s for s in source_specs() if s.parser == "zai_pricing"}
# The page's rates of 10/28 per 1M tokens: input, cached input, output (its "Cached Input Storage" column is "Limited-time Free", no unit).
EXPECTED = {
    "glm-4.7": {"input": "0.6", "cache_read": "0.11", "output": "2.2"},
    "glm-5": {"input": "1", "cache_read": "0.2", "output": "3.2"},
    "glm-5.2": {"input": "1.4", "cache_read": "0.26", "output": "4.4"},
    "glm-5.3": {"input": "1.4", "cache_read": "0.26", "output": "4.4"},
    "glm-5.3-flashx": {"input": "0.37", "cache_read": "0.075", "output": "1.25"},
}
LABELS = {"glm-4.7": ("Text Models", "GLM-4.7"), "glm-5": ("Text Models", "GLM-5"), "glm-5.2": ("Text Models", "GLM-5.2"),
          "glm-5.3": ("Latest Models", "GLM-5.3"), "glm-5.3-flashx": ("Latest Models", "GLM-5.3-FlashX")}
HEADER = "| Model | Input | Cached Input | Cached Input Storage | Output |"


def parse(model, body=BODY):
    spec = SPECS[model]
    result = parse_source(spec, body, "test-source")
    core._validate_parse(spec, "test-source", *result)
    return result


def row(label):
    """The one table row of the page that begins with this label (rows of the quoted 'Other Pricing' callout begin with '>')."""
    found = [line for line in BODY.decode().splitlines(keepends=True) if line.startswith(f"| {label} |")]
    assert len(found) == 1
    return found[0]


def replaced(old, new, count=None):
    text = BODY.decode()
    assert old in text and (count is None or text.count(old) == count), old
    return text.replace(old, new).encode()


def with_cell(label, index, value):
    cells = [cell.strip() for cell in row(label).strip().strip("|").split("|")]
    cells[index] = value
    return replaced(row(label), "| " + " | ".join(cells) + " |\n")


def test_five_specs_read_one_page_from_the_zai_route():
    assert list(SPECS) == list(EXPECTED)
    assert [s.source_key for s in SPECS.values()] == ["zai_glm_4_7", "zai_glm_5", "zai_glm_5_2", "zai_glm_5_3", "zai_glm_5_3_flashx"]
    assert {(s.route, s.task, s.url, s.parser) for s in SPECS.values()} == {
        ("zai_direct", "text_generation", "https://docs.z.ai/guides/overview/pricing.md", "zai_pricing")}
    # glm-5.2 keeps its OpenRouter and Vercel catalog and endpoint sources next to the new route.
    assert {"openrouter", "vercel_ai_gateway", "zai_direct"} <= {s.route for s in source_specs() if s.model_id == "glm-5.2"}
    p = json.loads((F / "provenance.json").read_text())[0]
    assert hashlib.sha256(BODY).hexdigest() == p["raw_sha256"] and len(BODY) == p["raw_bytes"] and p["source_url"] == SPECS["glm-5.3"].url


@pytest.mark.parametrize("model", list(EXPECTED))
def test_exact_input_cached_input_and_output_rates(model):
    prices, conditions = parse(model)
    table, label = LABELS[model]
    assert [(p["billing_item"], p["amount"]) for p in prices] == list(EXPECTED[model].items())
    assert all(p["currency"] == "USD" and p["unit"] == "1M tokens" and p["upstream_provider"] == "Z.ai" and p["provider_model_id"] == model
               and p["task"] == "text_generation" and p["price_kind"] == "published_rate" and p["evidence_status"] == "direct"
               and p["locator"].startswith(f"Models > {table} > {label} > ") for p in prices)
    # One stated condition per price. The table name is only in the locator, so a model moving between tables keeps its series.
    assert [(c["condition_set_id"], c["condition_key"], c["status"]) for c in conditions] == [(p["condition_set_id"], "price_basis", "stated") for p in prices]
    assert {c["value"] for c in conditions} == {"Z.ai pricing page: USD per 1M tokens"}


def test_zai_direct_is_the_makers_own_pricing_source():
    info = Catalog().price_source(asdict(SPECS["glm-5.3"]))
    assert (info["role"], info["authority"], info["publisher"]) == ("pricing", "manufacturer_official", "Z.ai")
    assert "model_lifecycle" in info["scope"]


def test_a_missing_row_of_one_model_leaves_the_others_readable_and_borrows_no_neighbour():
    body = replaced(row("GLM-5.3"), "")  # GLM-5.3-Flash and GLM-5.3-FlashX stay on the page
    with pytest.raises(ValueError):
        parse("glm-5.3", body)
    for model in EXPECTED:
        if model != "glm-5.3":
            assert parse(model, body) == parse(model)


def test_a_row_only_in_the_quoted_older_models_callout_is_not_read():
    callout = "> | GLM-4.7-FlashX |"
    body = replaced(row("GLM-4.7"), "").decode()
    assert body.count(callout) == 1
    body = body.replace(callout, "> | GLM-4.7 | \\$9 | \\$9 | Limited-time Free | \\$9 |\n" + callout)
    with pytest.raises(ValueError):
        parse("glm-4.7", body.encode())


@pytest.mark.parametrize("model,mutate", [
    ("glm-5.3", lambda: replaced(row("GLM-5.3"), "")),
    ("glm-5.2", lambda: replaced(row("GLM-5.2"), row("GLM-5.2") * 2)),
    ("glm-5.2", lambda: replaced(row("GLM-5.3"), row("GLM-5.3") + row("GLM-5.2"))),
    ("glm-5.3", lambda: replaced(HEADER, HEADER.replace("Cached Input |", "Cache Read |", 1))),
    ("glm-5.3", lambda: replaced(HEADER, "| Model | Cached Input | Input | Cached Input Storage | Output |")),
    ("glm-5.3", lambda: replaced("Prices per 1M tokens.", "Prices per 1K tokens.")),
    ("glm-5.3", lambda: replaced("Prices per 1M tokens.", "Prices per token.")),
    ("glm-5.3", lambda: replaced("### Latest Models", "### New Models", 1)),
    ("glm-5.3", lambda: replaced("All prices are in USD.", "All prices are in CNY.", 1)),
    ("glm-5.3", lambda: with_cell("GLM-5.3", 1, "Free")),
    ("glm-5.3", lambda: with_cell("GLM-5.3", 2, "-")),
    ("glm-5.3", lambda: with_cell("GLM-5.3", 4, "\\$4.4 / 1K")),
    ("glm-5.3", lambda: with_cell("GLM-5.3", 4, "")),
    ("glm-5.3-flashx", lambda: with_cell("GLM-5.3-FlashX", 2, "\\$0.075/MTok")),
    ("glm-5.3", lambda: b"<html><body><h1>Pricing</h1></body></html>"),
    ("glm-5.3", lambda: b"\xff\xfe"),
], ids=["missing_row", "doubled_row", "row_in_both_tables", "renamed_header", "reordered_header", "unit_1k", "unit_token", "renamed_section",
        "currency", "input_free", "cached_dash", "output_unit", "output_empty", "cached_per_mtok", "html", "not_utf8"])
def test_missing_doubled_reformatted_or_unit_changed_page_fails_closed(model, mutate):
    with pytest.raises(ValueError):
        parse(model, mutate())


def test_only_the_table_holding_the_model_must_state_its_unit():
    text = BODY.decode()
    body = text.replace("### Text Models\n\nPrices per 1M tokens.", "### Text Models\n\n", 1).encode()
    assert body != BODY
    with pytest.raises(ValueError):
        parse("glm-5.2", body)  # GLM-5.2 is in the Text Models table
    assert parse("glm-5.3", body) == parse("glm-5.3")  # GLM-5.3 is in the Latest Models table, which still says per 1M tokens


def test_a_changed_rate_is_one_official_price_change_per_changed_item(tmp_path):
    """A 10% cut of glm-5.3 input and output, cached input unchanged: two events from two runs of the same registered source."""
    _, adapters = core.registry()
    cut = replaced(row("GLM-5.3"), row("GLM-5.3").replace("\\$1.4", "\\$1.26").replace("\\$4.4", "\\$3.96"))
    runs = [core.live(tmp_path, specs=[SPECS["glm-5.3"]], adapters=adapters, fetcher=lambda s, body=body: (body, s.url, 200)) for body in (BODY, cut)]
    builder = Builder(Catalog())
    builder.build([evidence.load_run(run) for run in runs])
    assert builder.state["not_comparable"] == [] and len(builder.state["baselines"]) == 3
    assert sorted((e["billing_item"], e["before"]["amount"], e["after"]["amount"], e["change"]["pct"], e["evidence_level"])
                  for e in builder.state["events"].values()) == [("input", "1.4", "1.26", "-10.00", "official"), ("output", "4.4", "3.96", "-10.00", "official")]
