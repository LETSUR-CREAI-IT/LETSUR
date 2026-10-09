"""Fixed list of external signal sources.

Feeds are checked as a whole and filtered by model names; the OpenRouter endpoints
API is called once per registered LLM model. Add a source here only after checking
that it is reachable without login (docs/external-signals.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.market_collection.core import CollectionError, _validate_url
from scripts.market_collection.models import BROKER_CATALOG_MODELS, GOOGLE_BROKER_MODEL, GOOGLE_LLM_MODEL
from scripts.market_collection.sources import _targets

# channel_notice/supplier_notice: the company's own announcements.
# developer_blog/price_tracker: secondary sources, always unverified until checked.
# channel_api: a channel's own machine-readable pricing field.
SOURCE_KINDS = {"channel_notice", "supplier_notice", "developer_blog", "price_tracker", "channel_api"}
PARSERS = {"atom", "rss", "openrouter_endpoints"}


@dataclass(frozen=True)
class SignalSourceSpec:
    source_key: str
    source_kind: str
    url: str
    parser: str
    model_id: str = ""
    model_label: str = ""


FEEDS = [
    SignalSourceSpec("vercel_changelog", "channel_notice", "https://vercel.com/atom", "atom"),
    SignalSourceSpec("openai_news", "supplier_notice", "https://openai.com/news/rss.xml", "rss"),
    SignalSourceSpec("simonwillison_blog", "developer_blog", "https://simonwillison.net/atom/everything/", "atom"),
    SignalSourceSpec("modelpricewatch_news", "price_tracker", "https://modelpricewatch.com/news/feed.xml", "atom"),
]


def _openrouter_models() -> list[tuple[str, str, str]]:
    models = [(model, title, openrouter_id) for model, (title, _, _, _, openrouter_id) in _targets().items()]
    models.append((GOOGLE_LLM_MODEL, "Gemini 3.8 Flash", GOOGLE_BROKER_MODEL))
    models.extend((model, title, openrouter_id) for model, title, _, openrouter_id, _ in BROKER_CATALOG_MODELS)
    return models


def openrouter_specs() -> list[SignalSourceSpec]:
    return [SignalSourceSpec("openrouter_endpoints_" + re.sub(r"[^a-z0-9]+", "_", model), "channel_api",
                             f"https://openrouter.ai/api/v1/models/{openrouter_id}/endpoints", "openrouter_endpoints", model, title)
            for model, title, openrouter_id in _openrouter_models()]


def validate(specs: list[SignalSourceSpec]) -> list[SignalSourceSpec]:
    seen: set[str] = set()
    for spec in specs:
        if not isinstance(spec, SignalSourceSpec):
            raise CollectionError("signal registry contains a non-SignalSourceSpec entry")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_]*", spec.source_key) or spec.source_key in seen:
            raise CollectionError(f"invalid or duplicate signal source_key: {spec.source_key}")
        seen.add(spec.source_key)
        if spec.source_kind not in SOURCE_KINDS or spec.parser not in PARSERS:
            raise CollectionError(f"invalid signal source kind or parser: {spec.source_key}")
        if (spec.parser == "openrouter_endpoints") != bool(spec.model_id):
            raise CollectionError(f"model_id must be set only for API sources: {spec.source_key}")
        _validate_url(spec.url)
    return specs


def signal_sources() -> list[SignalSourceSpec]:
    return validate(FEEDS + openrouter_specs())
