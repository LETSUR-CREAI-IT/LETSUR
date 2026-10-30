"""OpenAI's pricing page, Standard pricing data, for the models whose model page cannot say which of its prices is Standard:
o3's page has two Text tokens tables without a tier name (2026-10-30). Its own module, so the other OpenAI parsers keep their
parser fingerprints. The one row of the model in the Input | Output | Cached input table is read; anything else raises."""

from __future__ import annotations

import re

from scripts.market_collection.core import SourceSpec
from .common import llm_condition
from .openai_models import _price

PARSERS = frozenset({"openai_pricing_standard"})
HEADER = ["Model", "Input", "Output", "Cached input"]
ITEMS = {"Input": "input", "Output": "output", "Cached input": "cache_read"}
AMOUNT = re.compile(r"\$(\d+(?:\.\d+)?)")


def _rows(section: str) -> list[list[str]]:
    """The model rows of every table in the section whose header is HEADER."""
    found, header = [], None
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if header is None:
            header = cells
        elif header == HEADER and not set(cells[0]) <= {"-", ":", " "}:
            found.append(cells)
    return found


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    text = body.decode("utf-8", errors="replace")
    # the page has a Standard pricing data section per kind of model (text tokens, then others): the model's row is looked
    # for in all of them and must be there once
    standard = [part for part in re.split(r"^### ", text, flags=re.M) if part.startswith("Standard pricing data\n")]
    if not standard:
        raise ValueError("OpenAI pricing page has no Standard pricing data section")
    rows = [row for part in standard for row in _rows(part) if row[0] == spec.model_id]
    if len(rows) != 1 or len(rows[0]) != len(HEADER):
        raise ValueError(f"OpenAI pricing page Standard table has {len(rows)} rows for {spec.model_id}")
    cells = dict(zip(HEADER, rows[0]))
    prices, conditions = [], []
    for label, item in ITEMS.items():
        if cells[label] == "-":
            continue
        amount = AMOUNT.fullmatch(cells[label])
        if not amount:
            raise ValueError(f"OpenAI pricing page {spec.model_id} {label} is not a dollar amount")
        set_id = f"{spec.source_key}_standard_{item}"
        locator = f"Pricing > Standard pricing data > {spec.model_id} > {label}: {cells[label]} / 1M tokens"
        conditions.append(llm_condition(set_id, "service_tier", "standard", locator))
        prices.append(_price(spec, provider_model_id=spec.model_id, upstream="OpenAI", item=item, kind="published_rate",
                             amount=amount.group(1), unit="1M tokens", set_id=set_id, locator=locator))
    if {price["billing_item"] for price in prices} < {"input", "output"}:
        raise ValueError(f"OpenAI pricing page {spec.model_id} lacks Input or Output")
    return prices, conditions
