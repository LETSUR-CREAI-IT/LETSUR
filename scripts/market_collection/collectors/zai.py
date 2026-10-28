"""Z.ai's own token prices, from the Markdown form of its official pricing page (docs.z.ai/guides/overview/pricing.md).

Two tables under "## Models" list the current models; the "Other Pricing" callout holds older ones and is never read. A model is
read only by its exact row label, from exactly one of the two tables. A missing or doubled row, a changed header, a table that
does not say "per 1M tokens", or a price cell that is not a dollar amount raises: no neighbouring model's row is borrowed.
"""

from __future__ import annotations

import re

from scripts.market_collection.core import SourceSpec
from scripts.market_collection.models import ZAI_DIRECT_MODELS
from .common import llm_condition, llm_price, _markdown_table, _number, _section

SECTIONS = ("Latest Models", "Text Models")
HEADER = ["Model", "Input", "Cached Input", "Cached Input Storage", "Output"]
UNIT_LINE = "Prices per 1M tokens."
# "Cached Input Storage" reads "Limited-time Free" with no unit, so it is not a price row.
ITEMS = ((1, "input"), (2, "cache_read"), (4, "output"))
AMOUNT = re.compile(r"\\?\$([0-9]+(?:\.[0-9]+)?)")  # the page escapes the dollar sign: \$1.4


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    label = dict(ZAI_DIRECT_MODELS).get(spec.model_id)
    if label is None:
        raise ValueError("model has no Z.ai pricing row label")
    text = body.decode("utf-8")
    if "All prices are in USD." not in text:
        raise ValueError("Z.ai page no longer states that prices are in USD")
    models = _section(text, "Models", 2)
    found = []
    for name in SECTIONS:
        section = _section(models, name, 3)
        row = _markdown_table(section, HEADER, label) if section else []
        if row:
            if [line.strip() for line in section.splitlines()].count(UNIT_LINE) != 1:
                raise ValueError(f"Z.ai {name} table of {label} is not stated per 1M tokens")
            found.append((name, row))
    if len(found) != 1:
        raise ValueError(f"Z.ai pricing row {label} is absent, doubled, or under a changed table header")
    name, row = found[0]
    prices, conditions = [], []
    for index, item in ITEMS:
        match = AMOUNT.fullmatch(row[index])
        if not match:
            raise ValueError(f"Z.ai {label} {HEADER[index]} is not a dollar amount: {row[index]}")
        set_id = f"{spec.source_key}_{item}"
        locator = f"Models > {name} > {label} > {HEADER[index]}: {row[index].replace(chr(92), '')}"
        # The table name stays out of the stated value: a model moving from "Latest Models" to "Text Models" keeps its series.
        conditions.append(llm_condition(set_id, "price_basis", "Z.ai pricing page: USD per 1M tokens", locator))
        prices.append(llm_price(provider_model_id=spec.model_id, upstream="Z.ai", item=item, kind="published_rate",
                                amount=_number(match.group(1)), unit="1M tokens", set_id=set_id, locator=locator))
    return prices, conditions
