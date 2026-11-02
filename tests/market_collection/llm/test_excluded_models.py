"""The models LETSUR lists but its calls failed on, read from their public routes whatever LETSUR's route is (2026-11-03).
Prices in the listings below are the public ones of 2026-11-03, trimmed to the fields the parsers read."""

import json

import pytest

from scripts.market_collection.collectors import parse_source, parser_module
from scripts.market_collection.collectors import openai_audio
from scripts.market_collection.dashboard import compare
from scripts.market_collection.events.catalog import authority, maker
from scripts.market_collection.models import MODEL_MODALITIES
from scripts.market_collection.signals.matching import models_in
from scripts.market_collection.sources import source_specs

SPECS = {spec.source_key: spec for spec in source_specs()}
ROUTES = {"jev-latest": {"vercel_ai_gateway"}, "jev-1.13.0": {"vercel_ai_gateway"}, "gpt-audio-1.5": {"openai_direct"},
          "gpt-audio-mini": {"openai_direct", "openrouter"}, "qwen3.5-flash": {"openrouter", "vercel_ai_gateway"},
          "qwen3-coder": {"openrouter", "vercel_ai_gateway"}, "qwen3-max-thinking": {"vercel_ai_gateway"}}
ENDED = ("gpt-3.5-turbo", "gpt-4", "gpt-4-turbo", "o1-pro", "qwen3-max", "qwen3-coder-plus")
TABLE = """Realtime and audio generation models

Prices per 1M tokens unless noted.

### Grouped Pricing Table data

| Model | Modality | Input | Output / cost | Cached input |
| --- | --- | --- | --- | --- |
| gpt-realtime | Audio | $32.00 | $64.00 | $0.40 |
| gpt-audio-1.5 | Audio | $32.00 | $64.00 | - |
| gpt-audio-1.5 | Text | $2.50 | $10.00 | - |
| gpt-audio-mini | Audio | $10.00 | $20.00 | - |
| gpt-audio-mini | Text | $0.60 | $2.40 | - |
| gpt-audio | Audio | $32.00 | $64.00 | - |

Image generation models

| Model | Modality | Input | Output / cost | Cached input |
| --- | --- | --- | --- | --- |
| gpt-audio-1.5 | Audio | $1.00 | $1.00 | - |
"""


def rates(prices: list[dict]) -> dict[str, str]:
    return {row["billing_item"]: row["amount"] for row in prices}


def test_the_seven_models_are_registered_on_their_public_routes_and_the_ended_ones_stay_out():
    for model, routes in ROUTES.items():
        assert MODEL_MODALITIES[model] == "llm" and {s.route for s in SPECS.values() if s.model_id == model} == routes
    assert not set(ENDED) & set(MODEL_MODALITIES)
    assert sum(s.model_id in ROUTES for s in SPECS.values()) == 10
    assert SPECS["vercel_llm_endpoints_jev_latest"].url == "https://ai-gateway.vercel.sh/v1/models/typesafe-ai/jev/endpoints"
    assert SPECS["openrouter_llm_endpoints_qwen3_5_flash"].url == "https://openrouter.ai/api/v1/models/qwen/qwen3.5-flash-02-23/endpoints"


def test_openai_audio_rows_give_the_text_and_audio_rates_of_that_model_only():
    for key, expected in (("openai_gpt_audio_1_5", {"input": "2.5", "output": "10", "audio_input": "32", "audio_output": "64"}),
                          ("openai_gpt_audio_mini", {"input": "0.6", "output": "2.4", "audio_input": "10", "audio_output": "20"})):
        spec = SPECS[key]
        assert parser_module(spec) is openai_audio
        prices, conditions = parse_source(spec, TABLE.encode(), "src")
        assert rates(prices) == expected and {row["unit"] for row in prices} == {"1M tokens"}
        assert {c["condition_set_id"] for c in conditions} == {row["condition_set_id"] for row in prices}


def test_a_cached_rate_is_read_when_the_table_starts_to_list_one():
    prices, _ = parse_source(SPECS["openai_gpt_audio_mini"], TABLE.replace("| gpt-audio-mini | Text | $0.60 | $2.40 | - |", "| gpt-audio-mini | Text | $0.60 | $2.40 | $0.06 |").encode(), "src")
    assert rates(prices)["cache_read"] == "0.06"


@pytest.mark.parametrize("broken", [
    TABLE.replace("| gpt-audio-mini | Audio | $10.00 | $20.00 | - |\n", ""),                        # the audio row gone
    TABLE.replace("| gpt-audio-mini | Text | $0.60 | $2.40 | - |\n", "| gpt-audio-mini | Text | $0.60 | $2.40 | - |\n" * 2),  # repeated
    TABLE.replace("| gpt-audio-mini | Text | $0.60 |", "| gpt-audio-mini | Text | 60 cents |"),      # another cell form
    TABLE.replace("| gpt-audio-mini | Text | $0.60 |", "| gpt-audio-mini | Text | - |"),            # a required rate unlisted
    TABLE.replace("| Output / cost |", "| Output |"),                                                 # another table
    TABLE.replace("Prices per 1M tokens unless noted.", "Prices per 1K tokens."),                     # another unit
    TABLE.replace("Realtime and audio generation models", "Audio models"),                            # the section renamed
])
def test_a_changed_page_raises_instead_of_reading_a_price(broken):
    with pytest.raises(ValueError):
        parse_source(SPECS["openai_gpt_audio_mini"], broken.encode(), "src")


def test_jev_on_vercel_keeps_its_zero_output_rate_and_its_makers_name():
    listing = {"data": {"id": "typesafe-ai/jev", "endpoints": [
        {"provider_name": name, "context_length": 1000000, "status": 0, "pricing": {"prompt": "0.000000042", "completion": "0"}}
        for name in ("digitalocean", "typesafe-ai")]}}
    prices, _ = parse_source(SPECS["vercel_llm_endpoints_jev_latest"], json.dumps(listing).encode(), "src")
    assert [(row["upstream_provider"], row["billing_item"], row["amount"]) for row in prices] == [
        ("digitalocean", "input", "0.042"), ("digitalocean", "output", "0"), ("typesafe-ai", "input", "0.042"), ("typesafe-ai", "output", "0")]
    assert maker("jev-1.13.0") == "TypeSafe AI" and authority("jev-latest", "vercel_ai_gateway") == "channel_official"
    assert authority("gpt-audio-1.5", "openai_direct") == "manufacturer_official"


def test_the_comparison_table_has_audio_columns_for_the_audio_models_only():
    assert compare.table_items("gpt-audio-1.5")[-2:] == ("audio_input", "audio_output") and compare.table_items("gpt-5.5") == compare.ITEMS
    observation = {"currency": "USD", "unit": "1M tokens", "price_kind": "published_rate", "billing_item": "audio_input"}
    assert compare.usable({**observation, "model_id": "gpt-audio-mini"}) and not compare.usable({**observation, "model_id": "gpt-5.5"})


@pytest.mark.parametrize("sentence, named", [
    ("GPT-Audio-1.5 is the replacement.", ["gpt-audio-1.5"]),
    ("gpt-audio-mini will be shut down.", ["gpt-audio-mini"]),
    ("Qwen3 Coder 480B is cheaper now.", ["qwen3-coder"]),
    ("Qwen3 Coder Next is cheaper now.", ["qwen3-coder-next"]),           # a longer registered name stays that model's mention
    ("qwen3-coder-plus has ended.", []),
    ("Qwen 3 Max Thinking is cheaper now.", ["qwen3-max-thinking"]),
    ("alibaba/qwen3.5-flash is cheaper now.", ["qwen3.5-flash"]),
    ("jev-latest is cheaper now.", ["jev-latest"]),
    ("Jev 1.13.0 is cheaper now.", ["jev-1.13.0"]),
    ("Jev 1.14 from TypeSafe AI is now available on AI Gateway.", []),    # a version we do not read
])
def test_a_mention_names_the_model_it_means(sentence, named):
    assert [model for alias in models_in(sentence) for model in alias.model_ids] == named
