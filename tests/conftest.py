"""Shared fixtures for the Beakon test suite.

The app reads its configuration — and therefore its database URI — at import
time, and importing it also creates the database schema. To keep the suite
hermetic we point ``BEAKON_CONFIG`` at a throwaway config file backed by a
temporary SQLite database before importing the app, so the real
``config.cfg``, ``instance/beakon.db`` and the network are never touched.
"""

import os
import sys
import tempfile
from datetime import datetime

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

_TMP_DIR = tempfile.mkdtemp(prefix='beakon-tests-')
_TMP_CONFIG = os.path.join(_TMP_DIR, 'config.cfg')
with open(_TMP_CONFIG, 'w') as _f:
    _f.write(
        "DEBUG = False\n"
        "SQLALCHEMY_DATABASE_URI = 'sqlite:///%s'\n" % os.path.join(_TMP_DIR, 'test.db')
        + "SQLALCHEMY_TRACK_MODIFICATIONS = False\n"
        "MAPBOX_API_KEY = ''\n"
        "REGISTRATION_ENABLED = True\n"
        "SECRET_KEY = 'test-secret-key'\n"
        "VERSION = 'test'\n"
        "LOCATION_REFRESH_INTERVAL = 10\n"
        "GEOFENCE_CHECK_INTERVAL = 5\n"
    )
os.environ['BEAKON_CONFIG'] = _TMP_CONFIG

import app as beakon_app  # noqa: E402

# Keep the suite from appending to the real instance/logs/beakon.log.
from logging.handlers import RotatingFileHandler  # noqa: E402

for _handler in list(beakon_app.app.logger.handlers):
    if isinstance(_handler, RotatingFileHandler):
        beakon_app.app.logger.removeHandler(_handler)
        _handler.close()

# The app forces HTTPS and marks its session cookie Secure, so the test client
# must speak HTTPS for a login session to round-trip.
HTTPS = 'https://localhost'


@pytest.fixture(scope='session')
def flask_app():
    return beakon_app.app


@pytest.fixture(scope='session')
def auth_client(flask_app):
    """A test client logged in as a throwaway user with one location."""
    from models import UserModel, UserDataModel, LocationsModel, db

    with flask_app.app_context():
        user = UserModel.query.filter_by(username='tester').first()
        if user is None:
            user = UserModel(username='tester')
            user.set_password('password')
            db.session.add(user)
            db.session.commit()
            db.session.add(UserDataModel(id=user.id, fname='Test', lname='User'))
            db.session.add(LocationsModel(
                lat=40.0, lon=-70.0, acc=5.0,
                timestamp=datetime.now(), userid=user.id,
                batt=50, ischarging=False))
            db.session.commit()

    client = flask_app.test_client()
    resp = client.post('/login',
                       data={'username': 'tester', 'password': 'password'},
                       base_url=HTTPS)
    assert resp.status_code in (301, 302, 303), resp.status_code
    return client
