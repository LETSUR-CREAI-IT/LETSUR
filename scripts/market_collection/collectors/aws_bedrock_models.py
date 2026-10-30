"""AWS Bedrock model cards of the OpenAI models LETSUR calls there that aws_bedrock.py does not list (its own module: that
file's sources keep their parser fingerprints). Same card layout, helpers and Standard Commercial tables as there; this module
only reads the geographic inference option as AWS writes it on the newer cards, "Geo CRIS" (older cards: "US Geo CRIS")."""

from __future__ import annotations

import re

from scripts.market_collection.core import SourceSpec
from scripts.market_collection.models import AWS_OPENAI_MORE_MODELS
from scripts.market_collection.sources import _key
from .aws_bedrock import _aws_identity, _cloud_condition, _cloud_price
from .common import _Page, _table_rows

PARSERS = {f"cloud_bedrock_{_key(model)}" for model, _, _ in AWS_OPENAI_MORE_MODELS}
HEADING = re.compile(r"(?:Standard — )?Commercial Regions(?: — |, )(short|long) context \(([^)]+)\)")
OPTION = re.compile(r"In-Region|Geo CRIS|Global CRIS")
ITEMS = {"Input": "input", "Input — cache read": "cache_read", "Output": "output", "Input — cache write": "cache_write",
         "Input — 30m cache write": "cache_write", "Input — 30-minute cache write": "cache_write"}


def _bedrock_openai_more(page: _Page, model: str, title: str) -> tuple[list[dict], list[dict]]:
    provider_id = _aws_identity(page, title, model)
    pricing_text = " ".join(value for section, value in page.text if section == "Pricing")
    if not provider_id or page.h2.count("Pricing") != 1 or not re.search(r"USD per 1 million tokens", pricing_text) or "Standard" not in pricing_text:
        return [], []
    prices: list[dict] = []
    conditions: list[dict] = []
    scoped_table = False
    for section, heading, table in page.tables:
        match = HEADING.fullmatch(heading) if section == "Pricing" else None
        if not match:
            continue
        scoped_table = True
        headers = [value for _, value in table[0]] if table and table[0] else []
        if (not headers or headers[0] != "Inference option" or not {"Input", "Input — cache read", "Output"} <= set(headers)
                or len(headers) != 5 or len(set(headers)) != 5 or not set(headers[1:]) <= set(ITEMS)):
            continue
        for row in _table_rows(table, set(headers)):
            option = row["Inference option"]
            cells = [(header, ITEMS[header], row[header]) for header in headers[1:]]
            if not OPTION.fullmatch(option or "") or not all(re.fullmatch(r"\$[0-9]+(?:\.[0-9]+)?", cell) for _, _, cell in cells):
                continue  # an option or a cell this module does not know leaves the whole row out, as in aws_bedrock.py
            group_id = f"cloud_{_key(model)}_{match.group(1)}_{re.sub('[^a-z0-9]+', '_', option.lower()).strip('_')}"
            locator = f"{title} > Pricing > {heading} > {option} (USD per 1 million tokens, Standard)"
            for header, item, cell in cells:
                set_id, amount = f"{group_id}_{item}", cell[1:]
                cell_locator = locator + f" > {header}: ${amount}"
                conditions.extend((_cloud_condition(set_id, "service_tier", "Standard", cell_locator),
                                   _cloud_condition(set_id, "region_group", "Commercial Regions", cell_locator),
                                   _cloud_condition(set_id, "context_band", match.group(2), cell_locator),
                                   _cloud_condition(set_id, "inference_option", option, cell_locator)))
                if header in {"Input — 30m cache write", "Input — 30-minute cache write"}:
                    conditions.append(_cloud_condition(set_id, "cache_write_ttl", "30m", cell_locator))
                if item in ("cache_write", "cache_read") and "Explicit prompt caching is not supported" in pricing_text:
                    conditions.append(_cloud_condition(set_id, "explicit_prompt_caching", "not supported", cell_locator))
                prices.append(_cloud_price(provider_id, "OpenAI", item, amount, set_id, cell_locator))
    if scoped_table and not prices:
        raise ValueError("AWS scoped Standard Commercial pricing tables contain no parseable prices")
    return prices, conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    page = _Page()
    page.feed(body.decode("utf-8", errors="replace"))
    page.close()
    for model, _, title in AWS_OPENAI_MORE_MODELS:
        if spec.model_id == model:
            return _bedrock_openai_more(page, model, title)
    return [], []
