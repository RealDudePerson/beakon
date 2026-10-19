"""Acceptance tests for the shared ("everyone who shares with you") map.

See REQUIREMENTS-SHARED-MAP.md. Each test maps to an acceptance criterion and
is written so that re-introducing a plausible bug (in particular, showing users
who do not share with the caller) makes it fail. The hermetic config/database
comes from conftest.py; the live instance/beakon.db is never touched.
"""

import os
import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app import _attempts, format_timestamp
from models import (UserModel, UserDataModel, LocationsModel,
                    SharingPermissionModel, db)

HTTPS = 'https://localhost'
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _clear_throttle():
    """The throttle is process-global; keep tests independent of each other."""
    _attempts.clear()
    yield
    _attempts.clear()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def make_user(flask_app, username, password='password123', is_admin=False):
    with flask_app.app_context():
        user = UserModel(username=username)
        user.set_password(password)
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


def add_location(flask_app, user_id, lat=40.0, lon=-70.0, batt=50,
                 timestamp=None):
    with flask_app.app_context():
        db.session.add(LocationsModel(
            lat=lat, lon=lon, acc=5.0,
            timestamp=timestamp or datetime.now(), userid=user_id,
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


def count_statements(flask_app, func):
    """Run ``func`` and return the SQL statements it issued."""
    with flask_app.app_context():
        engine = db.engine

    statements = []

    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, 'before_cursor_execute', before)
    try:
        func()
    finally:
        event.remove(engine, 'before_cursor_execute', before)
    return statements


def usernames_of(body):
    return [person['username'] for person in body['people']]


# --------------------------------------------------------------------------
# Acceptance 2 — fail-closed: a non-sharer never appears
# --------------------------------------------------------------------------
def test_fail_closed_non_sharer_never_appears(flask_app):
    """A user with no sharing_permission row is absent even with locations.

    Red-capable: an implementation that merely lists every user with a location
    (or that treats a missing permission as "allow") would include the
    stranger and fail the ``usernames == [sharer]`` assertion.
    """
    caller = make_user(flask_app, 'shared_caller_fc')
    sharer = make_user(flask_app, 'shared_sharer_fc')
    stranger = make_user(flask_app, 'shared_stranger_fc')
    add_location(flask_app, sharer, lat=41.0, lon=-71.0)
    add_location(flask_app, stranger, lat=42.0, lon=-72.0)
    # Only `sharer` granted the caller permission.
    add_share(flask_app, sharer, caller, 'shared_sharer_fc', 'shared_caller_fc')

    client = login(flask_app, 'shared_caller_fc')
    body = client.get('/api/locations/shared', base_url=HTTPS).get_json()

    assert usernames_of(body) == ['shared_sharer_fc']
    assert 'shared_stranger_fc' not in usernames_of(body)


def test_fail_closed_no_incoming_shares_is_empty_not_everything(flask_app):
    """A caller with no rows at all must get ``{"people": []}`` and 200."""
    make_user(flask_app, 'shared_caller_none')
    stranger = make_user(flask_app, 'shared_stranger_none')
    add_location(flask_app, stranger, lat=42.0, lon=-72.0)

    client = login(flask_app, 'shared_caller_none')
    resp = client.get('/api/locations/shared', base_url=HTTPS)
    assert resp.status_code == 200
    assert resp.get_json() == {'people': []}


# --------------------------------------------------------------------------
# Acceptance 3 — positive case
# --------------------------------------------------------------------------
def test_sharer_appears_with_latest_and_non_empty_trail(flask_app):
    caller = make_user(flask_app, 'shared_caller_pos')
    sharer = make_user(flask_app, 'shared_sharer_pos')
    now = datetime.now()
    add_location(flask_app, sharer, lat=40.0, lon=-70.0, timestamp=now)
    # >91 m away, so the existing filter keeps both points.
    add_location(flask_app, sharer, lat=40.01, lon=-70.0, timestamp=now)
    add_share(flask_app, sharer, caller, 'shared_sharer_pos', 'shared_caller_pos')

    client = login(flask_app, 'shared_caller_pos')
    body = client.get('/api/locations/shared', base_url=HTTPS).get_json()

    assert usernames_of(body) == ['shared_sharer_pos']
    person = body['people'][0]
    assert person['display_name'] == 'Test User'
    assert person['latest']['lat'] is not None
    assert person['latest']['lon'] is not None
    assert person['latest']['timestamp'] == format_timestamp(now)
    assert len(person['trail']) == 2


# --------------------------------------------------------------------------
# Acceptance 4 — empty set (already covered above) and Acceptance 5 —
# permitted but no locations is absent
# --------------------------------------------------------------------------
def test_permitted_but_no_locations_is_absent(flask_app):
    caller = make_user(flask_app, 'shared_caller_perm_empty')
    sharer = make_user(flask_app, 'shared_sharer_perm_empty')
    add_share(flask_app, sharer, caller, 'shared_sharer_perm_empty',
              'shared_caller_perm_empty')

    client = login(flask_app, 'shared_caller_perm_empty')
    body = client.get('/api/locations/shared', base_url=HTTPS).get_json()

    assert body == {'people': []}


# --------------------------------------------------------------------------
# Acceptance 6 — no N+1: query count does not scale with the number of sharers
# --------------------------------------------------------------------------
def test_query_count_does_not_scale_with_number_of_sharers(flask_app):
    caller_one = make_user(flask_app, 'shared_qc_one')
    sharer_one = make_user(flask_app, 'shared_qc_sharer_one')
    add_location(flask_app, sharer_one)
    add_share(flask_app, sharer_one, caller_one,
              'shared_qc_sharer_one', 'shared_qc_one')
    client_one = login(flask_app, 'shared_qc_one')

    caller_three = make_user(flask_app, 'shared_qc_three')
    for i in range(3):
        name = 'shared_qc_sharer_three_%d' % i
        uid = make_user(flask_app, name)
        add_location(flask_app, uid)
        add_share(flask_app, uid, caller_three, name, 'shared_qc_three')
    client_three = login(flask_app, 'shared_qc_three')

    def call_one():
        assert client_one.get('/api/locations/shared',
                              base_url=HTTPS).status_code == 200

    def call_three():
        assert client_three.get('/api/locations/shared',
                                base_url=HTTPS).status_code == 200

    one = count_statements(flask_app, call_one)
    three = count_statements(flask_app, call_three)

    assert len(one) == len(three), (
        'query count scaled with sharers: 1 sharer=%d, 3 sharers=%d\n%s\n%s'
        % (len(one), len(three), one, three))
    # Exactly one query fetches locations, regardless of how many people share:
    # the grouped IN (...) query, not one query per person.
    location_queries = [s for s in three if 'FROM locations_table' in s]
    assert len(location_queries) == 1, location_queries


# --------------------------------------------------------------------------
# Acceptance 7 — the existing per-person route is unchanged
# --------------------------------------------------------------------------
def test_existing_per_person_route_shape_unchanged(flask_app):
    caller = make_user(flask_app, 'shared_caller_pp')
    owner = make_user(flask_app, 'shared_owner_pp')
    now = datetime.now()
    add_location(flask_app, owner, lat=40.0, lon=-70.0, timestamp=now)
    add_share(flask_app, owner, caller, 'shared_owner_pp', 'shared_caller_pp')

    client = login(flask_app, 'shared_caller_pp')
    resp = client.get('/api/locations/shared_owner_pp', base_url=HTTPS)
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body.keys()) == {'locations', 'latest'}
    assert body['latest']['timestamp'] == format_timestamp(now)
    assert body['locations'][0]['timestamp'] == format_timestamp(now)
    assert set(body['locations'][0].keys()) == {
        'lat', 'lon', 'timestamp', 'batt', 'ischarging'}
    # The per-person page itself still renders.
    assert client.get('/map/shared_owner_pp', base_url=HTTPS).status_code == 200


# --------------------------------------------------------------------------
# Acceptance 8 — no staleness logic; same format_timestamp() strings
# --------------------------------------------------------------------------
def test_group_view_uses_same_timestamp_strings_and_no_staleness(flask_app):
    caller = make_user(flask_app, 'shared_caller_ts')
    sharer = make_user(flask_app, 'shared_sharer_ts')
    now = datetime.now()
    add_location(flask_app, sharer, lat=40.0, lon=-70.0, timestamp=now)
    add_share(flask_app, sharer, caller, 'shared_sharer_ts', 'shared_caller_ts')

    client = login(flask_app, 'shared_caller_ts')
    group = client.get('/api/locations/shared', base_url=HTTPS).get_json()
    single = client.get('/api/locations/shared_sharer_ts',
                        base_url=HTTPS).get_json()

    # Identical formatter output in both views: the group view invents no rule.
    assert (group['people'][0]['latest']['timestamp']
            == single['latest']['timestamp'])
    assert (group['people'][0]['trail'][0]['timestamp']
            == single['locations'][0]['timestamp'])

    with open(os.path.join(REPO_ROOT, 'static', 'js', 'shared_map.js')) as f:
        js = f.read()
    # Scan the executable code only: a comment may *document* the absence of a
    # staleness rule, but no such rule may be present.
    code = re.sub(r'/\*.*?\*/', '', js, flags=re.S)
    code = re.sub(r'//[^\n]*', '', code).lower()
    assert 'stale' not in code
    assert 'dim' not in code


def test_old_location_is_shown_as_timestamp_text_not_hidden(flask_app):
    """Correction 1: an old fix is still rendered, by its timestamp text."""
    caller = make_user(flask_app, 'shared_caller_old')
    sharer = make_user(flask_app, 'shared_sharer_old')
    old = datetime.now() - timedelta(hours=5)
    add_location(flask_app, sharer, lat=40.0, lon=-70.0, timestamp=old)
    add_share(flask_app, sharer, caller, 'shared_sharer_old', 'shared_caller_old')

    client = login(flask_app, 'shared_caller_old')
    body = client.get('/api/locations/shared', base_url=HTTPS).get_json()

    assert usernames_of(body) == ['shared_sharer_old']
    assert body['people'][0]['latest']['timestamp'] == '5 hours ago'
    assert body['people'][0]['latest']['timestamp'] == format_timestamp(old)


# --------------------------------------------------------------------------
# Acceptance 9 — the page renders and the legend lists exactly the permitted
# --------------------------------------------------------------------------
def test_shared_map_page_renders_legend_with_exactly_permitted_people(flask_app):
    caller = make_user(flask_app, 'shared_caller_page')
    allowed = make_user(flask_app, 'shared_allowed_page')
    denied = make_user(flask_app, 'shared_denied_page')
    add_location(flask_app, allowed)
    add_location(flask_app, denied)
    add_share(flask_app, allowed, caller,
              'shared_allowed_page', 'shared_caller_page')

    client = login(flask_app, 'shared_caller_page')
    resp = client.get('/map', base_url=HTTPS)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'shared_allowed_page' in html
    assert 'shared_denied_page' not in html
    # Legend entries link to the existing per-person view.
    assert '/map/shared_allowed_page' in html


def test_shared_map_page_empty_state_when_nobody_shares(flask_app):
    make_user(flask_app, 'shared_caller_page_empty')
    client = login(flask_app, 'shared_caller_page_empty')
    resp = client.get('/map', base_url=HTTPS)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # The legend is empty and the explicit empty-state message is present.
    assert 'shared-empty' in html
    assert 'When someone shares their location with you' in html


# --------------------------------------------------------------------------
# Revocation — a person dropped between polls disappears
# --------------------------------------------------------------------------
def test_revoked_sharer_disappears_on_next_poll(flask_app):
    caller = make_user(flask_app, 'shared_caller_rev')
    sharer = make_user(flask_app, 'shared_sharer_rev')
    add_location(flask_app, sharer)
    add_share(flask_app, sharer, caller, 'shared_sharer_rev', 'shared_caller_rev')

    client = login(flask_app, 'shared_caller_rev')
    first = client.get('/api/locations/shared', base_url=HTTPS).get_json()
    assert usernames_of(first) == ['shared_sharer_rev']

    with flask_app.app_context():
        SharingPermissionModel.query.filter_by(
            shared_with_id=caller, data_owner_id=sharer
        ).delete(synchronize_session=False)
        db.session.commit()

    second = client.get('/api/locations/shared', base_url=HTTPS).get_json()
    assert second == {'people': []}
