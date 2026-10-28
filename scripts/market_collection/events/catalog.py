"""Per-model source catalog built from the existing registries, never a copy.

A source has two separate axes:
  role       what kind of document it is (pricing, api, docs, notice, blog, tracker, social)
  authority  what its publisher may state: manufacturer_official (the model's own
             maker: model lifecycle and its own route), channel_official (a channel
             about its own route only), unofficial, or unregistered.
Authority comes from the registered publisher of the route or feed, never from a
caller flag, a manifest claim, or a domain alone. Nationality is never a factor.
"""

from __future__ import annotations

from dataclasses import asdict

from scripts.market_collection.core import CollectionError
from scripts.market_collection.models import MODEL_MODALITIES
from scripts.market_collection.signals.registry import SignalSourceSpec, signal_sources
from scripts.market_collection.sources import source_specs

# Publisher of each registered route. A route missing here is "unknown" and
# therefore never official.
ROUTE_PUBLISHERS = {
    "google_direct": "Google", "google_vertex": "Google Cloud", "openai_direct": "OpenAI", "openai": "OpenAI",
    "anthropic_direct": "Anthropic", "ideogram_direct": "Ideogram", "openrouter": "OpenRouter",
    "vercel_ai_gateway": "Vercel", "vercel": "Vercel", "fal": "fal", "aws_bedrock": "AWS",
    "azure_foundry": "Microsoft", "azure_databricks": "Databricks", "BytePlus ModelArk": "BytePlus",
}
# Model family prefix -> maker. Families, not model IDs, so a new model of a
# known family needs no edit here.
FAMILY_MAKERS = (("gemini", "Google"), ("veo", "Google"), ("gpt", "OpenAI"), ("claude", "Anthropic"),
                 ("ideogram", "Ideogram"), ("seedance", "ByteDance"), ("deepseek", "DeepSeek"),
                 ("glm", "Z.ai"), ("kimi", "Moonshot AI"),
                 # OpenRouter names the Grok upstream xAI although its model names say SpaceXAI.
                 ("grok", "xAI"), ("minimax", "MiniMax"), ("muse", "Meta"), ("qwen", "Alibaba"),
                 # OpenAI's o-series and embedding models, Kling and LETSUR's "byteplus-" Seedance ids (2026-10-28).
                 ("o1", "OpenAI"), ("o3", "OpenAI"), ("o4", "OpenAI"), ("text-embedding", "OpenAI"),
                 ("kling", "Kuaishou"), ("byteplus-seedance", "ByteDance"))
# Signal feeds: publisher and the route a channel notice speaks for.
SIGNAL_PUBLISHERS = {"vercel_changelog": ("Vercel", "vercel_ai_gateway"), "openai_news": ("OpenAI", "")}
# Channel API signal parsers: the route whose own endpoint listing they read.
API_ROUTES = {"openrouter_endpoints": "openrouter", "vercel_endpoints": "vercel_ai_gateway"}
SIGNAL_ROLES = {"channel_notice": "notice", "supplier_notice": "notice", "developer_blog": "blog",
                "price_tracker": "tracker", "channel_api": "api"}
# No collector exists for these; they are listed so a model shows the gap.
UNSUPPORTED_SOCIAL = ("social_x", "social_threads")


def maker(model_id: str) -> str:
    return next((name for prefix, name in FAMILY_MAKERS if model_id.startswith(prefix)), "unknown")


def source_role(parser: str) -> str:
    if parser.startswith("missing_"):
        return "catalog_listing"
    if any(token in parser for token in ("endpoints", "_api", "retail", "catalog")):
        return "api"
    if parser.endswith("campaign"):
        return "notice"
    if any(token in parser for token in ("guide", "_doc", "howto", "regions", "generate", "_ui")):
        return "docs"
    return "pricing"


def authority(model_id: str, route: str) -> str:
    publisher = ROUTE_PUBLISHERS.get(route, "unknown")
    if publisher == "unknown":
        return "unregistered"
    return "manufacturer_official" if publisher == maker(model_id) else "channel_official"


class Catalog:
    """Registered price and signal sources, with an optional disabled set."""

    def __init__(self, disabled: tuple[str, ...] = ()) -> None:
        self.specs = source_specs()
        self.signal_specs = signal_sources()
        self.by_key = {spec.source_key: spec for spec in self.specs}
        self.signal_by_key = {spec.source_key: spec for spec in self.signal_specs}
        unknown = set(disabled) - set(self.by_key) - set(self.signal_by_key)
        if unknown:
            raise CollectionError(f"unknown disabled source: {sorted(unknown)}")
        self.disabled = frozenset(disabled)

    def registered(self, spec: dict) -> bool:
        """True only when a manifest spec equals the registered SourceSpec field by field."""
        known = self.by_key.get(spec.get("source_key", ""))
        return known is not None and asdict(known) == spec

    def registered_signal(self, spec: dict) -> bool:
        known = self.signal_by_key.get(spec.get("source_key", ""))
        return known is not None and asdict(known) == spec

    def price_source(self, spec: dict) -> dict:
        if not self.registered(spec):
            return {"role": "unknown", "authority": "unregistered", "publisher": "unknown", "scope": []}
        level = authority(spec["model_id"], spec["route"])
        scope = ["route_price", "route_availability"] + (["model_lifecycle"] if level == "manufacturer_official" else [])
        return {"role": source_role(spec["parser"]), "authority": level,
                "publisher": ROUTE_PUBLISHERS.get(spec["route"], "unknown"), "scope": scope}

    def signal_source(self, spec: dict, model_id: str) -> dict:
        """Authority of a signal source with respect to one matched model."""
        if not self.registered_signal(spec):
            return {"role": "unknown", "authority": "unregistered", "publisher": "unknown", "route": "", "scope": []}
        role = SIGNAL_ROLES[spec["source_kind"]]
        if spec["source_kind"] == "channel_api":
            route = API_ROUTES[spec["parser"]]
            return {"role": role, "authority": "channel_official", "publisher": ROUTE_PUBLISHERS[route],
                    "route": route, "scope": ["route_price", "route_availability"]}
        publisher, route = SIGNAL_PUBLISHERS.get(spec["source_key"], ("unknown", ""))
        if spec["source_kind"] in ("developer_blog", "price_tracker") or publisher == "unknown":
            return {"role": role, "authority": "unofficial", "publisher": publisher, "route": "", "scope": []}
        if publisher == maker(model_id):
            return {"role": role, "authority": "manufacturer_official", "publisher": publisher, "route": "",
                    "scope": ["model_lifecycle", "route_price", "route_availability"]}
        if route:
            return {"role": role, "authority": "channel_official", "publisher": publisher, "route": route,
                    "scope": ["route_price", "route_availability"]}
        return {"role": role, "authority": "unofficial", "publisher": publisher, "route": "", "scope": []}

    def as_json(self) -> dict:
        models: dict[str, list] = {}
        for spec in self.specs:
            info = self.price_source(asdict(spec))
            models.setdefault(spec.model_id, []).append({
                "source_key": spec.source_key, "route": spec.route, "role": info["role"], "authority": info["authority"],
                "publisher": info["publisher"], "active": spec.source_key not in self.disabled, "status": "registered",
                "task": spec.task, "url": spec.url, "parser": spec.parser, "registry": "SourceSpec"})
        for spec in self.signal_specs:
            targets = [spec.model_id] if spec.model_id else []
            for model_id in targets:
                info = self.signal_source(asdict(spec), model_id)
                models.setdefault(model_id, []).append({
                    "source_key": spec.source_key, "route": info["route"], "role": info["role"], "authority": info["authority"],
                    "publisher": info["publisher"], "active": spec.source_key not in self.disabled, "status": "registered",
                    "task": "", "url": spec.url, "parser": spec.parser, "registry": "SignalSourceSpec"})
        feeds = [{"source_key": spec.source_key, "role": SIGNAL_ROLES[spec.source_kind], "source_kind": spec.source_kind,
                  "active": spec.source_key not in self.disabled, "url": spec.url, "parser": spec.parser,
                  "note": "feed matched to models by name; authority is decided per matched model"}
                 for spec in self.signal_specs if not spec.model_id]
        return {"schema_version": 1,
                "models": [{"model_id": model_id, "modality": MODEL_MODALITIES[model_id], "maker": maker(model_id),
                            "sources": sources,
                            "unsupported": [{"role": role, "status": "unsupported",
                                             "note": "no collector; manual evidence only, never auto-confirmed"}
                                            for role in UNSUPPORTED_SOCIAL]}
                           for model_id, sources in models.items()],
                "feeds": feeds, "disabled": sorted(self.disabled)}


def signal_spec(spec: dict) -> SignalSourceSpec:
    return SignalSourceSpec(**spec)
