"""Image models on OpenRouter and Vercel AI Gateway, read from the gateways' endpoint APIs.

llm_endpoints reads the token fields of a listing (prompt, completion, cache) with the endpoint conditions the listing states.
The image models use it unchanged, their rows carrying the spec's task. What is added or different here:
- OpenRouter's pricing.image_output is read as USD per image-output token. Its schema text says "per output image", but every value
  equals the same endpoint's output_image rate typed unit=token in OpenRouter's images catalog (6e-05 for gemini-3.1-flash-image-preview),
  and Google's pricing page lists the same $60 per 1M image tokens for gemini-3.1-flash-image. A value of $1000 per 1M or more is
  refused, so a switch to the documented per-image dollars cannot pass as a token price. `image` is documented per input image yet
  equals the per-token prompt rate, so it is not read; neither are audio, web_search or internal_reasoning.
- Vercel's endpoint JSON documents only prompt, completion and the cache fields. Its image_output and image are "0" placeholders, so
  no image price is read there.
- OpenRouter lists gpt-image-2 only in its images catalog. Its endpoint records carry typed rate lines (billable, unit, cost_usd),
  of which only the per-token lines named below are read.
"""

from __future__ import annotations

import json
from decimal import Decimal

from scripts.market_collection.core import SourceSpec
from . import llm_endpoints as L

# USD per 1M tokens: the dearest image-output rate listed is 120, while a price per image (cents) read as per token would be in the tens of thousands.
IMAGE_TOKEN_CEILING = Decimal(1000)
CATALOG_ITEMS = {"input_text": "input", "input_image": "image_input", "output_image": "image_output"}


def _openrouter(spec: SourceSpec, body: bytes) -> tuple[list[dict], list[dict]]:
    prices, conditions = L.parse_openrouter(spec, body)
    model = L._expected_id(spec)
    by_set: dict[str, list[dict]] = {}
    for row in conditions:
        by_set.setdefault(row["condition_set_id"], []).append(row)
    for index, endpoint in enumerate(L._load(body, model)["endpoints"]):
        pricing = endpoint["pricing"]
        if any("image_output" in override for override in pricing.get("overrides", [])):
            raise ValueError("OpenRouter image_output override is not read")
        value = pricing.get("image_output")
        if value is None:
            continue
        locator = f"endpoints[{index}].pricing.image_output={value}"
        amount = L._per_million(value, locator)
        if Decimal(amount) >= IMAGE_TOKEN_CEILING:
            raise ValueError(f"OpenRouter image_output {value} is not a per-token price")
        # Same conditions as the endpoint's other rows: those of its prompt row, the first listed.
        base = L._set_id(spec, endpoint["tag"], "default")
        source = next((f"{base}_{field}" for field in L.OPENROUTER_ITEMS if f"{base}_{field}" in by_set), None)
        if source is None:
            raise ValueError("OpenRouter image_output of an endpoint with no token price to take its conditions from")
        set_id = f"{base}_image_output"
        conditions.extend(dict(row, condition_set_id=set_id) for row in by_set[source])
        prices.append(L._price(spec, model, endpoint["provider_name"], "image_output", amount, set_id, locator))
    return prices, conditions


def _catalog(spec: SourceSpec, body: bytes) -> tuple[list[dict], list[dict]]:
    model = L._expected_id(spec)
    data = json.loads(body.decode("utf-8"), parse_float=str, object_pairs_hook=L._unique_pairs)
    if not isinstance(data, dict) or data.get("id") != model or not isinstance(data.get("endpoints"), list):
        raise ValueError("image catalog listing is not for the registered model id")
    prices, conditions, tags = [], [], set()
    for index, endpoint in enumerate(data["endpoints"]):
        where = f"endpoints[{index}]"
        if not isinstance(endpoint, dict):
            raise ValueError("invalid image catalog endpoint record")
        provider, tag, rates = endpoint.get("provider_name"), endpoint.get("provider_tag"), endpoint.get("pricing")
        if not isinstance(provider, str) or not provider or not isinstance(tag, str) or not tag or not isinstance(rates, list):
            raise ValueError("invalid image catalog endpoint identity or pricing")
        if tag in tags:
            raise ValueError("duplicate image catalog endpoint tag")
        tags.add(tag)
        items = set()
        for number, rate in enumerate(rates):
            at = f"{where}.pricing[{number}]"
            # An extra key such as variant (a rate that depends on one) makes the key set differ: that line is not read.
            if not isinstance(rate, dict) or set(rate) != {"billable", "unit", "cost_usd"}:
                raise ValueError("unsupported image catalog rate line at " + at)
            if rate["billable"] not in CATALOG_ITEMS or rate["unit"] != "token":
                raise ValueError(f"unsupported image catalog rate {rate['billable']}/{rate['unit']} at {at}")
            item = CATALOG_ITEMS[rate["billable"]]
            if item in items:
                raise ValueError(f"duplicate image catalog rate {rate['billable']} at {at}")
            items.add(item)
            set_id = L._set_id(spec, tag, item)
            locator = f"{at}: {rate['billable']} per {rate['unit']}, cost_usd={rate['cost_usd']}"
            conditions.extend((L._condition(set_id, "price_basis", "endpoint_listed_rate", locator), L._condition(set_id, "endpoint_tag", tag, f"{where}.provider_tag")))
            prices.append(L._price(spec, model, provider, item, L._per_million(rate["cost_usd"], locator), set_id, locator))
    return prices, conditions


READERS = {"openrouter_image_endpoints": _openrouter, "vercel_image_endpoints": L.parse_vercel, "openrouter_image_catalog_endpoints": _catalog}
PARSERS = frozenset(READERS)


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    return READERS[spec.parser](spec, body)
