"""Byteplus public price parsers."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec
from scripts.market_collection.models import BYTEPLUS_ID


def is_unrendered_template(body: bytes) -> bool:
    """Recognize the empty SSR template retained in failed public responses."""
    return bool(re.search(rb'<div\s+id=["\']root["\']\s*>\s*<!--<\?- html \?>-->\s*</div>', body)) and b"<!--<?- SSRDataScript ?>-->" in body


def _page(body: bytes) -> str:
    page = html.unescape(body.decode("utf-8", "replace"))
    # BytePlus embeds its document markdown in escaped page state. Decode only
    # character escapes needed for matching; the original bytes remain in raw.
    page = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), page)
    page = re.sub(r"\\+-", "-", page)
    return page.replace(r"\n", "\n").replace(r'\"', '"')


def _video_condition(group: str, key: str, value: str, locator: str, *, status: str = "stated", unit: str = "") -> dict:
    return dict(condition_set_id=group, condition_key=key, operator="=", value=value, unit=unit, status=status, locator=locator)


def _video_price(provider: str, kind: str, amount: str, unit: str, group: str, locator: str, *, status: str = "direct", reason: str = "") -> dict:
    return dict(provider_model_id=provider, model_version="2.5", upstream_provider="ByteDance", task="text-to-video", billing_item="generated_video", price_kind=kind, amount=amount, currency="USD" if amount else "", unit=unit if amount else "", normalized_amount="", normalized_unit="", condition_set_id=group, effective_from="", effective_until="", locator=locator, evidence_status=status, unknown_reason=reason)


def _video_byteplus(page: str, source_id: str) -> tuple[list[dict], list[dict]]:
    # The unit is supplied by the table header, never guessed from cell values.
    row = re.search(r"\|" + re.escape(BYTEPLUS_ID) + r"\b([^\n]*)", page, re.I)
    headers = list(re.finditer(r"\|\*\*Model ID\*\*\s*\|\*\*Online inference\*\*[^\n]*?USD\s*/\s*M tokens", page[:row.start()] if row else "", re.I))
    if not row or not headers or row.start() - headers[-1].start() > 2500:
        raise ValueError("BytePlus Seedance 2.5 model row or USD/M token unit missing")
    prices: list[dict] = []
    conditions: list[dict] = []
    cells = row[1]
    for resolution, pattern in (
        ("480p/720p", r"For\s+480p\s+and\s+720p\s+outputs:.*?Input\s+without\s+video:\s*([0-9.]+)"),
        ("1080p", r"For\s+1080p\s+outputs:.*?Input\s+without\s+video:\s*([0-9.]+)"),
    ):
        match = re.search(pattern, cells, re.I | re.S)
        if match:
            group = f"{source_id}:byteplus_{resolution.replace('/', '_')}"
            prices.append(_video_price(BYTEPLUS_ID, "published_rate", match[1], "1M tokens", group, f"Video generation models > Pricing > {BYTEPLUS_ID}: {match[0]}"))
            conditions.extend([_video_condition(group, "resolution", resolution, "pricing table row"), _video_condition(group, "input_video", "none", "pricing table: Input without video"), _video_condition(group, "inference_mode", "online", "pricing table: Online inference")])
    if not prices:
        raise ValueError("BytePlus Seedance 2.5 input-without-video rate missing")
    if prices:
        group = f"{source_id}:byteplus_formula"
        formula = re.search(r"Estimated token consumption\s*=\s*`([^`]+)`", page, re.I)
        if formula:
            conditions.append(_video_condition(group, "estimated_token_formula", formula[1], f"pricing formula: {formula[0]}"))
        else:
            conditions.append(_video_condition(group, "estimated_token_formula", "unavailable in this response", "Video generation models > pricing formula", status="unknown"))
        actual = re.search(r"actual token consumption is subject to the\s*`?usage\.completion_tokens`?", page, re.I)
        if actual:
            conditions.append(_video_condition(group, "actual_usage", "usage.completion_tokens", f"pricing formula: {actual[0]}"))
        # The promotion text and linked rules name 2.0 mini/fast; neither proves
        # a 2.5 discount. Keep the unresolved applicability explicit.
        if "60% off" in page and "Seedance 2.0 mini" in page:
            conditions.append(_video_condition(group, "seedance_25_discount_eligibility", "unconfirmed: promotion names Seedance 2.0 mini/fast", "Video generation models > Limited-time discount", status="unknown"))
    return prices, conditions


def _video_campaign(page: str, source_id: str) -> tuple[list[dict], list[dict]]:
    if not re.search(r"Seedance\s+2\.0\s+mini", page, re.I) or not re.search(r"60%\s+off", page, re.I):
        raise ValueError("BytePlus Seedance campaign eligibility or discount rule missing")
    group = f"{source_id}:campaign_scope"
    conditions = [
        _video_condition(group, "named_eligible_models", "Seedance 2.0 mini; Seedance 2.0 fast", "campaign rules: eligible models"),
        _video_condition(group, "seedance_25_discount_eligibility", "unconfirmed: Seedance 2.5 is absent from eligible-model rules", "campaign rules: eligible models", status="conflicted"),
    ]
    return [_video_price(BYTEPLUS_ID, "disputed_claim", "", "", group, "campaign rules: Seedance 2.0 mini/fast eligibility", status="conflicted", reason="No confirmed Seedance 2.5 discount or applicable price")], conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    if is_unrendered_template(body):
        raise ValueError("BytePlus unrendered SSR template: document body unavailable")
    return {"byteplus_pricing": _video_byteplus,
            "byteplus_campaign": _video_campaign}[spec.parser](_page(body), source_id)
