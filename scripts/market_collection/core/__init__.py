"""Bounded public-page collection and evidence-preserving exports."""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

MAX_BODY_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 20
MAX_REDIRECTS = 2
SOURCE_FIELDS = ("run_id", "source_id", "source_key", "model_id", "route", "requested_url", "effective_url", "fetched_at_utc", "http_status", "fetch_status", "parse_status", "raw_path", "raw_sha256", "body_bytes", "error")
PRICE_FIELDS = ("observation_id", "run_id", "source_id", "observed_at_utc", "modality", "model_id", "provider_model_id", "model_version", "route", "upstream_provider", "task", "billing_item", "price_kind", "amount", "currency", "unit", "normalized_amount", "normalized_unit", "condition_set_id", "effective_from", "effective_until", "locator", "evidence_status", "unknown_reason")
CONDITION_FIELDS = ("condition_set_id", "condition_key", "operator", "value", "unit", "status", "source_id", "locator")
ADAPTER_PRICE_FIELDS = ("provider_model_id", "model_version", "upstream_provider", "task", "billing_item", "price_kind", "amount", "currency", "unit", "normalized_amount", "normalized_unit", "condition_set_id", "effective_from", "effective_until", "locator", "evidence_status", "unknown_reason")
ADAPTER_CONDITION_FIELDS = ("condition_set_id", "condition_key", "operator", "value", "unit", "status", "locator")
PRICE_KINDS = {"published_rate", "announced_future_rate", "provider_estimate", "disputed_claim"}
EVIDENCE_STATUSES = {"direct", "inferred", "conflicted"}
CONDITION_STATUSES = {"stated", "unknown", "conflicted"}
ALLOWED_MODALITIES = {"llm", "image", "video"}
# Canonical output units are denominators for the quoted USD rate. The raw
# source's wording remains available through its locator and preserved body.
UNIT_SCALE = {
    "1 token": ("token", Decimal(1)),
    "1000 tokens": ("token", Decimal(1000)),
    "1K tokens": ("token", Decimal(1000)),
    "1M tokens": ("token", Decimal(1000000)),
    "1,000,000 tokens": ("token", Decimal(1000000)),
    "1 second": ("second", Decimal(1)),
    "1 image": ("image", Decimal(1)),
    "1 video": ("video", Decimal(1)),
    "1 request": ("request", Decimal(1)),
    "1 generation": ("generation", Decimal(1)),
}


@dataclass(frozen=True)
class SourceSpec:
    source_key: str
    modality: str
    model_id: str
    route: str
    task: str
    url: str
    parser: str


class CollectionError(ValueError):
    """Invalid registry, evidence, or export."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise CollectionError(f"invalid HTTPS source URL: {url}")
    if parsed.port not in (None, 443):
        raise CollectionError(f"nonstandard port in source URL: {url}")
    return parsed.hostname.lower()


def validate_registry(specs: list[SourceSpec]) -> list[SourceSpec]:
    seen: set[str] = set()
    for spec in specs:
        if not isinstance(spec, SourceSpec):
            raise CollectionError("registry contains a non-SourceSpec entry")
        if not all((spec.source_key, spec.model_id, spec.route, spec.task, spec.parser)):
            raise CollectionError("registry contains an empty required field")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", spec.source_key):
            raise CollectionError(f"invalid source_key: {spec.source_key}")
        if spec.source_key in seen:
            raise CollectionError(f"duplicate source_key: {spec.source_key}")
        seen.add(spec.source_key)
        if spec.modality not in ALLOWED_MODALITIES:
            raise CollectionError(f"invalid modality: {spec.modality}")
        _validate_url(spec.url)
    return specs


def registry() -> tuple[list[SourceSpec], dict[str, object]]:
    """Import only the three registered adapters, when a run needs them."""
    adapters = {name: importlib.import_module(f"scripts.market_collection.adapters.{name}") for name in ("llm", "image", "video")}
    specs: list[SourceSpec] = []
    for name, adapter in adapters.items():
        own = adapter.source_specs()
        if any(spec.modality != name for spec in own):
            raise CollectionError(f"{name} adapter registered another modality")
        specs.extend(own)
    return validate_registry(specs), adapters


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def fetch(spec: SourceSpec) -> tuple[bytes, str, int]:
    """GET one fixed URL, allowing at most two same-host HTTPS redirects."""
    original_host = _validate_url(spec.url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    url = spec.url
    for redirects in range(MAX_REDIRECTS + 1):
        request = urllib.request.Request(url, headers={"User-Agent": "LETSUR-public-price-collector/1.0", "Accept-Encoding": "identity"}, method="GET")
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
                chunk = response.read(min(65536, MAX_BODY_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise CollectionError("response body exceeds 4 MiB")
            return bytes(body), url, status
    raise CollectionError("redirect limit exceeded")


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def _write_json(path: Path, data: dict) -> None:
    _write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path, fields: tuple[str, ...]) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != fields:
            raise CollectionError(f"CSV header mismatch: {path}")
        return list(reader)


def _new_run(output_root: Path, mode: str) -> tuple[str, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}"
    run_dir = output_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_id, run_dir


def _validate_parse(spec: SourceSpec, source_id: str, price_rows: object, condition_rows: object) -> tuple[list[dict], list[dict]]:
    if not isinstance(price_rows, list) or not isinstance(condition_rows, list):
        raise CollectionError("parser must return two lists")
    condition_ids: set[str] = set()
    conditions: list[dict] = []
    for row in condition_rows:
        if not isinstance(row, dict) or set(row) != set(ADAPTER_CONDITION_FIELDS):
            raise CollectionError("invalid condition fields")
        if not row["condition_set_id"] or not row["condition_key"] or not row["locator"] or row["status"] not in CONDITION_STATUSES:
            raise CollectionError("invalid condition value")
        if not all(isinstance(value, str) for value in row.values()):
            raise CollectionError("condition values must be strings")
        condition_ids.add(row["condition_set_id"])
        conditions.append({**row, "source_id": source_id})
    prices: list[dict] = []
    for row in price_rows:
        if not isinstance(row, dict) or set(row) != set(ADAPTER_PRICE_FIELDS):
            raise CollectionError("invalid price fields")
        if not all(isinstance(value, str) for value in row.values()):
            raise CollectionError("price values must be strings")
        if row["price_kind"] not in PRICE_KINDS or row["evidence_status"] not in EVIDENCE_STATUSES or not row["locator"]:
            raise CollectionError("invalid price classification or locator")
        if row["condition_set_id"] and row["condition_set_id"] not in condition_ids:
            raise CollectionError("price references missing condition set")
        if row["task"] and row["task"] != spec.task:
            raise CollectionError("price task differs from registered source")
        for field in ("amount", "normalized_amount"):
            if row[field]:
                try:
                    value = Decimal(row[field])
                except InvalidOperation as exc:
                    raise CollectionError(f"invalid {field}") from exc
                if not value.is_finite() or value < 0:
                    raise CollectionError(f"invalid {field}")
        if row["amount"] and (not row["currency"] or not row["unit"]):
            raise CollectionError("amount lacks currency or unit")
        if row["currency"] and row["currency"] != "USD":
            raise CollectionError("unsupported currency")
        if row["unit"] and row["unit"] not in UNIT_SCALE:
            raise CollectionError("unsupported price unit")
        if row["normalized_amount"] or row["normalized_unit"]:
            if not row["amount"] or not row["normalized_amount"] or not row["normalized_unit"]:
                raise CollectionError("normalized amount lacks source amount or unit")
            if row["normalized_unit"] not in UNIT_SCALE:
                raise CollectionError("unsupported normalized unit")
            source_kind, source_scale = UNIT_SCALE[row["unit"]]
            normalized_kind, normalized_scale = UNIT_SCALE[row["normalized_unit"]]
            if source_kind != normalized_kind:
                raise CollectionError("normalized unit changes physical quantity")
            if Decimal(row["normalized_amount"]) != Decimal(row["amount"]) * normalized_scale / source_scale:
                raise CollectionError("normalized amount does not match unit conversion")
        if not row["amount"] and not row["unknown_reason"]:
            raise CollectionError("missing amount lacks unknown_reason")
        prices.append(row)
    return prices, conditions


def _record_source(run_id: str, source_id: str, spec: SourceSpec, fetched_at: str) -> dict:
    return {"run_id": run_id, "source_id": source_id, "source_key": spec.source_key, "model_id": spec.model_id, "route": spec.route, "requested_url": spec.url, "effective_url": "", "fetched_at_utc": fetched_at, "http_status": "", "fetch_status": "pending", "parse_status": "not_run", "raw_path": "", "raw_sha256": "", "body_bytes": "", "error": ""}


def _finish(run_dir: Path, manifest: dict, sources: list[dict], prices: list[dict], conditions: list[dict]) -> Path:
    _write_csv(run_dir / "sources.csv", SOURCE_FIELDS, sources)
    _write_csv(run_dir / "price_observations.csv", PRICE_FIELDS, prices)
    _write_csv(run_dir / "conditions.csv", CONDITION_FIELDS, conditions)
    _write_json(run_dir / "manifest.json", manifest)
    return run_dir


def live(output_root: Path | str, disable_source: tuple[str, ...] = (), *, specs: list[SourceSpec] | None = None, adapters: dict[str, object] | None = None, fetcher: Callable[[SourceSpec], tuple[bytes, str, int]] = fetch) -> Path:
    if specs is None or adapters is None:
        specs, adapters = registry()
    validate_registry(specs)
    unknown = set(disable_source) - {spec.source_key for spec in specs}
    if unknown:
        raise CollectionError(f"unknown disabled source: {sorted(unknown)}")
    run_id, run_dir = _new_run(Path(output_root), "live")
    sources: list[dict] = []
    prices: list[dict] = []
    conditions: list[dict] = []
    manifest = {"schema_version": 1, "mode": "live", "run_id": run_id, "created_at_utc": _utc_now(), "disabled_sources": sorted(set(disable_source)), "sources": []}
    for index, spec in enumerate(specs, 1):
        if spec.source_key in disable_source:
            continue
        source_id = f"{run_id}-s{index:03d}"
        source = _record_source(run_id, source_id, spec, _utc_now())
        sources.append(source)
        try:
            body, effective_url, status = fetcher(spec)
            if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
                raise CollectionError("invalid or oversized response body")
            if _validate_url(effective_url) != _validate_url(spec.url):
                raise CollectionError("effective URL changed host")
            source.update(effective_url=effective_url, http_status=str(status), fetch_status="ok" if status == 200 else "http_error", body_bytes=str(len(body)))
            raw_path = f"raw/{source_id}.bin"
            _write_bytes(run_dir / raw_path, body)
            source.update(raw_path=raw_path, raw_sha256=hashlib.sha256(body).hexdigest())
            if status != 200:
                continue
            parsed = adapters[spec.modality].parse_source(spec, body, source_id)
            if not isinstance(parsed, tuple) or len(parsed) != 2:
                raise CollectionError("parser must return a pair")
            own_prices, own_conditions = _validate_parse(spec, source_id, *parsed)
            for ordinal, row in enumerate(own_prices, 1):
                prices.append({"observation_id": f"{source_id}-p{ordinal:04d}", "run_id": run_id, "source_id": source_id, "observed_at_utc": source["fetched_at_utc"], "modality": spec.modality, "model_id": spec.model_id, **row, "route": spec.route})
            conditions.extend(own_conditions)
            source["parse_status"] = "ok"
        except Exception as exc:
            source["error"] = f"{type(exc).__name__}: {exc}"
            if source["fetch_status"] == "pending":
                source["fetch_status"] = "error"
            else:
                source["parse_status"] = "error"
        manifest["sources"].append({"spec": asdict(spec), "source": dict(source)})
    return _finish(run_dir, manifest, sources, prices, conditions)


def _validated_run(run_dir: Path) -> tuple[dict, list[dict], list[dict], list[dict]]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    sources = _read_csv(run_dir / "sources.csv", SOURCE_FIELDS)
    prices = _read_csv(run_dir / "price_observations.csv", PRICE_FIELDS)
    conditions = _read_csv(run_dir / "conditions.csv", CONDITION_FIELDS)
    if manifest.get("run_id") != run_dir.name or manifest.get("mode") not in {"live", "replay"}:
        raise CollectionError("manifest identity or mode mismatch")
    if [item.get("source") for item in manifest.get("sources", [])] != sources:
        raise CollectionError("manifest/source CSV mismatch")
    ids = {row["source_id"] for row in sources}
    if len(ids) != len(sources):
        raise CollectionError("duplicate source ID")
    for source in sources:
        if source["run_id"] != manifest["run_id"]:
            raise CollectionError("source run reference mismatch")
        if source["raw_path"]:
            raw_path = (run_dir / source["raw_path"]).resolve()
            if not raw_path.is_relative_to(run_dir.resolve()):
                raise CollectionError("raw path leaves run directory")
            body = raw_path.read_bytes()
            if len(body) != int(source["body_bytes"]) or hashlib.sha256(body).hexdigest() != source["raw_sha256"]:
                raise CollectionError(f"raw integrity failure: {source['source_id']}")
        elif source["raw_sha256"] or source["body_bytes"]:
            raise CollectionError("missing raw reference")
    condition_refs = {(row["source_id"], row["condition_set_id"]) for row in conditions}
    for row in prices:
        if row["run_id"] != manifest["run_id"] or row["source_id"] not in ids:
            raise CollectionError("price reference mismatch")
        if row["condition_set_id"] and (row["source_id"], row["condition_set_id"]) not in condition_refs:
            raise CollectionError("price condition reference mismatch")
    for row in conditions:
        if row["source_id"] not in ids:
            raise CollectionError("condition reference mismatch")
    return manifest, sources, prices, conditions


def replay(run_dir: Path | str, output_root: Path | str, *, adapters: dict[str, object] | None = None) -> Path:
    origin_dir = Path(run_dir)
    origin, old_sources, _, _ = _validated_run(origin_dir)
    if origin["mode"] != "live":
        raise CollectionError("replay requires a live origin")
    if adapters is None:
        _, adapters = registry()
    run_id, destination = _new_run(Path(output_root), "replay")
    sources: list[dict] = []
    prices: list[dict] = []
    conditions: list[dict] = []
    manifest = {"schema_version": 1, "mode": "replay", "run_id": run_id, "origin_run_id": origin["run_id"], "created_at_utc": _utc_now(), "disabled_sources": origin.get("disabled_sources", []), "sources": []}
    for index, (item, old) in enumerate(zip(origin["sources"], old_sources), 1):
        spec = SourceSpec(**item["spec"])
        source_id = f"{run_id}-s{index:03d}"
        source = dict(old, run_id=run_id, source_id=source_id)
        sources.append(source)
        if old["raw_path"]:
            body = (origin_dir / old["raw_path"]).read_bytes()
            new_path = f"raw/{source_id}.bin"
            _write_bytes(destination / new_path, body)
            source["raw_path"] = new_path
            if old["fetch_status"] == "ok":
                try:
                    parsed = adapters[spec.modality].parse_source(spec, body, source_id)
                    if not isinstance(parsed, tuple) or len(parsed) != 2:
                        raise CollectionError("parser must return a pair")
                    own_prices, own_conditions = _validate_parse(spec, source_id, *parsed)
                    for ordinal, row in enumerate(own_prices, 1):
                        prices.append({"observation_id": f"{source_id}-p{ordinal:04d}", "run_id": run_id, "source_id": source_id, "observed_at_utc": old["fetched_at_utc"], "modality": spec.modality, "model_id": spec.model_id, **row, "route": spec.route})
                    conditions.extend(own_conditions)
                    source["parse_status"] = "ok"
                    source["error"] = ""
                except Exception as exc:
                    source["parse_status"] = "error"
                    source["error"] = f"{type(exc).__name__}: {exc}"
        manifest["sources"].append({"spec": asdict(spec), "source": dict(source)})
    return _finish(destination, manifest, sources, prices, conditions)


def history(output_root: Path | str, output_dir: Path | str) -> Path:
    root = Path(output_root)
    destination = Path(output_dir)
    if destination.exists():
        raise CollectionError(f"history destination exists: {destination}")
    sources: list[dict] = []
    prices: list[dict] = []
    conditions: list[dict] = []
    for run_dir in sorted((root / "runs").glob("*")):
        if not run_dir.is_dir():
            continue
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("mode") != "live":
            continue
        _, own_sources, own_prices, own_conditions = _validated_run(run_dir)
        sources.extend(own_sources)
        prices.extend(own_prices)
        conditions.extend(own_conditions)
    destination.mkdir(parents=True, exist_ok=False)
    _write_csv(destination / "sources.csv", SOURCE_FIELDS, sources)
    _write_csv(destination / "price_observations.csv", PRICE_FIELDS, prices)
    _write_csv(destination / "conditions.csv", CONDITION_FIELDS, conditions)
    return destination
