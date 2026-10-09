"""External price signals: public notices, blogs, and trackers that explain or foretell price changes.

Signals are kept apart from the price CSVs. A signal records that a public text
mentions a registered model with a price-related claim; it is a lead for "why did
the price change" and "what will change soon", never proof that a price changed.

Layout (beside, not inside, the price runs so existing readers are unaffected):
    <root>/signals/runs/<run_id>/manifest.json
    <root>/signals/runs/<run_id>/signal_sources.csv
    <root>/signals/runs/<run_id>/signals.csv
    <root>/signals/runs/<run_id>/raw/<source_id>.bin
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from scripts.market_collection.core import (CollectionError, TIMEOUT_SECONDS, MAX_REDIRECTS, _NoRedirect, _new_run, _read_csv,
                                            _utc_now, _validate_url, _write_bytes, _write_csv, _write_json)

from .parsers import SignalParseError, atom_entries, feed_signals, openrouter_endpoint_signals, rss_entries, utc_text
from .registry import SignalSourceSpec, signal_sources, validate

# Vercel's full changelog feed is ~3.7 MB, close to the 4 MiB price-page cap.
MAX_SIGNAL_BODY_BYTES = 16 * 1024 * 1024
DEFAULT_LOOKBACK_DAYS = 120
USER_AGENT = "LETSUR-public-signal-collector/1.0"

SIGNAL_SOURCE_FIELDS = ("run_id", "source_id", "source_key", "source_kind", "model_id", "requested_url", "effective_url", "fetched_at_utc",
                        "http_status", "fetch_status", "parse_status", "raw_path", "raw_sha256", "body_bytes",
                        "entries_total", "entries_in_window", "signal_count", "error")
# linked_event and next_check are left empty for the person who reviews the signal.
SIGNAL_FIELDS = ("signal_id", "signal_key", "run_id", "source_id", "source_key", "source_kind", "observed_at_utc",
                 "entry_id", "entry_url", "entry_title", "published_at_utc", "model_label", "model_ids", "model_match",
                 "claim_kinds", "quote", "date_phrases", "match_terms", "origin_status", "locator", "linked_event", "next_check")
HISTORY_FIELDS = ("signal_key", "first_seen_utc", "last_seen_utc", "seen_runs", "first_signal_id") + tuple(
    field for field in SIGNAL_FIELDS if field not in {"signal_id", "signal_key", "run_id", "source_id", "observed_at_utc"})

# Only a channel's own API field is "stated" by the channel; notices still need
# the condition check (target model, tier, region, account eligibility).
ORIGIN_STATUS = {"channel_api": "channel_stated", "channel_notice": "official_notice", "supplier_notice": "official_notice",
                 "developer_blog": "unverified", "price_tracker": "unverified"}


def fetch(spec: SignalSourceSpec) -> tuple[bytes, str, int]:
    """GET one fixed HTTPS URL with the price collector's redirect rules and a larger body cap."""
    original_host = _validate_url(spec.url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    url = spec.url
    for redirects in range(MAX_REDIRECTS + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}, method="GET")
        try:
            response = opener.open(request, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = response.status
            if status in (301, 302, 303, 307, 308):
                if redirects >= MAX_REDIRECTS:
                    raise CollectionError("redirect limit exceeded")
                location = response.headers.get("Location")
                if not location:
                    raise CollectionError("redirect has no Location")
                next_url = urllib.parse.urljoin(url, location)
                if _validate_url(next_url) != original_host:
                    raise CollectionError("redirect changed host")
                url = next_url
                continue
            body = bytearray()
            while True:
                chunk = response.read(min(65536, MAX_SIGNAL_BODY_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_SIGNAL_BODY_BYTES:
                    raise CollectionError("response body exceeds 16 MiB")
            return bytes(body), url, status
    raise CollectionError("redirect limit exceeded")


def parse_body(spec: SignalSourceSpec, body: bytes, window_start: datetime) -> tuple[int, int, list[dict]]:
    """Return (entries_total, entries_in_window, signal rows without run identity)."""
    if spec.parser == "openrouter_endpoints":
        total, rows = openrouter_endpoint_signals(body, spec.model_id, spec.model_label)
        return total, total, rows
    entries = atom_entries(body) if spec.parser == "atom" else rss_entries(body)
    in_window, rows = feed_signals(entries, window_start)
    return len(entries), in_window, rows


def signal_key(source_key: str, row: dict) -> str:
    material = "|".join((source_key, row["entry_id"], row["model_label"], row["claim_kinds"], row["quote"]))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _source_record(run_id: str, source_id: str, spec: SignalSourceSpec) -> dict:
    return {"run_id": run_id, "source_id": source_id, "source_key": spec.source_key, "source_kind": spec.source_kind, "model_id": spec.model_id,
            "requested_url": spec.url, "effective_url": "", "fetched_at_utc": _utc_now(), "http_status": "", "fetch_status": "pending",
            "parse_status": "not_run", "raw_path": "", "raw_sha256": "", "body_bytes": "", "entries_total": "", "entries_in_window": "",
            "signal_count": "", "error": ""}


def _apply_parse(spec: SignalSourceSpec, source: dict, body: bytes, window_start: datetime, signals: list[dict]) -> None:
    try:
        total, in_window, rows = parse_body(spec, body, window_start)
    except (SignalParseError, ValueError) as exc:
        source.update(parse_status="error", error=f"{type(exc).__name__}: {exc}")
        return
    for ordinal, row in enumerate(rows, 1):
        signals.append({"signal_id": f"{source['source_id']}-g{ordinal:04d}", "signal_key": signal_key(spec.source_key, row),
                        "run_id": source["run_id"], "source_id": source["source_id"], "source_key": spec.source_key,
                        "source_kind": spec.source_kind, "observed_at_utc": source["fetched_at_utc"], **row,
                        "origin_status": ORIGIN_STATUS[spec.source_kind], "linked_event": "", "next_check": ""})
    source.update(parse_status="ok", error="", entries_total=str(total), entries_in_window=str(in_window), signal_count=str(len(rows)))


def _finish(run_dir: Path, manifest: dict, sources: list[dict], signals: list[dict]) -> Path:
    _write_csv(run_dir / "signal_sources.csv", SIGNAL_SOURCE_FIELDS, sources)
    _write_csv(run_dir / "signals.csv", SIGNAL_FIELDS, signals)
    _write_json(run_dir / "manifest.json", manifest)
    return run_dir


def live(root: Path | str, lookback_days: int = DEFAULT_LOOKBACK_DAYS, disable_source: tuple[str, ...] = (), *,
         specs: list[SignalSourceSpec] | None = None, fetcher: Callable[[SignalSourceSpec], tuple[bytes, str, int]] = fetch) -> Path:
    specs = validate(list(specs)) if specs is not None else signal_sources()
    if lookback_days < 1:
        raise CollectionError("lookback_days must be positive")
    unknown = set(disable_source) - {spec.source_key for spec in specs}
    if unknown:
        raise CollectionError(f"unknown disabled signal source: {sorted(unknown)}")
    run_id, run_dir = _new_run(Path(root) / "signals", "live")
    window_start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    manifest = {"schema_version": 1, "mode": "live", "run_id": run_id, "created_at_utc": _utc_now(), "lookback_days": lookback_days,
                "window_start_utc": utc_text(window_start), "disabled_sources": sorted(set(disable_source)), "sources": []}
    sources: list[dict] = []
    signals: list[dict] = []
    for index, spec in enumerate(specs, 1):
        if spec.source_key in disable_source:
            continue
        source = _source_record(run_id, f"{run_id}-x{index:03d}", spec)
        sources.append(source)
        try:
            body, effective_url, status = fetcher(spec)
            if not isinstance(body, bytes) or len(body) > MAX_SIGNAL_BODY_BYTES:
                raise CollectionError("invalid or oversized response body")
            if _validate_url(effective_url) != _validate_url(spec.url):
                raise CollectionError("effective URL changed host")
            raw_path = f"raw/{source['source_id']}.bin"
            _write_bytes(run_dir / raw_path, body)
            source.update(fetched_at_utc=_utc_now(), effective_url=effective_url, http_status=str(status), fetch_status="ok" if status == 200 else "http_error",
                          raw_path=raw_path, raw_sha256=hashlib.sha256(body).hexdigest(), body_bytes=str(len(body)))
        except Exception as exc:  # one broken source must not stop the others
            source.update(fetch_status="error", error=f"{type(exc).__name__}: {exc}")
        else:
            if status == 200:
                _apply_parse(spec, source, body, window_start, signals)
        manifest["sources"].append({"spec": asdict(spec), "source": dict(source)})
    return _finish(run_dir, manifest, sources, signals)


def validated_run(run_dir: Path) -> tuple[dict, list[dict], list[dict]]:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    sources = _read_csv(run_dir / "signal_sources.csv", SIGNAL_SOURCE_FIELDS)
    signals = _read_csv(run_dir / "signals.csv", SIGNAL_FIELDS)
    if manifest.get("run_id") != run_dir.name or manifest.get("mode") not in {"live", "replay"}:
        raise CollectionError("signal manifest identity or mode mismatch")
    if [item.get("source") for item in manifest.get("sources", [])] != sources:
        raise CollectionError("signal manifest/source CSV mismatch")
    ids = {source["source_id"] for source in sources}
    if len(ids) != len(sources):
        raise CollectionError("duplicate signal source ID")
    for source in sources:
        if source["run_id"] != manifest["run_id"]:
            raise CollectionError("signal source run reference mismatch")
        if source["raw_path"]:
            raw_path = (run_dir / source["raw_path"]).resolve()
            if not str(raw_path).startswith(str(run_dir.resolve()) + "/"):
                raise CollectionError("raw path leaves signal run directory")
            body = raw_path.read_bytes()
            if len(body) != int(source["body_bytes"]) or hashlib.sha256(body).hexdigest() != source["raw_sha256"]:
                raise CollectionError(f"signal raw integrity failure: {source['source_id']}")
        elif source["raw_sha256"] or source["body_bytes"]:
            raise CollectionError("missing signal raw reference")
    for row in signals:
        if row["run_id"] != manifest["run_id"] or row["source_id"] not in ids:
            raise CollectionError("signal reference mismatch")
    return manifest, sources, signals


def replay(run_dir: Path | str, root: Path | str) -> Path:
    """Re-read preserved raw bodies of a live run with the current parsers (same time window)."""
    origin_dir = Path(run_dir)
    origin, old_sources, _ = validated_run(origin_dir)
    if origin["mode"] != "live":
        raise CollectionError("signal replay requires a live origin")
    window_start = datetime.fromisoformat(origin["window_start_utc"].replace("Z", "+00:00"))
    run_id, destination = _new_run(Path(root) / "signals", "replay")
    manifest = {"schema_version": 1, "mode": "replay", "run_id": run_id, "origin_run_id": origin["run_id"], "created_at_utc": _utc_now(),
                "lookback_days": origin["lookback_days"], "window_start_utc": origin["window_start_utc"],
                "disabled_sources": origin.get("disabled_sources", []), "sources": []}
    sources: list[dict] = []
    signals: list[dict] = []
    for item, old in zip(origin["sources"], old_sources):
        spec = SignalSourceSpec(**item["spec"])
        source_id = old["source_id"].replace(origin["run_id"], run_id, 1)
        source = dict(old, run_id=run_id, source_id=source_id, entries_total="", entries_in_window="", signal_count="", parse_status="not_run")
        sources.append(source)
        if old["raw_path"]:
            body = (origin_dir / old["raw_path"]).read_bytes()
            source["raw_path"] = f"raw/{source_id}.bin"
            _write_bytes(destination / source["raw_path"], body)
            if old["fetch_status"] == "ok":
                _apply_parse(spec, source, body, window_start, signals)
        manifest["sources"].append({"spec": asdict(spec), "source": dict(source)})
    return _finish(destination, manifest, sources, signals)


def history(root: Path | str, output: Path | str) -> Path:
    """Merge live runs into one row per signal_key with first/last seen times.

    Feeds keep only recent entries (Simon Willison: last 30 posts), so the history
    is how an early warning survives after the post leaves the feed.
    """
    destination = Path(output)
    if destination.exists():
        raise CollectionError(f"signal history destination exists: {destination}")
    merged: dict[str, dict] = {}
    runs_dir = Path(root) / "signals" / "runs"
    for run_dir in sorted(path for path in runs_dir.glob("*") if path.is_dir()) if runs_dir.is_dir() else []:
        manifest, _, signals = validated_run(run_dir)
        if manifest["mode"] != "live":
            continue
        for row in signals:
            item = merged.get(row["signal_key"])
            if item is None:
                merged[row["signal_key"]] = {**{field: row[field] for field in HISTORY_FIELDS if field in row},
                                             "first_seen_utc": row["observed_at_utc"], "last_seen_utc": row["observed_at_utc"],
                                             "seen_runs": {row["run_id"]}, "first_signal_id": row["signal_id"]}
            else:
                item["first_seen_utc"] = min(item["first_seen_utc"], row["observed_at_utc"])
                item["last_seen_utc"] = max(item["last_seen_utc"], row["observed_at_utc"])
                item["seen_runs"].add(row["run_id"])
    rows = [dict(item, seen_runs=str(len(item["seen_runs"]))) for item in sorted(merged.values(), key=lambda value: (value["first_seen_utc"], value["signal_key"]))]
    destination.mkdir(parents=True, exist_ok=False)
    _write_csv(destination / "signal_history.csv", HISTORY_FIELDS, rows)
    return destination


def _plain(text: str) -> str:
    """Feed titles go into a Markdown step summary; drop link/HTML/emphasis syntax."""
    return re.sub(r"[<>\[\]()`*_|]", " ", text).strip()


def summary(root: Path | str, limit: int = 20) -> str:
    """Markdown for the Actions step summary: latest live run only."""
    runs_dir = Path(root) / "signals" / "runs"
    runs = sorted(path for path in runs_dir.glob("*") if path.is_dir()) if runs_dir.is_dir() else []
    live_runs = [path for path in runs if json.loads((path / "manifest.json").read_text(encoding="utf-8")).get("mode") == "live"]
    if not live_runs:
        return "## External signals\n\nNo signal run found.\n"
    manifest, sources, signals = validated_run(live_runs[-1])
    failed = sorted(source["source_key"] for source in sources if source["fetch_status"] != "ok" or source["parse_status"] != "ok")
    lines = ["## External signals", "", f"Run: {manifest['run_id']}", f"Window start: {manifest['window_start_utc']}",
             f"Sources: {len(sources)}", f"Signals: {len(signals)}", "Failed sources: " + (", ".join(failed) if failed else "none"), "",
             "Signals are leads, not confirmed price changes.", ""]
    for row in sorted(signals, key=lambda value: value["published_at_utc"], reverse=True)[:limit]:
        lines.append(f"- {row['published_at_utc'][:10] or 'undated'} · {row['model_label']} · {row['claim_kinds']} · {row['source_key']}: {_plain(row['entry_title'] or row['quote'][:80])}")
    return "\n".join(lines) + "\n"
