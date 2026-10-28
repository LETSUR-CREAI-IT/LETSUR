"""GitHub Actions error lines that name each failed source (core live CLI)."""

import csv
import json

from scripts.market_collection.core import SOURCE_FIELDS
from scripts.market_collection.core.__main__ import ANNOTATION_LIMIT, failure_annotations


def _run(tmp_path, rows, name="run"):
    run = tmp_path / name
    run.mkdir()
    specs = [{"source": {}, "spec": {"source_key": row["source_key"], "parser": "parser_" + row["source_key"]}} for row in rows]
    (run / "manifest.json").write_text(json.dumps({"sources": specs}), encoding="utf-8")
    with (run / "sources.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, SOURCE_FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in SOURCE_FIELDS} for row in rows)
    return run


def _row(key, *, model="claude-haiku-4-5", fetch="ok", parse="ok", http="200", error=""):
    return {"source_key": key, "model_id": model, "route": "openrouter", "fetch_status": fetch, "parse_status": parse,
            "http_status": http, "error": error}


def test_failed_source_names_model_route_collector_and_reason(tmp_path):
    run = _run(tmp_path, [_row("fine"), _row("openrouter_claude_haiku_4_5", fetch="error", parse="not_run", http="",
                                              error="TimeoutError: The read operation timed out")])
    assert failure_annotations(run) == [
        "::error title=수집 실패 · claude-haiku-4-5 · openrouter::openrouter_claude_haiku_4_5 · 수집기 parser_openrouter_claude_haiku_4_5"
        " · 가져오기 error · 해석 not_run · TimeoutError: The read operation timed out"]


def test_http_status_parse_error_and_escaping(tmp_path):
    run = _run(tmp_path, [_row("blocked", model="m:1,2", fetch="http_error", parse="not_run", http="403"),
                          _row("changed", parse="error", error="ValueError: 100%\nrow missing")])
    assert failure_annotations(run) == [
        "::error title=수집 실패 · m%3A1%2C2 · openrouter::blocked · 수집기 parser_blocked · 가져오기 http_error (HTTP 403) · 해석 not_run",
        "::error title=수집 실패 · claude-haiku-4-5 · openrouter::changed · 수집기 parser_changed · 가져오기 ok · 해석 error"
        " · ValueError: 100%25%0Arow missing"]


def test_no_failures_and_the_per_step_limit(tmp_path):
    assert failure_annotations(_run(tmp_path, [_row("fine")])) == []
    many = [_row(f"s{i}", fetch="error") for i in range(ANNOTATION_LIMIT + 3)]
    lines = failure_annotations(_run(tmp_path, many, "many"))
    assert len(lines) == ANNOTATION_LIMIT and lines[-1].startswith("::error title=수집 실패 외 4개::")
