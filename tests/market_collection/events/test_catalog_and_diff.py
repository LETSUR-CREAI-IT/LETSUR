"""Event catalog, run diff, state, and claim contract on synthetic fixtures.

Runs below are synthetic fixtures in the real core layout (manifest.json,
sources.csv, price_observations.csv, conditions.csv, raw/*.bin) checked with
core._validated_run. Google raw bodies are HTML in the format the registered
Google parser reads (marked synthetic in a comment), and their CSV rows are
produced by that parser, as core.live does. No network, credential, or real sample.

CLI contract (python -m scripts.market_collection.events):

  catalog [--disable-source KEY]
      JSON from the existing registries: {"schema_version": 1, "models": [{"model_id",
      "modality", "maker", "sources": [{"source_key", "route", "role", "authority",
      "publisher", "active", "status", "task", "url", "parser", "registry"}],
      "unsupported": [{"role": "social_x"|"social_threads", "status": "unsupported"}]}],
      "feeds": [...], "disabled": [...]}. role is the document kind (pricing, api, docs,
      notice, ...); authority is a separate scope axis: manufacturer_official (the
      model's own maker), channel_official (a channel about its own route), unofficial.

  build --run RUN_DIR [...] [--state STATE_JSON] --output OUT_DIR [--policy all_verified]
        [--disable-source KEY] [--manual CLAIMS_JSON]
      RUN_DIR may be a core run or a signal run. OUT_DIR must not exist. Runs are
      ordered by manifest created_at_utc; repeated runs, replay runs, and runs already
      in the previous state add nothing. Success: exit 0 and OUT_DIR/state.json,
      events.json, report.json. Integrity failure: exit != 0, report.json
      {"status": "rejected", "errors": [...]} naming the run, no events.json.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection import signals as signal_collector
from scripts.market_collection.collectors.google import parse_source as google_parse
from scripts.market_collection.models import GOOGLE_LLM_MODEL, MODEL_MODALITIES
from scripts.market_collection.signals import SIGNAL_FIELDS, SIGNAL_SOURCE_FIELDS
from scripts.market_collection.signals.parsers import openrouter_endpoint_signals
from scripts.market_collection.signals.registry import signal_sources
from scripts.market_collection.sources import source_specs

ROOT = Path(__file__).resolve().parents[3]
GOOGLE = "google_gemini_38_flash"
OPENROUTER = "openrouter_gemini_38_flash"
RUN_A = "20261013T001700Z-" + "a" * 32
RUN_B = "20261014T001700Z-" + "b" * 32
RUN_C = "20261015T001700Z-" + "c" * 32
LABELS = {"input": "Input price", "output": "Output price (including thinking tokens)"}
LEGACY = Path("/Users/parkgeon/LETSUR-CREAI-IT/LETSUR-events/experiments/event-cases-20261009")


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "scripts.market_collection.events", *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)


def _spec(key: str) -> dict:
    return asdict(next(spec for spec in source_specs() if spec.source_key == key))


def google_html(*rows: tuple) -> bytes:
    """rows are (tier, item, amount[, period]); empty amount leaves the paid cell without a rate."""
    tables: dict[str, list[str]] = {}
    for row in rows:
        tier, item, amount = row[:3]
        period = row[3] if len(row) > 3 else "through December 31, 2026."
        cell = f"${amount} {period}" if amount else "Not published"
        tables.setdefault(tier, []).append(f"<tr><td>{LABELS[item]}</td><td>Free of charge</td><td>{cell}</td></tr>")
    body = ["<!-- synthetic fixture page for tests -->", '<h2 id="gemini-3.8-flash">Gemini 3.8 Flash</h2>']
    for tier, trs in tables.items():
        body += [f"<h3>{tier.title()}</h3>", "<table><tr><th></th><th>Free Tier</th><th>Paid Tier, per 1M tokens in USD</th></tr>",
                 *trs, "</table>"]
    return "\n".join(body).encode()


def google_entry(*rows: tuple, spec: dict | None = None, csv_amounts: dict | None = None) -> dict:
    """Raw HTML plus the rows the real Google parser extracts from it. csv_amounts edits CSV only."""
    return {"spec": spec or _spec(GOOGLE), "body": google_html(*rows), "csv_amounts": csv_amounts or {}}


def openrouter_entry(amount: str) -> dict:
    """Representative endpoint data whose serving conditions are not stated."""
    set_id = "openrouter_representative_input"
    price = dict.fromkeys(core.ADAPTER_PRICE_FIELDS, "")
    price.update(provider_model_id=GOOGLE_LLM_MODEL, task="text_generation", billing_item="input", price_kind="published_rate",
                 amount=amount, currency="USD", unit="1M tokens", condition_set_id=set_id, evidence_status="direct",
                 locator=f"synthetic representative input ${amount}")
    condition = dict(zip(core.ADAPTER_CONDITION_FIELDS, (set_id, "serving_endpoint", "eq", "", "", "unknown", "synthetic")))
    return {"spec": _spec(OPENROUTER), "body": f"synthetic representative ${amount}\n".encode(), "rows": ([price], [condition])}


def write_run(root: Path, run_id: str, created_at: str, entries: list[dict], *, mode: str = "live", origin: str = "") -> Path:
    """Write one synthetic fixture run in the exact core layout."""
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True)
    manifest = {"schema_version": 1, "mode": mode, "run_id": run_id, "created_at_utc": created_at, "disabled_sources": [], "sources": []}
    if origin:
        manifest["origin_run_id"] = origin
    sources, prices, conditions = [], [], []
    for index, entry in enumerate(entries, 1):
        spec, source_id = entry["spec"], f"{run_id}-s{index:03d}"
        source = {"run_id": run_id, "source_id": source_id, "source_key": spec["source_key"], "model_id": spec["model_id"],
                  "route": spec["route"], "requested_url": spec["url"], "effective_url": "", "fetched_at_utc": created_at,
                  "http_status": "", "fetch_status": "error", "parse_status": "not_run", "raw_path": "", "raw_sha256": "",
                  "body_bytes": "", "error": ""}
        if entry.get("fetch_error"):
            source["error"] = "URLError: synthetic timeout"
        else:
            body = entry["body"]
            raw_path = f"raw/{source_id}.bin"
            (run_dir / "raw").mkdir(exist_ok=True)
            (run_dir / raw_path).write_bytes(body)
            source.update(effective_url=spec["url"], http_status="200", fetch_status="ok", parse_status="ok",
                          raw_path=raw_path, raw_sha256=hashlib.sha256(body).hexdigest(), body_bytes=str(len(body)))
            own_prices, own_conditions = entry.get("rows") or google_parse(core.SourceSpec(**spec), body, source_id)
            for ordinal, row in enumerate(own_prices, 1):
                row = dict(row, amount=entry.get("csv_amounts", {}).get(row["billing_item"], row["amount"]))
                prices.append({"observation_id": f"{source_id}-p{ordinal:04d}", "run_id": run_id, "source_id": source_id,
                               "observed_at_utc": created_at, "modality": spec["modality"], "model_id": spec["model_id"],
                               **row, "route": spec["route"]})
            conditions += [{**row, "source_id": source_id} for row in own_conditions]
        sources.append(source)
        manifest["sources"].append({"spec": spec, "source": dict(source)})
    for name, fields, rows in (("sources.csv", core.SOURCE_FIELDS, sources),
                               ("price_observations.csv", core.PRICE_FIELDS, prices),
                               ("conditions.csv", core.CONDITION_FIELDS, conditions)):
        with (run_dir / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    core._validated_run(run_dir)
    return run_dir


def write_signal_run(root: Path, run_id: str, created_at: str, entries: list[tuple[dict, bytes, list[dict]]]) -> Path:
    """Write one synthetic signal run in the exact signals layout. entries are (spec, raw body, signal rows)."""
    run_dir = root / "signals" / "runs" / run_id
    (run_dir / "raw").mkdir(parents=True)
    manifest = {"schema_version": 1, "mode": "live", "run_id": run_id, "created_at_utc": created_at, "lookback_days": 120,
                "window_start_utc": "2026-06-01T00:00:00Z", "disabled_sources": [], "sources": []}
    sources, signals = [], []
    for index, (spec, body, rows) in enumerate(entries, 1):
        source_id = f"{run_id}-x{index:03d}"
        raw_path = f"raw/{source_id}.bin"
        (run_dir / raw_path).write_bytes(body)
        source = {"run_id": run_id, "source_id": source_id, "source_key": spec["source_key"], "source_kind": spec["source_kind"],
                  "model_id": spec["model_id"], "requested_url": spec["url"], "effective_url": spec["url"], "fetched_at_utc": created_at,
                  "http_status": "200", "fetch_status": "ok", "parse_status": "ok", "raw_path": raw_path,
                  "raw_sha256": hashlib.sha256(body).hexdigest(), "body_bytes": str(len(body)), "entries_total": str(len(rows)),
                  "entries_in_window": str(len(rows)), "signal_count": str(len(rows)), "error": ""}
        for ordinal, row in enumerate(rows, 1):
            full = dict.fromkeys(SIGNAL_FIELDS, "")
            full.update(row, signal_id=f"{source_id}-g{ordinal:04d}", signal_key=f"k{index}{ordinal}", run_id=run_id, source_id=source_id,
                        source_key=spec["source_key"], source_kind=spec["source_kind"], observed_at_utc=created_at)
            signals.append(full)
        sources.append(source)
        manifest["sources"].append({"spec": spec, "source": dict(source)})
    for name, fields, rows in (("signal_sources.csv", SIGNAL_SOURCE_FIELDS, sources), ("signals.csv", SIGNAL_FIELDS, signals)):
        with (run_dir / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return run_dir


def build(out: Path, *runs: Path, extra: tuple[str, ...] = ()) -> tuple[subprocess.CompletedProcess, dict | None, dict | None]:
    args = ["build"]
    for run in runs:
        args += ["--run", str(run)]
    result = _cli(*args, "--output", str(out), *extra)
    events = json.loads((out / "events.json").read_text()) if (out / "events.json").exists() else None
    report = json.loads((out / "report.json").read_text()) if (out / "report.json").exists() else None
    return result, events, report


def ok_build(out: Path, *runs: Path, extra: tuple[str, ...] = ()) -> dict:
    result, events, report = build(out, *runs, extra=extra)
    assert result.returncode == 0, result.stderr
    assert report["status"] == "ok" and events is not None and (out / "state.json").exists()
    return events


def changes(events: dict, source_key: str = GOOGLE, item: str = "input", tier: str = "standard") -> list[dict]:
    return [event for event in events["events"] if event["event_type"] == "price_change" and event["source_key"] == source_key
            and event["billing_item"] == item and event["conditions"].get("service_tier") == tier]


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_catalog_reuses_registries_with_separate_role_and_authority():
    """catalog lists each model's registered SourceSpec sources in order; role and authority are separate axes."""
    result = _cli("catalog")
    assert result.returncode == 0, result.stderr
    catalog = json.loads(result.stdout)
    expected: dict[str, list] = {}
    for spec in source_specs():
        expected.setdefault(spec.model_id, []).append(spec)
    assert [model["model_id"] for model in catalog["models"]] == list(expected)
    for model in catalog["models"]:
        own = [s for s in model["sources"] if s["registry"] == "SourceSpec"]
        assert model["modality"] == MODEL_MODALITIES[model["model_id"]]
        assert [(s["source_key"], s["route"], s["task"], s["url"], s["parser"]) for s in own] == \
            [(s.source_key, s.route, s.task, s.url, s.parser) for s in expected[model["model_id"]]]
        assert all(s["active"] is True and s["role"] for s in model["sources"])
        assert {u["role"] for u in model["unsupported"]} == {"social_x", "social_threads"}
        assert all(u["status"] == "unsupported" for u in model["unsupported"])
    by_key = {s["source_key"]: s for model in catalog["models"] for s in model["sources"]}
    assert by_key[GOOGLE]["authority"] == "manufacturer_official" and by_key[GOOGLE]["role"] == "pricing"
    # OpenRouter is official for its own route price, never for the maker's lifecycle.
    assert by_key[OPENROUTER]["authority"] == "channel_official"
    signal_keys = {spec.source_key for spec in signal_sources() if spec.model_id}
    assert signal_keys <= set(by_key)
    disabled = _cli("catalog", "--disable-source", OPENROUTER)
    assert disabled.returncode == 0
    by_key = {s["source_key"]: s for model in json.loads(disabled.stdout)["models"] for s in model["sources"]}
    assert by_key[OPENROUTER]["active"] is False and by_key[GOOGLE]["active"] is True
    assert _cli("catalog", "--disable-source", "not_a_registered_source").returncode != 0


def test_one_percent_official_change_is_price_change_and_first_is_baseline(tmp_path):
    """all_verified: a 1% same-condition official change is an event; the first run is a baseline."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"), ("standard", "output", "2.50"))])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.303"), ("standard", "output", "2.50"))])
    only_first = ok_build(tmp_path / "first", run_a)
    assert only_first["policy"] == "all_verified" and only_first["events"] == []
    assert {(b["source_key"], b["billing_item"], b["conditions"].get("service_tier")) for b in only_first["baselines"]} == \
        {(GOOGLE, "input", "standard"), (GOOGLE, "output", "standard")}
    events = ok_build(tmp_path / "out", run_a, run_b)
    assert len(events["events"]) == 1
    [event] = changes(events)
    assert event["status"] == "confirmed" and event["comparison"]["raw_check"] == "current_parser_both_raws"
    assert event["before"]["amount"] == "0.30" and event["after"]["amount"] == "0.303"
    assert event["change"]["pct"] == "1.00"
    assert event["before"]["run_id"] == RUN_A and event["after"]["run_id"] == RUN_B
    assert [item["billing_item"] for item in event["unchanged_items"]] == ["output"]


def test_condition_mismatch_failure_and_missing_price_are_not_changes(tmp_path):
    """Batch vs standard, fetch failure, and a missing price create no event."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"), ("standard", "output", "2.50"))])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("batch", "input", "0.15"), ("standard", "output", ""))])
    run_c = write_run(tmp_path, RUN_C, "2026-10-15T00:17:00Z", [{"spec": _spec(GOOGLE), "fetch_error": True}])
    events = ok_build(tmp_path / "out", run_a, run_b, run_c)
    assert events["events"] == []
    assert any(item["reason"] == "price_missing" and item["billing_item"] == "output" for item in events["not_comparable"])


def test_csv_only_difference_is_not_an_event(tmp_path):
    """Identical raw bodies with a different CSV value (parser repair or edit) never become a price change."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.30"), csv_amounts={"input": "0.303"})])
    events = ok_build(tmp_path / "out", run_a, run_b)
    assert events["events"] == []
    assert any(gap["kind"] == "parser_artifact" for gap in events["gaps"])


def test_repeated_replayed_and_continued_runs_keep_event_ids(tmp_path):
    """Repeats, replays, and a rerun from state create no new event or revision; event_id is stable."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.303"))])
    replay_id = "20261014T051700Z-" + "d" * 32
    replay_b = write_run(tmp_path, replay_id, "2026-10-14T05:17:00Z", [google_entry(("standard", "input", "0.303"))], mode="replay", origin=RUN_B)
    run_c = write_run(tmp_path, RUN_C, "2026-10-15T00:17:00Z", [google_entry(("standard", "input", "0.303"))])
    first = ok_build(tmp_path / "first", run_a, run_b)
    again = ok_build(tmp_path / "again", run_a, run_b, run_b, replay_b)
    later = ok_build(tmp_path / "later", run_a, run_b, replay_b, run_c)
    ids = [[event["event_id"] for event in events["events"]] for events in (first, again, later)]
    assert len(ids[0]) == 1 and ids[0] == ids[1] == ids[2]
    assert all(event["revision"] == 1 for events in (first, again, later) for event in events["events"])
    # Continue from state: only run C is new; rerunning the same input adds nothing.
    state = str(tmp_path / "first" / "state.json")
    continued = ok_build(tmp_path / "continued", run_a, run_b, run_c, extra=("--state", state))
    assert [e["event_id"] for e in continued["events"]] == ids[0]
    report = json.loads((tmp_path / "continued" / "report.json").read_text())
    assert report["new_events"] == [] and report["revised_events"] == []
    rerun = ok_build(tmp_path / "rerun", run_c, extra=("--state", str(tmp_path / "continued" / "state.json")))
    assert rerun["events"] == continued["events"]


@pytest.mark.parametrize("tamper", ["raw_sha", "manifest"])
def test_integrity_failure_is_rejected_without_touching_inputs(tmp_path, tamper):
    """A raw SHA mismatch or edited manifest rejects the build; inputs stay byte-identical."""
    inputs = tmp_path / "inputs"
    run_a = write_run(inputs, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    run_b = write_run(inputs, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.303"))])
    if tamper == "raw_sha":
        raw = next((run_b / "raw").iterdir())
        raw.write_bytes(raw.read_bytes().replace(b"synthetic", b"SYNTHETIC"))
    else:
        manifest = json.loads((run_b / "manifest.json").read_text())
        manifest["sources"][0]["source"]["raw_sha256"] = "0" * 64
        (run_b / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    before = snapshot(inputs)
    result, events, report = build(tmp_path / "out", run_a, run_b)
    assert result.returncode != 0
    assert report is not None and report["status"] == "rejected" and report["errors"]
    assert any(RUN_B in error for error in report["errors"])
    assert events is None and not (tmp_path / "out" / "state.json").exists()
    assert snapshot(inputs) == before


def test_event_detail_and_official_scope(tmp_path):
    """Details carry evidence and unknowns; a manifest spec that is not registered is never promoted."""
    forged = dict(_spec(GOOGLE), source_key="google_official_selfclaimed", parser="official_profile")
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [
        google_entry(("standard", "input", "0.30")), openrouter_entry("0.30"), google_entry(("standard", "input", "0.30"), spec=forged)])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [
        google_entry(("standard", "input", "0.303")), openrouter_entry("0.36"), google_entry(("standard", "input", "0.36"), spec=forged)])
    events = ok_build(tmp_path / "out", run_a, run_b)
    [event] = changes(events)
    assert event["evidence_level"] == "official" and event["status"] == "confirmed"
    assert event["model_id"] == GOOGLE_LLM_MODEL and event["route"] == "google_direct"
    assert event["conditions"] == {"service_tier": "standard", "rate_period": "through"}
    for side, run_id in (("before", RUN_A), ("after", RUN_B)):
        assert event[side]["currency"] == "USD" and event[side]["unit"] == "1M tokens"
        assert event[side]["run_id"] == run_id and event[side]["observation_id"].startswith(run_id)
        assert event[side]["effective_until"] == "2026-12-31"
    assert {ref["run_id"] for ref in event["evidence_refs"]} == {RUN_A, RUN_B}
    for ref in event["evidence_refs"]:
        run_dir = tmp_path / "runs" / ref["run_id"]
        assert hashlib.sha256((run_dir / ref["raw_path"]).read_bytes()).hexdigest() == ref["raw_sha256"]
        assert ref["locator"] and ref["source_key"] == GOOGLE and ref["url"] == _spec(GOOGLE)["url"]
    assert event["cause"] == "unknown" and event["letsur_usage"] == "unknown" and event["significance"] == "unknown"
    assert event["cause_detail"]["supplier_explanation"] is None and event["impact"] == "unknown"
    assert not [e for e in events["events"] if e["source_key"] == OPENROUTER]
    assert any(item["source_key"] == OPENROUTER and item["reason"] == "condition_unknown" for item in events["not_comparable"])
    assert not [e for e in events["events"] if e["source_key"] == forged["source_key"]]
    assert any(item["source_key"] == forged["source_key"] and item["reason"] == "unverified_source" for item in events["not_comparable"])
    # A disabled source makes no event; its earlier evidence is not deleted.
    disabled = ok_build(tmp_path / "disabled", run_a, run_b, extra=("--disable-source", GOOGLE))
    assert disabled["events"] == []


def test_official_future_rate_is_scheduled_change(tmp_path):
    """A registered official page stating a future date and a new rate is a scheduled_change, not a price_change."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(
        ("standard", "input", "0.75"), ("standard", "input", "1.50", "starting January 1, 2027."))])
    events = ok_build(tmp_path / "out", run_a)
    [event] = events["events"]
    assert event["event_type"] == "scheduled_change" and event["status"] == "confirmed"
    assert event["before"]["amount"] == "0.75" and event["after"]["amount"] == "1.50"
    assert event["dates"]["effective_from"] == "2027-01-01" and event["change"]["pct"] == "100.00"


def _openrouter_signal_spec() -> dict:
    return asdict(next(s for s in signal_sources() if s.model_id == GOOGLE_LLM_MODEL and s.parser == "openrouter_endpoints"))


def blog_run(root: Path, *entries: tuple[str, str]) -> Path:
    """Real signals.live over the registered developer_blog spec; entries are (slug, html body) of a synthetic Atom feed."""
    spec = next(s for s in signal_sources() if s.source_kind == "developer_blog")
    items = "".join(f'<entry><id>https://example.invalid/{slug}</id><link href="https://example.invalid/{slug}"/>'
                    f'<published>2026-10-12T00:00:00Z</published><title>{slug}</title><content type="html">{body}</content></entry>'
                    for slug, body in entries)
    feed = ('<?xml version="1.0"?><!-- synthetic source-shaped fixture --><feed xmlns="http://www.w3.org/2005/Atom">'
            f"<title>synthetic</title>{items}</feed>").encode()
    return signal_collector.live(root, lookback_days=3650, specs=[spec], fetcher=lambda _: (feed, spec.url, 200))


def test_unofficial_lead_is_confirmed_by_later_official_change_with_same_event_id(tmp_path):
    """A blog lead naming the route, direction and after amount stays a candidate until the official change confirms the
    same event. Leads without a stated route stay apart. The blog never becomes official."""
    run_a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [google_entry(("standard", "input", "0.30"))])
    body = json.dumps({"data": {"id": "google/gemini-3.8-flash", "name": "Gemini 3.8 Flash", "endpoints": [],
                                "expiration_date": "2026-11-30"}}).encode()
    _, expiry_rows = openrouter_endpoint_signals(body, GOOGLE_LLM_MODEL, "Gemini 3.8 Flash")
    signals = write_signal_run(tmp_path, "20261013T120000Z-" + "e" * 32, "2026-10-13T12:00:00Z",
                               [(_openrouter_signal_spec(), body, expiry_rows)])
    # The route is stated by an exact channel phrase in the raw blog sentence; it is read by the common signal path.
    blog = blog_run(tmp_path / "blog",
                    ("cut", "Gemini 3.8 Flash price cut on the Google Direct API: $0.30 → $0.27 per 1M input tokens."),
                    ("vague", "Gemini 3.8 Flash price cut coming."))
    first = ok_build(tmp_path / "first", run_a, signals, blog)
    leads = [e for e in first["events"] if e["event_type"] == "price_change"]
    assert len(leads) == 2 and all(e["status"] == "candidate" and e["evidence_level"] == "unofficial" for e in leads)
    [expiry] = [e for e in first["events"] if e["event_type"] == "route_change"]
    assert expiry["route"] == "openrouter" and expiry["subtags"] == ["remove"] and expiry["dates"]["effective_from"] == "2026-11-30"
    lead_id = next(e["event_id"] for e in leads if "$0.27" in e["sources"][0]["quote"])
    assert {e["route"] for e in leads if e["event_id"] == lead_id} == {"google_direct"}
    assert all(s["authority"] == "unofficial" for e in leads for s in e["sources"])
    run_b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [google_entry(("standard", "input", "0.27"))])
    later = ok_build(tmp_path / "later", run_b, extra=("--state", str(tmp_path / "first" / "state.json")))
    confirmed = next(e for e in later["events"] if e["event_id"] == lead_id)
    assert confirmed["event_type"] == "price_change" and confirmed["status"] == "confirmed" and confirmed["revision"] == 2
    assert confirmed["lineages"] == 2 and confirmed["after"]["amount"] == "0.27"
    vague = [e for e in later["events"] if e["event_id"] != lead_id and e["event_type"] == "price_change"]
    assert len(vague) == 1 and vague[0]["status"] == "candidate"


@pytest.mark.skipif(not LEGACY.is_dir(), reason="read-only legacy samples not present")
def test_legacy_five_events_through_common_rules(tmp_path):
    """The five legacy events convert by the common rules; Vercel availability is not a confirmed model_release."""
    names = ("price", "billing", "retirement", "launch", "discount")
    before = {name: (LEGACY / name / "event.json").read_bytes() for name in names}
    args = ["legacy", "--output", str(tmp_path / "legacy")]
    for name in names:
        args += ["--event", str(LEGACY / name / "event.json")]
    result = _cli(*args)
    assert result.returncode == 0, result.stderr
    events = json.loads((tmp_path / "legacy" / "events.json").read_text())["events"]
    by_legacy: dict[str, list] = {}
    for event in events:
        by_legacy.setdefault(event["legacy_event_type"], []).append(event)
    assert {e["event_type"] for e in by_legacy["price_change"]} == {"scheduled_change"}
    [billing] = by_legacy["billing_condition_change"]
    assert billing["event_type"] == "condition_change" and billing["subtags"][0].startswith("billing.")
    [retire] = by_legacy["service_end"]
    assert retire["event_type"] == "route_change" and retire["subtags"] == ["remove"]
    [launch] = by_legacy["model_release"]
    assert launch["event_type"] is None and set(launch["type_candidates"]) == {"model_release", "route_change"}
    [discount] = by_legacy["limited_discount"]
    assert discount["event_type"] == "limited_discount"
    # Without --inputs the raw bodies are unverified, so nothing is confirmed; no demo route is used.
    assert all(e["status"] != "confirmed" and e["letsur_usage"] == "unknown" for e in events)
    assert before == {name: (LEGACY / name / "event.json").read_bytes() for name in names}
