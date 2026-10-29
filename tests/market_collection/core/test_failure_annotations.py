"""GitHub Actions lines that name each failed source and say whether someone must act (core live CLI)."""

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
    # a timeout is temporary: a warning that says it is tried again (2026-10-29)
    run = _run(tmp_path, [_row("fine"), _row("openrouter_claude_haiku_4_5", fetch="error", parse="not_run", http="",
                                              error="TimeoutError: The read operation timed out")])
    assert failure_annotations(run) == [
        "::warning title=일시 오류 · claude-haiku-4-5 · openrouter::다음 수집에서 다시 시도합니다. 2회 연속이면 출처를 확인하세요"
        " · openrouter_claude_haiku_4_5 · 수집기 parser_openrouter_claude_haiku_4_5"
        " · 가져오기 error · 해석 not_run · TimeoutError: The read operation timed out"]


def test_http_status_parse_error_and_escaping(tmp_path):
    run = _run(tmp_path, [_row("blocked", model="m:1,2", fetch="http_error", parse="not_run", http="403"),
                          _row("changed", parse="error", error="ValueError: 100%\nrow layout changed")])
    assert failure_annotations(run) == [
        "::error title=접근 문제 · m%3A1%2C2 · openrouter::출처 주소나 접근이 바뀌었는지 확인하고 주소를 고치거나 출처를 퇴역하세요"
        " · blocked · 수집기 parser_blocked · 가져오기 http_error (HTTP 403) · 해석 not_run",
        "::error title=수집기 수정 필요 · claude-haiku-4-5 · openrouter::원문을 열어 바뀐 형식에 맞게 수집기를 고치세요"
        " · changed · 수집기 parser_changed · 가져오기 ok · 해석 error · ValueError: 100%25%0Arow layout changed"]


def test_the_title_starts_with_the_judgement_and_fixes_come_first(tmp_path):
    rows = [_row("slow", fetch="error", parse="not_run", http="", error="TimeoutError: timed out"),
            _row("down", fetch="http_error", parse="not_run", http="503"),
            _row("gone", parse="error", error="ValueError: Google Veo exact preview identity absent"),
            _row("moved", fetch="error", parse="not_run", http="", error="CollectionError: redirect changed host"),
            _row("odd", parse="error", error="KeyError: 'models'")]
    lines = failure_annotations(_run(tmp_path, rows))
    assert [(line.split(" ")[0], line.split(" title=")[1].split(" · ")[0]) for line in lines] == [
        ("::error", "항목 없음"), ("::error", "접근 문제"), ("::error", "수집기 수정 필요"), ("::warning", "일시 오류"), ("::warning", "일시 오류")]
    assert [line.split("::")[2].split(" · ")[1] for line in lines] == ["gone", "moved", "odd", "slow", "down"]  # the failing order within a group


def test_no_failures_and_the_per_step_limit(tmp_path):
    assert failure_annotations(_run(tmp_path, [_row("fine")])) == []
    many = [_row(f"s{i}", fetch="error") for i in range(ANNOTATION_LIMIT + 3)]
    lines = failure_annotations(_run(tmp_path, many, "many"))
    assert len(lines) == ANNOTATION_LIMIT and lines[-1].startswith("::error title=수집 실패 외 4개::")
    # what needs action is not the part cut off: temporary errors listed first still come last
    mixed = [_row(f"t{i}", fetch="error", error="TimeoutError: timed out") for i in range(ANNOTATION_LIMIT)] + [_row("fix", parse="error")]
    lines = failure_annotations(_run(tmp_path, mixed, "mixed"))
    assert len(lines) == ANNOTATION_LIMIT and lines[0].startswith("::error title=수집기 수정 필요 ·") and lines[-1].startswith("::error title=수집 실패 외 2개::")
