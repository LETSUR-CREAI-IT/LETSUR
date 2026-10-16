"""Per-upstream LLM price rows from the public endpoint listings of OpenRouter and Vercel AI Gateway.

The model-page headline price of a gateway does not say which upstream serves it. These listings are the
gateways' own per-endpoint price sources. Only fields whose meaning the gateway's public API documentation
defines become stated conditions; documented-but-unmeasurable or undocumented price structures stay unknown
so the row is never treated as comparable on a guess.
"""

from __future__ import annotations

import json
from decimal import Decimal

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec

# Token-priced fields that map to an existing billing item. Per-call, per-image, audio and reasoning fields are
# not priced here: their unit or scope is not established by the documentation for a text-generation row.
OPENROUTER_ITEMS = {"prompt": "input", "completion": "output", "input_cache_read": "cache_read",
                    "input_cache_write": "cache_write", "input_cache_write_1h": "cache_write"}
VERCEL_ITEMS = {"prompt": "input", "completion": "output", "input_cache_read": "cache_read", "input_cache_write": "cache_write"}
OPENROUTER_DOC = "https://openrouter.ai/docs/api/api-reference/endpoints/list-endpoints"
VERCEL_DOC = "https://vercel.com/docs/ai-gateway/sdks-and-apis/rest-api#get-model-endpoints"
# Enumerated fixed serving precisions (OpenRouter Quantization schema minus its "unknown" member). Anything else,
# including null, "unknown", auto/default-like labels, is not a stated serving configuration.
KNOWN_QUANTIZATIONS = frozenset(("int4", "int8", "fp4", "mxfp4", "nvfp4", "fp6", "fp8", "mxfp8", "fp16", "bf16", "fp32"))
# Vercel documents service tiers only for these upstream families (matched by name prefix, e.g. vertexAnthropic); for other upstreams the setting has no effect.
VERCEL_TIER_UPSTREAMS = ("openai", "azure", "google", "vertex", "spacexai", "xai")
VERCEL_TIER_DOC = "https://vercel.com/docs/ai-gateway/models-and-providers/service-tiers"
OPENROUTER_STATUSES = frozenset((0, -1, -2, -3, -5, -10))
VERCEL_UNDOCUMENTED = (("inference_regions", "region"), ("service_tiers", "service_tier"), ("service_tier", "service_tier"), ("peak_pricing", "peak_pricing"))


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate endpoint JSON field: " + key)
        value[key] = item
    return value


def _load(body: bytes, expected_id: str) -> dict:
    data = json.loads(body.decode("utf-8"), parse_float=str, object_pairs_hook=_unique_pairs)
    data = data.get("data") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("id") != expected_id:
        raise ValueError("endpoint listing is not for the registered model id")
    if not isinstance(data.get("endpoints"), list):
        raise ValueError("endpoint list absent")
    return data


def _expected_id(spec: SourceSpec) -> str:
    head, _, tail = spec.url.partition("/models/")
    model, sep, _ = tail.rpartition("/endpoints")
    if not head or not sep or not model:
        raise ValueError("endpoint source URL does not name a model")
    return model


def _per_million(value: object, where: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("invalid endpoint token price at " + where)
    try:
        amount = Decimal(str(value)) * 1000000
    except ArithmeticError:
        raise ValueError("invalid endpoint token price at " + where) from None
    if not amount.is_finite() or amount < 0:
        raise ValueError("invalid endpoint token price at " + where)
    return format(amount.normalize(), "f")


def _condition(set_id: str, key: str, value: str, locator: str, *, operator: str = "eq", status: str = "stated") -> dict:
    return dict(zip(ADAPTER_CONDITION_FIELDS, (set_id, key, operator, value, "", status, locator)))


def _price(spec: SourceSpec, model: str, upstream: str, item: str, amount: str, set_id: str, locator: str) -> dict:
    row = dict.fromkeys(ADAPTER_PRICE_FIELDS, "")
    row.update(provider_model_id=model, upstream_provider=upstream, task=spec.task, billing_item=item, price_kind="published_rate",
               amount=amount, currency="USD", unit="1M tokens", condition_set_id=set_id, locator=locator, evidence_status="direct")
    return row


def _quantization_condition(set_id: str, value: object, locator: str) -> dict:
    if isinstance(value, str) and value in KNOWN_QUANTIZATIONS:
        return _condition(set_id, "quantization", value, locator)
    reason = "not reported (null)" if value is None else f"{value!r} is not a stated fixed quantization level"
    return _condition(set_id, "quantization", reason, locator, status="unknown")


def _status_condition(set_id: str, endpoint: dict, where: str, allowed: frozenset | None, doc: str) -> dict:
    """0 is the advertised active state (not proof the account can call it); any other number is kept raw as unknown."""
    if "status" not in endpoint:
        return _condition(set_id, "endpoint_status", "not reported (field absent)", f"{where}.status", status="unknown")
    value = endpoint["status"]
    if type(value) is not int or (allowed is not None and value not in allowed):
        raise ValueError("endpoint status outside the documented values")
    locator = f"{where}.status={value} ({doc})"
    if value == 0:
        return _condition(set_id, "endpoint_status", "0", locator + "; 0 is the documented active status, not proof of callability for any account")
    return _condition(set_id, "endpoint_status", str(value), locator + "; non-zero is not the active status", status="unknown")


def _discount_conditions(set_id: str, discount: object, where: str, doc: str) -> list[dict]:
    """A zero discount is a stated zero. Non-zero or absent: whether the listed price is before or after it is undocumented."""
    locator = f"{where}.pricing.discount"
    if discount is None:
        return [_condition(set_id, "discount_application", "not reported (field absent)", locator, status="unknown")]
    if isinstance(discount, bool) or not isinstance(discount, (str, int)):
        raise ValueError("invalid endpoint discount")
    try:
        amount = Decimal(str(discount))
    except ArithmeticError:
        raise ValueError("invalid endpoint discount") from None
    if not amount.is_finite() or amount < 0 or amount > 1:
        raise ValueError("invalid endpoint discount")
    if amount == 0:
        return [_condition(set_id, "endpoint_discount", "0", f"{locator}={discount}; zero discount means the listed price is unaffected")]
    return [_condition(set_id, "endpoint_discount", str(discount), f"{locator}={discount}"),
            _condition(set_id, "discount_application", f"discount {discount} is non-zero: the documentation does not say whether the listed prompt/completion is before or after it ({doc})",
                       locator, status="unknown")]


def _slug(endpoint: dict) -> str:
    # "Relace | z-ai/glm-5.2-20260616": the part after the bar is the model slug this endpoint serves.
    name = endpoint.get("name")
    return name.partition("|")[2].strip() if isinstance(name, str) and "|" in name else ""


def _set_id(spec: SourceSpec, identity: str, kind: str) -> str:
    import hashlib
    return f"{spec.source_key}_{hashlib.sha256(identity.encode()).hexdigest()[:12]}_{kind}"


def parse_openrouter(spec: SourceSpec, body: bytes) -> tuple[list[dict], list[dict]]:
    model = _expected_id(spec)
    data = _load(body, model)
    prices, conditions, identities = [], [], set()
    for index, endpoint in enumerate(data["endpoints"]):
        where = f"endpoints[{index}]"
        if not isinstance(endpoint, dict):
            raise ValueError("invalid endpoint record")
        provider, tag, pricing = endpoint.get("provider_name"), endpoint.get("tag"), endpoint.get("pricing")
        if not isinstance(provider, str) or not provider or not isinstance(tag, str) or not tag or not isinstance(pricing, dict):
            raise ValueError("invalid endpoint identity or pricing")
        if tag in identities:
            raise ValueError("duplicate endpoint tag")
        identities.add(tag)
        tier, quantization = endpoint.get("service_tier"), endpoint.get("quantization")
        if tier not in (None, "flex", "priority", "ultrafast") or not isinstance(quantization, (str, type(None))):
            raise ValueError("unsupported endpoint service tier or quantization")
        context, discount = endpoint.get("context_length"), pricing.get("discount")
        if type(context) is not int:
            raise ValueError("endpoint context length absent")

        def common(set_id: str, basis: str, locator: str) -> None:
            conditions.extend((
                _condition(set_id, "price_basis", basis, locator),
                _condition(set_id, "endpoint_tag", tag, f"{where}.tag"),
                # PublicServiceTier is documented as null for a standard endpoint.
                _condition(set_id, "service_tier", tier or "standard", f"{where}.service_tier={json.dumps(tier)}; null is a standard endpoint ({OPENROUTER_DOC})"),
                _condition(set_id, "context_length", str(context), f"{where}.context_length"),
                _quantization_condition(set_id, quantization, f"{where}.quantization={json.dumps(quantization)}"),
                _status_condition(set_id, endpoint, where, OPENROUTER_STATUSES, OPENROUTER_DOC),
                *_discount_conditions(set_id, discount, where, OPENROUTER_DOC),
            ))
            slug = _slug(endpoint)
            if slug:
                conditions.append(_condition(set_id, "endpoint_model_slug", slug, f"{where}.name"))

        def emit(field: str, value: object, set_id: str, locator: str) -> None:
            item = OPENROUTER_ITEMS[field]
            if field == "input_cache_write_1h":
                conditions.append(_condition(set_id, "cache_write_ttl", "1h", f"{locator}; field is documented as the 1-hour cache-write price"))
            elif field == "input_cache_write":
                conditions.append(_condition(set_id, "cache_write_ttl", "not confirmed: TTL is provider dependent; OpenRouter documents input_cache_write as the default 5-minute rate only for providers with multiple TTLs (e.g. Anthropic), so the TTL of this endpoint is not established", locator, status="unknown"))
            prices.append(_price(spec, model, provider, item, _per_million(value, locator), set_id, locator))

        base = _set_id(spec, tag, "default")
        base_fields = [field for field in OPENROUTER_ITEMS if pricing.get(field) is not None]
        if base_fields:
            for field in base_fields:
                set_id = f"{base}_{field}"
                locator = f"{where}.pricing.{field}={pricing[field]}"
                common(set_id, "endpoint_default_conditions", f"{where}.pricing (top-level keys are the price under default conditions per {OPENROUTER_DOC})")
                emit(field, pricing[field], set_id, locator)
        overrides = pricing.get("overrides", [])
        if not isinstance(overrides, list):
            raise ValueError("invalid pricing overrides")
        for number, override in enumerate(overrides):
            if not isinstance(override, dict):
                raise ValueError("invalid pricing override")
            fields = [field for field in OPENROUTER_ITEMS if override.get(field) is not None]
            for field in fields:
                set_id = _set_id(spec, tag, f"override{number}_{field}")
                pointer = f"{where}.pricing.overrides[{number}]"
                locator = f"{pointer}.{field}={override[field]}"
                common(set_id, "endpoint_override", pointer)
                if "min_prompt_tokens" in override:
                    conditions.append(_condition(set_id, "prompt_tokens", str(override["min_prompt_tokens"]), f"{pointer}.min_prompt_tokens (strictly greater than)", operator="gt"))
                for key in ("utc_start", "utc_end"):
                    if key in override:
                        conditions.append(_condition(set_id, key, str(override[key]), f"{pointer}.{key} (HHMM, window [utc_start, utc_end) per {OPENROUTER_DOC})"))
                if "utc_days" in override:
                    conditions.append(_condition(set_id, "utc_days", ",".join(override["utc_days"]), f"{pointer}.utc_days"))
                emit(field, override[field], set_id, locator)
    return prices, conditions


def parse_vercel(spec: SourceSpec, body: bytes) -> tuple[list[dict], list[dict]]:
    model = _expected_id(spec)
    data = _load(body, model)
    prices, conditions, identities = [], [], set()
    for index, endpoint in enumerate(data["endpoints"]):
        where = f"endpoints[{index}]"
        if not isinstance(endpoint, dict):
            raise ValueError("invalid endpoint record")
        provider, pricing, context = endpoint.get("provider_name"), endpoint.get("pricing"), endpoint.get("context_length")
        if not isinstance(provider, str) or not provider or not isinstance(pricing, dict) or type(context) is not int:
            raise ValueError("invalid endpoint identity or pricing")
        if provider in identities:
            raise ValueError("duplicate endpoint provider")
        identities.add(provider)
        quantization = endpoint.get("quantization")

        def common(set_id: str, field: str, locator: str) -> None:
            conditions.extend((
                _condition(set_id, "price_basis", "endpoint_listed_rate", f"{where}.pricing ({VERCEL_DOC})"),
                _condition(set_id, "endpoint_provider", provider, f"{where}.provider_name"),
                _condition(set_id, "context_length", str(context), f"{where}.context_length"),
            ))
            conditions.append(_quantization_condition(set_id, quantization, f"{where}.quantization={json.dumps(quantization)}"))
            if provider.lower().startswith(VERCEL_TIER_UPSTREAMS):
                conditions.append(_condition(set_id, "service_tier", "not stated: tier-capable upstream; the endpoint API does not say which tier the listed rate is", f"{where}.provider_name ({VERCEL_TIER_DOC})", status="unknown"))
            else:
                conditions.append(_condition(set_id, "service_tier", "standard", f"{where}.provider_name; service tiers are documented only for OpenAI, Google and SpaceXAI models ({VERCEL_TIER_DOC})"))
            if field == "input_cache_write":
                conditions.append(_condition(set_id, "cache_write_ttl", "not stated: documentation names no TTL for input_cache_write", locator, status="unknown"))
            conditions.append(_status_condition(set_id, endpoint, where, None, VERCEL_DOC))
            discount = pricing.get("discount")
            if discount not in (None, 0, "0"):
                conditions.append(_condition(set_id, "discount_application", f"discount {discount}: the field is not defined in the Vercel documentation", f"{where}.pricing.discount", status="unknown"))
            for key, name in VERCEL_UNDOCUMENTED:
                if key in endpoint or key in pricing:
                    conditions.append(_condition(set_id, name + "_applicability", f"{key} is present in the endpoint record but not defined in the Vercel documentation; which price it scopes is unconfirmed",
                                                 f"{where}.{'pricing.' if key in pricing else ''}{key}", status="unknown"))

        for field, item in VERCEL_ITEMS.items():
            tiers = pricing.get(field + "_tiers")
            value = pricing.get(field)
            if tiers is not None:
                if not isinstance(tiers, list) or not tiers:
                    raise ValueError("invalid pricing tiers")
                for number, tier in enumerate(tiers):
                    if not isinstance(tier, dict) or "cost" not in tier:
                        raise ValueError("invalid pricing tier")
                    set_id = _set_id(spec, provider, f"{field}_tier{number}")
                    pointer = f"{where}.pricing.{field}_tiers[{number}]"
                    common(set_id, field, pointer)
                    conditions.append(_condition(set_id, "context_tier_min_tokens", str(tier.get("min", 0)), pointer + " (inclusive; documented tier minimum)"))
                    if "max" in tier:
                        conditions.append(_condition(set_id, "context_tier_max_tokens", str(tier["max"]), pointer + " (exclusive; documented tier maximum)"))
                    prices.append(_price(spec, model, provider, item, _per_million(tier["cost"], pointer), set_id, f"{pointer}: {json.dumps(tier, sort_keys=True)}"))
                first = tiers[0].get("cost")
                if value is not None and Decimal(str(value)) != Decimal(str(first)):
                    set_id = _set_id(spec, provider, f"{field}_base_conflict")
                    common(set_id, field, f"{where}.pricing.{field}")
                    conditions.append(_condition(set_id, "tier_vs_base", f"base {value} differs from first tier {first}", f"{where}.pricing.{field}", status="conflicted"))
                    prices.append(_price(spec, model, provider, item, _per_million(value, field), set_id, f"{where}.pricing.{field}={value}"))
            elif value is not None:
                set_id = _set_id(spec, provider, field)
                locator = f"{where}.pricing.{field}={value}"
                common(set_id, field, locator)
                prices.append(_price(spec, model, provider, item, _per_million(value, locator), set_id, locator))
    return prices, conditions
