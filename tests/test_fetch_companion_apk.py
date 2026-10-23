"""Tests for the deploy-time companion APK fetch.

See REQUIREMENTS-FETCH-APK.md. Two things are under test:

* ``companion-apk.txt`` — the tracked, machine-readable single source of truth
  for which APK build an instance should serve.
* ``scripts/fetch-companion-apk.sh`` — the downloader that reads that file,
  verifies the asset against it, and only then installs it.

Most tests run against a local HTTP server so they are hermetic and fast. The
one test that must talk to the real GitHub release is marked ``network``: it is
deselected with ``-m "not network"`` rather than skipped silently, so it runs by
default and a broken release is a failure, not a quiet omission.
"""

import hashlib
import http.server
import os
import shutil
import subprocess
import threading

import pytest

# The pinned values the menu test hard-codes; companion-apk.txt must agree.
from test_apk_download_menu import APK_PACKAGE, APK_SHA256, APK_SIZE  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, 'scripts', 'fetch-companion-apk.sh')
MANIFEST = os.path.join(REPO_ROOT, 'companion-apk.txt')
APK_PATH = os.path.join(REPO_ROOT, 'static', 'beakon-companion-debug.apk')
APK_FILENAME = 'beakon-companion-debug.apk'


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _parse_manifest(path=MANIFEST):
    values = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip()
    return values


def _write_manifest(path, tag, filename, size, sha256):
    with open(path, 'w') as fh:
        fh.write(
            '# test manifest\n'
            'tag=%s\n'
            'filename=%s\n'
            'size=%s\n'
            'sha256=%s\n' % (tag, filename, size, sha256)
        )


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _run(args, **kwargs):
    return subprocess.run(
        [str(a) for a in args], capture_output=True, text=True, **kwargs)


class _APKServer:
    """A throwaway HTTP server that serves (or 404s) the companion asset."""

    def __init__(self, payload):
        self.payload = payload
        self.filename = APK_FILENAME
        self.requests = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                server.requests.append(
                    (self.path,
                     {k.lower(): v for k, v in self.headers.items()}))
                if self.path.endswith('/' + server.filename):
                    body = server.payload
                    self.send_response(200)
                    self.send_header(
                        'Content-Type', 'application/vnd.android.package-archive')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()

            def log_message(self, *args):
                pass

        self.httpd = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return 'http://127.0.0.1:%d' % self.httpd.server_address[1]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def apk_server():
    started = []

    def factory(payload=b'fake-apk-payload'):
        server = _APKServer(payload)
        started.append(server)
        return server

    yield factory
    for server in started:
        server.close()


# --------------------------------------------------------------------------
# The script and the manifest
# --------------------------------------------------------------------------
def test_script_exists_executable_and_has_a_valid_shebang():
    assert os.path.isfile(SCRIPT), SCRIPT
    assert os.access(SCRIPT, os.X_OK), 'fetch-companion-apk.sh is not executable'
    with open(SCRIPT, 'rb') as fh:
        shebang = fh.readline()
    assert shebang.startswith(b'#!'), shebang
    assert b'bash' in shebang, shebang
    result = subprocess.run(
        ['bash', '-n', SCRIPT], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_manifest_parses_and_agrees_with_the_pinned_test_values():
    manifest = _parse_manifest()
    for key in ('tag', 'filename', 'size', 'sha256'):
        assert manifest.get(key), 'companion-apk.txt is missing %r' % key
    assert manifest['filename'] == APK_FILENAME
    assert int(manifest['size']) == APK_SIZE
    assert manifest['sha256'].lower() == APK_SHA256


def test_manifest_agrees_with_the_apk_that_is_actually_served():
    assert os.path.isfile(APK_PATH), APK_PATH
    manifest = _parse_manifest()
    assert os.path.getsize(APK_PATH) == int(manifest['size'])
    assert _sha256(APK_PATH) == manifest['sha256'].lower()


# --------------------------------------------------------------------------
# Idempotency, no credentials, corrupt input — all hermetic
# --------------------------------------------------------------------------
def test_idempotent_skip_without_download_and_force_refetch(tmp_path, apk_server):
    payload = b'idempotent-payload'
    server = apk_server(payload)
    manifest = tmp_path / 'companion-apk.txt'
    _write_manifest(manifest, 'companion-v1.0.0', APK_FILENAME,
                    len(payload), hashlib.sha256(payload).hexdigest())
    dest = tmp_path / 'dest' / APK_FILENAME
    dest.parent.mkdir(parents=True)
    dest.write_bytes(payload)

    before = len(server.requests)
    skip = _run([SCRIPT, '--manifest', manifest, '--base-url', server.base_url,
                 '--dest', dest])
    assert skip.returncode == 0, (skip.stdout, skip.stderr)
    assert 'already matches' in skip.stdout.lower()
    assert len(server.requests) == before, 'idempotent run still downloaded'

    forced = _run([SCRIPT, '--force', '--manifest', manifest,
                   '--base-url', server.base_url, '--dest', dest])
    assert forced.returncode == 0, (forced.stdout, forced.stderr)
    assert len(server.requests) > before, '--force did not re-download'
    assert dest.read_bytes() == payload


def test_no_credentials_are_required_or_sent(tmp_path, apk_server):
    payload = b'no-creds-payload'
    server = apk_server(payload)
    manifest = tmp_path / 'companion-apk.txt'
    _write_manifest(manifest, 'companion-v1.0.0', APK_FILENAME,
                    len(payload), hashlib.sha256(payload).hexdigest())
    dest = tmp_path / 'dest' / APK_FILENAME

    env = dict(os.environ, GITHUB_TOKEN='canary-token-that-must-not-be-sent')
    result = _run([SCRIPT, '--force', '--manifest', manifest,
                   '--base-url', server.base_url, '--dest', dest], env=env)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert dest.is_file()
    assert server.requests, 'the script never contacted the asset host'
    for _path, headers in server.requests:
        assert 'authorization' not in headers, headers
        assert 'private-token' not in headers, headers

    # And the code itself never references a token (comments may mention why).
    with open(SCRIPT) as fh:
        code = '\n'.join(
            line for line in fh.read().splitlines()
            if not line.lstrip().startswith('#'))
    assert 'GITHUB_TOKEN' not in code
    assert 'Authorization' not in code


def test_tampered_manifest_fails_nonzero_and_leaves_no_file(tmp_path, apk_server):
    """A wrong hash in companion-apk.txt must abort before anything is installed."""
    payload = b'tamper-target-bytes'
    server = apk_server(payload)
    manifest = tmp_path / 'companion-apk.txt'
    _write_manifest(manifest, 'companion-v1.0.0', APK_FILENAME,
                    len(payload), 'f' * 64)  # well-formed but wrong
    dest = tmp_path / 'dest' / APK_FILENAME

    result = _run([SCRIPT, '--force', '--manifest', manifest,
                   '--base-url', server.base_url, '--dest', dest])

    assert result.returncode != 0, (result.stdout, result.stderr)
    assert not dest.exists(), 'a file that failed verification was installed'
    assert list(dest.parent.iterdir()) == [], 'a temporary file was left behind'
    assert 'sha256' in (result.stdout + result.stderr).lower()


def test_size_mismatch_fails_nonzero_and_leaves_no_file(tmp_path, apk_server):
    payload = b'short'
    server = apk_server(payload)
    manifest = tmp_path / 'companion-apk.txt'
    _write_manifest(manifest, 'companion-v1.0.0', APK_FILENAME,
                    len(payload) + 1, hashlib.sha256(payload).hexdigest())
    dest = tmp_path / 'dest' / APK_FILENAME

    result = _run([SCRIPT, '--force', '--manifest', manifest,
                   '--base-url', server.base_url, '--dest', dest])

    assert result.returncode != 0, (result.stdout, result.stderr)
    assert not dest.exists()
    assert list(dest.parent.iterdir()) == []
    assert 'size' in (result.stdout + result.stderr).lower()


def test_http_404_is_reported_and_leaves_no_file(tmp_path, apk_server):
    server = apk_server(b'x')
    manifest = tmp_path / 'companion-apk.txt'
    _write_manifest(manifest, 'companion-v1.0.0', 'missing.apk',
                    1, '0' * 64)
    dest = tmp_path / 'dest' / APK_FILENAME

    result = _run([SCRIPT, '--force', '--manifest', manifest,
                   '--base-url', server.base_url, '--dest', dest])

    assert result.returncode != 0, (result.stdout, result.stderr)
    assert not dest.exists()
    assert list(dest.parent.iterdir()) == []
    combined = (result.stdout + result.stderr).lower()
    assert 'http' in combined or '404' in combined


def test_missing_manifest_fails_with_an_actionable_message(tmp_path):
    dest = tmp_path / 'dest' / APK_FILENAME
    result = _run([SCRIPT, '--force', '--manifest', tmp_path / 'nope.txt',
                   '--dest', dest])

    assert result.returncode != 0
    assert not dest.exists()
    combined = (result.stdout + result.stderr).lower()
    assert 'manifest' in combined
    assert 'git checkout' in combined


@pytest.mark.parametrize('content,needle', [
    ('filename=%s\nsize=1\nsha256=%s\n' % (APK_FILENAME, '0' * 64), 'tag'),
    ('tag=x\nfilename=%s\nsize=notanumber\nsha256=%s\n' % (APK_FILENAME, '0' * 64), 'size'),
    ('tag=x\nfilename=%s\nsize=1\nsha256=zzzz\n' % APK_FILENAME, 'sha256'),
    ('tag=x\nfilename=%s\nsize=1\nsha256=%s\n' % (APK_FILENAME, '0' * 63), 'sha256'),
])
def test_corrupt_manifest_fails_clearly(tmp_path, content, needle):
    manifest = tmp_path / 'companion-apk.txt'
    manifest.write_text(content)
    dest = tmp_path / 'dest' / APK_FILENAME

    result = _run([SCRIPT, '--force', '--manifest', manifest, '--dest', dest])

    assert result.returncode != 0, (result.stdout, result.stderr)
    assert not dest.exists()
    assert needle in (result.stdout + result.stderr).lower()


# --------------------------------------------------------------------------
# The real thing: fetch the pinned asset from the public GitHub release
# --------------------------------------------------------------------------
@pytest.mark.network
def test_real_fetch_to_a_temp_destination(tmp_path):
    """Download the pinned release asset for real and verify it byte-for-byte.

    Marked ``network`` so it can be deselected on an offline host with
    ``-m "not network"``. It is intentionally *not* skipped when the network is
    unavailable: a real fetch failure should be a visible failure, not silence.
    """
    dest = tmp_path / 'nested' / 'beakon-companion-debug.apk'
    result = _run([SCRIPT, '--force', '--dest', dest])

    assert result.returncode == 0, (
        'real fetch failed; if this host is offline, deselect with '
        '-m "not network"\nstdout: %s\nstderr: %s'
        % (result.stdout, result.stderr))
    assert dest.is_file(), 'script reported success but produced no file'
    assert dest.stat().st_size == APK_SIZE
    assert _sha256(dest) == APK_SHA256

    # And the manifest agrees with what the release actually serves.
    manifest = _parse_manifest()
    assert int(manifest['size']) == dest.stat().st_size
    assert manifest['sha256'].lower() == _sha256(dest)

    # The downloaded binary is the companion app, not some other APK.
    from test_apk_download_menu import _package_name_of
    assert _package_name_of(dest.read_bytes()) == APK_PACKAGE
