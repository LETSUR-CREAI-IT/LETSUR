import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import urllib.error
import zipfile
from datetime import datetime, timezone

from github_source import fetch_latest, GitHubClient, SourceError, unpack

NOW = datetime(2026, 10, 27, 1, tzinfo=timezone.utc)
TEST_TEMP = Path(__file__).resolve().parent / 'output'
TEST_TEMP.mkdir(exist_ok=True)


def temporary_directory():
    # The Windows managed environment permits nested directories in this workspace.
    return tempfile.TemporaryDirectory(dir=TEST_TEMP)


def run(identity=42, **kwargs):
    value = {'id': identity, 'run_attempt': 1, 'created_at': '2026-10-27T00:17:00Z',
             'head_branch': 'develop', 'head_sha': 'a' * 40,
             'status': 'completed', 'conclusion': 'success',
             'path': '.github/workflows/market-collection.yml'}
    value.update(kwargs)
    return value


def archive(identity=42, extra=None, continuity=None, events=None):
    data = {'events.json': {'events': events if events is not None else [{'event_id': 'e1'}]},
            'report.json': {}, 'continuity.json': continuity or {
                'status': 'ok', 'build_exit_code': 0,
                'current_run': {'id': identity, 'run_attempt': 1, 'head_branch': 'develop'}}}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:xz') as tar:
        for name, content in data.items():
            raw = json.dumps(content).encode()
            member = tarfile.TarInfo('market_collection/events/' + name)
            member.size = len(raw)
            tar.addfile(member, io.BytesIO(raw))
        if extra:
            member = tarfile.TarInfo(extra)
            member.size = 1
            tar.addfile(member, io.BytesIO(b'x'))
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as zip:
        zip.writestr('market-collection.tar.xz', stream.getvalue())
    return result.getvalue()


class FakeClient:
    def __init__(self, runs=None, successful=None, blob=None):
        self.runs = runs or [run()]
        self.successful = successful if successful is not None else self.runs
        self.blob = blob or archive()
        self.downloads = 0

    def json(self, path):
        if '/artifacts?' in path:
            identity = int(path.split('/runs/')[1].split('/')[0])
            return {'artifacts': [{'id': 100 + identity,
                    'name': 'market-collection-' + str(identity) + '-1', 'expired': False,
                    'digest': 'sha256:' + hashlib.sha256(self.blob).hexdigest()}]}
        return {'workflow_runs': self.successful if 'status=success' in path else self.runs}

    def archive(self, identity):
        self.downloads += 1
        return self.blob


class GitHubSourceTest(unittest.TestCase):
    def test_caches_validated_version_and_repeat_does_not_download(self):
        with temporary_directory() as folder:
            client = FakeClient()
            one = fetch_latest(folder, client=client, now=NOW)
            two = fetch_latest(folder, client=client, now=NOW)
            self.assertTrue(one['fresh'])
            self.assertEqual(one['events_path'], two['events_path'])
            self.assertEqual(client.downloads, 1)
            self.assertEqual(json.loads(Path(one['events_path']).read_text())['events'][0]['event_id'], 'e1')

    def test_orders_results_and_excludes_wrong_branch_and_workflow(self):
        values = [run(40), run(99, head_branch='main'), run(42),
                  run(100, path='.github/workflows/other.yml')]
        with temporary_directory() as folder:
            result = fetch_latest(folder, client=FakeClient(values), now=NOW)
            self.assertEqual(result['metadata']['run_id'], 42)

    def test_newer_pending_run_marks_cached_success_stale(self):
        values = [run(), run(43, status='in_progress', conclusion=None)]
        with temporary_directory() as folder:
            result = fetch_latest(folder, client=FakeClient(values, [run()]), now=NOW)
            self.assertFalse(result['fresh'])
            self.assertIn('newer_run_pending', result['stale_reasons'])

    def test_old_success_is_not_presented_as_fresh(self):
        with temporary_directory() as folder:
            result = fetch_latest(folder, client=FakeClient([run(created_at='2026-10-24T00:17:00Z')]), now=NOW)
            self.assertEqual(result['stale_reasons'], ['last_success_too_old'])

    def test_regression_preserves_latest_pointer(self):
        with temporary_directory() as folder:
            fetch_latest(folder, client=FakeClient(), now=NOW)
            old = Path(folder, 'latest.json').read_bytes()
            with self.assertRaisesRegex(SourceError, '^regression$'):
                fetch_latest(folder, client=FakeClient([run(41)], blob=archive(41)), now=NOW)
            self.assertEqual(Path(folder, 'latest.json').read_bytes(), old)

    def test_malformed_archive_preserves_cache(self):
        with temporary_directory() as folder:
            fetch_latest(folder, client=FakeClient(), now=NOW)
            old = Path(folder, 'latest.json').read_bytes()
            with self.assertRaisesRegex(SourceError, '^invalid_artifact$'):
                fetch_latest(folder, client=FakeClient([run(43)], blob=b'bad zip'), now=NOW)
            self.assertEqual(Path(folder, 'latest.json').read_bytes(), old)

    def test_rejects_wrong_continuity_duplicate_events_and_traversal(self):
        for blob in (archive(41), archive(events=[{'event_id': 'x'}, {'event_id': 'x'}]),
                     archive(extra='../escape.txt')):
            with self.subTest(blob_length=len(blob)), self.assertRaisesRegex(SourceError, '^invalid_artifact$'):
                unpack(blob, run())

    def test_rejects_corrupted_cache(self):
        with temporary_directory() as folder:
            result = fetch_latest(folder, client=FakeClient(), now=NOW)
            Path(result['events_path']).write_text('{}')
            with self.assertRaisesRegex(SourceError, '^invalid_cache$'):
                fetch_latest(folder, client=FakeClient(), now=NOW)

    def test_redirect_does_not_forward_authorization(self):
        requests = []
        class Stream(io.BytesIO):
            pass
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                if len(requests) == 1:
                    raise urllib.error.HTTPError(request.full_url, 302, '',
                        {'Location': 'https://artifact-storage.example/archive?sig=private'}, None)
                return Stream(b'archive')
        result = GitHubClient('secret-token', Opener()).archive(1)
        self.assertEqual(result, b'archive')
        self.assertEqual(requests[0].get_header('Authorization'), 'Bearer secret-token')
        self.assertIsNone(requests[1].get_header('Authorization'))

    def test_safe_errors_classify_auth_and_network(self):
        class Opener:
            def __init__(self, error): self.error = error
            def open(self, request, timeout): raise self.error
        for error, code in ((urllib.error.HTTPError('https://api.github.com/secret', 401, 'token', {}, None), 'authentication'),
                            (urllib.error.URLError('sensitive URL'), 'network')):
            with self.assertRaisesRegex(SourceError, '^' + code + '$'):
                GitHubClient('secret', Opener(error)).archive(1)


if __name__ == '__main__':
    unittest.main()
