"""Regression tests for the missing-Mapbox-key 500s.

Root cause: the config loader only applied a line when ``key and value`` were
both truthy, so ``MAPBOX_API_KEY = ''`` (written by setup.sh when the user
declines a Mapbox key) never reached ``app.config``. Every
``app.config['MAPBOX_API_KEY']`` subscript then raised ``KeyError`` and Flask
returned HTTP 500.
"""

from flask import Flask

import app as beakon_app

HTTPS = 'https://localhost'


def _write_config(tmp_path, body):
    cfg = tmp_path / 'config.cfg'
    cfg.write_text(body)
    return str(cfg)


def test_loader_keeps_empty_string_value(tmp_path):
    """An empty value is a legitimate setting and must survive the loader."""
    target = Flask(__name__)
    path = _write_config(
        tmp_path,
        "MAPBOX_API_KEY = ''\n"
        "REGISTRATION_ENABLED = \n",
    )

    beakon_app.load_config(target, path)

    assert target.config['MAPBOX_API_KEY'] == ''
    assert target.config['REGISTRATION_ENABLED'] == ''


def test_loader_still_coerces_booleans(tmp_path):
    """The string->bool coercion must be unchanged for real values."""
    target = Flask(__name__)
    path = _write_config(
        tmp_path,
        "MAPBOX_API_KEY = 'pk.test'\n"
        "REGISTRATION_ENABLED = false\n"
        "DEBUG = 1\n",
    )

    beakon_app.load_config(target, path)

    assert target.config['MAPBOX_API_KEY'] == 'pk.test'
    assert target.config['REGISTRATION_ENABLED'] is False
    assert target.config['DEBUG'] is True


def test_authenticated_pages_ok_with_empty_key(auth_client, flask_app):
    """No page may 500 (or emit an empty access_token) when the key is unset."""
    flask_app.config['MAPBOX_API_KEY'] = ''

    for path in ('/account', '/dashboard', '/presence'):
        resp = auth_client.get(path, base_url=HTTPS)
        assert resp.status_code == 200, '%s returned %s' % (path, resp.status_code)
        assert b'access_token=' not in resp.data, path


def test_pages_render_token_when_key_set(auth_client, flask_app):
    """With a key present, behaviour is unchanged: the token reaches the page."""
    flask_app.config['MAPBOX_API_KEY'] = 'pk.test-token-12345'

    for path in ('/account', '/dashboard'):
        resp = auth_client.get(path, base_url=HTTPS)
        assert resp.status_code == 200, '%s returned %s' % (path, resp.status_code)
        assert b'pk.test-token-12345' in resp.data, path
