"""Read-only GitHub Actions source; credentials never enter caches or errors."""
import hashlib
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from alerts import validate_events

REPOSITORY = 'LETSUR-CREAI-IT/LETSUR'
BRANCH = 'develop'
WORKFLOW = 'market-collection.yml'
BASE = '/repos/' + REPOSITORY + '/actions'
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_TAR = 256 * 1024 * 1024
MAX_JSON = 32 * 1024 * 1024
FILES = ('events.json', 'report.json', 'continuity.json')
# actions/upload-pages-artifact's default name. The dashboard build writes cards.json next to index.html, so it is in there too.
PAGES_ARTIFACT = 'github-pages'


class SourceError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    def __init__(self, token=None, opener=None):
        self._token = token
        self._opener = opener or urllib.request.build_opener(NoRedirect())

    def _read(self, path, *, archive=False):
        if not path.startswith(BASE + '/') or '..' in path:
            raise SourceError('invalid_api_path')
        url = 'https://api.github.com' + path
        headers = {'Accept': 'application/vnd.github+json',
                   'User-Agent': 'letsur-alert-source', 'X-GitHub-Api-Version': '2022-11-28'}
        if self._token:
            headers['Authorization'] = 'Bearer ' + self._token
        for hop in range(4):
            try:
                with self._opener.open(urllib.request.Request(url, headers=headers), timeout=40) as stream:
                    limit = MAX_ARCHIVE if archive else MAX_JSON
                    data = stream.read(limit + 1)
                    if len(data) > limit:
                        raise SourceError('response_too_large')
                    return data
            except urllib.error.HTTPError as error:
                if archive and error.code in (301, 302, 303, 307, 308):
                    url = urllib.parse.urljoin(url, error.headers.get('Location', ''))
                    target = urllib.parse.urlsplit(url)
                    if (target.scheme != 'https' or not target.hostname or target.username or
                            target.password or target.port not in (None, 443)):
                        raise SourceError('invalid_download_redirect') from None
                    # Signed artifact URLs authenticate themselves. Never forward the API token.
                    headers = {'User-Agent': 'letsur-alert-source'}
                    continue
                if error.code == 429 or (error.code == 403 and error.headers.get('X-RateLimit-Remaining') == '0'):
                    raise SourceError('rate_limit') from None
                if error.code in (401, 403):
                    raise SourceError('authentication') from None
                if error.code == 404:
                    raise SourceError('not_found_or_no_access') from None
                if error.code == 410:
                    raise SourceError('artifact_expired') from None
                raise SourceError('github_http_' + str(error.code)) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise SourceError('network') from None
        raise SourceError('too_many_redirects')

    def json(self, path):
        try:
            data = json.loads(self._read(path))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise SourceError('invalid_api_response') from None

    def archive(self, artifact_id):
        if not isinstance(artifact_id, int) or artifact_id <= 0:
            raise SourceError('invalid_api_response')
        return self._read(BASE + '/artifacts/' + str(artifact_id) + '/zip', archive=True)


def _date(raw):
    try:
        value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if value.tzinfo is None:
            raise ValueError()
        return value
    except (AttributeError, TypeError, ValueError):
        raise SourceError('invalid_api_response') from None


def _rank(run):
    return (_date(run['created_at']), int(run['id']), int(run.get('run_attempt', 1)))


def _runs(client, status=None):
    result = []
    for page in range(1, 11):
        query = {'branch': BRANCH, 'per_page': 100, 'page': page}
        if status:
            query['status'] = status
        body = client.json(BASE + '/workflows/' + WORKFLOW + '/runs?' + urllib.parse.urlencode(query))
        runs = body.get('workflow_runs')
        if not isinstance(runs, list):
            raise SourceError('invalid_api_response')
        for run in runs:
            if (run.get('head_branch') != BRANCH or
                    (run.get('path') and run['path'].split('@')[0] != '.github/workflows/' + WORKFLOW)):
                continue
            repo = (run.get('repository') or {}).get('full_name')
            if repo and repo.casefold() != REPOSITORY.casefold():
                continue
            result.append(run)
        # APIs return newest first, but choose explicitly instead of trusting array order.
        if result or len(runs) < 100:
            break
    return sorted(result, key=_rank, reverse=True)


def _safe_name(name):
    path = PurePosixPath(name)
    return not path.is_absolute() and '..' not in path.parts and '\\' not in name


def unpack(archive, expected_run):
    """Read exact files without extracting any archive path to the filesystem."""
    try:
        if len(archive) > MAX_ARCHIVE:
            raise ValueError()
        with zipfile.ZipFile(io.BytesIO(archive)) as outer:
            entries = outer.infolist()
            if len(entries) > 1000 or any(not _safe_name(i.filename) for i in entries):
                raise ValueError()
            selected = [i for i in entries if i.filename == 'market-collection.tar.xz']
            if len(selected) != 1 or selected[0].file_size > MAX_ARCHIVE:
                raise ValueError()
            compressed = outer.read(selected[0])
        with lzma.LZMAFile(io.BytesIO(compressed)) as decoded:
            tar_bytes = decoded.read(MAX_TAR + 1)
        if len(tar_bytes) > MAX_TAR:
            raise ValueError()
        raw = {}
        names = {'market_collection/events/' + name: name for name in FILES}
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode='r:') as inner:
            for index, member in enumerate(inner):
                if index > 100000 or not _safe_name(member.name):
                    raise ValueError()
                if member.name not in names:
                    continue
                name = names[member.name]
                if name in raw or not member.isfile() or member.size > MAX_JSON:
                    raise ValueError()
                raw[name] = inner.extractfile(member).read(MAX_JSON + 1)
        if set(raw) != set(FILES):
            raise ValueError()
        documents = {name: json.loads(value) for name, value in raw.items()}
        validate_events(documents['events.json'])
        if not isinstance(documents['report.json'], dict):
            raise ValueError()
        continuity = documents['continuity.json']
        current = continuity.get('current_run') or {}
        if (continuity.get('status') != 'ok' or continuity.get('build_exit_code') != 0 or
                current.get('id') != expected_run['id'] or
                current.get('run_attempt', 1) != expected_run.get('run_attempt', 1) or
                current.get('head_branch') != BRANCH):
            raise ValueError()
        return raw
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, OSError,
            zipfile.BadZipFile, tarfile.TarError, lzma.LZMAError, EOFError):
        raise SourceError('invalid_artifact') from None


def unpack_cards(archive):
    """Read cards.json out of a Pages artifact without extracting anything.

    upload-pages-artifact uploads one file, artifact.tar: a plain tar of the site folder whose members start with './'.
    """
    raw = None
    try:
        if len(archive) > MAX_ARCHIVE:
            raise ValueError()
        with zipfile.ZipFile(io.BytesIO(archive)) as outer:
            entries = outer.infolist()
            if len(entries) > 1000 or any(not _safe_name(i.filename) for i in entries):
                raise ValueError()
            selected = [i for i in entries if i.filename == 'artifact.tar']
            if len(selected) != 1 or selected[0].file_size > MAX_ARCHIVE:
                raise ValueError()
            tar_bytes = outer.read(selected[0])
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode='r:') as inner:
            for index, member in enumerate(inner):
                if index > 100000 or not _safe_name(member.name):
                    raise ValueError()
                if PurePosixPath(member.name).parts != ('cards.json',):
                    continue
                if raw is not None or not member.isfile() or member.size > MAX_JSON:
                    raise ValueError()
                raw = inner.extractfile(member).read(MAX_JSON + 1)
        if raw is not None and not isinstance(json.loads(raw), dict):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, OSError,
            zipfile.BadZipFile, tarfile.TarError, EOFError):
        raise SourceError('invalid_artifact') from None
    if raw is None:
        raise SourceError('cards_not_in_artifact')
    return raw


def _fetch_cards(client, artifacts, destination):
    """Keep the run's cards.json beside its events. It is optional input: a failure comes back as a code and never
    raises, so the alerts still go out (by the bot's own rules) when only the dashboard side is missing."""
    path = destination / 'cards.json'
    try:
        if path.exists():
            json.loads(path.read_bytes())  # a damaged copy is fetched again
            return str(path.resolve()), None
    except (ValueError, OSError):
        pass
    try:
        named = [a for a in artifacts if a.get('name') == PAGES_ARTIFACT]
        found = [a for a in named if a.get('expired') is False]
        if len(found) != 1:
            raise SourceError('pages_artifact_ambiguous' if found else 'pages_artifact_expired' if named else 'pages_artifact_missing')
        archive = client.archive(found[0].get('id'))
        if found[0].get('digest') and found[0]['digest'] != 'sha256:' + hashlib.sha256(archive).hexdigest():
            raise SourceError('archive_digest_mismatch')
        temporary = path.with_suffix('.tmp-' + str(os.getpid()))
        try:
            temporary.write_bytes(unpack_cards(archive))
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    except SourceError as error:
        return None, error.code
    except OSError:
        return None, 'cards_cache'
    return str(path.resolve()), None


def _atomic_json(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, encoding='utf-8', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fetch_latest(cache_dir, token=None, *, client=None, now=None, max_age_hours=36):
    """Return validated cached paths and freshness; never silently fall back after failure.

    Uses the latest successful develop workflow. A newer pending/failed execution or
    an old success is explicitly marked stale, even when its artifact is readable.
    """
    client = client or GitHubClient(token)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now needs a timezone')
    latest_runs = _runs(client)
    successful = [r for r in _runs(client, 'success')
                  if r.get('status') == 'completed' and r.get('conclusion') == 'success']
    if not successful:
        raise SourceError('no_success')
    run = successful[0]
    try:
        run_id, attempt = int(run['id']), int(run.get('run_attempt', 1))
        if run_id <= 0 or attempt <= 0 or not re.fullmatch('[0-9a-fA-F]{40}', run.get('head_sha', '')):
            raise ValueError()
        created = _date(run['created_at'])
    except (KeyError, TypeError, ValueError):
        raise SourceError('invalid_api_response') from None
    stale = []
    if (now - created).total_seconds() > max_age_hours * 3600:
        stale.append('last_success_too_old')
    if latest_runs and _rank(latest_runs[0]) > _rank(run):
        stale.append('newer_run_' + ('pending' if latest_runs[0].get('status') != 'completed' else 'unsuccessful'))
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    old = None
    if (cache / 'latest.json').exists():
        try:
            old = json.loads((cache / 'latest.json').read_text(encoding='utf-8'))
            if (_date(old['run_created_at']), int(old['run_id']), int(old['run_attempt'])) > _rank(run):
                raise SourceError('regression')
        except (ValueError, KeyError, TypeError, OSError):
            raise SourceError('invalid_cache') from None
    artifacts = client.json(BASE + '/runs/' + str(run_id) + '/artifacts?per_page=100').get('artifacts', [])
    wanted = 'market-collection-' + str(run_id) + '-' + str(attempt)
    found = [a for a in artifacts if a.get('name') == wanted and a.get('expired') is False]
    if len(found) != 1:
        raise SourceError('artifact_missing')
    artifact = found[0]
    version = str(run_id) + '-' + str(attempt) + '-' + str(artifact['id'])
    if not re.fullmatch(r'[0-9]+-[0-9]+-[0-9]+', version):
        raise SourceError('invalid_api_response')
    destination = cache / version
    if destination.exists():
        try:
            metadata = json.loads((destination / 'metadata.json').read_text(encoding='utf-8'))
            for name in FILES:
                if hashlib.sha256((destination / name).read_bytes()).hexdigest() != metadata['files'][name]:
                    raise ValueError()
            if metadata['run_id'] != run_id or metadata['run_attempt'] != attempt or metadata['head_sha'] != run['head_sha']:
                raise ValueError()
        except (KeyError, ValueError, OSError):
            raise SourceError('invalid_cache') from None
    else:
        archive = client.archive(artifact['id'])
        archive_hash = hashlib.sha256(archive).hexdigest()
        if artifact.get('digest') and artifact['digest'] != 'sha256:' + archive_hash:
            raise SourceError('archive_digest_mismatch')
        raw = unpack(archive, run)
        metadata = {'repository': REPOSITORY, 'branch': BRANCH, 'workflow': WORKFLOW,
                    'run_id': run_id, 'run_attempt': attempt, 'head_sha': run['head_sha'],
                    'run_created_at': run['created_at'], 'artifact_id': artifact['id'],
                    'archive_sha256': archive_hash, 'downloaded_at': now.isoformat(),
                    'files': {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}}
        temporary = Path(tempfile.mkdtemp(prefix='.download-', dir=cache))
        try:
            for name, value in raw.items():
                (temporary / name).write_bytes(value)
            (temporary / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    _atomic_json(cache / 'latest.json', metadata)
    cards_path, cards_error = _fetch_cards(client, artifacts, destination)
    return {'events_path': str((destination / 'events.json').resolve()),
            'report_path': str((destination / 'report.json').resolve()),
            'continuity_path': str((destination / 'continuity.json').resolve()),
            'cards_path': cards_path, 'cards_error': cards_error,
            'metadata': metadata, 'fresh': not stale, 'stale_reasons': stale}
