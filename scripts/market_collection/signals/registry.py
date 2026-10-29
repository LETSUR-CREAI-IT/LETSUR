"""Fixed list of external signal sources.

Feeds are checked as a whole and filtered by model names; the OpenRouter and Vercel
endpoints APIs are called once per registered LLM model the gateway lists; the retirement
schedule pages are read whole and matched by exact model id. Add a source here only after
checking that it is reachable without login (docs/external-signals.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.market_collection.core import CollectionError, _validate_url
from scripts.market_collection.models import BROKER_API_MODELS, BROKER_CATALOG_MODELS, GATEWAY_EMBEDDING_MODELS, GATEWAY_IMAGE_MODELS, GATEWAY_MORE_MODELS, GOOGLE_BROKER_MODEL, GOOGLE_LLM_MODEL
from scripts.market_collection.sources import _targets

from .retirements import READERS

# channel_notice/supplier_notice: the company's own announcements.
# developer_blog/price_tracker: secondary sources, always unverified until checked.
# channel_api: a channel's own machine-readable pricing field.
SOURCE_KINDS = {"channel_notice", "supplier_notice", "developer_blog", "price_tracker", "channel_api"}
API_PARSERS = {"openrouter_endpoints", "vercel_endpoints"}  # one call per model: model_id is required
RETIREMENT_PARSERS = set(READERS)
PARSERS = {"atom", "rss", *API_PARSERS, *RETIREMENT_PARSERS}


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

# The route owners' own retirement schedules, in their Markdown form (2026-10-29): Azure is a channel for every model it
# lists, the other three are the makers' own pages. Listed after the gateway APIs so the earlier sources keep their order.
RETIREMENT_PAGES = [
    SignalSourceSpec("azure_retirement_schedule", "channel_notice",
                     "https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule?accept=text/markdown",
                     "azure_retirements"),
    SignalSourceSpec("openai_deprecations", "supplier_notice", "https://developers.openai.com/api/docs/deprecations.md", "openai_deprecations"),
    SignalSourceSpec("anthropic_deprecations", "supplier_notice", "https://platform.claude.com/docs/en/about-claude/model-deprecations.md",
                     "anthropic_deprecations"),
    SignalSourceSpec("google_deprecations", "supplier_notice", "https://ai.google.dev/gemini-api/docs/deprecations.md.txt", "google_deprecations"),
]


def _gateway_models() -> list[tuple[str, str, str | None, str | None]]:
    """(model, title, OpenRouter id, Vercel id) by the id rule of sources.llm_endpoint_source_specs; None: not listed there."""
    models = [(model, title, gateway_id, gateway_id) for model, (title, _, _, _, gateway_id) in _targets().items()]
    models.append((GOOGLE_LLM_MODEL, "Gemini 3.8 Flash", GOOGLE_BROKER_MODEL, GOOGLE_BROKER_MODEL))
    models.extend((model, title, openrouter_id, vercel_id) for model, title, _, openrouter_id, vercel_id in BROKER_CATALOG_MODELS)
    models.extend(BROKER_API_MODELS + GATEWAY_MORE_MODELS + GATEWAY_EMBEDDING_MODELS + GATEWAY_IMAGE_MODELS)
    return models


def openrouter_specs() -> list[SignalSourceSpec]:
    return [SignalSourceSpec("openrouter_endpoints_" + re.sub(r"[^a-z0-9]+", "_", model), "channel_api",
                             f"https://openrouter.ai/api/v1/models/{openrouter_id}/endpoints", "openrouter_endpoints", model, title)
            for model, title, openrouter_id, _ in _gateway_models() if openrouter_id]


def vercel_specs() -> list[SignalSourceSpec]:
    """Vercel AI Gateway endpoint listings, read for an endpoint's deprecated_at (2026-10-28)."""
    return [SignalSourceSpec("vercel_endpoints_" + re.sub(r"[^a-z0-9]+", "_", model), "channel_api",
                             f"https://ai-gateway.vercel.sh/v1/models/{vercel_id}/endpoints", "vercel_endpoints", model, title)
            for model, title, _, vercel_id in _gateway_models() if vercel_id]


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
        if (spec.parser in API_PARSERS) != bool(spec.model_id):
            raise CollectionError(f"model_id must be set only for API sources: {spec.source_key}")
        _validate_url(spec.url)
    return specs


def signal_sources() -> list[SignalSourceSpec]:
    return validate(FEEDS + openrouter_specs() + vercel_specs() + RETIREMENT_PAGES)
