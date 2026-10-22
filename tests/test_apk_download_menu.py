"""Acceptance tests for the top-bar overflow menu and the hosted Android APK.

See REQUIREMENTS-APK-DOWNLOAD-MENU.md.

The regression that matters here is placement: most templates override the
``top_menu_buttons`` block, so the overflow menu must live *outside* that block
to survive. These tests therefore assert its markup on a page that overrides the
block (the dashboard, and the login page whose block holds only a Login button)
as well as on a page that uses the default block (the account page).
"""

import hashlib
import os
import shutil
import subprocess

import pytest

HTTPS = 'https://localhost'
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APK_PATH = os.path.join(REPO_ROOT, 'static', 'scavenge-companion-debug.apk')
APK_URL = '/static/scavenge-companion-debug.apk'
APK_SIZE = 12836013
APK_SHA256 = '49c0f62d2255e8a9d4c23a11241c7d1fe281d4e84e776cb68cc6518d895d0ab2'
APK_MIMETYPE = 'application/vnd.android.package-archive'

# The pieces that make up the overflow menu. The menu is a Foundation Dropdown:
# a real <button data-toggle=...> plus a data-dropdown pane.
MENU_MARKERS = (
    'data-toggle="topbar-overflow-menu"',
    'id="topbar-overflow-menu"',
    'data-dropdown',
    'Download Android app',
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


def test_apk_is_git_ignored():
    """The 12.8MB binary stays untracked (see .gitignore)."""
    if shutil.which('git') is None:
        pytest.skip('git not available')
    result = subprocess.run(
        ['git', '-C', REPO_ROOT, 'check-ignore', '-q',
         'static/scavenge-companion-debug.apk'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert result.returncode == 0, 'static/scavenge-companion-debug.apk is not ignored'


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
    assert 'Download Android app' in html
    _assert_menu_present(html)


def test_overflow_menu_on_account_which_uses_the_default_block(auth_client):
    html = _get_html(auth_client, '/account')
    # account.html does not override the block, so layout's default items show.
    assert 'Dashboard' in html
    _assert_menu_present(html)
