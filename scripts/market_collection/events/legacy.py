"""Convert the five 0.1-experiment events into claims judged by the common rules.

The legacy event_type, event_status and wording are not trusted: each file's
recorded evidence (subject, quotes, dates, raw_ref + raw_sha256) becomes a claim,
authority comes from the registered route publisher, and classify_claim decides.
A claim is confirmed only when every cited raw body is found under --inputs with
the recorded SHA256; otherwise it stays a candidate with a raw_not_verified gap.
Inputs are read only. demo_internal_routes.json is never read.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .engine import Builder, legacy_authority


def _raw_verified(sources: list[dict], inputs: Path | None) -> tuple[bool, list[str]]:
    if inputs is None:
        return False, ["no --inputs root given"]
    problems = []
    for source in sources:
        if not source.get("raw_ref"):
            problems.append(f"{source.get('source_id')}: no raw_ref")
            continue
        path = (inputs / source["raw_ref"]).resolve()
        if not path.is_relative_to(inputs.resolve()) or not path.is_file():
            problems.append(f"{source.get('source_id')}: raw not found")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != source.get("raw_sha256"):
            problems.append(f"{source.get('source_id')}: raw SHA256 mismatch")
    return not problems, problems


def _claims(event: dict) -> list[dict]:
    subject, changes = event["subject"], event.get("changes", {})
    model_id, route = subject["model_id"], subject["route_id"]
    level, scope = legacy_authority(model_id, route)
    sources = event.get("sources", [])
    first = sources[0] if sources else {}
    base = {"model_id": model_id, "route": route, "authority": level, "scope": scope, "role": "legacy_evidence",
            "publisher": "", "source_key": "legacy:" + event["event_id"], "run_id": "", "source_id": first.get("source_id", ""),
            "observation_id": "", "raw_path": first.get("raw_ref", ""), "raw_sha256": first.get("raw_sha256", ""),
            "locator": first.get("locator", ""), "url": first.get("url", ""), "lineage": first.get("url", ""),
            "quote": "\n".join(s.get("quote", "") for s in sources), "published_at_utc": "", "observed_at_utc": first.get("observed_at", "")}
    dates = {d["kind"]: d for d in event.get("dates", [])}
    kind = event["event_type"]
    if kind == "price_change" and changes.get("future_rate_from"):
        return [dict(base, kind="announced_future_rate", billing_item=row["billing_item"], effective_from=changes["future_rate_from"],
                     after_amount=str(row["after"]["amount"]), anchor=f"{route}:{row['billing_item']}:{row['before']['conditions']}",
                     before={"amount": str(row["before"]["amount"]), "currency": row["before"]["currency"], "unit": row["before"]["unit"]},
                     after={"amount": str(row["after"]["amount"]), "currency": row["after"]["currency"], "unit": row["after"]["unit"]})
                for row in changes.get("prices", [])]
    if kind == "billing_condition_change":
        before, after = changes["before"], changes["after"]
        return [dict(base, kind="condition", condition_key=f"{changes['billing_item']}_multiplier", billing_item=changes["billing_item"],
                     before={"multiplier": str(before["cache_read_multiplier"]["value"]), "amount": str(before["cache_read"]["value"]),
                             "currency": "USD", "unit": "1M tokens"},
                     after={"multiplier": str(after["cache_read_multiplier"]["value"]), "amount": str(after["cache_read"]["value"]),
                            "currency": "USD", "unit": "1M tokens"},
                     effective_from=(dates.get("effective_from") or {}).get("date"))]
    if kind == "service_end":
        return [dict(base, kind="endpoint_deprecation", endpoint_id=changes.get("endpoint_id"), model_wide=False,
                     effective_from=(dates.get("deprecation_effective_from") or {}).get("date"))]
    if kind == "model_release":
        confirmed_release = changes.get("manufacturer_original_launch_confirmed") is True
        return [dict(base, kind="availability", maker_first_release_date=None if not confirmed_release else changes.get("effective_date"),
                     route_added_date=changes.get("effective_date"))]
    if kind == "limited_discount":
        end = dates.get("discount_end") or {}
        return [dict(base, kind="discount", end_date=end.get("date"), end_date_qualifier=end.get("qualifier"))]
    return []


def convert(paths: list[Path], inputs: Path | None, builder: Builder) -> list[dict]:
    results = []
    for path in paths:
        event = json.loads(path.read_text(encoding="utf-8"))
        verified, problems = _raw_verified(event.get("sources", []), inputs)
        claims = _claims(event)
        before_ids = set(builder.state["events"])
        for claim in claims:
            builder.claim(dict(claim, raw_verified=verified))
        new_ids = [event_id for event_id in builder.state["events"] if event_id not in before_ids]
        for event_id in new_ids:
            converted = builder.state["events"][event_id]
            converted["legacy_event_id"] = event["event_id"]
            converted["legacy_event_type"] = event["event_type"]
            if not verified and converted["status"] == "confirmed":
                converted["status"] = "candidate"
                converted["gaps"].append({"field": "raw_check", "class": "check_first", "reason": "raw_not_verified: " + "; ".join(problems),
                                          "next_check": "run legacy with --inputs pointing at the preserved raw files"})
        results.append({"legacy_event_id": event["event_id"], "legacy_event_type": event["event_type"], "raw_verified": verified,
                        "raw_problems": problems, "claims": len(claims), "events": new_ids})
    return results
