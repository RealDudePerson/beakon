"""Tests for the persistent admin-controlled application log level."""

import logging

import pytest

import app as beakon_app
from models import AppSettingModel, UserModel, UserDataModel, db

HTTPS = 'https://localhost'
_MISSING = object()


class RecordingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture(autouse=True)
def restore_logging_state(flask_app):
    """Keep database and process-wide logger changes isolated to each test."""
    def clear_setting():
        with flask_app.app_context():
            row = db.session.get(AppSettingModel, beakon_app.LOG_LEVEL_SETTING_KEY)
            if row is not None:
                db.session.delete(row)
                db.session.commit()

    clear_setting()
    original_level = flask_app.logger.level
    original_handlers = list(flask_app.logger.handlers)
    original_handler_levels = [(handler, handler.level) for handler in original_handlers]
    original_config = flask_app.config.get('LOG_LEVEL', _MISSING)
    yield
    clear_setting()
    flask_app.logger.setLevel(original_level)
    for handler, level in original_handler_levels:
        handler.setLevel(level)
    for handler in list(flask_app.logger.handlers):
        if handler not in original_handlers:
            flask_app.logger.removeHandler(handler)
            handler.close()
    if original_config is _MISSING:
        flask_app.config.pop('LOG_LEVEL', None)
    else:
        flask_app.config['LOG_LEVEL'] = original_config


def make_user(flask_app, username, is_admin):
    with flask_app.app_context():
        user = UserModel.query.filter_by(username=username).first()
        if user is None:
            user = UserModel(username=username)
            user.set_password('password123')
            db.session.add(user)
            db.session.commit()
            db.session.add(UserDataModel(id=user.id, is_admin=is_admin))
            db.session.commit()
        return user.id


def login(flask_app, username):
    client = flask_app.test_client()
    response = client.post('/login', data={'username': username,
                                          'password': 'password123'}, base_url=HTTPS)
    assert response.status_code in (301, 302, 303), response.data
    return client


@pytest.fixture
def admin_client(flask_app):
    make_user(flask_app, 'logging_admin', is_admin=True)
    return login(flask_app, 'logging_admin')


@pytest.mark.parametrize('raw, expected', [
    ('debug', 'DEBUG'),
    (' Info ', 'INFO'),
    ('WARNING', 'WARNING'),
    ('error', 'ERROR'),
    ('critical', 'CRITICAL'),
    ('TRACE', 'DEBUG'),
    (None, 'DEBUG'),
    (20, 'DEBUG'),
])
def test_normalise_log_level(raw, expected):
    assert beakon_app.normalise_log_level(raw) == expected


def test_config_default_and_database_override_precedence(flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, 'LOG_LEVEL', 'INFO')
    with flask_app.app_context():
        assert beakon_app.current_log_level() == 'INFO'
        db.session.add(AppSettingModel(key=beakon_app.LOG_LEVEL_SETTING_KEY,
                                       value='ERROR'))
        db.session.commit()
        assert beakon_app.current_log_level() == 'ERROR'
        # Simulate a restart by reapplying the effective persisted value.
        assert beakon_app.apply_log_level(beakon_app.current_log_level()) == 'ERROR'
        assert flask_app.logger.level == logging.ERROR


def test_invalid_config_and_stored_values_fall_back_safely(flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, 'LOG_LEVEL', 'TRACE')
    with flask_app.app_context():
        assert beakon_app.configured_log_level() == 'DEBUG'
        db.session.add(AppSettingModel(key=beakon_app.LOG_LEVEL_SETTING_KEY,
                                       value='VERBOSE'))
        db.session.commit()
        assert beakon_app.current_log_level() == 'DEBUG'


def test_apply_log_level_updates_logger_and_handlers(flask_app):
    handler = logging.NullHandler()
    flask_app.logger.addHandler(handler)
    try:
        assert beakon_app.apply_log_level(' warning ') == 'WARNING'
        assert flask_app.logger.level == logging.WARNING
        assert handler.level == logging.WARNING
    finally:
        flask_app.logger.removeHandler(handler)
        handler.close()


def test_admin_can_persist_and_immediately_apply_level(flask_app, admin_client):
    handler = logging.NullHandler()
    flask_app.logger.addHandler(handler)
    try:
        response = admin_client.post('/admin/settings/log-level',
                                     json={'log_level': ' warning'}, base_url=HTTPS)
        assert response.status_code == 200
        assert response.get_json() == {'log_level': 'WARNING'}
        assert flask_app.logger.level == logging.WARNING
        assert handler.level == logging.WARNING
        with flask_app.app_context():
            row = db.session.get(AppSettingModel, beakon_app.LOG_LEVEL_SETTING_KEY)
            assert row.value == 'WARNING'
    finally:
        flask_app.logger.removeHandler(handler)
        handler.close()


@pytest.mark.parametrize('previous, target', [
    ('DEBUG', 'WARNING'),
    ('CRITICAL', 'DEBUG'),
])
def test_level_change_is_audited_even_when_info_is_suppressed(
        flask_app, admin_client, monkeypatch, previous, target):
    monkeypatch.setitem(flask_app.config, 'LOG_LEVEL', previous)
    handler = RecordingHandler()
    flask_app.logger.addHandler(handler)
    try:
        beakon_app.apply_log_level(previous)
        response = admin_client.post('/admin/settings/log-level',
                                     json={'log_level': target}, base_url=HTTPS)
        assert response.status_code == 200
        audits = [record for record in handler.records
                  if 'changed the log level' in record.getMessage()]
        assert len(audits) == 1
        assert audits[0].levelno == logging.INFO
        assert ('logging_admin changed the log level from %s to %s' %
                (previous, target)) == audits[0].getMessage()
    finally:
        flask_app.logger.removeHandler(handler)
        handler.close()


def test_new_level_filters_subsequent_records(flask_app, admin_client, caplog):
    caplog.set_level(logging.DEBUG, logger='app')
    response = admin_client.post('/admin/settings/log-level',
                                 json={'log_level': 'WARNING'}, base_url=HTTPS)
    assert response.status_code == 200

    caplog.clear()
    flask_app.logger.info('suppressed info message')
    flask_app.logger.warning('visible warning message')
    assert 'suppressed info message' not in caplog.text
    assert 'visible warning message' in caplog.text


@pytest.mark.parametrize('payload', [
    {},
    {'log_level': 'TRACE'},
    {'log_level': None},
    {'log_level': 20},
    [],
])
def test_invalid_level_is_rejected_without_changing_setting(flask_app, admin_client, payload):
    original_level = flask_app.logger.level
    response = admin_client.post('/admin/settings/log-level', json=payload, base_url=HTTPS)

    assert response.status_code == 400
    assert response.get_json()['error']
    assert flask_app.logger.level == original_level
    with flask_app.app_context():
        assert db.session.get(AppSettingModel, beakon_app.LOG_LEVEL_SETTING_KEY) is None


def test_non_admin_cannot_change_log_level(flask_app):
    make_user(flask_app, 'logging_plain', is_admin=False)
    client = login(flask_app, 'logging_plain')
    response = client.post('/admin/settings/log-level', json={'log_level': 'DEBUG'},
                           base_url=HTTPS)
    assert response.status_code == 403


def test_anonymous_user_cannot_change_log_level(flask_app):
    response = flask_app.test_client().post('/admin/settings/log-level',
                                            json={'log_level': 'DEBUG'}, base_url=HTTPS)
    assert response.status_code in (301, 302, 303)
    assert '/login' in response.headers['Location']


def test_dashboard_exposes_effective_level_and_script_wiring(flask_app, admin_client):
    with flask_app.app_context():
        db.session.add(AppSettingModel(key=beakon_app.LOG_LEVEL_SETTING_KEY,
                                       value='ERROR'))
        db.session.commit()
        beakon_app.apply_log_level('ERROR')

    response = admin_client.get('/admin', base_url=HTTPS)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="log-level-select"' in html
    assert '<option value="ERROR" selected>ERROR</option>' in html
    assert 'id="save-log-level-btn"' in html

    with open('static/js/admin.js') as script:
        source = script.read()
    assert "fetch('/admin/settings/log-level'" in source
    assert "saveLogLevelBtn.addEventListener('click', saveLogLevel)" in source
