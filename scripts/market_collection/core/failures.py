"""Why one source failed and whether someone must act now (2026-10-29 박건: 수집기를 고쳐야 하는 실패만 가려서 알린다).

classify() reads one failed sources.csv row. The 수집 상태 screen, the Slack bot's cards.json and the GitHub error lines all call
it, so the three say the same thing. Neither core/__init__.py nor a collector imports this module, so editing it moves no
parser fingerprint.
"""

import re

# kind: (판단, what to do)
KINDS = {
    "temporary": ("일시 오류", "다음 수집에서 다시 시도합니다. 2회 연속이면 출처를 확인하세요"),
    "access": ("접근 문제", "출처 주소나 접근이 바뀌었는지 확인하고 주소를 고치거나 출처를 퇴역하세요"),
    "missing": ("항목 없음", "페이지에서 이 모델이 빠졌는지 확인하세요. 퇴역이면 등록부에서 빼고, 아니면 수집기를 고치세요"),
    "format": ("수집기 수정 필요", "원문을 열어 바뀐 형식에 맞게 수집기를 고치세요"),
    "unknown": ("확인 필요", "원인을 알 수 없습니다. 오류 내용과 원문을 열어 확인하세요"),
}
REPEAT_RUNS = 2  # a temporary error is acted on once the source has failed this many runs in a row
REPEATED = "연속으로 실패했습니다. 출처가 열리는지, 주소나 접근이 바뀌었는지 확인하세요"

# core.live records an error as f"{type(exc).__name__}: {exc}", so the first word is the exception type.
NETWORK = re.compile(r"^(?:\w*(?:Timeout|Connection|SSL)\w*|URLError|OSError|gaierror|herror|BrokenPipeError|RemoteDisconnected"
                     r"|IncompleteRead|BadStatusLine|HTTPException)\b|timed out", re.I)
ACCESS_ERROR = re.compile(r"^CollectionError: (?:redirect|effective URL changed host)")
BODY_ERROR = re.compile(r"^CollectionError: (?:response body|invalid or oversized)")
# BytePlus sometimes serves an empty page shell with HTTP 200; core retries it three times, so what is left is the site's hiccup.
SHELL = re.compile(r"unrendered", re.I)
# The parser says the model, row, section, meter or identity is not on the page. The missing action also tells the person to
# fix the collector, so a page that merely changed its wording lands here safely. Not this kind: "unknown or missing fields"
# (a changed record layout) and CollectionError (core's validation of what the parser returned).
MISSING = re.compile(r"\b(?:absent|missing|incomplete|has no|0 sections|not (?:found|listed)|does not (?:list|state))\b", re.I)
NOT_MISSING = re.compile(r"^CollectionError|\bfields?\b", re.I)


def _kind_of(row: dict) -> str:
    fetch, parse = row.get("fetch_status") or "", row.get("parse_status") or ""
    status, error = (row.get("http_status") or "").strip(), (row.get("error") or "").strip()
    if fetch == "http_error":
        code = int(status) if status.isdecimal() else 0
        if code in (408, 429) or 500 <= code < 600:
            return "temporary"
        return "access" if 300 <= code < 500 else "unknown"
    if fetch == "error":  # the fetch itself raised (parse_status reads "error" too when a retry raised)
        if ACCESS_ERROR.search(error):
            return "access"
        if BODY_ERROR.search(error):
            return "format"
        return "temporary" if NETWORK.search(error) else "unknown"
    if fetch == "ok" and parse not in ("ok", "not_run"):
        if SHELL.search(error):
            return "temporary"
        return "missing" if MISSING.search(error) and not NOT_MISSING.search(error) else "format"
    return "unknown"


def classify(row: dict, runs: int = 1) -> dict:
    """kind, label (the 판단), action and fix (whether to act now) of one failed sources.csv row that has failed `runs` runs in
    a row. Every kind but a temporary error needs action at once; unknown included, since nobody knows it will pass."""
    kind = _kind_of(row)
    label, action = KINDS[kind]
    fix = kind != "temporary" or runs >= REPEAT_RUNS
    return {"kind": kind, "label": label, "action": REPEATED if kind == "temporary" and fix else action, "fix": fix}
