"""Acceptance tests for the shared ("everyone who shares with you") map.

See REQUIREMENTS-SHARED-MAP.md. Each test maps to an acceptance criterion and
is written so that re-introducing a plausible bug (in particular, showing users
who do not share with the caller) makes it fail. The hermetic config/database
comes from conftest.py; the live instance/beakon.db is never touched.
"""

import operator
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


def read_static(relative_path):
    with open(os.path.join(REPO_ROOT, *relative_path.split('/'))) as f:
        return f.read()


def js_code_only(js):
    """JS with comments removed, so a comment cannot satisfy an assertion."""
    code = re.sub(r'/\*.*?\*/', '', js, flags=re.S)
    return re.sub(r'//[^\n]*', '', code)


# JS comparison operators, so the boundary test evaluates the operator that is
# actually written in the source rather than a re-implementation of it.
_JS_OPERATORS = {
    '>': operator.gt, '<': operator.lt,
    '>=': operator.ge, '<=': operator.le,
    '==': operator.eq, '===': operator.eq,
}


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
# Same format_timestamp() strings in both views (dimming addendum keeps the
# display string; it adds age_seconds alongside it, it does not replace it).
# --------------------------------------------------------------------------
def test_group_view_uses_same_timestamp_strings_as_single_view(flask_app):
    caller = make_user(flask_app, 'shared_caller_ts')
    sharer = make_user(flask_app, 'shared_sharer_ts')
    now = datetime.now()
    add_location(flask_app, sharer, lat=40.0, lon=-70.0, timestamp=now)
    add_share(flask_app, sharer, caller, 'shared_sharer_ts', 'shared_caller_ts')

    client = login(flask_app, 'shared_caller_ts')
    group = client.get('/api/locations/shared', base_url=HTTPS).get_json()
    single = client.get('/api/locations/shared_sharer_ts',
                        base_url=HTTPS).get_json()

    # Identical formatter output in both views: dimming changes styling only.
    assert (group['people'][0]['latest']['timestamp']
            == single['latest']['timestamp'])
    assert (group['people'][0]['trail'][0]['timestamp']
            == single['locations'][0]['timestamp'])


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
# Dimming addendum (REQUIREMENTS-SHARED-MAP.md "Dimming" section).
#
# Group view only: a person whose latest fix is older than STALE_AFTER_SECONDS
# (600) is dimmed and flagged stale in the legend, but is never hidden and the
# API never drops them. The existing per-person view keeps no such rule.
# --------------------------------------------------------------------------
def test_age_seconds_present_and_numerically_correct_for_known_age(flask_app):
    """Criterion 4: age_seconds is present and correct for a known fixture age."""
    caller = make_user(flask_app, 'dim_caller_age')
    sharer = make_user(flask_app, 'dim_sharer_age')
    fixture_age = 187  # 3 minutes 7 seconds
    ts = datetime.now() - timedelta(seconds=fixture_age)
    add_location(flask_app, sharer, timestamp=ts)
    add_share(flask_app, sharer, caller, 'dim_sharer_age', 'dim_caller_age')

    client = login(flask_app, 'dim_caller_age')
    latest = client.get('/api/locations/shared',
                        base_url=HTTPS).get_json()['people'][0]['latest']

    assert 'age_seconds' in latest
    assert isinstance(latest['age_seconds'], int)
    # Server-side now - timestamp; allow a couple of seconds of test drift.
    assert fixture_age - 2 <= latest['age_seconds'] <= fixture_age + 2, latest
    # The display string is kept alongside the numeric age.
    assert latest['timestamp'] == '3 minutes ago'
    assert latest['timestamp'] == format_timestamp(ts)


def test_stale_threshold_boundary_just_under_and_just_over(flask_app):
    """Criterion 3: the JS boundary is tested just under vs just over.

    Red-capable: this evaluates the operator and constant *as written in
    shared_map.js*. Flipping ``>`` to ``<``/``>=``, or changing 600, makes one
    of the three boundary assertions fail. (Verified by temporarily breaking the
    comparison; see the commit report.)
    """
    code = js_code_only(read_static('static/js/shared_map.js'))

    constant = re.search(r'STALE_AFTER_SECONDS\s*=\s*(\d+)', code)
    assert constant, 'STALE_AFTER_SECONDS must be a single named constant'
    threshold = int(constant.group(1))
    assert threshold == 600, 'threshold should be 10 minutes (600s)'

    comparison = re.search(
        r'ageSeconds\s*(>=|<=|===|==|>|<)\s*STALE_AFTER_SECONDS', code)
    assert comparison, 'isStale must compare ageSeconds to the constant'
    decide = _JS_OPERATORS[comparison.group(1)]

    assert decide(threshold - 1, threshold) is False, 'just under: not stale'
    assert decide(threshold, threshold) is False, 'exactly at: not stale'
    assert decide(threshold + 1, threshold) is True, 'just over: stale'


def test_stale_person_is_dimmed_and_flagged_but_never_hidden(flask_app):
    """Criteria 2 and 5: stale => dimmed + legend flag, but still present."""
    caller = make_user(flask_app, 'dim_caller_stale')
    stale_user = make_user(flask_app, 'dim_sharer_stale')
    fresh_user = make_user(flask_app, 'dim_sharer_fresh')
    add_location(flask_app, stale_user,
                 timestamp=datetime.now() - timedelta(minutes=30))
    add_location(flask_app, fresh_user, timestamp=datetime.now())
    add_share(flask_app, stale_user, caller, 'dim_sharer_stale', 'dim_caller_stale')
    add_share(flask_app, fresh_user, caller, 'dim_sharer_fresh', 'dim_caller_stale')

    client = login(flask_app, 'dim_caller_stale')
    body = client.get('/api/locations/shared', base_url=HTTPS).get_json()

    # The stale person is still in the response: dimming hides nothing.
    assert set(usernames_of(body)) == {'dim_sharer_stale', 'dim_sharer_fresh'}
    by_name = {p['username']: p for p in body['people']}
    assert by_name['dim_sharer_stale']['latest']['age_seconds'] > 600
    assert by_name['dim_sharer_fresh']['latest']['age_seconds'] < 600

    # ...and is still listed in the server-rendered legend.
    html = client.get('/map', base_url=HTTPS).get_data(as_text=True)
    assert 'dim_sharer_stale' in html
    assert 'dim_sharer_fresh' in html


def test_js_dims_marker_and_trail_and_flags_legend_only_when_stale(flask_app):
    """Criterion 2: dimming is driven by the stale decision in the JS."""
    code = js_code_only(read_static('static/js/shared_map.js'))

    # Marker and trail opacity are conditional on the stale flag.
    assert re.search(r'fillOpacity:\s*stale\s*\?', code), \
        'marker fillOpacity must be reduced when stale'
    assert re.search(r'opacity:\s*stale\s*\?', code), \
        'trail opacity must be reduced when stale'
    # The legend marks a stale entry and appends a visible stale flag.
    assert re.search(r"shared-legend-entry'\s*\+\s*\(stale\s*\?", code)
    assert 'shared-stale-badge' in code
    assert "'stale'" in code or '"stale"' in code

    # The stale flag must not gate the person out of the legend: the only
    # early-return in the render loop is the user's own show/hide toggle.
    render = code[code.index('function render('):code.index('function renderLegend(')]
    assert 'if (hidden[person.username])' in render
    assert not re.search(r'if\s*\(stale\)\s*\{?\s*return', render)
    legend = code[code.index('function renderLegend('):]
    assert not re.search(r'if\s*\(stale\)\s*\{?\s*(return|continue)', legend)


def test_css_dims_stale_legend_entry(flask_app):
    css = read_static('static/css/beakon.css')
    assert re.search(r'\.shared-legend-entry\.stale\s*\{[^}]*opacity', css)
    assert '.shared-stale-badge' in css


def test_existing_per_person_view_is_unchanged_and_not_dimmed(flask_app):
    """Criterion 6: /map/<username> keeps its behaviour and gains no dimming."""
    caller = make_user(flask_app, 'dim_caller_single')
    owner = make_user(flask_app, 'dim_owner_single')
    add_location(flask_app, owner,
                 timestamp=datetime.now() - timedelta(hours=5))
    add_share(flask_app, owner, caller, 'dim_owner_single', 'dim_caller_single')

    client = login(flask_app, 'dim_caller_single')

    # The per-person API response is byte-compatible with before: no age_seconds.
    body = client.get('/api/locations/dim_owner_single',
                      base_url=HTTPS).get_json()
    assert set(body.keys()) == {'locations', 'latest'}
    assert 'age_seconds' not in body['latest']
    assert set(body['latest'].keys()) == {
        'lat', 'lon', 'timestamp', 'batt', 'ischarging'}
    for loc in body['locations']:
        assert 'age_seconds' not in loc

    # The per-person page still renders, with no dimming logic in its assets.
    assert client.get('/map/dim_owner_single', base_url=HTTPS).status_code == 200
    for path in ('static/js/map.js', 'templates/map.html'):
        code = read_static(path).lower()
        assert 'stale' not in code, path
        assert 'age_seconds' not in code, path


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
