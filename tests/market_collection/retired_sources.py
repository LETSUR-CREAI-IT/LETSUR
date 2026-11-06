"""Registry sources removed or moved on purpose; the immutable baseline fixtures still list them as they were."""

# Google ended the Gemini API Veo 3.1 preview on 2026-10-22 and removed its pricing section.
# LETSUR's model list has no Veo or Ideogram, so their fal, Vercel and Ideogram sources went on 2026-10-28.
RETIRED_SOURCE_KEYS = frozenset({"google_veo_3_1_standard", "google_veo_3_1_fast", "google_veo_3_1_lite",
                                 "fal_veo_3_1_standard", "fal_veo_3_1_fast", "fal_veo_3_1_lite",
                                 "vercel_veo_3_1_standard", "vercel_veo_3_1_fast", "vercel_veo_3_1_lite",
                                 "ideogram_45_t2i", "ideogram_45_edit", "ideogram_45_precise_edit",
                                 "fal_ideogram_45_t2i", "fal_ideogram_45_edit", "fal_ideogram_45_precise_edit",
                                 # Azure catalog pages of the GPT models, replaced by the retail-meter specs azure_retail_<model> (2026-10-28).
                                 "azure_gpt_6_astra", "azure_gpt_6_sol", "azure_gpt_6_1_sol", "azure_gpt_6_luna",
                                 "azure_gpt_5_6_sol", "azure_gpt_5_6_terra", "azure_gpt_5_6_luna",
                                 # Vercel dropped the Gemini 3.1 Flash Image preview: 404 model_not_found from 2026-11-06 (models.py).
                                 "vercel_image_endpoints_gemini_3_1_flash_image_preview"})

# Google moved the Gemini API pricing page off ai.google.dev (2026-11-03).
MOVED_SOURCE_URLS = {"google_gemini_38_flash": "https://aistudio.google.com/docs/pricing.md"}


def without_retired(specs: list[dict]) -> list[dict]:
    return [dict(spec, url=MOVED_SOURCE_URLS.get(spec["source_key"], spec["url"])) for spec in specs if spec["source_key"] not in RETIRED_SOURCE_KEYS]
