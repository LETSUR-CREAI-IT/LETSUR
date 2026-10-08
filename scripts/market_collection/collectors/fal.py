"""Fal public price parsers."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser

from scripts.market_collection.core import ADAPTER_CONDITION_FIELDS, ADAPTER_PRICE_FIELDS, SourceSpec
from scripts.market_collection.models import SUNBURST_MODEL, SUNBURST_FAL_MODEL, FLARE_MODEL, FLARE_FAL_MODEL, SEEDANCE_FAL_ID
from .common import _number, _plain
from .video_common import flight_payloads


def _sun_price(spec: SourceSpec, label: str, amount: str, unit: str, locator: str,
           *, provider_model_id: str = "", condition_set_id: str = "",
           kind: str = "published_rate", status: str = "direct", unknown: str = "") -> dict[str, str]:
    return dict(provider_model_id=provider_model_id or SUNBURST_MODEL, model_version="", upstream_provider="OpenAI",
                task=spec.task, billing_item=label, price_kind=kind, amount=amount,
                currency="USD" if amount else "", unit=unit if amount else "",
                normalized_amount="", normalized_unit="", condition_set_id=condition_set_id,
                effective_from="", effective_until="", locator=locator,
                evidence_status=status, unknown_reason=unknown)


def _sun_condition(group: str, key: str, value: str, locator: str, *, status: str = "stated") -> dict[str, str]:
    return dict(condition_set_id=group, condition_key=key, operator="=", value=value,
                unit="", status=status, locator=locator)


_SUN_FAL_RATES = re.compile(
    r"Text tokens \(per 1M\):\s*\$([\d,.]+) input,\s*\$([\d,.]+) cached,\s*\$([\d,.]+) output\.\s*"
    r"Image tokens \(per 1M\):\s*\$([\d,.]+) input,\s*\$([\d,.]+) cached,\s*\$([\d,.]+) output",
    re.I,
)


def _fal_task_reference(body: str, model: str) -> list[dict]:
    """Keep the optional technical table separate from billable token rates."""
    path = "/models/" + model
    group = model.rsplit("/", 2)[1] + "_fal_task_reference"
    locator = f'contentData[path={path},slot=main,type=markdown]'
    decoder = json.JSONDecoder()
    records = []
    # Read original Flight strings before HTML/plain-text or quote unescaping.
    for payload in flight_payloads(body):
        for match in re.finditer(r'"contentData"\s*:\s*', payload):
            try:
                record, _ = decoder.raw_decode(payload, match.end())
            except ValueError:
                continue
            if (isinstance(record, dict) and record.get("path") == path
                    and record.get("slot") == "main" and record.get("type") == "markdown"):
                records.append(record)

    def unknown(reason: str, status: str = "unknown") -> list[dict]:
        return [_sun_condition(group, "reference_status", status, locator, status=status),
                _sun_condition(group, "reference_unknown_reason", reason, locator, status=status)]

    if not records:
        return unknown("exact main markdown contentData absent or undecodable")
    if len(records) != 1:
        return unknown("duplicate exact main markdown contentData; no record selected", "conflicted")
    record = records[0]
    try:
        content = record["content"]
        if not isinstance(content, str):
            raise ValueError("reference content is not markdown text")
        lines = content.splitlines()
        headers = [i for i, line in enumerate(lines) if re.match(r'^\s*\|\s*Size\s*\|', line)]
        if len(headers) != 1:
            raise ValueError("reference Size table absent or duplicate")
        start = headers[0]

        def columns(line: str) -> list[str]:
            if not line.strip().startswith("|") or not line.strip().endswith("|"):
                raise ValueError("reference row lacks pipe boundaries")
            return [cell.strip() for cell in line.strip()[1:-1].split("|")]

        qualities = columns(lines[start])[1:]
        if len(qualities) != 5 or any(not q for q in qualities) or len(set(qualities)) != 5:
            raise ValueError("reference requires five unique nonempty quality columns")
        separators = columns(lines[start + 1])
        if len(separators) != 6 or any(not re.fullmatch(r':?-{3,}:?', s) for s in separators):
            raise ValueError("reference separator width or syntax invalid")
        cells, sizes = [], []
        end = start + 2
        while end < len(lines) and lines[end].strip().startswith("|"):
            row = columns(lines[end])
            if len(row) != 6:
                raise ValueError("reference row width differs from header")
            size = row[0]
            dimensions = re.fullmatch(r'(\d+)×(\d+)', size)
            if not dimensions or size in sizes or any(int(n) <= 0 for n in dimensions.groups()):
                raise ValueError("reference size invalid or duplicate")
            sizes.append(size)
            for quality, raw in zip(qualities, row[1:]):
                if not re.fullmatch(r'\$\d+(?:\.\d+)?', raw):
                    raise ValueError("reference cell is not a nonnegative finite dollar decimal")
                numeric = raw[1:]
                number = Decimal(numeric)
                if not number.is_finite() or number < 0:
                    raise ValueError("reference cell must be finite and nonnegative")
                cells.append(dict(size=size, width=int(dimensions[1]), height=int(dimensions[2]),
                                  quality=quality, raw_amount=raw, numeric_text=numeric,
                                  locator=f'{locator}.content:line={end + 1};size={size};quality={quality};cell={raw}'))
            end += 1
        if len(sizes) != 6 or len(cells) != 30:
            raise ValueError("reference requires a complete six-size thirty-cell matrix")
        caption = "The following table shows the pricing of ChatGPT Images 2.5 from a technical standpoint."
        if caption not in lines[:start]:
            raise ValueError("technical reference caption absent")
        matrix = dict(currency_symbol="$", qualities=qualities, sizes=sizes, cells=cells)
    except (KeyError, IndexError, ValueError) as exc:
        return unknown(f"malformed reference: {exc}")
    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return [
        _sun_condition(group, "reference_status", "recorded", locator),
        _sun_condition(group, "reference_model", model, locator),
        _sun_condition(group, "reference_matrix", encode(matrix), locator),
        _sun_condition(group, "reference_content_metadata", encode({k: v for k, v in record.items() if k != "content"}), locator),
        _sun_condition(group, "reference_caption", caption, locator + ".content"),
        _sun_condition(group, "reference_context", content, locator + ".content"),
        _sun_condition(group, "reference_billing_scope", "table does not establish billing unit, quantity, token usage or fixed final bill", locator, status="unknown"),
    ]


def _sun_fal_task(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    if SUNBURST_FAL_MODEL not in body or "GPT Image 2.5 Sunburst" not in body:
        raise ValueError("fal exact task model absent")
    reference = _fal_task_reference(body, SUNBURST_FAL_MODEL)
    visible = _plain(body)
    match = _SUN_FAL_RATES.search(visible)
    if not match:
        raise ValueError("fal task scoped token rates or unit absent")
    labels = ("Text tokens / Input", "Text tokens / Cached input", "Text tokens / Output",
              "Image tokens / Input", "Image tokens / Cached input", "Image tokens / Output")
    locator = f"fal task Pricing | {match.group(0)}"
    group = "fal_task_token_rates"
    prices = [_sun_price(spec, label, _number(match.group(index)), "1M tokens", locator,
                     provider_model_id=SUNBURST_FAL_MODEL, condition_set_id=group,
                     kind="disputed_claim" if label == "Text tokens / Output" else "published_rate",
                     status="conflicted" if label == "Text tokens / Output" else "direct")
              for index, label in enumerate(labels, 1)]
    conditions = [_sun_condition(group, "task", "text-to-image", f"fal task identifier | {SUNBURST_FAL_MODEL}")]
    quality = re.search(r"Changing the quality parameter significantly affects cost; by default we use high", visible, re.I)
    if quality:
        conditions.append(_sun_condition(group, "quality_price_applicability", "unverified by token rate",
                                     f"fal task Pricing | {quality.group(0)}", status="unknown"))
    return prices, conditions + reference


def _fal_overview(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    visible = _plain(body)
    if "GPT Image 2.5 Sunburst" not in visible or SUNBURST_FAL_MODEL not in body:
        raise ValueError("fal overview Sunburst identity absent")
    claim = re.search(r"There is no charge for text output\.", visible, re.I)
    if not claim:
        raise ValueError("fal overview text output claim absent")
    group = "fal_overview_text_output"
    locator = f"fal overview FAQ | {claim.group(0)}"
    price = _sun_price(spec, "Text tokens / Output", "", "", locator,
                   provider_model_id=SUNBURST_FAL_MODEL, condition_set_id=group,
                   kind="disputed_claim", status="conflicted",
                   unknown="overview says no charge but specifies no unit; Sunburst task applicability unconfirmed")
    condition = _sun_condition(group, "sunburst_applicability", "unconfirmed", locator, status="unknown")
    return [price], [condition]


def _flare_price(spec: SourceSpec, label: str, amount: str, locator: str, *,
           provider_model_id: str = FLARE_MODEL, condition_set_id: str = "",
           kind: str = "published_rate", status: str = "direct",
           unknown: str = "") -> dict[str, str]:
    return dict(provider_model_id=provider_model_id, model_version="", upstream_provider="OpenAI",
                task=spec.task, billing_item=label, price_kind=kind, amount=amount,
                currency="USD" if amount else "", unit="1M tokens" if amount else "",
                normalized_amount="", normalized_unit="", condition_set_id=condition_set_id,
                effective_from="", effective_until="", locator=locator,
                evidence_status=status, unknown_reason=unknown)


def _flare_condition(group: str, key: str, value: str, locator: str,
               *, status: str = "stated") -> dict[str, str]:
    return dict(condition_set_id=group, condition_key=key, operator="=", value=value,
                unit="", status=status, locator=locator)


_FLARE_FAL_RATES = re.compile(
    r"Text tokens \(per 1M\):\s*\$([\d,.]+) input,\s*\$([\d,.]+) cached,\s*\$([\d,.]+) output\.\s*"
    r"Image tokens \(per 1M\):\s*\$([\d,.]+) input,\s*\$([\d,.]+) cached,\s*\$([\d,.]+) output", re.I)


def _flare_fal_task(spec: SourceSpec, body: str) -> tuple[list[dict], list[dict]]:
    if FLARE_FAL_MODEL not in body or "GPT Image 2.5 Flare" not in body:
        raise ValueError("fal exact Flare task model absent")
    reference = _fal_task_reference(body, FLARE_FAL_MODEL)
    decoded = body.replace('\\"', '"')
    scoped = re.search(r'"modelUrl":"https://fal.run/' + re.escape(FLARE_FAL_MODEL) +
                       r'".{0,700}?"pricingInfoOverride":"(.*?)","machineType"', decoded, re.S)
    if not scoped:
        raise ValueError("fal Flare task-scoped pricing block absent")
    pricing = scoped.group(1).replace("**", "").replace("\\\\n", " ").replace("\\n", " ")
    match = _FLARE_FAL_RATES.search(pricing)
    if not match:
        raise ValueError("fal Flare token rates or unit absent")
    labels = ("Text tokens / Input", "Text tokens / Cached input", "Text tokens / Output",
              "Image tokens / Input", "Image tokens / Cached input", "Image tokens / Output")
    group = "flare_fal_task_token_rates"
    prices = [_flare_price(spec, label, _number(match.group(index)), f"fal Flare task Pricing | {match.group(0)}",
                     provider_model_id=FLARE_FAL_MODEL, condition_set_id=group,
                     kind="disputed_claim" if label == "Text tokens / Output" else "published_rate",
                     status="conflicted" if label == "Text tokens / Output" else "direct")
              for index, label in enumerate(labels, 1)]
    conditions = [_flare_condition(group, "task", "text-to-image", f"fal task identifier | {FLARE_FAL_MODEL}")]
    quality = re.search(r"Changing the quality parameter significantly affects cost; by default we use high", pricing, re.I)
    if quality:
        conditions.append(_flare_condition(group, "quality_price_applicability", "unverified by token rate",
                                     f"fal Flare task Pricing | {quality.group(0)}", status="unknown"))
    return prices, conditions + reference


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


def _video_fal(page: str, source_id: str) -> tuple[list[dict], list[dict]]:
    if SEEDANCE_FAL_ID not in page or "Text to Video" not in page:
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
            prices.append(_video_price(SEEDANCE_FAL_ID, "published_rate" if resolution != "1080p" else "provider_estimate", match[1], "1000 tokens", group, f"pricing: {match[0]}"))
            conditions.extend([_video_condition(group, "resolution", resolution, "pricing: token rate"), _video_condition(group, "input_video", "none", "endpoint: text-to-video")])
    if not any(price["price_kind"] == "published_rate" and price["unit"] == "1000 tokens" for price in prices):
        raise ValueError("fal Seedance 2.5 token rate or unit missing")
    for resolution in ("480p", "720p", "1080p"):
        match = re.search(rf"{resolution}[^\n]{{0,130}}?roughly\s+\$([0-9.]+)\s+per\s+second", page, re.I)
        if not match:
            match = re.search(rf"{resolution}\s+with\s+audio\s*\|\s*~\$([0-9.]+)\s*/\s*second", page, re.I)
        if match:
            group = f"{source_id}:fal_estimate_{resolution}"
            prices.append(_video_price(SEEDANCE_FAL_ID, "provider_estimate", match[1], "1 second", group, f"pricing: {match[0]}"))
            conditions.extend([_video_condition(group, "resolution", resolution, "pricing: per-second example"), _video_condition(group, "aspect_ratio", "16:9", "pricing: standard 16:9 output"), _video_condition(group, "input_video", "none", "endpoint: text-to-video")])
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
                conditions.append(_video_condition(group, condition_key, row, f"fal input schema > Text to Video: {row}"))
        formula = re.search(r"tokens\s*=\s*\(output_height\s*\*\s*output_width\s*\*\s*duration_seconds\s*\*\s*24\)\s*/\s*1024", page, re.I)
        if formula:
            conditions.append(_video_condition(group, "token_formula", formula[0], f"fal pricing: {formula[0]}"))
        resolution_row = schema_rows.get("resolution", "")
        if any(c["condition_key"] == "resolution_schema" for c in conditions) and '"1080p"' not in resolution_row and any(c["condition_key"] == "resolution" and c["value"] == "1080p" for c in conditions):
            conditions.append(_video_condition(group, "1080p_availability", "pricing prose lists 1080p; Text to Video input-schema table lists only 480p/720p", f"fal pricing versus input schema: {resolution_row}", status="conflicted"))
    return prices, conditions


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    if spec.parser == "ideogram_fal":
        from . import ideogram_fal
        return ideogram_fal.parse_source(spec, body, source_id)
    if spec.modality == "image":
        decoded = body.decode("utf-8")
        return {"fal_task": _sun_fal_task, "fal_overview": _fal_overview,
                "flare_fal_task": _flare_fal_task}[spec.parser](spec, decoded)
    if spec.parser == "fal_veo":
        return _veo_metadata(spec, body.decode("utf-8"))
    return _video_fal(_page(body), source_id)


def _veo_metadata(spec, body):
    from .video_common import flight_payloads, objects_with_key, price, conditions
    variant = spec.model_id.split("-")[-1]
    endpoint = "fal-ai/veo3.1" + ("/" + variant if variant in ("fast", "lite") else "")
    roots = [obj for payload in flight_payloads(body) for obj in objects_with_key(payload, "modelData")
             if obj.get("endpointId") == endpoint and obj.get("modelData", {}).get("endpointId") == endpoint]
    if len(roots) != 1:
        raise ValueError("fal exact primary endpoint container absent or duplicate")
    model = roots[0].get("model", {})
    if model.get("modelUrl") != "https://fal.run/" + endpoint or model.get("id") != endpoint or "pricingInfoOverride" not in model or model.get("category") != "text-to-video" or model.get("status") != "public":
        raise ValueError("fal exact modelUrl pricing object absent")
    pricing = model["pricingInfoOverride"].replace("**", "")
    if variant == "lite":
        match = re.search(r'For every second of video you generate you will be charged \$(\d+(?:\.\d+)?) for 720p with audio, \$(\d+(?:\.\d+)?) for 720p without audio, \$(\d+(?:\.\d+)?) for 1080p with audio or \$(\d+(?:\.\d+)?) for 1080p without audio\.', pricing)
        rows = (("720p", "on", 1), ("720p", "off", 2), ("1080p", "on", 3), ("1080p", "off", 4))
    else:
        match = re.search(r'For every second of video you generate you will be charged \$(\d+(?:\.\d+)?) without audio or \$(\d+(?:\.\d+)?) with audio for 720p or 1080p\. At 4k, you will be charged \$(\d+(?:\.\d+)?) per second without audio, or \$(\d+(?:\.\d+)?) with\.', pricing)
        rows = (("720p", "off", 1), ("720p", "on", 2), ("1080p", "off", 1), ("1080p", "on", 2), ("4k", "off", 3), ("4k", "on", 4))
    if not match:
        raise ValueError("fal exact endpoint per-second audio/resolution quote absent")
    prices, own_conditions = [], []
    for resolution, audio, index in rows:
        group = f"{spec.source_key}_{resolution}_{audio}"
        locator = f"modelUrl=https://fal.run/{endpoint}; pricingInfoOverride: {match[0]}"
        prices.append(price(spec, endpoint, "Google", match[index], "1 second", group, locator))
        own_conditions.extend(conditions(group, locator, resolution=resolution, audio=audio, input_video="none"))
    return prices, own_conditions
