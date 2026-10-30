"""Official retirement schedule pages -> dated entries of registered models (2026-10-29).

The fixtures are the four pages as a local live signals run fetched them on 2026-10-29, trimmed to their headings and
tables (fixtures/retirements/provenance.json); the parser reads them exactly as it reads the whole pages.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.market_collection import signals
from scripts.market_collection.events.catalog import Catalog
from scripts.market_collection.signals.parsers import SignalParseError
from scripts.market_collection.signals.registry import RETIREMENT_PAGES, RETIREMENT_PARSERS, signal_sources
from scripts.market_collection.signals.retirements import day, page_url, read, retirement_signals

FIXTURES = Path(__file__).parent / "fixtures" / "retirements"
SPECS = {spec.source_key: spec for spec in RETIREMENT_PAGES}


def body(key: str) -> bytes:
    return (FIXTURES / f"{key}.md").read_bytes()


def entries(key: str) -> dict[tuple[str, str], tuple]:
    _, found = read(SPECS[key].parser, body(key))
    return {(e.model_id, e.version): (e.state, e.date, e.earliest, e.date_text, e.replacement, e.announced) for e in found}


def test_the_date_forms_the_pages_write():
    assert [day(text) for text in ("2026-11-19", "November 24, 2026", "Not sooner than November 24, 2026", "Apr 1, 2027", "Sept 3, 2026",
                                   "2026‑03‑26", "No earlier than 2027-04-14^1^")] == [
        "2026-11-19", "2026-11-24", "2026-11-24", "2027-04-01", "2026-09-03", "2026-03-26", "2027-04-14"]
    assert [day(text) for text in ("No shutdown date announced", "N/A", "To be announced", "—", "February 30, 2026", "Smarch 3, 2026")] == [""] * 6


def test_azure_rows_are_per_model_version_and_only_exact_names_map():
    found = entries("azure_retirement_schedule")
    assert len(found) == 48
    for model in ("o1", "o3", "o3-mini", "o3-pro", "o4-mini"):
        assert [rest[:2] for (m, _), rest in found.items() if m == model] == [("Deprecated", "2026-11-19")]  # one version each
    assert found[("o3", "2025-04-16")] == ("Deprecated", "2026-11-19", False, "2026-11-19", "gpt-5.6-sol", "")
    assert found[("o4-mini", "2025-04-16")][4] == "gpt-5.6-terra"
    # three gpt-4o versions, each with its own date; LETSUR's deployment version is unknown
    assert {v: rest[1] for (m, v), rest in found.items() if m == "gpt-4o"} == {
        "2024-05-13": "2026-12-09", "2024-08-06": "2027-04-14", "2024-11-20": "2027-04-14"}
    assert found[("gpt-4o-mini", "2024-07-18")][4] == ""  # "—": no replacement stated
    # a dated id we register is the model name plus its version; an undated Claude id that only shares a prefix is not
    assert found[("gpt-5.4-2026-03-05", "2026-03-05")][1] == found[("gpt-5.4", "2026-03-05")][1] == "2027-09-02"
    assert found[("claude-haiku-4-5", "1")][1] == found[("claude-haiku-4-5", "2")][1] == "2026-11-15"
    models = {m for m, _ in found}
    for skipped in ("claude-opus-4-5-20251101", "claude-sonnet-4-5-20250929", "kimi-k2.5", "deepseek-v4-flash-0731", "gpt-5.5-2026-04-23"):
        assert skipped not in models  # Azure writes claude-opus-4-5, Kimi-K2.5, DeepSeek-V4-Flash-0731, gpt-5.5 version 2026-04-24


def test_openai_rows_take_the_backticked_ids_and_the_sections_announcement_day():
    found = entries("openai_deprecations")
    assert len(found) == 14
    assert found[("o3", "")] == ("", "2026-12-11", False, "Dec 11, 2026", "gpt-5.6-sol", "2026-06-11")
    assert found[("o3-pro", "")][4] == "gpt-5.6-sol (reasoning.mode: pro)"
    assert found[("o1", "")][1:2] + found[("o1", "")][5:] == ("2026-10-23", "2026-04-22")
    assert found[("gpt-5.1-codex-mini", "")][1] == "2026-07-23" and found[("gpt-5.4-nano", "")][1] == "2027-04-01"
    # "GPT-4o (`gpt-4o-2024-08-06`, `gpt-4o-2024-11-20`)" names the snapshots, never the id gpt-4o
    assert not {"gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"} & {m for m, _ in found}


def test_anthropic_counts_only_deprecated_or_retired_models_and_takes_the_replacement_from_the_history():
    # An Active model's "Not sooner than" date is a minimum lifetime, not an announced retirement (2026-10-30): the 12 Active
    # models we register, claude-opus-4-5-20251101 among them, are no entries.
    assert entries("anthropic_deprecations") == {
        ("claude-sonnet-4-5-20250929", ""): ("Deprecated", "2026-11-30", False, "November 30, 2026", "claude-sonnet-5-5", "2026-09-30")}
    # once Anthropic deprecates a model its row counts, and a "Not sooner than" date there stays an earliest date
    deprecated = body("anthropic_deprecations").replace(b"| claude-opus-4-5-20251101   | Active        | N/A                |",
                                                         b"| claude-opus-4-5-20251101   | retired       | October 30, 2026   |")
    found = {(e.model_id, e.state, e.date, e.earliest, e.announced) for e in read("anthropic_deprecations", deprecated)[1]}
    assert ("claude-opus-4-5-20251101", "retired", "2026-11-24", True, "2026-10-30") in found and len(found) == 2


def test_google_dates_are_earliest_dates_and_undated_rows_are_left_out():
    found = entries("google_deprecations")
    assert found == {
        ("gemini-3.1-flash-lite", ""): ("", "2027-05-07", True, "May 7, 2027", "gemini-3.5-flash-lite", ""),
        ("gemini-2.5-flash-image", ""): ("", "2027-03-15", True, "March 15, 2027", "gemini-3.1-flash-lite-image", ""),
        ("gemini-3.1-flash-image-preview", ""): ("", "2026-06-25", True, "June 25, 2026", "gemini-nano-banana-2.1", ""),
        ("gemini-3-pro-image-preview", ""): ("", "2026-06-25", True, "June 25, 2026", "gemini-3-pro-image", "")}
    # without the page's note the dates would be read as stated
    note = body("google_deprecations").replace(b"earliest possible", b"planned")
    assert not any(entry.earliest for entry in read("google_deprecations", note)[1])


@pytest.mark.parametrize("parser", sorted(RETIREMENT_PARSERS))
def test_a_body_without_the_pages_tables_is_refused(parser: str):
    for refused in (b"<!doctype html><html><body>sign in</body></html>", b"", b"| a | b |\n|---|---|\n| c | d |\n", b"\xff\xfe"):
        with pytest.raises(SignalParseError):
            read(parser, refused)


def test_the_four_pages_are_registered_last_and_speak_for_their_owners_route():
    assert signal_sources()[-4:] == RETIREMENT_PAGES
    assert [(s.source_key, s.source_kind, s.parser) for s in RETIREMENT_PAGES] == [
        ("azure_retirement_schedule", "channel_notice", "azure_retirements"), ("openai_deprecations", "supplier_notice", "openai_deprecations"),
        ("anthropic_deprecations", "supplier_notice", "anthropic_deprecations"), ("google_deprecations", "supplier_notice", "google_deprecations")]
    assert [page_url(s.url) for s in RETIREMENT_PAGES] == [
        "https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule",
        "https://developers.openai.com/api/docs/deprecations", "https://platform.claude.com/docs/en/about-claude/model-deprecations",
        "https://ai.google.dev/gemini-api/docs/deprecations"]
    catalog = Catalog()
    seen = {(key, model): catalog.signal_source(asdict(SPECS[key]), model) for key, model in (
        ("azure_retirement_schedule", "o3"), ("azure_retirement_schedule", "claude-haiku-4-5"), ("openai_deprecations", "o3"),
        ("openai_deprecations", "gpt-image-2"), ("anthropic_deprecations", "claude-sonnet-4-5-20250929"), ("google_deprecations", "gemini-2.5-flash-image"))}
    assert {key: (info["authority"], info["route"]) for key, info in seen.items()} == {
        ("azure_retirement_schedule", "o3"): ("channel_official", "azure_foundry"),
        ("azure_retirement_schedule", "claude-haiku-4-5"): ("channel_official", "azure_foundry"),
        ("openai_deprecations", "o3"): ("manufacturer_official", "openai_direct"),
        ("openai_deprecations", "gpt-image-2"): ("manufacturer_official", "openai"),  # OpenAI sells its image models on route openai
        ("anthropic_deprecations", "claude-sonnet-4-5-20250929"): ("manufacturer_official", "anthropic_direct"),
        ("google_deprecations", "gemini-2.5-flash-image"): ("manufacturer_official", "google_direct")}
    assert all("route_availability" in info["scope"] for info in seen.values())
    # a maker's news feed still speaks for the model, not a route
    news = next(s for s in signal_sources() if s.source_key == "openai_news")
    assert catalog.signal_source(asdict(news), "o3")["route"] == ""


def test_live_reads_each_page_through_its_registered_spec(tmp_path: Path):
    run = signals.live(tmp_path, specs=RETIREMENT_PAGES, fetcher=lambda spec: (body(spec.source_key), spec.url, 200))
    _, sources, rows = signals.validated_run(run)
    assert [(s["source_key"], s["parse_status"], s["entries_total"], s["signal_count"]) for s in sources] == [
        ("azure_retirement_schedule", "ok", "173", "48"), ("openai_deprecations", "ok", "151", "14"),
        ("anthropic_deprecations", "ok", "23", "1"), ("google_deprecations", "ok", "102", "4")]
    o3 = next(row for row in rows if row["source_key"] == "azure_retirement_schedule" and row["model_ids"] == "o3")
    assert {key: o3[key] for key in ("entry_id", "entry_url", "entry_title", "published_at_utc", "model_match", "claim_kinds", "date_phrases",
                                      "match_terms", "origin_status", "locator")} == {
        "entry_id": "o3@2025-04-16", "entry_url": "https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule",
        "entry_title": "Azure OpenAI", "published_at_utc": "", "model_match": "exact", "claim_kinds": "retirement",
        "date_phrases": "2026-11-19", "match_terms": "Retirement date", "origin_status": "official_notice", "locator": "Azure OpenAI/row[66]"}
    assert o3["quote"] == ("Azure OpenAI: Model: o3; Version: 2025-04-16; Lifecycle: Deprecated; Retirement date: 2026-11-19; "
                           "Replacement: gpt-5.6-sol")
    # the announcement day stays off the rows (one page, many rows); the next run of the same pages gives the same signals
    assert {row["published_at_utc"] for row in rows} == {""}
    again = signals.live(tmp_path, specs=RETIREMENT_PAGES, fetcher=lambda spec: (body(spec.source_key), spec.url, 200))
    assert [row["signal_key"] for row in signals.validated_run(again)[2]] == [row["signal_key"] for row in rows]
    assert retirement_signals("google_deprecations", body("google_deprecations"), SPECS["google_deprecations"].url)[1][0]["quote"].endswith(
        "(page note: the shutdown dates indicate the earliest possible dates)")


def test_fixture_provenance_names_the_run_and_the_full_raws():
    provenance = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    assert [item["source_key"] for item in provenance] == list(SPECS)
    assert all(item["source_url"] == SPECS[item["source_key"]].url and len(item["raw_sha256"]) == 64 and
               item["fixture_bytes"] == len(body(item["source_key"])) for item in provenance)
