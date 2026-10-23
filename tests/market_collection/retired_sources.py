"""Registry sources removed on purpose; the immutable baseline fixtures still list them."""

# Google ended the Gemini API Veo 3.1 preview on 2026-10-22 and removed its pricing section.
RETIRED_SOURCE_KEYS = frozenset({"google_veo_3_1_standard", "google_veo_3_1_fast", "google_veo_3_1_lite"})


def without_retired(specs: list[dict]) -> list[dict]:
    return [spec for spec in specs if spec["source_key"] not in RETIRED_SOURCE_KEYS]
