"""gpt-image-2 token rates (2026-10-28). OpenAI's page for the model lists no price, so the source is the image generation
section of its pricing page: the trimmed real section, fail-closed row checks, and the run read back into events."""
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.adapters.image import parse_source, source_specs
from scripts.market_collection.events import evidence
from scripts.market_collection.events.catalog import Catalog
from scripts.market_collection.events.engine import Builder

F = Path(__file__).parent / "fixtures" / "openai_pricing"
BODY = (F / "pricing-image-section.md").read_bytes()
SPEC = next(spec for spec in source_specs() if spec.model_id == "gpt-image-2")
STANDARD_IMAGE, BATCH_IMAGE = "| gpt-image-2 | Image | $8.00 | $30.00 | $2.00 |", "| gpt-image-2 | Image | $4.00 | $15.00 | $1.00 |"
# (processing, billing item) -> USD per 1M tokens; text output is "-" on the page, so it has no row
EXPECTED = {
    ("Standard", "Image tokens / Input"): "8", ("Standard", "Image tokens / Output"): "30", ("Standard", "Image tokens / Cached input"): "2",
    ("Standard", "Text tokens / Input"): "5", ("Standard", "Text tokens / Cached input"): "1.25",
    ("Batch", "Image tokens / Input"): "4", ("Batch", "Image tokens / Output"): "15", ("Batch", "Image tokens / Cached input"): "1",
    ("Batch", "Text tokens / Input"): "2.5", ("Batch", "Text tokens / Cached input"): "0.625",
}


def parse(content=BODY, source_id="saved-s1"):
    return core._validate_parse(SPEC, source_id, *parse_source(SPEC, content, source_id))


def edit(old, new, count=1):
    text = BODY.decode()
    assert text.count(old) == count, old
    return text.replace(old, new).encode()


def test_fixture_is_the_recorded_section():
    record = json.loads((F / "provenance.json").read_text())[0]
    assert hashlib.sha256(BODY).hexdigest() == record["fixture_sha256"] and record["source_url"] == SPEC.url


def test_the_pricing_page_is_the_registered_source_of_the_openai_route():
    assert (SPEC.source_key, SPEC.modality, SPEC.route, SPEC.task, SPEC.url, SPEC.parser) == (
        "openai_gpt_image_2", "image", "openai", "text-to-image", "https://developers.openai.com/api/docs/pricing.md", "openai_gpt_image_2")
    info = Catalog().price_source(asdict(SPEC))
    assert (info["role"], info["authority"]) == ("pricing", "manufacturer_official")
    with pytest.raises(ValueError):  # image sources need a source ID at parse time
        parse(source_id="")


def test_standard_and_batch_rates_of_gpt_image_2_only():
    prices, conditions = parse()
    processing = {c["condition_set_id"]: c["value"] for c in conditions if c["condition_key"] == "processing"}
    assert {(processing[p["condition_set_id"]], p["billing_item"]): p["amount"] for p in prices} == EXPECTED and len(prices) == 10
    assert all(p["provider_model_id"] == "gpt-image-2" and p["upstream_provider"] == "OpenAI" and p["unit"] == "1M tokens" and p["currency"] == "USD"
               and p["task"] == "text-to-image" and p["price_kind"] == "published_rate" and p["evidence_status"] == "direct" for p in prices)
    assert all("Image generation models" in p["locator"] and "gpt-image-2 >" in p["locator"] for p in prices)
    assert not any(p["billing_item"] == "Text tokens / Output" for p in prices)


def test_neighbour_rows_are_not_read():
    lines = [re.sub(r"\$\d+(\.\d+)?", "$999", line) if line.startswith(("| gpt-image-2.5", "| gpt-image-1", "| chatgpt-image")) else line
             for line in BODY.decode().splitlines()]
    assert "$999" in "\n".join(lines) and parse("\n".join(lines).encode()) == parse()


@pytest.mark.parametrize("old,new", [
    (STANDARD_IMAGE + "\n", ""),
    (STANDARD_IMAGE, STANDARD_IMAGE + "\n" + STANDARD_IMAGE),
    ("| gpt-image-2 | Text | $5.00 | - | $1.25 |\n", ""),
    (BATCH_IMAGE + "\n", ""),
    (STANDARD_IMAGE, "| gpt-image-2 | Image | - | $30.00 | $2.00 |"),
    (STANDARD_IMAGE, "| gpt-image-2 | Image | $8.00 | - | $2.00 |"),
    (STANDARD_IMAGE, "| gpt-image-2 | Image | $8.00 | $30.00 / 1K | $2.00 |"),
    (STANDARD_IMAGE, "| gpt-image-2 | Image | $8.00 | $30.00 |"),
    (STANDARD_IMAGE, "| gpt-image-2 | Audio | $8.00 | $30.00 | $2.00 |"),
    (STANDARD_IMAGE, "| gpt-image-2.1 | Image | $8.00 | $30.00 | $2.00 |"),
    ("| gpt-image-2 | Text | $5.00 | - | $1.25 |", "| gpt-image-2 | Text | $5.00 | $1.00 | $1.25 |\n| gpt-image-2 | Text | $5.00 | - | $1.25 |"),
    ("Prices per 1M tokens.", "Prices per 1K tokens."),
    ("\nStandard\n", "\n"),
    ("\nBatch\n", "\n"),
    ("Image generation models", "Image models"),
    ("Transcription models", "Speech models"),
], ids=["standard_image_missing", "standard_image_twice", "standard_text_missing", "batch_image_missing", "image_input_dash", "image_output_dash",
        "output_cell", "short_row", "other_modality", "other_model", "text_rows_twice", "unit_line", "standard_label",
        "batch_label", "section_label", "next_section_label"])
def test_missing_repeated_or_reformatted_rows_fail_closed(old, new):
    with pytest.raises(ValueError):
        parse(edit(old, new))


@pytest.mark.parametrize("which", [0, 1], ids=["standard_table", "batch_table"])
def test_a_changed_header_of_either_table_fails_closed(which):
    header, wrong = "| Model | Modality | Input | Output | Cached input |", "| Model | Modality | Input | Output | Cache |"
    first, rest = BODY.decode().split(header, 1)  # the Standard table's header, then everything after it
    changed = first + wrong + rest if which == 0 else first + header + rest.replace(header, wrong, 1)
    assert changed.count(wrong) == 1
    with pytest.raises(ValueError):  # the other table's rates are not lent to the unreadable one
        parse(changed.encode())


def test_live_run_over_the_section_is_read_into_events(tmp_path):
    _, adapters = core.registry()
    runs = [core.live(tmp_path, specs=[SPEC], adapters=adapters, fetcher=lambda spec: (BODY, spec.url, 200)) for _ in range(2)]
    (source,) = core._read_csv(runs[0] / "sources.csv", core.SOURCE_FIELDS)
    assert (source["fetch_status"], source["parse_status"], source["error"]) == ("ok", "ok", "")
    builder = Builder(Catalog())
    builder.build([evidence.load_run(run) for run in runs])
    assert builder.state["events"] == {} and builder.state["not_comparable"] == [] and builder.state["gaps"] == []
    assert len(builder.state["baselines"]) == 10
