"""Daily workflow step: previous state.json + today's runs -> events build.

    python -m scripts.market_collection.events_daily --root data/market_collection

The previous state comes from the newest completed run of the same workflow, on
the same branch, in the same repository (never the current run or a fork) that
either succeeded or whose Build events step succeeded (read from the run's jobs).
Newer runs whose events step failed, did not run, or is not present in the
jobs (renamed or cancelled early; never assumed to predate events) are listed in
continuity.json as unprocessed_runs; the result is then continued_with_gaps,
or broken when no state is left. Only the exact members
market_collection/events/state.json and report.json (must be status ok) are read
from that run's artifact zip -> market-collection.tar.xz; nothing else is
extracted. The event engine itself runs as
`python -m scripts.market_collection.events build`.

Writes <root>/events/ (build output) plus <root>/events/continuity.json. A
download, permission, or archive problem fails the step; it never silently
falls back to an older run or a baseline. Baselines are only taken, with the
reason recorded, when nothing was skipped and either there is no previous run or
the previous successful run's artifact has no events/; an expired artifact is
broken history.

Environment: GITHUB_API_URL, GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_TOKEN
(github.token with actions: read).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from scripts.market_collection.events.engine import empty_state

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_NAME = "market-collection.tar.xz"
EVENTS_PREFIX = "market_collection/events/"
STATE_MEMBER = EVENTS_PREFIX + "state.json"
ENGINE_REPORT_MEMBER = EVENTS_PREFIX + "report.json"
EVENTS_STEP = "Build events"
MAX_STATE_BYTES = 256 * 1024 * 1024
REPORT_NAME = "continuity.json"


class ContinuityError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _http(url: str, token: str, dest: Path | None = None) -> bytes:
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "letsur-market-collection"}
    try:
        response = urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location", "")
        if exc.code not in (301, 302, 303, 307, 308) or not location.startswith("https://"):
            raise
        # Artifact zips redirect to a signed storage URL, which must not receive the token.
        response = urllib.request.urlopen(urllib.request.Request(location, headers={"User-Agent": headers["User-Agent"]}),
                                          timeout=300)
    with response:
        if dest is None:
            return response.read()
        with dest.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        return b""


def _build(args: list[str]) -> int:
    return subprocess.run([sys.executable, "-B", "-m", "scripts.market_collection.events", *args], cwd=REPO_ROOT).returncode


def _read_state(zip_path: Path) -> tuple[bytes | None, bytes | None, bool]:
    """Return (state bytes or None, engine report bytes or None, whether the archive has an events/ directory)."""
    with zipfile.ZipFile(zip_path) as archive:
        if archive.namelist().count(ARCHIVE_NAME) != 1:
            raise ContinuityError("previous_artifact_layout", f"zip must hold exactly one {ARCHIVE_NAME}")
        with archive.open(ARCHIVE_NAME) as stream, tarfile.open(fileobj=stream, mode="r|xz") as tar:
            found: dict[str, bytes] = {}
            has_events = False
            for member in tar:
                has_events = has_events or member.name.startswith(EVENTS_PREFIX) or member.name == EVENTS_PREFIX.rstrip("/")
                if member.name not in (STATE_MEMBER, ENGINE_REPORT_MEMBER):
                    continue
                if member.name in found:
                    raise ContinuityError("previous_state_invalid", f"duplicate {member.name}")
                if not member.isfile() or member.size > MAX_STATE_BYTES:
                    raise ContinuityError("previous_state_invalid", f"{member.name} is not a regular file within size limit")
                found[member.name] = tar.extractfile(member).read()
    return found.get(STATE_MEMBER), found.get(ENGINE_REPORT_MEMBER), has_events


def _check_report(data: bytes | None) -> None:
    """The previous engine build must have finished with status ok; otherwise its state is not trusted."""
    try:
        report = json.loads(data.decode("utf-8")) if data is not None else None
    except (UnicodeError, ValueError) as exc:
        raise ContinuityError("previous_report_invalid", f"{ENGINE_REPORT_MEMBER} not JSON: {exc}") from exc
    if not isinstance(report, dict) or report.get("status") != "ok":
        raise ContinuityError("previous_report_invalid", f"{ENGINE_REPORT_MEMBER} missing or status is not ok")


def _events_step(jobs: dict) -> str:
    """Conclusion of the Build events step in a run's jobs, or 'not_present' when no step has that name.

    A missing step does not prove the run predates events (the step may be renamed or the
    run cancelled before it started), so callers treat it as unknown, never as a baseline.
    """
    steps = [step for job in jobs["jobs"] for step in job.get("steps") or () if step.get("name") == EVENTS_STEP]
    if not steps:
        return "not_present"
    if len(steps) != 1:
        raise ContinuityError("previous_run_layout", f"{len(steps)} steps named {EVENTS_STEP}")
    return steps[0].get("conclusion") or "not_run"


def _check_state(data: bytes) -> dict:
    try:
        state = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ContinuityError("previous_state_invalid", f"not JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise ContinuityError("previous_state_invalid", "not a JSON object")
    for key, default in empty_state().items():
        if key not in state or not isinstance(state[key], type(default)):
            raise ContinuityError("previous_state_invalid", f"missing or wrong type: {key}")
        if not isinstance(default, (list, dict)) and state[key] != default:
            raise ContinuityError("previous_state_invalid", f"{key} {state[key]!r} != {default!r}")
    return state


def restore(env: dict, http, work: Path) -> tuple[dict, Path | None]:
    """Find and read the previous state. Returns (report fields, state path or None for a baseline)."""
    missing = [name for name in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_TOKEN") if not env.get(name)]
    if missing:
        raise ContinuityError("missing_environment", ", ".join(missing))
    api = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    repo, token = env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"]

    def get(path: str) -> dict:
        data = json.loads(http(f"{api}/repos/{repo}{path}", token))
        if not isinstance(data, dict):
            raise ValueError(f"{path}: response is not a JSON object")
        return data

    current = get(f"/actions/runs/{env['GITHUB_RUN_ID']}")
    if current["repository"]["full_name"] != repo:
        raise ContinuityError("current_run_mismatch", f"run {current['id']} is not in {repo}")
    info = {"current_run": {key: current[key] for key in ("id", "run_attempt", "workflow_id", "head_branch", "created_at")},
            "previous_run": None, "previous_artifact": None}
    query = urllib.parse.urlencode({"branch": current["head_branch"], "status": "completed",
                                    "exclude_pull_requests": "true", "per_page": 50})
    listed = get(f"/actions/workflows/{current['workflow_id']}/runs?{query}")["workflow_runs"]
    candidates = [run for run in listed
                  if run["id"] != current["id"] and run["workflow_id"] == current["workflow_id"]
                  and run["head_branch"] == current["head_branch"] and run["conclusion"]
                  and (run.get("repository") or {}).get("full_name") == repo
                  and (run.get("head_repository") or {}).get("full_name") == repo
                  and run["created_at"] < current["created_at"]]
    # A failed run still counts when its Build events step succeeded (e.g. one source failed
    # in Summarize collection); runs whose events step failed, did not run, or cannot be found
    # are skipped and listed.
    info["unprocessed_runs"] = skipped = []
    previous, step = None, None
    for run in sorted(candidates, key=lambda run: (run["created_at"], run["id"]), reverse=True):
        if run["conclusion"] == "success":
            previous, step = run, "success"
            break
        step = _events_step(get(f"/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100"))
        if step == "success":
            previous = run
            break
        skipped.append({**{key: run[key] for key in ("id", "run_attempt", "created_at", "conclusion")},
                        "events_step": step, "reason": "events_step_" + step})

    def no_state(reason: str) -> tuple[dict, None]:
        # Without a state, skipped event-producing runs mean missing history, not a fresh start.
        return {**info, "continuity": "broken" if skipped else "baseline", "reason": reason}, None

    if previous is None:
        return no_state("no_previous_events_state" if skipped else "no_previous_successful_run")
    info["previous_run"] = {**{key: previous[key] for key in ("id", "run_attempt", "created_at", "conclusion", "html_url")},
                            "events_step": step}

    name = f"market-collection-{previous['id']}-{previous['run_attempt']}"
    artifacts = [item for item in get(f"/actions/runs/{previous['id']}/artifacts?{urllib.parse.urlencode({'name': name})}")["artifacts"]
                 if item["name"] == name and (item.get("workflow_run") or {}).get("id", previous["id"]) == previous["id"]]
    if len(artifacts) != 1:
        raise ContinuityError("previous_artifact_missing", f"run {previous['id']} has {len(artifacts)} artifacts named {name}")
    artifact = artifacts[0]
    info["previous_artifact"] = {key: artifact.get(key) for key in ("id", "name", "expired", "size_in_bytes", "created_at")}
    if artifact["expired"]:
        return {**info, "continuity": "broken", "reason": "previous_artifact_expired"}, None

    zip_path = work / "artifact.zip"
    http(f"{api}/repos/{repo}/actions/artifacts/{artifact['id']}/zip", token, zip_path)
    data, engine_report, has_events = _read_state(zip_path)
    zip_path.unlink()
    if data is None:
        if has_events or previous["conclusion"] != "success":
            raise ContinuityError("previous_state_missing", f"{STATE_MEMBER} absent but events/ present or events step succeeded")
        return no_state("previous_artifact_without_events")
    state = _check_state(data)
    _check_report(engine_report)
    state_path = work / "previous_state.json"
    state_path.write_bytes(data)
    return {**info, "continuity": "continued_with_gaps" if skipped else "continued", "reason": "previous_state_restored",
            "previous_state": {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                               "processed_runs": len(state["processed_runs"])}}, state_path


def main(argv: list[str] | None = None, env: dict | None = None, http=_http, build=_build) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.market_collection.events_daily")
    parser.add_argument("--root", default="data/market_collection")
    args = parser.parse_args(argv)
    root = Path(args.root)
    out = root / "events"
    if out.exists():
        print(f"output exists: {out}", file=sys.stderr)
        return 2
    report: dict = {"status": "failed", "continuity": "failed", "reason": None, "error": None, "build_exit_code": None}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            fields, state_path = restore(os.environ if env is None else env, http, Path(tmp))
            report.update(fields)
        except ContinuityError as exc:
            report.update(reason=exc.reason, error=str(exc))
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, tarfile.TarError, lzma.LZMAError, EOFError) as exc:
            report.update(reason="previous_state_unavailable", error=f"{type(exc).__name__}: {exc}")
        if report["error"] is None:
            runs = [path for base in (root / "runs", root / "signals" / "runs") if base.is_dir()
                    for path in sorted(base.iterdir()) if path.is_dir()]
            report["runs"] = [str(path.relative_to(root)) for path in runs]
            command = ["build", *[arg for path in runs for arg in ("--run", str(path))]]
            if state_path is not None:
                command += ["--state", str(state_path)]
            report["build_exit_code"] = build(command + ["--output", str(out)])
            if report["build_exit_code"] == 0:
                report["status"] = "ok"
            else:
                report["error"] = "events build failed; see report.json"
    out.mkdir(parents=True, exist_ok=True)
    (out / REPORT_NAME).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
