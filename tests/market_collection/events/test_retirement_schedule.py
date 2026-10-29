"""Retirement schedule rows -> dated model_end events on the route owner's route (2026-10-29).

The raw bodies are the four pages of a local live signals run of 2026-10-29, trimmed to their tables
(tests/market_collection/signals/fixtures/retirements/provenance.json). Each signal run is written with a fixed
creation time, because the build day decides which rows are near enough to become events.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from scripts.market_collection.events.engine import RETIREMENT_HORIZON_DAYS, classify_claim
from scripts.market_collection.signals.registry import RETIREMENT_PAGES
from scripts.market_collection.signals.retirements import retirement_signals

from test_catalog_and_diff import ok_build, write_signal_run

FIXTURES = Path(__file__).parents[1] / "signals" / "fixtures" / "retirements"
AZURE_PAGE = "https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule"
# (model, route, version, date, date basis, replacement) of every row within 90 days of 2026-10-29, or already past.
# Anthropic's Active models (claude-opus-4-5-20251101: "Not sooner than November 24, 2026") have no row: a minimum lifetime.
EXPECTED = {
    ("o1", "azure_foundry", "2024-12-17", "2026-11-19", "stated", "gpt-5.6-sol"),
    ("o3", "azure_foundry", "2025-04-16", "2026-11-19", "stated", "gpt-5.6-sol"),
    ("o3-mini", "azure_foundry", "2025-01-31", "2026-11-19", "stated", "gpt-5.6-terra"),
    ("o3-pro", "azure_foundry", "2025-06-10", "2026-11-19", "stated", "gpt-5.6-sol"),
    ("o4-mini", "azure_foundry", "2025-04-16", "2026-11-19", "stated", "gpt-5.6-terra"),
    ("gpt-4o", "azure_foundry", "2024-05-13", "2026-12-09", "stated", "gpt-5.6-sol"),
    ("claude-haiku-4-5", "azure_foundry", "1", "2026-11-15", "stated", None),
    ("claude-haiku-4-5", "azure_foundry", "2", "2026-11-15", "stated", None),
    ("gpt-5", "openai_direct", "", "2026-12-11", "stated", "gpt-5.6-sol"),
    ("gpt-5-mini", "openai_direct", "", "2026-12-11", "stated", "gpt-5.6-terra"),
    ("gpt-5-nano", "openai_direct", "", "2026-12-11", "stated", "gpt-5.6-luna"),
    ("o3", "openai_direct", "", "2026-12-11", "stated", "gpt-5.6-sol"),
    ("o3-pro", "openai_direct", "", "2026-12-11", "stated", "gpt-5.6-sol (reasoning.mode: pro)"),
    ("gpt-4.1-nano", "openai_direct", "", "2026-10-23", "stated", "gpt-5.6-luna"),
    ("o1", "openai_direct", "", "2026-10-23", "stated", "gpt-5.6-sol"),
    ("o3-mini", "openai_direct", "", "2026-10-23", "stated", "gpt-5.6-sol"),
    ("o4-mini", "openai_direct", "", "2026-10-23", "stated", "gpt-5.6-terra"),
    ("gpt-5-codex", "openai_direct", "", "2026-07-23", "stated", "gpt-5.6-sol"),
    ("gpt-5.1-codex", "openai_direct", "", "2026-07-23", "stated", "gpt-5.6-sol"),
    ("gpt-5.1-codex-mini", "openai_direct", "", "2026-07-23", "stated", "gpt-5.6-terra"),
    ("claude-sonnet-4-5-20250929", "anthropic_direct", "", "2026-11-30", "stated", "claude-sonnet-5-5"),
    ("gemini-3.1-flash-image-preview", "google_direct", "", "2026-06-25", "not_before", "gemini-nano-banana-2.1"),
    ("gemini-3-pro-image-preview", "google_direct", "", "2026-06-25", "not_before", "gemini-3-pro-image"),
}


def bodies(**changes: tuple[bytes, bytes]) -> dict[str, bytes]:
    found = {spec.source_key: (FIXTURES / f"{spec.source_key}.md").read_bytes() for spec in RETIREMENT_PAGES}
    for key, (old, new) in changes.items():
        assert found[key].count(old) == 1
        found[key] = found[key].replace(old, new)
    return found


def schedule_run(root: Path, run_id: str, created_at: str, pages: dict[str, bytes] | None = None) -> Path:
    """A signal run of the four registered pages with the rows their registered parsers give."""
    pages = pages or bodies()
    return write_signal_run(root, run_id, created_at, [(asdict(spec), pages[spec.source_key],
                                                        retirement_signals(spec.parser, pages[spec.source_key], spec.url)[1])
                                                       for spec in RETIREMENT_PAGES])


def ends(events: dict) -> dict[tuple, dict]:
    return {(e["model_id"], e["route"], e["conditions"].get("model_version", ""), e["dates"]["effective_from"], e["dates"]["date_basis"],
             e["change"]["replacement"]): e for e in events["events"] if e["claim_kind"] == "retirement"}


def claim(**fields) -> dict:
    """The Gemini table's row of gemini-2.5-flash-image: an earliest date on Google's own route."""
    return {"kind": "retirement", "source_key": "google_deprecations", "authority": "manufacturer_official", "route": "google_direct",
            "scope": ["model_lifecycle", "route_price", "route_availability"], "effective_from": "2027-03-15", "date_basis": "not_before",
            "date_text": "March 15, 2027", "replacement": "gemini-3.1-flash-lite-image", "versions": 1, "published_at_utc": "",
            "quote": "Nano Banana models: Model: `gemini-2.5-flash-image`; Release date: October 2, 2025; Shutdown date: March 15, 2027; "
                     "Recommended replacement: `gemini-3.1-flash-lite-image` (page note: the shutdown dates indicate the earliest possible dates)",
            **fields}


def test_a_route_owners_dated_row_is_a_confirmed_end_on_that_route():
    event_type, subtags, status, gaps, extra = classify_claim(claim())
    assert (event_type, subtags, status) == ("model_end", [], "confirmed")
    assert extra["dates"]["effective_from"] == "2027-03-15" and extra["dates"]["date_basis"] == "not_before"
    assert extra["dates"]["date_text"] == "March 15, 2027" and extra["change"] == {"replacement": "gemini-3.1-flash-lite-image"}
    assert [(g["field"], g["class"]) for g in gaps] == [("earliest_date", "limits_value"), ("shutdown_confirmed", "limits_value")]
    # a deprecated Anthropic row's "Not sooner than" qualifies its date; it is not read as a negation
    deprecated = claim(quote="Model status: API model name: x; Current state: Deprecated; Tentative retirement date: Not sooner than March 15, 2027")
    assert [g["field"] for g in classify_claim(deprecated)[3]] == ["earliest_date", "shutdown_confirmed"]
    versions = classify_claim(claim(date_basis="stated", versions=3, replacement="gpt-5.6-sol"))
    assert [g["field"] for g in versions[3]] == ["deployment_version", "shutdown_confirmed"]
    assert versions[4]["change"] == {"replacement": "gpt-5.6-sol"} and "3 versions" in versions[3][0]["reason"]
    for weaker in (claim(authority="unofficial", scope=[]), claim(route=""), claim(effective_from=None)):
        assert classify_claim(weaker)[2] == "candidate"


def test_rows_within_ninety_days_or_past_become_end_events_on_the_pages_routes(tmp_path: Path):
    assert RETIREMENT_HORIZON_DAYS == 90
    run = schedule_run(tmp_path / "data", "20261029T144759Z-" + "1" * 32, "2026-10-29T14:47:59Z")
    events = ok_build(tmp_path / "out", run)
    found = ends(events)
    assert set(found) == EXPECTED
    assert {e["event_type"] for e in found.values()} == {"model_end"} and {e["status"] for e in found.values()} == {"confirmed"}
    o3 = found[("o3", "azure_foundry", "2025-04-16", "2026-11-19", "stated", "gpt-5.6-sol")]
    assert [(s["source_key"], s["authority"]) for s in o3["sources"]] == [("azure_retirement_schedule", "channel_official")]
    assert o3["evidence_refs"][0]["url"] == AZURE_PAGE and o3["evidence_refs"][0]["locator"] == "Azure OpenAI/row[66]"
    assert o3["dates"]["published"] is None and [g["field"] for g in o3["gaps"]] == ["shutdown_confirmed"]
    assert o3["sources"][0]["quote"].startswith("Azure OpenAI: Model: o3; Version: 2025-04-16; Lifecycle: Deprecated;")
    gpt4o = found[("gpt-4o", "azure_foundry", "2024-05-13", "2026-12-09", "stated", "gpt-5.6-sol")]
    assert [g["field"] for g in gpt4o["gaps"]] == ["deployment_version", "shutdown_confirmed"]  # Azure lists three gpt-4o versions
    sonnet = found[("claude-sonnet-4-5-20250929", "anthropic_direct", "", "2026-11-30", "stated", "claude-sonnet-5-5")]
    assert sonnet["dates"]["published"] == "2026-09-30" and sonnet["sources"][0]["authority"] == "manufacturer_official"
    assert found[("o3", "openai_direct", "", "2026-12-11", "stated", "gpt-5.6-sol")]["dates"]["published"] == "2026-06-11"
    # further out than 90 days: no event yet (Azure's GA term of gpt-5, Gemini 3.1 Flash Lite); an Active Anthropic model never
    assert not any(e["model_id"] in ("gpt-5.6-sol", "claude-opus-4-5-20251101", "gemini-3.1-flash-lite") for e in found.values())
    assert ("gpt-5", "azure_foundry") not in {(e["model_id"], e["route"]) for e in found.values()}


def test_the_same_rows_again_change_nothing_and_a_changed_date_revises_the_same_event(tmp_path: Path):
    first = ok_build(tmp_path / "first", schedule_run(tmp_path / "data", "20261029T144759Z-" + "1" * 32, "2026-10-29T14:47:59Z"))
    state = ("--state", str(tmp_path / "first" / "state.json"))
    ok_build(tmp_path / "again", schedule_run(tmp_path / "data", "20261030T144759Z-" + "2" * 32, "2026-10-30T14:47:59Z"), extra=state)
    report = json.loads((tmp_path / "again" / "report.json").read_text())
    assert report["new_events"] == [] and report["revised_events"] == []
    later = bodies(azure_retirement_schedule=(b"| o3 | 2025-04-16 | Deprecated | 2026-11-19 |", b"| o3 | 2025-04-16 | Deprecated | 2026-12-19 |"))
    events = ok_build(tmp_path / "moved", schedule_run(tmp_path / "data", "20261031T144759Z-" + "3" * 32, "2026-10-31T14:47:59Z", later),
                      extra=("--state", str(tmp_path / "again" / "state.json")))
    report = json.loads((tmp_path / "moved" / "report.json").read_text())
    old = ends(first)[("o3", "azure_foundry", "2025-04-16", "2026-11-19", "stated", "gpt-5.6-sol")]
    moved = ends(events)[("o3", "azure_foundry", "2025-04-16", "2026-12-19", "stated", "gpt-5.6-sol")]
    assert report["new_events"] == [] and report["revised_events"] == [old["event_id"]] == [moved["event_id"]]
    assert moved["revision"] == 2 and moved["history"][-1]["reason"].endswith("(date 2026-11-19 -> 2026-12-19)")
    assert len(ends(events)) == len(EXPECTED)


def test_a_row_further_out_waits_for_a_build_day_within_ninety_days(tmp_path: Path):
    ok_build(tmp_path / "first", schedule_run(tmp_path / "data", "20261029T144759Z-" + "1" * 32, "2026-10-29T14:47:59Z"))
    events = ok_build(tmp_path / "later", schedule_run(tmp_path / "data", "20261112T144759Z-" + "4" * 32, "2026-11-12T14:47:59Z"),
                      extra=("--state", str(tmp_path / "first" / "state.json")))
    added = set(ends(events)) - EXPECTED
    # 2026-11-12 + 90 days = 2027-02-10, that day included: Azure's gpt-5 family, claude-opus-4-6 and claude-sonnet-4-6 (2/10).
    # Anthropic's claude-opus-4-6 is Active ("Not sooner than February 5, 2027"), so its route gets no event.
    assert added == {("gpt-5", "azure_foundry", "2025-08-07", "2027-02-09", "stated", None),
                     ("gpt-5-mini", "azure_foundry", "2025-08-07", "2027-02-09", "stated", None),
                     ("gpt-5-nano", "azure_foundry", "2025-08-07", "2027-02-09", "stated", None),
                     ("claude-opus-4-6", "azure_foundry", "1", "2027-02-02", "stated", None),
                     ("claude-sonnet-4-6", "azure_foundry", "1", "2027-02-10", "stated", None)}
