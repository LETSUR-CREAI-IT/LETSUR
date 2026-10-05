"""Azure Foundry public price parsers."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec
from .common import _Page


def _cloud_condition(set_id: str, key: str, value: str, locator: str) -> dict:
    return dict(zip(ADAPTER_CONDITION_FIELDS, (set_id, key, "eq", value, "", "stated", locator)))


def _cloud_price(model_id: str, upstream: str, item: str, amount: str, set_id: str, locator: str, reason: str = "") -> dict:
    row = dict.fromkeys(ADAPTER_PRICE_FIELDS, "")
    row.update(provider_model_id=model_id, upstream_provider=upstream, task="text_generation",
               billing_item=item, price_kind="published_rate", amount=amount,
               currency="USD" if amount else "", unit="1M tokens" if amount else "",
               condition_set_id=set_id, locator=locator, evidence_status="direct", unknown_reason=reason)
    return row


def _azure(page: _Page, model: str) -> tuple[list[dict], list[dict]]:
    if page.h1 != [model] or page.h2.count("Pricing") != 1 or not any(
        label == "View pricing" and href.startswith("https://aka.ms/") for _, label, href in page.links
    ):
        return [], []
    # The quick-facts link is a general rate page, not a model-specific price.
    locator = f"{model} > Pricing; Quick facts > View pricing link"
    set_id = f"cloud_azure_{model.replace('.', '_').replace('-', '_')}_listing"
    conditions = [_cloud_condition(set_id, "price_basis", "Azure Foundry catalog listing", locator)]
    facts = [value for section, value in page.text if section == "Quick facts"]
    paired = dict(zip(facts[::2], facts[1::2]))
    upstream = paired.get("Publisher", "")
    if upstream in ("OpenAI", "Anthropic"):
        conditions.append(_cloud_condition(set_id, "publisher", upstream, locator))
    else:
        upstream = ""
    hosting = paired.get("Hosted on", "")
    if hosting in ("Azure", "Anthropic infrastructure"):
        conditions.append(_cloud_condition(set_id, "hosting", hosting, locator))
    reason = "Azure catalog lists the exact model but quotes no Azure-specific numeric rate"
    return [_cloud_price(model, upstream, item, "", set_id, locator, reason) for item in ("input", "output")], conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    if spec.modality == "image":
        from . import azure_retail
        return azure_retail.parse_source(spec, body, source_id)
    page = _Page(); page.feed(body.decode("utf-8", errors="replace")); page.close()
    return _azure(page, spec.model_id)
