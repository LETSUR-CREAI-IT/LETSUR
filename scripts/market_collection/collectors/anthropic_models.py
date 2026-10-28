"""Anthropic's price page for the models LETSUR calls directly (its own module: the other Anthropic parsers keep their fingerprints)."""

from __future__ import annotations

from scripts.market_collection.core import SourceSpec
from scripts.market_collection.models import ANTHROPIC_DIRECT_MODELS
from scripts.market_collection.sources import _key
from .anthropic import _anthropic_model
from .common import llm_condition

PARSERS = {f"anthropic_{_key(model)}" for model, _ in ANTHROPIC_DIRECT_MODELS}
# Haiku 5.5 has one row per prompt length, each title ending in its band. The compare table reads a band from ">NK".
HAIKU = "claude-haiku-5-5"
BANDS = (("up to", "<=100K"), ("over", ">100K"))


def _row(body: str, model: str, title: str, parser: str) -> tuple[list[dict], list[dict]]:
    prices, conditions = _anthropic_model(body, model, title, parser)
    if len(prices) != 5:  # one exact row with all five cells as "$N / MTok"; anything else is a changed page, never a neighbour's rate
        raise ValueError(f"Anthropic price row absent, duplicated or reformatted: {title}")
    return prices, conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    text = body.decode("utf-8", errors="replace")
    title = dict(ANTHROPIC_DIRECT_MODELS)[spec.model_id]
    if spec.model_id != HAIKU:
        return _row(text, spec.model_id, title, spec.parser)
    prices, conditions = [], []
    for word, band in BANDS:
        own_prices, own_conditions = _row(text, spec.model_id, f"{title} (for prompts {word} 100,000 tokens)", f"{spec.parser}_{word.replace(' ', '_')}")
        conditions.extend(own_conditions)
        conditions.extend(llm_condition(price["condition_set_id"], "input_token_band", band, price["locator"]) for price in own_prices)
        prices.extend(own_prices)
    return prices, conditions
