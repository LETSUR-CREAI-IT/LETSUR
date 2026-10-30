"""OpenRouter and Vercel AI Gateway endpoint listings of the image models (2026-10-29): the real responses, the image-output token
price beside the token rows, fail-closed checks, the registry, and the runs read back into events."""
import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.market_collection import core, sources
from scripts.market_collection.adapters.image import parse_source, source_specs
from scripts.market_collection.collectors import gateway_image_endpoints as G
from scripts.market_collection.events import evidence
from scripts.market_collection.events.catalog import Catalog
from scripts.market_collection.events.engine import Builder
from scripts.market_collection.models import GATEWAY_IMAGE_CATALOG_MODELS, GATEWAY_IMAGE_MODELS, MODEL_MODALITIES
from scripts.market_collection.signals.parsers import openrouter_endpoint_signals, vercel_endpoint_signals

F = Path(__file__).parent / "fixtures" / "gateway_image_endpoints"
SPECS = {s.source_key: s for s in source_specs() if s.parser in G.PARSERS}
OR_FLASH, OR_PRO_PREVIEW = "openrouter_image_endpoints_gemini_3_1_flash_image", "openrouter_image_endpoints_gemini_3_pro_image_preview"
VC_FLASH_PREVIEW, VC_GPT = "vercel_image_endpoints_gemini_3_1_flash_image_preview", "vercel_image_endpoints_gpt_image_2"
CATALOG_GPT = "openrouter_image_catalog_endpoints_gpt_image_2"
FIXTURES = {OR_FLASH: "openrouter-gemini-3.1-flash-image.json", OR_PRO_PREVIEW: "openrouter-gemini-3-pro-image-preview.json",
            VC_FLASH_PREVIEW: "vercel-gemini-3.1-flash-image-preview.json", VC_GPT: "vercel-gpt-image-2.json",
            CATALOG_GPT: "openrouter-images-catalog-gpt-image-2.json"}
# GPT Image 2.5 (2026-10-30), fetched once by curl rather than kept from a run, so the run test below leaves them out.
IMAGE25 = {f"{prefix}_gpt_image_2_5_{variant}": f"{name}-gpt-image-2.5-{variant}.json" for variant in ("flare", "sunburst")
           for prefix, name in (("vercel_image_endpoints", "vercel"), ("openrouter_image_catalog_endpoints", "openrouter-images-catalog"))}


def body(key):
    return (F / (FIXTURES | IMAGE25)[key]).read_bytes()


def parse(key, content=None):
    spec = SPECS[key]
    return core._validate_parse(spec, "saved-s1", *parse_source(spec, body(key) if content is None else content, "saved-s1"))


def changed(key, mutate):
    doc = json.loads(body(key))
    mutate(doc)
    return json.dumps(doc).encode()


def endpoint_rows(key, content=None):
    """endpoint -> {billing item: USD per 1M tokens}; the endpoint is its stated tag."""
    prices, conditions = parse(key, content)
    tags = {c["condition_set_id"]: c["value"] for c in conditions if c["condition_key"] == "endpoint_tag"}
    rows = {}
    for p in prices:
        rows.setdefault((p["upstream_provider"], tags.get(p["condition_set_id"], "")), {})[p["billing_item"]] = p["amount"]
    return rows


def test_nine_models_register_seventeen_sources_after_the_existing_ones():
    assert len(SPECS) == 17 and {s.model_id for s in SPECS.values()} == {row[0] for row in GATEWAY_IMAGE_MODELS}
    assert all(s.modality == "image" and s.task == "text-to-image" and MODEL_MODALITIES[s.model_id] == "image" for s in SPECS.values())
    assert {(s.route, s.parser) for s in SPECS.values()} == {("openrouter", "openrouter_image_endpoints"), ("vercel_ai_gateway", "vercel_image_endpoints"),
                                                             ("openrouter", "openrouter_image_catalog_endpoints")}
    registered = sources.source_specs()
    first = registered.index(next(iter(SPECS.values())))
    assert registered[first:first + 17] == list(SPECS.values())  # one block, in this order
    routes = {}
    for s in SPECS.values():
        routes.setdefault(s.model_id, set()).add((s.route, s.parser))
    # gemini-3-pro-image-preview: Vercel's model list does not carry it. gpt-image-2: OpenRouter's /api/v1/models does not, its images catalog does.
    assert routes["gemini-3-pro-image-preview"] == {("openrouter", "openrouter_image_endpoints")}
    assert routes["gpt-image-2"] == {("vercel_ai_gateway", "vercel_image_endpoints"), ("openrouter", "openrouter_image_catalog_endpoints")}
    # GPT Image 2.5 (2026-10-30): the endpoint listings beside the model pages, which give one headline price per gateway.
    assert routes["gpt-image-2.5-flare"] == routes["gpt-image-2.5-sunburst"] == routes["gpt-image-2"]
    assert all(routes[m] == {("openrouter", "openrouter_image_endpoints"), ("vercel_ai_gateway", "vercel_image_endpoints")}
               for m in routes if m.startswith("gemini") and m != "gemini-3-pro-image-preview")
    urls = {s.source_key: s.url for s in SPECS.values()}
    for key, url in (
        (OR_PRO_PREVIEW, "https://openrouter.ai/api/v1/models/google/gemini-3-pro-image-preview/endpoints"),
        (VC_FLASH_PREVIEW, "https://ai-gateway.vercel.sh/v1/models/google/gemini-3.1-flash-image-preview/endpoints"),
        ("openrouter_image_endpoints_gemini_nano_banana_2_1", "https://openrouter.ai/api/v1/models/google/gemini-nano-banana-2.1/endpoints"),
        (VC_GPT, "https://ai-gateway.vercel.sh/v1/models/openai/gpt-image-2/endpoints"),
        (CATALOG_GPT, "https://openrouter.ai/api/v1/images/models/openai/gpt-image-2/endpoints"),
    ):
        assert urls[key] == url
    assert [row[:2] for row in GATEWAY_IMAGE_CATALOG_MODELS] == [("gpt-image-2", "openai/gpt-image-2"), ("gpt-image-2.5-flare", "openai/gpt-image-2.5-flare"),
                                                                  ("gpt-image-2.5-sunburst", "openai/gpt-image-2.5-sunburst")]
    for p in json.loads((F / "provenance.json").read_text()):
        assert hashlib.sha256((F / p["fixture"]).read_bytes()).hexdigest() == p["raw_sha256"] and SPECS[p["source_key"]].url == p["source_url"]


def test_gateway_listings_are_api_role_channel_sources():
    for spec in SPECS.values():
        info = Catalog().price_source(asdict(spec))
        assert (info["role"], info["authority"], info["publisher"]) == ("api", "channel_official", "OpenRouter" if spec.route == "openrouter" else "Vercel")
    with pytest.raises(ValueError):  # image sources need a source ID at parse time
        parse_source(SPECS[OR_FLASH], body(OR_FLASH), "")


def test_openrouter_rows_are_the_text_tokens_and_the_image_output_token_price_per_endpoint():
    """gemini-3.1-flash-image, 2026-10-28: the regional Vertex endpoint is 10% dearer on every item, image output included."""
    assert endpoint_rows(OR_FLASH) == {
        ("Google AI Studio", "google-ai-studio"): {"input": "0.5", "output": "3", "image_output": "60"},
        ("Google", "google-vertex/global"): {"input": "0.5", "output": "3", "image_output": "60"},
        ("Google", "google-vertex/us"): {"input": "0.55", "output": "3.3", "image_output": "66"}}
    prices, _ = parse(OR_FLASH)
    assert all(p["provider_model_id"] == "google/gemini-3.1-flash-image" and p["unit"] == "1M tokens" and p["currency"] == "USD" and p["task"] == "text-to-image"
               and p["price_kind"] == "published_rate" and p["evidence_status"] == "direct" for p in prices)
    assert {p["locator"].split("=")[0] for p in prices if p["billing_item"] == "image_output"} == {f"endpoints[{i}].pricing.image_output" for i in range(3)}


def test_the_image_output_price_has_the_conditions_of_its_endpoints_other_rows():
    """gemini-3-pro-image-preview: the flex endpoint (half price) and the default one; cache, audio, image input and web search fields too."""
    prices, conditions = parse(OR_PRO_PREVIEW)
    by_set = {}
    for c in conditions:
        by_set.setdefault(c["condition_set_id"], []).append({k: v for k, v in c.items() if k != "condition_set_id"})
    for tag, expected in (("google-ai-studio/global/flex", {"input": "1", "output": "6", "cache_read": "0.1", "cache_write": "0.1875", "image_output": "60"}),
                          ("google-ai-studio/global", {"input": "2", "output": "12", "cache_read": "0.2", "cache_write": "0.375", "image_output": "120"})):
        own = {p["billing_item"]: p for p in prices if {c["condition_key"]: c["value"] for c in by_set[p["condition_set_id"]]}["endpoint_tag"] == tag}
        assert {item: p["amount"] for item, p in own.items()} == expected
        assert by_set[own["image_output"]["condition_set_id"]] == by_set[own["input"]["condition_set_id"]]
        assert {c["condition_key"]: c["value"] for c in by_set[own["image_output"]["condition_set_id"]]}["service_tier"] == ("flex" if tag.endswith("flex") else "standard")
    # image (documented per input image, valued like a token), audio, input_audio_cache, web_search and internal_reasoning give no row
    assert {p["billing_item"] for p in prices} == {"input", "output", "cache_read", "cache_write", "image_output"}


def test_an_endpoint_without_image_output_gets_no_such_row():
    prices, _ = parse(OR_FLASH, changed(OR_FLASH, lambda doc: doc["data"]["endpoints"][1]["pricing"].pop("image_output")))
    assert sorted(p["billing_item"] for p in prices) == ["image_output"] * 2 + ["input"] * 3 + ["output"] * 3
    prices, _ = parse(OR_FLASH, changed(OR_FLASH, lambda doc: doc["data"]["endpoints"][1]["pricing"].update(image_output=None)))  # null is absent
    assert sorted(p["billing_item"] for p in prices) == ["image_output"] * 2 + ["input"] * 3 + ["output"] * 3


@pytest.mark.parametrize("value", ["0.067", "0.001", "1", "x", "-0.00006", "", True, {}, []],
                         ids=["per_image_dollars", "ceiling", "one_dollar_a_token", "word", "negative", "empty", "bool", "object", "list"])
def test_image_output_must_be_a_token_price_below_the_ceiling(value):
    with pytest.raises(ValueError):
        parse(OR_FLASH, changed(OR_FLASH, lambda doc: doc["data"]["endpoints"][0]["pricing"].update(image_output=value)))


def test_image_output_is_read_from_a_json_number_and_from_a_string_alike():
    for value in ("0.00006", 6e-05):
        prices, _ = parse(OR_FLASH, changed(OR_FLASH, lambda doc: doc["data"]["endpoints"][0]["pricing"].update(image_output=value)))
        assert {p["amount"] for p in prices if p["billing_item"] == "image_output"} == {"60", "66"}


def strip_token_prices(doc):
    for field in ("prompt", "completion"):
        doc["data"]["endpoints"][0]["pricing"].pop(field)


def wrapped(doc):
    inner = dict(doc)
    doc.clear()
    doc["data"] = inner


@pytest.mark.parametrize("mutate", [
    lambda doc: doc["data"]["endpoints"][0]["pricing"].update(overrides=[{"min_prompt_tokens": 200000, "image_output": "0.00009"}]),
    strip_token_prices,
    lambda doc: doc["data"].update(id="google/gemini-3.1-flash-image-preview"),
    lambda doc: doc["data"]["endpoints"].append(copy.deepcopy(doc["data"]["endpoints"][0])),
    lambda doc: doc["data"]["endpoints"][0].update(tag=""),
], ids=["override_with_image_output", "image_output_without_token_price", "other_model", "duplicate_tag", "empty_tag"])
def test_openrouter_listings_that_cannot_be_read_exactly_fail_closed(mutate):
    with pytest.raises(ValueError):
        parse(OR_FLASH, changed(OR_FLASH, mutate))


def test_an_override_without_image_output_is_read_as_before():
    plain = parse(OR_FLASH)
    with_override = changed(OR_FLASH, lambda doc: doc["data"]["endpoints"][0]["pricing"].update(overrides=[{"min_prompt_tokens": 200000, "prompt": "0.000001"}]))
    prices, _ = parse(OR_FLASH, with_override)
    assert len(prices) == len(plain[0]) + 1 and sum(p["billing_item"] == "image_output" for p in prices) == 3


def test_vercel_rows_are_the_token_rates_and_state_no_image_price():
    """Vercel's endpoint API documents prompt, completion and the cache fields; image and image_output are "0" placeholders there."""
    assert endpoint_rows(VC_FLASH_PREVIEW) == {("google", ""): {"input": "0.5", "output": "3", "cache_read": "0.05"}}
    assert endpoint_rows(VC_GPT) == {("openai", ""): {"input": "5", "output": "30", "cache_read": "1.25"}}
    for key in (VC_FLASH_PREVIEW, VC_GPT):
        assert {p["task"] for p in parse(key)[0]} == {"text-to-image"}
    # The flex tier price stays a condition of the listing, never a row; a stated image_output would not be read either.
    stated = changed(VC_FLASH_PREVIEW, lambda doc: doc["data"]["endpoints"][0]["pricing"].update(image_output="0.00006"))
    assert parse(VC_FLASH_PREVIEW, stated) == parse(VC_FLASH_PREVIEW)
    assert "0.25" not in {p["amount"] for p in parse(VC_FLASH_PREVIEW)[0]}


def test_images_catalog_token_rates_of_gpt_image_2():
    """OpenRouter's images catalog types every rate line: gpt-image-2 is 5 text input, 8 image input and 30 image output per 1M tokens."""
    prices, conditions = parse(CATALOG_GPT)
    assert {p["billing_item"]: p["amount"] for p in prices} == {"input": "5", "image_input": "8", "image_output": "30"}
    assert all(p["upstream_provider"] == "OpenAI" and p["provider_model_id"] == "openai/gpt-image-2" and p["unit"] == "1M tokens" and p["task"] == "text-to-image"
               and p["price_kind"] == "published_rate" and p["evidence_status"] == "direct" for p in prices)
    assert {p["locator"].split(": ")[1].split(",")[0] for p in prices} == {"input_text per token", "input_image per token", "output_image per token"}
    groups = {}
    for c in conditions:
        assert c["status"] == "stated"
        groups.setdefault(c["condition_set_id"], {})[c["condition_key"]] = c["value"]
    assert {p["condition_set_id"] for p in prices} == set(groups) and len(groups) == 3
    assert all(group == {"price_basis": "endpoint_listed_rate", "endpoint_tag": "openai"} for group in groups.values())


@pytest.mark.parametrize("mutate", [
    lambda doc: doc["endpoints"][0]["pricing"][0].update(billable="input_font"),
    lambda doc: doc["endpoints"][0]["pricing"][0].update(unit="image"),
    lambda doc: doc["endpoints"][0]["pricing"][0].update(unit="megapixel"),
    lambda doc: doc["endpoints"][0]["pricing"][0].update(variant="1K"),
    lambda doc: doc["endpoints"][0]["pricing"][0].update(note="per request"),
    lambda doc: doc["endpoints"][0]["pricing"][0].pop("unit"),
    lambda doc: doc["endpoints"][0]["pricing"].append(dict(doc["endpoints"][0]["pricing"][2])),
    lambda doc: doc["endpoints"][0]["pricing"][2].update(cost_usd=-1),
    lambda doc: doc["endpoints"][0]["pricing"][2].update(cost_usd="x"),
    lambda doc: doc["endpoints"][0]["pricing"][2].update(cost_usd=True),
    lambda doc: doc["endpoints"][0]["pricing"][2].update(cost_usd=None),
    lambda doc: doc["endpoints"][0].update(provider_tag=None),
    lambda doc: doc["endpoints"][0].update(provider_tag=""),
    lambda doc: doc["endpoints"][0].update(provider_name=""),
    lambda doc: doc["endpoints"][0].update(pricing={}),
    lambda doc: doc["endpoints"].append(copy.deepcopy(doc["endpoints"][0])),
    lambda doc: doc.update(id="openai/gpt-image-1"),
    lambda doc: doc.pop("endpoints"),
    wrapped,
], ids=["unknown_billable", "per_image_unit", "megapixel_unit", "variant", "extra_key", "no_unit", "duplicate_line", "negative", "word", "bool", "null",
        "null_tag", "empty_tag", "empty_name", "pricing_object", "duplicate_tag", "other_model", "no_endpoints", "data_wrapper"])
def test_image_catalog_listings_that_cannot_be_read_exactly_fail_closed(mutate):
    with pytest.raises(ValueError):
        parse(CATALOG_GPT, changed(CATALOG_GPT, mutate))


def test_gpt_image_2_5_listings_name_the_endpoint_beside_the_model_pages():
    """The model pages give one headline price per gateway (a 모델 페이지 대표가 on the dashboard); these listings name the
    endpoint, its status and its rates: OpenAI serves both models at 5 text input, 8 image input and 30 image output per 1M
    tokens (Vercel calls the last one output, as for gpt-image-2)."""
    for variant in ("flare", "sunburst"):
        model = f"gpt-image-2.5-{variant}"
        vercel, catalog = f"vercel_image_endpoints_gpt_image_2_5_{variant}", f"openrouter_image_catalog_endpoints_gpt_image_2_5_{variant}"
        assert endpoint_rows(vercel) == {("openai", ""): {"input": "5", "output": "30", "cache_read": "1.25"}}
        assert {c["condition_key"]: c["value"] for c in parse(vercel)[1] if c["status"] == "stated"} == {
            "context_length": "0", "endpoint_provider": "openai", "endpoint_status": "0", "price_basis": "endpoint_listed_rate"}
        prices, conditions = parse(catalog)
        assert {p["billing_item"]: p["amount"] for p in prices} == {"input": "5", "image_input": "8", "image_output": "30"}
        assert {p["provider_model_id"] for p in prices} == {"openai/" + model} and {SPECS[key].model_id for key in (vercel, catalog)} == {model}
        assert {(c["condition_key"], c["value"]) for c in conditions} == {("price_basis", "endpoint_listed_rate"), ("endpoint_tag", "openai")}


def test_a_listing_with_no_endpoints_is_read_as_no_rows():
    """As for the text models: a model every endpoint of which is gone leaves no row, so its series are seen to drop out."""
    assert parse(CATALOG_GPT, changed(CATALOG_GPT, lambda doc: doc.update(endpoints=[]))) == ([], [])
    assert parse(OR_FLASH, changed(OR_FLASH, lambda doc: doc["data"].update(endpoints=[]))) == ([], [])


def test_runs_over_the_fixtures_are_read_into_baselines_and_image_output_events(tmp_path):
    _, adapters = core.registry()
    specs = [SPECS[key] for key in FIXTURES]
    cheaper = changed(CATALOG_GPT, lambda doc: doc["endpoints"][0]["pricing"][2].update(cost_usd=2.4e-05))
    runs = []
    for catalog_body in (body(CATALOG_GPT), cheaper):
        bodies = {SPECS[key].url: body(key) for key in FIXTURES} | {SPECS[CATALOG_GPT].url: catalog_body}
        runs.append(core.live(tmp_path, specs=specs, adapters=adapters, fetcher=lambda spec, bodies=bodies: (bodies[spec.url], spec.url, 200)))
    sources = core._read_csv(runs[0] / "sources.csv", core.SOURCE_FIELDS)
    assert {(s["source_key"], s["fetch_status"], s["parse_status"], s["error"]) for s in sources} == {(key, "ok", "ok", "") for key in FIXTURES}
    builder = Builder(Catalog())
    builder.build([evidence.load_run(run) for run in runs])
    # Since 2026-10-30 an unstated serving precision does not keep a closed model's gateway row out, nor do Vercel's untiered
    # tier and region (decisions 10, 18; evidence.blocking_unknowns): the endpoint rows of these closed models form series too.
    # Only OpenRouter's cache writes, whose TTL is not stated, stay out.
    assert {(n["reason"], n["route"], n["billing_item"]) for n in builder.state["not_comparable"]} == {("condition_unknown", "openrouter", "cache_write")}
    assert len(builder.state["baselines"]) == 26  # the catalog's input, image_input and image_output (the last then moved) and 23 endpoint rows
    assert [(e["model_id"], e["route"], e["billing_item"], e["before"]["amount"], e["after"]["amount"], e["change"]["pct"])
            for e in builder.state["events"].values()] == [("gpt-image-2", "openrouter", "image_output", "30", "24", "-20.00")]


def test_the_endpoint_signal_parsers_read_the_same_listings():
    """The new signal sources (every listing but the images catalog's) use the same URLs: no discount or end date is stated now."""
    assert openrouter_endpoint_signals(body(OR_FLASH), "gemini-3.1-flash-image", "Gemini 3.1 Flash Image") == (3, [])
    assert vercel_endpoint_signals(body(VC_GPT), "gpt-image-2", "GPT Image 2") == (1, [])
    count, rows = openrouter_endpoint_signals(changed(OR_FLASH, lambda doc: doc["data"]["endpoints"][1]["pricing"].update(discount=0.5)),
                                              "gemini-3.1-flash-image", "Gemini 3.1 Flash Image")
    assert count == 3 and [(r["claim_kinds"], r["model_ids"], r["locator"]) for r in rows] == [
        ("discount_active", "gemini-3.1-flash-image", "data.endpoints[1].pricing.discount")]
    count, rows = vercel_endpoint_signals(changed(VC_GPT, lambda doc: doc["data"]["endpoints"][0].update(deprecated_at=1794960000000)), "gpt-image-2", "GPT Image 2")
    assert count == 1 and [(r["claim_kinds"], r["date_phrases"]) for r in rows] == [("endpoint_deprecation", "2026-11-18")]
