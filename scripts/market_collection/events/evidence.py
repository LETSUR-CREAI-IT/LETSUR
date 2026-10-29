"""Read core and signal runs into verified observations and claims.

Every run is checked with the collectors' own validators (core._validated_run,
signals.validated_run); a failure rejects the whole build. Adapters here are by
source format (core price rows, OpenRouter and Vercel endpoints JSON, atom/rss feed rows,
retirement schedule tables), never by model.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from scripts.market_collection import core
from scripts.market_collection import signals as signal_runs
from scripts.market_collection.collectors import parser_module
from scripts.market_collection.signals.registry import RETIREMENT_PARSERS

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


PACKAGE = "scripts.market_collection"


def parser_fingerprint(spec: dict) -> str | None:
    """Digest of the code that turns this spec's raw body into rows: the collector module that parses it and every
    collector module it imports (whole files), plus the core, models, and sources definitions they use (each top-level
    definition's source lines, followed through the names it references). An edit to another source's collector leaves
    it unchanged. None when no collector module parses the spec."""
    try:
        module = parser_module(core.SourceSpec(**spec))
    except (TypeError, ValueError):
        return None
    return _code_digest(module.__name__.removeprefix(PACKAGE + ".").replace(".", "/") + ".py")


@lru_cache(maxsize=None)
def _code_digest(start: str) -> str:
    base = Path(__file__).resolve().parents[1]
    modules: dict[str, tuple[list[str], ast.Module, dict]] = {}
    found: dict[str, str] = {}
    todo: list[tuple[str, str | None]] = [(start, None), ("core/__init__.py", "_validate_parse")]
    while todo:
        path, name = todo.pop()
        name = None if path.startswith("collectors/") else name
        key = path if name is None else f"{path}:{name}"
        if key in found:
            continue
        if path not in modules:
            text = (base / path).read_text(encoding="utf-8")
            tree = ast.parse(text)
            modules[path] = (text.splitlines(), tree, _symbols(base, path, tree))
        lines, tree, symbols = modules[path]
        nodes = [tree] if name is None else symbols.get(name)
        if isinstance(nodes, tuple):  # imported here from another package module
            todo.append(nodes)
            continue
        if not nodes:  # not a top-level definition: take the whole file
            todo.append((path, None))
            continue
        if name is None:
            found[key] = hashlib.sha256((base / path).read_bytes()).hexdigest()
        else:
            first = [min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]) for node in nodes]
            text = "\n".join("\n".join(lines[start - 1:node.end_lineno]) for start, node in zip(first, nodes))
            found[key] = hashlib.sha256(text.encode()).hexdigest()
        for node in (sub for top in nodes for sub in ast.walk(top)):
            if isinstance(node, ast.ImportFrom):
                todo += [target for target in _imported(base, path, node) if target]
            elif name is not None and isinstance(node, ast.Name) and node.id != name and node.id in symbols:
                target = symbols[node.id]
                todo.append(target if isinstance(target, tuple) else (path, node.id))
    return digest(sorted(found.items()), 16)


def _symbols(base: Path, path: str, tree: ast.Module) -> dict:
    """Top-level names of one module: their definition nodes, or the (module, name) a package import points to."""
    found: dict = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.setdefault(node.name, []).append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(node)
        elif isinstance(node, ast.ImportFrom):
            found.update((alias.asname or alias.name, target) for alias, target in zip(node.names, _imported(base, path, node)) if target)
    return found


def _imported(base: Path, path: str, node: ast.ImportFrom) -> list:
    """For each name of one `from ... import`, the package module file and name it refers to (name None for a whole
    module), or None outside the package."""
    if node.level:
        module = "/".join(path.split("/")[:-node.level] + ([node.module.replace(".", "/")] if node.module else []))
    elif node.module == PACKAGE or (node.module or "").startswith(PACKAGE + "."):
        module = node.module[len(PACKAGE) + 1:].replace(".", "/")
    else:
        return [None] * len(node.names)
    found = []
    for alias in node.names:
        sub = f"{module}/{alias.name}" if module else alias.name
        if (base / sub / "__init__.py").exists() or (base / f"{sub}.py").exists():
            found.append((_module_file(base, sub), None))
        else:
            found.append((_module_file(base, module), alias.name))
    return found


def _module_file(base: Path, module: str) -> str:
    return f"{module}/__init__.py" if (base / module / "__init__.py").exists() else f"{module}.py"


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


MAKER_PRICE_VIA_CHANNEL = "manufacturer list price change found in a channel notice"


def maker_list_price(quote: str, model_id: str) -> bool:
    """A channel sentence about the maker's own list price: the maker's name with "list pric…" or "Direct API".
    Its price move is the maker's, so the channel's route is not the card's route (user decision 2026-10-19)."""
    name = maker(model_id)
    return name != "unknown" and re.search(r"\b" + re.escape(name) + r"\b", quote, re.I) is not None \
        and re.search(r"\blist\s+pric|\bDirect\s+API\b", quote, re.I) is not None


AMBIGUOUS = {"field": "model_attribution", "class": "check_first",
             "reason": "the sentence or article names several models; the price or condition is not clearly attributed",
             "next_check": "read the notice and confirm which model the value applies to"}


def retirement_facts(entries: list, row: dict, model_id: str) -> dict:
    """The dated fields of the retirement schedule row (signals/retirements.py) a signal row was made from; versions counts
    the rows the page has for the model, each with its own date."""
    entry = next((entry for entry in entries if entry.entry_id == row["entry_id"] and entry.model_id == model_id), None)
    if entry is None:
        return {}
    return {"effective_from": entry.date, "date_basis": "not_before" if entry.earliest else "stated", "date_text": entry.date_text,
            "replacement": entry.replacement, "version": entry.version, "published_at_utc": entry.announced,
            "versions": sum(1 for other in entries if other.model_id == model_id)}


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
        retirements = None
        if reread is None:
            gaps.append({"kind": "signal_raw_unparsable", "run_id": run_id, "source_id": source["source_id"],
                         "source_key": spec.get("source_key", ""), "note": "registered parser cannot re-read the raw; rows stay leads"})
            parsed_keys = None
        else:
            parsed_keys = {notices.row_key(row) for row in reread[0]}
            if spec.get("parser") in RETIREMENT_PARSERS:
                retirements = reread[1]
            else:
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
                    parts = row["entry_id"].rsplit("#", 2)
                    if spec.get("parser") == "vercel_endpoints" and len(parts) == 3:
                        claim["endpoint_id"] = parts[1]  # <data.id>#<provider>#deprecated_at: the upstream that ends
                    if retirements is not None:
                        claim.update(retirement_facts(retirements, row, model_id))
                    if kind in ("price_increase", "price_decrease") and info["authority"] == "channel_official" \
                            and maker_list_price(row["quote"], model_id):
                        # The same sentence's discount kind keeps the channel route; only the maker's price move loses it.
                        claim.update(route="", announced_via={"route": info["route"], "note": MAKER_PRICE_VIA_CHANNEL})
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
