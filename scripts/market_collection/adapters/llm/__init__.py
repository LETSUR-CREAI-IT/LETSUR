"""Evidence-scoped Gemini 3.8 Flash public pricing adapters."""

from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec

MODEL = "gemini-3.8-flash"
BROKER_MODEL = "google/gemini-3.8-flash"
MONEY = r"\$([0-9]+(?:\.[0-9]+)?)"
PERIOD = re.compile(rf"{MONEY}\s+(through|starting)\s+([A-Z][a-z]+\s+\d{{1,2}},\s+\d{{4}})\.?", re.I)
STORAGE = re.compile(rf"{MONEY}\s*/\s*1,000,000\s+tokens\s+per\s+hour\s*\(storage price\)\s+(through|starting)\s+([A-Z][a-z]+\s+\d{{1,2}},\s+\d{{4}})\.?", re.I)


def source_specs() -> list[SourceSpec]:
    return [
        SourceSpec("google_gemini_38_flash", "llm", MODEL, "google_direct", "text_generation", "https://ai.google.dev/gemini-api/docs/pricing", "google"),
        SourceSpec("openrouter_gemini_38_flash", "llm", MODEL, "openrouter", "text_generation", "https://openrouter.ai/google/gemini-3.8-flash/providers", "openrouter"),
        SourceSpec("vercel_gemini_38_flash", "llm", MODEL, "vercel_ai_gateway", "text_generation", "https://vercel.com/ai-gateway/models/gemini-3.8-flash", "vercel"),
    ]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class _Reader(HTMLParser):
    """Read visible text and semantic table cells, scoped by h2/h3 headings."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.model = False
        self.tier = ""
        self.heading: str | None = None
        self.heading_parts: list[str] = []
        self.table = False
        self.row: list[tuple[str, str]] | None = None
        self.cell: tuple[str, list[str]] | None = None
        self.current_rows: list[list[tuple[str, str]]] = []
        self.tables: list[tuple[str, list[list[tuple[str, str]]]]] = []
        self.text: list[str] = []
        self.scoped_text: list[str] = []
        self.model_scope = False
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self.suppressed += 1
        if self.suppressed:
            return
        if tag in ("h1", "h2", "h3"):
            self.heading = tag
            self.heading_parts = []
            if tag == "h2" and dict(attrs).get("id") == "gemini-3.8-flash":
                self.model = True
                self.tier = ""
        elif tag == "table" and self.model and self.tier:
            self.table = True
            self.current_rows = []
        elif tag == "tr" and self.table:
            self.row = []
        elif tag in ("th", "td") and self.row is not None:
            self.cell = (tag, [])
        elif tag == "br" and self.cell:
            self.cell[1].append("\n")

    def handle_data(self, data: str) -> None:
        if self.suppressed:
            return
        if self.heading:
            self.heading_parts.append(data)
        if self.cell:
            self.cell[1].append(data)
        if data.strip():
            self.text.append(_clean(data))
            if self.model_scope and not self.heading:
                self.scoped_text.append(_clean(data))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self.suppressed:
            self.suppressed -= 1
            return
        if self.suppressed:
            return
        if tag == self.heading:
            heading = _clean("".join(self.heading_parts))
            if tag == "h2":
                self.model = heading.lower() == "gemini 3.8 flash"
                self.tier = ""
            elif tag == "h1":
                self.model_scope = bool(re.fullmatch(r"(?:Google:\s*)?Gemini 3\.8 Flash", heading, re.I))
            elif tag == "h3" and self.model:
                self.tier = heading.lower()
            if tag in ("h2", "h3") and self.model_scope:
                # The broker quote is in the model overview. A subsequent
                # section is a new evidence boundary; OpenRouter's own model
                # slug directly under h1 is the one allowed subheading.
                same_model = bool(re.fullmatch(r"(?:Google:\s*)?Gemini 3\.8 Flash|google\s*/\s*gemini-3\.8-flash", heading, re.I))
                if not same_model:
                    self.model_scope = False
            self.heading = None
        elif tag in ("th", "td") and self.cell and self.row is not None:
            self.row.append((self.cell[0], _clean("".join(self.cell[1]))))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.current_rows.append(self.row)
            self.row = None
        elif tag == "table" and self.table:
            self.tables.append((self.tier, self.current_rows))
            self.table = False


def _condition(set_id: str, key: str, value: str, locator: str, *, unit: str = "") -> dict:
    return dict(zip(ADAPTER_CONDITION_FIELDS, (set_id, key, "eq", value, unit, "stated", locator)))


def _price(*, provider_model_id: str, upstream: str, item: str, kind: str, amount: str, unit: str, set_id: str, locator: str, effective_from: str = "", effective_until: str = "", unknown_reason: str = "") -> dict:
    values = dict.fromkeys(ADAPTER_PRICE_FIELDS, "")
    values.update(provider_model_id=provider_model_id, upstream_provider=upstream, task="text_generation", billing_item=item,
                  price_kind=kind, amount=amount, currency="USD" if amount else "", unit=unit,
                  condition_set_id=set_id, effective_from=effective_from, effective_until=effective_until,
                  locator=locator, evidence_status="direct", unknown_reason=unknown_reason)
    return values


def _google(reader: _Reader) -> tuple[list[dict], list[dict]]:
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
            periods = list(PERIOD.finditer(read_paid))
            # The paid cell must consist only of the quoted periods. A changed
            # format is unknown, never a reason to borrow a nearby model's rate.
            remainder = PERIOD.sub("", read_paid).strip(" .")
            if not periods or remainder:
                if not periods:
                    prices.append(_price(provider_model_id=MODEL, upstream="Google", item=item, kind="published_rate", amount="", unit="", set_id="", locator=f"Gemini 3.8 Flash > {tier.title()} > {label} > Paid Tier", unknown_reason="paid rate or period missing/ambiguous"))
                continue
            for match in periods:
                amount, direction, date_text = match.groups()
                try:
                    date = datetime.strptime(date_text, "%B %d, %Y").date()
                except ValueError:
                    continue
                future = direction.lower() == "starting"
                set_id = f"google_{tier}_{item}_{'future' if future else 'current'}"
                locator = f"Gemini 3.8 Flash > {tier.title()} > {label} > Paid Tier: {match.group(0)}"
                conditions.append(_condition(set_id, "service_tier", tier, locator))
                conditions.append(_condition(set_id, "rate_period", "starting" if future else "through", locator))
                prices.append(_price(provider_model_id=MODEL, upstream="Google", item=item,
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
                locator = f"Gemini 3.8 Flash > {tier.title()} > {label} > Paid Tier: {match.group(0)}"
                conditions.append(_condition(set_id, "service_tier", tier, locator))
                conditions.append(_condition(set_id, "rate_period", "starting" if future else "through", locator))
                prices.append(_price(provider_model_id=MODEL, upstream="Google", item="cache_storage", kind="announced_future_rate" if future else "published_rate",
                                     amount="", unit="", set_id=set_id, locator=locator,
                                     effective_from=date.isoformat() if future else "", effective_until=date.isoformat() if not future else "",
                                     unknown_reason="source quotes USD per token-hour; core has no canonical token-hour price unit"))
    return prices, conditions


def _broker(reader: _Reader, parser: str) -> tuple[list[dict], list[dict]]:
    page = " ".join(reader.scoped_text)
    if not page:
        return [], []
    if parser == "openrouter":
        pattern = re.compile(r"In\s*/\s*Out Price\s+(?:(?P<promo>50% off)\s+)?\$(?P<input>[0-9]+(?:\.[0-9]+)?)\s*/\s*\$(?P<output>[0-9]+(?:\.[0-9]+)?)\s+per\s+1M\b", re.I)
        kind = "published_rate"
        basis = "model_page_rate"
        upstream = ""
    else:
        # Vercel's sr-only price string may contain React HTML comments
        # between the output amount and comma. HTMLParser joins those text
        # nodes with a space; that layout whitespace is not part of the unit.
        pattern = re.compile(rf"Prices from:\s*Input\s*{MONEY}\s*,\s*Output\s*{MONEY}\s*,\s*Per\s+1M\s+tokens", re.I)
        kind = "published_rate"
        basis = "prices_from_minimum"
        upstream = ""
    match = pattern.search(page)
    if not match:
        return [], []
    set_id = f"{parser}_{basis}"
    locator = f"Gemini 3.8 Flash > {match.group(0)}"
    conditions = [_condition(set_id, "price_basis", basis, locator)]
    if parser == "openrouter" and match.group("promo"):
        conditions.append(_condition(set_id, "promotion_label", match.group("promo"), locator))
    prices = [_price(provider_model_id=BROKER_MODEL, upstream=upstream, item=item, kind=kind,
                     amount=amount, unit="1M tokens", set_id=set_id, locator=locator)
              for item, amount in zip(("input", "output"), (match.group("input"), match.group("output")) if parser == "openrouter" else match.groups())]
    return prices, conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    expected = {item.parser: item for item in source_specs()}
    if spec.parser not in expected or spec != expected[spec.parser]:
        raise ValueError("LLM source spec differs from fixed registry")
    if not isinstance(body, bytes):
        raise TypeError("source body must be bytes")
    reader = _Reader()
    reader.feed(body.decode("utf-8", errors="replace"))
    reader.close()
    if spec.parser == "google":
        return _google(reader)
    return _broker(reader, spec.parser)
