"""OpenRouter endpoint rows through a real events build (synthetic endpoint bodies, real parser).

- first observation of the new endpoint source is a baseline, never a price_change;
- same input repeated: nothing new, nothing revised;
- same endpoint conditions with only the number changed: a confirmed price_change;
- quantization unknown/missing: no event, reported as not_comparable (condition_unknown).
"""

from __future__ import annotations

import json

from scripts.market_collection import core
from scripts.market_collection.collectors.llm_endpoints import parse_openrouter
from scripts.market_collection.sources import source_specs

from test_catalog_and_diff import RUN_A, RUN_B, RUN_C, ok_build, write_run, _spec

def _key() -> str:
    keys = [s.source_key for s in source_specs() if s.source_key.startswith("openrouter_llm_endpoints_") and "glm" in s.source_key]
    return keys[0]


def body(prompt: str, quantization="fp16", status=0, discount=0) -> bytes:
    endpoint = {"name": "Relace | z-ai/glm-5.2", "context_length": 1048576, "provider_name": "Relace", "tag": "relace",
                "service_tier": None, "quantization": quantization, "status": status,
                "pricing": {"prompt": prompt, "completion": "0.000006", "discount": discount}}
    if quantization == "__missing__":
        del endpoint["quantization"]
    return json.dumps({"data": {"id": "z-ai/glm-5.2", "endpoints": [endpoint]}}).encode()


def entry(prompt: str, quantization="fp16", **extra) -> dict:
    spec = _spec(_key())
    raw = body(prompt, quantization, **extra)
    return {"spec": spec, "body": raw, "rows": parse_openrouter(core.SourceSpec(**spec), raw)}


def price_changes(events: dict) -> list[dict]:
    return [e for e in events["events"] if e["event_type"] == "price_change" and e["source_key"] == _key()]


def test_first_observation_repeat_and_number_only_change(tmp_path):
    a = write_run(tmp_path, RUN_A, "2026-10-13T00:17:00Z", [entry("0.0000014")])
    b = write_run(tmp_path, RUN_B, "2026-10-14T00:17:00Z", [entry("0.0000014")])
    c = write_run(tmp_path, RUN_C, "2026-10-15T00:17:00Z", [entry("0.0000015")])
    first = ok_build(tmp_path / "first", a)
    assert price_changes(first) == [] and first["baselines"]
    repeat = ok_build(tmp_path / "repeat", a, b)
    assert repeat["events"] == []
    again = ok_build(tmp_path / "again", a, b, extra=("--state", str(tmp_path / "repeat" / "state.json")))
    report = json.loads((tmp_path / "again" / "report.json").read_text())
    assert report["new_events"] == [] and report["revised_events"] == [] and again["events"] == []
    moved = ok_build(tmp_path / "moved", a, b, c)
    [event] = [e for e in price_changes(moved) if e["billing_item"] == "input"]
    assert event["status"] == "confirmed" and event["before"]["amount"] == "1.4" and event["after"]["amount"] == "1.5"
    assert event["conditions"]["quantization"] == "fp16"
    assert [e for e in price_changes(moved) if e["billing_item"] == "output"] == []


def test_unknown_or_missing_quantization_is_not_comparable_and_makes_no_event(tmp_path):
    for label, q in (("unknown", "unknown"), ("missing", "__missing__"), ("null", None)):
        root = tmp_path / label
        a = write_run(root, RUN_A, "2026-10-13T00:17:00Z", [entry("0.0000014", q)])
        b = write_run(root, RUN_B, "2026-10-14T00:17:00Z", [entry("0.0000015", q)])
        out = ok_build(root / "out", a, b)
        assert price_changes(out) == []
        reasons = {(n["reason"], n["billing_item"]) for n in out["not_comparable"] if n.get("source_key") == _key()}
        assert ("condition_unknown", "input") in reasons


def test_inactive_status_and_nonzero_discount_block_a_price_change(tmp_path):
    for label, extra in (("inactive", {"status": -5}), ("discount", {"discount": 0.25})):
        root = tmp_path / label
        a = write_run(root, RUN_A, "2026-10-13T00:17:00Z", [entry("0.0000014", **extra)])
        b = write_run(root, RUN_B, "2026-10-14T00:17:00Z", [entry("0.0000015", **extra)])
        out = ok_build(root / "out", a, b)
        assert price_changes(out) == []
        assert any(n["reason"] == "condition_unknown" and n["billing_item"] == "input" and n.get("source_key") == _key() for n in out["not_comparable"])
