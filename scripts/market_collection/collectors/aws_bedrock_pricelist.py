"""Amazon Bedrock token rates from the AWS Price List API offer file of us-east-1 (public JSON, no login), for the Bedrock models
whose model card gives no price (AWS_PRICELIST_MODELS, 2026-10-30).

A model's input and output rates are the products whose usagetype is USE1-<stem>-input-tokens and USE1-<stem>-output-tokens: the
bedrock-runtime meters of the Standard tier. The file's other meters of the model are not read: the -batch, -flex and -priority
tiers (the existing Bedrock sources read Standard only) and the -mantle- meters of the bedrock-mantle endpoint. Each usagetype
must be exactly one product of the model in us-east-1, with one OnDemand term of one price dimension in USD per 1K tokens for all
volumes and a positive rate; anything else raises, and no neighbouring model's or tier's meter is borrowed.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from scripts.market_collection.core import SourceSpec
from scripts.market_collection.models import AWS_PRICELIST_MODELS
from .common import llm_condition, llm_price

PARSERS = frozenset({"aws_bedrock_pricelist_api"})
REGION, PREFIX = "us-east-1", "USE1-"
ITEMS = (("input", "input-tokens"), ("output", "output-tokens"))
UPSTREAM = {"Z AI": "Z.ai"}  # the file's provider attribute -> the maker's name on its other routes


def _product(products: dict, usagetype: str, name: str) -> tuple[str, dict]:
    found = [(sku, product) for sku, product in products.items()
             if isinstance(product, dict) and isinstance(product.get("attributes"), dict) and product["attributes"].get("usagetype") == usagetype]
    if len(found) != 1:
        raise ValueError(f"AWS price list has {len(found)} products with usagetype {usagetype}")
    sku, product = found[0]
    attributes = product["attributes"]
    if (product.get("sku"), attributes.get("model"), attributes.get("regionCode"), attributes.get("servicecode")) != (sku, name, REGION, "AmazonBedrock"):
        raise ValueError(f"AWS price list product {sku} is not {name} of Amazon Bedrock in {REGION}")
    if attributes.get("provider") not in UPSTREAM:
        raise ValueError(f"AWS price list product {sku} names an unknown model provider")
    return sku, attributes


def _rate(offers: object, sku: str) -> tuple[str, str, str, Decimal, str]:
    """(term key, rate code, quoted USD, rate, effectiveDate) of the one OnDemand term and price dimension of a product."""
    if not isinstance(offers, dict) or len(offers) != 1:
        raise ValueError(f"AWS price list product {sku} has no single OnDemand term")
    (term_key, term), = offers.items()
    dimensions = term.get("priceDimensions") if isinstance(term, dict) else None
    if not isinstance(dimensions, dict) or len(dimensions) != 1 or term.get("sku") != sku:
        raise ValueError(f"AWS price list term {term_key} has no single price dimension")
    (rate_code, dimension), = dimensions.items()
    dimension = dimension if isinstance(dimension, dict) else {}
    price = dimension.get("pricePerUnit")
    if (dimension.get("unit"), dimension.get("beginRange"), dimension.get("endRange")) != ("1K tokens", "0", "Inf") \
            or not isinstance(price, dict) or list(price) != ["USD"] or not isinstance(price["USD"], str):
        raise ValueError(f"AWS price list rate {rate_code} is not one USD price per 1K tokens for all volumes")
    try:
        rate = Decimal(price["USD"])
    except InvalidOperation:
        raise ValueError(f"AWS price list rate {rate_code} is not a number") from None
    if not rate.is_finite() or rate <= 0:
        raise ValueError(f"AWS price list rate {rate_code} is not a positive price")
    return term_key, rate_code, price["USD"], rate, str(term.get("effectiveDate") or "")


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    name, stem = {model: (name, stem) for model, name, stem in AWS_PRICELIST_MODELS}[spec.model_id]
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict) or data.get("offerCode") != "AmazonBedrock" or not isinstance(data.get("products"), dict) \
            or not isinstance(data.get("terms"), dict) or not isinstance(data["terms"].get("OnDemand"), dict):
        raise ValueError("not the AWS Price List offer file of Amazon Bedrock")
    prices: list[dict] = []
    conditions: list[dict] = []
    for item, meter in ITEMS:
        usagetype = f"{PREFIX}{stem}-{meter}"
        sku, attributes = _product(data["products"], usagetype, name)
        term_key, rate_code, quoted, rate, effective = _rate(data["terms"]["OnDemand"].get(sku), sku)
        set_id = f"{spec.source_key}_{item}"
        locator = (f"products/{sku}: usagetype={usagetype}; model={name}; regionCode={REGION}; inferenceType={attributes.get('inferenceType', '')}; "
                   f"terms/OnDemand/{term_key}/priceDimensions/{rate_code}: pricePerUnit USD {quoted} per 1K tokens")
        # Standard is Bedrock's default tier, the meter without a tier suffix (on 10/30 the bedrock-mantle meter of the same rate,
        # ...-mantle-input-tokens-standard, states service_tier standard).
        for key, value in (("service_tier", "Standard"), ("region", REGION), ("price_basis", "AWS Price List API; OnDemand")):
            conditions.append(llm_condition(set_id, key, value, locator))
        prices.append(llm_price(provider_model_id=spec.model_id, upstream=UPSTREAM[attributes["provider"]], item=item, kind="published_rate",
                                amount=format((rate * 1000).normalize(), "f"), unit="1M tokens", set_id=set_id, locator=locator,
                                effective_from=effective))
    return prices, conditions
