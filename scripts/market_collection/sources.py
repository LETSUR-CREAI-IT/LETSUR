"""Explicit, ordered model by route source configuration."""

from __future__ import annotations

from scripts.market_collection.core import SourceSpec, CollectionError
from scripts.market_collection.models import (GOOGLE_LLM_MODEL, ASTRA_MODEL, FABLE_MODEL, STANDARD_LLM_MODELS, BROKER_CATALOG_MODELS, _AWS_OPENAI, _AWS_ANTHROPIC, _AZURE_MODELS, SUNBURST_MODEL, FLARE_MODEL, SEEDANCE_MODEL, MODEL_MODALITIES)

def _targets() -> dict[str, tuple[str, str, str, str, str]]:
    targets = {
        ASTRA_MODEL: ("GPT-6 Astra", "openai", "gpt-6-astra", "GPT-6 Astra", "openai/gpt-6-astra"),
        FABLE_MODEL: ("Claude Fable 5.1", "anthropic", "claude-fable-5.1", "Claude Fable 5.1", "anthropic/claude-fable-5.1"),
    }
    for model, title, slug in STANDARD_LLM_MODELS:
        provider = "openai" if model.startswith("gpt-") else "anthropic"
        vercel_title = title.replace("GPT-5.6", "GPT 5.6")
        targets[model] = (title, provider, slug, vercel_title, provider + "/" + slug)
    return targets


def _key(model: str) -> str:
    return model.replace("-", "_").replace(".", "_")


_AWS_BASE = "https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-"


_AZURE_BASE = "https://ai.azure.com/catalog/models/"


_BEDROCK_PRICING = "https://aws.amazon.com/bedrock/pricing/"


def llm_source_specs() -> list[SourceSpec]:
    specs = [
        SourceSpec("google_gemini_38_flash", "llm", GOOGLE_LLM_MODEL, "google_direct", "text_generation", "https://ai.google.dev/gemini-api/docs/pricing", "google"),
        SourceSpec("openrouter_gemini_38_flash", "llm", GOOGLE_LLM_MODEL, "openrouter", "text_generation", "https://openrouter.ai/google/gemini-3.8-flash/providers", "openrouter"),
        SourceSpec("vercel_gemini_38_flash", "llm", GOOGLE_LLM_MODEL, "vercel_ai_gateway", "text_generation", "https://vercel.com/ai-gateway/models/gemini-3.8-flash", "vercel"),
        SourceSpec("openai_gpt_6_astra", "llm", ASTRA_MODEL, "openai_direct", "text_generation", "https://developers.openai.com/api/docs/models/gpt-6-astra.md", "openai_astra"),
        SourceSpec("openrouter_gpt_6_astra", "llm", ASTRA_MODEL, "openrouter", "text_generation", "https://openrouter.ai/openai/gpt-6-astra/providers", "openrouter_astra"),
        SourceSpec("vercel_gpt_6_astra", "llm", ASTRA_MODEL, "vercel_ai_gateway", "text_generation", "https://vercel.com/ai-gateway/models/gpt-6-astra.md", "vercel_astra"),
        SourceSpec("anthropic_claude_fable_51", "llm", FABLE_MODEL, "anthropic_direct", "text_generation", "https://platform.claude.com/docs/en/about-claude/pricing.md", "anthropic_fable"),
        SourceSpec("openrouter_claude_fable_51", "llm", FABLE_MODEL, "openrouter", "text_generation", "https://openrouter.ai/anthropic/claude-fable-5.1/providers", "openrouter_fable"),
        SourceSpec("vercel_claude_fable_51", "llm", FABLE_MODEL, "vercel_ai_gateway", "text_generation", "https://vercel.com/ai-gateway/models/claude-fable-5.1.md", "vercel_fable"),
        SourceSpec("bedrock_claude_fable_51", "llm", FABLE_MODEL, "aws_bedrock", "text_generation", "https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-fable-5-1.md", "bedrock_fable"),
    ]
    for model, title, slug in STANDARD_LLM_MODELS:
        provider = "openai" if model.startswith("gpt-") else "anthropic"
        key = model.replace("-", "_").replace(".", "_")
        specs.extend((
            SourceSpec(f"{provider}_{key}", "llm", model, f"{provider}_direct", "text_generation", f"https://developers.openai.com/api/docs/models/{model}.md" if provider == "openai" else "https://platform.claude.com/docs/en/about-claude/pricing.md", f"{provider}_{key}"),
            SourceSpec(f"openrouter_{key}", "llm", model, "openrouter", "text_generation", f"https://openrouter.ai/{provider}/{slug}/providers", f"openrouter_{key}"),
            SourceSpec(f"vercel_{key}", "llm", model, "vercel_ai_gateway", "text_generation", f"https://vercel.com/ai-gateway/models/{slug}.md", f"vercel_{key}"),
        ))
    return specs + cloud_source_specs() + broker_catalog_source_specs()


def broker_catalog_source_specs() -> list[SourceSpec]:
    specs: list[SourceSpec] = []
    for model, _, _, openrouter_id, _ in BROKER_CATALOG_MODELS:
        key = _key(model)
        specs.extend((
            SourceSpec(f"openrouter_{key}", "llm", model, "openrouter", "text_generation", f"https://openrouter.ai/{openrouter_id}", f"missing_openrouter_{key}"),
            SourceSpec(f"vercel_{key}", "llm", model, "vercel_ai_gateway", "text_generation", f"https://vercel.com/ai-gateway/models/{model}.md", f"missing_vercel_{key}"),
        ))
    specs.insert(0, SourceSpec("anthropic_claude_sonnet_5", "llm", "claude-sonnet-5", "anthropic_direct", "text_generation", "https://platform.claude.com/docs/en/about-claude/pricing.md", "missing_anthropic_claude_sonnet_5"))
    return specs


def cloud_source_specs() -> list[SourceSpec]:
    specs = [SourceSpec(f"bedrock_{model.replace('-', '_').replace('.', '_')}", "llm", model,
                        "aws_bedrock", "text_generation", f"{_AWS_BASE}openai-{slug}.html",
                        f"cloud_bedrock_{model.replace('-', '_').replace('.', '_')}")
             for model, slug, _ in _AWS_OPENAI]
    specs.extend(SourceSpec(f"bedrock_{model.replace('-', '_')}", "llm", model,
                            "aws_bedrock", "text_generation", f"{_AWS_BASE}anthropic-{slug}.html",
                            f"cloud_bedrock_{model.replace('-', '_')}")
                 for model, slug, _ in _AWS_ANTHROPIC)
    specs.extend(SourceSpec(f"azure_{model.replace('-', '_').replace('.', '_')}", "llm", model,
                            "azure_foundry", "text_generation", f"{_AZURE_BASE}{model}",
                            f"cloud_azure_{model.replace('-', '_').replace('.', '_')}")
                 for model in _AZURE_MODELS)
    return specs


def image_source_specs() -> list[SourceSpec]:
    sources = (
        ("openai_sunburst", "openai", "https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst.md", "openai"),
        ("fal_sunburst_t2i", "fal", "https://fal.ai/models/openai/gpt-image-2.5/sunburst/text-to-image", "fal_task"),
        ("fal_sunburst_overview", "fal", "https://fal.ai/gpt-image-2.5", "fal_overview"),
        ("openrouter_sunburst", "openrouter", "https://openrouter.ai/openai/gpt-image-2.5-sunburst-20260908", "openrouter"),
        ("vercel_sunburst", "vercel", "https://vercel.com/ai-gateway/models/gpt-image-2.5-sunburst", "vercel"),
    )
    return [SourceSpec(key, "image", SUNBURST_MODEL, route, "text-to-image", url, parser) for key, route, url, parser in sources] + flare_source_specs()


def flare_source_specs() -> list[SourceSpec]:
    sources = (
        ("openai_flare", "openai", "https://developers.openai.com/api/docs/models/gpt-image-2.5-flare.md", "flare_openai"),
        ("fal_flare_t2i", "fal", "https://fal.ai/models/openai/gpt-image-2.5/flare/text-to-image", "flare_fal_task"),
        ("openrouter_flare", "openrouter", "https://openrouter.ai/openai/gpt-image-2.5-flare", "flare_openrouter"),
        ("vercel_flare", "vercel", "https://vercel.com/ai-gateway/models/gpt-image-2.5-flare", "flare_vercel"),
    )
    return [SourceSpec(key, "image", FLARE_MODEL, route, "text-to-image", url, parser)
            for key, route, url, parser in sources]


def video_source_specs() -> list[SourceSpec]:
    return [
        SourceSpec("fal_seedance_25_t2v", "video", SEEDANCE_MODEL, "fal", "text-to-video", "https://fal.ai/models/bytedance/seedance-2.5/text-to-video", "fal"),
        SourceSpec("byteplus_seedance_25_t2v", "video", SEEDANCE_MODEL, "BytePlus ModelArk", "text-to-video", "https://docs.byteplus.com/id/docs/modelark/model-pricing?redirect=1", "byteplus_pricing"),
        SourceSpec("byteplus_seedance_promotion_rules", "video", SEEDANCE_MODEL, "BytePlus ModelArk", "text-to-video", "https://docs.byteplus.com/en/docs/modelark/seedance-2-0-mini-fast-limited-time-discount?redirect=1", "byteplus_campaign"),
    ]

def source_specs() -> list[SourceSpec]:
    specs = llm_source_specs() + image_source_specs() + video_source_specs()
    for spec in specs:
        if MODEL_MODALITIES.get(spec.model_id) != spec.modality:
            raise CollectionError(f"source model/modality mismatch: {spec.source_key}")
    return specs
