"""OpenAI model pages of the text models in OPENAI_DIRECT_MORE_MODELS (its own module: openai_models.py reads OPENAI_DIRECT_MODELS,
so a row added there would change the parser fingerprint of every source it parses). The reading is openai_models.py's Text
tokens reading, with the page title and slug taken from OPENAI_DIRECT_MORE_MODELS."""

from __future__ import annotations

import re

from scripts.market_collection.core import SourceSpec
from scripts.market_collection.models import OPENAI_DIRECT_MORE_MODELS
from scripts.market_collection.sources import _key
from .common import _section, llm_condition
from .openai_models import PROMOTION, TEXT_ITEMS, _identity, _note, _price, _rates

PARSERS = frozenset(f"openai_{_key(model)}" for model, _, _ in OPENAI_DIRECT_MORE_MODELS)


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    """openai_models._text for these models: a missing, repeated or reformatted page, row or note raises."""
    title, slug = {model: (page_title, page) for model, page_title, page in OPENAI_DIRECT_MORE_MODELS}[spec.model_id]
    text = body.decode("utf-8", errors="replace")
    conditions = _identity(spec, text, title, slug)
    section = _section(_section(text, "Pricing", 2), "Text tokens", 3)
    rows = _rates(section, set(TEXT_ITEMS))
    if "Input" not in rows or "Output" not in rows:
        raise ValueError("OpenAI Text tokens Input or Output row absent")
    notes = {"long_context_multiplier": _note(re.findall(r"^- (.*272K input tokens.*)$", section, re.M), "272K rule"),
             "promotion_note": _note(PROMOTION.findall(section), "promotion")}
    prices = []
    for label, item in TEXT_ITEMS.items():
        if label not in rows:
            continue
        set_id = f"{spec.parser}_standard_{item}"
        locator = f"{title} > Pricing > Text tokens > {label}: ${rows[label]} / 1M tokens"
        conditions.append(llm_condition(set_id, "service_tier", "standard", locator))
        for key, note in notes.items():
            if note:
                conditions.append(llm_condition(set_id, key, note, f"{title} > Pricing > Text tokens: {note}"))
        prices.append(_price(spec, provider_model_id=spec.model_id, upstream="OpenAI", item=item, kind="published_rate", amount=rows[label], unit="1M tokens", set_id=set_id, locator=locator))
    return prices, conditions
