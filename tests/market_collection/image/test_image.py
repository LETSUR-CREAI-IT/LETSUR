"""Saved-source excerpts from data/research/20261001T060236018508Z-a56a9ea7/raw.

These fixtures are shortened raw evidence, not synthetic prices or live observations.
"""

from pathlib import Path

import pytest

from scripts.market_collection.adapters.image import parse_source, source_specs
from scripts.market_collection.core import _validate_parse, validate_registry


FIXTURES = Path(__file__).parent / "fixtures"
FILES = {
    "openai_sunburst": "openai_sunburst.md",
    "fal_sunburst_t2i": "fal_task.html",
    "fal_sunburst_overview": "fal_overview.html",
    "openrouter_sunburst": "openrouter.html",
    "vercel_sunburst": "vercel.html",
}
SPECS = {spec.source_key: spec for spec in source_specs() if spec.parser != "google_model_pricing"}


def _body(key: str) -> bytes:
    return (FIXTURES / FILES[key]).read_bytes()


def _parse(key: str, body: bytes | None = None):
    spec = SPECS[key]
    return _validate_parse(spec, "saved-s1", *parse_source(spec, body if body is not None else _body(key), "saved-s1"))


def test_fixed_five_pages_four_routes():
    validate_registry(source_specs())
    assert {key for key in SPECS if not key.startswith('image25_')} == set(FILES) | {"openai_flare", "fal_flare_t2i", "openrouter_flare", "vercel_flare", "azure_sunburst", "azure_flare", "openai_gpt_image_2"}
    assert {spec.route for spec in SPECS.values()} == {"openai", "fal", "openrouter", "vercel", "azure_foundry"}
    assert {spec.model_id for spec in SPECS.values()} == {"gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2"}
    assert {spec.task for spec in SPECS.values()} == {"text-to-image", "model-conditions"}


@pytest.mark.parametrize("key,expected", [
    ("openai_sunburst", 6), ("fal_sunburst_t2i", 6),
    ("fal_sunburst_overview", 1), ("openrouter_sunburst", 5), ("vercel_sunburst", 4),
])
def test_saved_raw_excerpts_validate_core_contract(key, expected):
    prices, _ = _parse(key)
    assert len(prices) == expected
    assert all(price["locator"] for price in prices)
    assert all(price["currency"] == "USD" for price in prices if price["amount"])


def test_generic_labels_remain_generic():
    openrouter, _ = _parse("openrouter_sunburst")
    assert {p["billing_item"] for p in openrouter} == {
        "Input Price", "Output Price", "Cache Read", "Text Input", "Image Output"
    }
    vercel, _ = _parse("vercel_sunburst")
    assert {p["billing_item"] for p in vercel} == {
        "Input", "Output", "imageInputCost", "cachedInputCost"
    }


def test_fal_conflict_and_unknown_applicability_stay_separate():
    task, task_conditions = _parse("fal_sunburst_t2i")
    overview, overview_conditions = _parse("fal_sunburst_overview")
    text_output = next(p for p in task if p["billing_item"] == "Text tokens / Output")
    assert text_output["amount"] == "10"
    assert text_output["price_kind"] == "disputed_claim"
    assert text_output["evidence_status"] == "conflicted"
    assert overview[0]["amount"] == ""
    assert overview[0]["unknown_reason"]
    assert overview[0]["evidence_status"] == "conflicted"
    assert overview_conditions[0]["status"] == "unknown"
    assert any(c["condition_key"] == "quality_price_applicability" and c["status"] == "unknown"
               for c in task_conditions)


def test_mutated_fal_amount_changes_scoped_fact_and_locator():
    body = _body("fal_sunburst_t2i")
    changed = body.replace(b"$10.00 output", b"$11.00 output", 1)
    original, _ = _parse("fal_sunburst_t2i", body)
    mutated, _ = _parse("fal_sunburst_t2i", changed)
    assert original[2]["amount"] == "10"
    assert mutated[2]["amount"] == "11"
    assert "$11.00 output" in mutated[2]["locator"]


@pytest.mark.parametrize("key,old,new", [
    ("openai_sunburst", b"Model ID: `gpt-image-2.5-sunburst`", b"Model ID: `other-model`"),
    ("fal_sunburst_t2i", b"openai/gpt-image-2.5/sunburst/text-to-image", b"other-model"),
    ("fal_sunburst_overview", b"openai/gpt-image-2.5/sunburst/text-to-image", b"other-model"),
    ("openrouter_sunburst", b'"provider_model_id":"gpt-image-2.5-sunburst"', b'"provider_model_id":"other-model"'),
    ("vercel_sunburst", b'"primaryModel":"gpt-image-2.5-sunburst"', b'"primaryModel":"other-model"'),
])
def test_missing_exact_model_fails(key, old, new):
    body = _body(key)
    assert old in body
    with pytest.raises(ValueError):
        _parse(key, body.replace(old, new, 1))


@pytest.mark.parametrize("key,old,new", [
    ("openai_sunburst", b"1M tokens", b"tokens"),
    ("fal_sunburst_t2i", b"Text tokens (per 1M)", b"Text tokens (per unknown)"),
    ("openrouter_sunburst", b'"unitLabel":"/M tokens"', b'"unitLabel":"/unknown"'),
    ("vercel_sunburst", b"Per 1M tokens", b"Per unknown"),
])
def test_missing_unit_does_not_create_price(key, old, new):
    body = _body(key)
    assert old in body
    with pytest.raises(ValueError):
        _parse(key, body.replace(old, new))
