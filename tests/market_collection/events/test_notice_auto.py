"""Official notice text -> typed claims -> classify_claim, through the real signal collector.

Feed bodies below are SOURCE-SHAPED SYNTHETIC fixtures: an Atom feed in the
shape of the registered vercel_changelog source, an RSS 2.0 feed in the shape of
openai_news, and an Atom feed in the shape of simonwillison_blog. They are not
live captures and prove nothing about live detection; they check that raw ->
signals.live (registered specs, controlled fetcher, real parse) -> build turns
clearly stated notices into events and leaves everything else a candidate or gap.
price_change and scheduled_change are covered by test_catalog_and_diff.py.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.market_collection import signals
from scripts.market_collection.models import ASTRA_MODEL, FABLE_MODEL, FLARE_MODEL, GOOGLE_LLM_MODEL, SUNBURST_MODEL
from scripts.market_collection.signals.registry import signal_sources

from test_catalog_and_diff import RUN_A, RUN_B, build, google_entry, ok_build, write_run

SPECS = {spec.source_key: spec for spec in signal_sources()}


def atom(*entries: tuple[str, str, str, str]) -> bytes:
    """entries are (slug, published, title, html body)."""
    items = "".join(f'<entry><id>urn:synthetic:{slug}</id><link href="https://vercel.com/changelog/{slug}"/>'
                    f"<published>{published}</published><title>{title}</title><content type=\"html\">{body}</content></entry>"
                    for slug, published, title, body in entries)
    return f'<?xml version="1.0"?><!-- synthetic source-shaped fixture --><feed xmlns="http://www.w3.org/2005/Atom"><title>synthetic</title>{items}</feed>'.encode()


def rss(*items: tuple[str, str, str, str]) -> bytes:
    """items are (slug, RFC 822 date or '', title, description)."""
    body = "".join(f"<item><guid>https://openai.com/index/{slug}</guid><link>https://openai.com/index/{slug}</link>"
                   + (f"<pubDate>{date}</pubDate>" if date else "") + f"<title>{title}</title><description>{text}</description></item>"
                   for slug, date, title, text in items)
    return f'<?xml version="1.0"?><!-- synthetic source-shaped fixture --><rss version="2.0"><channel><title>synthetic</title>{body}</channel></rss>'.encode()


VERCEL = atom(
    ("astra-gateway", "2026-10-10T16:00:00Z", "GPT-6 Astra on AI Gateway", "GPT-6 Astra is now available on AI Gateway."),
    ("gemini-remove", "2026-10-11T16:00:00Z", "Gemini 3.8 Flash removal",
     "Gemini 3.8 Flash will be removed from AI Gateway on November 30, 2026."),
    ("gemini-sale", "2026-10-12T16:00:00Z", "Gemini 3.8 Flash promotion",
     "Gemini 3.8 Flash is 50% off on AI Gateway through November 30, 2026."),
    ("two-models-sale", "2026-10-12T17:00:00Z", "Promotion",
     "Claude Fable 5.1 and GPT-6 Astra are 20% off on AI Gateway through November 15, 2026."),
    ("fable-cache", "2026-10-13T16:00:00Z", "Claude Fable 5.1 cache pricing",
     "The Claude Fable 5.1 cache read multiplier on AI Gateway changes from 0.25x to 0.1x of the input price, "
     "and the input price drops to $2.50 per 1M tokens."),
)
OPENAI = rss(
    ("astra", "Tue, 01 Sep 2026 17:00:00 GMT", "GPT-6 Astra",
     "Today we release GPT-6 Astra, our new model, available today via the API."),
    ("sunburst", "Wed, 02 Sep 2026 17:00:00 GMT", "Introducing GPT Image 2.5 Sunburst", "Introducing GPT Image 2.5 Sunburst, our new image model."),
    ("flare-endpoint", "Thu, 03 Sep 2026 17:00:00 GMT", "API update",
     "The GPT Image 2.5 Flare edits endpoint will be retired on December 1, 2026."),
    ("flare-retire", "Fri, 04 Sep 2026 17:00:00 GMT", "Retiring GPT Image 2.5 Flare",
     "GPT Image 2.5 Flare will be retired across all products on January 15, 2027."),
)
BLOG = atom(("blog-retire", "2026-10-12T10:00:00Z", "Notes",
             "Gemini 3.8 Flash will be retired across all products on December 31, 2026, according to a post."))
BODIES = {"openai_news": OPENAI, "vercel_changelog": VERCEL, "simonwillison_blog": BLOG}


def collect(root: Path) -> Path:
    """Real signals.live over registered specs; the fetcher only returns the fixture body."""
    specs = [SPECS[key] for key in ("openai_news", "vercel_changelog", "simonwillison_blog")]
    return signals.live(root, lookback_days=3650, specs=specs, fetcher=lambda spec: (BODIES[spec.source_key], spec.url, 200))


def of(events: dict, event_type: str, model_id: str) -> list[dict]:
    return [e for e in events["events"] if e["event_type"] == event_type and e["model_id"] == model_id]


def test_official_notices_become_typed_events_through_signal_collection(tmp_path):
    run = collect(tmp_path / "data")
    events = ok_build(tmp_path / "out", run)
    # model_release: maker's own notice, "available today", official publication date.
    [release] = of(events, "model_release", ASTRA_MODEL)
    assert release["status"] == "confirmed" and release["route"] == "openai_direct"
    assert release["dates"]["maker_first_release_date"] == "2026-09-01"
    assert "available today" in release["sources"][0]["quote"]
    # "Introducing" alone is not a release.
    assert not [e for e in events["events"] if e["model_id"] == SUNBURST_MODEL and e["status"] == "confirmed"]
    # route_change add: channel notice after the maker's release (> 7 days) on the channel's own route.
    [added] = [e for e in of(events, "route_change", ASTRA_MODEL) if e["subtags"] == ["add"]]
    assert added["status"] == "confirmed" and added["route"] == "vercel_ai_gateway"
    assert added["dates"]["route_added_date"] == "2026-10-10"
    # route_change remove on the channel; never model_end from a channel.
    [removed] = [e for e in of(events, "route_change", GOOGLE_LLM_MODEL) if e["route"] == "vercel_ai_gateway"]
    assert removed["status"] == "confirmed" and removed["subtags"] == ["remove"] and removed["dates"]["effective_from"] == "2026-11-30"
    # model_end only from the maker's whole-model retirement; the endpoint notice is not a model end.
    ends = of(events, "model_end", FLARE_MODEL)
    assert [(e["status"], e["dates"]["effective_from"]) for e in ends] == [("confirmed", "2027-01-15")]
    endpoint = [e for e in events["events"] if e["model_id"] == FLARE_MODEL and "edits endpoint" in e["sources"][0]["quote"]]
    assert endpoint and all(e["event_type"] != "model_end" and e["status"] == "candidate" for e in endpoint)
    # Unofficial retelling of a retirement stays a candidate.
    assert not of(events, "model_end", GOOGLE_LLM_MODEL)
    assert all(e["status"] == "candidate" for e in events["events"] if e["evidence_level"] == "unofficial")
    # limited_discount: stated percent, period, and channel route.
    [sale] = [e for e in of(events, "limited_discount", GOOGLE_LLM_MODEL)]
    assert sale["status"] == "confirmed" and sale["route"] == "vercel_ai_gateway" and sale["change"]["discount_pct"] == "50"
    assert sale["dates"]["end_date"] == "2026-11-30"
    # Two models in one sentence: attribution unclear, never confirmed.
    shared = [e for e in events["events"] if "20% off" in e["sources"][0]["quote"]]
    assert shared and all(e["status"] == "candidate" for e in shared)
    assert all(any(g["field"] == "model_attribution" for g in e["gaps"]) for e in shared)
    # condition_change with a simultaneous price move: one condition_change carrying before/after.
    [cache] = of(events, "condition_change", FABLE_MODEL)
    assert cache["status"] == "confirmed" and cache["subtags"] == ["billing.cache_multiplier"]
    assert cache["before"] == {"value": "0.25x"} and cache["after"] == {"value": "0.1x"}
    assert not [e for e in of(events, "price_change", FABLE_MODEL) if "0.1x" in e["sources"][0]["quote"]]
    # Every confirmed notice event cites the raw sentence it came from.
    for event in events["events"]:
        if event["status"] == "confirmed":
            ref = event["evidence_refs"][0]
            assert ref["raw_sha256"] and ref["locator"]
            raw = (run / ref["raw_path"]).read_bytes().decode()
            assert event["sources"][0]["quote"].split(" ")[0] in raw
    # Same input again from state adds nothing.
    again = ok_build(tmp_path / "again", run, extra=("--state", str(tmp_path / "out" / "state.json")))
    assert again["events"] == events["events"]


def test_tampered_signal_row_is_rejected_as_gap(tmp_path):
    run = collect(tmp_path / "data")
    path = run / "signals.csv"
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    target = next(row for row in rows if "50% off" in row["quote"])
    target["quote"] = target["quote"].replace("50% off", "90% off")
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=signals.SIGNAL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    events = ok_build(tmp_path / "out", run)
    assert not [e for e in events["events"] if any("90% off" in s["quote"] for s in e["sources"])]
    assert any(g["kind"] == "signal_row_mismatch" and g["signal_id"] == target["signal_id"] for g in events["gaps"])
    # The raw notice itself is still read by the registered parser.
    [sale] = of(events, "limited_discount", GOOGLE_LLM_MODEL)
    assert sale["change"]["discount_pct"] == "50"


def test_state_from_another_parser_cannot_confirm_without_before_raw(tmp_path):
    """Continuing from state without the before raw needs the same parser fingerprint."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.303"))])
    ok_build(tmp_path / "first", run_a)
    state = json.loads((tmp_path / "first" / "state.json").read_text())
    assert all(item["parser_fingerprint"] for item in state["series"].values())
    for item in state["series"].values():
        item["parser_fingerprint"] = "0" * 16
    (tmp_path / "old.json").write_text(json.dumps(state))
    events = ok_build(tmp_path / "later", run_b, extra=("--state", str(tmp_path / "old.json")))
    [event] = [e for e in events["events"] if e["event_type"] == "price_change"]
    assert event["status"] == "candidate" and event["comparison"]["raw_check"] == "parser_fingerprint_mismatch"
    assert any(g["kind"] == "parser_fingerprint_mismatch" for g in events["gaps"])
    result, _, _ = build(tmp_path / "same", run_b, extra=("--state", str(tmp_path / "first" / "state.json")))
    assert result.returncode == 0
    same = json.loads((tmp_path / "same" / "events.json").read_text())
    assert [e["status"] for e in same["events"] if e["event_type"] == "price_change"] == ["confirmed"]
