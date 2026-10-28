"""Vercel endpoint deprecated_at -> one upstream's end on the Vercel route, a candidate (2026-10-28).

The raw body is the real Vercel listing of minimax/minimax-m2 from the 10/28 core run
(tests/market_collection/llm/fixtures/endpoints/provenance.json), read through the registered
signal spec by signals.live with a controlled fetcher, then the events build.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from scripts.market_collection import signals
from scripts.market_collection.events.catalog import Catalog
from scripts.market_collection.events.engine import classify_claim
from scripts.market_collection.signals.registry import signal_sources

from test_catalog_and_diff import ok_build

SPECS = {spec.source_key: spec for spec in signal_sources()}
LISTING = Path(__file__).parents[1] / "llm" / "fixtures" / "endpoints" / "vercel-minimax-m2.json"


def test_vercel_listing_speaks_for_the_vercel_route_and_openrouter_for_its_own():
    catalog = Catalog()
    vercel = catalog.signal_source(asdict(SPECS["vercel_endpoints_minimax_m2"]), "minimax-m2")
    assert vercel == {"role": "api", "authority": "channel_official", "publisher": "Vercel", "route": "vercel_ai_gateway",
                      "scope": ["route_price", "route_availability"]}
    openrouter = catalog.signal_source(asdict(SPECS["openrouter_endpoints_minimax_m2"]), "minimax-m2")
    assert (openrouter["publisher"], openrouter["route"], openrouter["authority"]) == ("OpenRouter", "openrouter", "channel_official")
    entries = {s["source_key"]: s for m in catalog.as_json()["models"] if m["model_id"] == "minimax-m2" for s in m["sources"]}
    assert entries["vercel_endpoints_minimax_m2"]["route"] == "vercel_ai_gateway"
    assert entries["vercel_endpoints_minimax_m2"]["registry"] == "SignalSourceSpec"


def claim(**fields) -> dict:
    return {"kind": "endpoint_deprecation", "source_key": "vercel_endpoints_minimax_m2", "locator": "data.endpoints[0].deprecated_at",
            "date_phrases": "2026-11-18", "authority": "channel_official", "route": "vercel_ai_gateway",
            "scope": ["route_price", "route_availability"], "endpoint_id": "minimax",
            "quote": "minimax: deprecated_at=1794960000000 (2026-11-18T00:00:00Z), status=1; "
                     "deprecated_at is absent from Vercel's API documentation, which defines only status 0 (active)", **fields}


def test_deprecated_at_is_a_route_removal_candidate_dated_by_the_field():
    event_type, subtags, status, gaps, extra = classify_claim(claim())
    assert (event_type, subtags, status) == ("route_change", ["remove"], "candidate")
    assert extra["dates"]["effective_from"] == "2026-11-18" and extra["dates"]["published"] is None
    assert "one upstream endpoint" in extra["dates"]["note"] and "not a route or model shutdown" in extra["dates"]["note"]
    assert [(gap["field"], gap["class"]) for gap in gaps] == [("undocumented_field", "check_first"), ("shutdown_confirmed", "limits_value")]
    assert "absent from Vercel's API documentation" in gaps[0]["reason"]
    # The quote's own words are not read as a negation.
    assert all(gap["field"] != "negated_statement" for gap in gaps)
    # Another locator of the same source is not the deprecated_at field; it keeps the common endpoint rule.
    assert classify_claim(claim(locator="data.endpoints[0].status"))[2] == "confirmed"


def test_a_vercel_listing_becomes_one_candidate_card_that_names_the_upstream(tmp_path):
    body = LISTING.read_bytes()
    run = signals.live(tmp_path / "data", specs=[SPECS["vercel_endpoints_minimax_m2"]], fetcher=lambda spec: (body, spec.url, 200))
    events = ok_build(tmp_path / "out", run)["events"]
    [event] = events
    assert (event["event_type"], event["subtags"], event["status"], event["model_id"], event["route"]) == (
        "route_change", ["remove"], "candidate", "minimax-m2", "vercel_ai_gateway")
    assert event["endpoint_id"] == "minimax" and event["claim_kind"] == "endpoint_deprecation"
    assert event["dates"]["effective_from"] == "2026-11-18" and event["evidence_level"] == "official"
    assert event["evidence_refs"][0]["locator"] == "data.endpoints[0].deprecated_at"
    assert event["evidence_refs"][0]["url"] == "https://ai-gateway.vercel.sh/v1/models/minimax/minimax-m2/endpoints"
    assert [(s["source_key"], s["authority"]) for s in event["sources"]] == [("vercel_endpoints_minimax_m2", "channel_official")]
    # The next day's same listing is a re-observation: no new event, no revision.
    again = signals.live(tmp_path / "data", specs=[SPECS["vercel_endpoints_minimax_m2"]], fetcher=lambda spec: (body, spec.url, 200))
    later = ok_build(tmp_path / "later", again, extra=("--state", str(tmp_path / "out" / "state.json")))
    assert [(e["event_id"], e["revision"]) for e in later["events"]] == [(event["event_id"], 1)]
    report = json.loads((tmp_path / "later" / "report.json").read_text())
    assert report["new_events"] == [] and report["revised_events"] == []
