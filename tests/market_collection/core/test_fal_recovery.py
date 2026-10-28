"""Regressions from retained October 13-15 fal responses; see fixture provenance."""
import csv
import hashlib
import json
import socket
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.market_collection import core
from scripts.market_collection.adapters import image, video

FIXTURES = Path(__file__).parent / 'fixtures' / 'fal'
SPECS = {s.source_key: s for s in video.source_specs() + image.source_specs()}
GUIDES = [SPECS['image25_fal_guide_' + v + '_doc'] for v in ('sunburst', 'flare')]
SHELL = (FIXTURES / 'previous-failure-guide.html').read_bytes()
COMPLETE = (FIXTURES / 'latest-guide.html').read_bytes()


def rows(path):
    with path.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def collect(tmp_path, monkeypatch, responses, spec=GUIDES[0]):
    calls, sleeps = [], []
    iterator = iter(responses)
    monkeypatch.setattr(core, '_utc_now', lambda: f'2026-10-15T06:00:{len(calls):02d}Z')
    monkeypatch.setattr('time.sleep', sleeps.append)

    def fetcher(received):
        assert received == spec
        calls.append(received.url)
        response = next(iterator)
        if isinstance(response, Exception):
            raise response
        body, status = response
        return body, spec.url, status

    run = core.live(tmp_path, specs=[spec], adapters={'image': image}, fetcher=fetcher)
    data = json.loads((run / 'manifest.json').read_text())['sources'][0]
    core._validated_run(run)
    return run, data, calls, sleeps


@pytest.mark.parametrize('spec', GUIDES)
@pytest.mark.parametrize('shells', [1, 2])
def test_streamed_guide_recovers_with_all_attempts_and_replay_time(tmp_path, monkeypatch, spec, shells):
    responses = [(SHELL, 200)] * shells + [(COMPLETE, 200)]
    run, data, calls, sleeps = collect(tmp_path, monkeypatch, responses, spec)
    source = data['source']
    assert source['parse_status'] == 'ok'
    assert source['fetched_at_utc'] == f'2026-10-15T06:00:0{shells + 1}Z'
    assert calls == [spec.url] * (shells + 1) and sleeps == list(range(1, shells + 1))
    for attempt, (body, _) in zip(data['fetch_attempts'], responses):
        assert (run / attempt['raw_path']).read_bytes() == body
        assert attempt['raw_sha256'] == hashlib.sha256(body).hexdigest()
        assert attempt['requested_url'] == attempt['effective_url'] == spec.url
    assert [a['fetched_at_utc'] for a in data['fetch_attempts']] == [f'2026-10-15T06:00:0{i}Z' for i in range(1, shells + 2)]
    assert (run / source['raw_path']).read_bytes() == COMPLETE
    replayed = core.replay(run, tmp_path, adapters={'image': image})
    assert rows(replayed / 'sources.csv')[0]['fetched_at_utc'] == source['fetched_at_utc']
    assert rows(replayed / 'conditions.csv')
    assert len(calls) == shells + 1


def test_streamed_guide_exhausted_stays_failed(tmp_path, monkeypatch):
    run, data, calls, sleeps = collect(tmp_path, monkeypatch, [(SHELL, 200)] * 3)
    assert len(calls) == 3 and sleeps == [1, 2]
    assert data['source']['parse_status'] == 'error'
    assert rows(run / 'conditions.csv') == []
    assert len(data['fetch_attempts']) == 3
    assert (run / data['source']['raw_path']).read_bytes() == SHELL
    replayed = core.replay(run, tmp_path, adapters={'image': image})
    assert rows(replayed / 'conditions.csv') == []
    assert rows(replayed / 'sources.csv')[0]['fetched_at_utc'] == data['source']['fetched_at_utc']


def test_initial_http_error_never_retries(tmp_path, monkeypatch):
    _, data, calls, sleeps = collect(tmp_path, monkeypatch, [(SHELL, 503)])
    assert len(calls) == 1 and sleeps == []
    assert data['source']['fetch_status'] == 'http_error'
    assert 'fetch_attempts' not in data


@pytest.mark.parametrize('response', [(b'unavailable', 503), (SHELL, 503), socket.timeout('retry timed out')])
def test_failed_retry_never_falls_back_to_shell(tmp_path, monkeypatch, response):
    run, data, calls, sleeps = collect(tmp_path, monkeypatch, [(SHELL, 200), response])
    source = data['source']
    assert len(calls) == 2 and sleeps == [1]
    assert source['fetch_status'] in {'error', 'http_error'}
    assert len(data['fetch_attempts']) == 2
    assert rows(run / 'conditions.csv') == []
    if isinstance(response, Exception):
        assert source['raw_path'] == source['raw_sha256'] == source['body_bytes'] == ''
    else:
        assert (run / source['raw_path']).read_bytes() == response[0]
    replayed = core.replay(run, tmp_path, adapters={'image': image})
    assert rows(replayed / 'conditions.csv') == []


@pytest.mark.parametrize('body', [COMPLETE, COMPLETE.replace(b'Every GPT Image 2.5 request', b'Changed claim'),
    SHELL.replace(b'hidden ', b''), SHELL.replace(b'id="S:2"', b'id="other"'),
    SHELL.replace(b'<template id="P:3"></template>', b'<p>Changed document</p>'),
    b'<article><template id="P:3"></template></article>'])
def test_complete_changed_or_unrecognized_document_not_retried(tmp_path, monkeypatch, body):
    _, data, calls, sleeps = collect(tmp_path, monkeypatch, [(body, 200)])
    assert len(calls) == 1 and sleeps == []
    assert 'fetch_attempts' not in data


@pytest.mark.parametrize('change', [dict(source_key='other'), dict(parser='image25_vercel_ui'),
    dict(route='other'), dict(url=GUIDES[0].url + '?other=1'), dict(url='https://example.test/learn')])
def test_only_exact_guide_source_retries(tmp_path, monkeypatch, change):
    _, data, calls, sleeps = collect(tmp_path, monkeypatch, [(SHELL, 200)], replace(GUIDES[0], **change))
    assert len(calls) == 1 and sleeps == []
    assert 'fetch_attempts' not in data
