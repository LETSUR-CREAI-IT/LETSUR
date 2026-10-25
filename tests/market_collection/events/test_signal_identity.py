"""Claim identity and strong anchors through the real signal collector and build.

Feed bodies below are SOURCE-SHAPED SYNTHETIC fixtures: Atom feeds in the shape of
the registered vercel_changelog and modelpricewatch_news sources (entry ids, the
shared model-page link, and titles follow the preserved Oct 13/15 captures). They
are not live captures. They check that:
- one entry keeps every distinct stated claim (availability and a discount);
- articles sharing one entry_url stay separate events, and a re-observed sentence
  with a new feed ordinal is not a revision;
- a candidate is merged into an official change only on the same route, direction
  and after amount; a before amount or an unstated route never confirms it.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.market_collection import signals
from scripts.market_collection.models import ASTRA_MODEL, GOOGLE_LLM_MODEL
from scripts.market_collection.signals.registry import signal_sources

from test_catalog_and_diff import RUN_A, RUN_B, google_entry, ok_build, write_run

SPECS = {spec.source_key: spec for spec in signal_sources()}
DEEPSEEK = "deepseek-v4-pro"
GLM = "glm-5.3-flash"


def atom(*entries: tuple[str, str, str, str, str]) -> bytes:
    """entries are (id, link, published, title, html body)."""
    items = "".join(f'<entry><id>{entry_id}</id><link href="{link}"/><published>{published}</published>'
                    f'<title>{title}</title><content type="html">{body}</content></entry>'
                    for entry_id, link, published, title, body in entries)
    return f'<?xml version="1.0"?><!-- synthetic source-shaped fixture --><feed xmlns="http://www.w3.org/2005/Atom"><title>synthetic</title>{items}</feed>'.encode()


def collect(root: Path, bodies: dict[str, bytes]) -> Path:
    """Real signals.live over registered specs; the fetcher only returns the fixture body."""
    return signals.live(root, lookback_days=3650, specs=[SPECS[key] for key in bodies],
                        fetcher=lambda spec: (bodies[spec.source_key], spec.url, 200))


def tracker(ordinals: dict[str, int]) -> bytes:
    """modelpricewatch-shaped feed: entry id ends in a feed ordinal; the link is the model page, shared by articles."""
    rows = (("2026-09-09", "zhipu-glm-5-3-flash", "glm-5-3-flash", "GLM-5.3-Flash (Z.AI) — price up: $0.119 → $0.238/Mtok blended"),
            ("2026-09-06", "together-deepseek-v4-pro", "deepseek-v4-pro", "DeepSeek V4 Pro (Together) — price down: $2.175 → $1.98/Mtok blended"),
            ("2026-08-31", "fireworks-deepseek-v4-pro", "deepseek-v4-pro", "DeepSeek V4 Pro (Fireworks) — price down: $2.175 → $1.98/Mtok blended"),
            ("2026-08-16", "deepseek-v4-pro", "deepseek-v4-pro", "DeepSeek V4 Pro (DeepSeek) — price up: $0.544 → $1.98/Mtok blended"))
    ordered = sorted(rows, key=lambda row: ordinals[row[1]])
    return atom(*((f"tag:modelpricewatch.com,{day}:{slug}:price_change:{ordinals[slug]}", f"https://modelpricewatch.com/models/{page}/",
                   f"{day}T00:00:00Z", title, "") for day, slug, page, title in ordered))


def test_one_entry_keeps_availability_and_discount(tmp_path):
    """A typed availability sentence does not drop the same entry's discount sentence; a two-model article is held."""
    vercel = atom(
        ("https://vercel.com/changelog/gemini-3-8-flash-now-available-on-ai-gateway",
         "https://vercel.com/changelog/gemini-3-8-flash-now-available-on-ai-gateway", "2026-09-02T00:00:00Z",
         "Gemini 3.8 Flash now available on AI Gateway",
         "Gemini 3.8 Flash is now available on AI Gateway. The model is 50% off through December 31st."),
        ("https://vercel.com/changelog/two-models", "https://vercel.com/changelog/two-models", "2026-09-03T00:00:00Z",
         "Gemini 3.8 Flash and GPT-6 Astra on AI Gateway", "Both are on AI Gateway. The models are 50% off through December 31st."))
    events = ok_build(tmp_path / "out", collect(tmp_path / "data", {"vercel_changelog": vercel}))["events"]
    gemini = [e for e in events if e["model_id"] == GOOGLE_LLM_MODEL and "gemini-3-8-flash-now" in e["sources"][0]["lineage"]]
    [added] = [e for e in gemini if e["event_type"] is None]
    assert "is now available on AI Gateway" in added["sources"][0]["quote"] and added["route"] == "vercel_ai_gateway"
    [sale] = [e for e in gemini if e["event_type"] == "limited_discount"]
    assert sale["status"] == "confirmed" and sale["route"] == "vercel_ai_gateway"
    assert sale["sources"][0]["quote"] == "The model is 50% off through December 31st."
    assert sale["change"]["discount_pct"] == "50"
    # No year in the source text: none is invented, the phrase is kept and the gap is stated.
    assert sale["dates"]["end_date"] is None and sale["dates"]["end_phrase"] == "through December 31st"
    assert any(g["field"] == "end_date_year" for g in sale["gaps"])
    # The discount sentence names no model; in a two-model article it is not attributed to either.
    shared = [e for e in events if e["event_type"] == "limited_discount" and "two-models" in e["sources"][0]["lineage"]]
    assert {e["model_id"] for e in shared} == {GOOGLE_LLM_MODEL, ASTRA_MODEL}
    assert all(e["status"] == "candidate" and any(g["field"] == "model_attribution" for g in e["gaps"]) for e in shared)


def test_a_tracker_article_on_glm_5_3_flash_raises_no_event_for_glm_5_3(tmp_path):
    """glm-5.3 is registered beside glm-5.3-flash: the shorter name inside the longer one is not a mention of it."""
    run = collect(tmp_path / "data", {"modelpricewatch_news": tracker(
        {"zhipu-glm-5-3-flash": 48, "together-deepseek-v4-pro": 54, "fireworks-deepseek-v4-pro": 63, "deepseek-v4-pro": 91})})
    events = ok_build(tmp_path / "out", run)["events"]
    assert sorted({e["model_id"] for e in events if e["model_id"].startswith("glm")}) == [GLM]


def test_shared_entry_url_articles_stay_apart_and_reobservation_is_not_a_revision(tmp_path):
    """Up and down articles on one model page are separate events; a new feed ordinal on the same sentence changes nothing."""
    first_run = collect(tmp_path / "day1", {"modelpricewatch_news": tracker(
        {"zhipu-glm-5-3-flash": 48, "together-deepseek-v4-pro": 54, "fireworks-deepseek-v4-pro": 63, "deepseek-v4-pro": 91})})
    first = ok_build(tmp_path / "first", first_run)["events"]
    deepseek = [e for e in first if e["model_id"] == DEEPSEEK]
    assert len(deepseek) == 3 and all(e["event_type"] == "price_change" and e["status"] == "candidate" for e in deepseek)
    for event in deepseek:
        assert len(event["sources"]) == 1 and event["revision"] == 1
        assert not any(h["reason"].startswith("supporting") for h in event["history"])
    assert sorted(e["claim_kind"] for e in deepseek) == ["price_decrease", "price_decrease", "price_increase"]
    [glm] = [e for e in first if e["model_id"] == GLM]
    assert len(glm["sources"]) == 1
    # Next day: the same articles with new ordinals and a different order.
    second_run = collect(tmp_path / "day2", {"modelpricewatch_news": tracker(
        {"zhipu-glm-5-3-flash": 50, "together-deepseek-v4-pro": 49, "fireworks-deepseek-v4-pro": 65, "deepseek-v4-pro": 93})})
    later = ok_build(tmp_path / "later", second_run, extra=("--state", str(tmp_path / "first" / "state.json")))["events"]
    report = json.loads((tmp_path / "later" / "report.json").read_text())
    assert report["new_events"] == [] and report["revised_events"] == []
    assert later == first


def _manual(path: Path, *claims: tuple[str, str, str]) -> str:
    """claims are (kind, route, quote) from a person-captured unofficial source."""
    path.write_text(json.dumps([{"kind": kind, "model_id": GOOGLE_LLM_MODEL, "route": route, "quote": quote,
                                 "url": f"https://example.invalid/{index}", "role": "social"}
                                for index, (kind, route, quote) in enumerate(claims)]))
    return str(path)


def test_candidate_merges_only_on_route_direction_and_after_amount(tmp_path):
    """A before amount, an unstated route, or the opposite direction never confirms a lead; the strong anchor does."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.119"))])
    manual = _manual(tmp_path / "leads.json",
                     ("price_increase", "google_direct", "Gemini 3.8 Flash price up: $0.119 → $0.238 per 1M input tokens"),
                     ("price_decrease", "", "Gemini 3.8 Flash price cut to $0.119 per 1M input tokens"),
                     ("price_decrease", "google_direct", "Gemini 3.8 Flash price down: $0.30 → $0.119 per 1M input tokens"))
    first = ok_build(tmp_path / "first", run_a, extra=("--manual", manual))["events"]
    leads = {e["sources"][0]["quote"]: e["event_id"] for e in first}
    assert len(leads) == 3
    later = ok_build(tmp_path / "later", run_b, extra=("--state", str(tmp_path / "first" / "state.json")))["events"]
    by_id = {e["event_id"]: e for e in later}
    before_only = by_id[leads["Gemini 3.8 Flash price up: $0.119 → $0.238 per 1M input tokens"]]
    no_route = by_id[leads["Gemini 3.8 Flash price cut to $0.119 per 1M input tokens"]]
    strong = by_id[leads["Gemini 3.8 Flash price down: $0.30 → $0.119 per 1M input tokens"]]
    # Only the route-stated lead with the same direction and after amount takes the official change.
    assert strong["status"] == "confirmed" and strong["after"]["amount"] == "0.119" and strong["revision"] == 2
    assert [e for e in later if e["event_type"] == "price_change" and e["status"] == "confirmed"] == [strong]
    # The before amount matches the official after amount: not merged, not linked.
    assert before_only["status"] == "candidate" and before_only["revision"] == 1 and len(before_only["sources"]) == 1
    assert all(r["event_id"] != before_only["event_id"] for r in strong["related"])
    # Route not stated: linked as related only, never merged.
    assert no_route["status"] == "candidate" and no_route["revision"] == 1 and len(no_route["sources"]) == 1
    assert [r["event_id"] for r in strong["related"]] == [no_route["event_id"]]
