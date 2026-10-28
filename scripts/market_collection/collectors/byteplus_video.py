"""BytePlus ModelArk Seedance 2.5 rates by video input, for LETSUR's reference-to-video and video-to-video models.

The same pricing page as byteplus_seedance_25_t2v; its own module so that source keeps its parser fingerprint.
"""

from __future__ import annotations

import re

from scripts.market_collection.core import SourceSpec
from scripts.market_collection.models import BYTEPLUS_ID
from .byteplus import _online_rates, _table, _zone_text, _zones, is_unrendered_template
from .video_common import conditions, price

PARSERS = ("byteplus_video_input",)
# The pricing tabs a task reads and the video input each tab states: a video-to-video request always has one.
TABS = {"reference-to-video": (("Input without video", "none"), ("Input with video", "yes")),
        "video-to-video": (("Input with video", "yes"),)}
LABELS = (("480p and 720p", "480p/720p"), ("1080p", "1080p"))
CELL = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)")


def _rows(zones: dict, title: str) -> list[tuple[list[str], list[str], list[str]]]:
    """Header rows and Seedance 2.5 row of each table in a tab of this title (the page has several such tabs)."""
    found = []
    for zone in zones.values():
        for op in zone["ops"]:
            attributes = op.get("attributes", {})
            if attributes.get("tabContent") and attributes.get("title", "").strip().lower() == title.lower():
                grid = _table(zones, attributes["zoneId"])
                found += [(grid[0], grid[1], row) for row in grid[2:] if row[0].lower() == BYTEPLUS_ID]
    return found


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    if spec.parser not in PARSERS or spec.task not in TABS:
        raise ValueError("unhandled BytePlus video-input source")
    if is_unrendered_template(body):
        raise ValueError("BytePlus unrendered SSR template: document body unavailable")
    try:
        zones = _zones(body)
        page = _zone_text(zones["0"])
        tabs = [(title, video, _rows(zones, title)) for title, video in TABS[spec.task]]
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("BytePlus pricing document absent or reformatted") from exc
    # The unit is the page's own pricing note, never guessed from cell values.
    if not re.search(r"Token\s+prices\s+are\s+USD\s+per\s+million\s+tokens", page, re.I):
        raise ValueError("BytePlus USD per million token unit missing")
    prices: list[dict] = []
    own: list[dict] = []
    for title, video, rows in tabs:
        if len(rows) != 1:
            raise ValueError(f"BytePlus {BYTEPLUS_ID} row absent or duplicate in {title}")
        rates = _online_rates(*rows[0])
        before = len(prices)
        for label, resolution in LABELS:
            cell = rates.get(label, "")
            if cell == "Not supported":
                continue
            match = CELL.fullmatch(cell)
            if not match:
                raise ValueError(f"BytePlus {title} {label} rate is not a plain dollar amount")
            group = f"{source_id}:byteplus_{video}_{resolution.replace('/', '_')}"
            locator = f"Video generation models > Pricing > {title} > {BYTEPLUS_ID} > Online inference > {label}: {cell}"
            prices.append(price(spec, BYTEPLUS_ID, "ByteDance", match[1], "1M tokens", group, locator, version="2.5"))
            own.extend(conditions(group, locator, resolution=resolution, input_video=video, inference_mode="online"))
        if len(prices) == before:
            raise ValueError(f"BytePlus {title} has no Seedance 2.5 online rate")
    formula = re.search(r"Estimated token consumption:\s*([^\n]*\S)", page, re.I)
    if formula:  # the token count both video-input tabs bill by
        own.extend(conditions(f"{source_id}:byteplus_formula", f"pricing formula: {formula[0]}", estimated_token_formula=formula[1]))
    return prices, own
