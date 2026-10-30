"""Which failures need someone to act (2026-10-29 박건): kind, 판단, action and fix of a failed sources.csv row."""

import pytest

from scripts.market_collection.core.failures import KINDS, REPEAT_RUNS, classify


def _row(fetch="ok", parse="error", http="200", error=""):
    return {"fetch_status": fetch, "parse_status": parse, "http_status": http, "error": error}


def _fetch_error(error):
    return _row("error", "not_run", "", error)


def _http(code):
    return _row("http_error", "not_run", code)


# Strings sources.csv and the engine's source_failed gaps really carried, then one made-up string per rule.
TEMPORARY = [
    _fetch_error("TimeoutError: The read operation timed out"),  # 10/28 openrouter_claude_haiku_4_5, back the next day
    _fetch_error("URLError: <urlopen error [Errno 8] nodename nor servname provided, or not known>"),
    _fetch_error("URLError: <urlopen error _ssl.c:983: The handshake operation timed out>"),
    _fetch_error("ConnectionResetError: [Errno 104] Connection reset by peer"),
    _fetch_error("RemoteDisconnected: Remote end closed connection without response"),
    _fetch_error("SSLError: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:2427)"),
    _fetch_error("IncompleteRead: IncompleteRead(100 bytes read, 20 more expected)"),
    _fetch_error("BrokenPipeError: [Errno 32] Broken pipe"),
    _http("408"), _http("429"), _http("500"), _http("502"), _http("503"), _http("504"),
    _row("ok", "error", "200", "ValueError: BytePlus unrendered SSR template: document body unavailable"),
]
ACCESS = [
    _http("401"), _http("403"), _http("404"), _http("410"), _http("400"), _http("451"), _http("304"),
    _fetch_error("CollectionError: redirect changed host"),
    _fetch_error("CollectionError: effective URL changed host"),
    _fetch_error("CollectionError: redirect limit exceeded"),
    _fetch_error("CollectionError: redirect has no Location"),
]
MISSING = [
    _row(error="ValueError: Google Veo exact preview identity absent"),  # google_veo_3_1_*, 10/22: the model left the page
    _row(error="ValueError: fal Seedance 2.5 token rate or unit missing"),
    _row(error="ValueError: BytePlus Seedance 2.5 model row or USD/M token unit missing"),
    _row(error="ValueError: Azure duplicate or missing meter"),
    _row(error="ValueError: Azure exact model five-meter set incomplete"),
    _row(error="ValueError: Google pricing page has 0 sections with id gemini-x"),
    _row(error="ValueError: Anthropic price row absent, duplicated or reformatted: Claude Haiku 4.5"),
    _row(error="ValueError: Google pricing: gemini-x has no Standard table"),
    _row(error="ValueError: Vercel gemini-x does not list the text operation"),
]
FORMAT = [
    _row(error="ValueError: n prose/schema constraints disagree"),  # image25_openai_generate_*_doc
    _row(error="ValueError: Google pricing page has 2 sections with id gemini-x"),
    _row(error="ValueError: Vercel token rate has unknown or missing fields"),  # a changed record layout, not a missing model
    _row(error="CollectionError: invalid price fields"),  # core's validation of what the parser returned
    _row(error="CollectionError: amount lacks currency or unit"),
    _row(error="ValueError: reference row lacks pipe boundaries"),
    _row(error="JSONDecodeError: Expecting value: line 1 column 1 (char 0)"),
    _row(error="KeyError: 'models'"),
    _row(parse="failed", error="ValueError: layout changed"),  # any parse_status that is not ok
    _row(error=""),
    _row("error", "error", "", "CollectionError: response body exceeds 4 MiB"),
    _fetch_error("CollectionError: invalid or oversized response body"),
]
UNKNOWN = [
    _row("pending", "not_run", ""),
    _row("error", "not_run", "", ""),
    _fetch_error("ValueError: unknown url type: 'htp://x'"),
    _row("http_error", "not_run", ""),
    _row("ok", "not_run", "200"),
]


@pytest.mark.parametrize("kind, rows", [("temporary", TEMPORARY), ("access", ACCESS), ("missing", MISSING), ("format", FORMAT), ("unknown", UNKNOWN)])
def test_each_failure_gets_its_kind(kind, rows):
    for row in rows:
        assert classify(row)["kind"] == kind, row
        assert classify(row)["label"] == KINDS[kind][0]


def test_a_retry_that_raised_is_still_a_fetch_error():
    # core.live marks parse_status "error" as well when a later attempt raised; the text is still the fetch's
    row = _row("error", "error", "", "TimeoutError: timed out")
    assert classify(row)["kind"] == "temporary"


def test_labels_and_actions_say_what_to_do():
    assert [KINDS[kind][0] for kind in ("temporary", "access", "missing", "format", "unknown")] == [
        "일시 오류", "접근 문제", "항목 없음", "수집기 수정 필요", "확인 필요"]
    assert classify(_http("503"))["action"] == "다음 수집에서 다시 시도합니다. 2회 연속이면 출처를 확인하세요"
    assert classify(_http("404"))["action"] == "출처 주소나 접근이 바뀌었는지 확인하고 주소를 고치거나 출처를 퇴역하세요"
    assert classify(MISSING[0])["action"] == "페이지에서 이 모델이 빠졌는지 확인하세요. 퇴역이면 등록부에서 빼고, 아니면 수집기를 고치세요"
    assert classify(FORMAT[0])["action"] == "원문을 열어 바뀐 형식에 맞게 수집기를 고치세요"
    assert all(classify(row)["action"] for rows in (TEMPORARY, ACCESS, MISSING, FORMAT, UNKNOWN) for row in rows)


def test_only_a_repeated_temporary_error_waits_for_the_next_run():
    assert REPEAT_RUNS == 2
    first, second = classify(TEMPORARY[0], 1), classify(TEMPORARY[0], 2)
    assert (first["fix"], second["fix"]) == (False, True) and classify(TEMPORARY[0], 5)["fix"]
    assert first["action"] == KINDS["temporary"][1] != second["action"]  # at two runs the line is no longer "we retry"
    for rows in (ACCESS, MISSING, FORMAT, UNKNOWN):
        assert all(classify(row, 1)["fix"] for row in rows)
    assert classify(MISSING[0], 1)["action"] == KINDS["missing"][1]


def test_a_row_without_the_optional_columns_still_classifies():
    assert classify({})["kind"] == "unknown"
    assert classify({"fetch_status": "ok", "parse_status": "error", "error": "ValueError: row absent"})["kind"] == "missing"
