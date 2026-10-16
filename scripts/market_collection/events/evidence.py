"""Read core and signal runs into verified observations and claims.

Every run is checked with the collectors' own validators (core._validated_run,
signals.validated_run); a failure rejects the whole build. Adapters here are by
source format (core price rows, OpenRouter endpoints JSON, atom/rss feed rows),
never by model.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from scripts.market_collection import core
from scripts.market_collection import signals as signal_runs

from . import notices
from .catalog import Catalog, maker


class RunRejected(ValueError):
    """An input run failed integrity checks; the message names the run."""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object, size: int = 20) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:size]


def run_kind(run_dir: Path) -> str:
    if (run_dir / "signal_sources.csv").exists():
        return "signals"
    if (run_dir / "sources.csv").exists():
        return "core"
    raise RunRejected(f"{run_dir.name}: not a core or signal run directory")


def load_run(run_dir: Path) -> dict:
    """Validate one run with the collectors' validators; raise RunRejected naming it."""
    run_dir = Path(run_dir)
    name = run_dir.name
    try:
        kind = run_kind(run_dir)
        if kind == "core":
            manifest, sources, prices, conditions = core._validated_run(run_dir)
            return {"kind": kind, "dir": run_dir, "manifest": manifest, "sources": sources, "prices": prices, "conditions": conditions}
        manifest, sources, rows = signal_runs.validated_run(run_dir)
        return {"kind": kind, "dir": run_dir, "manifest": manifest, "sources": sources, "signals": rows}
    except RunRejected:
        raise
    except Exception as exc:  # any unreadable or inconsistent input rejects the build
        raise RunRejected(f"{name}: {type(exc).__name__}: {exc}") from exc


def condition_view(rows: list[dict]) -> tuple[dict, list[str], tuple]:
    """Stated conditions, keys not stated, and a semantic key free of run-local IDs and locators."""
    stated, missing, semantic = {}, [], []
    for row in rows:
        if row["status"] != "stated" or not row["value"]:
            missing.append(row["condition_key"])
            continue
        stated[row["condition_key"]] = row["value"]
        semantic.append((row["condition_key"], row["operator"], row["value"], row["unit"]))
    return stated, sorted(set(missing)), tuple(sorted(semantic))


def _price_rows(prices: list[dict], conditions: list[dict]) -> list[tuple[dict, list[dict]]]:
    joined: dict[tuple[str, str], list[dict]] = {}
    for row in conditions:
        joined.setdefault((row["source_id"], row["condition_set_id"]), []).append(row)
    return [(row, joined.get((row["source_id"], row["condition_set_id"]), []) if row["condition_set_id"] else []) for row in prices]


# Billing policies a parser extracts only from an explicit statement of the source (a condition row with its locator),
# keyed to the item the rule is relative to. They stay in the raw-verified semantic, but are left out of the series key
# so a series is compared across a change of them. A ratio of two observed prices is never one of these.
MUTABLE_CONDITIONS = {"cache_read_multiplier": "input"}


def parent_semantic(semantic: tuple) -> tuple:
    return tuple(x for x in semantic if x[0] not in MUTABLE_CONDITIONS)


def series_key(source_key: str, row: dict, semantic: tuple) -> str:
    """Same series = same registered source, model, route, item, unit, and stated conditions (stated policies aside)."""
    return digest([source_key, row["model_id"], row["route"], row["provider_model_id"], row["upstream_provider"], row["task"],
                   row["billing_item"], row["price_kind"], row["currency"], row["unit"], list(parent_semantic(semantic))])


def stated_policy(rows: list[dict]) -> dict:
    """key -> {value, locator} of stated mutable policies in one condition set."""
    return {row["condition_key"]: {"value": row["value"], "unit": row["unit"], "locator": row["locator"]} for row in rows
            if row["condition_key"] in MUTABLE_CONDITIONS and row["status"] == "stated" and row["value"]}


def core_observations(run: dict, catalog: Catalog) -> tuple[list[dict], list[dict]]:
    """Return (observations, source_status) for one validated core run."""
    manifest = run["manifest"]
    run_id = manifest["run_id"]
    by_source = {item["source"]["source_id"]: item for item in manifest["sources"]}
    observations, statuses = [], []
    for item in manifest["sources"]:
        spec, source = item["spec"], item["source"]
        info = catalog.price_source(spec)
        ok = source["fetch_status"] == "ok" and source["parse_status"] == "ok"
        statuses.append({"run_id": run_id, "source_id": source["source_id"], "source_key": spec.get("source_key", ""),
                         "model_id": spec.get("model_id", ""), "route": spec.get("route", ""),
                         "status": "ok" if ok else "failed", "authority": info["authority"],
                         "active": spec.get("source_key", "") not in catalog.disabled,
                         "error": source["error"]})
    for key in manifest.get("disabled_sources", []):
        statuses.append({"run_id": run_id, "source_id": "", "source_key": key, "model_id": "", "route": "", "status": "disabled",
                         "authority": "", "active": False, "error": ""})
    for row, conds in _price_rows(run["prices"], run["conditions"]):
        item = by_source[row["source_id"]]
        spec, source = item["spec"], item["source"]
        if source["fetch_status"] != "ok" or source["parse_status"] != "ok" or spec.get("source_key") in catalog.disabled:
            continue
        stated, missing, semantic = condition_view(conds)
        info = catalog.price_source(spec)
        observations.append({
            "observation_id": row["observation_id"], "run_id": run_id, "source_id": row["source_id"],
            "source_key": spec["source_key"], "spec": spec, "model_id": row["model_id"], "route": row["route"],
            "provider_model_id": row["provider_model_id"], "task": row["task"], "billing_item": row["billing_item"], "price_kind": row["price_kind"], "amount": row["amount"],
            "currency": row["currency"], "unit": row["unit"], "upstream_provider": row["upstream_provider"],
            "conditions": stated, "unknown_conditions": missing, "semantic": semantic, "policy": stated_policy(conds),
            "series": series_key(spec["source_key"], row, semantic),
            "evidence_status": row["evidence_status"], "effective_from": row["effective_from"],
            "effective_until": row["effective_until"], "locator": row["locator"], "unknown_reason": row["unknown_reason"],
            "observed_at_utc": row["observed_at_utc"], "raw_path": source["raw_path"], "raw_sha256": source["raw_sha256"],
            "url": source["effective_url"] or source["requested_url"], "authority": info["authority"], "role": info["role"],
            "scope": info["scope"], "registered": catalog.registered(spec),
        })
    return observations, statuses


def comparability(obs: dict) -> str:
    """Empty when comparable; otherwise a reason code. Unknown is never a wildcard."""
    if not obs["registered"]:
        return "unverified_source"
    if not obs["amount"]:
        return "price_missing"
    if obs["unknown_conditions"] or not obs["upstream_provider"]:
        return "condition_unknown"
    if obs["evidence_status"] != "direct":
        return "not_direct_evidence"
    return ""


class Reparser:
    """Re-read preserved raw bodies with the current registered parser (no network)."""

    def __init__(self) -> None:
        self._adapters = None
        self._cache: dict[tuple[str, str], tuple[dict, dict] | None] = {}

    def amounts(self, run_dir: Path, obs: dict) -> dict | None:
        """series -> amount parsed now from the raw body of obs's source, or None if unparsable."""
        parsed = self._get(run_dir, obs)
        return None if parsed is None else parsed[0]

    def policies(self, run_dir: Path, obs: dict) -> dict | None:
        """series -> {key: value} of the stated policies parsed now from the same raw, or None if unparsable."""
        parsed = self._get(run_dir, obs)
        return None if parsed is None else parsed[1]

    def _get(self, run_dir: Path, obs: dict) -> tuple[dict, dict] | None:
        cache_key = (obs["run_id"], obs["source_id"])
        if cache_key not in self._cache:
            self._cache[cache_key] = self._parse(run_dir, obs)
        return self._cache[cache_key]

    def _parse(self, run_dir: Path, obs: dict) -> tuple[dict, dict] | None:
        if not obs["raw_path"]:
            return None
        try:
            if self._adapters is None:
                _, self._adapters = core.registry()
            spec = core.SourceSpec(**obs["spec"])
            body = (run_dir / obs["raw_path"]).read_bytes()
            prices, conditions = core._validate_parse(spec, obs["source_id"], *self._adapters[spec.modality].parse_source(spec, body, obs["source_id"]))
        except Exception:
            return None
        # _validate_parse tags conditions with source_id but not prices; tag prices so _price_rows joins as for CSV rows.
        prices = [dict(row, source_id=obs["source_id"]) for row in prices]
        found: dict[str, str] = {}
        policies: dict[str, dict] = {}
        ambiguous: set[str] = set()
        for row, conds in _price_rows(prices, conditions):
            if not row["amount"]:
                continue
            row = dict(row, model_id=spec.model_id, route=spec.route)
            key = series_key(spec.source_key, row, condition_view(conds)[2])
            policy = {k: v["value"] for k, v in stated_policy(conds).items()}
            if key in found and (policy or policies[key]):  # rows differing only by a stated policy: no unique pair
                ambiguous.add(key)
            found[key], policies[key] = row["amount"], policy
        for key in ambiguous:
            del found[key], policies[key]
        return found, policies


@lru_cache(maxsize=1)
def parser_fingerprint() -> str:
    """Digest of the parser code that turns raw bodies into rows (price collectors, signal parsers, notice adapter)."""
    base = Path(__file__).resolve().parents[1]
    files = [base / "core" / "__init__.py", *sorted((base / "collectors").rglob("*.py")), base / "signals" / "parsers.py",
             base / "signals" / "matching.py", base / "events" / "notices.py"]
    return digest([[path.relative_to(base).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()] for path in files], 16)


def _claim(row: dict, spec: dict, source: dict, run_id: str, model_id: str, kind: str, info: dict) -> dict:
    # Feeds may put a run-local ordinal in entry_id and share one entry_url (a model page) across
    # articles, so identity is the entry URL plus the quoted sentence, never the entry ordinal.
    article = row["entry_url"] or row["entry_id"]
    return {
        "claim_id": digest([spec.get("source_key", ""), article, model_id, kind, row["quote"]]),
        "kind": kind, "model_id": model_id, "route": info["route"], "authority": info["authority"],
        "role": info["role"], "publisher": info["publisher"], "scope": info["scope"],
        "source_key": spec.get("source_key", ""), "run_id": run_id, "source_id": source["source_id"],
        "signal_id": row.get("signal_id", ""), "entry_id": row["entry_id"], "entry_url": row["entry_url"],
        "quote": row["quote"], "date_phrases": row.get("date_phrases", ""), "published_at_utc": row["published_at_utc"],
        "observed_at_utc": source["fetched_at_utc"], "locator": row["locator"], "raw_path": source["raw_path"],
        "raw_sha256": source["raw_sha256"], "url": row["entry_url"] or source["effective_url"] or source["requested_url"],
        "lineage": article, "anchor": article + "#" + digest(row["quote"], 12), "blockers": [],
    }


# Generic signal kinds a typed claim of the same entry and model reads more precisely. Kinds outside
# the typed claim's family (a discount sentence next to an availability sentence) are kept.
COVERS = {"discount": {"discount", "free"}, "route_removal": {"deprecation"}, "endpoint_deprecation": {"deprecation"},
          "condition": {"price_increase", "price_decrease"}, "availability": set()}


def _typed_claims(entries: list, spec: dict, source: dict, run_id: str, catalog: Catalog) -> list[dict]:
    claims = []
    for entry in entries:
        for found in notices.entry_claims(entry, spec, catalog):
            row = {"entry_id": entry.entry_id, "entry_url": entry.url, "quote": found["quote"],
                   "published_at_utc": signal_runs.utc_text(entry.published), "locator": f"{entry.locator}/line[{found['line']}]"}
            info = dict(found["info"], route=found["route"])
            claim = _claim(row, spec, source, run_id, found["model_id"], found["kind"], info)
            claim["claim_id"] = digest(["notice", claim["claim_id"]])
            claim.update({k: v for k, v in found.items() if k not in ("quote", "line", "info", "route", "model_id", "kind", "ambiguous_model")})
            if found.get("ambiguous_model"):
                claim["blockers"].append(AMBIGUOUS)
            claims.append(claim)
    return claims


def stated_route(quote: str, model_id: str, catalog: Catalog) -> str:
    """The route a sentence names unambiguously, or "". Cues are the maker's own "<Maker> Direct API" (when the maker
    has exactly one registered route for the model) and the URL of a registered price source of the model. A model
    or maker name alone is never a route, and the publisher's authority is unchanged."""
    found = set()
    direct, name = notices.maker_route(catalog, model_id), maker(model_id)
    if direct and name != "unknown" and re.search(r"\b" + re.escape(name) + r"\s+Direct\s+API\b", quote, re.I):
        found.add(direct)
    text = quote.lower()
    for spec in catalog.specs:
        if spec.model_id == model_id and spec.url.split("://", 1)[-1].rstrip("/").lower() in text:
            found.add(spec.route)
    return found.pop() if len(found) == 1 else ""


AMBIGUOUS = {"field": "model_attribution", "class": "check_first",
             "reason": "the sentence or article names several models; the price or condition is not clearly attributed",
             "next_check": "read the notice and confirm which model the value applies to"}


def signal_claims(run: dict, catalog: Catalog) -> tuple[list[dict], list[dict], list[dict]]:
    """Claims from one signal run: typed notice claims, then verified signal rows. Also gaps for rows the raw does not support."""
    manifest = run["manifest"]
    run_id = manifest["run_id"]
    window = datetime.fromisoformat(manifest["window_start_utc"].replace("Z", "+00:00"))
    statuses, typed, generic, gaps = [], [], [], []
    rows_by_source: dict[str, list[dict]] = {}
    for row in run["signals"]:
        rows_by_source.setdefault(row["source_id"], []).append(row)
    for item in manifest["sources"]:
        spec, source = item["spec"], item["source"]
        ok = source["fetch_status"] == "ok" and source["parse_status"] == "ok"
        statuses.append({"run_id": run_id, "source_id": source["source_id"], "source_key": spec.get("source_key", ""),
                         "model_id": spec.get("model_id", ""), "route": "", "status": "ok" if ok else "failed",
                         "authority": "", "active": spec.get("source_key", "") not in catalog.disabled, "error": source["error"]})
        if not ok or spec.get("source_key") in catalog.disabled:
            continue
        reread = notices.reread(spec, (run["dir"] / source["raw_path"]).read_bytes(), window) if source["raw_path"] else None
        if reread is None:
            gaps.append({"kind": "signal_raw_unparsable", "run_id": run_id, "source_id": source["source_id"],
                         "source_key": spec.get("source_key", ""), "note": "registered parser cannot re-read the raw; rows stay leads"})
            parsed_keys = None
        else:
            parsed_keys = {notices.row_key(row) for row in reread[0]}
            typed += _typed_claims(reread[1], spec, source, run_id, catalog)
        for row in rows_by_source.get(source["source_id"], []):
            if parsed_keys is not None and notices.row_key(row) not in parsed_keys:
                gaps.append({"kind": "signal_row_mismatch", "run_id": run_id, "source_id": source["source_id"],
                             "signal_id": row["signal_id"], "source_key": spec.get("source_key", ""),
                             "note": "signal row is not produced by the registered parser from the preserved raw; row rejected"})
                continue
            for model_id in [value for value in row["model_ids"].split(";") if value]:
                info = catalog.signal_source(spec, model_id)
                if not info["route"]:
                    info = dict(info, route=stated_route(row["quote"], model_id, catalog))
                for kind in [value for value in row["claim_kinds"].split(";") if value]:
                    claim = _claim(row, spec, dict(source, fetched_at_utc=row["observed_at_utc"]), run_id, model_id, kind, info)
                    if parsed_keys is None:
                        claim["blockers"].append({"field": "raw_check", "class": "check_first",
                                                  "reason": "raw could not be re-read by the registered parser",
                                                  "next_check": "re-collect the source"})
                    if kind in notices.PRICE_KINDS and notices.ambiguous_row(row, model_id):
                        claim["blockers"].append(AMBIGUOUS)
                    generic.append(claim)
    # A typed claim reads the same entry more precisely; generic leads of the same kind family are dropped,
    # other kinds stay. Makers first so a channel add can find the maker's release date.
    covered = {(claim["source_id"], claim["entry_id"], claim["model_id"], kind) for claim in typed for kind in COVERS.get(claim["kind"], ())}
    typed.sort(key=lambda claim: claim["authority"] != "manufacturer_official")
    return typed + [c for c in generic if (c["source_id"], c["entry_id"], c["model_id"], c["kind"]) not in covered], statuses, gaps
