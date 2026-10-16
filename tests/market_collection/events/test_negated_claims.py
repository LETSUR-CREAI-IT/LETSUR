"""Negated or cancelled discount/condition sentences are never confirmed, even from an official publisher.

Feed bodies are SOURCE-SHAPED SYNTHETIC fixtures in the shape of the registered
vercel_changelog Atom source, run through signals.live -> build. Each negated
sentence sits next to the same claim stated positively, which must still confirm.
"""

from __future__ import annotations

import pytest

from scripts.market_collection.events.engine import _states_change, classify_claim
from scripts.market_collection.events.notices import FROM_TO
from scripts.market_collection.models import FABLE_MODEL, GOOGLE_LLM_MODEL

from test_catalog_and_diff import RUN_A, RUN_B, google_entry, ok_build, write_run
from test_signal_identity import _manual
from test_notice_auto import SPECS, atom, of
from scripts.market_collection import signals

CANCELLED = "The 50% off promotion on Gemini 3.8 Flash through December 31, 2026 has been cancelled."
UNCHANGED = "The Claude Fable 5.1 cache read multiplier on AI Gateway will not change from 0.25x to 0.1x."
BODY = atom(
    ("gemini-cancel", "2026-10-12T18:00:00Z", "Promotion update", CANCELLED),
    ("gemini-sale", "2026-10-12T16:00:00Z", "Gemini 3.8 Flash promotion",
     "Gemini 3.8 Flash is 50% off on AI Gateway through November 30, 2026."),
    ("fable-no-change", "2026-10-13T18:00:00Z", "Cache pricing note", UNCHANGED),
    ("fable-cache", "2026-10-13T16:00:00Z", "Claude Fable 5.1 cache pricing",
     "The Claude Fable 5.1 cache read multiplier on AI Gateway changes from 0.25x to 0.1x of the input price."),
)


def quoted(events: list[dict], text: str) -> list[dict]:
    return [e for e in events if any(text in s.get("quote", "") for s in e["sources"])]


def test_negated_discount_and_condition_stay_candidates(tmp_path):
    run = signals.live(tmp_path / "data", lookback_days=3650, specs=[SPECS["vercel_changelog"]],
                       fetcher=lambda spec: (BODY, spec.url, 200))
    events = ok_build(tmp_path / "out", run)
    sales = of(events, "limited_discount", GOOGLE_LLM_MODEL)
    cancelled = quoted(sales, "has been cancelled")
    assert cancelled and all(e["status"] == "candidate" for e in cancelled)
    assert all(any(g["field"] == "negated_statement" for g in e["gaps"]) for e in cancelled)
    [sale] = quoted(sales, "is 50% off on AI Gateway")
    assert sale["status"] == "confirmed" and sale["dates"]["end_date"] == "2026-11-30"
    conditions = of(events, "condition_change", FABLE_MODEL)
    unchanged = quoted(conditions, "will not change")
    assert unchanged and all(e["status"] == "candidate" for e in unchanged)
    assert all(any(g["field"] == "negated_statement" for g in e["gaps"]) for e in unchanged)
    [cache] = quoted(conditions, "changes from 0.25x to 0.1x")
    assert cache["status"] == "confirmed" and cache["after"] == {"value": "0.1x"}


def test_negated_price_lead_does_not_state_the_observed_change():
    event = {"change": {"direction": "down"}, "after": {"amount": "2.50"}}
    assert _states_change("price_decrease", "Input drops from $3.00 to $2.50 per 1M tokens.", event)
    assert not _states_change("price_decrease", "Input will not drop from $3.00 to $2.50 per 1M tokens.", event)
    assert not _states_change("price_decrease", "The cut from $3.00 to $2.50 has been cancelled.", event)


@pytest.mark.parametrize("kind, positive, negative", [
    ("discount", "Gemini 3.8 Flash is 50% off through December 31, 2026.", CANCELLED),
    ("condition", "The cache multiplier changes from 0.25x to 0.1x.", UNCHANGED),
])
def test_shared_classification_guards_untyped_claims(kind, positive, negative):
    claim = {"kind": kind, "authority": "channel_official", "route": "vercel_ai_gateway",
             "scope": ["route_price"], "condition_key": "cache_multiplier"}
    assert classify_claim(dict(claim, quote=positive))[2] == "confirmed"
    _, _, status, gaps, _ = classify_claim(dict(claim, quote=negative))
    assert status == "candidate" and any(g["field"] == "negated_statement" for g in gaps)


def test_condition_values_allow_sentence_period_but_not_partial_numbers_or_words():
    match = FROM_TO.search(UNCHANGED)
    assert match and match.group(2) == "0.1x"
    match = FROM_TO.search("from 0.25x to 0.125x.")
    assert match and match.group(2) == "0.125x"
    assert not FROM_TO.search("from 0.25x to 0.1xyz.")
    assert not FROM_TO.search("from 0.25x to 0.1x.2")


@pytest.mark.parametrize("lead_first", [True, False])
def test_negated_lead_cannot_absorb_or_attach_to_observed_change(tmp_path, lead_first):
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.119"))])
    quote = "Gemini 3.8 Flash will not drop from $0.30 to $0.119 per 1M input tokens."
    manual = _manual(tmp_path / "lead.json", ("price_decrease", "google_direct", quote))
    if lead_first:
        ok_build(tmp_path / "first", run_a, extra=("--manual", manual))
        later = ok_build(tmp_path / "later", run_b, extra=("--state", str(tmp_path / "first" / "state.json")))
    else:
        ok_build(tmp_path / "first", run_a, run_b)
        later = ok_build(tmp_path / "later", run_b,
                         extra=("--state", str(tmp_path / "first" / "state.json"), "--manual", manual))
    [lead] = quoted(later["events"], quote)
    assert lead["status"] == "candidate" and lead["revision"] == 1 and len(lead["sources"]) == 1
    [observed] = [e for e in later["events"] if e["event_type"] == "price_change" and e["status"] == "confirmed"]
    assert observed["event_id"] != lead["event_id"]
    assert not quoted([observed], quote)
    assert all(r["event_id"] != lead["event_id"] for r in observed["related"])


@pytest.mark.parametrize("kind", ["deprecation", "route_removal", "announced_future_rate", "endpoint_deprecation", "availability"])
def test_other_typed_claims_cannot_confirm_a_negated_statement(kind):
    claim = {"kind": kind, "quote": "The announced change has been cancelled.",
             "authority": "channel_official", "route": "vercel_ai_gateway", "scope": ["route_price", "model_lifecycle"],
             "source_key": "openrouter_endpoints_test", "locator": "data.expiration_date", "effective_from": "2026-11-30",
             "after_amount": "0.1", "model_wide": True, "maker_first_release_date": "2026-10-13", "route_added_date": "2026-10-13"}
    _, _, status, gaps, _ = classify_claim(claim)
    assert status == "candidate" and any(g["field"] == "negated_statement" for g in gaps)
