"""Tests for the admin registration on/off toggle.

Registration can be turned on or off at runtime from the admin dashboard. The
stored setting wins when present; otherwise the app falls back to the
``REGISTRATION_ENABLED`` config flag, which stays fail-closed.
"""

import pytest

from app import REGISTRATION_SETTING_KEY, registration_enabled
from models import (UserModel, UserDataModel, AppSettingModel,
                    get_app_setting, db)

HTTPS = 'https://localhost'


def make_admin(flask_app, username='reg_admin'):
    with flask_app.app_context():
        user = UserModel.query.filter_by(username=username).first()
        if user is None:
            user = UserModel(username=username)
            user.set_password('password123')
            db.session.add(user)
            db.session.commit()
            db.session.add(UserDataModel(id=user.id, is_admin=True))
            db.session.commit()
        return user.id


def login(flask_app, username, password='password123'):
    client = flask_app.test_client()
    resp = client.post('/login', data={'username': username,
                                       'password': password}, base_url=HTTPS)
    assert resp.status_code in (301, 302, 303), (resp.status_code, resp.data)
    return client


@pytest.fixture(autouse=True)
def _clear_registration_setting(flask_app):
    """Keep the stored override from leaking into other tests."""
    def clear():
        with flask_app.app_context():
            row = db.session.get(AppSettingModel, REGISTRATION_SETTING_KEY)
            if row is not None:
                db.session.delete(row)
                db.session.commit()
    clear()
    yield
    clear()


def test_admin_can_disable_then_enable_registration(flask_app):
    make_admin(flask_app)
    client = login(flask_app, 'reg_admin')
    anonymous = flask_app.test_client()

    # Enable first, so the test does not depend on the config default.
    resp = client.post('/admin/settings/registration',
                       json={'enabled': True}, base_url=HTTPS)
    assert resp.status_code == 200
    assert resp.get_json()['registration_enabled'] is True
    assert b'<form' in anonymous.get('/register', base_url=HTTPS).data.lower()

    resp = client.post('/admin/settings/registration',
                       json={'enabled': False}, base_url=HTTPS)
    assert resp.status_code == 200
    assert resp.get_json()['registration_enabled'] is False
    closed = anonymous.get('/register', base_url=HTTPS)
    assert closed.status_code == 200
    assert b'closed' in closed.data.lower()


def test_setting_persists_in_database(flask_app):
    make_admin(flask_app)
    client = login(flask_app, 'reg_admin')
    client.post('/admin/settings/registration',
                json={'enabled': False}, base_url=HTTPS)
    with flask_app.app_context():
        assert get_app_setting(REGISTRATION_SETTING_KEY) == 'false'
        assert registration_enabled() is False


def test_non_admin_cannot_toggle(flask_app):
    with flask_app.app_context():
        user = UserModel(username='reg_plain')
        user.set_password('password123')
        db.session.add(user)
        db.session.commit()
        db.session.add(UserDataModel(id=user.id, is_admin=False))
        db.session.commit()
    client = login(flask_app, 'reg_plain')
    resp = client.post('/admin/settings/registration',
                       json={'enabled': True}, base_url=HTTPS)
    assert resp.status_code == 403


def test_missing_field_is_rejected(flask_app):
    make_admin(flask_app)
    client = login(flask_app, 'reg_admin')
    resp = client.post('/admin/settings/registration',
                       json={}, base_url=HTTPS)
    assert resp.status_code == 400


def test_falls_back_to_config_when_unset(flask_app, monkeypatch):
    make_admin(flask_app)
    with flask_app.app_context():
        monkeypatch.setitem(flask_app.config, 'REGISTRATION_ENABLED', False)
        assert registration_enabled() is False
        monkeypatch.setitem(flask_app.config, 'REGISTRATION_ENABLED', True)
        assert registration_enabled() is True


def test_login_page_hides_register_link_when_disabled(flask_app):
    make_admin(flask_app)
    client = login(flask_app, 'reg_admin')
    anonymous = flask_app.test_client()

    client.post('/admin/settings/registration',
                json={'enabled': False}, base_url=HTTPS)
    assert b'/register' not in anonymous.get('/login', base_url=HTTPS).data

    client.post('/admin/settings/registration',
                json={'enabled': True}, base_url=HTTPS)
    assert b'/register' in anonymous.get('/login', base_url=HTTPS).data
