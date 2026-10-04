import csv
import io
import json
import socket
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.market_collection import core


SPEC = core.SourceSpec("sample", "llm", "model-one", "direct", "chat", "https://example.test/prices", "sample")
OTHER = core.SourceSpec("other", "llm", "model-one", "gateway", "chat", "https://example.test/other", "sample")


def price(condition_set_id="condition-one"):
    return dict(provider_model_id="model-one", model_version="", upstream_provider="Provider", task="chat", billing_item="input", price_kind="published_rate", amount="1.25", currency="USD", unit="1M tokens", normalized_amount="", normalized_unit="", condition_set_id=condition_set_id, effective_from="", effective_until="", locator="table:input", evidence_status="direct", unknown_reason="")


def condition():
    return dict(condition_set_id="condition-one", condition_key="context", operator="=", value="standard", unit="", status="stated", locator="table:input")


class Adapter:
    @staticmethod
    def parse_source(spec, body, source_id):
        assert spec in (SPEC, OTHER)
        assert source_id
        if body == b"bad parser":
            raise ValueError("changed page")
        return [price()], [condition()]


def rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_live_partial_repeated_replay_history_and_tamper(tmp_path):
    calls = []

    def fetcher(spec):
        calls.append(spec.source_key)
        if spec == OTHER:
            raise socket.timeout("timed out")
        return b"public evidence", spec.url, 200

    kwargs = dict(specs=[SPEC, OTHER], adapters={"llm": Adapter}, fetcher=fetcher)
    first = core.live(tmp_path, **kwargs)
    second = core.live(tmp_path, **kwargs)
    assert first != second and first.exists() and second.exists()
    assert calls == ["sample", "other", "sample", "other"]
    sources = rows(first / "sources.csv")
    assert [row["fetch_status"] for row in sources] == ["ok", "error"]
    assert "timed out" in sources[1]["error"]
    assert len(rows(first / "price_observations.csv")) == 1
    assert (first / sources[0]["raw_path"]).read_bytes() == b"public evidence"
    replayed = core.replay(first, tmp_path, adapters={"llm": Adapter})
    assert json.loads((replayed / "manifest.json").read_text())["mode"] == "replay"
    assert rows(replayed / "price_observations.csv")[0]["observed_at_utc"] == rows(first / "price_observations.csv")[0]["observed_at_utc"]
    exported = core.history(tmp_path, tmp_path / "history")
    assert len(rows(exported / "price_observations.csv")) == 2
    assert len(rows(exported / "sources.csv")) == 4
    (first / sources[0]["raw_path"]).write_bytes(b"tampered")
    with pytest.raises(core.CollectionError, match="integrity"):
        core.replay(first, tmp_path, adapters={"llm": Adapter})
    with pytest.raises(core.CollectionError, match="integrity"):
        core.history(tmp_path, tmp_path / "history-two")


def test_disable_and_registry_validation(tmp_path):
    with pytest.raises(core.CollectionError, match="duplicate"):
        core.validate_registry([SPEC, SPEC])
    with pytest.raises(core.CollectionError, match="HTTPS"):
        core.validate_registry([core.SourceSpec("x", "llm", "m", "r", "chat", "http://example.test", "p")])
    with pytest.raises(core.CollectionError, match="unknown disabled"):
        core.live(tmp_path, ("missing",), specs=[SPEC], adapters={"llm": Adapter})
    run = core.live(tmp_path, ("sample",), specs=[SPEC], adapters={"llm": Adapter}, fetcher=lambda _: pytest.fail("disabled source fetched"))
    assert rows(run / "sources.csv") == []
    assert json.loads((run / "manifest.json").read_text())["disabled_sources"] == ["sample"]


@pytest.mark.parametrize("alter, message", [
    (lambda p, c: (p.update(unit=""), c), "currency or unit"),
    (lambda p, c: (p.update(condition_set_id="missing"), c), "missing condition"),
    (lambda p, c: (p.update(price_kind="guess"), c), "classification"),
    (lambda p, c: (p.update(amount="nan"), c), "invalid amount"),
    (lambda p, c: (p.update(unit="bananas"), c), "unsupported price unit"),
    (lambda p, c: (p.update(currency="BAN"), c), "unsupported currency"),
    (lambda p, c: (p.update(normalized_amount="1.25", normalized_unit="1 second"), c), "physical quantity"),
    (lambda p, c: (p.update(normalized_amount="2", normalized_unit="1M tokens"), c), "unit conversion"),
])
def test_parser_invalid_rows_are_explicit(tmp_path, alter, message):
    p, c = price(), condition()
    alter(p, c)
    adapter = SimpleNamespace(parse_source=lambda *_: ([p], [c]))
    run = core.live(tmp_path, specs=[SPEC], adapters={"llm": adapter}, fetcher=lambda spec: (b"public", spec.url, 200))
    source = rows(run / "sources.csv")[0]
    assert source["parse_status"] == "error" and message in source["error"]
    assert rows(run / "price_observations.csv") == []
    assert (run / source["raw_path"]).read_bytes() == b"public"


def test_token_unit_conversion_and_adapter_exception(tmp_path):
    converted = price()
    converted.update(normalized_amount="0.00000125", normalized_unit="1 token")
    adapter = SimpleNamespace(parse_source=lambda *_: ([converted], [condition()]))
    run = core.live(tmp_path, specs=[SPEC], adapters={"llm": adapter}, fetcher=lambda spec: (b"public", spec.url, 200))
    assert rows(run / "sources.csv")[0]["parse_status"] == "ok"
    assert rows(run / "price_observations.csv")[0]["normalized_amount"] == "0.00000125"

    def broken_adapter(*_):
        raise RuntimeError("source layout changed")

    run = core.live(tmp_path, specs=[SPEC], adapters={"llm": SimpleNamespace(parse_source=broken_adapter)}, fetcher=lambda spec: (b"changed", spec.url, 200))
    source = rows(run / "sources.csv")[0]
    assert source["fetch_status"] == "ok"
    assert source["parse_status"] == "error"
    assert "source layout changed" in source["error"]
    assert rows(run / "price_observations.csv") == []
    assert (run / source["raw_path"]).read_bytes() == b"changed"


class Response:
    def __init__(self, body=b"", status=200, location=None):
        self.stream = io.BytesIO(body)
        self.status = status
        self.headers = {"Location": location} if location else {}

    def read(self, size):
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def test_fetch_rejects_oversize_and_timeout():
    opener = SimpleNamespace(open=lambda *args, **kwargs: Response(b"x" * (core.MAX_BODY_BYTES + 1)))
    with patch.object(core.urllib.request, "build_opener", return_value=opener):
        with pytest.raises(core.CollectionError, match="3 MB"):
            core.fetch(SPEC)
    opener = SimpleNamespace(open=lambda *args, **kwargs: (_ for _ in ()).throw(socket.timeout("timed out")))
    with patch.object(core.urllib.request, "build_opener", return_value=opener):
        with pytest.raises(socket.timeout):
            core.fetch(SPEC)


def test_fetch_rejects_third_redirect_and_cross_host():
    def redirect(url):
        return urllib.error.HTTPError(url, 302, "redirect", {"Location": url + "/next"}, None)

    opener = SimpleNamespace(open=lambda request, **kwargs: redirect(request.full_url))
    with patch.object(core.urllib.request, "build_opener", return_value=opener):
        with pytest.raises(core.CollectionError, match="redirect limit"):
            core.fetch(SPEC)
    opener = SimpleNamespace(open=lambda request, **kwargs: urllib.error.HTTPError(request.full_url, 302, "redirect", {"Location": "https://elsewhere.test"}, None))
    with patch.object(core.urllib.request, "build_opener", return_value=opener):
        with pytest.raises(core.CollectionError, match="changed host"):
            core.fetch(SPEC)
