"""Kling 3.0/4.0 and Seedance 2.5 reference-to-video and video-to-video sources (2026-10-28).

Fixtures are trimmed real responses of 2026-10-28; fixtures/provenance.json records each source and what was cut.
"""
import csv
import hashlib
import json
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.adapters import video
from scripts.market_collection.collectors import SUPPLIERS, parser_module
from scripts.market_collection.events.catalog import ROUTE_PUBLISHERS, source_role
from scripts.market_collection.models import KLING_VIDEO_MODELS, SEEDANCE_VARIANT_MODELS
from scripts.market_collection.sources import kling_seedance_source_specs

F = Path(__file__).parent / "fixtures"
SPECS = {s.source_key: s for s in kling_seedance_source_specs()}
SHELL = (Path(__file__).parents[1] / "core" / "fixtures" / "byteplus" / "unrendered-template.html").read_bytes()
HOW_READ = {"price_basis", "endpoint_provider", "operation"}


def fixture(spec):
    name = {"kling_pricing": "kling-pricing.html", "byteplus_video_input": "byteplus-pricing.html"}.get(spec.parser)
    return (F / (name or spec.source_key + (".md" if spec.parser == "fal_llms" else ".json"))).read_bytes()


def parse(key, body=None):
    spec = SPECS[key]
    return core._validate_parse(spec, "s1", *video.parse_source(spec, fixture(spec) if body is None else body, "s1"))


def table(key, body=None):
    """(amount, unit, price kind, stated conditions) of each price row, leaving out how the row was read."""
    prices, conditions = parse(key, body)
    stated = {}
    for row in conditions:
        stated.setdefault(row["condition_set_id"], {})[row["condition_key"]] = row["value"]
    return [(p["amount"], p["unit"], p["price_kind"], {k: v for k, v in stated[p["condition_set_id"]].items() if k not in HOW_READ}) for p in prices]


def terms(key):
    prices, conditions = parse(key)
    priced = {p["condition_set_id"] for p in prices}
    return {row["condition_key"]: row["value"] for row in conditions if row["condition_set_id"] not in priced}


def sec(amount, kind="published_rate", **conditions):
    return (amount, "1 second", kind, conditions)


def changed(key, old, new):
    body = fixture(SPECS[key])
    assert old in body
    return body.replace(old, new)


def test_twenty_sources_for_eight_models_in_their_own_modules():
    specs = list(SPECS.values())
    assert len(specs) == 20 and set(specs) <= set(video.source_specs())
    assert {s.model_id for s in specs} == {model for model, _ in KLING_VIDEO_MODELS + SEEDANCE_VARIANT_MODELS}
    assert {(s.route, s.parser, parser_module(s).__name__.rsplit(".", 1)[1]) for s in specs} == {
        ("kling_direct", "kling_pricing", "kling"), ("fal", "fal_llms", "fal_llms"),
        ("BytePlus ModelArk", "byteplus_video_input", "byteplus_video"), ("vercel_ai_gateway", "vercel_video_endpoints", "vercel_video")}
    assert {s.parser: source_role(s.parser) for s in specs} == {
        "kling_pricing": "pricing", "fal_llms": "pricing", "byteplus_video_input": "pricing", "vercel_video_endpoints": "api"}
    assert {s.model_id: s.task for s in specs} == dict(KLING_VIDEO_MODELS + SEEDANCE_VARIANT_MODELS)
    assert ROUTE_PUBLISHERS["kling_direct"] == "Kling AI" and "kling_direct" in SUPPLIERS
    # Kling 4.0 has no official price row and Seedance 2.5 video-to-video no fal or Vercel listing.
    assert {s.route for s in specs if s.model_id.startswith("kling-v4")} == {"fal"}
    assert {s.route for s in specs if s.model_id == "byteplus-seedance-2.5-video-to-video"} == {"BytePlus ModelArk"}
    for p in json.loads((F / "provenance.json").read_text()):
        assert hashlib.sha256((F / p["fixture"]).read_bytes()).hexdigest() == p["fixture_sha256"]
        assert p["source_url"] in {s.url for s in specs}


def test_kling_table_prices_the_model_so_text_and_image_to_video_read_one_row():
    rows = [sec("0.084", resolution="720p", audio="off"), sec("0.126", resolution="720p", audio="on", voice_control="off"),
            sec("0.112", resolution="1080p", audio="off"), sec("0.168", resolution="1080p", audio="on", voice_control="off"),
            sec("0.42", resolution="4k", audio="off"), sec("0.42", resolution="4k", audio="on", voice_control="off")]
    for key in ("kling_v3_t2v", "kling_v3_i2v"):
        assert table(key) == rows
        prices, conditions = parse(key)
        assert {(p["provider_model_id"], p["upstream_provider"], p["task"], p["model_version"]) for p in prices} == {("Kling 3.0", "Kling AI", SPECS[key].task, "3.0")}
        assert {c["value"] for c in conditions if c["condition_key"] == "price_basis"} == {"list price"}
        assert "0.6 Units ($0.084) /s" in prices[0]["locator"]
    assert table("kling_v3_omni") == [
        sec("0.084", resolution="720p", input_video="none", audio="off"), sec("0.112", resolution="720p", input_video="none", audio="on"),
        sec("0.126", resolution="720p", input_video="yes", audio="off"), sec("0.112", resolution="1080p", input_video="none", audio="off"),
        sec("0.14", resolution="1080p", input_video="none", audio="on"), sec("0.168", resolution="1080p", input_video="yes", audio="off"),
        sec("0.42", resolution="4k", input_video="none", audio="off"), sec("0.42", resolution="4k", input_video="none", audio="on"),
        sec("0.42", resolution="4k", input_video="yes", audio="off")]
    assert {p["provider_model_id"] for p in parse("kling_v3_omni")[0]} == {"Kling 3.0 Omni"}


@pytest.mark.parametrize("key,old,new,error", [
    ("kling_v3_omni", b'"model":"Kling 3.0 Omni"', b'"model":"Kling 3.0 Omni Pro"', "absent or duplicate"),
    ("kling_v3_t2v", b'"model":"Kling 3.0 Turbo"', b'"model":"Kling 3.0"', "absent or duplicate"),
    ("kling_v3_t2v", b"0.6 Units ($0.084) /s", b"0.6 Units ($0.084) /video", "per-second unit price"),
    ("kling_v3_t2v", b"0.6 Units ($0.084) /s", b"0.6 Units /s", "per-second unit price"),
    ("kling_v3_t2v", b'"Price (720P)"', b'"Price (720p)"', "columns changed"),
    ("kling_v3_t2v", b"1 Unit = $0.14 (list price)", b"1 Unit = $0.14", "basis or columns changed"),
    ("kling_v3_omni", b'"With Video Input x No Native Audio"', b'"With Video Input"', "feature label"),
    ("kling_v3_t2v", b'"spec":"Per second"', b'"spec":"Per video"', "billing type"),
    ("kling_v3_t2v", b"window['kConf_ytech.klingWeb.devModelPageInfo_18n']", b"window['kConf_other']", "table absent or reformatted"),
])
def test_kling_row_unit_label_and_identity_fail_closed(key, old, new, error):
    with pytest.raises(ValueError, match=error):
        parse(key, changed(key, old, new))


def test_kling_reads_only_the_english_table():
    # The Japanese copy of the same row changing is not this source's evidence.
    body = fixture(SPECS["kling_v3_t2v"])
    japanese = body.index(b'"ja":')
    altered = body[:japanese] + body[japanese:].replace(b"($0.084)", b"($0.099)", 1)
    assert altered != body and table("kling_v3_t2v", altered) == table("kling_v3_t2v")


FAL = {
    "fal_kling_v3_t2v_standard": [sec("0.084", mode="standard", audio="off"), sec("0.126", mode="standard", audio="on", voice_control="off"),
                                  sec("0.154", mode="standard", audio="on", voice_control="on")],
    "fal_kling_v3_t2v_pro": [sec("0.112", mode="pro", audio="off"), sec("0.168", mode="pro", audio="on", voice_control="off"),
                             sec("0.196", mode="pro", audio="on", voice_control="on")],
    "fal_kling_v3_i2v_standard": [sec("0.084", mode="standard", audio="off"), sec("0.126", mode="standard", audio="on", voice_control="off"),
                                  sec("0.154", mode="standard", audio="on", voice_control="on")],
    "fal_kling_v3_i2v_pro": [sec("0.112", mode="pro", audio="off"), sec("0.168", mode="pro", audio="on", voice_control="off"),
                             sec("0.196", mode="pro", audio="on", voice_control="on")],
    "fal_kling_v3_omni_standard": [sec("0.084", mode="standard", input_video="none", audio="off"), sec("0.112", mode="standard", input_video="none", audio="on")],
    "fal_kling_v3_omni_pro": [sec("0.112", mode="pro", input_video="none", audio="off"), sec("0.14", mode="pro", input_video="none", audio="on")],
    "fal_kling_v3_omni_video_standard": [sec("0.126", mode="standard", input_video="yes")],
    "fal_kling_v3_omni_video_pro": [sec("0.168", mode="pro", input_video="yes")],
    "fal_kling_v4_t2v": [sec("0.157", resolution="720p"), sec("0.235", resolution="1080p")],
    "fal_kling_v4_i2v": [sec("0.157", resolution="720p"), sec("0.235", resolution="1080p")],
    "fal_kling_v4_omni": [sec("0.157", resolution="720p", input_video="none"), sec("0.235", resolution="1080p", input_video="none"),
                          sec("0.094", resolution="720p", input_video="yes", billed_duration="input plus output length (minimum 2× the output)"),
                          sec("0.141", resolution="1080p", input_video="yes", billed_duration="input plus output length (minimum 2× the output)")],
    "fal_seedance_25_r2v": [sec("0.2205", "provider_estimate", resolution="480p", input_video="none"),
                            sec("0.4730", "provider_estimate", resolution="720p", input_video="none"),
                            sec("1.164", "provider_estimate", resolution="1080p", input_video="none"),
                            ("0.0214", "1000 tokens", "published_rate", {"resolution": "480p/720p", "input_video": "none"}),
                            ("0.0234", "1000 tokens", "provider_estimate", {"resolution": "1080p", "input_video": "none"}),
                            sec("0.2838", "provider_estimate", resolution="720p", input_video="yes")],
}


@pytest.mark.parametrize("key", list(FAL))
def test_fal_endpoint_rates_as_written(key):
    assert table(key) == FAL[key]
    endpoint = SPECS[key].url.removeprefix("https://fal.ai/models/").removesuffix("/llms.txt")
    prices, _ = parse(key)
    assert {(p["provider_model_id"], p["task"]) for p in prices} == {(endpoint, SPECS[key].task)}


def test_fal_seedance_video_multiplier_is_a_term_never_applied():
    assert terms("fal_seedance_25_r2v") == {
        "token_formula": "(height of output video * width of output video * (input video duration + output video duration) * 24) / 1024",
        "video_input_multiplier": "0.6", "video_reference_billing": "With video references, you will be charged for both input and output videos"}
    # The only video-input rate is fal's own per-second estimate; no per-token rate is derived from the multiplier.
    assert [row for row in table("fal_seedance_25_r2v") if row[3]["input_video"] == "yes"] == [sec("0.2838", "provider_estimate", resolution="720p", input_video="yes")]


@pytest.mark.parametrize("key,old,new,error", [
    # another endpoint's page, so another mode's price
    ("fal_kling_v3_t2v_standard", b"`fal-ai/kling-video/v3/standard/text-to-video`", b"`fal-ai/kling-video/v3/pro/text-to-video`", "exact endpoint"),
    ("fal_kling_v4_omni", b"`kling/v4/reference-to-video`", b"`kling/v4/video-to-video/edit`", "exact endpoint"),
    ("fal_kling_v3_t2v_pro", b"(audio off)", b"(no audio)", "worded differently"),
    ("fal_kling_v4_t2v", b"per second at **720p**", b"per video at **720p**", "worded differently"),
    ("fal_kling_v4_i2v", b"per second at **1080p**.", b"per second at **1080p**. 4K costs **$0.42** per second.", "worded differently"),
    ("fal_seedance_25_r2v", b"per 1,000 tokens for 480p", b"per 1M tokens for 480p", "worded differently"),
    ("fal_kling_v3_omni_pro", b"## Pricing", b"## Cost", "worded differently"),
    ("fal_kling_v4_t2v", b"at **1080p**", b"at **720p**", "two rates for one condition"),
    ("fal_kling_v3_omni_video_standard", b"- **`video_url`** (`string`, _required_)", b"- **`video_url`** (`string`, _optional_)", "input schema"),
    ("fal_kling_v3_omni_standard", b"- **`image_urls`** (`list<string>`, _optional_)", b"- **`video_url`** (`string`, _optional_)", "input schema"),
    ("fal_kling_v4_omni", b"- **`video_urls`** (`list<string>`, _optional_)", b"- **`audio_urls`** (`list<string>`, _optional_)", "input schema"),
])
def test_fal_identity_wording_unit_and_input_fail_closed(key, old, new, error):
    with pytest.raises(ValueError, match=error):
        parse(key, changed(key, old, new))


def test_fal_accepts_the_thousands_separator_only():
    assert table("fal_seedance_25_r2v", changed("fal_seedance_25_r2v", b"per 1,000 tokens for 480p", b"per 1000 tokens for 480p")) == FAL["fal_seedance_25_r2v"]


def test_byteplus_rates_by_video_input_tab():
    online = {"inference_mode": "online"}
    assert table("byteplus_seedance_25_r2v") == [
        ("10.70", "1M tokens", "published_rate", {"resolution": "480p/720p", "input_video": "none", **online}),
        ("11.7", "1M tokens", "published_rate", {"resolution": "1080p", "input_video": "none", **online}),
        ("6.40", "1M tokens", "published_rate", {"resolution": "480p/720p", "input_video": "yes", **online}),
        ("7.0", "1M tokens", "published_rate", {"resolution": "1080p", "input_video": "yes", **online})]
    assert table("byteplus_seedance_25_v2v") == table("byteplus_seedance_25_r2v")[2:]
    assert terms("byteplus_seedance_25_v2v") == {"estimated_token_formula": "(Input video duration + output video duration) × output video width × output video height × output video frame rate / 1024"}
    assert {p["provider_model_id"] for p in parse("byteplus_seedance_25_v2v")[0]} == {"dreamina-seedance-2-5-260628"}


@pytest.mark.parametrize("key,old,new,error", [
    ("byteplus_seedance_25_r2v", b"dreamina-seedance-2-5-260628", b"dreamina-seedance-2-0-260128", "row absent or duplicate in Input without video"),
    ("byteplus_seedance_25_v2v", b"$6.40", b"$6.40 per image", "not a plain dollar amount"),
    ("byteplus_seedance_25_r2v", b"USD per million tokens", b"USD per thousand tokens", "unit missing"),
    # the document is JSON inside the page's JSON, so its quotes are escaped
    ("byteplus_seedance_25_v2v", b'\\"title\\": \\"Input with video\\"', b'\\"title\\": \\"Input with audio\\"', "row absent or duplicate in Input with video"),
])
def test_byteplus_row_cell_unit_and_tab_fail_closed(key, old, new, error):
    with pytest.raises(ValueError, match=error):
        parse(key, changed(key, old, new))


def test_byteplus_video_input_sources_retry_the_unrendered_template(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    for key in ("byteplus_seedance_25_r2v", "byteplus_seedance_25_v2v"):
        spec, responses = SPECS[key], iter([SHELL, fixture(SPECS[key])])
        run = core.live(tmp_path / key, specs=[spec], adapters={"video": video}, fetcher=lambda _: (next(responses), spec.url, 200))
        manifest = json.loads((run / "manifest.json").read_text())["sources"][0]
        assert manifest["source"]["parse_status"] == "ok" and len(manifest["fetch_attempts"]) == 2
        core._validated_run(run)


def test_vercel_endpoint_rates():
    kling = [sec("0.168", mode="standard", audio="off"), sec("0.252", mode="standard", audio="on"), sec("0.308", mode="standard", audio="on", voice_control="on"),
             sec("0.224", mode="pro", audio="off"), sec("0.336", mode="pro", audio="on"), sec("0.392", mode="pro", audio="on", voice_control="on")]
    assert table("vercel_kling_v3_t2v") == table("vercel_kling_v3_i2v") == kling
    token = lambda amount, resolution, video: (amount, "1M tokens", "published_rate", {"resolution": resolution, "input_video": video})
    assert table("vercel_seedance_25_r2v") == [token("10.7", "480p", "none"), token("6.4", "480p", "yes"), token("10.7", "720p", "none"),
                                               token("6.4", "720p", "yes"), token("11.7", "1080p", "none"), token("7", "1080p", "yes")]
    prices, conditions = parse("vercel_seedance_25_r2v")
    assert {(p["provider_model_id"], p["upstream_provider"]) for p in prices} == {("bytedance/seedance-2.5", "ByteDance")}
    assert {(c["condition_key"], c["value"]) for c in conditions if c["condition_key"] in HOW_READ} == {
        ("endpoint_provider", "bytedance"), ("operation", "reference-to-video"), ("price_basis", "endpoint_listed_rate")}
    assert terms("vercel_seedance_25_r2v")["billing_notes"].startswith("Pricing varies based on whether the input contains video.")


def vercel_changed(key, mutate):
    data = json.loads(fixture(SPECS[key]))
    mutate(data["data"])
    return json.dumps(data).encode()


@pytest.mark.parametrize("key,mutate,error", [
    ("vercel_seedance_25_r2v", lambda d: d.update(id="bytedance/seedance-2.0"), "not for the registered model"),
    ("vercel_seedance_25_r2v", lambda d: d["capabilities"]["supported_operations"].remove("reference-to-video"), "does not list"),
    ("vercel_kling_v3_t2v", lambda d: d["endpoints"][0]["pricing"]["video_duration_pricing"][0].update(fps=30), "unknown or missing fields"),
    ("vercel_kling_v3_t2v", lambda d: d["endpoints"][0]["pricing"]["video_duration_pricing"][0].update(mode="master"), "mode not recognized"),
    ("vercel_kling_v3_i2v", lambda d: d["endpoints"][0]["pricing"]["video_duration_pricing"][1].update(audio=False), "repeated for one condition"),
    ("vercel_kling_v3_i2v", lambda d: d["endpoints"][0]["pricing"].update(discount=0.1), "discount"),
    ("vercel_seedance_25_r2v", lambda d: d["endpoints"][0]["pricing"].update(video_input_pricing={}), "price fields not recognized"),
    ("vercel_seedance_25_r2v", lambda d: d["endpoints"][0]["pricing"]["video_token_pricing"]["tiers"][0]["with_video_input"].update(unit="1K"), "token rate"),
])
def test_vercel_identity_operation_and_unknown_dimensions_fail_closed(key, mutate, error):
    with pytest.raises(ValueError, match=error):
        parse(key, vercel_changed(key, mutate))


def test_live_run_collects_every_new_source(tmp_path):
    specs = list(SPECS.values())
    run = core.live(tmp_path, specs=specs, adapters={"video": video}, fetcher=lambda spec: (fixture(spec), spec.url, 200))
    with (run / "sources.csv").open(newline="", encoding="utf-8") as stream:
        sources = list(csv.DictReader(stream))
    assert [(row["source_key"], row["fetch_status"], row["parse_status"]) for row in sources] == [(s.source_key, "ok", "ok") for s in specs]
    core._validated_run(run)
    replayed = core.replay(run, tmp_path)
    with (replayed / "sources.csv").open(newline="", encoding="utf-8") as stream:
        assert {row["parse_status"] for row in csv.DictReader(stream)} == {"ok"}
