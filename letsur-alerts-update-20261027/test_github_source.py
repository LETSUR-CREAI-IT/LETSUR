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

from github_source import fetch_latest, GitHubClient, SourceError, unpack, unpack_cards

NOW = datetime(2026, 10, 27, 1, tzinfo=timezone.utc)
PAGES_ID = 900  # ids of github-pages artifacts in FakeClient: this plus the run id
CARDS = {'schema': 1, 'base_day': '2026-10-27', 'names': {'routes': {'openrouter': 'OpenRouter · openrouter'}},
         'cards': [{'id': 'g_0000000000a1', 'events': ['e1'], 'action': 'REVIEW'}]}
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


def pages_archive(members=None, name='artifact.tar'):
    """What actions/upload-pages-artifact uploads: a zip holding artifact.tar, a plain tar of the site folder
    (`tar -cf artifact.tar --directory site .`, so the members are ./, ./cards.json and ./index.html)."""
    if members is None:
        members = [('index.html', b'<!doctype html>'), ('cards.json', json.dumps(CARDS).encode())]
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as tar:
        folder = tarfile.TarInfo('./')
        folder.type, folder.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(folder)
        for filename, content in members:
            member = tarfile.TarInfo('./' + filename.rstrip('/'))
            if filename.endswith('/'):
                member.type = tarfile.DIRTYPE
            else:
                member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as zip:
        zip.writestr(name, stream.getvalue())
    return result.getvalue()


class FakeClient:
    def __init__(self, runs=None, successful=None, blob=None, pages=None, pages_expired=False, pages_copies=1,
                 pages_digest=None):
        self.runs = runs or [run()]
        self.successful = successful if successful is not None else self.runs
        self.blob = blob or archive()
        self.pages = pages  # the run's github-pages artifact, or None when it has none
        self.pages_expired, self.pages_copies, self.pages_digest = pages_expired, pages_copies, pages_digest
        self.downloads = 0

    def json(self, path):
        if '/artifacts?' in path:
            identity = int(path.split('/runs/')[1].split('/')[0])
            artifacts = [{'id': 100 + identity,
                          'name': 'market-collection-' + str(identity) + '-1', 'expired': False,
                          'digest': 'sha256:' + hashlib.sha256(self.blob).hexdigest()}]
            if self.pages is not None:
                artifacts += [{'id': PAGES_ID + identity + copy, 'name': 'github-pages', 'expired': self.pages_expired,
                               'digest': self.pages_digest or 'sha256:' + hashlib.sha256(self.pages).hexdigest()}
                              for copy in range(self.pages_copies)]
            return {'artifacts': artifacts}
        return {'workflow_runs': self.successful if 'status=success' in path else self.runs}

    def archive(self, identity):
        self.downloads += 1
        return self.pages if identity >= PAGES_ID else self.blob


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

    def test_reads_cards_json_out_of_the_pages_artifact(self):
        self.assertEqual(json.loads(unpack_cards(pages_archive())), CARDS)

    def test_cards_json_is_found_with_or_without_the_dot_prefix_but_only_at_the_top(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as tar:
            for filename, text in (('docs/cards.json', b'{"nested": true}'), ('cards.json', json.dumps(CARDS).encode())):
                member = tarfile.TarInfo(filename)
                member.size = len(text)
                tar.addfile(member, io.BytesIO(text))
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, 'w') as zip:
            zip.writestr('artifact.tar', stream.getvalue())
        self.assertEqual(json.loads(unpack_cards(blob.getvalue())), CARDS)
        with self.assertRaisesRegex(SourceError, '^cards_not_in_artifact$'):
            unpack_cards(pages_archive([('index.html', b'x'), ('docs/cards.json', b'{}')]))

    def test_pages_artifact_without_cards_json_says_so(self):
        # A build from before the dashboard wrote cards.json.
        with self.assertRaisesRegex(SourceError, '^cards_not_in_artifact$'):
            unpack_cards(pages_archive([('index.html', b'<!doctype html>')]))

    def test_rejects_unusable_pages_artifacts(self):
        text = json.dumps(CARDS).encode()
        cases = {
            'not a zip': b'bad zip',
            'no artifact.tar': pages_archive(name='site.tar'),
            'path traversal': pages_archive([('../escape.txt', b'x'), ('cards.json', text)]),
            'two cards.json': pages_archive([('cards.json', text), ('cards.json', text)]),
            'cards.json is not JSON': pages_archive([('cards.json', b'{broken')]),
            'cards.json is not an object': pages_archive([('cards.json', b'[]')]),
            'cards.json is a folder': pages_archive([('cards.json/', b'')]),
        }
        for label, blob in cases.items():
            with self.subTest(label), self.assertRaisesRegex(SourceError, '^invalid_artifact$'):
                unpack_cards(blob)

    def test_fetch_latest_keeps_cards_json_beside_the_events_and_downloads_it_once(self):
        with temporary_directory() as folder:
            client = FakeClient(pages=pages_archive())
            one = fetch_latest(folder, client=client, now=NOW)
            two = fetch_latest(folder, client=client, now=NOW)
            self.assertIsNone(one['cards_error'])
            self.assertEqual(json.loads(Path(one['cards_path']).read_text(encoding='utf-8')), CARDS)
            self.assertEqual(Path(one['cards_path']).parent, Path(one['events_path']).parent)
            self.assertEqual(one['cards_path'], two['cards_path'])
            self.assertEqual(client.downloads, 2)  # the collection and the Pages archive, once each

    def test_missing_expired_ambiguous_or_broken_pages_artifact_is_reported_and_the_events_still_load(self):
        cases = [
            (FakeClient(), 'pages_artifact_missing'),
            (FakeClient(pages=pages_archive(), pages_expired=True), 'pages_artifact_expired'),
            (FakeClient(pages=pages_archive(), pages_copies=2), 'pages_artifact_ambiguous'),
            (FakeClient(pages=pages_archive(), pages_digest='sha256:' + '0' * 64), 'archive_digest_mismatch'),
            (FakeClient(pages=b'bad zip'), 'invalid_artifact'),
            (FakeClient(pages=pages_archive([('index.html', b'x')])), 'cards_not_in_artifact'),
        ]
        for client, code in cases:
            with self.subTest(code), temporary_directory() as folder:
                result = fetch_latest(folder, client=client, now=NOW)
                self.assertEqual((result['cards_path'], result['cards_error']), (None, code))
                self.assertTrue(result['fresh'])
                self.assertEqual(json.loads(Path(result['events_path']).read_text())['events'][0]['event_id'], 'e1')
                self.assertFalse(Path(result['events_path']).with_name('cards.json').exists())

    def test_cards_json_missing_once_is_fetched_on_the_next_call(self):
        with temporary_directory() as folder:
            first = fetch_latest(folder, client=FakeClient(), now=NOW)
            self.assertEqual(first['cards_error'], 'pages_artifact_missing')
            client = FakeClient(pages=pages_archive())
            second = fetch_latest(folder, client=client, now=NOW)
            self.assertIsNone(second['cards_error'])
            self.assertEqual(json.loads(Path(second['cards_path']).read_text(encoding='utf-8')), CARDS)
            self.assertEqual(client.downloads, 1)  # only the Pages archive: the collection was cached

    def test_damaged_cached_cards_json_is_fetched_again(self):
        with temporary_directory() as folder:
            first = fetch_latest(folder, client=FakeClient(pages=pages_archive()), now=NOW)
            Path(first['cards_path']).write_text('{broken', encoding='utf-8')
            client = FakeClient(pages=pages_archive())
            second = fetch_latest(folder, client=client, now=NOW)
            self.assertEqual(json.loads(Path(second['cards_path']).read_text(encoding='utf-8')), CARDS)
            self.assertEqual(client.downloads, 1)


if __name__ == '__main__':
    unittest.main()
