"""Openrouter public price parsers."""

from __future__ import annotations

import html
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


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    decoded = body.decode("utf-8", errors="replace" if spec.modality == "llm" else "strict")
    if spec.modality == "image":
        return {"openrouter": _sun_openrouter, "flare_openrouter": _flare_openrouter}[spec.parser](spec, decoded)
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
