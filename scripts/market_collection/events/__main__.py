"""python -m scripts.market_collection.events {catalog,build,legacy}  (see docs/events.md)"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.market_collection.core import CollectionError

from . import DEFAULT_POLICY, SCHEMA_VERSION
from .catalog import Catalog
from .engine import Builder, empty_state, output
from .evidence import RunRejected, digest, load_run


def _write(directory: Path, name: str, data: dict) -> None:
    (directory / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _reject(out: Path, errors: list[str]) -> int:
    out.mkdir(parents=True, exist_ok=False)
    _write(out, "report.json", {"status": "rejected", "errors": errors})
    print("\n".join(errors), file=sys.stderr)
    return 2


def _manual_claims(path: Path) -> list[dict]:
    """Manual evidence (e.g. an X or Threads post captured by a person) is never official."""
    claims = []
    for item in json.loads(path.read_text(encoding="utf-8")):
        lineage = item.get("lineage") or item.get("url", "")
        claims.append({**{k: item.get(k, "") for k in ("kind", "model_id", "route", "quote", "url", "published_at_utc",
                                                        "observed_at_utc", "date_phrases", "locator")},
                       "authority": "unofficial", "role": item.get("role", "social"), "publisher": "unverified",
                       "scope": [], "source_key": "manual:" + item.get("role", "social"), "run_id": "", "source_id": "",
                       "raw_path": "", "raw_sha256": item.get("raw_sha256", ""), "lineage": lineage,
                       # Same strong identity as signal claims: the page plus the quoted sentence, never the URL alone.
                       "anchor": lineage + "#" + digest(item.get("quote", ""), 12),
                       "claim_id": digest(["manual", lineage, item.get("model_id", ""), item.get("kind", ""), item.get("quote", "")])})
    return claims


def build(args: argparse.Namespace) -> int:
    out = Path(args.output)
    if out.exists():
        print(f"output exists: {out}", file=sys.stderr)
        return 2
    if args.policy != DEFAULT_POLICY:
        print(f"unsupported policy: {args.policy}", file=sys.stderr)
        return 2
    try:
        catalog = Catalog(tuple(args.disable_source))
    except CollectionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    runs, errors, seen = [], [], set()
    for path in args.run:
        try:
            run = load_run(Path(path))
        except RunRejected as exc:
            errors.append(str(exc))
            continue
        if run["manifest"]["run_id"] not in seen:
            seen.add(run["manifest"]["run_id"])
            runs.append(run)
    state = empty_state()
    if args.state:
        try:
            state = json.loads(Path(args.state).read_text(encoding="utf-8"))
            if state.get("schema_version") != SCHEMA_VERSION or state.get("policy") != DEFAULT_POLICY:
                errors.append(f"state {args.state}: schema or policy mismatch")
        except (OSError, ValueError) as exc:
            errors.append(f"state {args.state}: {exc}")
    if errors:
        return _reject(out, errors)
    runs.sort(key=lambda run: (run["manifest"].get("created_at_utc", ""), run["manifest"]["run_id"]))
    builder = Builder(catalog, state)
    builder.build(runs)
    for path in args.manual:
        for claim in _manual_claims(Path(path)):
            if claim["claim_id"] not in builder.state["claims_seen"]:
                builder.state["claims_seen"].append(claim["claim_id"])
                builder.claim(claim)
    events, report = output(builder)
    out.mkdir(parents=True, exist_ok=False)
    _write(out, "state.json", builder.state)
    _write(out, "events.json", events)
    _write(out, "report.json", report)
    return 0


def legacy(args: argparse.Namespace) -> int:
    from .legacy import convert
    out = Path(args.output)
    if out.exists():
        print(f"output exists: {out}", file=sys.stderr)
        return 2
    builder = Builder(Catalog())
    results = convert([Path(p) for p in args.event], Path(args.inputs) if args.inputs else None, builder)
    events, report = output(builder)
    report["legacy"] = results
    out.mkdir(parents=True, exist_ok=False)
    _write(out, "events.json", events)
    _write(out, "report.json", report)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.market_collection.events")
    sub = parser.add_subparsers(dest="command", required=True)
    cat = sub.add_parser("catalog", help="print registered sources per model as JSON")
    cat.add_argument("--disable-source", action="append", default=[])
    run = sub.add_parser("build", help="core/signal runs (+ previous state) -> state.json, events.json, report.json")
    run.add_argument("--run", action="append", default=[], help="core or signal run directory; repeatable")
    run.add_argument("--state", help="state.json from the previous build")
    run.add_argument("--output", required=True, help="new directory; must not exist")
    run.add_argument("--policy", default=DEFAULT_POLICY)
    run.add_argument("--disable-source", action="append", default=[], help="registered source_key to treat as inactive")
    run.add_argument("--manual", action="append", default=[], help="JSON list of manual claims; always unofficial candidates")
    old = sub.add_parser("legacy", help="convert the five 0.1-experiment event.json files with the common rules")
    old.add_argument("--event", action="append", required=True)
    old.add_argument("--inputs", help="root that raw_ref paths resolve against, to verify raw SHA256")
    old.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "catalog":
        try:
            print(json.dumps(Catalog(tuple(args.disable_source)).as_json(), ensure_ascii=False, indent=2))
        except CollectionError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    if args.command == "build":
        return build(args)
    return legacy(args)


if __name__ == "__main__":
    sys.exit(main())
