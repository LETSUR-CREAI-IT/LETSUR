"""OpenAI's own pages for the models LETSUR calls directly: text models, embeddings and gpt-image-2 (its own module: the
other OpenAI parsers keep their fingerprints). A missing, repeated or reformatted target raises; nothing is borrowed."""

from __future__ import annotations

import re

from scripts.market_collection.core import SourceSpec
from scripts.market_collection.models import GPT_IMAGE_2_MODEL, OPENAI_DIRECT_MODELS, OPENAI_EMBEDDING_MODELS
from scripts.market_collection.sources import _key
from .common import _markdown_identity, _number, _section, llm_condition, llm_price

PARSERS = {f"openai_{_key(model)}" for model in (*(row[0] for row in OPENAI_DIRECT_MODELS), *OPENAI_EMBEDDING_MODELS, GPT_IMAGE_2_MODEL)}
AMOUNT = r"\$[0-9]+(?:\.[0-9]+)?"
TEXT_ITEMS = {"Input": "input", "Cached input": "cache_read", "Cache writes": "cache_write", "Output": "output"}
PROMOTION = re.compile(r"promotional pricing is available at least through [A-Z][a-z]+ \d{1,2}, \d{4}\.")
IMAGE_HEADER = ["Model", "Modality", "Input", "Output", "Cached input"]
IMAGE_REQUIRED = {("Image", "Input"), ("Image", "Output")}  # the rates LETSUR lists


def _price(spec: SourceSpec, **fields) -> dict:
    return {**llm_price(**fields), "task": spec.task}  # llm_price fixes "text_generation"


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")] if line.strip().startswith("|") else []


def _once(lines: list[str], label: str, start: int = 0, stop: int | None = None) -> int:
    found = [i for i in range(start, len(lines) if stop is None else stop) if lines[i].strip() == label]
    if len(found) != 1:
        raise ValueError(f"OpenAI pricing page label absent or repeated: {label}")
    return found[0]


def _table(part: list[str], header: list[str], what: str) -> list[list[str]]:
    """Rows of the one table in these lines whose header cells are exactly `header`; none, several or no separator row is an error."""
    found = [i for i, line in enumerate(part) if _cells(line) == header]
    if len(found) != 1 or found[0] + 1 >= len(part) or not re.fullmatch(r"[|:\-\s]+", part[found[0] + 1]):
        raise ValueError(f"OpenAI {what} table absent, repeated or without its separator row")
    rows = []
    for line in part[found[0] + 2:]:
        if not line.strip().startswith("|"):
            break
        rows.append(_cells(line))
    return rows


def _rates(section: str, labels: set[str]) -> dict[str, str]:
    """label -> amount of the `| label | $N | 1M tokens |` rows; a repeated label or any other cell form is an error."""
    found: dict[str, str] = {}
    for cells in map(_cells, section.splitlines()):
        if not cells or cells[0] not in labels:
            continue
        if len(cells) != 3 or cells[0] in found or not re.fullmatch(AMOUNT, cells[1]) or cells[2] != "1M tokens":
            raise ValueError(f"OpenAI price row repeated or reformatted: {cells[0]}")
        found[cells[0]] = cells[1][1:]
    return found


def _note(matches: list[str], what: str) -> str:
    if len(matches) > 1:
        raise ValueError(f"OpenAI {what} stated more than once")
    return matches[0] if matches else ""


def _identity(spec: SourceSpec, body: str, title: str, slug: str) -> list[dict]:
    """Conditions, in a set no price points to, saying how the page covers the registered model. A model whose id is not
    the URL slug is a snapshot: the page must be the slug's model and list it under Snapshots. Otherwise the page's Model ID
    is the model, or it is the target of an alias that the page says routes there. Anything else raises."""
    ids = re.findall(r"^Model ID:\s*`([^`]+)`\s*$", body, re.M)
    if len(ids) != 1 or not _markdown_identity(body, title, ids[0]):
        raise ValueError("OpenAI page title or Model ID differs from the registered model")
    model, page, group = spec.model_id, ids[0], f"{spec.parser}_identity"
    if model != slug:
        if page != slug or f"- `{model}`" not in [line.strip() for line in _section(body, "Snapshots", 2).splitlines()]:
            raise ValueError(f"OpenAI page does not list {model} as a snapshot of {slug}")
        return [llm_condition(group, "model_snapshot", model, f"{title} > Snapshots: - `{model}`"),
                llm_condition(group, "snapshot_of", page, f"{title} > Model ID: `{page}`")]
    if page == model:
        return []
    alias = f"The `{model}` alias routes requests to {title}."
    if alias not in " ".join(body.split()):  # the sentence may wrap across lines
        raise ValueError(f"OpenAI page does not state that {model} routes to {page}")
    return [llm_condition(group, "model_alias_of", page, f"{title}: {alias}")]


def _text(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    title, slug = {model: (page_title, page) for model, page_title, page in OPENAI_DIRECT_MODELS}[spec.model_id]
    conditions = _identity(spec, body, title, slug)
    section = _section(_section(body, "Pricing", 2), "Text tokens", 3)
    rows = _rates(section, set(TEXT_ITEMS))
    if "Input" not in rows or "Output" not in rows:
        raise ValueError("OpenAI Text tokens Input or Output row absent")
    notes = {"long_context_multiplier": _note(re.findall(r"^- (.*272K input tokens.*)$", section, re.M), "272K rule"),
             "promotion_note": _note(PROMOTION.findall(section), "promotion")}
    prices = []
    for label, item in TEXT_ITEMS.items():
        if label not in rows:
            continue
        set_id = f"{spec.parser}_standard_{item}"
        locator = f"{title} > Pricing > Text tokens > {label}: ${rows[label]} / 1M tokens"
        conditions.append(llm_condition(set_id, "service_tier", "standard", locator))
        for key, note in notes.items():
            if note:
                conditions.append(llm_condition(set_id, key, note, f"{title} > Pricing > Text tokens: {note}"))
        prices.append(_price(spec, provider_model_id=spec.model_id, upstream="OpenAI", item=item, kind="published_rate", amount=rows[label], unit="1M tokens", set_id=set_id, locator=locator))
    return prices, conditions


def _embedding(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    conditions = _identity(spec, body, spec.model_id, spec.model_id)  # the page title is the model id itself
    rows = _rates(_section(_section(body, "Pricing", 2), "Embeddings", 3), {"Cost"})
    if "Cost" not in rows:
        raise ValueError("OpenAI Embeddings Cost row absent")
    set_id = f"{spec.parser}_standard_input"
    locator = f"{spec.model_id} > Pricing > Embeddings > Cost: ${rows['Cost']} / 1M tokens"
    conditions.append(llm_condition(set_id, "service_tier", "standard", locator))
    return [_price(spec, provider_model_id=spec.model_id, upstream="OpenAI", item="input", kind="published_rate", amount=rows["Cost"], unit="1M tokens", set_id=set_id, locator=locator)], conditions


def _image(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    """gpt-image-2's Image and Text rows in the Standard and Batch tables of the image generation models on the pricing page."""
    lines = body.splitlines()
    top = _once(lines, "Image generation models")
    bottom = _once(lines, "Transcription models", top + 1)
    _once(lines, "Prices per 1M tokens.", top, bottom)
    standard = _once(lines, "Standard", top, bottom)
    batch = _once(lines, "Batch", standard + 1, bottom)
    prices, conditions = [], []
    for processing, begin, end in (("Standard", standard, batch), ("Batch", batch, bottom)):
        table = _table(lines[begin:end], IMAGE_HEADER, f"image {processing}")
        own = [cells for cells in table if cells[:1] == [GPT_IMAGE_2_MODEL]]
        if len(own) != 2 or any(len(cells) != 5 for cells in own) or {cells[1] for cells in own} != {"Image", "Text"}:
            raise ValueError(f"OpenAI image {processing} rows of {GPT_IMAGE_2_MODEL} absent, repeated or reformatted")
        for _, modality, *rates in own:
            for column, cell in zip(IMAGE_HEADER[2:], rates):
                if cell == "-":  # no such rate listed (text output is not billed)
                    if (modality, column) in IMAGE_REQUIRED:
                        raise ValueError(f"OpenAI image {processing} {modality} {column} price absent")
                    continue
                if not re.fullmatch(AMOUNT, cell):
                    raise ValueError(f"OpenAI image price cell reformatted: {processing} {modality} {column}")
                set_id = f"{spec.parser}_{processing.lower()}_{modality.lower()}_{column.lower().replace(' ', '_')}"
                locator = f"Image generation models > {processing} > {GPT_IMAGE_2_MODEL} > {modality} > {column}: {cell} / 1M tokens"
                conditions.append(llm_condition(set_id, "processing", processing, locator))
                prices.append(_price(spec, provider_model_id=spec.model_id, upstream="OpenAI", item=f"{modality} tokens / {column}", kind="published_rate", amount=_number(cell[1:]), unit="1M tokens", set_id=set_id, locator=locator))
    return prices, conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    text = body.decode("utf-8", errors="replace" if spec.modality == "llm" else "strict")
    if spec.modality == "image":
        return _image(spec, text)
    return (_embedding if spec.task == "embedding" else _text)(spec, text)
