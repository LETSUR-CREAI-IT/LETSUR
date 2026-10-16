"""Daily events step: previous state from the last same-branch run whose events step succeeded.

GitHub API responses are JSON fixtures served by a fake transport; artifacts are
real zip files holding a real market-collection.tar.xz, packed like the workflow
does. No network, token, or real artifact is used.
"""

from __future__ import annotations

import io
import json
import tarfile
import urllib.error
import zipfile
from pathlib import Path

import pytest

from scripts.market_collection import events_daily
from scripts.market_collection.events.engine import empty_state

API = "https://api.test/repos/org/repo"
ENV = {"GITHUB_API_URL": "https://api.test", "GITHUB_REPOSITORY": "org/repo", "GITHUB_RUN_ID": "500", "GITHUB_TOKEN": "t"}
REPO = {"full_name": "org/repo"}


def _run(run_id: int, created: str, branch: str = "develop", workflow: int = 7, repo: dict = REPO,
         conclusion: str | None = "success") -> dict:
    return {"id": run_id, "run_attempt": 1, "workflow_id": workflow, "head_branch": branch, "created_at": created,
            "conclusion": conclusion, "html_url": f"https://github.test/run/{run_id}", "repository": REPO,
            "head_repository": repo}


CURRENT = _run(500, "2026-10-15T00:17:00Z", conclusion=None)
LISTED = [
    CURRENT,
    _run(300, "2026-10-13T00:17:00Z"),
    _run(501, "2026-10-16T00:17:00Z"),
    _run(450, "2026-10-14T12:00:00Z", branch="feature"),
    _run(460, "2026-10-14T13:00:00Z", workflow=8),
    _run(470, "2026-10-14T14:00:00Z", repo={"full_name": "fork/repo"}),
    _run(400, "2026-10-14T00:17:00Z"),
]


def _artifact(run_id: int, expired: bool = False) -> dict:
    return {"id": run_id * 10, "name": f"market-collection-{run_id}-1", "expired": expired, "size_in_bytes": 1,
            "created_at": "2026-10-14T00:30:00Z", "workflow_run": {"id": run_id}}


def _tar_entry(tar: tarfile.TarFile, name: str, data: bytes | None = None, symlink: str | None = None) -> None:
    info = tarfile.TarInfo(name)
    if symlink is not None:
        info.type, info.linkname = tarfile.SYMTYPE, symlink
        tar.addfile(info)
    elif data is None:
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
    else:
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def _zip(entries: list[tuple], archive_name: str = "market-collection.tar.xz") -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:xz") as tar:
        for entry in entries:
            _tar_entry(tar, *entry)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(archive_name, payload.getvalue())
    return out.getvalue()


STATE = json.dumps({**empty_state(), "processed_runs": ["20261014T001700Z-live"]}).encode()
REPORT_OK = ("market_collection/events/report.json", b'{"status": "ok"}')
RAW = [("market_collection", None), ("market_collection/runs/20261014T001700Z-live/raw/a.bin", b"raw"),
       ("../escape.txt", b"x")]
WITH_STATE = _zip(RAW + [("market_collection/events", None), ("market_collection/events/state.json", STATE),
                         ("market_collection/events/events.json", b"{}"), REPORT_OK])


def _jobs(events_step: str | None = "success", has_step: bool = True) -> dict:
    steps = [{"name": "Collect market data", "conclusion": "success"},
             {"name": "Summarize collection", "conclusion": "failure"}]
    if has_step:
        steps.append({"name": "Build events", "conclusion": events_step})
    return {"jobs": [{"name": "collect", "steps": steps}]}


class Transport:
    def __init__(self, responses: dict) -> None:
        self.responses = {"/actions/runs/500": CURRENT, "/actions/workflows/7/runs": {"workflow_runs": LISTED},
                          "/actions/runs/400/artifacts": {"artifacts": [_artifact(400)]},
                          "/actions/artifacts/4000/zip": WITH_STATE, **responses}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url: str, token: str, dest: Path | None = None) -> bytes:
        self.calls.append((url, token))
        assert url.startswith(API)
        body = self.responses[url[len(API):].split("?")[0]]
        if isinstance(body, Exception):
            raise body
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        if dest is None:
            return data
        dest.write_bytes(data)
        return b""


class Build:
    def __init__(self, code: int = 0) -> None:
        self.code, self.args, self.state = code, None, None

    def __call__(self, args: list[str]) -> int:
        self.args = args
        if "--state" in args:
            self.state = Path(args[args.index("--state") + 1]).read_bytes()
        out = Path(args[args.index("--output") + 1])
        out.mkdir()
        if self.code:
            (out / "report.json").write_text('{"status": "rejected", "errors": ["x"]}')
        else:
            for name in ("state.json", "events.json", "report.json"):
                (out / name).write_text("{}")
        return self.code


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "data" / "market_collection"
    (root / "runs" / "20261015T001700Z-live").mkdir(parents=True)
    (root / "signals" / "runs" / "20261015T001900Z-live").mkdir(parents=True)
    return root


def _daily(root: Path, transport: Transport, build: Build) -> tuple[int, dict]:
    code = events_daily.main(["--root", str(root)], env=ENV, http=transport, build=build)
    return code, json.loads((root / "events" / "continuity.json").read_text())


def test_restores_latest_successful_same_branch_state_and_builds(root: Path) -> None:
    transport, build = Transport({}), Build()
    code, report = _daily(root, transport, build)
    assert code == 0 and report["status"] == "ok" and report["continuity"] == "continued"
    assert report["previous_run"]["id"] == 400 and report["previous_artifact"]["id"] == 4000
    assert report["previous_state"]["processed_runs"] == 1
    assert build.state == STATE
    assert build.args[:5] == ["build", "--run", str(root / "runs" / "20261015T001700Z-live"),
                              "--run", str(root / "signals" / "runs" / "20261015T001900Z-live")]
    assert build.args[-2:] == ["--output", str(root / "events")]
    assert all(token == "t" for _, token in transport.calls)
    assert not any("/runs/500/artifacts" in url or "/runs/501/" in url or "/runs/450/" in url for url, _ in transport.calls)
    # Only state.json was read; nothing else from the old tar landed on disk.
    assert not (root / "runs" / "20261014T001700Z-live").exists()
    assert not (root.parent / "escape.txt").exists() and not (root.parent.parent / "escape.txt").exists()
    assert sorted(p.name for p in (root / "events").iterdir()) == ["continuity.json", "events.json", "report.json", "state.json"]


def test_first_run_without_previous_success_is_a_recorded_baseline(root: Path) -> None:
    transport = Transport({"/actions/workflows/7/runs": {"workflow_runs": [CURRENT, LISTED[3], LISTED[5], LISTED[2]]}})
    build = Build()
    code, report = _daily(root, transport, build)
    assert code == 0 and report["continuity"] == "baseline" and report["reason"] == "no_previous_successful_run"
    assert "--state" not in build.args and report["previous_run"] is None


def test_artifact_from_before_events_is_a_recorded_baseline(root: Path) -> None:
    build = Build()
    code, report = _daily(root, Transport({"/actions/artifacts/4000/zip": _zip(RAW)}), build)
    assert code == 0 and report["reason"] == "previous_artifact_without_events" and report["previous_run"]["id"] == 400
    assert "--state" not in build.args


def test_expired_artifact_is_reported_as_broken_history(root: Path) -> None:
    transport, build = Transport({"/actions/runs/400/artifacts": {"artifacts": [_artifact(400, expired=True)]}}), Build()
    code, report = _daily(root, transport, build)
    assert code == 0 and report["continuity"] == "broken" and report["reason"] == "previous_artifact_expired"
    assert "--state" not in build.args and not any(url.endswith("/zip") for url, _ in transport.calls)


@pytest.mark.parametrize("response", [
    urllib.error.HTTPError(API + "/actions/artifacts/4000/zip", 403, "Forbidden", {}, None),
    urllib.error.URLError("network down"),
])
def test_download_failure_fails_instead_of_baseline(root: Path, response: Exception) -> None:
    build = Build()
    code, report = _daily(root, Transport({"/actions/artifacts/4000/zip": response}), build)
    assert code == 1 and report["status"] == "failed" and report["reason"] == "previous_state_unavailable"
    assert build.args is None and not (root / "events" / "state.json").exists()


@pytest.mark.parametrize("body, reason", [
    (b"not a zip", "previous_state_unavailable"),
    (WITH_STATE[: len(WITH_STATE) // 2], "previous_state_unavailable"),
    (_zip(RAW, archive_name="other.tar.xz"), "previous_artifact_layout"),
    (_zip([("market_collection/events/state.json", b"{broken")]), "previous_state_invalid"),
    (_zip([("market_collection/events/state.json", json.dumps({**empty_state(), "policy": "x"}).encode())]),
     "previous_state_invalid"),
    (_zip([("market_collection/events/state.json", None, "/etc/passwd")]), "previous_state_invalid"),
    (_zip([("market_collection/events/state.json", STATE), ("market_collection/events/state.json", STATE)]),
     "previous_state_invalid"),
    (_zip(RAW + [("market_collection/events/report.json", b"{}")]), "previous_state_missing"),
    (_zip([("market_collection/events/state.json", STATE)]), "previous_report_invalid"),
    (_zip([("market_collection/events/state.json", STATE),
           ("market_collection/events/report.json", b'{"status": "rejected"}')]), "previous_report_invalid"),
    (_zip([("market_collection/events/state.json", STATE), REPORT_OK, REPORT_OK]), "previous_state_invalid"),
])
def test_damaged_or_unsafe_archive_fails(root: Path, body: bytes, reason: str) -> None:
    build = Build()
    code, report = _daily(root, Transport({"/actions/artifacts/4000/zip": body}), build)
    assert code == 1 and report["reason"] == reason and build.args is None


FAILED = _run(480, "2026-10-14T20:00:00Z", conclusion="failure")
STATE_480 = json.dumps({**empty_state(), "processed_runs": ["20261014T001700Z-live", "20261014T200000Z-live"]}).encode()


def _with_failed(jobs: dict, zip_body: bytes | None = None, listed: list | None = None) -> Transport:
    responses = {"/actions/workflows/7/runs": {"workflow_runs": (LISTED if listed is None else listed) + [FAILED]},
                 "/actions/runs/480/attempts/1/jobs": jobs, "/actions/runs/480/artifacts": {"artifacts": [_artifact(480)]}}
    if zip_body is not None:
        responses["/actions/artifacts/4800/zip"] = zip_body
    return Transport(responses)


def test_failed_run_with_successful_events_step_is_continued(root: Path) -> None:
    body = _zip([("market_collection/events/state.json", STATE_480), REPORT_OK])
    transport, build = _with_failed(_jobs("success"), body), Build()
    code, report = _daily(root, transport, build)
    assert code == 0 and report["continuity"] == "continued" and report["unprocessed_runs"] == []
    assert report["previous_run"]["id"] == 480 and report["previous_run"]["conclusion"] == "failure"
    assert report["previous_run"]["events_step"] == "success" and build.state == STATE_480
    assert not any("/runs/400/" in url for url, _ in transport.calls)


@pytest.mark.parametrize("step, reason", [("failure", "events_step_failure"), (None, "events_step_not_run")])
def test_failed_events_step_is_skipped_as_an_explicit_gap(root: Path, step: str | None, reason: str) -> None:
    transport, build = _with_failed(_jobs(step)), Build()
    code, report = _daily(root, transport, build)
    assert code == 0 and report["continuity"] == "continued_with_gaps" and report["reason"] == "previous_state_restored"
    assert report["previous_run"]["id"] == 400 and build.state == STATE
    assert [(run["id"], run["reason"]) for run in report["unprocessed_runs"]] == [(480, reason)]
    assert not any("/runs/480/artifacts" in url for url, _ in transport.calls)


def test_only_failed_events_steps_is_broken_history_not_a_baseline(root: Path) -> None:
    transport, build = _with_failed(_jobs("failure"), listed=[CURRENT]), Build()
    code, report = _daily(root, transport, build)
    assert code == 0 and report["continuity"] == "broken" and report["reason"] == "no_previous_events_state"
    assert report["previous_run"] is None and [run["id"] for run in report["unprocessed_runs"]] == [480]
    assert "--state" not in build.args


def test_failed_run_without_events_step_is_broken_not_a_guessed_baseline(root: Path) -> None:
    # No step named Build events may mean a renamed step or a run cancelled early, not an old version.
    transport, build = _with_failed(_jobs(has_step=False), listed=[CURRENT]), Build()
    code, report = _daily(root, transport, build)
    assert code == 0 and report["continuity"] == "broken" and report["reason"] == "no_previous_events_state"
    assert report["previous_run"] is None
    assert [(run["id"], run["events_step"], run["reason"]) for run in report["unprocessed_runs"]] == [
        (480, "not_present", "events_step_not_present")]
    assert "--state" not in build.args and not any(url.endswith("/zip") for url, _ in transport.calls)


def test_failed_run_without_events_step_continues_older_state_with_gap(root: Path) -> None:
    transport, build = _with_failed(_jobs(has_step=False)), Build()
    code, report = _daily(root, transport, build)
    assert code == 0 and report["continuity"] == "continued_with_gaps" and report["reason"] == "previous_state_restored"
    assert report["previous_run"]["id"] == 400 and build.state == STATE
    assert [(run["id"], run["reason"]) for run in report["unprocessed_runs"]] == [(480, "events_step_not_present")]
    assert not any("/runs/480/artifacts" in url for url, _ in transport.calls)


@pytest.mark.parametrize("body, reason", [
    (_zip([("market_collection/events/state.json", STATE_480)]), "previous_report_invalid"),
    (_zip([("market_collection/events/state.json", STATE_480),
           ("market_collection/events/report.json", b'{"status": "rejected"}')]), "previous_report_invalid"),
    (_zip(RAW), "previous_state_missing"),
])
def test_failed_run_state_without_ok_report_is_rejected_not_bypassed(root: Path, body: bytes, reason: str) -> None:
    transport, build = _with_failed(_jobs("success"), body), Build()
    code, report = _daily(root, transport, build)
    assert code == 1 and report["status"] == "failed" and report["reason"] == reason and build.args is None
    assert not any("/runs/400/" in url for url, _ in transport.calls)


def test_jobs_permission_error_fails_instead_of_older_state(root: Path) -> None:
    denied = urllib.error.HTTPError(API + "/actions/runs/480/attempts/1/jobs", 403, "Forbidden", {}, None)
    transport, build = _with_failed(denied), Build()
    code, report = _daily(root, transport, build)
    assert code == 1 and report["reason"] == "previous_state_unavailable" and build.args is None
    assert not any("/runs/400/" in url for url, _ in transport.calls)


def test_corrupt_api_response_fails(root: Path) -> None:
    code, report = _daily(root, Transport({"/actions/workflows/7/runs": b"<html>"}), Build())
    assert code == 1 and report["reason"] == "previous_state_unavailable"


def test_build_rejection_keeps_engine_report_and_fails(root: Path) -> None:
    code, report = _daily(root, Transport({}), Build(code=2))
    assert code == 1 and report["build_exit_code"] == 2 and report["continuity"] == "continued"
    assert json.loads((root / "events" / "report.json").read_text())["status"] == "rejected"


def test_existing_output_is_not_overwritten(root: Path) -> None:
    (root / "events").mkdir()
    (root / "events" / "state.json").write_text("keep")
    assert events_daily.main(["--root", str(root)], env=ENV, http=Transport({}), build=Build()) == 2
    assert (root / "events" / "state.json").read_text() == "keep"


def test_real_events_cli_accepts_restored_state(tmp_path: Path) -> None:
    root = tmp_path / "market_collection"
    root.mkdir()
    code = events_daily.main(["--root", str(root)], env=ENV, http=Transport({}))
    report = json.loads((root / "events" / "continuity.json").read_text())
    assert code == 0, report
    assert report["build_exit_code"] == 0 and report["continuity"] == "continued"
    assert json.loads((root / "events" / "state.json").read_text())["processed_runs"] == ["20261014T001700Z-live"]
