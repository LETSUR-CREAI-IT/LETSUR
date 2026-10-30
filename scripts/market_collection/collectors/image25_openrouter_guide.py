"""OpenRouter's image generation guide for the GPT Image 2.5 supplement docs, in a module of its own so the other five
parsers of image25_supplement.py keep their fingerprints: collectors/__init__.py sends image25_openrouter_guide here
first, and image25_supplement._openrouter is no longer reached.

On 2026-10-30 OpenRouter reworded the size rules: explicit pixel sizes became "provider-dependent", and providers that
accept them "treat the value as authoritative and reject a mismatched resolution or aspect_ratio alongside it with a 400"
(xAI and Sourceful V2/V2.5 reject them outright). That is what size_alias_semantics records, so the earlier wording and
this one give the same rows; any other wording raises."""

from __future__ import annotations

from .image25_supplement import _HTML, _one, _section, condition

PARSERS = frozenset({"image25_openrouter_guide"})
SIZE_WORDINGS = (  # every phrase of one wording must be in the Resolution and Aspect Ratio section
    ("size : convenience shorthand.", "A tier size is equivalent to setting resolution", "An explicit pixel size is authoritative.",
     "rejected with a 400.", "supported_parameters"),  # until 2026-10-29
    ("size : convenience shorthand.", "A tier size is equivalent to setting resolution", "Explicit pixel sizes are provider-dependent",
     "treat the value as authoritative", "with a 400.", "supported_parameters"),  # from 2026-10-30
)


def parse_source(spec, body, source_id):
    rows = []
    group = "image25_doc_" + spec.source_key

    def add(key, value, locator, status="stated"):
        rows.append(condition(group, key, value, locator, status))
    add("model_id", spec.model_id, "document registry exact target; profile_scope governs applicability")
    root = _HTML(body.decode("utf-8")).root
    main = _one((n for n in root.nodes("main") if n.attrs.get("id") == "content-container"), "OpenRouter guide main")
    text = _section(main, "Resolution and Aspect Ratio")
    if not any(all(phrase in text for phrase in wording) for wording in SIZE_WORDINGS):
        raise ValueError("OpenRouter generic size alias contract changed")
    absent = _one((n for n in main.nodes("span") if n.attrs.get("data-as") == "p" and n.text() == "An absent key means the parameter is unsupported by that endpoint."), "endpoint capability explanation")
    add("profile_scope", "generic_api_reference", "Image generation guide > Resolution and Aspect Ratio")
    add("evidence_kind", "generic_api_contract", "Image generation guide")
    add("size_alias_semantics", dict(tier_alias="resolution", explicit_pixel_size="authoritative when supported", conflicting_resolution_or_aspect_ratio="HTTP400"), "Resolution and Aspect Ratio: " + text)
    add("endpoint_capability_absent_key", absent.text(), "Endpoint capability descriptors: " + absent.text())
    add("exact_pixel_support", "Requires the exact endpoint capability; generic aliases do not establish supported pixel dimensions", "Resolution and Aspect Ratio", "unknown")
    return [], rows
