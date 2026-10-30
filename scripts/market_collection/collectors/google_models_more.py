"""Gemini API prices google_models.py does not read, from the same pricing page (its own module, so the sources of google_models.py
keep their parser fingerprints). The section reader, table header check and cell statements are google_models.py's.

- google_model_pricing_more: a model's own section whose input row is labelled by its modalities, "Input price (text, image,
  video)", as gemini-2.5-flash-lite's is (2026-10-30). The cell still states each modality's rate, so audio stays a row of its own.
- google_routed_pricing: a model whose API requests Google routes to another model (GOOGLE_ROUTED_MODELS) is priced from that
  model's section, read exactly as google_models.py reads it, so the rows name the serving model as provider_model_id. Once the
  routed model has a section of its own, this raises instead of reading its successor's.
"""

from __future__ import annotations

from dataclasses import replace

from scripts.market_collection.core import SourceSpec
from scripts.market_collection.models import GOOGLE_ROUTED_MODELS
from . import google_models
from .google_models import TIERS, _Section, _cell, _paid_header

PARSERS = frozenset({"google_model_pricing_more", "google_routed_pricing"})
LABELS = {**google_models.LABELS["llm"], "Input price (text, image, video)": "input"}


def _page(body: bytes, section_id: str) -> _Section:
    page = _Section(section_id)
    page.feed(body.decode("utf-8", errors="replace")); page.close()
    return page


def _own_section(spec: SourceSpec, body: bytes) -> tuple[list[dict], list[dict]]:
    """google_models.parse_source with LABELS; two rows of one table read as the same item raise."""
    page = _page(body, spec.model_id)
    if page.count != 1:
        raise ValueError(f"Google pricing page has {page.count} sections with id {spec.model_id}")
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
            if label not in LABELS:
                continue
            if LABELS[label] in seen or len(row) != 3 or any(tag != "td" for tag, _ in row):
                raise ValueError(f"Google pricing: {spec.model_id} {tier} row {label} repeated or malformed")
            seen.add(LABELS[label])
            own_prices, own_conditions = _cell(spec, page.title, tier, label, LABELS[label], row[2][1])
            prices.extend(own_prices)
            conditions.extend(own_conditions)
        if tier == "standard" and not {"input", "output"} <= seen:
            raise ValueError(f"Google pricing: {spec.model_id} Standard table lacks {sorted({'input', 'output'} - seen)}")
    if "standard" not in tiers:
        raise ValueError(f"Google pricing: {spec.model_id} has no Standard table")
    return prices, conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    if spec.parser == "google_model_pricing_more":
        return _own_section(spec, body)
    served = dict(GOOGLE_ROUTED_MODELS)[spec.model_id]
    if _page(body, spec.model_id).count:
        raise ValueError(f"Google pricing page now has a section of its own for {spec.model_id}; it is no longer read as {served}")
    return google_models.parse_source(replace(spec, model_id=served), body, source_id)
