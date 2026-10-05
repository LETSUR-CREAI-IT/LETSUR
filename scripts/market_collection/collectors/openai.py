"""Openai public price parsers."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec
from scripts.market_collection.models import SUNBURST_MODEL, FLARE_MODEL
from .common import llm_condition, llm_price, _section, _markdown_identity, _number


def _openai_model(body: str, model: str, title: str, parser: str) -> tuple[list[dict], list[dict]]:
    if not _markdown_identity(body, title, model):
        return [], []
    pricing = _section(body, "Pricing", 2)
    section = _section(pricing, "Text tokens", 3)
    if not section:
        return [], []
    rows = {}
    for label in ("Input", "Cached input", "Cache writes", "Output"):
        matches = re.findall(r"^\|\s*" + re.escape(label) + r"\s*\|\s*\$([0-9]+(?:\.[0-9]+)?)\s*\|\s*(1M tokens)\s*\|\s*$", section, re.M)
        if len(matches) == 1:
            rows[label] = matches[0][0]
    prices, conditions = [], []
    note = re.search(r"^- Prompts with (?:more than |>)272K input tokens are priced at 2x input(?: and cache rates)? and 1\.5x output for the full request\.$", section, re.M)
    promo = re.search(r"promotional pricing is available at least through November 21, 2026\.", section) if model == "gpt-5.6-sol" else None
    for label, item in (("Input", "input"), ("Cached input", "cache_read"), ("Cache writes", "cache_write"), ("Output", "output")):
        if label not in rows:
            continue
        set_id = f"{parser}_standard_{item}"
        locator = f"{title} > Pricing > Text tokens > {label}: ${rows[label]} / 1M tokens"
        conditions.append(llm_condition(set_id, "service_tier", "standard", locator))
        if note:
            conditions.append(llm_condition(set_id, "long_context_multiplier", note.group(0).removeprefix("- "), f"{title} > Pricing > Text tokens: {note.group(0)}"))
        if promo:
            conditions.append(llm_condition(set_id, "promotion_note", promo.group(0), f"{title} > Pricing > Text tokens: {promo.group(0)}"))
        prices.append(llm_price(provider_model_id=model, upstream="OpenAI", item=item, kind="published_rate", amount=rows[label], unit="1M tokens", set_id=set_id, locator=locator))
    return prices, conditions


def _sun_price(spec: SourceSpec, label: str, amount: str, unit: str, locator: str,
           *, provider_model_id: str = "", condition_set_id: str = "",
           kind: str = "published_rate", status: str = "direct", unknown: str = "") -> dict[str, str]:
    return dict(provider_model_id=provider_model_id or SUNBURST_MODEL, model_version="", upstream_provider="OpenAI",
                task=spec.task, billing_item=label, price_kind=kind, amount=amount,
                currency="USD" if amount else "", unit=unit if amount else "",
                normalized_amount="", normalized_unit="", condition_set_id=condition_set_id,
                effective_from="", effective_until="", locator=locator,
                evidence_status=status, unknown_reason=unknown)


def _sun_condition(group: str, key: str, value: str, locator: str, *, status: str = "stated") -> dict[str, str]:
    return dict(condition_set_id=group, condition_key=key, operator="=", value=value,
                unit="", status=status, locator=locator)


def _sun_openai(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    if f"Model ID: `{SUNBURST_MODEL}`" not in body:
        raise ValueError("OpenAI exact model ID absent")
    prices: list[dict] = []
    conditions: list[dict] = []
    batch = re.search(r"Image output costs \$[\d.]+ per million tokens with Standard processing or \$([\d.]+) with Batch processing", body)
    for heading in ("Text", "Image"):
        table = re.search(rf"### {heading} tokens\s*(.*?)(?=\n### |\n## |\Z)", body, re.S)
        if not table:
            raise ValueError(f"OpenAI {heading} token table absent")
        for match in re.finditer(r"^\| (Input|Cached input|Output) \| \$([\d,.]+) \| (1M tokens) \|$", table.group(1), re.M):
            label, amount, unit = match.groups()
            standard_output = batch is not None and heading == "Image" and label == "Output"
            prices.append(_sun_price(spec, f"{heading} tokens / {label}", _number(amount), unit,
                                 f"### {heading} tokens | {match.group(0)}",
                                 condition_set_id="openai_standard_image_output" if standard_output else ""))
    if not prices:
        raise ValueError("OpenAI scoped prices absent")
    if batch:
        conditions.append(_sun_condition("openai_standard_image_output", "processing", "Standard",
                                     f"Standard image output | {batch.group(0)}"))
        group = "openai_batch_image_output"
        prices.append(_sun_price(spec, "Image tokens / Output", _number(batch.group(1)), "1M tokens",
                             f"Batch image output | {batch.group(0)}", condition_set_id=group))
        conditions.append(_sun_condition(group, "processing", "Batch", f"Batch image output | {batch.group(0)}"))
    return prices, conditions


def _flare_price(spec: SourceSpec, label: str, amount: str, locator: str, *,
           provider_model_id: str = FLARE_MODEL, condition_set_id: str = "",
           kind: str = "published_rate", status: str = "direct",
           unknown: str = "") -> dict[str, str]:
    return dict(provider_model_id=provider_model_id, model_version="", upstream_provider="OpenAI",
                task=spec.task, billing_item=label, price_kind=kind, amount=amount,
                currency="USD" if amount else "", unit="1M tokens" if amount else "",
                normalized_amount="", normalized_unit="", condition_set_id=condition_set_id,
                effective_from="", effective_until="", locator=locator,
                evidence_status=status, unknown_reason=unknown)


def _flare_condition(group: str, key: str, value: str, locator: str,
               *, status: str = "stated") -> dict[str, str]:
    return dict(condition_set_id=group, condition_key=key, operator="=", value=value,
                unit="", status=status, locator=locator)


def _flare_openai(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    if f"Model ID: `{FLARE_MODEL}`" not in body:
        raise ValueError("OpenAI exact Flare model ID absent")
    prices: list[dict] = []
    conditions: list[dict] = []
    batch = re.search(r"Image output costs \$([\d.]+) per million tokens with Standard processing or \$([\d.]+) with Batch processing", body)
    if not batch:
        raise ValueError("OpenAI Flare processing rates absent")
    for heading in ("Text", "Image"):
        table = re.search(rf"### {heading} tokens\s*(.*?)(?=\n### |\n## |\Z)", body, re.S)
        if not table:
            raise ValueError(f"OpenAI Flare {heading} token table absent")
        matches = list(re.finditer(r"^\| (Input|Cached input|Output) \| \$([\d,.]+) \| (1M tokens) \|$", table.group(1), re.M))
        if len(matches) != (2 if heading == "Text" else 3):
            raise ValueError(f"OpenAI Flare {heading} token rows incomplete")
        for match in matches:
            label, amount, _ = match.groups()
            group = "flare_openai_standard_image_output" if heading == "Image" and label == "Output" else ""
            if group and _number(amount) != _number(batch.group(1)):
                raise ValueError("OpenAI Flare Standard output prices disagree")
            prices.append(_flare_price(spec, f"{heading} tokens / {label}", _number(amount),
                                 f"### {heading} tokens | {match.group(0)}", condition_set_id=group))
    standard, batched = "flare_openai_standard_image_output", "flare_openai_batch_image_output"
    conditions.extend((_flare_condition(standard, "processing", "Standard", batch.group(0)),
                       _flare_condition(batched, "processing", "Batch", batch.group(0))))
    prices.append(_flare_price(spec, "Image tokens / Output", _number(batch.group(2)),
                         f"Batch image output | {batch.group(0)}", condition_set_id=batched))
    no_text = re.search(r"Text output is not billed because this model outputs images, not text\.", body)
    if not no_text:
        raise ValueError("OpenAI Flare no-text-output statement absent")
    group = "flare_openai_text_output"
    prices.append(_flare_price(spec, "Text tokens / Output", "", no_text.group(0),
                         condition_set_id=group, kind="disputed_claim",
                         status="conflicted", unknown="OpenAI says text output is not billed; no priced text-output unit"))
    conditions.append(_flare_condition(group, "billing", "not billed", no_text.group(0)))
    return prices, conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    decoded = body.decode("utf-8", errors="replace" if spec.modality == "llm" else "strict")
    if spec.modality == "image":
        return {"openai": _sun_openai, "flare_openai": _flare_openai}[spec.parser](spec, decoded)
    from scripts.market_collection.sources import _targets
    title = _targets()[spec.model_id][0]
    return _openai_model(decoded, spec.model_id, title, spec.parser)
