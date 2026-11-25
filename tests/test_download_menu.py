"""Tests for the shared overflow menu's external Android app download link."""

HTTPS = 'https://localhost'
APK_URL = ('https://github.com/RealDudePerson/beakon-android/releases/download/'
           'v1.4/beakon-companion-1.4-release.apk')

# The menu is a Foundation Dropdown: a real button plus a data-dropdown pane.
MENU_MARKERS = (
    'data-toggle="topbar-overflow-menu"',
    'id="topbar-overflow-menu"',
    'data-dropdown',
    'Download the Beakon app',
    APK_URL,
)


def _get_html(client, path):
    response = client.get(path, base_url=HTTPS)
    assert response.status_code == 200, (path, response.status_code)
    return response.get_data(as_text=True)


def _assert_menu_present(html):
    for marker in MENU_MARKERS:
        assert marker in html, 'missing %r from rendered page' % marker
    assert 'href="%s" download' % APK_URL in html
    # Accessibility contract: a real button with the ARIA wiring.
    assert '<button' in html
    assert 'aria-haspopup="true"' in html
    assert 'aria-expanded="false"' in html


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
