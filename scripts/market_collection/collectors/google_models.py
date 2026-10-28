"""Google Gemini API price parsers: one model's section of the shared pricing page, picked by its h2 id."""

from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser

from scripts.market_collection.core import SourceSpec
from .common import _clean, llm_condition, llm_price


PARSERS = frozenset({"google_model_pricing"})

# The Contributor tier is an invite-only private preview, so it is not a price anyone can order.
TIERS = ("standard", "batch", "flex", "priority")

# Row labels read per modality; every other row (grounding, data use) is not a model price.
LABELS = {
    "llm": {"Input price": "input", "Output price (including thinking tokens)": "output", "Context caching price": "cache_read"},
    "image": {"Input price": "input", "Output price": "output"},
}

NUMBER = r"\$(\d+(?:\.\d+)?)"
DATE = r"([A-Z][a-z]+ \d{1,2}, \d{4})"
RESOLUTION = r"\d+(?:\.\d+)?K(?:/\d+(?:\.\d+)?K)*"

# The statements a paid cell is made of. The page separates them with <br>, a bare line wrap or nothing, so a cell is
# read as one text. Anything left over that is no statement makes the whole cell unknown instead of a partial price.
STATEMENTS = (
    ("period", re.compile(rf"{NUMBER} (through|starting) {DATE}\.?")),
    ("storage", re.compile(rf"{NUMBER} / 1,000,000 tokens per hour \(storage price\)(?: (through|starting) {DATE})?\.?")),
    ("band", re.compile(rf"{NUMBER}, prompts (<=|>) (\d+k)(?: tokens)?")),
    ("image", re.compile(rf"(?:(?:[Ee]quivalent to|and) )?{NUMBER} per (?:({RESOLUTION}) )?image\*{{0,3}}[,.]?")),
    ("label", re.compile(rf"{NUMBER} \(([^()]+)\)(\*{{0,3}}),?")),
    ("plain", re.compile(NUMBER)),
)
# Remarks that state no price; "Same as Standard" is followed by the amounts themselves, never read as a rate.
NOTES = ("Same as Standard", "Implicit caching only (explicit caching is not supported).")
MODALITIES = re.compile(r"(?:text|image|video|audio)(?:\s*[/,]\s*(?:text|image|video|audio))*")
UNREADABLE = "paid rate or period missing/ambiguous"
STORAGE = "source quotes USD per token-hour; core has no canonical token-hour price unit"
UNIT_UNSTATED = "unit not stated: the value carries a footnote under a per-1M-token column header"


class _Section(HTMLParser):
    """Tier tables of the one h2 section with the given id; a cell is its whitespace-normalized text."""

    def __init__(self, section_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.section_id = section_id
        self.count = 0
        self.title = ""
        self.tables: list[tuple[str, list[list[tuple[str, str]]]]] = []
        self._inside = False
        self._tier = ""
        self._heading: str | None = None
        self._heading_parts: list[str] = []
        self._rows: list | None = None
        self._row: list | None = None
        self._cell: tuple[str, list[str]] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if self._skip:
            return
        if tag in ("h2", "h3"):
            self._heading, self._heading_parts = tag, []
            if tag == "h2":  # any h2 ends the previous section
                self._inside = dict(attrs).get("id") == self.section_id
                self._tier = ""
                if self._inside:
                    self.count += 1
        elif not self._inside:
            return
        elif tag == "table" and self._tier:
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in ("th", "td") and self._row is not None:
            self._cell = (tag, [])
        elif tag == "br" and self._cell:
            self._cell[1].append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._heading:
            self._heading_parts.append(data)
        if self._cell:
            self._cell[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == self._heading:
            text = _clean("".join(self._heading_parts))
            if self._inside and tag == "h2":
                self.title = text
            elif self._inside:
                self._tier = text.lower()
            self._heading = None
        elif tag in ("th", "td") and self._cell and self._row is not None:
            self._row.append((self._cell[0], _clean("".join(self._cell[1]))))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            self.tables.append((self._tier, self._rows))
            self._rows = None


def _statements(text: str) -> list[tuple[str, tuple, str]] | None:
    """The (kind, groups, quoted text) statements of a paid cell, or None if anything in it is not one."""
    found, pos = [], 0
    while pos < len(text):
        if text[pos] == " ":
            pos += 1
            continue
        note = next((note for note in NOTES if text.startswith(note, pos)), None)
        if note:
            pos += len(note)
            continue
        for kind, pattern in STATEMENTS:
            match = pattern.match(text, pos)
            if match:
                break
        else:
            return None
        found.append((kind, match.groups(), match.group(0)))
        pos = match.end()
    return found


def _period(direction: str, text: str) -> tuple[bool, str] | None:
    """(is a future rate, ISO date) of a "through" or "starting" date; None if the date is not a date."""
    try:
        return direction.lower() == "starting", datetime.strptime(text, "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def _entry(base: str, kind: str, groups: tuple) -> dict | None:
    """What one statement of a row (base item input, output or cache_read) says, or None when it cannot be read exactly."""
    entry = dict(item=base, unit="1M tokens", amount=groups[0], quals={}, tag="", period=None, unknown="")
    if kind == "period":
        entry["period"] = _period(*groups[1:])
        return entry if entry["period"] else None
    if kind == "storage":
        if base != "cache_read":
            return None
        entry.update(item="cache_storage", amount="", unit="", unknown=STORAGE, period=_period(*groups[1:]) if groups[1] else None)
        return None if groups[1] and not entry["period"] else entry
    if kind == "band":
        entry["quals"]["context_band"] = f"prompts {groups[1]} {groups[2]} tokens"
        entry["tag"] = ("le_" if groups[1] == "<=" else "gt_") + groups[2]
    elif kind == "image":  # a per-image price states its own unit, whatever the column header says
        if base == "cache_read":
            return None
        entry.update(item="image_input" if base == "input" else "image_output", unit="1 image", tag="per_image")
        if groups[1]:
            entry["quals"]["resolution"] = groups[1]
            entry["tag"] += "_" + re.sub(r"\W+", "_", groups[1].lower())
    elif kind == "label":
        label, footnote = groups[1], groups[2]
        if footnote:  # a footnote may restate the unit, as "$0.0006 (image)*" does: never read as per 1M tokens
            entry.update(amount="", unit="", unknown=UNIT_UNSTATED, tag="unit_unstated_" + re.sub(r"\W+", "_", label.lower()))
        elif base == "output" and label in ("text and thinking", "images"):
            entry["item"] = "output" if label == "text and thinking" else "image_output"
        elif base != "output" and MODALITIES.fullmatch(label):
            modalities = re.split(r"\s*[/,]\s*", label)
            if "text" not in modalities:  # the text rate is the model's price; a rate for audio alone is a row of its own
                entry["quals"]["input_modality"] = ",".join(modalities)  # not input_modalities: the comparison table must not mix it in
                entry["tag"] = "_".join(modalities)
        else:
            return None
    return entry


def _cell(spec: SourceSpec, title: str, tier: str, label: str, base: str, text: str) -> tuple[list[dict], list[dict]]:
    """Rows of one paid cell. A cell that cannot be read exactly is one amount-less row, never a partial price."""
    where = f"{title} > {tier.title()} > {label} > Paid Tier"
    statements = _statements(text)
    entries = [_entry(base, kind, groups) for kind, groups, _ in statements or []]
    if not entries or None in entries:
        return [dict(llm_price(provider_model_id=spec.model_id, upstream="Google", item=base, kind="published_rate", amount="", unit="", set_id="", locator=where, unknown_reason=UNREADABLE), task=spec.task)], []
    prices: list[dict] = []
    conditions: list[dict] = []
    seen: set[str] = set()
    for (_, _, quote), entry in zip(statements, entries):
        future = bool(entry["period"] and entry["period"][0])
        set_id = "_".join(part for part in ("google", tier, entry["item"], entry["tag"], ("future" if future else "current") if entry["period"] else "") if part)
        if set_id in seen:
            raise ValueError(f"Google pricing: {spec.model_id} {tier} {label} states {set_id} twice")
        seen.add(set_id)
        locator = f"{where}: {quote}"
        for key, value in (("service_tier", tier), *entry["quals"].items(), *((("rate_period", "starting" if future else "through"),) if entry["period"] else ())):
            conditions.append(llm_condition(set_id, key, value, locator))
        prices.append(dict(llm_price(provider_model_id=spec.model_id, upstream="Google", item=entry["item"], kind="announced_future_rate" if future else "published_rate",
                                     amount=entry["amount"], unit=entry["unit"], set_id=set_id, locator=locator,
                                     effective_from=entry["period"][1] if future else "", effective_until=entry["period"][1] if entry["period"] and not future else "",
                                     unknown_reason=entry["unknown"]), task=spec.task))
    return prices, conditions


def _paid_header(rows: list) -> bool:
    header = rows[0] if rows else []
    return len(header) == 3 and all(tag == "th" for tag, _ in header) and header[1][1] == "Free Tier" \
        and re.fullmatch(r"Paid Tier, per 1M tokens in USD", header[2][1], re.I) is not None


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    page = _Section(spec.model_id)
    page.feed(body.decode("utf-8", errors="replace")); page.close()
    if page.count != 1:
        raise ValueError(f"Google pricing page has {page.count} sections with id {spec.model_id}")
    labels = LABELS[spec.modality]
    required = {label for label, item in labels.items() if item in ("input", "output")}
    prices: list[dict] = []
    conditions: list[dict] = []
    tiers: set[str] = set()
    for tier, rows in page.tables:
        if tier not in TIERS:
            continue
        if tier in tiers or not _paid_header(rows):
            raise ValueError(f"Google pricing: {spec.model_id} {tier} table repeated or header changed")
        tiers.add(tier)
        seen: set[str] = set()
        for row in rows[1:]:
            label = row[0][1] if row else ""
            if label not in labels:
                continue
            if label in seen or len(row) != 3 or any(tag != "td" for tag, _ in row):
                raise ValueError(f"Google pricing: {spec.model_id} {tier} row {label} repeated or malformed")
            seen.add(label)
            own_prices, own_conditions = _cell(spec, page.title, tier, label, labels[label], row[2][1])
            prices.extend(own_prices)
            conditions.extend(own_conditions)
        if tier == "standard" and not required <= seen:
            raise ValueError(f"Google pricing: {spec.model_id} Standard table lacks {sorted(required - seen)}")
    if "standard" not in tiers:
        raise ValueError(f"Google pricing: {spec.model_id} has no Standard table")
    return prices, conditions
