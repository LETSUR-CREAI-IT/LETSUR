"""Explicit, ordered model by route source configuration."""

from __future__ import annotations

from scripts.market_collection.core import SourceSpec, CollectionError
from scripts.market_collection.models import (GOOGLE_LLM_MODEL, ASTRA_MODEL, FABLE_MODEL, STANDARD_LLM_MODELS, BROKER_CATALOG_MODELS, BROKER_API_MODELS, _AWS_OPENAI, _AWS_ANTHROPIC, _AZURE_MODELS, _AZURE_RETAIL_MODELS, AZURE_RETAIL_GPT5_MODELS, AZURE_RETAIL_OPENAI_MODELS, SUNBURST_MODEL, FLARE_MODEL, SEEDANCE_MODEL, MODEL_MODALITIES)
from scripts.market_collection.models import ANTHROPIC_DIRECT_MODELS, OPENAI_DIRECT_MODELS, OPENAI_EMBEDDING_MODELS, GPT_IMAGE_2_MODEL
from scripts.market_collection.models import GOOGLE_DIRECT_MODELS, GOOGLE_IMAGE_MODELS

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
    return specs + cloud_source_specs() + broker_catalog_source_specs() + [
        SourceSpec("vertex_gemini_38_flash", "llm", GOOGLE_LLM_MODEL, "google_vertex", "text_generation", "https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing", "vertex_gemini"),
        SourceSpec("databricks_gemini_38_flash", "llm", GOOGLE_LLM_MODEL, "azure_databricks", "text_generation", "https://learn.microsoft.com/en-us/azure/databricks/machine-learning/foundation-model-apis/supported-models", "databricks_gemini"),
    ]


def llm_endpoint_source_specs() -> list[SourceSpec]:
    gateway_ids = [(GOOGLE_LLM_MODEL, "google/gemini-3.8-flash", "google/gemini-3.8-flash")]
    gateway_ids.extend((model, target[4], target[4]) for model, target in _targets().items())
    gateway_ids.extend((model, openrouter_id, vercel_id) for model, _, _, openrouter_id, vercel_id in BROKER_CATALOG_MODELS)
    gateway_ids.extend((model, openrouter_id, vercel_id) for model, _, openrouter_id, vercel_id in BROKER_API_MODELS)
    specs: list[SourceSpec] = []
    for model, openrouter_id, vercel_id in gateway_ids:
        key = _key(model)
        if openrouter_id:  # None: the gateway does not list the model
            specs.append(SourceSpec(f"openrouter_llm_endpoints_{key}", "llm", model, "openrouter", "text_generation", f"https://openrouter.ai/api/v1/models/{openrouter_id}/endpoints", "openrouter_endpoints_llm"))
        if vercel_id:
            specs.append(SourceSpec(f"vercel_llm_endpoints_{key}", "llm", model, "vercel_ai_gateway", "text_generation", f"https://ai-gateway.vercel.sh/v1/models/{vercel_id}/endpoints", "vercel_endpoints_llm"))
    return specs


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


_AZURE_IMAGE_PRICING = "https://prices.azure.com/api/retail/prices?$filter=contains(productName,'OpenAI')%20and%20contains(meterName,'2.5')%20and%20armRegionName%20eq%20'koreacentral'"


# One fixed page of the Foundry Models GPT-6 and GPT-5 products in koreacentral, shared by the seven GPT specs and the
# GPT-5.x specs of azure_retail_models_source_specs().
_AZURE_GPT_PRICING = "https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20'Foundry%20Models'%20and%20armRegionName%20eq%20'koreacentral'%20and%20(productName%20eq%20'Azure%20OpenAI%20GPT6'%20or%20productName%20eq%20'Azure%20OpenAI%20GPT5')"


def azure_retail_llm_source_specs() -> list[SourceSpec]:
    # Since 2026-10-28 these replace the catalog-page specs azure_<model> of the same models (appended: the registry is additive).
    return [SourceSpec(f"azure_retail_{_key(model)}", "llm", model, "azure_foundry", "text_generation", _AZURE_GPT_PRICING, "azure_llm_retail")
            for model in _AZURE_RETAIL_MODELS]


# One fixed page of the Foundry Models Azure OpenAI and Azure OpenAI Reasoning products in koreacentral (GPT-4o, GPT-4.1, o-series).
_AZURE_OPENAI_PRICING = "https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20'Foundry%20Models'%20and%20armRegionName%20eq%20'koreacentral'%20and%20(productName%20eq%20'Azure%20OpenAI'%20or%20productName%20eq%20'Azure%20OpenAI%20Reasoning')"


def azure_retail_models_source_specs() -> list[SourceSpec]:
    # The other Azure OpenAI models LETSUR serves (2026-10-28); the embedding models have no Global Standard meter in koreacentral.
    pages = [(model, _AZURE_GPT_PRICING) for model in AZURE_RETAIL_GPT5_MODELS] + [(model, _AZURE_OPENAI_PRICING) for model in AZURE_RETAIL_OPENAI_MODELS]
    return [SourceSpec(f"azure_retail_{_key(model)}", "llm", model, "azure_foundry", "text_generation", url, "azure_models_retail")
            for model, url in pages]


def maker_direct_source_specs() -> list[SourceSpec]:
    """Anthropic and OpenAI models LETSUR calls on the makers' own routes, priced from the makers' pages (2026-10-28)."""
    specs = [SourceSpec(f"anthropic_{_key(model)}", "llm", model, "anthropic_direct", "text_generation", "https://platform.claude.com/docs/en/about-claude/pricing.md", f"anthropic_{_key(model)}")
             for model, _ in ANTHROPIC_DIRECT_MODELS]
    specs.extend(SourceSpec(f"openai_{_key(model)}", "llm", model, "openai_direct", "text_generation", f"https://developers.openai.com/api/docs/models/{slug}.md", f"openai_{_key(model)}")
                 for model, _, slug in OPENAI_DIRECT_MODELS)
    specs.extend(SourceSpec(f"openai_{_key(model)}", "llm", model, "openai_direct", "embedding", f"https://developers.openai.com/api/docs/models/{model}.md", f"openai_{_key(model)}")
                 for model in OPENAI_EMBEDDING_MODELS)
    # The gpt-image-2 model page lists no price, so its token rates are read from OpenAI's pricing page.
    specs.append(SourceSpec(f"openai_{_key(GPT_IMAGE_2_MODEL)}", "image", GPT_IMAGE_2_MODEL, "openai", "text-to-image", "https://developers.openai.com/api/docs/pricing.md", f"openai_{_key(GPT_IMAGE_2_MODEL)}"))
    return specs


def google_model_source_specs() -> list[SourceSpec]:
    # One section of the Gemini API pricing page (the page google_gemini_38_flash reads) per model, picked by h2 id = model id.
    return [SourceSpec(f"google_{_key(model)}", modality, model, "google_direct", task, "https://ai.google.dev/gemini-api/docs/pricing", "google_model_pricing")
            for modality, task, models in (("llm", "text_generation", GOOGLE_DIRECT_MODELS), ("image", "text-to-image", GOOGLE_IMAGE_MODELS))
            for model, _ in models]


def image_source_specs() -> list[SourceSpec]:
    # Ideogram 4.5 is not collected: LETSUR's model list has no Ideogram (2026-10-28).
    sources = (
        ("openai_sunburst", "openai", "https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst.md", "openai"),
        ("fal_sunburst_t2i", "fal", "https://fal.ai/models/openai/gpt-image-2.5/sunburst/text-to-image", "fal_task"),
        ("fal_sunburst_overview", "fal", "https://fal.ai/gpt-image-2.5", "fal_overview"),
        ("openrouter_sunburst", "openrouter", "https://openrouter.ai/openai/gpt-image-2.5-sunburst-20260908", "openrouter"),
        ("vercel_sunburst", "vercel", "https://vercel.com/ai-gateway/models/gpt-image-2.5-sunburst", "vercel"),
    )
    return [SourceSpec(key, "image", SUNBURST_MODEL, route, "text-to-image", url, parser) for key, route, url, parser in sources] + flare_source_specs() + [
        SourceSpec("azure_" + model.split("-")[-1], "image", model, "azure_foundry", "text-to-image", _AZURE_IMAGE_PRICING, "azure_image_retail")
        for model in (SUNBURST_MODEL, FLARE_MODEL)
    ]


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
    specs = [
        SourceSpec("fal_seedance_25_t2v", "video", SEEDANCE_MODEL, "fal", "text-to-video", "https://fal.ai/models/bytedance/seedance-2.5/text-to-video", "fal"),
        SourceSpec("byteplus_seedance_25_t2v", "video", SEEDANCE_MODEL, "BytePlus ModelArk", "text-to-video", "https://docs.byteplus.com/id/docs/modelark/model-pricing?redirect=1", "byteplus_pricing"),
        SourceSpec("byteplus_seedance_promotion_rules", "video", SEEDANCE_MODEL, "BytePlus ModelArk", "text-to-video", "https://docs.byteplus.com/en/docs/modelark/seedance-2-0-mini-fast-limited-time-discount?redirect=1", "byteplus_campaign"),
    ]
    # Veo 3.1 is not collected: Google ended its Gemini API preview on 2026-10-22, and LETSUR's model list has no Veo (2026-10-28).
    specs.append(SourceSpec("vercel_seedance_25_t2v", "video", SEEDANCE_MODEL, "vercel_ai_gateway", "text-to-video", "https://vercel.com/ai-gateway/models/seedance-2.5", "vercel_video"))
    return specs


def source_specs() -> list[SourceSpec]:
    specs = llm_source_specs() + image_source_specs() + video_source_specs() + image25_api_source_specs() + image25_openrouter_endpoint_source_specs() + image25_supplement_source_specs() + llm_endpoint_source_specs() + azure_retail_llm_source_specs() + azure_retail_models_source_specs() + maker_direct_source_specs() + google_model_source_specs()
    for spec in specs:
        if MODEL_MODALITIES.get(spec.model_id) != spec.modality:
            raise CollectionError(f"source model/modality mismatch: {spec.source_key}")
    return specs


def image25_api_source_specs() -> list[SourceSpec]:
    specs = []
    for model in (SUNBURST_MODEL, FLARE_MODEL):
        variant = model.rsplit('-', 1)[1]
        for supplier, route, task, url, parser in (
            ('openai', 'openai', 'model-conditions', 'https://developers.openai.com/api/docs/guides/image-generation.md', 'image25_openai_guide'),
            ('fal', 'fal', 'text-to-image', f'https://fal.ai/models/openai/gpt-image-2.5/{variant}/text-to-image/api', 'image25_fal_api'),
            ('openrouter', 'openrouter', 'model-conditions', 'https://openrouter.ai/api/v1/images/models', 'image25_openrouter_api'),
            ('vercel', 'vercel', 'model-conditions', 'https://vercel.com/changelog/gpt-image-2-5-flare-and-sunburst-now-available-on-ai-gateway', 'image25_vercel_api'),
        ):
            specs.append(SourceSpec(f'image25_{supplier}_{variant}_api', 'image', model, route, task, url, parser))
    return specs


def image25_openrouter_endpoint_source_specs() -> list[SourceSpec]:
    return [SourceSpec(f"image25_openrouter_{model.rsplit('-', 1)[1]}_endpoints", "image", model,
                       "openrouter", "text-to-image",
                       f"https://openrouter.ai/api/v1/images/models/openai/{model}/endpoints",
                       "image25_openrouter_endpoints") for model in (SUNBURST_MODEL, FLARE_MODEL)]


def image25_supplement_source_specs() -> list[SourceSpec]:
    specs = []
    for model in (SUNBURST_MODEL, FLARE_MODEL):
        variant = model.rsplit('-', 1)[1]
        for kind, route, url in (
            ('openai_generate', 'openai', 'https://developers.openai.com/api/reference/resources/images/methods/generate'),
            ('azure_howto', 'azure_foundry', 'https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/dall-e'),
            ('azure_regions', 'azure_foundry', 'https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability'),
            ('openrouter_guide', 'openrouter', 'https://openrouter.ai/docs/guides/overview/multimodal/image-generation'),
            ('vercel_ui', 'vercel', f'https://vercel.com/ai-gateway/models/gpt-image-2.5-{variant}'),
            ('fal_guide', 'fal', 'https://fal.ai/learn/tools/how-to-use-gpt-image-2-5'),
        ):
            specs.append(SourceSpec(f'image25_{kind}_{variant}_doc', 'image', model, route,
                                    'model-conditions', url, 'image25_'+kind))
    return specs
