"""Canonical model and provider identities for the fixed collection registry."""

GOOGLE_LLM_MODEL = "gemini-3.8-flash"

GOOGLE_BROKER_MODEL = "google/gemini-3.8-flash"

ASTRA_MODEL = "gpt-6-astra"
FABLE_MODEL = "claude-fable-5-1"

STANDARD_LLM_MODELS = (
    ("gpt-6-sol", "GPT-6 Sol", "gpt-6-sol"),
    ("gpt-6.1-sol", "GPT-6.1 Sol", "gpt-6.1-sol"),
    ("gpt-6-luna", "GPT-6 Luna", "gpt-6-luna"),
    ("gpt-5.6-sol", "GPT-5.6 Sol", "gpt-5.6-sol"),
    ("gpt-5.6-terra", "GPT-5.6 Terra", "gpt-5.6-terra"),
    ("gpt-5.6-luna", "GPT-5.6 Luna", "gpt-5.6-luna"),
    ("claude-opus-5-5", "Claude Opus 5.5", "claude-opus-5.5"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5", "claude-sonnet-5.5"),
    ("claude-haiku-4-5", "Claude Haiku 4.5", "claude-haiku-4.5"),
)

BROKER_CATALOG_MODELS = (
    ("claude-sonnet-5", "Claude Sonnet 5", "Anthropic: Claude Sonnet 5", "anthropic/claude-sonnet-5", "anthropic/claude-sonnet-5"),
    ("deepseek-v4-pro", "DeepSeek V4 Pro", "DeepSeek: DeepSeek V4 Pro 0423", "deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-pro"),
    ("deepseek-v4.1-flash", "DeepSeek V4.1 Flash", "DeepSeek: DeepSeek V4.1 Flash", "deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash"),
    ("glm-5.2", "GLM 5.2", "Z.ai: GLM 5.2", "z-ai/glm-5.2", "zai/glm-5.2"),
    ("glm-5.3-flash", "GLM 5.3 Flash", "Z.ai: GLM 5.3 Flash", "z-ai/glm-5.3-flash", "zai/glm-5.3-flash"),
    ("kimi-k3", "Kimi K3", "MoonshotAI: Kimi K3", "moonshotai/kimi-k3", "moonshotai/kimi-k3"),
)

# Models LETSUR serves through OpenRouter or Vercel AI Gateway, collected only from the two endpoint APIs (2026-10-28):
# (model, title, OpenRouter id, Vercel id); None where that gateway does not list the model.
# qwen3.5-plus has no OpenRouter id: OpenRouter lists only qwen/qwen3.5-plus-02-15 and qwen/qwen3.5-plus-20260420,
# and which one LETSUR's OpenRouter route calls is not known.
BROKER_API_MODELS = (
    ("deepseek-v4-flash", "DeepSeek V4 Flash", "deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-flash"),
    ("deepseek-v4-flash-0731", "DeepSeek V4 Flash 0731", "deepseek/deepseek-v4-flash-0731", "deepseek/deepseek-v4-flash-0731"),
    ("deepseek-v4-pro-0813", "DeepSeek V4 Pro 0813", "deepseek/deepseek-v4-pro-0813", "deepseek/deepseek-v4-pro-0813"),
    ("deepseek-v4.1-flash-1017", "DeepSeek V4.1 Flash 1017", "deepseek/deepseek-v4.1-flash-1017", "deepseek/deepseek-v4.1-flash-1017"),
    ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", "google/gemini-2.5-flash-lite", "google/gemini-2.5-flash-lite"),
    ("glm-4.5", "GLM 4.5", "z-ai/glm-4.5", "zai/glm-4.5"),
    ("glm-4.5-air", "GLM 4.5 Air", "z-ai/glm-4.5-air", "zai/glm-4.5-air"),
    ("glm-4.6", "GLM 4.6", "z-ai/glm-4.6", "zai/glm-4.6"),
    ("glm-5-turbo", "GLM 5 Turbo", "z-ai/glm-5-turbo", "zai/glm-5-turbo"),
    ("glm-5.1", "GLM 5.1", "z-ai/glm-5.1", "zai/glm-5.1"),
    ("grok-4.20", "Grok 4.20", "x-ai/grok-4.20", None),
    ("grok-4.3", "Grok 4.3", "x-ai/grok-4.3", "spacexai/grok-4.3"),
    ("grok-4.6", "Grok 4.6", "x-ai/grok-4.6", "spacexai/grok-4.6"),
    ("kimi-k2-thinking", "Kimi K2 Thinking", "moonshotai/kimi-k2-thinking", None),
    ("kimi-k2.5", "Kimi K2.5", "moonshotai/kimi-k2.5", "moonshotai/kimi-k2.5"),
    ("kimi-k2.6", "Kimi K2.6", "moonshotai/kimi-k2.6", "moonshotai/kimi-k2.6"),
    ("kimi-k2.7-code", "Kimi K2.7 Code", "moonshotai/kimi-k2.7-code", "moonshotai/kimi-k2.7-code"),
    ("kimi-k3.1-pro", "Kimi K3.1 Pro", "moonshotai/kimi-k3.1-pro", "moonshotai/kimi-k3.1-pro"),
    ("minimax-m2", "MiniMax M2", "minimax/minimax-m2", "minimax/minimax-m2"),
    ("minimax-m2.1", "MiniMax M2.1", "minimax/minimax-m2.1", "minimax/minimax-m2.1"),
    ("minimax-m2.5", "MiniMax M2.5", "minimax/minimax-m2.5", "minimax/minimax-m2.5"),
    ("minimax-m2.7", "MiniMax M2.7", "minimax/minimax-m2.7", "minimax/minimax-m2.7"),
    ("minimax-m3", "MiniMax M3", "minimax/minimax-m3", "minimax/minimax-m3"),
    ("muse-spark-1.2", "Muse Spark 1.2", "meta/muse-spark-1.2", "meta/muse-spark-1.2"),
    ("muse-spark-1.3", "Muse Spark 1.3", "meta/muse-spark-1.3", "meta/muse-spark-1.3"),
    ("muse-wonder-1.0", "Muse Wonder", None, "meta/muse-wonder-1.0"),
    ("qwen3-coder-flash", "Qwen3 Coder Flash", "qwen/qwen3-coder-flash", None),
    ("qwen3-coder-next", "Qwen3 Coder Next", "qwen/qwen3-coder-next", "alibaba/qwen3-coder-next"),
    ("qwen3.5-plus", "Qwen 3.5 Plus", None, "alibaba/qwen3.5-plus"),
    ("qwen3.6-plus", "Qwen 3.6 Plus", "qwen/qwen3.6-plus", "alibaba/qwen3.6-plus"),
    ("qwen3.7-flash", "Qwen 3.7 Flash", "qwen/qwen3.7-flash", "alibaba/qwen3.7-flash"),
    ("qwen3.7-max", "Qwen 3.7 Max", "qwen/qwen3.7-max", "alibaba/qwen3.7-max"),
    ("qwen3.7-plus", "Qwen 3.7 Plus", "qwen/qwen3.7-plus", "alibaba/qwen3.7-plus"),
    ("qwen3.8-max", "Qwen 3.8 Max", None, "alibaba/qwen3.8-max"),
)

# Gemini models LETSUR serves straight from Google, each read from its own section of the Gemini API pricing page
# (2026-10-28): (model, title). The model id is also the id of the section's h2 on that page.
# Not collected, because the page has no section for them: gemini-3.5-flash and gemini-3.7-flash (their model pages say
# deprecated, routed to gemini-3.6-flash and gemini-3.8-flash), gemini-3-pro-image-preview and gemini-3.1-flash-image-preview.
GOOGLE_DIRECT_MODELS = (
    ("gemini-4-argon", "Gemini 4 Argon"),
    ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
    ("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite"),
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite"),
    ("gemini-3.6-flash", "Gemini 3.6 Flash"),
    ("gemini-3-flash-preview", "Gemini 3 Flash Preview"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ("gemini-2.5-pro", "Gemini 2.5 Pro"),
)

GOOGLE_IMAGE_MODELS = (
    ("gemini-nano-banana-2.1", "Gemini Nano Banana 2.1"),
    ("gemini-2.5-flash-image", "Gemini 2.5 Flash Image"),
    ("gemini-3-pro-image", "Gemini 3 Pro Image"),
    ("gemini-3.1-flash-image", "Gemini 3.1 Flash Image"),
)

# More models LETSUR serves whose OpenRouter and Vercel AI Gateway prices are read from the two endpoint APIs (2026-10-28), in the
# shape of BROKER_API_MODELS; the makers' own prices come from other sources. Every id below answered with priced endpoints.
# - claude-opus-4-5-20251101 and claude-sonnet-4-5-20250929 are LETSUR's dated ids; both gateways list only the undated model
#   (anthropic/claude-opus-4.5, anthropic/claude-sonnet-4.5), so that is the id read here.
# - gpt-5-codex has no OpenRouter id: its endpoint list there is empty. gpt-5.1 and gemini-3-flash-preview have no Vercel id: its
#   endpoint API still answers for both (as "GPT 5.1 Thinking" and "Gemini 3 Flash"), but its model list does not carry them.
# - gpt-5.6 is not read: openai/gpt-5.6 is an OpenRouter alias whose listing names gpt-5.6-sol, and Vercel has no such id.
GATEWAY_MORE_MODELS = (
    ("gpt-5.4-mini", "GPT-5.4 Mini", "openai/gpt-5.4-mini", "openai/gpt-5.4-mini"),
    ("gpt-5.1-codex", "GPT-5.1-Codex", "openai/gpt-5.1-codex", "openai/gpt-5.1-codex"),
    ("o4-mini", "o4-mini", "openai/o4-mini", "openai/o4-mini"),
    ("o3", "o3", "openai/o3", "openai/o3"),
    ("gpt-4o", "GPT-4o", "openai/gpt-4o", "openai/gpt-4o"),
    ("gpt-5.1-codex-mini", "GPT-5.1-Codex-Mini", "openai/gpt-5.1-codex-mini", "openai/gpt-5.1-codex-mini"),
    ("o3-mini", "o3-mini", "openai/o3-mini", "openai/o3-mini"),
    ("o1", "o1", "openai/o1", "openai/o1"),
    ("gpt-4.1-nano", "GPT-4.1 Nano", "openai/gpt-4.1-nano", "openai/gpt-4.1-nano"),
    ("gpt-5-mini", "GPT-5 Mini", "openai/gpt-5-mini", "openai/gpt-5-mini"),
    ("gpt-5.2", "GPT-5.2", "openai/gpt-5.2", "openai/gpt-5.2"),
    ("gpt-5", "GPT-5", "openai/gpt-5", "openai/gpt-5"),
    ("gpt-4o-mini", "GPT-4o-mini", "openai/gpt-4o-mini", "openai/gpt-4o-mini"),
    ("gpt-5-codex", "GPT-5-Codex", None, "openai/gpt-5-codex"),
    ("gpt-5.4-nano", "GPT-5.4 Nano", "openai/gpt-5.4-nano", "openai/gpt-5.4-nano"),
    ("gpt-5-nano", "GPT-5 Nano", "openai/gpt-5-nano", "openai/gpt-5-nano"),
    ("gpt-5.1", "GPT-5.1", "openai/gpt-5.1", None),
    ("gpt-5.4", "GPT-5.4", "openai/gpt-5.4", "openai/gpt-5.4"),
    ("o3-pro", "o3-pro", "openai/o3-pro", "openai/o3-pro"),
    ("gpt-4.1", "GPT-4.1", "openai/gpt-4.1", "openai/gpt-4.1"),
    ("gpt-5.5", "GPT-5.5", "openai/gpt-5.5", "openai/gpt-5.5"),
    ("gpt-4.1-mini", "GPT-4.1 Mini", "openai/gpt-4.1-mini", "openai/gpt-4.1-mini"),
    ("gpt-5.5-pro", "GPT-5.5 Pro", "openai/gpt-5.5-pro", "openai/gpt-5.5-pro"),
    ("gpt-5.2-pro", "GPT-5.2 Pro", "openai/gpt-5.2-pro", "openai/gpt-5.2-pro"),
    ("gpt-6.1-terra", "GPT-6.1 Terra", "openai/gpt-6.1-terra", "openai/gpt-6.1-terra"),
    ("claude-opus-4-6", "Claude Opus 4.6", "anthropic/claude-opus-4.6", "anthropic/claude-opus-4.6"),
    ("claude-opus-5", "Claude Opus 5", "anthropic/claude-opus-5", "anthropic/claude-opus-5"),
    ("claude-fable-5", "Claude Fable 5", "anthropic/claude-fable-5", "anthropic/claude-fable-5"),
    ("claude-opus-4-8", "Claude Opus 4.8", "anthropic/claude-opus-4.8", "anthropic/claude-opus-4.8"),
    ("claude-opus-4-7", "Claude Opus 4.7", "anthropic/claude-opus-4.7", "anthropic/claude-opus-4.7"),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6", "anthropic/claude-sonnet-4.6", "anthropic/claude-sonnet-4.6"),
    ("claude-haiku-5-5", "Claude Haiku 5.5", "anthropic/claude-haiku-5.5", "anthropic/claude-haiku-5.5"),
    ("claude-opus-4-5-20251101", "Claude Opus 4.5", "anthropic/claude-opus-4.5", "anthropic/claude-opus-4.5"),
    ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5", "anthropic/claude-sonnet-4.5", "anthropic/claude-sonnet-4.5"),
    ("gemini-3.5-flash", "Gemini 3.5 Flash", "google/gemini-3.5-flash", "google/gemini-3.5-flash"),
    ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", "google/gemini-3.1-pro-preview", "google/gemini-3.1-pro-preview"),
    ("gemini-4-argon", "Gemini 4 Argon", "google/gemini-4-argon", "google/gemini-4-argon"),
    ("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite", "google/gemini-3.1-flash-lite", "google/gemini-3.1-flash-lite"),
    ("gemini-3.7-flash", "Gemini 3.7 Flash", "google/gemini-3.7-flash", "google/gemini-3.7-flash"),
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite", "google/gemini-3.5-flash-lite", "google/gemini-3.5-flash-lite"),
    ("gemini-3.6-flash", "Gemini 3.6 Flash", "google/gemini-3.6-flash", "google/gemini-3.6-flash"),
    ("gemini-3-flash-preview", "Gemini 3 Flash Preview", "google/gemini-3-flash-preview", None),
    ("gemini-2.5-flash", "Gemini 2.5 Flash", "google/gemini-2.5-flash", "google/gemini-2.5-flash"),
    ("gemini-2.5-pro", "Gemini 2.5 Pro", "google/gemini-2.5-pro", "google/gemini-2.5-pro"),
    ("glm-4.7", "GLM 4.7", "z-ai/glm-4.7", "zai/glm-4.7"),
    ("glm-5", "GLM 5", "z-ai/glm-5", "zai/glm-5"),
    ("glm-5.3", "GLM 5.3", "z-ai/glm-5.3", "zai/glm-5.3"),
    ("glm-5.3-flashx", "GLM 5.3 FlashX", "z-ai/glm-5.3-flashx", "zai/glm-5.3-flashx"),
)

# Embedding models, read with task "embedding". OpenRouter lists them in its separate embeddings catalog (not in /api/v1/models).
# Both gateways give a prompt price and completion "0"; Vercel also gives context_length 0. The endpoint parser keeps all as written.
GATEWAY_EMBEDDING_MODELS = (
    ("text-embedding-3-large", "text-embedding-3-large", "openai/text-embedding-3-large", "openai/text-embedding-3-large"),
    ("text-embedding-3-small", "text-embedding-3-small", "openai/text-embedding-3-small", "openai/text-embedding-3-small"),
    ("text-embedding-ada-002", "text-embedding-ada-002", "openai/text-embedding-ada-002", "openai/text-embedding-ada-002"),
)

# Z.ai's own token prices (model, row label in the tables of https://docs.z.ai/guides/overview/pricing.md).
ZAI_DIRECT_MODELS = (
    ("glm-4.7", "GLM-4.7"),
    ("glm-5", "GLM-5"),
    ("glm-5.2", "GLM-5.2"),
    ("glm-5.3", "GLM-5.3"),
    ("glm-5.3-flashx", "GLM-5.3-FlashX"),
)

_AWS_OPENAI = (
    ("gpt-6-astra", "gpt-6-astra", "GPT-6 Astra"),
    ("gpt-6-sol", "gpt-6-sol", "GPT-6 Sol"),
    ("gpt-6.1-sol", "gpt-6-1-sol", "GPT-6.1 Sol"),
    ("gpt-6-luna", "gpt-6-luna", "GPT-6 Luna"),
    ("gpt-5.6-sol", "gpt-56-sol", "GPT-5.6 Sol"),
    ("gpt-5.6-terra", "gpt-56-terra", "GPT-5.6 Terra"),
    ("gpt-5.6-luna", "gpt-56-luna", "GPT-5.6 Luna"),
)

_AWS_ANTHROPIC = (
    ("claude-opus-5-5", "claude-opus-5-5", "Claude Opus 5.5"),
    ("claude-sonnet-5-5", "claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ("claude-haiku-4-5", "claude-haiku-4-5", "Claude Haiku 4.5"),
)

# Azure Foundry catalog pages (no numeric rate); the GPT models are priced from Azure retail meters since 2026-10-28.
_AZURE_MODELS = ("claude-fable-5-1",) + tuple(model for model, _, _ in _AWS_ANTHROPIC)
_AZURE_RETAIL_MODELS = tuple(model for model, _, _ in _AWS_OPENAI)

# GPT and o-series models LETSUR serves through Azure OpenAI (10/28 test calls), priced from Azure retail meters
# (2026-10-28): the GPT-5.x ones on the GPT6/GPT5 product page, the others on the Azure OpenAI and Reasoning product page.
AZURE_RETAIL_GPT5_MODELS = ("gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-codex", "gpt-5.1", "gpt-5.1-codex", "gpt-5.1-codex-mini",
                            "gpt-5.2", "gpt-5.4", "gpt-5.4-2026-03-05", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.5")
AZURE_RETAIL_OPENAI_MODELS = ("gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "o1", "o3", "o3-mini", "o3-pro",
                              "o4-mini")

# Models LETSUR calls on Anthropic's and OpenAI's own routes, read from the makers' pages (2026-10-28).
# (model, row title on Anthropic's price page); the ids are the Claude API ids on Anthropic's model pages. Haiku 5.5
# is priced by prompt length, so its one title stands for two rows.
ANTHROPIC_DIRECT_MODELS = (
    ("claude-opus-4-6", "Claude Opus 4.6"),
    ("claude-opus-5", "Claude Opus 5"),
    ("claude-fable-5", "Claude Fable 5"),
    ("claude-opus-4-8", "Claude Opus 4.8"),
    ("claude-opus-4-7", "Claude Opus 4.7"),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
    ("claude-haiku-5-5", "Claude Haiku 5.5"),
    ("claude-opus-4-5-20251101", "Claude Opus 4.5"),
    ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5"),
)

# (model, title and URL slug of the OpenAI page that prices it). The two dated snapshots have no page of their own (404):
# the page of the base model lists them. gpt-5.6 is an alias: its URL redirects to the GPT-5.6 Sol page, which says so.
OPENAI_DIRECT_MODELS = (
    ("gpt-5.5-pro", "GPT-5.5 Pro", "gpt-5.5-pro"),
    ("gpt-5.2-pro", "GPT-5.2 Pro", "gpt-5.2-pro"),
    ("gpt-6.1-terra", "GPT-6.1 Terra", "gpt-6.1-terra"),
    ("gpt-5.5-pro-2026-04-23", "GPT-5.5 Pro", "gpt-5.5-pro"),
    ("gpt-5.5-2026-04-23", "GPT-5.5", "gpt-5.5"),
    ("gpt-5.6", "GPT-5.6 Sol", "gpt-5.6"),
)

# Embeddings priced on OpenAI's model pages; Azure sells the same three ids.
OPENAI_EMBEDDING_MODELS = ("text-embedding-3-large", "text-embedding-3-small", "text-embedding-ada-002")

# OpenAI's page for it states no price; its token rates are on the pricing page.
GPT_IMAGE_2_MODEL = "gpt-image-2"

SUNBURST_MODEL = "gpt-image-2.5-sunburst"

SUNBURST_FAL_MODEL = "openai/gpt-image-2.5/sunburst/text-to-image"

FLARE_MODEL = "gpt-image-2.5-flare"

FLARE_FAL_MODEL = "openai/gpt-image-2.5/flare/text-to-image"

SEEDANCE_MODEL = "seedance-2.5"

SEEDANCE_FAL_ID = "bytedance/seedance-2.5/text-to-video"

BYTEPLUS_ID = "dreamina-seedance-2-5-260628"

MODEL_MODALITIES = {
    **{model: "llm" for model in (GOOGLE_LLM_MODEL, ASTRA_MODEL, FABLE_MODEL)},
    **{model: "llm" for model, _, _ in STANDARD_LLM_MODELS},
    **{model: "llm" for model, *_ in BROKER_CATALOG_MODELS},
    **{model: "llm" for model, *_ in BROKER_API_MODELS},
    **{model: "llm" for model in AZURE_RETAIL_GPT5_MODELS + AZURE_RETAIL_OPENAI_MODELS},
    **{model: "llm" for model, _ in GOOGLE_DIRECT_MODELS},
    **{model: "image" for model, _ in GOOGLE_IMAGE_MODELS},
    **{model: "llm" for model, *_ in GATEWAY_MORE_MODELS + GATEWAY_EMBEDDING_MODELS},
    SUNBURST_MODEL: "image", FLARE_MODEL: "image", SEEDANCE_MODEL: "video",
    **{model: "llm" for model, _ in ANTHROPIC_DIRECT_MODELS},
    **{model: "llm" for model, *_ in OPENAI_DIRECT_MODELS},
    **{model: "llm" for model in OPENAI_EMBEDDING_MODELS},
    GPT_IMAGE_2_MODEL: "image",
}
