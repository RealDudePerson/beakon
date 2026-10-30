"""Acceptance tests for storing timestamps as Unix epoch (REQUIREMENTS-UNIX-TIMESTAMPS.md).

Two contracts are covered here:

1. the tolerant reader (``to_epoch``/``format_timestamp``) works against both an
   unmigrated database (text timestamps) and a migrated one (integers), so the
   code and the data migration can land in either order; and
2. the API's ``timestamp`` display strings are unchanged while the additive
   ``timestamp_unix`` fields are added.

The hermetic config/database comes from conftest.py; the live
``instance/beakon.db`` is never touched.
"""

import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

import app as beakon_app
from app import _attempts, format_timestamp
from models import (UserModel, UserDataModel, LocationsModel,
                    SharingPermissionModel, db, to_epoch, now_epoch, host_zone)

HTTPS = 'https://localhost'


@pytest.fixture(autouse=True)
def _clear_throttle():
    """The throttle is process-global; keep tests independent of each other."""
    _attempts.clear()
    yield
    _attempts.clear()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def make_user(flask_app, username, password='password123'):
    with flask_app.app_context():
        user = UserModel(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        db.session.add(UserDataModel(id=user.id, fname='Test', lname='User'))
        db.session.commit()
        return user.id


def login(flask_app, username, password='password123'):
    client = flask_app.test_client()
    resp = client.post('/login', data={'username': username,
                                       'password': password}, base_url=HTTPS)
    assert resp.status_code in (301, 302, 303), (resp.status_code, resp.data)
    return client


def add_location(flask_app, user_id, lat=40.0, lon=-70.0, timestamp=None,
                 batt=50):
    with flask_app.app_context():
        db.session.add(LocationsModel(
            lat=lat, lon=lon, acc=5.0,
            timestamp=to_epoch(timestamp) if timestamp is not None else now_epoch(),
            userid=user_id, batt=batt, ischarging=False))
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
# Criterion 4 / section 2 — to_epoch() on int/datetime/text/None
# --------------------------------------------------------------------------
def test_to_epoch_int_and_float_are_returned_unchanged():
    assert to_epoch(1792806460) == 1792806460
    assert to_epoch(1792806460.278027) == 1792806460.278027


def test_to_epoch_none_is_none():
    assert to_epoch(None) is None


def test_to_epoch_naive_datetime_is_local():
    dt = datetime(2021, 6, 1, 12, 0, 0)
    assert to_epoch(dt) == dt.replace(tzinfo=host_zone()).timestamp()


def test_to_epoch_text_is_parsed_then_treated_as_local():
    dt = datetime(2021, 6, 1, 12, 0, 0)
    assert to_epoch('2021-06-01 12:00:00') == dt.replace(tzinfo=host_zone()).timestamp()
    # Microseconds survive, which is what keeps the display string identical.
    precise = datetime(2021, 6, 1, 12, 0, 0, 278027)
    assert to_epoch('2021-06-01 12:00:00.278027') == precise.replace(
        tzinfo=host_zone()).timestamp()


def test_to_epoch_handles_a_text_database_and_an_int_database():
    """The same instant read from an unmigrated and a migrated row agrees."""
    dt = datetime(2021, 6, 1, 12, 0, 0)
    epoch = to_epoch(dt)
    # Unmigrated row is text; migrated row is the integer epoch.
    assert int(to_epoch(dt.strftime('%Y-%m-%d %H:%M:%S.%f'))) == int(epoch)
    assert int(to_epoch(int(epoch))) == int(epoch)


# --------------------------------------------------------------------------
# Criterion 1 / section 3 — format_timestamp() strings are identical
# --------------------------------------------------------------------------
def test_format_timestamp_old_branch_is_byte_identical_before_and_after():
    dt = datetime(2020, 1, 2, 3, 4, 5, 678901)
    epoch = to_epoch(dt)
    assert format_timestamp(dt) == str(dt)
    assert format_timestamp(epoch) == str(dt)
    assert format_timestamp('2020-01-02 03:04:05.678901') == str(dt)


def test_format_timestamp_time_only_is_byte_identical_before_and_after():
    dt = datetime(2021, 6, 1, 23, 59, 30, 123456)
    epoch = to_epoch(dt)
    assert format_timestamp(dt, time_only=True) == '23:59:30'
    assert format_timestamp(epoch, time_only=True) == '23:59:30'
    assert format_timestamp('2021-06-01 23:59:30.123456', time_only=True) == '23:59:30'


def test_format_timestamp_relative_branches_are_unchanged():
    now = datetime.now()
    five_minutes = now - timedelta(minutes=5)
    two_hours = now - timedelta(hours=2)
    assert format_timestamp(five_minutes) == format_timestamp(to_epoch(five_minutes))
    assert format_timestamp(five_minutes) == '5 minutes ago'
    assert format_timestamp(two_hours) == format_timestamp(to_epoch(two_hours))
    assert format_timestamp(two_hours) == '2 hours ago'


# --------------------------------------------------------------------------
# Section 3 — day-range filter still selects *local* days
# --------------------------------------------------------------------------
def test_day_range_filter_selects_local_days(flask_app):
    uid = make_user(flask_app, 'unix_day_range')
    before_midnight = datetime(2021, 6, 1, 23, 59, 30)
    after_midnight = datetime(2021, 6, 2, 0, 0, 30)
    add_location(flask_app, uid, lat=1.0, lon=1.0, timestamp=before_midnight)
    add_location(flask_app, uid, lat=2.0, lon=2.0, timestamp=after_midnight)

    with flask_app.app_context():
        day_one = beakon_app.get_locations_for_date(uid, '2021-06-01')
        day_two = beakon_app.get_locations_for_date(uid, '2021-06-02')

    assert [loc.get_lat() for loc in day_one] == [1.0]
    assert [loc.get_lat() for loc in day_two] == [2.0]


# --------------------------------------------------------------------------
# Section 3 — /api/presence buckets by local day, first/last stay strings
# --------------------------------------------------------------------------
def test_presence_buckets_by_local_day_and_adds_unix(flask_app):
    uid = make_user(flask_app, 'unix_presence')
    before_midnight = datetime(2021, 6, 1, 23, 59, 30)
    after_midnight = datetime(2021, 6, 2, 0, 0, 30)
    add_location(flask_app, uid, lat=10.0, lon=10.0, timestamp=before_midnight)
    add_location(flask_app, uid, lat=10.0, lon=10.0, timestamp=after_midnight)

    client = login(flask_app, 'unix_presence')
    body = client.get('/api/presence?lat=10&lon=10&radius=5000',
                      base_url=HTTPS).get_json()
    by_date = {day['date']: day for day in body['days']}
    assert set(by_date) == {'2021-06-01', '2021-06-02'}

    first = by_date['2021-06-01']
    assert first['first'] == before_midnight.strftime('%Y-%m-%d %H:%M:%S')
    assert first['last'] == before_midnight.strftime('%Y-%m-%d %H:%M:%S')
    assert first['first_unix'] == int(to_epoch(before_midnight))
    assert first['last_unix'] == int(to_epoch(before_midnight))

    second = by_date['2021-06-02']
    assert second['first'] == after_midnight.strftime('%Y-%m-%d %H:%M:%S')
    assert second['first_unix'] == int(to_epoch(after_midnight))


# --------------------------------------------------------------------------
# Section 4 — additive API fields; existing fields keep their format
# --------------------------------------------------------------------------
def test_api_locations_adds_timestamp_unix_and_keeps_timestamp(flask_app):
    uid = make_user(flask_app, 'unix_api_own')
    ts = datetime(2021, 6, 1, 12, 0, 0, 500000)
    add_location(flask_app, uid, lat=41.0, lon=-71.0, timestamp=ts)

    client = login(flask_app, 'unix_api_own')
    body = client.get('/api/locations', base_url=HTTPS).get_json()
    loc = body['locations'][0]
    assert loc['timestamp'] == format_timestamp(ts)
    assert loc['timestamp_unix'] == int(to_epoch(ts))
    assert body['latest']['timestamp'] == format_timestamp(ts)
    assert body['latest']['timestamp_unix'] == int(to_epoch(ts))
    # No field was lost.
    assert set(loc) >= {'lat', 'lon', 'timestamp', 'timestamp_unix',
                        'batt', 'ischarging'}


def test_api_user_locations_adds_timestamp_unix(flask_app):
    caller = make_user(flask_app, 'unix_api_caller')
    owner = make_user(flask_app, 'unix_api_owner')
    ts = datetime(2021, 6, 1, 12, 0, 0)
    add_location(flask_app, owner, timestamp=ts)
    add_share(flask_app, owner, caller, 'unix_api_owner', 'unix_api_caller')

    client = login(flask_app, 'unix_api_caller')
    body = client.get('/api/locations/unix_api_owner', base_url=HTTPS).get_json()
    assert body['latest']['timestamp'] == format_timestamp(ts)
    assert body['latest']['timestamp_unix'] == int(to_epoch(ts))
    assert body['locations'][0]['timestamp_unix'] == int(to_epoch(ts))


def test_shared_endpoint_adds_timestamp_unix_and_age_from_epoch(flask_app):
    caller = make_user(flask_app, 'unix_shared_caller')
    sharer = make_user(flask_app, 'unix_shared_sharer')
    ts = datetime.now() - timedelta(seconds=187)
    add_location(flask_app, sharer, timestamp=ts)
    add_share(flask_app, sharer, caller, 'unix_shared_sharer',
              'unix_shared_caller')

    client = login(flask_app, 'unix_shared_caller')
    person = client.get('/api/locations/shared',
                        base_url=HTTPS).get_json()['people'][0]

    assert person['latest']['timestamp'] == format_timestamp(ts)
    assert person['latest']['timestamp_unix'] == int(to_epoch(ts))
    assert person['trail'][0]['timestamp_unix'] == int(to_epoch(ts))
    # age_seconds is computed from epoch; allow a second of test drift.
    expected_age = int(now_epoch() - to_epoch(ts))
    assert expected_age - 2 <= person['latest']['age_seconds'] <= expected_age + 2


# --------------------------------------------------------------------------
# Criterion 4 — the reader tolerates an unmigrated (text) database
# --------------------------------------------------------------------------
def test_unmigrated_text_row_is_read_tolerantly(flask_app):
    uid = make_user(flask_app, 'unix_text_row')
    # Write text directly, bypassing the model, to simulate a row that predates
    # the migration (SQLite happily stores it in the INTEGER-affinity column).
    with flask_app.app_context():
        db.session.execute(text(
            "INSERT INTO locations_table "
            "(lat, lon, acc, timestamp, userid, batt, ischarging) "
            "VALUES (:lat, :lon, :acc, :ts, :uid, :batt, :ch)"),
            {'lat': 40.0, 'lon': -70.0, 'acc': 5.0,
             'ts': '2021-06-01 12:34:56.789012', 'uid': uid,
             'batt': 50, 'ch': 0})
        db.session.commit()

    client = login(flask_app, 'unix_text_row')
    loc = client.get('/api/locations', base_url=HTTPS).get_json()['locations'][0]
    expected = datetime(2021, 6, 1, 12, 34, 56, 789012)
    assert loc['timestamp'] == str(expected)
    assert loc['timestamp_unix'] == int(to_epoch(expected))
