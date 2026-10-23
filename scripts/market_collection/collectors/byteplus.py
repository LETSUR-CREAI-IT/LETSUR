"""Byteplus public price parsers."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec
from scripts.market_collection.models import BYTEPLUS_ID


def is_unrendered_template(body: bytes) -> bool:
    """Recognize the empty SSR template retained in failed public responses."""
    return bool(re.search(rb'<div\s+id=["\']root["\']\s*>\s*<!--<\?- html \?>-->\s*</div>', body)) and b"<!--<?- SSRDataScript ?>-->" in body


def _page(body: bytes) -> str:
    page = html.unescape(body.decode("utf-8", "replace"))
    # BytePlus embeds its document markdown in escaped page state. Decode only
    # character escapes needed for matching; the original bytes remain in raw.
    page = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), page)
    page = re.sub(r"\\+-", "-", page)
    return page.replace(r"\n", "\n").replace(r'\"', '"')


def _video_condition(group: str, key: str, value: str, locator: str, *, status: str = "stated", unit: str = "") -> dict:
    return dict(condition_set_id=group, condition_key=key, operator="=", value=value, unit=unit, status=status, locator=locator)


def _video_price(provider: str, kind: str, amount: str, unit: str, group: str, locator: str, *, status: str = "direct", reason: str = "") -> dict:
    return dict(provider_model_id=provider, model_version="2.5", upstream_provider="ByteDance", task="text-to-video", billing_item="generated_video", price_kind=kind, amount=amount, currency="USD" if amount else "", unit=unit if amount else "", normalized_amount="", normalized_unit="", condition_set_id=group, effective_from="", effective_until="", locator=locator, evidence_status=status, unknown_reason=reason)


MODEL_ROW_OR_UNIT_MISSING = "BytePlus Seedance 2.5 model row or USD/M token unit missing"


def _zones(body: bytes) -> dict:
    """The pricing document as rich-text zones; the page ships it as JSON inside its router state, not as markdown."""
    text = body.decode("utf-8", "replace")
    marker = re.search(r"window\._ROUTER_DATA\s*=\s*", text)
    if not marker:
        raise ValueError(MODEL_ROW_OR_UNIT_MISSING)
    state, _ = json.JSONDecoder().raw_decode(text, marker.end())
    documents = [page["curDoc"] for page in state["loaderData"].values() if isinstance(page, dict) and "curDoc" in page]
    return json.loads(documents[0]["Content"])["data"]


def _zone_text(zone: dict) -> str:
    return "".join(op["insert"] for op in zone["ops"] if isinstance(op.get("insert"), str) and op["insert"] != "*")


def _ids(zone: dict) -> list[str]:
    return [op["insert"]["id"] for op in zone["ops"] if isinstance(op.get("insert"), dict)]


def _table(zones: dict, host: str) -> list[list[str]]:
    """Cell text of the table a zone hosts: aceTable names its row and column id zones, a cell is the zone x<row>x<col>."""
    spec = next((op["attributes"]["aceTable"] for op in zones[host]["ops"] if "aceTable" in op.get("attributes", {})), "")
    if not spec:
        return []
    row_zone, col_zone = spec.split()
    return [[_zone_text(zones[f"x{row}x{col}"]).strip() for col in _ids(zones[col_zone])] for row in _ids(zones[row_zone])]


def _without_video_row(zones: dict) -> tuple[list[str], list[str], list[str]]:
    """The two header rows and the Seedance 2.5 row of the pricing table in the "Input without video" tab."""
    for zone in zones.values():
        for tab in zone["ops"]:
            attributes = tab.get("attributes", {})
            if attributes.get("tabContent") and attributes.get("title", "").strip().lower() == "input without video":
                grid = _table(zones, attributes["zoneId"])
                for row in grid[2:]:
                    if row[0].lower() == BYTEPLUS_ID:
                        return grid[0], grid[1], row
    raise ValueError(MODEL_ROW_OR_UNIT_MISSING)


def _online_rates(groups: list[str], labels: list[str], row: list[str]) -> dict[str, str]:
    """Online-inference cells of a row by resolution label; a merged group header is blank over the columns it spans."""
    rates, group = {}, ""
    for top, label, cell in zip(groups, labels, row):
        group = top or group
        if group.lower() == "online inference":
            rates[label.lower()] = cell
    return rates


def _video_byteplus(body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    try:
        zones = _zones(body)
        groups, labels, row = _without_video_row(zones)
        page = _zone_text(zones["0"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(MODEL_ROW_OR_UNIT_MISSING) from exc
    # The unit is the page's own pricing note, never guessed from cell values.
    if not re.search(r"Token\s+prices\s+are\s+USD\s+per\s+million\s+tokens", page, re.I):
        raise ValueError(MODEL_ROW_OR_UNIT_MISSING)
    prices: list[dict] = []
    conditions: list[dict] = []
    rates = _online_rates(groups, labels, row)
    for label, resolution in (("480p and 720p", "480p/720p"), ("1080p", "1080p")):
        match = re.fullmatch(r"\$\s*([0-9]+(?:\.[0-9]+)?)", rates.get(label, ""))
        if match:
            group = f"{source_id}:byteplus_{resolution.replace('/', '_')}"
            prices.append(_video_price(BYTEPLUS_ID, "published_rate", match[1], "1M tokens", group, f"Video generation models > Pricing > Input without video > {BYTEPLUS_ID} > Online inference > {label}: {match[0]}"))
            conditions.extend([_video_condition(group, "resolution", resolution, f"pricing table column: {label}"), _video_condition(group, "input_video", "none", "pricing table: Input without video"), _video_condition(group, "inference_mode", "online", "pricing table: Online inference")])
    if not prices:
        raise ValueError("BytePlus Seedance 2.5 input-without-video rate missing")
    if prices:
        group = f"{source_id}:byteplus_formula"
        formula = re.search(r"Estimated token consumption:\s*([^\n]*\S)", page, re.I)
        if formula:
            conditions.append(_video_condition(group, "estimated_token_formula", formula[1], f"pricing formula: {formula[0]}"))
        else:
            conditions.append(_video_condition(group, "estimated_token_formula", "unavailable in this response", "Video generation models > pricing formula", status="unknown"))
        actual = re.search(r"Actual consumption is determined by\s+usage\.completion_tokens", page, re.I)
        if actual:
            conditions.append(_video_condition(group, "actual_usage", "usage.completion_tokens", f"pricing formula: {actual[0]}"))
        # The promotion text and linked rules name 2.0 mini/fast; neither proves
        # a 2.5 discount. Keep the unresolved applicability explicit.
        if "60% off" in page and "Seedance 2.0 mini" in page:
            conditions.append(_video_condition(group, "seedance_25_discount_eligibility", "unconfirmed: promotion names Seedance 2.0 mini/fast", "Video generation models > Limited-time discount", status="unknown"))
    return prices, conditions


def _video_campaign(page: str, source_id: str) -> tuple[list[dict], list[dict]]:
    if not re.search(r"Seedance\s+2\.0\s+mini", page, re.I) or not re.search(r"60%\s+off", page, re.I):
        raise ValueError("BytePlus Seedance campaign eligibility or discount rule missing")
    group = f"{source_id}:campaign_scope"
    conditions = [
        _video_condition(group, "named_eligible_models", "Seedance 2.0 mini; Seedance 2.0 fast", "campaign rules: eligible models"),
        _video_condition(group, "seedance_25_discount_eligibility", "unconfirmed: Seedance 2.5 is absent from eligible-model rules", "campaign rules: eligible models", status="conflicted"),
    ]
    return [_video_price(BYTEPLUS_ID, "disputed_claim", "", "", group, "campaign rules: Seedance 2.0 mini/fast eligibility", status="conflicted", reason="No confirmed Seedance 2.5 discount or applicable price")], conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    if is_unrendered_template(body):
        raise ValueError("BytePlus unrendered SSR template: document body unavailable")
    if spec.parser == "byteplus_pricing":
        return _video_byteplus(body, source_id)  # reads the page's own JSON, which _page's unescaping would corrupt
    return {"byteplus_campaign": _video_campaign}[spec.parser](_page(body), source_id)
