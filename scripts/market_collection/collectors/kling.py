"""Kling AI's own API price table (kling.ai/dev/pricing).

The page is rendered by script; the table is the configuration JSON the server embeds in the page, so the
plain HTTP response holds the same rows a reader sees.
"""

from __future__ import annotations

import json
import re

from scripts.market_collection.core import SourceSpec
from scripts.market_collection.models import KLING_PRICE_ROWS
from .video_common import conditions, price

PARSERS = ("kling_pricing",)
CONFIG = re.compile(r"window\['kConf_ytech\.klingWeb\.devModelPageInfo_18n'\]\s*=\s*")
# The video table's columns by key; any other layout is not read.
COLUMNS = {"model": "Model", "spec": "Billing Type", "function": "Features", "p720": "Price (720P)", "p1080": "Price (1080P)", "p4k": "Price (4K)"}
RESOLUTIONS = (("p720", "720p"), ("p1080", "1080p"), ("p4k", "4k"))
# The conditions each feature label states; a label not listed here fails closed rather than being guessed.
FEATURES = {
    "No Native Audio": {"audio": "off"},
    "With Native Audio x No Voice Control": {"audio": "on", "voice_control": "off"},
    "No Video Input x No Native Audio": {"input_video": "none", "audio": "off"},
    "No Video Input x With Native Audio": {"input_video": "none", "audio": "on"},
    "With Video Input x No Native Audio": {"input_video": "yes", "audio": "off"},
}
# A cell gives Kling's units and their list price in USD; the USD figure is the rate, the units stay in the locator.
CELL = re.compile(r"[0-9]+(?:\.[0-9]+)? Units? \(\$([0-9]+(?:\.[0-9]+)?)\) /s")
BASIS = re.compile(r"Video API Pricing 1 Unit = \$[0-9]+(?:\.[0-9]+)? \(list price\)")


def _video_table(body: bytes) -> dict:
    text = body.decode("utf-8")
    starts = [match.end() for match in CONFIG.finditer(text)]
    if len(starts) != 1:
        raise ValueError("Kling price configuration absent or duplicate")
    config, _ = json.JSONDecoder().raw_decode(text, starts[0])
    return config["en"]["newVideoApi"]["main"]


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    if spec.parser not in PARSERS:
        raise ValueError("unhandled Kling parser")
    name = KLING_PRICE_ROWS[spec.model_id]
    try:
        table = _video_table(body)
        basis, columns = table["subTitle"], {column["key"]: column["title"] for column in table["columns"]}
        rows = [row for entry in table["list"] for row in entry["table"]["data"] if row.get("model") == name]
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Kling video price table absent or reformatted") from exc
    if not isinstance(basis, str) or not BASIS.fullmatch(basis) or columns != COLUMNS:
        raise ValueError("Kling video price table basis or columns changed")
    if len(rows) != 1:
        raise ValueError(f"Kling price row absent or duplicate: {name}")
    row = rows[0]
    features = row.get("function")
    if (row.get("spec") != "Per second" or not isinstance(features, list) or not all(isinstance(feature, str) for feature in features)
            or len(set(features)) != len(features)):
        raise ValueError(f"Kling {name} billing type or feature list changed")
    if any(feature not in FEATURES for feature in features):
        raise ValueError(f"Kling {name} feature label not recognized")
    version = re.search(r"[0-9]+\.[0-9]+", name)[0]
    prices: list[dict] = []
    own: list[dict] = []
    for key, resolution in RESOLUTIONS:
        cells = row.get(key)
        if not isinstance(cells, list) or len(cells) != len(features):
            raise ValueError(f"Kling {name} {columns[key]} cells do not match its features")
        for feature, cell in zip(features, cells):
            if cell == "-":  # not offered at this resolution
                continue
            match = CELL.fullmatch(cell) if isinstance(cell, str) else None
            if not match:
                raise ValueError(f"Kling {name} {columns[key]} cell is not a per-second unit price")
            group = f"{source_id}:kling_{resolution}_{len(prices)}"
            locator = f"Video API Pricing ({basis}) > {name} > {feature} > {columns[key]}: {cell}"
            prices.append(price(spec, name, "Kling AI", match[1], "1 second", group, locator, version=version))
            own.extend(conditions(group, locator, resolution=resolution, **FEATURES[feature], price_basis="list price"))
    if not prices:
        raise ValueError(f"Kling {name} has no priced cell")
    return prices, own
