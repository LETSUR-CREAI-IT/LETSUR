"""Recover only evidenced BytePlus SSR templates, preserving observations."""

import csv
import hashlib
import json
import socket
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.adapters import video


FIXTURES = Path(__file__).parent / "fixtures" / "byteplus"
# Retained Actions run 37735843718; campaign markdown from run 37579330828.
SHELL = (FIXTURES / "unrendered-template.html").read_bytes()
CAMPAIGN = (FIXTURES / "campaign.md").read_bytes()
SPECS = {s.source_key: s for s in video.source_specs()}
SPEC = SPECS["byteplus_seedance_promotion_rules"]


def rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def collect(tmp_path, monkeypatch, responses, spec=SPEC):
    calls, sleeps = [], []
    iterator = iter(responses)
    monkeypatch.setattr(core, "_utc_now", lambda: f"2026-10-08T06:00:{len(calls):02d}Z")
    # Patch the external wait, keeping collection and filesystem writes real.
    monkeypatch.setattr("time.sleep", sleeps.append)

    def fetcher(received):
        assert received == spec
        calls.append(received.url)
        response = next(iterator)
        if isinstance(response, Exception):
            raise response
        body, status = response
        return body, spec.url, status

    run = core.live(tmp_path, specs=[spec], adapters={"video": video}, fetcher=fetcher)
    return run, calls, sleeps


def manifest(run):
    return json.loads((run / "manifest.json").read_text())


def test_retained_shell_recovers_with_selected_response_time_and_all_raw(tmp_path, monkeypatch):
    run, calls, sleeps = collect(tmp_path, monkeypatch, [(SHELL, 200), (CAMPAIGN, 200)])
    source = rows(run / "sources.csv")[0]
    assert source["parse_status"] == "ok"
    assert source["fetched_at_utc"] == "2026-10-08T06:00:02Z"
    prices = rows(run / "price_observations.csv")
    assert len(prices) == 1 and prices[0]["amount"] == ""
    assert prices[0]["observed_at_utc"] == source["fetched_at_utc"]
    assert calls == [SPEC.url, SPEC.url] and sleeps == [1]
    attempts = manifest(run)["sources"][0]["fetch_attempts"]
    assert [a["fetched_at_utc"] for a in attempts] == ["2026-10-08T06:00:01Z", "2026-10-08T06:00:02Z"]
    assert len({a["raw_path"] for a in attempts}) == 2
    for attempt, body in zip(attempts, [SHELL, CAMPAIGN]):
        assert (run / attempt["raw_path"]).read_bytes() == body
        assert attempt["raw_sha256"] == hashlib.sha256(body).hexdigest()
        assert attempt["body_bytes"] == str(len(body))
        assert attempt["requested_url"] == attempt["effective_url"] == SPEC.url
        assert attempt["http_status"] == "200"
    assert (run / source["raw_path"]).read_bytes() == CAMPAIGN
    core._validated_run(run)
    replayed = core.replay(run, tmp_path, adapters={"video": video})
    assert rows(replayed / "price_observations.csv")[0]["observed_at_utc"] == source["fetched_at_utc"]
    assert calls == [SPEC.url, SPEC.url]


def test_repeated_shell_remains_explicit_error_without_prices(tmp_path, monkeypatch):
    run, calls, sleeps = collect(tmp_path, monkeypatch, [(SHELL, 200)] * 3)
    source = rows(run / "sources.csv")[0]
    assert len(calls) == 3 and sleeps == [1, 2]
    assert source["parse_status"] == "error" and "unrendered" in source["error"]
    assert source["fetched_at_utc"] == "2026-10-08T06:00:03Z"
    assert rows(run / "price_observations.csv") == []
    assert len(manifest(run)["sources"][0]["fetch_attempts"]) == 3
    assert (run / source["raw_path"]).read_bytes() == SHELL
    core._validated_run(run)


@pytest.mark.parametrize("body, expected", [(CAMPAIGN, "ok"), (CAMPAIGN.replace(b"60% off", b"offer"), "error"), (b"<html>BytePlus</html>", "error")])
def test_complete_or_unrecognized_document_never_retries(tmp_path, monkeypatch, body, expected):
    run, calls, sleeps = collect(tmp_path, monkeypatch, [(body, 200)])
    assert rows(run / "sources.csv")[0]["parse_status"] == expected
    assert len(calls) == 1 and sleeps == []
    assert "fetch_attempts" not in manifest(run)["sources"][0]
    core._validated_run(run)


@pytest.mark.parametrize("spec", [replace(SPEC, source_key="not_byteplus"), replace(SPEC, url="https://example.test/prices"), replace(SPEC, parser="byteplus_pricing", source_key="not_registered")])
def test_non_byteplus_template_does_not_retry(tmp_path, monkeypatch, spec):
    run, calls, sleeps = collect(tmp_path, monkeypatch, [(SHELL, 200)], spec)
    assert len(calls) == 1 and sleeps == []
    assert rows(run / "sources.csv")[0]["parse_status"] == "error"
    assert "fetch_attempts" not in manifest(run)["sources"][0]


@pytest.mark.parametrize("response", [socket.timeout("retry timed out"), (b"unavailable", 503), (SHELL, 503)])
def test_retry_failure_retains_earlier_response_and_never_falls_back(tmp_path, monkeypatch, response):
    run, calls, sleeps = collect(tmp_path, monkeypatch, [(SHELL, 200), response])
    source = rows(run / "sources.csv")[0]
    assert len(calls) == 2 and sleeps == [1]
    assert source["fetch_status"] in {"error", "http_error"}
    assert rows(run / "price_observations.csv") == []
    attempts = manifest(run)["sources"][0]["fetch_attempts"]
    assert len(attempts) == 2
    assert (run / attempts[0]["raw_path"]).read_bytes() == SHELL
    if isinstance(response, Exception):
        assert "retry timed out" in source["error"]
        assert source["raw_path"] == source["raw_sha256"] == source["body_bytes"] == ""
        assert "retry timed out" in attempts[1]["error"]
    else:
        assert source["http_status"] == "503"
        assert (run / attempts[1]["raw_path"]).read_bytes() == response[0]
    core._validated_run(run)
    replayed = core.replay(run, tmp_path, adapters={"video": video})
    assert rows(replayed / "price_observations.csv") == []


def test_non_byteplus_http_error_preserves_valid_partial_manifest(tmp_path, monkeypatch):
    spec = replace(SPEC, source_key="not_byteplus", url="https://example.test/prices")
    run, calls, sleeps = collect(tmp_path, monkeypatch, [(b"unavailable", 503)], spec)
    source = rows(run / "sources.csv")[0]
    assert source["fetch_status"] == "http_error" and source["parse_status"] == "not_run"
    assert source["http_status"] == "503"
    assert (run / source["raw_path"]).read_bytes() == b"unavailable"
    assert len(calls) == 1 and sleeps == []
    assert manifest(run)["sources"][0]["source"] == source
    core._validated_run(run)


def test_third_attempt_recovery_and_pricing_source_use_same_bounds(tmp_path, monkeypatch):
    spec = SPECS["byteplus_seedance_25_t2v"]
    complete = b'''|**Model ID** |**Online inference** (USD / M tokens) |
|dreamina-seedance-2-5-260628 |For 480p and 720p outputs: Input without video: 10.70 |
'''
    run, calls, sleeps = collect(tmp_path, monkeypatch, [(SHELL, 200), (SHELL, 200), (complete, 200)], spec)
    source = rows(run / "sources.csv")[0]
    assert source["parse_status"] == "ok"
    assert source["fetched_at_utc"] == "2026-10-08T06:00:03Z"
    assert rows(run / "price_observations.csv")[0]["amount"] == "10.70"
    assert len(calls) == 3 and sleeps == [1, 2]
    assert len(manifest(run)["sources"][0]["fetch_attempts"]) == 3
    core._validated_run(run)


@pytest.mark.parametrize("body", [SHELL.replace(b"<!--<?- SSRDataScript ?>-->", b""), SHELL.replace(b"<!--<?- html ?>-->", b""), b"x" * len(SHELL)])
def test_each_explicit_marker_required_not_size_or_missing_claims(tmp_path, monkeypatch, body):
    run, calls, sleeps = collect(tmp_path, monkeypatch, [(body, 200)])
    assert len(calls) == 1 and sleeps == []
    assert rows(run / "sources.csv")[0]["parse_status"] == "error"
    assert "fetch_attempts" not in manifest(run)["sources"][0]


@pytest.mark.parametrize("tamper", ["bytes", "hash", "path", "symlink"])
def test_retry_raw_tamper_and_escape_rejected(tmp_path, monkeypatch, tamper):
    run, _, _ = collect(tmp_path, monkeypatch, [(SHELL, 200), (CAMPAIGN, 200)])
    data = manifest(run)
    attempt = data["sources"][0]["fetch_attempts"][0]
    if tamper == "bytes":
        (run / attempt["raw_path"]).write_bytes(b"tampered")
    elif tamper == "hash":
        attempt["raw_sha256"] = "0" * 64
    elif tamper == "path":
        attempt["raw_path"] = "../../outside.bin"
    else:
        outside = tmp_path / "outside.bin"
        outside.write_bytes(SHELL)
        path = run / attempt["raw_path"]
        path.unlink()
        path.symlink_to(outside)
    (run / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(core.CollectionError, match="integrity|leaves run"):
        core.replay(run, tmp_path, adapters={"video": video})
