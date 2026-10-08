"""Fixed, explicit supplier dispatch with registered source identity checks."""
from __future__ import annotations

from scripts.market_collection.core import SourceSpec
from scripts.market_collection.sources import source_specs
from . import google, openai, anthropic, openrouter, vercel, aws_bedrock, azure_foundry, fal, byteplus, google_vertex, azure_databricks, ideogram, image25_api, image25_supplement

SUPPLIERS = {
    "google_direct": google, "openai_direct": openai, "openai": openai,
    "anthropic_direct": anthropic, "openrouter": openrouter,
    "vercel_ai_gateway": vercel, "vercel": vercel,
    "aws_bedrock": aws_bedrock, "azure_foundry": azure_foundry,
    "google_vertex": google_vertex, "azure_databricks": azure_databricks,
    "ideogram_direct": ideogram, "fal": fal, "BytePlus ModelArk": byteplus,
}

def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    registered = {item.source_key: item for item in source_specs()}
    if not isinstance(spec, SourceSpec) or registered.get(spec.source_key) != spec:
        raise ValueError("source spec differs from fixed registry")
    if not isinstance(body, bytes):
        raise TypeError("source body must be bytes")
    if spec.modality == "image" and not source_id:
        raise ValueError("unregistered image source or invalid body/source ID")
    if spec.parser in image25_supplement.PARSERS:
        return image25_supplement.parse_source(spec, body, source_id)
    if spec.parser in image25_api.PARSERS:
        return image25_api.parse_source(spec, body, source_id)
    module = SUPPLIERS.get(spec.route)
    if module is None:
        raise ValueError("unhandled source route")
    return module.parse_source(spec, body, source_id)
