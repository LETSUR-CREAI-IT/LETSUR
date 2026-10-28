"""Vercel AI Gateway's public endpoint listing of a video model (ai-gateway.vercel.sh/v1/models/<id>/endpoints).

Each endpoint's video price list is read as published. A price field this parser does not know fails closed, so a
new pricing dimension is never folded into a neighbouring row.
"""

from __future__ import annotations

import json
import re

from scripts.market_collection.core import SourceSpec
from .video_common import conditions, price

PARSERS = ("vercel_video_endpoints",)
URL = re.compile(r"https://ai-gateway\.vercel\.sh/v1/models/(.+)/endpoints")
UPSTREAMS = {"klingai": "Kling AI", "bytedance": "ByteDance"}
MODES = {"std": "standard", "pro": "pro"}
SWITCHES = {True: "on", False: "off"}
DURATION_FIELDS = {"mode", "resolution", "audio", "voice_control", "cost_per_second"}
PRICE_FIELDS = {"prompt", "completion", "request", "image", "image_output", "web_search", "internal_reasoning", "discount",
                "video_duration_pricing", "video_token_pricing"}


def _unique(pairs: list[tuple[str, object]]) -> dict:
    found: dict = {}
    for key, value in pairs:
        if key in found:
            raise ValueError("duplicate endpoint JSON field: " + key)
        found[key] = value
    return found


def _duration_rows(items: object) -> list[tuple[str, str, dict, str]]:
    rows = []
    for number, item in enumerate(items if isinstance(items, list) else [None]):
        if not isinstance(item, dict) or not set(item) <= DURATION_FIELDS or "cost_per_second" not in item:
            raise ValueError("Vercel per-second price entry has unknown or missing fields")
        conds = {}
        if "mode" in item:
            if item["mode"] not in MODES:
                raise ValueError("Vercel video mode not recognized")
            conds["mode"] = MODES[item["mode"]]
        if "resolution" in item:
            conds["resolution"] = str(item["resolution"])
        for field in ("audio", "voice_control"):
            if field in item:
                if not isinstance(item[field], bool):
                    raise ValueError(f"Vercel {field} flag is not a boolean")
                conds[field] = SWITCHES[item[field]]
        rows.append((item["cost_per_second"], "1 second", conds, f"video_duration_pricing[{number}]"))
    return rows


def _token_rows(pricing: object) -> list[tuple[str, str, dict, str]]:
    if not isinstance(pricing, dict) or not set(pricing) <= {"tiers", "notes"} or not isinstance(pricing.get("tiers"), list):
        raise ValueError("Vercel token pricing has unknown or missing fields")
    rows = []
    for number, tier in enumerate(pricing["tiers"]):
        if not isinstance(tier, dict) or not set(tier) <= {"resolution", "no_video_input", "with_video_input"} or "resolution" not in tier:
            raise ValueError("Vercel token tier has unknown or missing fields")
        for field, video in (("no_video_input", "none"), ("with_video_input", "yes")):
            if field in tier:
                rate = tier[field]
                if not isinstance(rate, dict) or set(rate) != {"cost_per_million_tokens"}:
                    raise ValueError("Vercel token rate has unknown or missing fields")
                rows.append((rate["cost_per_million_tokens"], "1M tokens", {"resolution": str(tier["resolution"]), "input_video": video},
                             f"video_token_pricing.tiers[{number}].{field}"))
    return rows


def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    url = URL.fullmatch(spec.url)
    if spec.parser not in PARSERS or not url:
        raise ValueError("unhandled Vercel video endpoint source")
    model = url[1]
    try:
        data = json.loads(body.decode("utf-8"), parse_float=str, object_pairs_hook=_unique)["data"]
        identity, operations, endpoints = data["id"], data["capabilities"]["supported_operations"], data["endpoints"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Vercel endpoint listing absent or reformatted") from exc
    if identity != model or not isinstance(endpoints, list) or not endpoints:
        raise ValueError("Vercel endpoint listing is not for the registered model")
    if not isinstance(operations, list) or spec.task not in operations:
        raise ValueError(f"Vercel {model} does not list the {spec.task} operation")
    version = re.search(r"[0-9]+\.[0-9]+", model)[0]
    prices: list[dict] = []
    own: list[dict] = []
    providers: set[str] = set()
    for index, endpoint in enumerate(endpoints):
        where = f"endpoints[{index}]"
        provider, pricing = (endpoint.get("provider_name"), endpoint.get("pricing")) if isinstance(endpoint, dict) else (None, None)
        if provider not in UPSTREAMS or provider in providers or not isinstance(pricing, dict) or not set(pricing) <= PRICE_FIELDS:
            raise ValueError("Vercel endpoint provider or price fields not recognized")
        providers.add(provider)
        if pricing.get("discount") not in (None, 0, "0"):
            raise ValueError("Vercel endpoint discount is not read by this parser")
        rows = (_duration_rows(pricing["video_duration_pricing"]) if "video_duration_pricing" in pricing else []) + \
               (_token_rows(pricing["video_token_pricing"]) if "video_token_pricing" in pricing else [])
        keys = [(unit, tuple(sorted(conds.items()))) for _, unit, conds, _ in rows]
        if not rows or len(set(keys)) != len(keys):
            raise ValueError("Vercel endpoint video prices absent or repeated for one condition")
        for number, (amount, unit, conds, pointer) in enumerate(rows):
            group = f"{source_id}:vercel_{provider}_{number}"
            locator = f"{where}.pricing.{pointer}={amount}"
            prices.append(price(spec, model, UPSTREAMS[provider], amount, unit, group, locator, version=version))
            own.extend(conditions(group, locator, endpoint_provider=provider, operation=spec.task, **conds, price_basis="endpoint_listed_rate"))
        notes = pricing.get("video_token_pricing", {}).get("notes")
        if isinstance(notes, str) and notes:
            own.extend(conditions(f"{source_id}:vercel_{provider}_terms", f"{where}.pricing.video_token_pricing.notes", billing_notes=notes))
    return prices, own
