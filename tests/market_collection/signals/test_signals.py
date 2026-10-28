import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.market_collection.core import CollectionError
from scripts.market_collection import signals
from scripts.market_collection.signals import __main__ as cli
from scripts.market_collection.signals.matching import claims_in, date_phrases, entry_signals, models_in, normalize
from scripts.market_collection.models import BROKER_API_MODELS, GATEWAY_EMBEDDING_MODELS, GATEWAY_MORE_MODELS
from scripts.market_collection.signals.parsers import (SignalParseError, atom_entries, openrouter_endpoint_signals, rss_entries,
                                                       vercel_endpoint_signals)
from scripts.market_collection.signals.registry import SignalSourceSpec, signal_sources, validate

FIXTURES = Path(__file__).parent / "fixtures"
# Raw Vercel endpoint listings of the 10/28 core run (provenance.json next to them).
ENDPOINT_FIXTURES = Path(__file__).parents[1] / "llm" / "fixtures" / "endpoints"
ATOM = SignalSourceSpec("channel_feed", "channel_notice", "https://example.org/atom", "atom")
RSS = SignalSourceSpec("supplier_feed", "supplier_notice", "https://example.org/rss.xml", "rss")
BLOG = SignalSourceSpec("blog_feed", "developer_blog", "https://example.org/blog.atom", "atom")
API = SignalSourceSpec("openrouter_endpoints_gpt_5_6_sol", "channel_api", "https://example.org/endpoints", "openrouter_endpoints", "gpt-5.6-sol", "GPT-5.6 Sol")
BODIES = {ATOM.url: "channel.atom", RSS.url: "supplier.rss", BLOG.url: "blog.atom", API.url: "endpoints.json"}


def fixture_fetcher(spec):
    return (FIXTURES / BODIES[spec.url]).read_bytes(), spec.url, 200


def rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def labels(text):
    return [(alias.label, alias.match) for alias in models_in(normalize(text))]


def test_exact_names_win_over_family_and_longer_versions_do_not_match_shorter():
    assert labels("GPT-5.6 Sol, the flagship of OpenAI's GPT-5.6 series") == [("GPT-5.6 Sol", "exact")]
    assert labels("GPT 5.6 Sol and openai/gpt-5.6-terra") == [("GPT-5.6 Sol", "exact"), ("GPT-5.6 Terra", "exact")]
    assert labels("GPT-5.6 has a scheduled 25% price increase for November") == [("GPT-5.6", "family")]
    assert labels("GPT-6.1 Sol is out") == [("GPT-6.1 Sol", "exact")]
    assert ("GPT-6", "family") not in labels("GPT-6.1 pricing")
    # Unicode non-breaking hyphen as used in OpenAI headlines.
    assert labels("Advancing price-performance with GPT‑5.6 Luna") == [("GPT-5.6 Luna", "exact")]
    assert labels("Seedance 2.0 mini is 60% off") == []


def test_gateway_only_models_match_by_title_and_gateway_ids():
    assert labels("Qwen3.7 Max and alibaba/qwen3.7-plus are on AI Gateway") == [("Qwen 3.7 Max", "exact"), ("Qwen 3.7 Plus", "exact")]
    assert labels("MiniMax M2.5 and x-ai/grok-4.20 pricing") == [("Grok 4.20", "exact"), ("MiniMax M2.5", "exact")]
    assert labels("Muse Wonder is 50% off") == [("Muse Wonder", "exact")]


def test_models_added_on_1028_match_and_a_name_inside_a_longer_model_name_is_that_model():
    """The 70 models added on 2026-10-28 match by their sources' names; a registered name found only inside a longer
    registered name (GPT-4o in GPT-4o mini, o3 in o3-pro, DeepSeek V4 Flash in DeepSeek V4 Flash 0731) is not a mention."""
    assert labels("GPT-4o mini is cheaper") == [("GPT-4o-mini", "exact")]
    assert labels("GPT-4o and GPT-4o mini") == [("GPT-4o", "exact"), ("GPT-4o-mini", "exact")]
    assert labels("o3-pro and o4-mini retire on Azure") == [("o4-mini", "exact"), ("o3-pro", "exact")]
    assert labels("DeepSeek V4 Flash 0731 is 50% off") == [("DeepSeek V4 Flash 0731", "exact")]
    assert labels("Gemini 2.5 Flash Image will be deprecated") == [("Gemini 2.5 Flash Image", "exact")]
    assert labels("GPT Image 2 pricing") == [("GPT Image 2", "exact")] and labels("GPT Image 2.5 pricing") == [("GPT Image 2.5", "family")]
    assert [alias.model_ids for alias in models_in("GPT-5.4 retirement")] == [("gpt-5.4",), ("gpt-5.4-2026-03-05",)]
    # gpt-5.6, OpenAI's alias of GPT-5.6 Sol, has no name of its own: it takes the family's notices, not Sol's or the family's name
    assert [alias.model_ids for alias in models_in("GPT-5.6 has a price increase")] == [("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6")]
    assert labels("GPT-5.6 Sol is 50% off") == [("GPT-5.6 Sol", "exact")]


def test_a_model_name_before_a_variant_word_is_another_model():
    """Price tracker lines of 2026-10-28: o1-pro, GPT-5.2 Codex and GPT-5.2 Chat retired are not o1 or GPT-5.2 notices, and
    the DeepSeek V4 Flash Vision Exp notice is not DeepSeek V4 Flash's (decision 36), registered or not."""
    assert labels("o1-pro (OpenAI) retired") == [] and labels("o1 (OpenAI) retired") == [("o1", "exact")]
    assert labels("GPT-5.2 Codex (OpenAI) retired") == [] and labels("GPT-5.2 Chat (OpenAI) retired") == []
    assert labels("DeepSeek V4 Flash Vision Exp is 50% off") == []
    assert labels("GPT-4.1 nano (OpenAI) retired") == [("GPT-4.1 Nano", "exact")]
    assert labels("Qwen 3.8 Max 0902 price cut") == [("Qwen 3.8 Max", "exact")]  # a dated snapshot is the same model


def test_claim_terms_skip_comparisons_and_keep_kinds():
    assert claims_in("Both models work at a lower price than GPT-6 Astra.")[0] == []
    assert claims_in("OpenAI lowered list pricing for GPT-5.6 Sol.")[0] == ["price_decrease"]
    assert claims_in("GLM-5.3-Flash - price up: $0.119 -> $0.238")[0] == ["price_increase"]
    assert claims_in("GLM 5.2 free for agents through August 27")[0] == ["free"]
    assert claims_in("Veo 3.1 Lite will be deprecated")[0] == ["deprecation"]
    assert claims_in("V7 cuts costs 78% with GPT-5.6 Luna")[0] == []


def test_date_phrases_keep_source_wording_including_at_least():
    phrases = date_phrases(["50% off through September 18.", "Promotional pricing at least until November 21, 2026.", "a scheduled 25% price increase for November"])
    assert phrases == ["through September 18", "at least until November 21, 2026", "for November"]


def test_body_sentence_without_model_uses_title_model():
    found = entry_signals("GPT-5.6 Sol pricing update", "<p>The discount ends through September 18.</p><p>Unrelated sentence.</p>")
    assert [(row["model_label"], row["claim_kinds"], row["date_phrases"]) for row in found] == [("GPT-5.6 Sol", "discount", "through September 18")]


def test_atom_rss_and_endpoint_parsers():
    atom = atom_entries((FIXTURES / "channel.atom").read_bytes())
    assert atom[0].title == "GPT-5.6 Sol is 50% off on AI Gateway for the next month"
    assert "through September 18" in atom[0].body and atom[0].published == datetime(2026, 8, 17, tzinfo=timezone.utc)
    rss = rss_entries((FIXTURES / "supplier.rss").read_bytes())
    assert rss[0].published == datetime(2026, 8, 21, 10, tzinfo=timezone.utc) and rss[0].url.startswith("https://")
    total, found = openrouter_endpoint_signals((FIXTURES / "endpoints.json").read_bytes(), "gpt-5.6-sol", "GPT-5.6 Sol")
    assert total == 3
    assert [row["quote"] for row in found] == [
        "OpenAI (openai/flex): pricing.discount=0.5; input 1 / output 5 USD per 1M tokens (discount applied)",
        "OpenAI (openai): pricing.discount=0.5; input 2 / output 10 USD per 1M tokens (discount applied)"]
    assert found[0]["locator"] == "data.endpoints[0].pricing.discount"


def test_unsafe_or_wrong_bodies_are_parse_errors():
    with pytest.raises(SignalParseError):
        atom_entries(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><feed xmlns="http://www.w3.org/2005/Atom"/>')
    with pytest.raises(SignalParseError):
        rss_entries((FIXTURES / "channel.atom").read_bytes())
    with pytest.raises(SignalParseError):
        openrouter_endpoint_signals(b"<html>login</html>", "gpt-5.6-sol", "GPT-5.6 Sol")


def test_vercel_endpoint_parser_reads_deprecated_at_of_the_real_listing():
    total, found = vercel_endpoint_signals((ENDPOINT_FIXTURES / "vercel-minimax-m2.json").read_bytes(), "minimax-m2", "MiniMax M2")
    assert total == 1
    assert found == [{
        "entry_id": "minimax/minimax-m2#minimax#deprecated_at", "entry_url": "", "entry_title": "MiniMax M2", "published_at_utc": "",
        "locator": "data.endpoints[0].deprecated_at", "model_label": "MiniMax M2", "model_ids": "minimax-m2", "model_match": "exact",
        "claim_kinds": "endpoint_deprecation", "match_terms": "deprecated_at",
        "quote": "minimax: deprecated_at=1794960000000 (2026-11-18T00:00:00Z), status=1; "
                 "deprecated_at is absent from Vercel's API documentation, which defines only status 0 (active)",
        "date_phrases": "2026-11-18"}]
    # No deprecated_at, no row; a discount is left to the core price collector.
    total, found = vercel_endpoint_signals((ENDPOINT_FIXTURES / "vercel-grok-4.3.json").read_bytes(), "grok-4.3", "Grok 4.3")
    assert total >= 1 and found == []
    listing = {"data": {"id": "x/y", "endpoints": [{"provider_name": "a", "pricing": {"discount": 0.5}, "status": 0}]}}
    assert vercel_endpoint_signals(json.dumps(listing).encode(), "y", "Y") == (1, [])


@pytest.mark.parametrize("endpoint", [
    {"provider_name": "minimax", "deprecated_at": 1794960000},  # seconds, not milliseconds
    {"provider_name": "minimax", "deprecated_at": 1794960000000.0},
    {"provider_name": "minimax", "deprecated_at": "1794960000000"},
    {"provider_name": "minimax", "deprecated_at": "2026-11-18"},
    {"provider_name": "minimax", "deprecated_at": None},
    {"provider_name": "minimax", "deprecated_at": True},
    {"provider_name": "minimax", "deprecated_at": -1794960000000},
    {"deprecated_at": 1794960000000},  # no provider to name the upstream
    {"provider_name": "a#b", "deprecated_at": 1794960000000},
    "minimax",
])
def test_vercel_endpoint_parser_refuses_anything_but_epoch_milliseconds(endpoint):
    body = json.dumps({"data": {"id": "minimax/minimax-m2", "endpoints": [endpoint]}}).encode()
    with pytest.raises(SignalParseError):
        vercel_endpoint_signals(body, "minimax-m2", "MiniMax M2")


def test_vercel_endpoint_parser_refuses_other_shapes_and_a_repeated_upstream():
    twice = [{"provider_name": "minimax", "deprecated_at": 1794960000000}] * 2
    for body in (b"<html>login</html>", b'{"data": {"endpoints": {}}}', b'{"endpoints": []}',
                 json.dumps({"data": {"id": "minimax/minimax-m2", "endpoints": twice}}).encode()):
        with pytest.raises(SignalParseError):
            vercel_endpoint_signals(body, "minimax-m2", "MiniMax M2")


def test_gateway_api_sources_cover_every_listed_model_of_both_gateways():
    specs = signal_sources()
    by_parser: dict[str, list] = {}
    for spec in specs:
        by_parser.setdefault(spec.parser, []).append(spec)
    # 2026-10-28: 4 feeds + OpenRouter 18 -> 49 (34 gateway-only models, 31 with an OpenRouter id) -> 99 (51 more, 50 with one)
    # + Vercel 49 -> 98 (49 of the 51 more: gemini-3-flash-preview and gpt-5.1 are OpenRouter only)
    assert len(specs) == 201 and {parser: len(found) for parser, found in by_parser.items()} == {
        "atom": 3, "rss": 1, "openrouter_endpoints": 99, "vercel_endpoints": 98}
    assert all(spec.source_kind == "channel_api" and spec.model_id for parser in ("openrouter_endpoints", "vercel_endpoints")
               for spec in by_parser[parser])
    keys = {spec.source_key: spec for spec in specs}
    assert keys["vercel_endpoints_minimax_m2"].url == "https://ai-gateway.vercel.sh/v1/models/minimax/minimax-m2/endpoints"
    assert keys["vercel_endpoints_claude_opus_5_5"].url == "https://ai-gateway.vercel.sh/v1/models/anthropic/claude-opus-5.5/endpoints"
    assert keys["vercel_endpoints_gemini_3_8_flash"].url == "https://ai-gateway.vercel.sh/v1/models/google/gemini-3.8-flash/endpoints"
    assert keys["vercel_endpoints_glm_5_2"].url == "https://ai-gateway.vercel.sh/v1/models/zai/glm-5.2/endpoints"
    assert keys["openrouter_endpoints_minimax_m2_7"].url == "https://openrouter.ai/api/v1/models/minimax/minimax-m2.7/endpoints"
    assert (keys["vercel_endpoints_minimax_m2_1"].model_id, keys["vercel_endpoints_minimax_m2_1"].model_label) == ("minimax-m2.1", "MiniMax M2.1")
    # a model a gateway does not list gets no source there
    for model, _, openrouter_id, vercel_id in BROKER_API_MODELS:
        key = model.replace("-", "_").replace(".", "_")
        assert (f"openrouter_endpoints_{key}" in keys) == bool(openrouter_id) and (f"vercel_endpoints_{key}" in keys) == bool(vercel_id)
    # the 18 OpenRouter sources of before come first, the 31 of the gateway-only models after them, then the 50 of the 51 more
    added = {model for model, *_ in BROKER_API_MODELS}
    more = {model for model, *_ in GATEWAY_MORE_MODELS + GATEWAY_EMBEDDING_MODELS}
    assert len(more) == 51 and not added & more
    assert not {spec.model_id for spec in by_parser["openrouter_endpoints"][:18]} & (added | more)
    assert {spec.model_id for spec in by_parser["openrouter_endpoints"][18:49]} <= added
    assert {spec.model_id for spec in by_parser["openrouter_endpoints"][49:]} <= more
    for model, _, openrouter_id, vercel_id in GATEWAY_MORE_MODELS + GATEWAY_EMBEDDING_MODELS:
        key = re.sub(r"[^a-z0-9]+", "_", model)
        assert (f"openrouter_endpoints_{key}" in keys) == bool(openrouter_id) and (f"vercel_endpoints_{key}" in keys) == bool(vercel_id)
    assert keys["vercel_endpoints_claude_opus_4_5_20251101"].url == "https://ai-gateway.vercel.sh/v1/models/anthropic/claude-opus-4.5/endpoints"
    assert keys["openrouter_endpoints_glm_5_3"].url == "https://openrouter.ai/api/v1/models/z-ai/glm-5.3/endpoints"
    assert (keys["vercel_endpoints_text_embedding_3_large"].model_id, keys["vercel_endpoints_text_embedding_3_large"].model_label) == ("text-embedding-3-large", "text-embedding-3-large")


def test_both_api_parsers_need_a_model_and_feeds_none():
    with pytest.raises(CollectionError):
        validate([SignalSourceSpec("vercel_endpoints_x", "channel_api", "https://ai-gateway.vercel.sh/v1/models/x/y/endpoints", "vercel_endpoints")])
    with pytest.raises(CollectionError):
        validate([SignalSourceSpec("feed_x", "channel_notice", "https://example.org/atom", "atom", "minimax-m2", "MiniMax M2")])
    assert validate([SignalSourceSpec("vercel_endpoints_x", "channel_api", "https://example.org/e", "vercel_endpoints", "x", "X")])


def test_live_reads_a_vercel_listing_through_its_registered_spec(tmp_path):
    spec = next(spec for spec in signal_sources() if spec.source_key == "vercel_endpoints_minimax_m2")
    body = (ENDPOINT_FIXTURES / "vercel-minimax-m2.json").read_bytes()
    run = signals.live(tmp_path, specs=[spec], fetcher=lambda found: (body, found.url, 200))
    _, sources, found = signals.validated_run(run)
    assert (sources[0]["parse_status"], sources[0]["entries_total"], sources[0]["signal_count"]) == ("ok", "1", "1")
    assert [(row["claim_kinds"], row["origin_status"], row["date_phrases"]) for row in found] == [("endpoint_deprecation", "channel_stated", "2026-11-18")]


def test_registry_is_valid_and_rejects_bad_specs():
    specs = signal_sources()
    assert {spec.source_key for spec in specs} >= {"vercel_changelog", "openai_news", "simonwillison_blog", "modelpricewatch_news", "openrouter_endpoints_gpt_5_6_sol"}
    assert all(spec.url.startswith("https://") for spec in specs)
    with pytest.raises(CollectionError):
        validate([ATOM, ATOM])
    with pytest.raises(CollectionError):
        validate([SignalSourceSpec("bad", "rumor", "https://example.org/a", "atom")])
    with pytest.raises(CollectionError):
        validate([SignalSourceSpec("bad", "channel_notice", "http://example.org/a", "atom")])


def test_live_preserves_raw_records_failures_and_separates_origin_status(tmp_path):
    def fetcher(spec):
        if spec is RSS:
            raise OSError("network down")
        return fixture_fetcher(spec)

    run = signals.live(tmp_path, 3650, specs=[ATOM, RSS, BLOG, API], fetcher=fetcher)
    assert run.parent == tmp_path / "signals" / "runs"
    manifest, sources, found = signals.validated_run(run)
    assert [source["fetch_status"] for source in sources] == ["ok", "error", "ok", "ok"]
    assert "network down" in sources[1]["error"] and not sources[1]["raw_path"]
    by_source = {}
    for row in found:
        by_source.setdefault(row["source_key"], []).append(row)
    assert {row["origin_status"] for row in by_source["channel_feed"]} == {"official_notice"}
    assert {row["origin_status"] for row in by_source["blog_feed"]} == {"unverified"}
    assert {row["origin_status"] for row in by_source["openrouter_endpoints_gpt_5_6_sol"]} == {"channel_stated"}
    blog = by_source["blog_feed"][0]
    assert (blog["model_label"], blog["model_match"], blog["claim_kinds"], blog["date_phrases"]) == ("GPT-5.6", "family", "price_increase", "for November")
    assert blog["model_ids"] == "gpt-5.6-sol;gpt-5.6-terra;gpt-5.6-luna;gpt-5.6"  # the gpt-5.6 alias joined the family (2026-10-28)
    assert all(row["linked_event"] == "" and row["next_check"] == "" for row in found)
    # Price CSVs are untouched: signals live under their own directory.
    assert not (tmp_path / "runs").exists()


def test_window_excludes_old_entries(tmp_path):
    run = signals.live(tmp_path, 1, specs=[ATOM], fetcher=fixture_fetcher)
    _, sources, found = signals.validated_run(run)
    assert sources[0]["entries_total"] == "3" and sources[0]["entries_in_window"] == "0" and found == []


def test_tampered_raw_is_refused(tmp_path):
    run = signals.live(tmp_path, 3650, specs=[ATOM], fetcher=fixture_fetcher)
    raw = run / rows(run / "signal_sources.csv")[0]["raw_path"]
    raw.write_bytes(raw.read_bytes() + b" ")
    with pytest.raises(CollectionError, match="integrity"):
        signals.validated_run(run)


def test_replay_reparses_raw_and_history_merges_live_runs(tmp_path):
    first = signals.live(tmp_path, 3650, specs=[ATOM, API], fetcher=fixture_fetcher)
    second = signals.live(tmp_path, 3650, specs=[ATOM, API], fetcher=fixture_fetcher)
    replayed = signals.replay(first, tmp_path)
    assert json.loads((replayed / "manifest.json").read_text())["origin_run_id"] == first.name
    assert [row["signal_key"] for row in rows(replayed / "signals.csv")] == [row["signal_key"] for row in rows(first / "signals.csv")]
    out = signals.history(tmp_path, tmp_path / "history")
    merged = rows(out / "signal_history.csv")
    assert len(merged) == len(rows(first / "signals.csv"))
    assert {row["seen_runs"] for row in merged} == {"2"}  # replay runs are not counted
    assert all(row["first_seen_utc"] <= row["last_seen_utc"] for row in merged)
    with pytest.raises(CollectionError, match="exists"):
        signals.history(tmp_path, tmp_path / "history")
    assert second.exists()


def test_cli_exit_codes_and_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(signals, "signal_sources", lambda: [ATOM, BLOG])
    monkeypatch.setitem(signals.live.__kwdefaults__, "fetcher", fixture_fetcher)
    assert cli.main(["live", "--root", str(tmp_path), "--lookback-days", "3650"]) == 0
    assert cli.main(["live", "--root", str(tmp_path), "--disable-source", "nope"]) == 2
    assert cli.main(["summary", "--root", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "## External signals" in text and "Failed sources: none" in text and "GPT-5.6 Sol" in text
