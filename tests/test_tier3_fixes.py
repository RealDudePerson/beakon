"""Regression tests for the Tier 3 review items (see REQUIREMENTS-TIER3.md).

Each test maps to an acceptance criterion and is written so that re-introducing
the original bug makes it fail. The tests use the hermetic config/database set
up by conftest.py; the live instance/beakon.db is never touched.
"""

import logging
import os
import re
from datetime import datetime, timedelta

import pytest
from flask import Flask

import app as beakon_app
from app import _attempts
from models import (UserModel, UserDataModel, LocationsModel,
                    SharingPermissionModel, KnownPlaceModel, db)

HTTPS = 'https://localhost'
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_throttle():
    """The throttle is process-global; keep tests independent of each other."""
    _attempts.clear()
    yield
    _attempts.clear()


def make_user(flask_app, username, password='password123', is_admin=False,
              token=None):
    with flask_app.app_context():
        user = UserModel(username=username)
        user.set_password(password)
        if token is not None:
            user.set_api_token(token)
        db.session.add(user)
        db.session.commit()
        db.session.add(UserDataModel(id=user.id, fname='Test', lname='User',
                                     is_admin=is_admin))
        db.session.commit()
        return user.id


def login(flask_app, username, password='password123'):
    client = flask_app.test_client()
    resp = client.post('/login', data={'username': username,
                                       'password': password}, base_url=HTTPS)
    assert resp.status_code in (301, 302, 303), (resp.status_code, resp.data)
    return client


def add_location(flask_app, user_id, lat=40.0, lon=-70.0, batt=50):
    with flask_app.app_context():
        db.session.add(LocationsModel(lat=lat, lon=lon, acc=5.0,
                                      timestamp=datetime.now(), userid=user_id,
                                      batt=batt, ischarging=False))
        db.session.commit()


def add_share(flask_app, owner_id, viewer_id, owner_name, viewer_name):
    with flask_app.app_context():
        share = SharingPermissionModel()
        share.set_data_owner_username(owner_name)
        share.set_data_owner_id(owner_id)
        share.set_shared_with_username(viewer_name)
        share.set_shared_with_id(viewer_id)
        db.session.add(share)
        db.session.commit()


# --------------------------------------------------------------------------
# Criterion 2 (S4) — stored XSS: place rows are built as text, never HTML
# --------------------------------------------------------------------------
def test_s4_place_rows_are_built_without_html_parsing(flask_app):
    make_user(flask_app, 'xss_s4')
    client = login(flask_app, 'xss_s4')
    payload = '<img src=x onerror=alert(1)>'
    created = client.post('/api/places', json={
        'name': payload, 'lat': 40.0, 'lon': -70.0, 'radius': 100,
        'webhook_url': '<svg/onload=alert(2)>'}, base_url=HTTPS)
    assert created.status_code == 201, created.data

    # The API returns the value as data (JSON), which is the correct channel.
    places = client.get('/api/places', base_url=HTTPS).get_json()['places']
    assert places[0]['name'] == payload

    # account.js must build the row with createElement/textContent and must not
    # concatenate markup. Re-introducing the old ``innerHTML +=`` row builder
    # makes this fail.
    with open(os.path.join(REPO_ROOT, 'static', 'js', 'account.js')) as f:
        js = f.read()
    assert 'innerHTML' not in js
    assert 'createElement' in js
    assert 'textContent' in js


def test_s4_dashboard_escapes_place_name(flask_app):
    """Server-side data (the dashboard geofence list) must not emit raw markup."""
    uid = make_user(flask_app, 'xss_s4_dash')
    add_location(flask_app, uid)
    client = login(flask_app, 'xss_s4_dash')
    payload = '<img src=x onerror=alert(1)>'
    client.post('/api/places', json={
        'name': payload, 'lat': 40.0, 'lon': -70.0, 'radius': 100,
        'webhook_url': 'https://example.test/h'}, base_url=HTTPS)

    html = client.get('/dashboard', base_url=HTTPS).get_data(as_text=True)
    assert '<img src=x onerror=alert(1)>' not in html
    assert 'onerror' in html  # present, but only inside escaped JSON


# --------------------------------------------------------------------------
# Criterion 3 (S9) — request bodies (coordinates, tokens) are not logged
# --------------------------------------------------------------------------
def test_s9_record_location_logs_omit_coordinates_and_token(flask_app, caplog):
    token = 'TOKEN-SENTINEL-9f3a2b'
    make_user(flask_app, 'log_s9', token=token)
    client = flask_app.test_client()
    lat, lon = 12.3456789, -98.7654321

    with caplog.at_level(logging.DEBUG):
        resp = client.post('/api/recordlocation',
                           headers={'username': 'log_s9', 'secret': token},
                           json={'lat': lat, 'lon': lon, 'acc': 3},
                           base_url=HTTPS)
    assert resp.status_code == 201, resp.status_code

    emitted = caplog.text
    assert '12.3456789' not in emitted
    assert '-98.7654321' not in emitted
    assert token not in emitted
    # The redacted summary still identifies the request and which fields were
    # present, so the log remains useful.
    assert '/api/recordlocation' in emitted
    assert 'lat' in emitted and 'lon' in emitted


# --------------------------------------------------------------------------
# Criterion 4 (B9) — deleting a user removes its sharing permissions
# --------------------------------------------------------------------------
def test_b9_delete_user_leaves_no_orphan_permissions(flask_app):
    make_user(flask_app, 'b9_admin', is_admin=True)
    owner = make_user(flask_app, 'b9_owner')
    grantee = make_user(flask_app, 'b9_grantee')
    # A share the victim owns and a share the victim was granted: the delete
    # must clean both directions.
    add_share(flask_app, owner, grantee, 'b9_owner', 'b9_grantee')
    add_share(flask_app, grantee, owner, 'b9_grantee', 'b9_owner')

    def referencing(uid):
        return SharingPermissionModel.query.filter(
            (SharingPermissionModel.data_owner_id == uid) |
            (SharingPermissionModel.shared_with_id == uid))

    with flask_app.app_context():
        # Scope the assertions to these users: the suite shares one database.
        assert referencing(grantee).count() == 2

    client = login(flask_app, 'b9_admin')
    resp = client.post('/admin/users/%d/delete' % grantee, base_url=HTTPS)
    assert resp.status_code == 200, resp.data

    with flask_app.app_context():
        assert UserModel.query.get(grantee) is None
        assert referencing(grantee).all() == []
        assert referencing(owner).all() == []


def test_b9_deleted_user_id_is_not_reused(flask_app):
    make_user(flask_app, 'b9_id_admin', is_admin=True)
    victim = make_user(flask_app, 'b9_id_victim')
    client = login(flask_app, 'b9_id_admin')
    assert client.post('/admin/users/%d/delete' % victim,
                       base_url=HTTPS).status_code == 200

    # AUTOINCREMENT keeps the id monotonic; a plain INTEGER PRIMARY KEY would
    # hand the freed (highest) id straight to the next account.
    new_id = make_user(flask_app, 'b9_id_new')
    assert new_id != victim
    assert new_id > victim


# --------------------------------------------------------------------------
# Criterion 7 — config: a named empty key survives the loader
# --------------------------------------------------------------------------
def test_config_loader_sets_named_empty_key(tmp_path):
    target = Flask(__name__)
    cfg = tmp_path / 'config.cfg'
    cfg.write_text(
        "MAPBOX_API_KEY = ''\n"
        "TRUSTED_PROXY_IPS = 192.168.2.8\n"
        "REGISTRATION_ENABLED = false\n"
    )

    beakon_app.load_config(target, str(cfg))

    # Present even though empty -- this is the exact KeyError->500 regression.
    assert 'MAPBOX_API_KEY' in target.config
    assert target.config['MAPBOX_API_KEY'] == ''
    # Legacy bare values are normalised to strings, not left to break exec().
    assert target.config['TRUSTED_PROXY_IPS'] == '192.168.2.8'
    assert target.config['REGISTRATION_ENABLED'] is False


# --------------------------------------------------------------------------
# Criterion 8 — CSP: no inline scripts, no inline handlers
# --------------------------------------------------------------------------
_HANDLER_RE = re.compile(
    r'on(?:click|change|submit|input|load|error|keyup|keydown|focus|blur)=')
_INLINE_SCRIPT_RE = re.compile(
    r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL)


def _template_files():
    templates = os.path.join(REPO_ROOT, 'templates')
    for name in sorted(os.listdir(templates)):
        if name.endswith('.html'):
            yield os.path.join(templates, name)


def test_csp_no_inline_event_handlers():
    offenders = []
    for path in _template_files():
        with open(path) as f:
            for match in _HANDLER_RE.finditer(f.read()):
                offenders.append((path, match.group(0)))
    assert offenders == []


def test_csp_no_inline_script_bodies():
    offenders = []
    for path in _template_files():
        with open(path) as f:
            for match in _INLINE_SCRIPT_RE.finditer(f.read()):
                if match.group(1).strip():
                    offenders.append((path, match.group(1)[:60]))
    assert offenders == []


def test_csp_header_drops_unsafe_inline_for_scripts(auth_client):
    resp = auth_client.get('/dashboard', base_url=HTTPS)
    assert resp.status_code == 200
    csp = resp.headers.get('Content-Security-Policy', '')
    script_src = next(d for d in csp.split(';') if 'script-src' in d)
    assert "'unsafe-inline'" not in script_src
    assert "'unsafe-eval'" in script_src


# --------------------------------------------------------------------------
# Criterion 9 — retention mechanism, default OFF
# --------------------------------------------------------------------------
def test_retention_unset_does_not_purge(flask_app):
    uid = make_user(flask_app, 'retention_off')
    now = datetime.now()
    with flask_app.app_context():
        db.session.add(LocationsModel(lat=1.0, lon=1.0,
                                      timestamp=now - timedelta(days=3650),
                                      userid=uid))
        db.session.add(LocationsModel(lat=2.0, lon=2.0, timestamp=now,
                                      userid=uid))
        db.session.commit()

    old_config = flask_app.config.pop('RETENTION_DAYS', None)
    try:
        assert beakon_app.purge_old_locations() == 0
        with flask_app.app_context():
            assert LocationsModel.query.filter_by(userid=uid).count() == 2
    finally:
        if old_config is not None:
            flask_app.config['RETENTION_DAYS'] = old_config
        with flask_app.app_context():
            LocationsModel.query.filter_by(userid=uid).delete()
            db.session.commit()


def test_retention_purges_only_rows_older_than_cutoff(flask_app):
    uid = make_user(flask_app, 'retention_on')
    other = make_user(flask_app, 'retention_other')
    now = datetime(2026, 1, 1, 12, 0, 0)
    with flask_app.app_context():
        db.session.add_all([
            LocationsModel(lat=10.0, lon=1.0,
                           timestamp=now - timedelta(days=10), userid=uid),
            LocationsModel(lat=20.0, lon=2.0,
                           timestamp=now - timedelta(days=2), userid=uid),
            LocationsModel(lat=30.0, lon=3.0, timestamp=now, userid=uid),
            LocationsModel(lat=40.0, lon=4.0,
                           timestamp=now - timedelta(days=30), userid=other),
        ])
        db.session.commit()

    with flask_app.app_context():
        removed = beakon_app.purge_old_locations(days=5, now=now)
        assert removed == 2  # both rows older than the 5-day cutoff

        remaining = LocationsModel.query.filter_by(userid=uid).all()
        assert sorted(r.lat for r in remaining) == [20.0, 30.0]
        # Nothing newer than the cutoff was touched.
        assert LocationsModel.query.filter_by(userid=other).count() == 0


def test_retention_days_parsing(flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, 'RETENTION_DAYS', '7')
    assert beakon_app._retention_days() == 7
    monkeypatch.setitem(flask_app.config, 'RETENTION_DAYS', 'nonsense')
    assert beakon_app._retention_days() == 0
    monkeypatch.setitem(flask_app.config, 'RETENTION_DAYS', 0)
    assert beakon_app._retention_days() == 0
    monkeypatch.delitem(flask_app.config, 'RETENTION_DAYS', raising=False)
    assert beakon_app._retention_days() == 0


# --------------------------------------------------------------------------
# Criterion 10 — the pages still render, with external scripts only
# --------------------------------------------------------------------------
def test_pages_render_with_external_scripts(flask_app):
    make_user(flask_app, 'pages_smoke', is_admin=True)
    client = login(flask_app, 'pages_smoke')
    for path in ('/dashboard', '/account', '/speed', '/presence',
                 '/admin', '/admin/health', '/admin/audit', '/recordlocation'):
        resp = client.get(path, base_url=HTTPS)
        assert resp.status_code == 200, path
        html = resp.get_data(as_text=True)
        assert '<script>' not in html, path
