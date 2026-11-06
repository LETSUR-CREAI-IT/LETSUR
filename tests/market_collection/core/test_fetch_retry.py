"""2026-11-06 박건 ('요청이 많아 거절되는 http429에 대해서는 수집 단계에서 바로 다시 요청해서 받게 해야하는거아니야?'): one request
per address in a run, and a transient failure (HTTP 408, 429, 5xx, a network error) asked again after 5 s and 15 s within the
three attempts, under a run-wide time budget. Fake fetchers only: no test reaches the network or really waits."""
import csv
import json
import socket

import pytest

from scripts.market_collection import core
from scripts.market_collection.core.failures import classify

AZURE = "https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20'Foundry%20Models'"
FIRST = core.SourceSpec("azure_one", "llm", "model-one", "azure_foundry", "chat", AZURE, "sample")
SECOND = core.SourceSpec("azure_two", "llm", "model-two", "azure_foundry", "chat", AZURE, "sample")
ELSEWHERE = core.SourceSpec("elsewhere", "llm", "model-one", "direct", "chat", "https://example.test/prices", "sample")


class Adapter:
    @staticmethod
    def parse_source(spec, body, source_id):
        price = dict(provider_model_id=spec.model_id, model_version="", upstream_provider="Provider", task="chat", billing_item="input",
                     price_kind="published_rate", amount="1.25", currency="USD", unit="1M tokens", normalized_amount="", normalized_unit="",
                     condition_set_id="", effective_from="", effective_until="", locator="table:input", evidence_status="direct", unknown_reason="")
        return [price], []


def rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def collect(tmp_path, monkeypatch, responses, specs=(FIRST,)):
    """Run live() over specs; responses are (body, status) pairs or exceptions, served in order to whichever source asks."""
    calls, sleeps, moments = [], [], iter(f"2026-11-06T00:00:{n:02d}Z" for n in range(60))
    iterator = iter(responses)
    monkeypatch.setattr(core, "_utc_now", lambda: next(moments))
    monkeypatch.setattr("time.sleep", sleeps.append)

    def fetcher(spec):
        calls.append(spec.source_key)
        response = next(iterator)
        if isinstance(response, Exception):
            raise response
        body, status = response
        return body, spec.url, status

    run = core.live(tmp_path, specs=list(specs), adapters={"llm": Adapter}, fetcher=fetcher)
    core._validated_run(run)
    return run, json.loads((run / "manifest.json").read_text())["sources"], calls, sleeps


def test_one_request_per_address_and_every_source_keeps_its_own_row_and_raw(tmp_path, monkeypatch):
    run, entries, calls, sleeps = collect(tmp_path, monkeypatch, [(b"azure prices", 200), (b"elsewhere", 200)], (FIRST, SECOND, ELSEWHERE))
    assert calls == ["azure_one", "elsewhere"] and sleeps == []
    sources = rows(run / "sources.csv")
    assert [s["source_key"] for s in sources] == ["azure_one", "azure_two", "elsewhere"]
    assert all(s["fetch_status"] == "ok" and s["parse_status"] == "ok" for s in sources)
    assert sources[0]["raw_path"] != sources[1]["raw_path"]
    assert (run / sources[0]["raw_path"]).read_bytes() == (run / sources[1]["raw_path"]).read_bytes() == b"azure prices"
    # the shared response keeps the moment it was fetched, so both prices carry the same observation time
    assert sources[1]["fetched_at_utc"] == sources[0]["fetched_at_utc"] != sources[2]["fetched_at_utc"]
    assert len({p["observed_at_utc"] for p in rows(run / "price_observations.csv")[:2]}) == 1
    assert all("fetch_attempts" not in entry for entry in entries)


def test_only_an_accepted_response_is_shared(tmp_path, monkeypatch):
    run, _, calls, sleeps = collect(tmp_path, monkeypatch, [(b"missing", 404), (b"azure prices", 200)], (FIRST, SECOND))
    assert calls == ["azure_one", "azure_two"] and sleeps == []
    assert [s["fetch_status"] for s in rows(run / "sources.csv")] == ["http_error", "ok"]


@pytest.mark.parametrize("failure", [(b"rate limited", 429), (b"busy", 503), (b"slow", 408), socket.timeout("timed out"), ConnectionResetError("reset")])
def test_a_transient_failure_is_asked_again_after_a_wait(tmp_path, monkeypatch, failure):
    run, entries, calls, sleeps = collect(tmp_path, monkeypatch, [failure, (b"azure prices", 200)])
    source = rows(run / "sources.csv")[0]
    assert calls == ["azure_one", "azure_one"] and sleeps == [5]
    assert source["fetch_status"] == "ok" and source["parse_status"] == "ok" and source["error"] == ""
    assert (run / source["raw_path"]).read_bytes() == b"azure prices"
    assert len(entries[0]["fetch_attempts"]) == 2 and len(rows(run / "price_observations.csv")) == 1


def test_a_transient_failure_on_every_attempt_stays_a_temporary_failure(tmp_path, monkeypatch):
    run, entries, calls, sleeps = collect(tmp_path, monkeypatch, [(b"rate limited", 429)] * 3)
    source = rows(run / "sources.csv")[0]
    assert len(calls) == 3 and sleeps == [5, 15]
    assert (source["fetch_status"], source["parse_status"], source["http_status"]) == ("http_error", "not_run", "429")
    assert [a["http_status"] for a in entries[0]["fetch_attempts"]] == ["429"] * 3
    assert classify(source)["label"] == "일시 오류" and rows(run / "price_observations.csv") == []


@pytest.mark.parametrize("failure", [(b"gone", 404), (b"denied", 403), core.CollectionError("redirect changed host")])
def test_a_lasting_failure_is_not_asked_again(tmp_path, monkeypatch, failure):
    run, entries, calls, sleeps = collect(tmp_path, monkeypatch, [failure])
    assert len(calls) == 1 and sleeps == [] and "fetch_attempts" not in entries[0]
    assert rows(run / "sources.csv")[0]["fetch_status"] in {"http_error", "error"}


def test_no_retry_once_the_run_budget_is_spent(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "TRANSIENT_BUDGET_SECONDS", 0)
    run, entries, calls, sleeps = collect(tmp_path, monkeypatch, [(b"rate limited", 429)])
    assert len(calls) == 1 and sleeps == [] and "fetch_attempts" not in entries[0]
    assert rows(run / "sources.csv")[0]["http_status"] == "429"
