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
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.market_collection import signals
from scripts.market_collection.events.evidence import digest
from scripts.market_collection.models import ASTRA_MODEL, FABLE_MODEL, FLARE_MODEL, GOOGLE_LLM_MODEL, SUNBURST_MODEL
from scripts.market_collection.signals.parsers import openrouter_endpoint_signals
from scripts.market_collection.signals.registry import signal_sources

from test_catalog_and_diff import (ROOT, RUN_A, RUN_B, _openrouter_signal_spec, build, google_entry, ok_build, write_run,
                                   write_signal_run)

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


def test_parser_fingerprint_covers_only_the_code_that_parses_the_series(tmp_path):
    """State written before an edit to another source's collector still confirms; an edit to this source's collector does not."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.303"))])
    ok_build(tmp_path / "first", run_a)
    for name, status, check in (("fal", "confirmed", "stored_parse_of_before_raw"),
                                ("google", "candidate", "parser_fingerprint_mismatch")):
        code = tmp_path / "code" / name
        shutil.copytree(ROOT / "scripts", code / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        collector = code / "scripts" / "market_collection" / "collectors" / f"{name}.py"
        collector.write_text(collector.read_text() + "\n\ndef _edited_after_state():\n    return None\n")
        out = tmp_path / "out" / name
        result = subprocess.run([sys.executable, "-m", "scripts.market_collection.events", "build", "--run", str(run_b),
                                 "--state", str(tmp_path / "first" / "state.json"), "--output", str(out)],
                                cwd=code, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        [event] = [e for e in json.loads((out / "events.json").read_text())["events"] if e["event_type"] == "price_change"]
        assert (name, event["status"], event["comparison"]["raw_check"]) == (name, status, check)


def test_discount_period_state_from_the_newest_run_date(tmp_path):
    """A discount end without a year takes the publication year (the next year when that date is before publication).
    Whether it has passed is judged by the newest run given to the build, the end day included; status is unchanged."""
    feed = atom(("gemini-sale", "2026-08-13T00:00:00Z", "Gemini 3.8 Flash promotion",
                 "Gemini 3.8 Flash is 50% off on AI Gateway through August 27."),
                ("fable-sale", "2026-08-14T00:00:00Z", "Claude Fable 5.1 promotion",
                 "Claude Fable 5.1 is 30% off on AI Gateway through January 10th."),
                ("astra-sale", "2026-08-15T00:00:00Z", "GPT-6 Astra promotion",
                 "GPT-6 Astra is 20% off on AI Gateway through November 30, 2026."),
                ("undated-sale", "", "Gemini 3.8 Flash promotion",
                 "Gemini 3.8 Flash is 10% off on AI Gateway through September 1."))
    spec = SPECS["vercel_changelog"]
    run = signals.live(tmp_path / "data", lookback_days=3650, specs=[spec], fetcher=lambda _: (feed, spec.url, 200))
    manifest = json.loads((run / "manifest.json").read_text())
    (run / "manifest.json").write_text(json.dumps(dict(manifest, created_at_utc="2026-08-27T23:00:00Z")))

    def periods(events: dict) -> dict:
        return {e["evidence_refs"][0]["url"].rsplit("/", 1)[-1]: (e["period_state"], e["period_end"]["date"], e["period_end"]["basis"])
                for e in events["events"] if e["event_type"] == "limited_discount"}

    first = ok_build(tmp_path / "first", run)
    assert periods(first) == {"gemini-sale": ("active", "2026-08-27", "derived_from_publication"),
                              "fable-sale": ("active", "2027-01-10", "derived_from_publication"),
                              "astra-sale": ("active", "2026-11-30", "stated"),
                              "undated-sale": ("unknown", None, None)}
    assert all(e["period_state"] is None and e["period_end"] is None for e in first["events"] if e["event_type"] != "limited_discount")
    core = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    _, later, report = build(tmp_path / "later", core, extra=("--state", str(tmp_path / "first" / "state.json")))
    assert {slug: value[0] for slug, value in periods(later).items()} == {
        "gemini-sale": "ended", "fable-sale": "active", "astra-sale": "active", "undated-sale": "unknown"}
    assert {e["period_end"]["as_of"] for e in later["events"] if e["event_type"] == "limited_discount"} == {"2026-10-14"}
    assert [(e["status"], e["revision"]) for e in later["events"] if e["period_state"] == "ended"] == [("confirmed", 1)]
    assert report["event_counts"]["limited_discount:confirmed"] == 4
    assert report["active_event_counts"]["limited_discount:confirmed"] == 3


def test_group_key_joins_cards_of_one_occurrence_and_keeps_each_card(tmp_path):
    """Cards about one occurrence share a group_key whatever their route: same model, type and subtags, and the same stated
    end or effective date, else a publication day within 3 days of the group's first card. A discount also needs the same
    rate (an endpoint discount joins only endpoint discounts), a price change the same direction. The maker's own card
    represents its group, otherwise the earliest card; every card stays listed with its own evidence."""
    vercel = atom(
        ("astra-sale", "2026-08-17T16:00:00Z", "Gateway update", "GPT-6 Astra is 50% off on AI Gateway through September 18."),
        ("astra-sale-again", "2026-08-21T16:00:00Z", "Gateway update", "GPT-6 Astra stays 50% off on AI Gateway through September 18."),
        ("astra-sale-later", "2026-09-25T16:00:00Z", "Gateway update", "GPT-6 Astra is 50% off on AI Gateway through October 2."),
        ("gemini-sale", "2026-08-17T16:00:00Z", "Gateway update", "Gemini 3.8 Flash is 50% off on AI Gateway through September 18."),
        ("astra-cut", "2026-09-01T16:00:00Z", "Gateway update", "GPT-6 Astra price cut on AI Gateway."),
        ("astra-rise", "2026-09-02T16:00:00Z", "Gateway update", "GPT-6 Astra price increase on AI Gateway."),
        ("astra-drop", "2026-09-03T16:00:00Z", "Gateway update", "GPT-6 Astra price drop on AI Gateway takes effect now."),
        ("astra-cut-later", "2026-09-20T16:00:00Z", "Gateway update", "GPT-6 Astra price cut on AI Gateway again."))
    maker = rss(("astra-price", "Wed, 02 Sep 2026 17:00:00 GMT", "Pricing update", "GPT-6 Astra price cut across the API."))
    bodies = {"vercel_changelog": vercel, "openai_news": maker}
    run = signals.live(tmp_path / "data", lookback_days=3650, specs=[SPECS[key] for key in bodies],
                       fetcher=lambda spec: (bodies[spec.source_key], spec.url, 200))
    endpoints = json.dumps({"data": {"id": "google/gemini-3.8-flash", "name": "Gemini 3.8 Flash", "endpoints": [
        {"provider_name": name, "tag": name.lower(), "pricing": {"prompt": "0.0000003", "completion": "0.0000025", "discount": rate}}
        for name, rate in (("Alpha", 0.2), ("Beta", 0.5))]}}).encode()
    _, rows = openrouter_endpoint_signals(endpoints, GOOGLE_LLM_MODEL, "Gemini 3.8 Flash")
    channel = write_signal_run(tmp_path, "20260818T120000Z-" + "e" * 32, "2026-08-18T12:00:00Z", [(_openrouter_signal_spec(), endpoints, rows)])
    events = ok_build(tmp_path / "out", run, channel)

    def name(event: dict) -> str:
        quote = event["sources"][0]["quote"]
        return quote.split(" ")[0] if "pricing.discount" in quote else event["evidence_refs"][0]["url"].rsplit("/", 1)[-1]

    cards = {name(event): event for event in events["events"]}
    assert len(cards) == len(events["events"]) == 11
    assert all(len(e["sources"]) == len(e["evidence_refs"]) == 1 for e in events["events"])
    groups: dict[str, list[str]] = {}
    for event in events["events"]:
        groups.setdefault(event["group_key"], []).append(name(event))
    assert sorted(sorted(members) for members in groups.values()) == sorted([
        ["astra-sale", "astra-sale-again"], ["astra-sale-later"], ["gemini-sale"], ["Alpha", "Beta"],
        ["astra-cut", "astra-drop", "astra-price"], ["astra-rise"], ["astra-cut-later"]])
    assert all(key.startswith("grp_") and [cards[n]["group_role"] for n in members].count("representative") == 1
               for key, members in groups.items())
    # The maker's own card represents the group although a channel card on another route came a day earlier.
    assert cards["astra-price"]["group_role"] == "representative" and cards["astra-price"]["route"] == ""
    assert cards["astra-cut"]["route"] == "vercel_ai_gateway" and cards["astra-sale"]["group_role"] == "representative"
    # Derived at output only: stored events and their revisions stay as they are, and a rebuild gives the same keys.
    state = json.loads((tmp_path / "out" / "state.json").read_text())
    assert not [e for e in state["events"].values() if "group_key" in e or "group_role" in e]
    again = ok_build(tmp_path / "again", run, channel, extra=("--state", str(tmp_path / "out" / "state.json")))
    assert again["events"] == events["events"]


def test_channel_notice_of_a_maker_list_price_change_has_no_route_and_joins_the_maker_change(tmp_path):
    """A channel notice that the maker changed its own list price (the maker's name with "list pric…" or "Direct API") is
    not a price of the channel's route: the card has no route and names the channel in announced_via, with no new gap.
    The same article's discount and the channel's own price moves keep the channel route. The card joins the maker's
    observed change of the same direction within 3 days, which represents the group. A card stored before this rule is
    corrected in place under the same event_id."""
    vercel = atom(
        ("gemini-lower-price", "2026-10-13T16:00:00Z", "Gemini 3.8 Flash is now 50% off a lower price",
         "Google lowered list pricing for Gemini 3.8 Flash, and the 50% AI Gateway discount now applies to the new, lower price "
         "through October 31."),
        ("astra-cheaper", "2026-10-13T17:00:00Z", "Gateway update", "OpenAI's GPT-6 Astra is now cheaper on AI Gateway."))
    notices = signals.live(tmp_path / "data", lookback_days=3650, specs=[SPECS["vercel_changelog"]],
                           fetcher=lambda spec: (vercel, spec.url, 200))
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.27"))])
    events = ok_build(tmp_path / "out", run_a, run_b, notices)
    [maker_change] = [e for e in events["events"] if e["event_type"] == "price_change" and e["route"] == "google_direct"]
    [list_price] = [e for e in events["events"] if e.get("claim_kind") == "price_decrease" and e["model_id"] == GOOGLE_LLM_MODEL]
    [discount] = [e for e in events["events"] if e["event_type"] == "limited_discount"]
    [channel_move] = [e for e in events["events"] if e["model_id"] == ASTRA_MODEL]
    assert list_price["route"] == "" and list_price["status"] == "candidate" and list_price["evidence_level"] == "official"
    assert list_price["announced_via"] == {"route": "vercel_ai_gateway", "note": "manufacturer list price change found in a channel notice"}
    assert [gap["field"] for gap in list_price["gaps"]] == ["observed_price"]
    # The event_id is the one the channel route gave before this rule, so stored cards keep their identity.
    assert list_price["event_id"] == "ev_" + digest(["claim", GOOGLE_LLM_MODEL, "vercel_ai_gateway", "price_change", list_price["anchor"]])
    assert discount["route"] == "vercel_ai_gateway" and discount["status"] == "confirmed" and "announced_via" not in discount
    assert discount["sources"][0]["quote"] == list_price["sources"][0]["quote"]
    assert channel_move["route"] == "vercel_ai_gateway" and channel_move["claim_kind"] == "price_decrease" and "announced_via" not in channel_move
    assert maker_change["status"] == "confirmed" and maker_change["change"]["direction"] == "down"
    assert list_price["group_key"] == maker_change["group_key"] != discount["group_key"]
    assert (maker_change["group_role"], list_price["group_role"]) == ("representative", "member")

    # A card stored with the channel route before this rule is corrected on the next build, once.
    state = json.loads((tmp_path / "out" / "state.json").read_text())
    stored = state["events"][list_price["event_id"]]
    stored["route"] = "vercel_ai_gateway"
    del stored["announced_via"]
    (tmp_path / "old.json").write_text(json.dumps(state))
    result, fixed, report = build(tmp_path / "fixed", extra=("--state", str(tmp_path / "old.json")))
    assert result.returncode == 0, result.stderr
    [card] = [e for e in fixed["events"] if e["event_id"] == list_price["event_id"]]
    assert card["revision"] == 2 and card["history"][-1]["reason"] == "route cleared: manufacturer list price change found in a channel notice"
    assert {k: v for k, v in card.items() if k not in ("revision", "history")} == \
        {k: v for k, v in list_price.items() if k not in ("revision", "history")}
    assert report["revised_events"] == [card["event_id"]]
    result, _, again = build(tmp_path / "again", extra=("--state", str(tmp_path / "fixed" / "state.json")))
    assert result.returncode == 0 and again["revised_events"] == []
