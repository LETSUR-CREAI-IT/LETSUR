"""Fixed, explicit supplier dispatch with registered source identity checks."""
from __future__ import annotations

from scripts.market_collection.core import SourceSpec
from scripts.market_collection.sources import source_specs
from . import google, openai, anthropic, openrouter, vercel, aws_bedrock, azure_foundry, fal, byteplus, google_vertex, azure_databricks, image25_api, image25_supplement, azure_retail_llm, azure_retail_models

SUPPLIERS = {
    "google_direct": google, "openai_direct": openai, "openai": openai,
    "anthropic_direct": anthropic, "openrouter": openrouter,
    "vercel_ai_gateway": vercel, "vercel": vercel,
    "aws_bedrock": aws_bedrock, "azure_foundry": azure_foundry,
    "google_vertex": google_vertex, "azure_databricks": azure_databricks,
    "fal": fal, "BytePlus ModelArk": byteplus,
}

def parser_module(spec: SourceSpec):
    """The collector module that parses this spec's raw body."""
    if spec.parser in image25_supplement.PARSERS:
        return image25_supplement
    if spec.parser in image25_api.PARSERS:
        return image25_api
    if spec.parser in azure_retail_llm.PARSERS:  # its own module, so the other Azure parsers keep their fingerprints
        return azure_retail_llm
    if spec.parser in azure_retail_models.PARSERS:  # likewise its own module
        return azure_retail_models
    module = SUPPLIERS.get(spec.route)
    if module is None:
        raise ValueError("unhandled source route")
    return module

def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    registered = {item.source_key: item for item in source_specs()}
    if not isinstance(spec, SourceSpec) or registered.get(spec.source_key) != spec:
        raise ValueError("source spec differs from fixed registry")
    if not isinstance(body, bytes):
        raise TypeError("source body must be bytes")
    if spec.modality == "image" and not source_id:
        raise ValueError("unregistered image source or invalid body/source ID")
    return parser_module(spec).parse_source(spec, body, source_id)
