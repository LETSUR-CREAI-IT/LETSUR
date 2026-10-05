"""Anthropic public price parsers."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec
from .common import llm_condition, llm_price, _section, _markdown_table


def _anthropic_model(body: str, model: str, title: str, parser: str) -> tuple[list[dict], list[dict]]:
    section = _section(body, "Model pricing", 2)
    if not section:
        return [], []
    headers = ["Model", "Base input tokens", "5m cache writes", "1h cache writes", "Cache hits and refreshes", "Output tokens"]
    row = _markdown_table(section, headers, title)
    if not row:
        return [], []
    prices, conditions = [], []
    for index, (item, duration) in enumerate((("input", ""), ("cache_write", "5m"), ("cache_write", "1h"), ("cache_read", ""), ("output", "")), 1):
        match = re.fullmatch(r"\$([0-9]+(?:\.[0-9]+)?)\s*/\s*MTok(?:<sup>\d+</sup>)?", row[index])
        if not match:
            continue
        set_id = f"{parser}_standard_{item}_{duration or 'base'}"
        locator = f"Model pricing > {title} > {headers[index]}: {row[index]}"
        conditions.append(llm_condition(set_id, "service_tier", "standard", locator))
        if duration:
            conditions.append(llm_condition(set_id, "cache_duration", duration, locator))
        prices.append(llm_price(provider_model_id=model, upstream="Anthropic", item=item, kind="published_rate", amount=match.group(1), unit="1M tokens", set_id=set_id, locator=locator))
    return prices, conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    from scripts.market_collection.sources import _targets
    if spec.parser.startswith("missing_"):
        model, title = "claude-sonnet-5", "Claude Sonnet 5"
    else:
        model, title = spec.model_id, _targets()[spec.model_id][0]
    return _anthropic_model(body.decode("utf-8", errors="replace"), model, title, spec.parser)
