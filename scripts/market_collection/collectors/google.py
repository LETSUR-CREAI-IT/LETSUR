"""Google public price parsers."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec
from scripts.market_collection.models import GOOGLE_ARGON_MODEL, GOOGLE_LLM_MODEL
from .common import GeminiPricingScopeReader, _clean, llm_condition, llm_price


MONEY = r"\$([0-9]+(?:\.[0-9]+)?)"


PERIOD = re.compile(rf"{MONEY}\s+(through|starting)\s+([A-Z][a-z]+\s+\d{{1,2}},\s+\d{{4}})\.?", re.I)


STORAGE = re.compile(rf"{MONEY}\s*/\s*1,000,000\s+tokens\s+per\s+hour\s*\(storage price\)\s+(through|starting)\s+([A-Z][a-z]+\s+\d{{1,2}},\s+\d{{4}})\.?", re.I)


# Gemini 4 Argon's cache price cell ends with this sentence; it is a stated condition, not a price.
IMPLICIT_ONLY = re.compile(r"Implicit caching only \(explicit caching is not supported\)\.?", re.I)


# The pricing page has one h2 section per model, selected by its heading text.
TITLES = {GOOGLE_LLM_MODEL: "Gemini 3.8 Flash", GOOGLE_ARGON_MODEL: "Gemini 4 Argon"}


class _GooglePricingReader(GeminiPricingScopeReader):
    """The shared reader scopes its tables to Gemini 3.8 Flash; this one scopes them to the section titled `title`."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title.lower()

    def handle_endtag(self, tag: str) -> None:
        closing_h2 = tag == "h2" and self.heading == "h2" and not self.suppressed
        heading = _clean("".join(self.heading_parts))
        super().handle_endtag(tag)
        if closing_h2:
            self.model = heading.lower() == self._title


def _google(reader: GeminiPricingScopeReader, model_id: str, title: str) -> tuple[list[dict], list[dict]]:
    prices: list[dict] = []
    conditions: list[dict] = []
    labels = {"Input price": "input", "Output price (including thinking tokens)": "output", "Context caching price": "cache_read"}
    for tier, rows in reader.tables:
        if tier not in {"standard", "batch", "flex", "priority"} or not rows:
            continue
        header = rows[0]
        if len(header) != 3 or [tag for tag, _ in header] != ["th"] * 3:
            continue
        if header[1][1] != "Free Tier" or not re.fullmatch(r"Paid Tier, per 1M tokens in USD", header[2][1], re.I):
            continue
        for row in rows[1:]:
            if len(row) != 3 or [tag for tag, _ in row] != ["td"] * 3:
                continue
            label, paid = row[0][1], row[2][1]
            item = labels.get(label)
            if not item:
                continue
            storage_periods = list(STORAGE.finditer(paid)) if item == "cache_read" else []
            read_paid = STORAGE.sub("", paid) if storage_periods else paid
            implicit_only = IMPLICIT_ONLY.search(read_paid) if item == "cache_read" else None
            if implicit_only:
                read_paid = IMPLICIT_ONLY.sub("", read_paid)
            periods = list(PERIOD.finditer(read_paid))
            # The paid cell must consist only of the quoted periods. A changed
            # format is unknown, never a reason to borrow a nearby model's rate.
            remainder = PERIOD.sub("", read_paid).strip(" .")
            if not periods or remainder:
                if not periods:
                    prices.append(llm_price(provider_model_id=model_id, upstream="Google", item=item, kind="published_rate", amount="", unit="", set_id="", locator=f"{title} > {tier.title()} > {label} > Paid Tier", unknown_reason="paid rate or period missing/ambiguous"))
                continue
            for match in periods:
                amount, direction, date_text = match.groups()
                try:
                    date = datetime.strptime(date_text, "%B %d, %Y").date()
                except ValueError:
                    continue
                future = direction.lower() == "starting"
                set_id = f"google_{tier}_{item}_{'future' if future else 'current'}"
                locator = f"{title} > {tier.title()} > {label} > Paid Tier: {match.group(0)}"
                conditions.append(llm_condition(set_id, "service_tier", tier, locator))
                conditions.append(llm_condition(set_id, "rate_period", "starting" if future else "through", locator))
                if implicit_only:
                    conditions.append(llm_condition(set_id, "explicit_prompt_caching", "not supported", f"{title} > {tier.title()} > {label} > Paid Tier: {implicit_only.group(0)}"))
                prices.append(llm_price(provider_model_id=model_id, upstream="Google", item=item,
                                     kind="announced_future_rate" if future else "published_rate", amount=amount,
                                     unit="1M tokens", set_id=set_id, locator=locator,
                                     effective_from=date.isoformat() if future else "",
                                     effective_until=date.isoformat() if not future else ""))
            for match in storage_periods:
                _, direction, date_text = match.groups()
                try:
                    date = datetime.strptime(date_text, "%B %d, %Y").date()
                except ValueError:
                    continue
                future = direction.lower() == "starting"
                set_id = f"google_{tier}_cache_storage_{'future' if future else 'current'}"
                locator = f"{title} > {tier.title()} > {label} > Paid Tier: {match.group(0)}"
                conditions.append(llm_condition(set_id, "service_tier", tier, locator))
                conditions.append(llm_condition(set_id, "rate_period", "starting" if future else "through", locator))
                prices.append(llm_price(provider_model_id=model_id, upstream="Google", item="cache_storage", kind="announced_future_rate" if future else "published_rate",
                                     amount="", unit="", set_id=set_id, locator=locator,
                                     effective_from=date.isoformat() if future else "", effective_until=date.isoformat() if not future else "",
                                     unknown_reason="source quotes USD per token-hour; core has no canonical token-hour price unit"))
    return prices, conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    title = TITLES.get(spec.model_id)
    if title is None:
        raise ValueError(f"google pricing page has no section reader for model {spec.model_id}")
    page = _GooglePricingReader(title); page.feed(body.decode("utf-8", errors="replace")); page.close()
    return _google(page, spec.model_id, title)
