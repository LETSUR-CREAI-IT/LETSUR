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

_AZURE_MODELS = tuple(model for model, _, _ in _AWS_OPENAI) + ("claude-fable-5-1",) + tuple(model for model, _, _ in _AWS_ANTHROPIC)

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
    SUNBURST_MODEL: "image", FLARE_MODEL: "image", SEEDANCE_MODEL: "video",
}
