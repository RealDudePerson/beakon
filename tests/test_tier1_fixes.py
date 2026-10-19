"""Regression tests for the Tier 1 review findings (see REQUIREMENTS-TIER1.md).

Each test maps to an acceptance criterion and is written so that
re-introducing the original bug makes it fail.
"""

from datetime import datetime

import pytest
from sqlalchemy import text

import geofencing
from app import _attempts
from models import (UserModel, UserDataModel, LocationsModel,
                    SharingPermissionModel, KnownPlaceModel, db)

HTTPS = 'https://localhost'


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


def add_location(flask_app, user_id, lat=40.0, lon=-70.0, batt=50):
    with flask_app.app_context():
        db.session.add(LocationsModel(lat=lat, lon=lon, acc=5.0,
                                      timestamp=datetime.now(), userid=user_id,
                                      batt=batt, ischarging=False))
        db.session.commit()


def login(flask_app, username, password='password123'):
    client = flask_app.test_client()
    resp = client.post('/login', data={'username': username,
                                       'password': password}, base_url=HTTPS)
    assert resp.status_code in (301, 302, 303), (resp.status_code, resp.data)
    return client


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
# B1 — tokenless account must be a clean 401, never a 500
# --------------------------------------------------------------------------
def test_tokenless_account_returns_401_not_500(flask_app):
    make_user(flask_app, 'tokenless_b1')
    client = flask_app.test_client()
    resp = client.post('/api/recordlocation',
                       headers={'username': 'tokenless_b1', 'secret': 'anything'},
                       json={'lat': 1.0, 'lon': 2.0}, base_url=HTTPS)
    assert resp.status_code == 401, resp.status_code


def test_tokenless_and_unknown_users_are_indistinguishable(flask_app):
    make_user(flask_app, 'tokenless_b1_oracle')
    client = flask_app.test_client()
    tokenless = client.post(
        '/api/recordlocation',
        headers={'username': 'tokenless_b1_oracle', 'secret': 'x'},
        json={'lat': 1.0, 'lon': 2.0}, base_url=HTTPS)
    unknown = client.post(
        '/api/recordlocation',
        headers={'username': 'no_such_user_b1', 'secret': 'x'},
        json={'lat': 1.0, 'lon': 2.0}, base_url=HTTPS)
    assert tokenless.status_code == unknown.status_code == 401


# --------------------------------------------------------------------------
# B3 — PUT /api/places validation and stored-row integrity
# --------------------------------------------------------------------------
def test_put_place_invalid_lat_rejected_and_row_unchanged(flask_app):
    uid = make_user(flask_app, 'place_owner_b3')
    client = login(flask_app, 'place_owner_b3')
    created = client.post('/api/places', json={
        'name': 'Home', 'lat': 40.0, 'lon': -70.0, 'radius': 100,
        'webhook_url': 'https://example.test/hook'}, base_url=HTTPS)
    assert created.status_code == 201, created.data

    with flask_app.app_context():
        pid = KnownPlaceModel.query.filter_by(userid=uid).first().id

    resp = client.put('/api/places', json={'id': pid, 'lat': 'abc'},
                      base_url=HTTPS)
    assert resp.status_code == 400, resp.status_code
    assert b'lat' in resp.data

    with flask_app.app_context():
        place = db.session.get(KnownPlaceModel, pid)
        assert place.lat == 40.0
        assert place.lon == -70.0
        assert place.radius == 100


def test_put_place_out_of_range_rejected(flask_app):
    uid = make_user(flask_app, 'place_owner_b3_range')
    client = login(flask_app, 'place_owner_b3_range')
    client.post('/api/places', json={
        'name': 'Home', 'lat': 40.0, 'lon': -70.0, 'radius': 100,
        'webhook_url': 'https://example.test/hook'}, base_url=HTTPS)
    with flask_app.app_context():
        pid = KnownPlaceModel.query.filter_by(userid=uid).first().id
    resp = client.put('/api/places', json={'id': pid, 'lat': 999},
                      base_url=HTTPS)
    assert resp.status_code == 400
    assert b'lat' in resp.data


# --------------------------------------------------------------------------
# B3 resilience — one malformed row must not abort the whole run
# --------------------------------------------------------------------------
def test_geofences_survive_malformed_row(flask_app, monkeypatch):
    bad_uid = make_user(flask_app, 'geo_malformed_b3')
    add_location(flask_app, bad_uid, lat=10.0, lon=10.0)
    good_uid = make_user(flask_app, 'geo_good_b3')
    add_location(flask_app, good_uid, lat=40.0, lon=-70.0)

    with flask_app.app_context():
        # Bypass the ORM/validator (which now rejects this) to simulate a row
        # written before validation existed. Inserted first so a naive run
        # aborts before reaching the good row.
        db.session.execute(text(
            "INSERT INTO known_places "
            "(userid, name, lat, lon, radius, webhook_url, enabled, is_inside, "
            " include_coords_in_webhook) "
            "VALUES (:uid, 'bad', 'abc', 10.0, 100, "
            " 'https://example.test/bad', 1, 0, 0)"), {'uid': bad_uid})
        db.session.commit()
        db.session.add(KnownPlaceModel(
            userid=good_uid, name='good', lat=40.0, lon=-70.0, radius=100,
            webhook_url='https://example.test/good', enabled=True,
            is_inside=False))
        db.session.commit()

    calls = []

    class FakeResponse:
        status_code = 200

    def fake_post(url, **kwargs):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr(geofencing.requests, 'post', fake_post)

    # Must complete without raising even though one row is malformed.
    geofencing.check_geofences(flask_app)

    assert 'https://example.test/good' in calls


# --------------------------------------------------------------------------
# B7 — location ingest validation
# --------------------------------------------------------------------------
def test_api_recordlocation_invalid_lat_400_and_stores_nothing(flask_app):
    uid = make_user(flask_app, 'api_ingest_b7', token='secret-b7')
    with flask_app.app_context():
        before = LocationsModel.query.filter_by(userid=uid).count()

    client = flask_app.test_client()
    resp = client.post('/api/recordlocation',
                       headers={'username': 'api_ingest_b7',
                                'secret': 'secret-b7'},
                       json={'lat': 'x', 'lon': 'y'}, base_url=HTTPS)
    assert resp.status_code == 400, resp.status_code
    assert b'lat' in resp.data

    with flask_app.app_context():
        assert LocationsModel.query.filter_by(userid=uid).count() == before


def test_browser_recordlocation_invalid_lat_400(flask_app):
    uid = make_user(flask_app, 'browser_ingest_b7')
    client = login(flask_app, 'browser_ingest_b7')
    resp = client.post('/recordlocation', json={'lat': 'x', 'lon': 'y'},
                       base_url=HTTPS)
    assert resp.status_code == 400
    with flask_app.app_context():
        assert LocationsModel.query.filter_by(userid=uid).count() == 0


def test_api_recordlocation_invalid_batt_400(flask_app):
    make_user(flask_app, 'api_ingest_batt', token='secret-batt')
    client = flask_app.test_client()
    resp = client.post('/api/recordlocation',
                       headers={'username': 'api_ingest_batt',
                                'secret': 'secret-batt'},
                       json={'lat': 1.0, 'lon': 2.0, 'batt': 'abc'},
                       base_url=HTTPS)
    assert resp.status_code == 400
    assert b'batt' in resp.data


# --------------------------------------------------------------------------
# B4 / B5 — partial payloads return 400, not 500
# --------------------------------------------------------------------------
def test_update_name_partial_returns_400(flask_app):
    make_user(flask_app, 'name_b4')
    client = login(flask_app, 'name_b4')
    resp = client.post('/account/update_name', json={'lname': 'x'},
                       base_url=HTTPS)
    assert resp.status_code == 400, resp.status_code
    assert b'name' in resp.data.lower()

    ok = client.post('/account/update_name',
                     json={'fname': 'Ada', 'lname': 'Lovelace'}, base_url=HTTPS)
    assert ok.status_code == 201


def test_remove_permission_empty_returns_400(flask_app):
    make_user(flask_app, 'perm_b5')
    client = login(flask_app, 'perm_b5')
    resp = client.post('/account/remove_permission', json={}, base_url=HTTPS)
    assert resp.status_code == 400, resp.status_code
    assert b'username' in resp.data.lower()


def test_update_password_partial_returns_400(flask_app):
    make_user(flask_app, 'pw_b5')
    client = login(flask_app, 'pw_b5')
    resp = client.post('/account/update_password',
                       json={'current_password': 'password123'}, base_url=HTTPS)
    assert resp.status_code == 400, resp.status_code


def test_admin_create_user_missing_fields_returns_400(flask_app):
    make_user(flask_app, 'admin_b5', is_admin=True)
    client = login(flask_app, 'admin_b5')
    resp = client.post('/admin/users/create', json={}, base_url=HTTPS)
    assert resp.status_code == 400, resp.status_code
    assert b'username' in resp.data.lower()


# --------------------------------------------------------------------------
# B6 — dashboard must show the logged-in user's own username
# --------------------------------------------------------------------------
def test_dashboard_shows_own_username_with_incoming_share(flask_app):
    owner = make_user(flask_app, 'share_owner_b6')
    viewer = make_user(flask_app, 'share_viewer_b6')
    add_location(flask_app, owner)
    add_location(flask_app, viewer)
    add_share(flask_app, owner, viewer, 'share_owner_b6', 'share_viewer_b6')

    client = login(flask_app, 'share_viewer_b6')
    resp = client.get('/dashboard', base_url=HTTPS)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "add 'share_viewer_b6'" in html
    assert "add 'share_owner_b6'" not in html


# --------------------------------------------------------------------------
# B10 — the last admin cannot be demoted
# --------------------------------------------------------------------------
def test_last_admin_cannot_be_demoted(flask_app):
    admin_id = make_user(flask_app, 'admin_b10', is_admin=True)
    with flask_app.app_context():
        UserDataModel.query.update({UserDataModel.is_admin: False})
        db.session.commit()
        ud = UserDataModel.query.filter_by(id=admin_id).first()
        ud.is_admin = True
        db.session.commit()

    client = login(flask_app, 'admin_b10')
    resp = client.post('/admin/users/%d/update' % admin_id,
                       json={'is_admin': False}, base_url=HTTPS)
    assert resp.status_code == 400, resp.status_code
    assert b'admin' in resp.data.lower()

    with flask_app.app_context():
        assert UserDataModel.query.filter_by(id=admin_id).first().is_admin is True


def test_second_admin_can_be_demoted(flask_app):
    first = make_user(flask_app, 'admin_two_a', is_admin=True)
    second = make_user(flask_app, 'admin_two_b', is_admin=True)
    with flask_app.app_context():
        UserDataModel.query.update({UserDataModel.is_admin: False})
        db.session.commit()
        for uid in (first, second):
            ud = UserDataModel.query.filter_by(id=uid).first()
            ud.is_admin = True
        db.session.commit()

    client = login(flask_app, 'admin_two_a')
    resp = client.post('/admin/users/%d/update' % second,
                       json={'is_admin': False}, base_url=HTTPS)
    assert resp.status_code == 200


# --------------------------------------------------------------------------
# S7 — 429 carries Retry-After and an actionable body
# --------------------------------------------------------------------------
def test_login_throttle_returns_retry_after_and_body(flask_app):
    make_user(flask_app, 'throttle_user')
    client = flask_app.test_client()
    for _ in range(5):
        r = client.post('/login', data={'username': 'throttle_user',
                                        'password': 'wrong'}, base_url=HTTPS)
        assert r.status_code == 200
    r = client.post('/login', data={'username': 'throttle_user',
                                    'password': 'wrong'}, base_url=HTTPS)
    assert r.status_code == 429
    assert r.headers.get('Retry-After') is not None
    assert 0 < int(r.headers['Retry-After']) <= 300
    assert r.get_data(as_text=True).strip()


def test_api_throttle_returns_retry_after_and_json(flask_app):
    client = flask_app.test_client()
    for _ in range(5):
        client.post('/api/recordlocation',
                    headers={'username': 'nobody', 'secret': 'x'},
                    json={'lat': 1.0, 'lon': 2.0}, base_url=HTTPS)
    r = client.post('/api/recordlocation',
                    headers={'username': 'nobody', 'secret': 'x'},
                    json={'lat': 1.0, 'lon': 2.0}, base_url=HTTPS)
    assert r.status_code == 429
    assert r.headers.get('Retry-After') is not None
    assert r.get_json().get('error')


# --------------------------------------------------------------------------
# S8 — oversized bodies are rejected with 413
# --------------------------------------------------------------------------
def test_oversized_body_returns_413(flask_app):
    client = flask_app.test_client()
    # Comfortably above the configured cap (64 KiB) and independent of it, so
    # removing the cap makes this test fail rather than error.
    big = 'x' * (256 * 1024)
    resp = client.post('/login', data={'username': big, 'password': 'y'},
                       base_url=HTTPS)
    assert resp.status_code == 413


# --------------------------------------------------------------------------
# S6 — minimum password length
# --------------------------------------------------------------------------
def test_registration_rejects_short_password(flask_app):
    client = flask_app.test_client()
    resp = client.post('/register',
                       data={'username': 'shortpw_user', 'password': 'short'},
                       base_url=HTTPS)
    assert resp.status_code == 400, resp.status_code
    assert b'at least' in resp.data.lower()

    with flask_app.app_context():
        assert UserModel.query.filter_by(username='shortpw_user').first() is None

    ok = client.post('/register',
                     data={'username': 'validpw_user',
                           'password': 'longenough1'}, base_url=HTTPS)
    assert ok.status_code in (301, 302, 303), ok.status_code


def test_password_change_rejects_short_password(flask_app):
    make_user(flask_app, 'pwchange_user', password='password123')
    client = login(flask_app, 'pwchange_user', password='password123')

    resp = client.post('/account/update_password',
                       json={'current_password': 'password123',
                             'new_password': 'short'}, base_url=HTTPS)
    assert resp.status_code == 400, resp.status_code
    assert b'at least' in resp.data.lower()

    ok = client.post('/account/update_password',
                     json={'current_password': 'password123',
                           'new_password': 'newpassword123'}, base_url=HTTPS)
    assert ok.status_code == 201

    # The new password must actually work.
    fresh = flask_app.test_client()
    relogin = fresh.post('/login', data={'username': 'pwchange_user',
                                         'password': 'newpassword123'},
                         base_url=HTTPS)
    assert relogin.status_code in (301, 302, 303), relogin.status_code


def test_admin_create_user_rejects_short_password(flask_app):
    make_user(flask_app, 'admin_s6', is_admin=True)
    client = login(flask_app, 'admin_s6')
    resp = client.post('/admin/users/create',
                       json={'username': 'made_by_admin', 'password': 'short'},
                       base_url=HTTPS)
    assert resp.status_code == 400
    assert b'at least' in resp.data.lower()


def test_short_api_token_still_works(flask_app):
    """The minimum length must not be applied to the Android API secret."""
    uid = make_user(flask_app, 'short_token_user', token='abc')
    client = flask_app.test_client()
    resp = client.post('/api/recordlocation',
                       headers={'username': 'short_token_user', 'secret': 'abc'},
                       json={'lat': 1.0, 'lon': 2.0}, base_url=HTTPS)
    assert resp.status_code == 201, resp.status_code
    with flask_app.app_context():
        assert LocationsModel.query.filter_by(userid=uid).count() == 1


def test_existing_short_password_can_still_log_in(flask_app):
    """The minimum is not applied retroactively to stored passwords."""
    make_user(flask_app, 'legacy_short_pw', password='abc')
    client = login(flask_app, 'legacy_short_pw', password='abc')
    assert client.get('/dashboard', base_url=HTTPS).status_code == 200


# --------------------------------------------------------------------------
# B8 / B12 — advertised controls are wired up
# --------------------------------------------------------------------------
def test_account_update_api_key_button_submits(flask_app):
    make_user(flask_app, 'button_user')
    client = login(flask_app, 'button_user')
    html = client.get('/account', base_url=HTTPS).get_data(as_text=True)
    assert ('<button class="button" type="submit" value="Submit">'
            'Update API Key</button>') in html


def test_admin_page_has_add_user_button(flask_app):
    make_user(flask_app, 'admin_ui', is_admin=True)
    client = login(flask_app, 'admin_ui')
    html = client.get('/admin', base_url=HTTPS).get_data(as_text=True)
    assert 'openAddModal()' in html


# --------------------------------------------------------------------------
# Criterion 14 — happy path still works end to end
# --------------------------------------------------------------------------
def test_happy_path_core_flows(flask_app):
    make_user(flask_app, 'happy_user', token='happy-secret')
    client = login(flask_app, 'happy_user')

    # browser location ingest
    r = client.post('/recordlocation',
                    json={'lat': 41.0, 'lon': -71.0, 'acc': 5, 'batt': 88},
                    base_url=HTTPS)
    assert r.status_code == 201, r.status_code

    # API location ingest with token
    r = client.post('/api/recordlocation',
                    headers={'username': 'happy_user',
                             'secret': 'happy-secret'},
                    json={'lat': 41.5, 'lon': -71.5}, base_url=HTTPS)
    assert r.status_code == 201, r.status_code

    # read APIs
    r = client.get('/api/locations', base_url=HTTPS)
    assert r.status_code == 200 and r.get_json()['locations']

    r = client.get('/api/presence?lat=41&lon=-71&radius=5000', base_url=HTTPS)
    assert r.status_code == 200
    assert r.get_json()['days']

    # dashboard
    r = client.get('/dashboard', base_url=HTTPS)
    assert r.status_code == 200

    # places CRUD
    r = client.post('/api/places', json={
        'name': 'Work', 'lat': 41.0, 'lon': -71.0, 'radius': 50,
        'webhook_url': 'https://example.test/h'}, base_url=HTTPS)
    assert r.status_code == 201

    r = client.get('/api/places', base_url=HTTPS)
    places = r.get_json()['places']
    assert len(places) == 1
    pid = places[0]['id']

    r = client.put('/api/places', json={'id': pid, 'name': 'Work2'},
                   base_url=HTTPS)
    assert r.status_code == 200

    r = client.delete('/api/places', json={'id': pid}, base_url=HTTPS)
    assert r.status_code == 200
    r = client.get('/api/places', base_url=HTTPS)
    assert r.get_json()['places'] == []


def test_login_and_dashboard_happy_path(flask_app):
    make_user(flask_app, 'plain_user')
    client = login(flask_app, 'plain_user')
    assert client.get('/dashboard', base_url=HTTPS).status_code == 200
