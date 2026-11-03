"""Google's Gemini API documentation, read from its Markdoc source. Since 2026-11-03 the pages live on aistudio.google.com, whose HTML has
no h2 or table elements, and each page's source is served at <page>.md: "##" headings with an id, tier tabs, and tables of "*" cells with
rows split by "---". The ai.google.dev addresses redirect to that other host and the .md.txt copies left there have stopped following the
pages, so neither is read."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

HEADING = re.compile(r'## (.*?)\s*\{% id="([^"]+)" %\}\s*')
TAB = re.compile(r'\{% tab title="([^"]+)" %\}')
CELL = re.compile(r"\*(?: |$)")
# Inline markup, read as the page shows it: emphasis ("*Same as Standard*", a letter right after the asterisk, so never a footnote mark),
# footnote marks, of which {% sup /%} is the empty form the page shows as an asterisk ("$0.0504 per 2K image{% sup /%}"), links and
# line breaks. Other markup stays in the text, so a price cell with it is unreadable, never misread.
INLINE = ((re.compile(r"\*([A-Za-z][^*{}]*)\*"), r"\1"), (re.compile(r"\{% sup /%\}"), "*"), (re.compile(r"\{% sup %\}(.*?)\{% /sup %\}"), r"\1"),
          (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"), (re.compile(r"\\\\"), " "))


def _text(markup: str) -> str:
    for pattern, shown in INLINE:
        markup = pattern.sub(shown, markup)
    return re.sub(r"\s+", " ", markup).strip()


def _blocks(text: str) -> Iterator[tuple]:
    """A page in order: ("heading", title, id), ("tab", title), ("tab_end",), ("table", rows of cell texts). "###" headings stay inside
    their "##" section. A table cell goes on over its lines until the next "*" at the indent of the table's first cell."""
    rows: list[list[str]] | None = None
    indent = 0
    for line in text.splitlines():
        stripped = line.strip()
        if rows is not None:
            depth = len(line) - len(line.lstrip())
            if stripped == "{% /table %}":
                yield ("table", [[_text(cell) for cell in row] for row in rows if row])
                rows = None
            elif stripped == "---":
                rows.append([])
            elif CELL.match(stripped) and (not rows[0] or depth == indent):
                indent = depth if not rows[0] else indent
                rows[-1].append(stripped[1:])
            elif rows[-1]:
                rows[-1][-1] += "\n" + stripped
        elif line.startswith("## "):
            heading = HEADING.fullmatch(line)
            yield ("heading", _text(heading.group(1)), heading.group(2)) if heading else ("heading", _text(line[3:]), "")
        elif stripped == "{% table %}":
            rows = [[]]
        elif stripped == "{% /tab %}":
            yield ("tab_end",)
        elif tab := TAB.fullmatch(stripped):
            yield ("tab", tab.group(1))


@dataclass
class Section:
    """The sections with the id asked for: how many, the title of the last, and its tier tables as (tier, rows), a row being (tag, text)
    cells with the first row of a table as th. A table outside a tier tab is not kept."""
    count: int = 0
    title: str = ""
    tables: list[tuple[str, list[list[tuple[str, str]]]]] = field(default_factory=list)


def read_section(body: bytes, section_id: str) -> Section:
    """One model's section of the pricing page, by its heading id."""
    page = Section()
    inside = False
    tier = ""
    for kind, *value in _blocks(body.decode("utf-8", errors="replace")):
        if kind == "heading":  # any heading ends the previous section
            inside = value[1] == section_id
            tier = ""
            if inside:
                page.count += 1
                page.title = value[0]
        elif inside and kind == "tab":
            tier = value[0].lower()
        elif kind == "tab_end":
            tier = ""
        elif inside and kind == "table" and tier:
            page.tables.append((tier, [[("td" if number else "th", cell) for cell in row] for number, row in enumerate(value[0])]))
    return page


def section_tables(text: str) -> list[tuple[str, list[str], list[list[str]]]]:
    """(the "##" heading above, header cells, row cells) of the deprecations page, its tables with one header under one heading as one."""
    found: list[tuple[str, list[str], list[list[str]]]] = []
    heading = ""
    for kind, *value in _blocks(text):
        if kind == "heading":
            heading = value[0]
        elif kind == "table" and value[0]:
            head, *rows = value[0]
            if found and found[-1][:2] == (heading, head):
                found[-1][2].extend(rows)
            else:
                found.append((heading, head, rows))
    return found
