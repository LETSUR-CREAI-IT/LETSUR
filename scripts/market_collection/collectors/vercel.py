"""Vercel public price parsers."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec
from scripts.market_collection.models import GOOGLE_BROKER_MODEL, SUNBURST_MODEL, FLARE_MODEL
from .common import GeminiPricingScopeReader, _clean, llm_condition, llm_price, _markdown_identity, _number


MONEY = r"\$([0-9]+(?:\.[0-9]+)?)"


def _broker(reader: GeminiPricingScopeReader, parser: str) -> tuple[list[dict], list[dict]]:
    page = " ".join(reader.scoped_text)
    if not page:
        return [], []
    # React HTML comments may split the sr-only quote into text nodes; the
    # shared reader joins those nodes with layout whitespace.
    pattern = re.compile(rf"Prices from:\s*Input\s*{MONEY}\s*,\s*Output\s*{MONEY}\s*,\s*Per\s+1M\s+tokens", re.I)
    match = pattern.search(page)
    if not match:
        return [], []
    set_id = f"{parser}_prices_from_minimum"
    locator = f"Gemini 3.8 Flash > {match.group(0)}"
    conditions = [llm_condition(set_id, "price_basis", "prices_from_minimum", locator)]
    prices = [llm_price(provider_model_id=GOOGLE_BROKER_MODEL, upstream="", item=item, kind="published_rate",
                        amount=amount, unit="1M tokens", set_id=set_id, locator=locator)
              for item, amount in zip(("input", "output"), match.groups())]
    return prices, conditions


def _vercel_markdown(body: str, title: str, provider_id: str, parser: str) -> tuple[list[dict], list[dict]]:
    lines = body.strip().splitlines()
    if not lines or lines[0] != "# " + title:
        return [], []
    end = next((i for i, line in enumerate(lines[1:], 1) if re.match(r"^#{1,2}\s", line)), len(lines))
    scoped = "\n".join(lines[:end])
    if not _markdown_identity(scoped, title, provider_id):
        return [], []
    matches = re.findall(r"^- \*\*Pricing:\*\* \$([0-9]+(?:\.[0-9]+)?)/1M input tokens, \$([0-9]+(?:\.[0-9]+)?)/1M output tokens\s*$", scoped, re.M)
    if len(matches) != 1:
        return [], []
    amounts = matches[0]
    set_id = parser + "_listed_model_rate"
    locator = f"{title} > Model ID: {provider_id} > Pricing: ${amounts[0]}/1M input tokens, ${amounts[1]}/1M output tokens"
    conditions = [llm_condition(set_id, "price_basis", "listed_model_rate", locator)]
    prices = [llm_price(provider_model_id=provider_id, upstream="", item=item, kind="published_rate", amount=amount, unit="1M tokens", set_id=set_id, locator=locator) for item, amount in zip(("input", "output"), amounts)]
    return prices, conditions


def _catalog_condition(set_id: str, key: str, value: str, locator: str) -> dict:
    return dict(zip(ADAPTER_CONDITION_FIELDS, (set_id, key, "eq", value, "", "stated", locator)))


def _catalog_price(model_id: str, item: str, amount: str, set_id: str, locator: str, *, upstream: str = "") -> dict:
    row = dict.fromkeys(ADAPTER_PRICE_FIELDS, "")
    row.update(provider_model_id=model_id, upstream_provider=upstream, task="text_generation", billing_item=item,
               price_kind="published_rate", amount=amount, currency="USD", unit="1M tokens",
               condition_set_id=set_id, locator=locator, evidence_status="direct")
    return row


def _catalog_vercel(body: str, title: str, provider_id: str, parser: str) -> tuple[list[dict], list[dict]]:
    lines = body.strip().splitlines()
    if not lines or lines[0] != "# " + title:
        return [], []
    # A page's initial model block is the evidence boundary.  A later section
    # may describe a related model and must not supply a target price.
    end = next((i for i, line in enumerate(lines[1:], 1) if re.match(r"^#{1,2}\s", line)), len(lines))
    block = "\n".join(lines[:end])
    if len(re.findall(r"^- \*\*Model ID:\*\* `" + re.escape(provider_id) + r"`\s*$", block, re.M)) != 1:
        return [], []
    prices = re.findall(r"^- \*\*Pricing:\*\* \$([0-9]+(?:\.[0-9]+)?)/1M input tokens, \$([0-9]+(?:\.[0-9]+)?)/1M output tokens\s*$", block, re.M)
    if len(prices) != 1:
        return [], []
    amounts = prices[0]
    set_id = f"{parser}_listed_model_rate"
    locator = f"{title} > Model ID: {provider_id} > Pricing: ${amounts[0]}/1M input tokens, ${amounts[1]}/1M output tokens"
    return [_catalog_price(provider_id, item, amount, set_id, locator) for item, amount in zip(("input", "output"), amounts)], [_catalog_condition(set_id, "price_basis", "listed_model_rate", locator)]


def _sun_price(spec: SourceSpec, label: str, amount: str, unit: str, locator: str,
           *, provider_model_id: str = "", condition_set_id: str = "",
           kind: str = "published_rate", status: str = "direct", unknown: str = "") -> dict[str, str]:
    return dict(provider_model_id=provider_model_id or SUNBURST_MODEL, model_version="", upstream_provider="OpenAI",
                task=spec.task, billing_item=label, price_kind=kind, amount=amount,
                currency="USD" if amount else "", unit=unit if amount else "",
                normalized_amount="", normalized_unit="", condition_set_id=condition_set_id,
                effective_from="", effective_until="", locator=locator,
                evidence_status=status, unknown_reason=unknown)


def _sun_vercel(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    decoded = body.replace('\\"', '"')
    match = re.search(r'"primaryModel":"' + SUNBURST_MODEL + r'"(.{0,5000}?)\}', decoded, re.S)
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
        prices.append(_sun_price(spec, label, _number(value.group(1)), "1M tokens",
                             f"primaryModel={SUNBURST_MODEL}; backingModels/{field}={value.group(1)}; visible: {visible.group(0)}"))
    if not prices:
        raise ValueError("Vercel scoped rates absent")
    return prices, []


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


def _flare_vercel(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    decoded = body.replace('\\"', '"')
    match = re.search(r'"primaryModel":"' + FLARE_MODEL + r'"(.{0,5000}?)\}', decoded, re.S)
    if not match or '"provider":"openai"' not in match.group(1) or '"type":"image"' not in match.group(1):
        raise ValueError("Vercel exact Flare image backing model absent")
    scoped = match.group(1)
    visible = re.search(r'Input \$([\d,.]+), Output \$([\d,.]+)(?:<!-- -->)?, Per 1M tokens', body)
    if not visible:
        raise ValueError("Vercel Flare visible price unit absent")
    fields = (("inputCost", "Input"), ("outputCost", "Output"),
              ("imageInputCost", "imageInputCost"), ("cachedInputCost", "cachedInputCost"))
    prices = []
    group = "flare_vercel_displayed_model_quote"
    for field, label in fields:
        value = re.search(r'"' + field + r'":"([\d,.]+)"', scoped)
        if not value:
            continue
        if field in {"inputCost", "outputCost"} and _number(value.group(1)) != _number(visible.group(1 if field == "inputCost" else 2)):
            raise ValueError("Vercel Flare visible and backing prices differ")
        prices.append(_flare_price(spec, label, _number(value.group(1)),
                             f"primaryModel={FLARE_MODEL}; backingModels/{field}={value.group(1)}; visible: {visible.group(0)}",
                             condition_set_id=group))
    if not prices:
        raise ValueError("Vercel Flare scoped rates absent")
    return prices, [_flare_condition(group, "price_basis", "displayed model quote",
                               f"Vercel model summary | {visible.group(0)}")]


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    decoded = body.decode("utf-8", errors="replace" if spec.modality == "llm" else "strict")
    if spec.modality == "video":
        return _video_metadata(spec, decoded)
    if spec.modality == "image":
        return {"vercel": _sun_vercel, "flare_vercel": _flare_vercel}[spec.parser](spec, decoded)
    if spec.parser == "vercel":
        reader = GeminiPricingScopeReader(); reader.feed(decoded); reader.close()
        return _broker(reader, spec.parser)
    if spec.parser.startswith("missing_"):
        from scripts.market_collection.models import BROKER_CATALOG_MODELS
        _, title, _, _, provider_id = next(row for row in BROKER_CATALOG_MODELS if row[0] == spec.model_id)
        return _catalog_vercel(decoded, title, provider_id, spec.parser)
    from scripts.market_collection.sources import _targets
    _, _, _, title, provider_id = _targets()[spec.model_id]
    return _vercel_markdown(decoded, title, provider_id, spec.parser)


def _video_metadata(spec, body):
    from .video_common import flight_payloads, objects_with_key, price, conditions
    from scripts.market_collection.models import VEO_MODELS, VEO_VERCEL_IDS
    provider = "seedance-2.5" if spec.model_id == "seedance-2.5" else VEO_VERCEL_IDS[VEO_MODELS.index(spec.model_id)]
    supplier = "bytedance" if spec.model_id == "seedance-2.5" else "vertex"
    roots = [obj for payload in flight_payloads(body) for obj in objects_with_key(payload, "backingModels")
             if "recommendedModels" in obj]
    if len(roots) != 1:
        raise ValueError("Vercel primary backing model container absent or duplicate")
    matches = [obj for obj in roots[0]["backingModels"] if obj.get("primaryModel") == provider
               and obj.get("provider") == supplier and obj.get("type") == "video"]
    if len(matches) != 1:
        raise ValueError("Vercel exact primary video backing model absent or duplicate")
    model = matches[0]
    capabilities = model.get("videoCapabilities", {})
    resolutions = capabilities.get("supportedResolutions", [])
    if "text-to-video" not in capabilities.get("supportedOperations", []) or not resolutions or len(resolutions) != len(set(resolutions)):
        raise ValueError("Vercel text-to-video capabilities missing or ambiguous")
    upstream = "ByteDance" if supplier == "bytedance" else "Google"
    prices, own_conditions, seen = [], [], set()
    def add(value, resolution, audio, unit, field):
        identity = (resolution, audio)
        if identity in seen:
            raise ValueError("Vercel duplicate video rate condition")
        seen.add(identity)
        group = f"{spec.source_key}_{resolution}_{audio}"
        locator = f"backingModels/primaryModel={provider}; provider={supplier}; {field}"
        prices.append(price(spec, provider, upstream, value, unit, group, locator, version="2.5" if supplier == "bytedance" else "3.1"))
        own_conditions.extend(conditions(group, locator, resolution=resolution, audio=audio, input_video="none", price_basis="Vercel backing metadata", provider=supplier))
        for key in ("supportedOperations", "supportedAspectRatios", "supportedDurationsSeconds", "supportedFps", "generateAudio"):
            if key in capabilities:
                own_conditions.extend(conditions(group, f"backingModels/{provider}/videoCapabilities/{key}", **{key: json.dumps(capabilities[key], separators=(",", ":"))}))
    dimensions = model.get("videoDimensionPricing")
    tokens = model.get("videoTokenPricing")
    if isinstance(dimensions, list) and dimensions:
        for i, row in enumerate(dimensions):
            if row.get("costUnit") != "sec" or row.get("size") not in resolutions or type(row.get("hasAudio")) is not bool:
                raise ValueError("Vercel video price unit/resolution/audio invalid")
            if row["hasAudio"] and capabilities.get("generateAudio") is False:
                raise ValueError("Vercel audio price conflicts with capabilities")
            if "cost" not in row:
                raise ValueError("Vercel video price missing")
            add(row["cost"], row["size"], "on" if row["hasAudio"] else "off", "1 second", f"videoDimensionPricing/{i}={json.dumps(row, separators=(',', ':'))}")
    elif isinstance(tokens, dict) and tokens:
        tiers = tokens.get("tiers", [])
        tier_resolutions = [row.get("resolution") for row in tiers]
        if len(tier_resolutions) != len(set(tier_resolutions)) or any(res not in resolutions for res in tier_resolutions):
            raise ValueError("Vercel token tier resolution invalid or duplicate")
        for resolution in resolutions:
            row = next((row for row in tiers if row["resolution"] == resolution), tokens)
            if "costPerMillionTokens" not in row:
                raise ValueError("Vercel video token price missing")
            add(row["costPerMillionTokens"], resolution, "supported; rate has no audio distinction", "1M tokens", f"videoTokenPricing; resolution={resolution}; costPerMillionTokens={row['costPerMillionTokens']}")
    else:
        group = spec.source_key + "_unquoted"
        locator = f"backingModels/primaryModel={provider}; video pricing absent"
        prices.append(price(spec, provider, upstream, None, "", group, locator, version="2.5" if supplier == "bytedance" else "3.1", reason="Exact video backing model supplies no video price metadata"))
        own_conditions.extend(conditions(group, locator, price_basis="Vercel backing metadata", input_video="none"))
    return prices, own_conditions
