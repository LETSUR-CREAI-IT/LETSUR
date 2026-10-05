"""Common public price parsers."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def llm_condition(set_id: str, key: str, value: str, locator: str, *, unit: str = "") -> dict:
    return dict(zip(ADAPTER_CONDITION_FIELDS, (set_id, key, "eq", value, unit, "stated", locator)))


def llm_price(*, provider_model_id: str, upstream: str, item: str, kind: str, amount: str, unit: str, set_id: str, locator: str, effective_from: str = "", effective_until: str = "", unknown_reason: str = "") -> dict:
    values = dict.fromkeys(ADAPTER_PRICE_FIELDS, "")
    values.update(provider_model_id=provider_model_id, upstream_provider=upstream, task="text_generation", billing_item=item,
                  price_kind=kind, amount=amount, currency="USD" if amount else "", unit=unit,
                  condition_set_id=set_id, effective_from=effective_from, effective_until=effective_until,
                  locator=locator, evidence_status="direct", unknown_reason=unknown_reason)
    return values


def _section(markdown: str, heading: str, level: int) -> str:
    """Return one exact Markdown heading's contents, stopping at its next peer."""
    lines = markdown.splitlines()
    marker = "#" * level + " "
    starts = [i for i, line in enumerate(lines) if line.strip() == marker + heading]
    if len(starts) != 1:
        return ""
    start = starts[0] + 1
    end = next((i for i in range(start, len(lines)) if re.match(r"^#{1," + str(level) + r"}\s", lines[i])), len(lines))
    return "\n".join(lines[start:end])


def _markdown_identity(body: str, title: str, model_id: str | None = None) -> bool:
    if not re.match(r"^# " + re.escape(title) + r"\s*$", body.strip().splitlines()[0] if body.strip() else ""):
        return False
    if model_id is None:
        return True
    ids = re.findall(r"^(?:- \*\*Model ID:\*\*|Model ID:)\s*`([^`]+)`\s*$", body, re.M)
    return ids == [model_id]


def _markdown_table(section: str, headers: list[str], target: str) -> list[str]:
    lines = section.splitlines()
    for i, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells != headers or i + 1 >= len(lines) or not re.fullmatch(r"[|:\-\s]+", lines[i + 1]):
            continue
        matches = []
        for candidate in lines[i + 2:]:
            if not candidate.strip().startswith("|"):
                break
            row = [cell.strip() for cell in candidate.strip().strip("|").split("|")]
            if len(row) == len(headers) and row[0] == target:
                matches.append(row)
        return matches[0] if len(matches) == 1 else []
    return []


class GeminiPricingScopeReader(HTMLParser):
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


class _Page(HTMLParser):
    """Keep visible headings, scoped tables and links with semantic cells."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.h3 = ""
        self.section = ""
        self.text: list[tuple[str, str]] = []
        self.links: list[tuple[str, str, str]] = []
        self.tables: list[tuple[str, str, list[list[tuple[str, str]]]]] = []
        self._heading = ""
        self._heading_text: list[str] = []
        self._table: list[list[tuple[str, str]]] | None = None
        self._row: list[tuple[str, str]] | None = None
        self._cell = ""
        self._cell_text: list[str] = []
        self._link = ""
        self._link_text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if self._skip:
            return
        if tag in ("h1", "h2", "h3"):
            self._heading, self._heading_text = tag, []
        elif tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("th", "td") and self._row is not None:
            self._cell, self._cell_text = tag, []
        elif tag == "a":
            self._link, self._link_text = dict(attrs).get("href") or "", []

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._heading:
            self._heading_text.append(data)
        elif self._cell:
            self._cell_text.append(data)
        elif data.strip():
            self.text.append((self.section, _clean(data)))
        if self._link:
            self._link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == self._heading:
            value = _clean("".join(self._heading_text))
            if tag == "h1":
                self.h1.append(value)
            elif tag == "h2":
                self.section, self.h3 = value, ""
                self.h2.append(value)
            else:
                self.h3 = value
            self._heading = ""
        elif tag == self._cell and self._row is not None:
            self._row.append((tag, _clean("".join(self._cell_text))))
            self._cell = ""
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append((self.section, self.h3, self._table))
            self._table = None
        elif tag == "a" and self._link:
            self.links.append((self.section, _clean("".join(self._link_text)), self._link))
            self._link = ""


def _table_rows(table: list[list[tuple[str, str]]], expected: set[str]) -> list[dict[str, str]]:
    if not table or not table[0] or any(tag != "th" for tag, _ in table[0]):
        return []
    headings = [value for _, value in table[0]]
    if len(set(headings)) != len(headings) or not expected <= set(headings):
        return []
    return [dict(zip(headings, (value for _, value in row))) for row in table[1:]
            if len(row) == len(headings) and all(tag == "td" for tag, _ in row)]


def _number(value: str) -> str:
    return format(Decimal(value.replace(",", "")).normalize(), "f")


def _plain(body: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()
