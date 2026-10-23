"""Acceptance tests for the top-bar overflow menu and the hosted Android APK.

See REQUIREMENTS-BEAKON-APK.md (and the earlier REQUIREMENTS-APK-DOWNLOAD-MENU.md
that introduced the menu).

The regression that matters here is placement: most templates override the
``top_menu_buttons`` block, so the overflow menu must live *outside* that block
to survive. These tests therefore assert its markup on a page that overrides the
block (the dashboard, and the login page whose block holds only a Login button)
as well as on a page that uses the default block (the account page).

A second regression, the one this file exists to catch, is *which* app is served:
the menu must offer Beakon's own companion app (``com.beakon.companion``), not
another project's APK.
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
import zipfile

import pytest

HTTPS = 'https://localhost'
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APK_PATH = os.path.join(REPO_ROOT, 'static', 'beakon-companion-debug.apk')
APK_URL = '/static/beakon-companion-debug.apk'
APK_MIMETYPE = 'application/vnd.android.package-archive'
APK_PACKAGE = 'com.beakon.companion'

# ``companion-apk.txt`` is the single source of truth for which build an
# instance serves. Read the pinned size and hash from it here instead of
# hard-coding them, so bumping the app is a one-file edit and the test cannot
# drift from the pin. ``scripts/fetch-companion-apk.sh`` reads the same file.
MANIFEST_PATH = os.path.join(REPO_ROOT, 'companion-apk.txt')


def _read_manifest(path=MANIFEST_PATH):
    """Parse the key=value manifest at ``path`` into a dict."""
    values = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip()
    return values


_PINNED_BUILD = _read_manifest()
APK_SIZE = int(_PINNED_BUILD['size'])
APK_SHA256 = _PINNED_BUILD['sha256'].lower()

# The Scavenge hunter app is a *different* project (fruitsaladday.com). Serving
# it from a Beakon instance was the original bug; it must be gone.
SCAVENGE_APK_PATH = os.path.join(REPO_ROOT, 'static', 'scavenge-companion-debug.apk')
SCAVENGE_APK_URL = '/static/scavenge-companion-debug.apk'

# The pieces that make up the overflow menu. The menu is a Foundation Dropdown:
# a real <button data-toggle=...> plus a data-dropdown pane.
MENU_MARKERS = (
    'data-toggle="topbar-overflow-menu"',
    'id="topbar-overflow-menu"',
    'data-dropdown',
    'Download the Beakon app',
    APK_URL,
)


def _get_html(client, path):
    resp = client.get(path, base_url=HTTPS)
    assert resp.status_code == 200, (path, resp.status_code)
    return resp.get_data(as_text=True)


def _assert_menu_present(html):
    for marker in MENU_MARKERS:
        assert marker in html, 'missing %r from rendered page' % marker
    # Accessibility contract: a real button, not a div, with the ARIA wiring.
    assert '<button' in html
    assert 'aria-haspopup="true"' in html
    assert 'aria-expanded="false"' in html


def _find_aapt2():
    """Locate aapt2 from PATH or a conventional Android SDK location."""
    exe = shutil.which('aapt2')
    if exe:
        return exe
    sdk_roots = [
        os.environ.get('ANDROID_HOME'),
        os.environ.get('ANDROID_SDK_ROOT'),
        os.path.expanduser('~/android-sdk'),
        os.path.expanduser('~/Android/Sdk'),
        '/opt/android-sdk',
        '/usr/lib/android-sdk',
    ]
    for root in sdk_roots:
        if not root:
            continue
        build_tools = os.path.join(root, 'build-tools')
        if not os.path.isdir(build_tools):
            continue
        for version in sorted(os.listdir(build_tools), reverse=True):
            candidate = os.path.join(build_tools, version, 'aapt2')
            if os.path.isfile(candidate):
                return candidate
    return None


def _package_name_of(apk_bytes):
    """Return the APK's package name, preferring ``aapt2 dump badging``.

    Falls back to reading the package string out of ``AndroidManifest.xml`` in
    the APK when aapt2 is unavailable, so the assertion still runs everywhere.
    """
    aapt2 = _find_aapt2()
    if aapt2 is not None:
        with tempfile.NamedTemporaryFile(suffix='.apk') as tmp:
            tmp.write(apk_bytes)
            tmp.flush()
            result = subprocess.run(
                [aapt2, 'dump', 'badging', tmp.name],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert result.returncode == 0, 'aapt2 failed to read the served APK'
        first_line = result.stdout.decode('utf-8', 'replace').splitlines()[0]
        assert first_line.startswith('package:'), first_line
        return first_line.split("name='", 1)[1].split("'", 1)[0]

    # Fallback: binary XML string pools are commonly UTF-16LE (or UTF-8).
    with tempfile.NamedTemporaryFile(suffix='.apk') as tmp:
        tmp.write(apk_bytes)
        tmp.flush()
        with zipfile.ZipFile(tmp.name) as archive:
            manifest = archive.read('AndroidManifest.xml')
    for encoding in ('utf-16-le', 'utf-8'):
        if APK_PACKAGE.encode(encoding) in manifest:
            return APK_PACKAGE
    return None


# --------------------------------------------------------------------------
# The APK copy and how it is served
# --------------------------------------------------------------------------
def test_apk_copy_matches_source_build():
    """The hosted file is the exact build described in the requirements."""
    assert os.path.isfile(APK_PATH), APK_PATH
    assert os.path.getsize(APK_PATH) == APK_SIZE
    digest = hashlib.sha256()
    with open(APK_PATH, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    assert digest.hexdigest() == APK_SHA256


def test_apk_served_with_installer_content_type_and_length(flask_app):
    client = flask_app.test_client()
    resp = client.get(APK_URL, base_url=HTTPS)
    assert resp.status_code == 200
    assert resp.mimetype == APK_MIMETYPE, resp.headers.get('Content-Type')
    assert resp.headers['Content-Length'] == str(APK_SIZE)
    # Confirm the body really is the whole file and not a placeholder.
    assert len(resp.get_data()) == APK_SIZE


def test_served_apk_is_beakon_companion(flask_app):
    """The binary served at the menu's URL is Beakon's own companion app.

    This is the assertion that would have caught the original mistake: the
    Scavenge hunter app's package is ``com.scavenge.hunter``.
    """
    client = flask_app.test_client()
    resp = client.get(APK_URL, base_url=HTTPS)
    assert resp.status_code == 200
    served = resp.get_data()
    assert len(served) == APK_SIZE
    assert _package_name_of(served) == APK_PACKAGE


def test_scavenge_apk_is_no_longer_served(flask_app):
    """The Scavenge hunter app must not be present or downloadable here."""
    assert not os.path.exists(SCAVENGE_APK_PATH), SCAVENGE_APK_PATH
    client = flask_app.test_client()
    resp = client.get(SCAVENGE_APK_URL, base_url=HTTPS)
    assert resp.status_code == 404


def test_apk_is_git_ignored():
    """The 12.8MB binary stays untracked (see .gitignore)."""
    if shutil.which('git') is None:
        pytest.skip('git not available')
    result = subprocess.run(
        ['git', '-C', REPO_ROOT, 'check-ignore', '-q',
         'static/beakon-companion-debug.apk'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert result.returncode == 0, 'static/beakon-companion-debug.apk is not ignored'


# --------------------------------------------------------------------------
# The overflow menu renders on every page — including block overriders
# --------------------------------------------------------------------------
def test_overflow_menu_on_dashboard_which_overrides_the_block(auth_client):
    html = _get_html(auth_client, '/dashboard')
    # dashboard.html replaces the block; its own "Account" item proves the
    # override is in effect, and the menu must still be there.
    assert 'Account' in html
    _assert_menu_present(html)


def test_overflow_menu_on_login_which_overrides_the_block(flask_app):
    client = flask_app.test_client()
    html = _get_html(client, '/login')
    # login.html's block contains only a Login button; the menu is outside it.
    assert 'Download the Beakon app' in html
    _assert_menu_present(html)


def test_overflow_menu_on_account_which_uses_the_default_block(auth_client):
    html = _get_html(auth_client, '/account')
    # account.html does not override the block, so layout's default items show.
    assert 'Dashboard' in html
    _assert_menu_present(html)
