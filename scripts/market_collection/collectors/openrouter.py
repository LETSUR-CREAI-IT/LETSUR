"""Openrouter public price parsers."""

from __future__ import annotations

import html
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec
from scripts.market_collection.models import GOOGLE_BROKER_MODEL, SUNBURST_MODEL, FLARE_MODEL
from .common import GeminiPricingScopeReader, _clean, llm_condition, llm_price, _number


def _broker(reader: GeminiPricingScopeReader, parser: str) -> tuple[list[dict], list[dict]]:
    page = " ".join(reader.scoped_text)
    if not page:
        return [], []
    pattern = re.compile(r"In\s*/\s*Out Price\s+(?:(?P<promo>50% off)\s+)?\$(?P<input>[0-9]+(?:\.[0-9]+)?)\s*/\s*\$(?P<output>[0-9]+(?:\.[0-9]+)?)\s+per\s+1M\b", re.I)
    match = pattern.search(page)
    if not match:
        return [], []
    set_id = f"{parser}_model_page_rate"
    locator = f"Gemini 3.8 Flash > {match.group(0)}"
    conditions = [llm_condition(set_id, "price_basis", "model_page_rate", locator)]
    if match.group("promo"):
        conditions.append(llm_condition(set_id, "promotion_label", match.group("promo"), locator))
    prices = [llm_price(provider_model_id=GOOGLE_BROKER_MODEL, upstream="", item=item, kind="published_rate",
                        amount=amount, unit="1M tokens", set_id=set_id, locator=locator)
              for item, amount in zip(("input", "output"), (match.group("input"), match.group("output")))]
    return prices, conditions


def _openrouter_target(body: str, title: str, provider_id: str, parser: str) -> tuple[list[dict], list[dict]]:
    # Require an exact target h1 before considering the overview text. An h2
    # or h3 ends scope, except OpenRouter's own model slug subheading.
    class Scope(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.heading = ""
            self.parts: list[str] = []
            self.active = False
            self.text: list[str] = []
            self.suppressed = 0
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript"):
                self.suppressed += 1
            elif not self.suppressed and tag in ("h1", "h2", "h3"):
                self.heading, self.parts = tag, []
        def handle_data(self, data):
            if not self.suppressed:
                if self.heading:
                    self.parts.append(data)
                elif self.active and data.strip():
                    self.text.append(_clean(data))
        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript") and self.suppressed:
                self.suppressed -= 1
            elif tag == self.heading:
                heading = _clean("".join(self.parts))
                self.active = heading == title if tag == "h1" else self.active and heading.replace(" ", "") == provider_id
                self.heading = ""
    scoped = Scope()
    scoped.feed(body)
    matches = list(re.finditer(r"In\s*/\s*Out Price\s+(?:(50% off)\s+)?\$([0-9]+(?:\.[0-9]+)?)\s*/\s*\$([0-9]+(?:\.[0-9]+)?)\s+per\s+1M\b", " ".join(scoped.text), re.I))
    if len(matches) != 1:
        return [], []
    promo, *amounts = matches[0].groups()
    set_id = parser + "_model_page_rate"
    locator = f"{title} > {matches[0].group(0)}"
    conditions = [llm_condition(set_id, "price_basis", "model_page_rate", locator)]
    if promo:
        conditions.append(llm_condition(set_id, "promotion_label", promo, locator))
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


class _OpenRouterScope(HTMLParser):
    def __init__(self, title: str, provider_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.title = title
        self.provider_id = provider_id
        self.heading = ""
        self.parts: list[str] = []
        self.active = False
        self.text: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self.suppressed += 1
        elif not self.suppressed and tag in ("h1", "h2", "h3"):
            self.heading, self.parts = tag, []

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            if self.heading:
                self.parts.append(data)
            elif self.active and data.strip():
                self.text.append(re.sub(r"\s+", " ", data).strip())

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self.suppressed:
            self.suppressed -= 1
        elif tag == self.heading:
            heading = re.sub(r"\s+", " ", "".join(self.parts)).strip()
            if tag == "h1":
                self.active = heading == self.title
            else:
                self.active = self.active and heading.replace(" ", "") == self.provider_id
            self.heading = ""


def _catalog_openrouter(body: str, title: str, provider_id: str, model: str, parser: str) -> tuple[list[dict], list[dict]]:
    scope = _OpenRouterScope(title, provider_id)
    scope.feed(body)
    matches = list(re.finditer(r"In\s*/\s*Out Price\s+(?:(50% off)\s+)?\$([0-9]+(?:\.[0-9]+)?)\s*/\s*\$([0-9]+(?:\.[0-9]+)?)\s+per\s+1M\b", " ".join(scope.text), re.I))
    if len(matches) != 1:
        return [], []
    match = matches[0]
    set_id = f"{parser}_model_page_rate"
    locator = f"{title} > {match.group(0)}"
    conditions = [_catalog_condition(set_id, "price_basis", "model_page_rate", locator)]
    if match.group(1):
        conditions.append(_catalog_condition(set_id, "promotion_label", match.group(1), locator))
    return [_catalog_price(provider_id, item, amount, set_id, locator) for item, amount in zip(("input", "output"), match.groups()[1:])], conditions


def _sun_price(spec: SourceSpec, label: str, amount: str, unit: str, locator: str,
           *, provider_model_id: str = "", condition_set_id: str = "",
           kind: str = "published_rate", status: str = "direct", unknown: str = "") -> dict[str, str]:
    return dict(provider_model_id=provider_model_id or SUNBURST_MODEL, model_version="", upstream_provider="OpenAI",
                task=spec.task, billing_item=label, price_kind=kind, amount=amount,
                currency="USD" if amount else "", unit=unit if amount else "",
                normalized_amount="", normalized_unit="", condition_set_id=condition_set_id,
                effective_from="", effective_until="", locator=locator,
                evidence_status=status, unknown_reason=unknown)


def _sun_openrouter(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    decoded = body.replace('\\"', '"')
    match = re.search(r'"provider_model_id":"' + SUNBURST_MODEL + r'"(.{0,12000}?)"pricing_version_id":"([^"]+)"', decoded, re.S)
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
        prices.append(_sun_price(spec, label, _number(str(Decimal(amount) * 1000000)), "1M tokens",
                             f"provider_model_id={SUNBURST_MODEL}; pricing_version_id={version}; display_pricing/{label}: {json.dumps(entry, sort_keys=True)}"))
    if not prices:
        raise ValueError("OpenRouter scoped rates absent")
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


def _flare_openrouter(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    decoded = body.replace('\\"', '"')
    match = re.search(r'"provider_model_id":"' + FLARE_MODEL + r'"(.{0,12000}?)"pricing_version_id":"([^"]+)"', decoded, re.S)
    if not match:
        raise ValueError("OpenRouter exact Flare provider model and pricing version absent")
    scoped, version = match.groups()
    displays = list(re.finditer(r'"display_pricing":(\[.*?\])', scoped, re.S))
    if not displays or '"pricing_json":' not in scoped:
        raise ValueError("OpenRouter Flare model-scoped display prices absent")
    entries = json.loads(displays[-1].group(1))
    prices: list[dict] = []
    for entry in entries:
        label = entry.get("sku_label", "")
        if entry.get("kind") != "token" or entry.get("displayMultiplier") != 1000000 or entry.get("unitLabel") != "/M tokens":
            raise ValueError("OpenRouter Flare unsupported or missing unit")
        if label not in {"Input Price", "Output Price", "Cache Read", "Text Input", "Image Output"}:
            continue
        amount = entry.get("price")
        if not isinstance(amount, str):
            raise ValueError("OpenRouter Flare price absent")
        prices.append(_flare_price(spec, label, _number(str(Decimal(amount) * 1000000)),
                             f"provider_model_id={FLARE_MODEL}; pricing_version_id={version}; display_pricing/{label}: {json.dumps(entry, sort_keys=True)}"))
    if not prices:
        raise ValueError("OpenRouter Flare scoped rates absent")
    return prices, []



def _endpoint_literal(value):
    # Preserve JSON number precision in the literal evidence without float conversion.
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return '[' + ','.join(_endpoint_literal(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + ':' + _endpoint_literal(v) for k, v in value.items()) + '}'
    return json.dumps(value, ensure_ascii=False)


def _image25_endpoints(spec, body):
    from .image25_api import condition, ESSENTIAL, _enum, _range
    def unique_fields(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate endpoint JSON field: ' + key)
            value[key] = item
        return value
    data = json.loads(body, parse_float=Decimal, parse_constant=Decimal, object_pairs_hook=unique_fields)
    if not isinstance(data, dict) or data.get('id') != 'openai/' + spec.model_id:
        raise ValueError('OpenRouter exact endpoint root model absent')
    endpoints = data.get('endpoints')
    if not isinstance(endpoints, list):
        raise ValueError('OpenRouter endpoint list absent')
    prices, rows, identities = [], [], set()
    if not endpoints:
        rows.append(condition(spec.source_key, 'endpoint_availability', 'unavailable', 'endpoints=[]'))
    for index, endpoint in enumerate(endpoints):
        loc = f'endpoints[{index}]'
        if not isinstance(endpoint, dict) or any(not isinstance(endpoint.get(k), str) or not endpoint[k].strip()
                                                for k in ('provider_name', 'provider_slug')):
            raise ValueError('invalid endpoint provider identity')
        if 'provider_tag' not in endpoint or (endpoint['provider_tag'] is not None and
                (not isinstance(endpoint['provider_tag'], str) or not endpoint['provider_tag'].strip())):
            raise ValueError('invalid endpoint provider tag')
        identity = (endpoint['provider_slug'], endpoint['provider_tag'])
        if identity in identities:
            raise ValueError('duplicate endpoint provider identity')
        identities.add(identity)
        identity_key = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
        profile = f'{spec.source_key}_endpoint_{identity_key}'
        def add(key, value, pointer, status='stated'):
            rows.append(condition(profile, key, value, pointer, status))
        add('profile_scope', 'channel_endpoint', loc)
        add('model_id', spec.model_id, 'id=' + data['id'])
        for key in ('provider_name', 'provider_slug', 'provider_tag'):
            add(key, endpoint[key], loc + '.' + key, 'unknown' if endpoint[key] is None else 'stated')
        if endpoint['provider_tag'] is None:
            add('provider_pin', 'Provider tag null; endpoint cannot be pinned by tag', loc + '.provider_tag', 'unknown')
        parameters = endpoint.get('supported_parameters')
        if not isinstance(parameters, dict):
            raise ValueError('invalid endpoint supported parameters')
        for key, descriptor in parameters.items():
            if not isinstance(key, str) or not key or not isinstance(descriptor, dict) or descriptor.get('type') not in {'enum', 'range', 'boolean'}:
                raise ValueError('invalid endpoint capability descriptor')
            if descriptor['type'] == 'enum':
                _enum(descriptor.get('values'))
            elif descriptor['type'] == 'range':
                _range(descriptor)
            add(key + '_capability', descriptor, loc + '.supported_parameters.' + key)
            if key == 'quality' and descriptor['type'] == 'enum':
                add('quality_options', descriptor['values'], loc + '.supported_parameters.quality.values')
            if key == 'n' and descriptor['type'] == 'range':
                add('num_images_range', _range(descriptor), loc + '.supported_parameters.n')
        add('supported_parameters', parameters, loc + '.supported_parameters')
        passthrough = endpoint.get('allowed_passthrough_parameters')
        if not isinstance(passthrough, list) or any(not isinstance(k, str) or not k for k in passthrough) or len(set(passthrough)) != len(passthrough):
            raise ValueError('invalid endpoint passthrough allowlist')
        if type(endpoint.get('supports_streaming')) is not bool:
            raise ValueError('invalid endpoint streaming capability')
        for key in ('allowed_passthrough_parameters', 'supports_streaming'):
            add(key, endpoint[key], loc + '.' + key)
        add('api_surface', ['images'], 'source URL=' + spec.url + ' (dedicated Image API endpoint records)')
        add('collection_task_context', spec.task, 'registered SourceSpec.task; not endpoint task support')
        present = {r['condition_key'] for r in rows if r['condition_set_id'] == profile}
        for key in ESSENTIAL:
            if key not in present:
                add(key, 'Not documented in this endpoint record; requires verification', loc + ' > absent ' + key, 'unknown')
        rates = endpoint.get('pricing')
        if not isinstance(rates, list):
            raise ValueError('invalid endpoint pricing list')
        rate_keys = set()
        for rate_index, rate in enumerate(rates):
            pointer = f'{loc}.pricing[{rate_index}]'
            if not isinstance(rate, dict) or any(not isinstance(rate.get(k), str) or not rate[k] for k in ('billable', 'unit')):
                raise ValueError('invalid typed rate identity')
            variant = rate.get('variant')
            if variant is not None and (not isinstance(variant, str) or not variant):
                raise ValueError('invalid typed rate variant')
            identity = (rate['billable'], rate['unit'], variant)
            if identity in rate_keys:
                raise ValueError('duplicate endpoint typed rate')
            rate_keys.add(identity)
            cost = rate.get('cost_usd')
            if type(cost) not in (int, Decimal) or not Decimal(cost).is_finite() or cost < 0:
                raise ValueError('invalid endpoint cost_usd')
            literal = _endpoint_literal(rate)
            group = f'{profile}_rate_{rate_index}'
            def rate_add(key, value, status='stated'):
                rows.append(condition(group, key, value, pointer + '.' + key + ': ' + literal, status))
            rate_add('endpoint_profile_id', profile)
            rate_add('billable', rate['billable'])
            rate_add('unit', rate['unit'])
            rate_add('pricing_line', literal)
            if 'variant' in rate:
                rate_add('variant', variant)
                rate_add('variant_applicability', 'Concrete variant selection mapping not documented', 'unknown')
            labels = {'input_text': 'Text Input', 'input_image': 'Image Input', 'output_image': 'Image Output'}
            unknown = []
            if rate['billable'] not in labels:
                unknown.append('unknown billable: ' + rate['billable'])
            if rate['unit'] not in {'token', 'image', 'request'}:
                unknown.append('unknown unit: ' + rate['unit'])
            amount = format(Decimal(cost), 'f') if not unknown else ''
            row = dict.fromkeys(ADAPTER_PRICE_FIELDS, '')
            row.update(provider_model_id=data['id'], upstream_provider=endpoint['provider_name'], task=spec.task,
                       billing_item=labels.get(rate['billable'], rate['billable']), price_kind='published_rate',
                       amount=amount, currency='USD' if amount else '', unit='1 ' + rate['unit'] if amount else '',
                       condition_set_id=group, locator=pointer + ': ' + literal, evidence_status='direct',
                       unknown_reason='; '.join(unknown))
            prices.append(row)
    return prices, rows

def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    decoded = body.decode("utf-8", errors="replace" if spec.modality == "llm" else "strict")
    if spec.modality == "image":
        return {"openrouter": _sun_openrouter, "flare_openrouter": _flare_openrouter, "image25_openrouter_endpoints": _image25_endpoints}[spec.parser](spec, decoded)
    if spec.parser == "openrouter":
        reader = GeminiPricingScopeReader(); reader.feed(decoded); reader.close()
        return _broker(reader, spec.parser)
    if spec.parser.startswith("missing_"):
        from scripts.market_collection.models import BROKER_CATALOG_MODELS
        model, _, title, provider_id, _ = next(row for row in BROKER_CATALOG_MODELS if row[0] == spec.model_id)
        return _catalog_openrouter(decoded, title, provider_id, model, spec.parser)
    from scripts.market_collection.sources import _targets
    title, provider, _, _, provider_id = _targets()[spec.model_id]
    return _openrouter_target(decoded, ("OpenAI" if provider == "openai" else "Anthropic") + ": " + title, provider_id, spec.parser)
