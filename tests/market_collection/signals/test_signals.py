import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.market_collection.core import CollectionError
from scripts.market_collection import signals
from scripts.market_collection.signals import __main__ as cli
from scripts.market_collection.signals.matching import claims_in, date_phrases, entry_signals, models_in, normalize
from scripts.market_collection.signals.parsers import SignalParseError, atom_entries, openrouter_endpoint_signals, rss_entries
from scripts.market_collection.signals.registry import SignalSourceSpec, signal_sources, validate

FIXTURES = Path(__file__).parent / "fixtures"
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
    assert blog["model_ids"] == "gpt-5.6-sol;gpt-5.6-terra;gpt-5.6-luna"
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
