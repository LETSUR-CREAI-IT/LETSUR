"""Stated billing conditions in core runs, manual claim identity, and stated signal routes.

Anthropic raw bodies are SOURCE-SHAPED SYNTHETIC fixtures: the Markdown pricing table
the registered anthropic_fable parser reads (same headers and footnote form as the
preserved pricing.md), and the core CSV rows are produced by that parser, as core.live
does. They check that:
- a cache read multiplier the page STATES for the model ("priced at 0.05x the base input
  price" footnote of its cell) that moves is one condition_change of the cache_read item,
  with prices, raw SHA, locators and refs, and no duplicate price_change;
- the ratio of two observed prices is never a stated multiplier: base-only, cache-only and
  proportional moves are price_change only; a CSV-only edit is never confirmed;
- a stated rule that contradicts the prices is kept as a conflict, not confirmed;
- a manual claim's identity is its URL plus quote, not its URL alone;
- a maker name alone in a blog sentence is not a route.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from scripts.market_collection import core
from scripts.market_collection.collectors.anthropic import parse_source as anthropic_parse
from scripts.market_collection.models import FABLE_MODEL, GOOGLE_LLM_MODEL
from scripts.market_collection.sources import source_specs

from test_catalog_and_diff import RUN_A, RUN_B, RUN_C, blog_run, google_entry, ok_build, write_run

FABLE = "anthropic_claude_fable_51"


def anthropic_entry(input_rate: str, cache_read: str, *, rule: str = "", input_unit: str = "MTok", note: str = "", csv_amounts: dict | None = None,
                    csv_rule: str = "") -> dict:
    """Pricing.md-shaped raw plus the rows the registered Anthropic parser extracts from it. rule is the cache-read
    multiplier the footnote of the Fable cell states (none when empty). csv_amounts / csv_rule edit CSV only."""
    spec = asdict(next(s for s in source_specs() if s.source_key == FABLE))
    footnote = (f"\n*<sup>1 Cache hits and refreshes on Claude Fable 5.1 and Claude Mythos 5.1 are priced at {rule}x the base input price.</sup>*\n"
                if rule else "")
    body = ("# Pricing\n<!-- synthetic fixture page for tests" + note + " -->\n## Model pricing\n"
            "| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| Claude Mythos 5.1 | $999 / MTok | $999 / MTok | $999 / MTok | $999 / MTok | $999 / MTok |\n"
            f"| Claude Fable 5.1 | ${input_rate} / {input_unit} | $12.50 / MTok | $20 / MTok | ${cache_read} / MTok<sup>1</sup> | $50 / MTok |\n"
            f"{footnote}\n*<sup>All other models use the standard 0.1x multiplier.</sup>*\n").encode()
    prices, conditions = anthropic_parse(core.SourceSpec(**spec), body, "unused")
    if csv_rule:
        conditions = [dict(c, value=csv_rule) if c["condition_key"] == "cache_read_multiplier" else c for c in conditions]
    return {"spec": spec, "body": body, "rows": (prices, conditions), "csv_amounts": csv_amounts or {}}


def kinds(events: dict) -> list[tuple[str, str]]:
    return sorted((e["event_type"], e["billing_item"]) for e in events["events"])


def test_parser_reads_only_the_rule_stated_for_the_model_cell():
    """The footnote of this model's cell is the rule; another model's footnote, the general line and the price ratio are not."""
    spec = core.SourceSpec(**asdict(next(s for s in source_specs() if s.source_key == FABLE)))

    def rules(body: bytes) -> list[tuple[str, str, str]]:
        prices, conditions = anthropic_parse(spec, body, "unused")
        assert [(p["billing_item"], p["amount"]) for p in prices] == \
            [("input", "10"), ("cache_write", "12.50"), ("cache_write", "20"), ("cache_read", "1"), ("output", "50")]
        return [(c["condition_set_id"], c["value"], c["locator"]) for c in conditions if c["condition_key"] == "cache_read_multiplier"]

    body = anthropic_entry("10", "1", rule="0.05")["body"]
    [(set_id, value, locator)] = rules(body)
    assert set_id.endswith("_cache_read_base") and value == "0.05"
    assert locator == "Model pricing > footnote 1: <sup>1 Cache hits and refreshes on Claude Fable 5.1 and Claude Mythos 5.1 " \
                      "are priced at 0.05x the base input price.</sup>"
    assert rules(anthropic_entry("10", "1")["body"]) == []  # "All other models use the standard 0.1x" is not this model's rule
    assert rules(body.replace(b"on Claude Fable 5.1 and Claude Mythos 5.1", b"on Claude Mythos 5.1")) == []
    assert rules(body.replace(b"<sup>1 Cache", b"<sup>2 Cache")) == []  # another cell's footnote
    assert rules(body.replace(b"MTok<sup>1</sup>", b"MTok")) == []
    assert rules(body + body[body.index(b"*<sup>1 Cache"):].split(b"\n", 1)[0].replace(b"0.05x", b"0.1x") + b"\n") == []  # two rules


def test_preserved_pricing_raw_states_rules_only_where_the_cell_has_one():
    """The preserved live pricing.md: Fable 5.1 and Opus 5.5 cells carry their own footnote rule; others carry none."""
    run = Path(__file__).resolve().parents[3] / "samples/market_collection/2026-10-05-expanded-live/runs/20261005T073538Z-72ba62e3895042e4b9b844c3896936a9"
    body = (run / "raw/20261005T073538Z-72ba62e3895042e4b9b844c3896936a9-s007.bin").read_bytes()
    found = {}
    for key in ("anthropic_claude_fable_51", "anthropic_claude_opus_5_5", "anthropic_claude_sonnet_5_5", "anthropic_claude_haiku_4_5",
                "anthropic_claude_sonnet_5"):
        spec = next(s for s in source_specs() if s.source_key == key)
        prices, conditions = anthropic_parse(spec, body, "unused")
        assert prices
        found[key] = [c["value"] for c in conditions if c["condition_key"] == "cache_read_multiplier"]
    assert found == {"anthropic_claude_fable_51": ["0.025"], "anthropic_claude_opus_5_5": ["0.05"], "anthropic_claude_sonnet_5_5": [],
                     "anthropic_claude_haiku_4_5": [], "anthropic_claude_sonnet_5": []}


def test_cache_multiplier_move_is_one_condition_change_with_price_detail(tmp_path):
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [anthropic_entry("10", "1", rule="0.1")])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [anthropic_entry("10", "0.50", rule="0.05")])
    first = ok_build(tmp_path / "first", run_a)
    assert first["events"] == [] and {b["billing_item"] for b in first["baselines"]} == {"input", "cache_write", "cache_read", "output"}
    events = ok_build(tmp_path / "out", run_a, run_b)
    [event] = events["events"]  # no price_change for the same cache_read move
    assert event["event_type"] == "condition_change" and event["status"] == "confirmed" and event["evidence_level"] == "official"
    assert event["sources"][0]["source_key"] == FABLE and event["sources"][0]["authority"] in ("manufacturer_official", "channel_official")
    assert event["subtags"] == ["billing.cache_read_multiplier"] and event["billing_item"] == "cache_read"
    assert event["model_id"] == FABLE_MODEL and event["route"] == "anthropic_direct" and event["conditions"] == {"service_tier": "standard"}
    assert event["change"]["condition_key"] == "cache_read_multiplier"
    assert (event["change"]["before_value"], event["change"]["after_value"]) == ("0.1", "0.05")
    assert event["before"]["amount"] == "1" and event["after"]["amount"] == "0.50" and event["change"]["price_pct"] == "-50.00"
    assert event["comparison"]["raw_check"] == "current_parser_both_raws" and event["conflicts"] == []
    for name, run_id, value, item, base in (("before", RUN_A, "0.1", "1", "10"), ("after", RUN_B, "0.05", "0.50", "10")):
        cond = event["condition"][name]
        assert (cond["value"], cond["item_amount"], cond["base_item"], cond["base_amount"]) == (value, item, "input", base)
        assert cond["basis"] == "stated" and cond["locator"].startswith("Model pricing > footnote 1:") and f"{value}x the base" in cond["locator"]
        assert cond["derived_ratio"] == value and cond["rule_consistent_with_prices"] is True
        assert cond["run_id"] == run_id and cond["item_observation_id"].startswith(run_id) and cond["base_observation_id"].startswith(run_id)
        raw = tmp_path / "runs" / run_id / cond["raw_path"]
        assert hashlib.sha256(raw.read_bytes()).hexdigest() == cond["raw_sha256"]
    assert {r["run_id"] for r in event["evidence_refs"]} == {RUN_A, RUN_B}
    # Continue from state, then rerun: same event id, no new event or revision.
    later = ok_build(tmp_path / "later", run_b, extra=("--state", str(tmp_path / "first" / "state.json")))
    assert [e["event_id"] for e in later["events"]] == [event["event_id"]]
    rerun = ok_build(tmp_path / "rerun", run_b, extra=("--state", str(tmp_path / "later" / "state.json")))
    report = json.loads((tmp_path / "rerun" / "report.json").read_text())
    assert rerun["events"] == later["events"] and report["new_events"] == [] and report["revised_events"] == []


def test_stated_move_with_same_item_price_and_price_only_moves(tmp_path):
    """A stated rule move with an unchanged cache price is not missed. Without a stated rule, a price ratio move is only
    the price_change of the item that moved: base-only, cache-only and proportional moves create no condition_change."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [anthropic_entry("10", "1", rule="0.1")])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [anthropic_entry("20", "1", rule="0.05")])
    events = ok_build(tmp_path / "same_price", run_a, run_b)
    assert kinds(events) == [("condition_change", "cache_read"), ("price_change", "input")]
    [cond] = [e for e in events["events"] if e["event_type"] == "condition_change"]
    assert (cond["change"]["before_value"], cond["change"]["after_value"], cond["change"]["price_direction"]) == ("0.1", "0.05", "unchanged")
    assert cond["status"] == "confirmed"
    for name, (before, after), expected in (("base_only", (("10", "1"), ("20", "1")), [("price_change", "input")]),
                                            ("cache_only", (("10", "1"), ("10", "0.50")), [("price_change", "cache_read")]),
                                            ("proportional", (("10", "1"), ("20", "2")), [("price_change", "cache_read"), ("price_change", "input")])):
        one = write_run(tmp_path / name, RUN_A, "2026-10-13T00:17:00Z", [anthropic_entry(*before)])
        two = write_run(tmp_path / name, RUN_B, "2026-10-14T00:17:00Z", [anthropic_entry(*after)])
        result = ok_build(tmp_path / name / "out", one, two)
        assert kinds(result) == expected and all(e["status"] == "confirmed" for e in result["events"])
        assert not any(g["kind"] == "stated_rule_price_mismatch" for g in result["gaps"])
    # The same rule stated on both sides and a proportional move: prices only, the rule did not change.
    run_c = write_run(tmp_path, RUN_C, "2026-10-15T00:17:00Z", [anthropic_entry("20", "2", rule="0.1", note=" v3")])
    assert kinds(ok_build(tmp_path / "chain", run_a, run_c)) == [("price_change", "cache_read"), ("price_change", "input")]


def test_csv_only_condition_edit_is_never_confirmed(tmp_path):
    """Identical raw or a raw that still states the old value: the CSV move is a gap, not an event."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [anthropic_entry("10", "1", rule="0.1")])
    same_raw = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [anthropic_entry("10", "1", rule="0.1", csv_rule="0.05")])
    other_raw = write_run(tmp_path, RUN_C, "2026-10-15T00:17:00Z",
                          [anthropic_entry("10", "1", rule="0.1", note=" edited", csv_rule="0.05")])
    for name, run, kind in (("same", same_raw, "parser_artifact"), ("other", other_raw, "csv_raw_mismatch")):
        events = ok_build(tmp_path / name, run_a, run)
        assert events["events"] == []
        assert any(gap["kind"] == kind and gap.get("condition_keys") == ["cache_read_multiplier"] for gap in events["gaps"])
    amount_only = write_run(tmp_path / "amount", RUN_B, "2026-10-14T00:17:00Z",
                            [anthropic_entry("10", "1", note=" edited", csv_amounts={"cache_read": "0.50"})])
    events = ok_build(tmp_path / "amount_only", run_a, amount_only)
    assert events["events"] == []
    assert any(gap["kind"] in ("parser_artifact", "csv_raw_mismatch") and gap["billing_item"] == "cache_read" for gap in events["gaps"])


def test_stated_rule_contradicting_prices_is_not_confirmed(tmp_path):
    """The raw states 0.05x but its prices give 0.1: kept as a conflict, neither side picked. No base row: check unknown."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [anthropic_entry("10", "1", rule="0.1")])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [anthropic_entry("10", "1", rule="0.05")])
    events = ok_build(tmp_path / "out", run_a, run_b)
    [event] = events["events"]
    assert (event["event_type"], event["billing_item"], event["status"]) == ("condition_change", "cache_read", "candidate")
    assert [(c["kind"], c["side"], c["stated_value"], c["derived_ratio"]) for c in event["conflicts"]] == \
        [("stated_rule_vs_prices", "after", "0.05", "0.1")]
    assert any(g["kind"] == "stated_rule_price_mismatch" for g in events["gaps"])
    # A stated rule kept while only the cache price moves: the price_change is held back as a conflict.
    run_c = write_run(tmp_path, RUN_C, "2026-10-15T00:17:00Z", [anthropic_entry("10", "0.50", rule="0.1")])
    [price] = ok_build(tmp_path / "price", run_a, run_c)["events"]
    assert (price["event_type"], price["status"], price["conflicts"][0]["kind"]) == ("price_change", "candidate", "stated_rule_vs_prices")
    # The base input row is not read (unit not per MTok): the rule move is kept, its price check is unknown.
    no_base = write_run(tmp_path / "nb", RUN_B, "2026-10-14T00:17:00Z", [anthropic_entry("10", "0.50", rule="0.05", input_unit="1K tokens")])
    [event] = ok_build(tmp_path / "no_base", run_a, no_base)["events"]
    assert (event["event_type"], event["status"], event["conflicts"]) == ("condition_change", "confirmed", [])
    assert event["condition"]["after"]["derived_ratio"] is None and event["condition"]["after"]["rule_consistent_with_prices"] is None
    assert any(g["field"] == "after.derived_ratio" for g in event["gaps"])


def test_manual_claim_identity_is_url_plus_quote(tmp_path):
    """Two quotes on one URL stay two events; the same manual input again adds nothing."""
    manual = tmp_path / "manual.json"
    manual.write_text(json.dumps([{"kind": "price_decrease", "model_id": GOOGLE_LLM_MODEL, "route": "google_direct", "role": "social",
                                   "url": "https://example.invalid/post", "quote": quote}
                                  for quote in ("Gemini 3.8 Flash price down: $0.30 → $0.27 per 1M input tokens",
                                                "Gemini 3.8 Flash price down: $2.50 → $2.00 per 1M output tokens")]))
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    first = ok_build(tmp_path / "first", run_a, extra=("--manual", str(manual)))
    assert len(first["events"]) == 2 and all(e["revision"] == 1 and len(e["sources"]) == 1 for e in first["events"])
    again = ok_build(tmp_path / "again", run_a, extra=("--state", str(tmp_path / "first" / "state.json"), "--manual", str(manual)))
    report = json.loads((tmp_path / "again" / "report.json").read_text())
    assert again["events"] == first["events"] and report["new_events"] == [] and report["revised_events"] == []


def test_maker_name_alone_is_not_a_route(tmp_path):
    """"by Google" names the maker, not the Direct API: the lead keeps no route and is only linked."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    blog = blog_run(tmp_path / "blog", ("maker", "Gemini 3.8 Flash price cut by Google to $0.27 per 1M input tokens."))
    first = ok_build(tmp_path / "first", run_a, blog)
    [lead] = first["events"]
    assert lead["route"] == "" and lead["status"] == "candidate"
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.27"))])
    later = ok_build(tmp_path / "later", run_b, extra=("--state", str(tmp_path / "first" / "state.json")))
    [official] = [e for e in later["events"] if e["status"] == "confirmed"]
    assert official["event_id"] != lead["event_id"] and [r["event_id"] for r in official["related"]] == [lead["event_id"]]
