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

# The cache-read cell's own footnote: "<sup>N Cache hits and refreshes on A and B are priced at 0.025x the base input price.</sup>".
# Only a footnote whose number is on this model's cell and whose model list names this exact model states its rule; the
# general "All other models use the standard 0.1x multiplier." line and the ratio of the two prices are never used.
CACHE_READ_NOTE = re.compile(r"^\*?<sup>(\d+) Cache hits and refreshes on (.+?) are priced at ([0-9]+(?:\.[0-9]+)?)x the base input price\.</sup>\*?$")


def _cache_read_rule(section: str, cell: str, title: str) -> tuple[str, str] | None:
    """(multiplier, locator) stated by the footnote of this cell for this model, or None."""
    mark = re.search(r"<sup>(\d+)</sup>$", cell)
    if not mark:
        return None
    notes = [m for m in (CACHE_READ_NOTE.match(line.strip()) for line in section.splitlines()) if m and m.group(1) == mark.group(1)]
    if len(notes) != 1 or title not in re.split(r",\s*(?:and\s+)?|\s+and\s+", notes[0].group(2)):
        return None
    return notes[0].group(3), f"Model pricing > footnote {mark.group(1)}: {notes[0].group(0).strip('*')}"


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
        rule = _cache_read_rule(section, row[index], title) if item == "cache_read" else None
        if rule:
            conditions.append(llm_condition(set_id, "cache_read_multiplier", rule[0], rule[1], unit="x base input price"))
        prices.append(llm_price(provider_model_id=model, upstream="Anthropic", item=item, kind="published_rate", amount=match.group(1), unit="1M tokens", set_id=set_id, locator=locator))
    return prices, conditions


# Models priced by prompt length have one row of the model pricing table per tier, so each tier is a series of its own.
# As the gateway endpoint rows do (llm_endpoints.py), only the tier above the threshold states prompt_tokens.
PROMPT_TIERS = {
    "claude-haiku-5-5": (("Claude Haiku 5.5 (for prompts up to 100,000 tokens)", "up_to_100k", ""),
                         ("Claude Haiku 5.5 (for prompts over 100,000 tokens)", "over_100k", "100000")),
}


def _anthropic_prompt_tiers(body: str, model: str, parser: str) -> tuple[list[dict], list[dict]]:
    """Every tier row must be present with all five prices: a renamed row or a missing cell raises instead of collecting less."""
    prices, conditions = [], []
    for title, name, over in PROMPT_TIERS[model]:
        tier_prices, tier_conditions = _anthropic_model(body, model, title, f"{parser}_{name}")
        if len(tier_prices) != 5:
            raise ValueError(f"anthropic pricing page has no complete row for {title!r}")
        if over:
            tier_conditions += [dict(llm_condition(price["condition_set_id"], "prompt_tokens", over, f"Model pricing > {title}"), operator="gt") for price in tier_prices]
        prices += tier_prices
        conditions += tier_conditions
    return prices, conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    from scripts.market_collection.sources import _targets
    if spec.model_id in PROMPT_TIERS:
        return _anthropic_prompt_tiers(body.decode("utf-8", errors="replace"), spec.model_id, spec.parser)
    if spec.parser.startswith("missing_"):
        model, title = "claude-sonnet-5", "Claude Sonnet 5"
    else:
        model, title = spec.model_id, _targets()[spec.model_id][0]
    return _anthropic_model(body.decode("utf-8", errors="replace"), model, title, spec.parser)
