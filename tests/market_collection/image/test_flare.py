"""Flare excerpts captured from the four public pages, 2026-10-05."""

from pathlib import Path

import pytest

from scripts.market_collection.adapters.image import parse_source, source_specs
from scripts.market_collection.core import _validate_parse


FIXTURES = Path(__file__).parent / "fixtures"
FILES = {
    "openai_flare": "openai_flare.md",
    "fal_flare_t2i": "fal_flare_task.html",
    "openrouter_flare": "openrouter_flare.html",
    "vercel_flare": "vercel_flare.html",
}
SPECS = {spec.source_key: spec for spec in source_specs() if spec.source_key in FILES}


def _body(key: str) -> bytes:
    return (FIXTURES / FILES[key]).read_bytes()


def _parse(key: str, body: bytes | None = None):
    spec = SPECS[key]
    return _validate_parse(spec, "flare-s1", *parse_source(spec, body if body is not None else _body(key), "flare-s1"))


def test_four_exact_source_specs():
    assert set(SPECS) == set(FILES)
    assert all(spec.model_id == "gpt-image-2.5-flare" for spec in SPECS.values())
    assert {spec.route for spec in SPECS.values()} == {"openai", "fal", "openrouter", "vercel"}
    assert SPECS["openrouter_flare"].url.endswith("/gpt-image-2.5-flare")


def test_openai_rates_and_processing_and_nonbillable_text_output():
    prices, conditions = _parse("openai_flare")
    assert len(prices) == 7
    outputs = [p for p in prices if p["billing_item"] == "Image tokens / Output"]
    assert {(p["amount"], p["condition_set_id"]) for p in outputs} == {
        ("30", "flare_openai_standard_image_output"), ("15", "flare_openai_batch_image_output")}
    assert {(c["condition_set_id"], c["value"]) for c in conditions if c["condition_key"] == "processing"} == {
        ("flare_openai_standard_image_output", "Standard"), ("flare_openai_batch_image_output", "Batch")}
    text = next(p for p in prices if p["billing_item"] == "Text tokens / Output")
    assert text["amount"] == "" and text["price_kind"] == "disputed_claim"
    assert "not billed" in text["locator"]


def test_fal_task_claim_stays_supplier_scoped_and_quality_unknown():
    prices, conditions = _parse("fal_flare_t2i")
    assert len(prices) == 6
    assert all(p["provider_model_id"] == "openai/gpt-image-2.5/flare/text-to-image" for p in prices)
    text = next(p for p in prices if p["billing_item"] == "Text tokens / Output")
    assert (text["amount"], text["price_kind"], text["evidence_status"]) == ("10", "disputed_claim", "conflicted")
    assert any(c["condition_key"] == "quality_price_applicability" and c["status"] == "unknown" for c in conditions)


def test_broker_original_skus_and_vercel_starting_basis():
    router, _ = _parse("openrouter_flare")
    assert [(p["billing_item"], p["amount"]) for p in router] == [
        ("Input Price", "8"), ("Output Price", "8"), ("Cache Read", "2"),
        ("Text Input", "5"), ("Image Output", "30")]
    vercel, conditions = _parse("vercel_flare")
    assert [(p["billing_item"], p["amount"]) for p in vercel] == [
        ("Input", "5"), ("Output", "30"), ("imageInputCost", "8"), ("cachedInputCost", "1.25")]
    assert all(p["condition_set_id"] == "flare_vercel_displayed_model_quote" for p in vercel)
    assert conditions[0]["value"] == "displayed model quote"


@pytest.mark.parametrize("key,old,new", [
    ("openai_flare", b"Model ID: `gpt-image-2.5-flare`", b"Model ID: `gpt-image-2.5-sunburst`"),
    ("fal_flare_t2i", b"modelUrl\\\":\\\"https://fal.run/openai/gpt-image-2.5/flare/text-to-image", b"modelUrl\\\":\\\"https://fal.run/openai/gpt-image-2.5/sunburst/text-to-image"),
    ("openrouter_flare", b'provider_model_id\\\":\\\"gpt-image-2.5-flare', b'provider_model_id\\\":\\\"gpt-image-2.5-sunburst'),
    ("vercel_flare", b'primaryModel\\\":\\\"gpt-image-2.5-flare', b'primaryModel\\\":\\\"gpt-image-2.5-sunburst'),
])
def test_adjacent_model_cannot_supply_price(key, old, new):
    body = _body(key)
    assert old in body
    with pytest.raises(ValueError):
        _parse(key, body.replace(old, new, 1))


@pytest.mark.parametrize("key,old,new", [
    ("openai_flare", b"1M tokens", b"tokens"),
    ("fal_flare_t2i", b"Text tokens (per 1M)", b"Text tokens (per unknown)"),
    ("openrouter_flare", b'unitLabel\\\":\\\"/M tokens', b'unitLabel\\\":\\\"/unknown'),
    ("vercel_flare", b"Per 1M tokens", b"Per unknown"),
])
def test_changed_unit_fails_closed(key, old, new):
    body = _body(key)
    assert old in body
    with pytest.raises(ValueError):
        _parse(key, body.replace(old, new))


def test_fal_unrelated_price_block_cannot_supply_flare_rate():
    body = _body("fal_flare_t2i")
    changed = body.replace(b"pricingInfoOverride", b"unrelatedPricingInfo", 1)
    changed += b"<p>Text tokens (per 1M): $5 input, $1 cached, $10 output. Image tokens (per 1M): $8 input, $2 cached, $30 output</p>"
    with pytest.raises(ValueError):
        _parse("fal_flare_t2i", changed)
