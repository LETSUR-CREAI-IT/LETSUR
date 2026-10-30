"""Official model retirement schedules, read in their Markdown form (2026-10-29): Azure's Foundry model retirement
schedule, OpenAI's and Anthropic's deprecation pages and the Gemini API deprecations page.

A table row becomes an entry only when it names a registered model id exactly: Azure's Model cell (or Model-Version for
a dated id we register, gpt-5.4-2026-03-05), the API model name in Anthropic's status table, an id in backticks on the
OpenAI and Google pages. Names that differ in any way (Kimi-K2.5, claude-opus-4-5, "GPT-4o (`gpt-4o-2024-08-06`)")
are skipped, never guessed. Anthropic's status table counts only for a Deprecated or Retired model: an Active model's
"Not sooner than" date is a minimum lifetime, not an announced retirement (2026-10-30). Each page's own wording of the
date is kept: every date of the Gemini table (its note says they are the earliest possible dates) and a "Not sooner
than" date are earliest dates, not retirement dates.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from scripts.market_collection.models import MODEL_MODALITIES

from .parsers import SignalParseError

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
NONE = {"", "—", "-", "---", "N/A"}
AZURE_HEAD = ["Model", "Version", "Lifecycle", "Retirement date", "Replacement"]
ANTHROPIC_HEAD = ["API model name", "Current state", "Deprecated", "Tentative retirement date"]
ANTHROPIC_ENDING = {"deprecated", "retired"}  # the states whose date is a retirement Anthropic has announced
ANTHROPIC_HISTORY = ["Retirement date", "Deprecated model", "Recommended replacement"]


@dataclass(frozen=True)
class Retirement:
    model_id: str
    version: str      # Azure's model version; "" on the other pages
    state: str        # lifecycle as the page writes it; "" where the table has none
    date: str         # ISO date
    earliest: bool    # the page states the earliest date the model may retire, not the date
    date_text: str    # the date cell as written
    replacement: str  # as written, "" when none is stated
    announced: str    # ISO date the page says the deprecation was announced, "" when it does not
    section: str
    column: str
    quote: str
    locator: str

    @property
    def entry_id(self) -> str:
        return f"{self.model_id}@{self.version}" if self.version else self.model_id


def day(text: str) -> str:
    """The ISO date a cell states ('2026-11-19', 'November 24, 2026', 'Apr 1, 2027'; OpenAI writes some ISO dates with
    non-breaking hyphens), or ''."""
    text = re.sub("[‐‑‒−]", "-", text)
    if found := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        year, month, number = map(int, found.groups())
    elif (found := re.search(r"\b([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})\b", text)) and found.group(1).lower() in MONTHS:
        year, month, number = int(found.group(3)), MONTHS.index(found.group(1).lower()) + 1, int(found.group(2))
    else:
        return ""
    try:
        return date(year, month, number).isoformat()
    except ValueError:
        return ""


def tables(text: str) -> list[tuple[str, list[str], list[list[str]]]]:
    """(nearest heading above, header cells, row cells) of every pipe table; '\\|' inside a cell is a literal pipe."""
    found, heading, block = [], "", []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            block.append([cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip()[1:-1])])
            continue
        if len(block) > 2:
            found.append((heading, [cell.strip("*").strip() for cell in block[0]], block[2:]))
        block = []
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
    return found


def plain(cell: str) -> str:
    """A replacement cell without code marks and footnote marks; '' when it says none."""
    text = re.sub(r"\^\d+\^", "", cell.replace("`", "")).strip()
    return "" if text in NONE else text


def _quote(section: str, head: list[str], cells: list[str], note: str = "") -> str:
    return f"{section}: " + "; ".join(f"{name}: {cell}" for name, cell in zip(head, cells) if cell) + note


def _azure(text: str) -> tuple[int, list[Retirement]]:
    rows, found = 0, []
    for section, head, cells_list in tables(text):
        if head != AZURE_HEAD:
            continue
        for index, cells in enumerate(cells_list):
            rows += 1
            name, version, state, stated, replacement = (cells + [""] * 5)[:5]
            # a dated id we register is the model name and its version (our Azure price source reads it from that row)
            for model in (name, f"{name}-{version}"):
                if model in MODEL_MODALITIES and day(stated):
                    found.append(Retirement(model, version, state, day(stated), False, stated, plain(replacement), "", section,
                                            "Retirement date", _quote(section, head, cells), f"{section}/row[{index}]"))
    return rows, found


def _openai(text: str) -> tuple[int, list[Retirement]]:
    rows, found = 0, []
    for section, head, cells_list in tables(text):
        if head[:1] != ["Shutdown date"]:
            continue  # the product timelines (Date, Update) name no model
        announced = day(section) if re.match(r"\d{4}-\d{2}-\d{2}:", section) else ""
        for index, cells in enumerate(cells_list):
            rows += 1
            # the second column names the models (Model / system, Model snapshot, Deprecated model, ...), the last the replacement
            for model in re.findall(r"`([^`]+)`", cells[1] if len(cells) > 1 else ""):
                if model in MODEL_MODALITIES and day(cells[0]):
                    found.append(Retirement(model, "", "", day(cells[0]), False, cells[0], plain(cells[-1]), announced, section,
                                            "Shutdown date", _quote(section, head, cells), f"{section}/row[{index}]"))
    return rows, found


def _anthropic(text: str) -> tuple[int, list[Retirement]]:
    replacements: dict[str, str] = {}
    for _, head, cells_list in tables(text):
        if head == ANTHROPIC_HISTORY:
            for cells in cells_list:
                for model in re.findall(r"`([^`]+)`", cells[1]):
                    replacements.setdefault(model, plain(cells[2]))  # newest announcement first
    rows, found = 0, []
    for section, head, cells_list in tables(text):
        if head != ANTHROPIC_HEAD:
            continue
        for index, cells in enumerate(cells_list):
            rows += 1
            name, state, deprecated, stated = (cells + [""] * 4)[:4]
            if name in MODEL_MODALITIES and state.lower() in ANTHROPIC_ENDING and day(stated):
                found.append(Retirement(name, "", state, day(stated), stated.lower().startswith("not sooner than"), stated,
                                        replacements.get(name, ""), day(deprecated), section, "Tentative retirement date",
                                        _quote(section, head, cells), f"{section}/row[{index}]"))
    return rows, found


def _google(text: str) -> tuple[int, list[Retirement]]:
    # The page: "The shutdown dates listed in the table indicate the earliest possible dates on which a model might be retired"
    earliest = re.search(r"earliest\s+possible", text, re.I) is not None
    note = " (page note: the shutdown dates indicate the earliest possible dates)" if earliest else ""
    rows, found = 0, []
    for section, head, cells_list in tables(text):
        if len(head) != 4 or head[2:] != ["Shutdown date", "Recommended replacement"]:
            continue
        for index, cells in enumerate(cells_list):
            rows += 1
            for model in re.findall(r"`([^`]+)`", cells[0]):
                if model in MODEL_MODALITIES and day(cells[2]):
                    found.append(Retirement(model, "", "", day(cells[2]), earliest, cells[2], plain(cells[3]), "", section,
                                            "Shutdown date", _quote(section, head, cells, note), f"{section}/row[{index}]"))
    return rows, found


READERS: dict[str, Callable[[str], tuple[int, list[Retirement]]]] = {
    "azure_retirements": _azure, "openai_deprecations": _openai, "anthropic_deprecations": _anthropic, "google_deprecations": _google}


def read(parser: str, body: bytes) -> tuple[int, list[Retirement]]:
    """(table rows read, entries of registered models); a body without the page's own tables is refused."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SignalParseError("retirement page is not UTF-8 text") from exc
    rows, found = READERS[parser](text)
    if not rows:
        raise SignalParseError(f"no retirement table of {parser}")
    entries, seen = [], set()
    for entry in found:
        if (entry.model_id, entry.version) not in seen:  # a model listed twice keeps the first row (newest first on the pages)
            seen.add((entry.model_id, entry.version))
            entries.append(entry)
    return rows, entries


def page_url(url: str) -> str:
    """The page people read for a registered Markdown URL."""
    return re.sub(r"\.md(?:\.txt)?$", "", url.split("?", 1)[0])


def retirement_signals(parser: str, body: bytes, url: str) -> tuple[int, list[dict]]:
    """One signal per entry. The announcement date stays off the row: one page holds many rows, and the dashboard dates
    an undated card by its page's single publication day (build.article_day); the events engine reads it from the entry."""
    rows, entries = read(parser, body)
    return rows, [{"entry_id": entry.entry_id, "entry_url": page_url(url), "entry_title": entry.section, "published_at_utc": "",
                   "locator": entry.locator, "model_label": entry.model_id, "model_ids": entry.model_id, "model_match": "exact",
                   "claim_kinds": "retirement", "match_terms": entry.column, "quote": entry.quote, "date_phrases": entry.date}
                  for entry in entries]
