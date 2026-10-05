"""VIDEO compatibility entrypoints for the fixed collector registry."""
from __future__ import annotations

from scripts.market_collection.core import SourceSpec

def source_specs() -> list[SourceSpec]:
    from scripts.market_collection.sources import source_specs as configured_sources
    return [spec for spec in configured_sources() if spec.modality == "video"]

def parse_source(spec: SourceSpec, body: bytes, source_id: str) -> tuple[list[dict], list[dict]]:
    if not isinstance(spec, SourceSpec) or spec not in source_specs():
        raise ValueError("unknown video source/parser")
    from scripts.market_collection.collectors import parse_source as dispatch
    return dispatch(spec, body, source_id)
