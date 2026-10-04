"""Seedance 2.5 public text-to-video price evidence, by route."""

from __future__ import annotations

import html
import re

from scripts.market_collection.core import SourceSpec

MODEL = "seedance-2.5"
FAL_ID = "bytedance/seedance-2.5/text-to-video"
BYTEPLUS_ID = "dreamina-seedance-2-5-260628"


def source_specs() -> list[SourceSpec]:
    return [
        SourceSpec("fal_seedance_25_t2v", "video", MODEL, "fal", "text-to-video", "https://fal.ai/models/bytedance/seedance-2.5/text-to-video", "fal"),
        SourceSpec("byteplus_seedance_25_t2v", "video", MODEL, "BytePlus ModelArk", "text-to-video", "https://docs.byteplus.com/id/docs/modelark/model-pricing?redirect=1", "byteplus_pricing"),
        SourceSpec("byteplus_seedance_promotion_rules", "video", MODEL, "BytePlus ModelArk", "text-to-video", "https://docs.byteplus.com/en/docs/modelark/seedance-2-0-mini-fast-limited-time-discount?redirect=1", "byteplus_campaign"),
    ]


def _page(body: bytes) -> str:
    page = html.unescape(body.decode("utf-8", "replace"))
    # BytePlus embeds its document markdown in escaped page state. Decode only
    # character escapes needed for matching; the original bytes remain in raw.
    page = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), page)
    page = re.sub(r"\\+-", "-", page)
    return page.replace(r"\n", "\n").replace(r'\"', '"')


def _condition(group: str, key: str, value: str, locator: str, *, status: str = "stated", unit: str = "") -> dict:
    return dict(condition_set_id=group, condition_key=key, operator="=", value=value, unit=unit, status=status, locator=locator)


def _price(provider: str, kind: str, amount: str, unit: str, group: str, locator: str, *, status: str = "direct", reason: str = "") -> dict:
    return dict(provider_model_id=provider, model_version="2.5", upstream_provider="ByteDance", task="text-to-video", billing_item="generated_video", price_kind=kind, amount=amount, currency="USD" if amount else "", unit=unit if amount else "", normalized_amount="", normalized_unit="", condition_set_id=group, effective_from="", effective_until="", locator=locator, evidence_status=status, unknown_reason=reason)


def _fal(page: str, source_id: str) -> tuple[list[dict], list[dict]]:
    if FAL_ID not in page or "Text to Video" not in page:
        raise ValueError("fal Seedance 2.5 text-to-video model content missing")
    page = page.replace("**$", "$").replace("** per", " per").replace("** /", " /")
    prices: list[dict] = []
    conditions: list[dict] = []
    for resolution, pattern in (
        ("480p/720p", r"(?:charged|cost)\s+\$([0-9.]+)\s+per\s+1000\s+tokens\s+(?:at\s+both\s+480p\s+and\s+720p|for\s+480p\s+and\s+720p(?:\s+video)?)"),
        ("1080p", r"roughly\s+\$([0-9.]+)\s+per\s+1000\s+tokens\s+for\s+1080p"),
    ):
        match = re.search(pattern, page, re.I)
        if match:
            group = f"{source_id}:fal_token_{resolution.replace('/', '_')}"
            prices.append(_price(FAL_ID, "published_rate" if resolution != "1080p" else "provider_estimate", match[1], "1000 tokens", group, f"pricing: {match[0]}"))
            conditions.extend([_condition(group, "resolution", resolution, "pricing: token rate"), _condition(group, "input_video", "none", "endpoint: text-to-video")])
    if not any(price["price_kind"] == "published_rate" and price["unit"] == "1000 tokens" for price in prices):
        raise ValueError("fal Seedance 2.5 token rate or unit missing")
    for resolution in ("480p", "720p", "1080p"):
        match = re.search(rf"{resolution}[^\n]{{0,130}}?roughly\s+\$([0-9.]+)\s+per\s+second", page, re.I)
        if not match:
            match = re.search(rf"{resolution}\s+with\s+audio\s*\|\s*~\$([0-9.]+)\s*/\s*second", page, re.I)
        if match:
            group = f"{source_id}:fal_estimate_{resolution}"
            prices.append(_price(FAL_ID, "provider_estimate", match[1], "1 second", group, f"pricing: {match[0]}"))
            conditions.extend([_condition(group, "resolution", resolution, "pricing: per-second example"), _condition(group, "aspect_ratio", "16:9", "pricing: standard 16:9 output"), _condition(group, "input_video", "none", "endpoint: text-to-video")])
    if prices:
        group = f"{source_id}:fal_options"
        schema_rows: dict[str, str] = {}
        for key in ("resolution", "duration", "aspect_ratio", "generate_audio"):
            # fal's retained document is a Markdown table with a leading pipe
            # and quoted values inside backticks, not plain unquoted cells.
            match = re.search(r"^\s*\|\s*`" + key + r"`\s*\|[^\n]*", page, re.I | re.M)
            if match:
                schema_rows[key] = match[0].strip()
        required_values = {
            "resolution": ('"480p"', '"720p"'),
            "duration": ('"auto"', '"4"', '"30"'),
            "aspect_ratio": ('"auto"', '"21:9"', '"9:16"'),
            "generate_audio": ("true",),
        }
        for key, values in required_values.items():
            row = schema_rows.get(key, "")
            if row and all(value in row for value in values):
                condition_key = "resolution_schema" if key == "resolution" else key
                conditions.append(_condition(group, condition_key, row, f"fal input schema > Text to Video: {row}"))
        formula = re.search(r"tokens\s*=\s*\(output_height\s*\*\s*output_width\s*\*\s*duration_seconds\s*\*\s*24\)\s*/\s*1024", page, re.I)
        if formula:
            conditions.append(_condition(group, "token_formula", formula[0], f"fal pricing: {formula[0]}"))
        resolution_row = schema_rows.get("resolution", "")
        if any(c["condition_key"] == "resolution_schema" for c in conditions) and '"1080p"' not in resolution_row and any(c["condition_key"] == "resolution" and c["value"] == "1080p" for c in conditions):
            conditions.append(_condition(group, "1080p_availability", "pricing prose lists 1080p; Text to Video input-schema table lists only 480p/720p", f"fal pricing versus input schema: {resolution_row}", status="conflicted"))
    return prices, conditions


def _byteplus(page: str, source_id: str) -> tuple[list[dict], list[dict]]:
    # The unit is supplied by the table header, never guessed from cell values.
    row = re.search(r"\|" + re.escape(BYTEPLUS_ID) + r"\b([^\n]*)", page, re.I)
    headers = list(re.finditer(r"\|\*\*Model ID\*\*\s*\|\*\*Online inference\*\*[^\n]*?USD\s*/\s*M tokens", page[:row.start()] if row else "", re.I))
    if not row or not headers or row.start() - headers[-1].start() > 2500:
        raise ValueError("BytePlus Seedance 2.5 model row or USD/M token unit missing")
    prices: list[dict] = []
    conditions: list[dict] = []
    cells = row[1]
    for resolution, pattern in (
        ("480p/720p", r"For\s+480p\s+and\s+720p\s+outputs:.*?Input\s+without\s+video:\s*([0-9.]+)"),
        ("1080p", r"For\s+1080p\s+outputs:.*?Input\s+without\s+video:\s*([0-9.]+)"),
    ):
        match = re.search(pattern, cells, re.I | re.S)
        if match:
            group = f"{source_id}:byteplus_{resolution.replace('/', '_')}"
            prices.append(_price(BYTEPLUS_ID, "published_rate", match[1], "1M tokens", group, f"Video generation models > Pricing > {BYTEPLUS_ID}: {match[0]}"))
            conditions.extend([_condition(group, "resolution", resolution, "pricing table row"), _condition(group, "input_video", "none", "pricing table: Input without video"), _condition(group, "inference_mode", "online", "pricing table: Online inference")])
    if not prices:
        raise ValueError("BytePlus Seedance 2.5 input-without-video rate missing")
    if prices:
        group = f"{source_id}:byteplus_formula"
        formula = re.search(r"Estimated token consumption\s*=\s*`([^`]+)`", page, re.I)
        if formula:
            conditions.append(_condition(group, "estimated_token_formula", formula[1], f"pricing formula: {formula[0]}"))
        else:
            conditions.append(_condition(group, "estimated_token_formula", "unavailable in this response", "Video generation models > pricing formula", status="unknown"))
        actual = re.search(r"actual token consumption is subject to the\s*`?usage\.completion_tokens`?", page, re.I)
        if actual:
            conditions.append(_condition(group, "actual_usage", "usage.completion_tokens", f"pricing formula: {actual[0]}"))
        # The promotion text and linked rules name 2.0 mini/fast; neither proves
        # a 2.5 discount. Keep the unresolved applicability explicit.
        if "60% off" in page and "Seedance 2.0 mini" in page:
            conditions.append(_condition(group, "seedance_25_discount_eligibility", "unconfirmed: promotion names Seedance 2.0 mini/fast", "Video generation models > Limited-time discount", status="unknown"))
    return prices, conditions


def _campaign(page: str, source_id: str) -> tuple[list[dict], list[dict]]:
    if not re.search(r"Seedance\s+2\.0\s+mini", page, re.I) or not re.search(r"60%\s+off", page, re.I):
        raise ValueError("BytePlus Seedance campaign eligibility or discount rule missing")
    group = f"{source_id}:campaign_scope"
    conditions = [
        _condition(group, "named_eligible_models", "Seedance 2.0 mini; Seedance 2.0 fast", "campaign rules: eligible models"),
        _condition(group, "seedance_25_discount_eligibility", "unconfirmed: Seedance 2.5 is absent from eligible-model rules", "campaign rules: eligible models", status="conflicted"),
    ]
    return [_price(BYTEPLUS_ID, "disputed_claim", "", "", group, "campaign rules: Seedance 2.0 mini/fast eligibility", status="conflicted", reason="No confirmed Seedance 2.5 discount or applicable price")], conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    parsers = {"fal": _fal, "byteplus_pricing": _byteplus, "byteplus_campaign": _campaign}
    registered = {item.source_key: item for item in source_specs()}
    if spec != registered.get(spec.source_key) or spec.parser not in parsers:
        raise ValueError("unknown video source/parser")
    return parsers[spec.parser](_page(body), source_id)
