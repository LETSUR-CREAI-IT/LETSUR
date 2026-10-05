"""Aws Bedrock public price parsers."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec
from scripts.market_collection.sources import _BEDROCK_PRICING
from .common import _Page, _table_rows, llm_price, _markdown_identity, _section


def _cloud_condition(set_id: str, key: str, value: str, locator: str) -> dict:
    return dict(zip(ADAPTER_CONDITION_FIELDS, (set_id, key, "eq", value, "", "stated", locator)))


def _cloud_price(model_id: str, upstream: str, item: str, amount: str, set_id: str, locator: str, reason: str = "") -> dict:
    row = dict.fromkeys(ADAPTER_PRICE_FIELDS, "")
    row.update(provider_model_id=model_id, upstream_provider=upstream, task="text_generation",
               billing_item=item, price_kind="published_rate", amount=amount,
               currency="USD" if amount else "", unit="1M tokens" if amount else "",
               condition_set_id=set_id, locator=locator, evidence_status="direct", unknown_reason=reason)
    return row


def _aws_identity(page: _Page, title: str, model: str) -> str:
    if page.h1 != [title]:
        return ""
    if model == "claude-haiku-4-5":
        # This card's bedrock-runtime Model ID is N/A. Its published inference
        # IDs include an exact dated version; the canonical ID is not invented.
        target = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
        valid = lambda row: row.get("Endpoint") == "bedrock-runtime" and row.get("Global inference ID") == target
    else:
        target = ("openai." if model.startswith("gpt-") else "anthropic.") + model
        valid = lambda row: row.get("Endpoint") in ("bedrock-runtime", "bedrock-mantle") and row.get("Model ID") == target
    for section, _, table in page.tables:
        if section not in ("Programmatic Access", "Call the model"):
            continue
        if any(valid(row) for row in _table_rows(table, {"Endpoint", "Model ID", "Global inference ID"})):
            return target
    return ""


def _bedrock_openai(page: _Page, model: str, title: str) -> tuple[list[dict], list[dict]]:
    provider_id = _aws_identity(page, title, model)
    pricing_text = " ".join(value for section, value in page.text if section == "Pricing")
    if not provider_id or page.h2.count("Pricing") != 1 or not re.search(r"USD per 1 million tokens", pricing_text):
        return [], []
    if "Standard" not in pricing_text and not any(h.startswith("Standard — Commercial Regions") for _, h, _ in page.tables):
        return [], []
    prices: list[dict] = []
    conditions: list[dict] = []
    for section, heading, table in page.tables:
        if section != "Pricing":
            continue
        match = re.fullmatch(r"(?:Standard — )?Commercial Regions(?: — |, )(short|long) context \(([^)]+)\)", heading)
        if not match:
            continue
        if not table or not table[0]:
            continue
        headers = [value for _, value in table[0]]
        header_items = {"Input": "input", "Input — cache read": "cache_read", "Output": "output",
                        "Input — cache write": "cache_write", "Input — 30m cache write": "cache_write"}
        if headers[0] != "Inference option" or not {"Input", "Input — cache read", "Output"} <= set(headers) or len(headers) != 5 or len(set(headers)) != 5 or not set(headers[1:]) <= set(header_items):
            continue
        for row in _table_rows(table, set(headers)):
            option = row["Inference option"]
            if not option or not re.fullmatch(r"(?:In-Region|Mantle in-Region|US Geo CRIS|Global CRIS|Regional \(Mantle in IAD\))", option):
                continue
            group_id = f"cloud_{model.replace('.', '_').replace('-', '_')}_{match.group(1)}_{re.sub('[^a-z0-9]+', '_', option.lower()).strip('_')}"
            locator = f"{title} > Pricing > {heading} > {option} (USD per 1 million tokens, Standard)"
            parsed: list[tuple[str, str, str]] = []
            for header in headers[1:]:
                cell = row[header]
                if not re.fullmatch(r"\$[0-9]+(?:\.[0-9]+)?", cell):
                    parsed = []
                    break
                parsed.append((header, header_items[header], cell[1:]))
            if not parsed:
                continue
            for header, item, amount in parsed:
                set_id = f"{group_id}_{item}"
                cell_locator = locator + f" > {header}: ${amount}"
                conditions.extend((_cloud_condition(set_id, "service_tier", "Standard", cell_locator),
                                   _cloud_condition(set_id, "region_group", "Commercial Regions", cell_locator),
                                   _cloud_condition(set_id, "context_band", match.group(2), cell_locator),
                                   _cloud_condition(set_id, "inference_option", option, cell_locator)))
                if header == "Input — 30m cache write":
                    conditions.append(_cloud_condition(set_id, "cache_write_ttl", "30m", cell_locator))
                if item in ("cache_write", "cache_read") and "Explicit prompt caching is not supported" in pricing_text:
                    conditions.append(_cloud_condition(set_id, "explicit_prompt_caching", "not supported", cell_locator))
                prices.append(_cloud_price(provider_id, "OpenAI", item, amount, set_id, cell_locator))
    return prices, conditions


def _bedrock_anthropic(page: _Page, model: str, title: str) -> tuple[list[dict], list[dict]]:
    provider_id = _aws_identity(page, title, model)
    if not provider_id or page.h2.count("Pricing") != 1 or not any(
        section == "Pricing" and label == "Amazon Bedrock Pricing" and href == _BEDROCK_PRICING
        for section, label, href in page.links
    ):
        return [], []
    pricing_text = " ".join(value for section, value in page.text if section == "Pricing")
    locator = f"{title} > Programmatic Access > {provider_id}; Pricing > Amazon Bedrock Pricing link"
    reason = "AWS model card identifies an inference ID but quotes no numeric rate; separate Bedrock pricing page required"
    conditions: list[dict] = []
    if "offered and billed through AWS Marketplace" in pricing_text:
        conditions.append(_cloud_condition(f"cloud_{model.replace('-', '_')}_listing", "billing_channel", "AWS Marketplace", locator))
    set_id = conditions[0]["condition_set_id"] if conditions else ""
    return [_cloud_price(provider_id, "Anthropic", item, "", set_id, locator, reason) for item in ("input", "output")], conditions


def _bedrock_fable(body: str) -> tuple[list[dict], list[dict]]:
    if not _markdown_identity(body, "Claude Fable 5.1"):
        return [], []
    section = _section(body, "Programmatic Access", 2)
    pricing = _section(body, "Pricing", 2)
    if not re.search(r"\|\s*bedrock-runtime\s*\|\s*anthropic\.claude-fable-5-1\s*\|", section) or not re.search(r"\[Amazon Bedrock Pricing\]\(https://aws\.amazon\.com/bedrock/pricing/\)", pricing):
        return [], []
    locator = "Claude Fable 5.1 > Programmatic Access > bedrock-runtime: anthropic.claude-fable-5-1; Pricing > Amazon Bedrock Pricing link"
    reason = "AWS model card identifies model ID but quotes no numeric rate; separate Bedrock pricing page required"
    return [llm_price(provider_model_id="anthropic.claude-fable-5-1", upstream="Anthropic", item=item, kind="published_rate", amount="", unit="", set_id="", locator=locator, unknown_reason=reason) for item in ("input", "output")], []


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    decoded = body.decode("utf-8", errors="replace")
    if spec.parser == "bedrock_fable":
        return _bedrock_fable(decoded)
    from scripts.market_collection.models import _AWS_OPENAI, _AWS_ANTHROPIC
    page = _Page(); page.feed(decoded); page.close()
    for model, _, title in _AWS_OPENAI:
        if spec.model_id == model:
            return _bedrock_openai(page, model, title)
    for model, _, title in _AWS_ANTHROPIC:
        if spec.model_id == model:
            return _bedrock_anthropic(page, model, title)
    return [], []
