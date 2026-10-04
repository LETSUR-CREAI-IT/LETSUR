"""Evidence-scoped GPT Image 2.5 Sunburst public price extraction."""

from __future__ import annotations

import html
import json
import re
from decimal import Decimal

from scripts.market_collection.core import SourceSpec

MODEL = "gpt-image-2.5-sunburst"
FAL_MODEL = "openai/gpt-image-2.5/sunburst/text-to-image"


def source_specs() -> list[SourceSpec]:
    sources = (
        ("openai_sunburst", "openai", "https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst.md", "openai"),
        ("fal_sunburst_t2i", "fal", "https://fal.ai/models/openai/gpt-image-2.5/sunburst/text-to-image", "fal_task"),
        ("fal_sunburst_overview", "fal", "https://fal.ai/gpt-image-2.5", "fal_overview"),
        ("openrouter_sunburst", "openrouter", "https://openrouter.ai/openai/gpt-image-2.5-sunburst-20260908", "openrouter"),
        ("vercel_sunburst", "vercel", "https://vercel.com/ai-gateway/models/gpt-image-2.5-sunburst", "vercel"),
    )
    return [SourceSpec(key, "image", MODEL, route, "text-to-image", url, parser) for key, route, url, parser in sources]


def _price(spec: SourceSpec, label: str, amount: str, unit: str, locator: str,
           *, provider_model_id: str = "", condition_set_id: str = "",
           kind: str = "published_rate", status: str = "direct", unknown: str = "") -> dict[str, str]:
    return dict(provider_model_id=provider_model_id or MODEL, model_version="", upstream_provider="OpenAI",
                task=spec.task, billing_item=label, price_kind=kind, amount=amount,
                currency="USD" if amount else "", unit=unit if amount else "",
                normalized_amount="", normalized_unit="", condition_set_id=condition_set_id,
                effective_from="", effective_until="", locator=locator,
                evidence_status=status, unknown_reason=unknown)


def _condition(group: str, key: str, value: str, locator: str, *, status: str = "stated") -> dict[str, str]:
    return dict(condition_set_id=group, condition_key=key, operator="=", value=value,
                unit="", status=status, locator=locator)


def _number(value: str) -> str:
    return format(Decimal(value.replace(",", "")).normalize(), "f")


def _plain(body: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()


def _openai(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    if f"Model ID: `{MODEL}`" not in body:
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
            prices.append(_price(spec, f"{heading} tokens / {label}", _number(amount), unit,
                                 f"### {heading} tokens | {match.group(0)}",
                                 condition_set_id="openai_standard_image_output" if standard_output else ""))
    if not prices:
        raise ValueError("OpenAI scoped prices absent")
    if batch:
        conditions.append(_condition("openai_standard_image_output", "processing", "Standard",
                                     f"Standard image output | {batch.group(0)}"))
        group = "openai_batch_image_output"
        prices.append(_price(spec, "Image tokens / Output", _number(batch.group(1)), "1M tokens",
                             f"Batch image output | {batch.group(0)}", condition_set_id=group))
        conditions.append(_condition(group, "processing", "Batch", f"Batch image output | {batch.group(0)}"))
    return prices, conditions


_FAL_RATES = re.compile(
    r"Text tokens \(per 1M\):\s*\$([\d,.]+) input,\s*\$([\d,.]+) cached,\s*\$([\d,.]+) output\.\s*"
    r"Image tokens \(per 1M\):\s*\$([\d,.]+) input,\s*\$([\d,.]+) cached,\s*\$([\d,.]+) output",
    re.I,
)


def _fal_task(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    if FAL_MODEL not in body or "GPT Image 2.5 Sunburst" not in body:
        raise ValueError("fal exact task model absent")
    visible = _plain(body)
    match = _FAL_RATES.search(visible)
    if not match:
        raise ValueError("fal task scoped token rates or unit absent")
    labels = ("Text tokens / Input", "Text tokens / Cached input", "Text tokens / Output",
              "Image tokens / Input", "Image tokens / Cached input", "Image tokens / Output")
    locator = f"fal task Pricing | {match.group(0)}"
    group = "fal_task_token_rates"
    prices = [_price(spec, label, _number(match.group(index)), "1M tokens", locator,
                     provider_model_id=FAL_MODEL, condition_set_id=group,
                     kind="disputed_claim" if label == "Text tokens / Output" else "published_rate",
                     status="conflicted" if label == "Text tokens / Output" else "direct")
              for index, label in enumerate(labels, 1)]
    conditions = [_condition(group, "task", "text-to-image", f"fal task identifier | {FAL_MODEL}")]
    quality = re.search(r"Changing the quality parameter significantly affects cost; by default we use high", visible, re.I)
    if quality:
        conditions.append(_condition(group, "quality_price_applicability", "unverified by token rate",
                                     f"fal task Pricing | {quality.group(0)}", status="unknown"))
    return prices, conditions


def _fal_overview(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    visible = _plain(body)
    if "GPT Image 2.5 Sunburst" not in visible or FAL_MODEL not in body:
        raise ValueError("fal overview Sunburst identity absent")
    claim = re.search(r"There is no charge for text output\.", visible, re.I)
    if not claim:
        raise ValueError("fal overview text output claim absent")
    group = "fal_overview_text_output"
    locator = f"fal overview FAQ | {claim.group(0)}"
    price = _price(spec, "Text tokens / Output", "", "", locator,
                   provider_model_id=FAL_MODEL, condition_set_id=group,
                   kind="disputed_claim", status="conflicted",
                   unknown="overview says no charge but specifies no unit; Sunburst task applicability unconfirmed")
    condition = _condition(group, "sunburst_applicability", "unconfirmed", locator, status="unknown")
    return [price], [condition]


def _openrouter(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    decoded = body.replace('\\"', '"')
    match = re.search(r'"provider_model_id":"' + MODEL + r'"(.{0,12000}?)"pricing_version_id":"([^"]+)"', decoded, re.S)
    if not match:
        raise ValueError("OpenRouter exact provider model and pricing version absent")
    scoped, version = match.groups()
    displays = list(re.finditer(r'"display_pricing":(\[.*?\])', scoped, re.S))
    if not displays or '"pricing_json":' not in scoped:
        raise ValueError("OpenRouter model-scoped display prices absent")
    entries = json.loads(displays[-1].group(1))
    prices: list[dict] = []
    for entry in entries:
        label = entry.get("sku_label", "")
        if entry.get("kind") != "token" or entry.get("displayMultiplier") != 1000000 or entry.get("unitLabel") != "/M tokens":
            raise ValueError("OpenRouter unsupported or missing unit")
        if label not in {"Input Price", "Output Price", "Cache Read", "Text Input", "Image Output"}:
            continue
        amount = entry.get("price")
        if not isinstance(amount, str):
            raise ValueError("OpenRouter price absent")
        prices.append(_price(spec, label, _number(str(Decimal(amount) * 1000000)), "1M tokens",
                             f"provider_model_id={MODEL}; pricing_version_id={version}; display_pricing/{label}: {json.dumps(entry, sort_keys=True)}"))
    if not prices:
        raise ValueError("OpenRouter scoped rates absent")
    return prices, []


def _vercel(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    decoded = body.replace('\\"', '"')
    match = re.search(r'"primaryModel":"' + MODEL + r'"(.{0,5000}?)\}', decoded, re.S)
    if not match or '"provider":"openai"' not in match.group(1) or '"type":"image"' not in match.group(1):
        raise ValueError("Vercel exact image backing model absent")
    scoped = match.group(1)
    visible = re.search(r'Input \$([\d,.]+), Output \$([\d,.]+)(?:<!-- -->)?, Per 1M tokens', body)
    if not visible:
        raise ValueError("Vercel visible price unit absent")
    fields = (("inputCost", "Input"), ("outputCost", "Output"),
              ("imageInputCost", "imageInputCost"), ("cachedInputCost", "cachedInputCost"))
    prices = []
    for field, label in fields:
        value = re.search(r'"' + field + r'":"([\d,.]+)"', scoped)
        if not value:
            continue
        if field in {"inputCost", "outputCost"} and _number(value.group(1)) != _number(visible.group(1 if field == "inputCost" else 2)):
            raise ValueError("Vercel visible and backing prices differ")
        prices.append(_price(spec, label, _number(value.group(1)), "1M tokens",
                             f"primaryModel={MODEL}; backingModels/{field}={value.group(1)}; visible: {visible.group(0)}"))
    if not prices:
        raise ValueError("Vercel scoped rates absent")
    return prices, []


_PARSERS = {"openai": _openai, "fal_task": _fal_task, "fal_overview": _fal_overview,
            "openrouter": _openrouter, "vercel": _vercel}


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    if spec not in source_specs() or not source_id or not isinstance(body, bytes):
        raise ValueError("unregistered image source or invalid body/source ID")
    return _PARSERS[spec.parser](spec, body.decode("utf-8"))
