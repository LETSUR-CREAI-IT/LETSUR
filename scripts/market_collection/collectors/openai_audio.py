"""OpenAI's pricing page, Realtime and audio generation models, for its audio models (2026-11-03): gpt-audio-mini's model page has
no audio rates, and this table has both rates of both models. Its own module, so the other OpenAI parsers keep their parser
fingerprints. The model's one Text and one Audio row are read; anything else raises."""

from __future__ import annotations

import re

from scripts.market_collection.core import SourceSpec
from .common import _number, llm_condition
from .openai_models import AMOUNT, _once, _price, _table

PARSERS = frozenset({"openai_audio_pricing"})
HEADER = ["Model", "Modality", "Input", "Output / cost", "Cached input"]
ITEMS = {("Text", "Input"): "input", ("Text", "Output / cost"): "output", ("Text", "Cached input"): "cache_read",
         ("Audio", "Input"): "audio_input", ("Audio", "Output / cost"): "audio_output", ("Audio", "Cached input"): "audio_cache_read"}
REQUIRED = {"input", "output", "audio_input", "audio_output"}


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    lines = body.decode("utf-8", errors="replace").splitlines()
    top = _once(lines, "Realtime and audio generation models")
    bottom = _once(lines, "Image generation models", top + 1)
    _once(lines, "Prices per 1M tokens unless noted.", top, bottom)
    own = [cells for cells in _table(lines[top:bottom], HEADER, "audio pricing") if cells[:1] == [spec.model_id]]
    if len(own) != 2 or any(len(cells) != len(HEADER) for cells in own) or {cells[1] for cells in own} != {"Text", "Audio"}:
        raise ValueError(f"OpenAI audio pricing rows of {spec.model_id} absent, repeated or reformatted")
    prices, conditions = [], []
    for _, modality, *rates in own:
        for column, cell in zip(HEADER[2:], rates):
            item = ITEMS[modality, column]
            if cell == "-":  # no such rate listed
                continue
            if not re.fullmatch(AMOUNT, cell):
                raise ValueError(f"OpenAI audio price cell reformatted: {spec.model_id} {modality} {column}")
            set_id = f"{spec.source_key}_standard_{item}"
            locator = f"Realtime and audio generation models > {spec.model_id} > {modality} > {column}: {cell} / 1M tokens"
            conditions.append(llm_condition(set_id, "service_tier", "standard", locator))
            prices.append(_price(spec, provider_model_id=spec.model_id, upstream="OpenAI", item=item, kind="published_rate",
                                 amount=_number(cell[1:]), unit="1M tokens", set_id=set_id, locator=locator))
    if not REQUIRED <= {price["billing_item"] for price in prices}:
        raise ValueError(f"OpenAI audio pricing of {spec.model_id} lacks a text or audio rate")
    return prices, conditions
