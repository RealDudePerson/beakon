"""Tests for the admin-only filterable application log viewer."""

import pytest

import app as beakon_app
from models import UserModel, UserDataModel, db

HTTPS = 'https://localhost'


def log_line(timestamp, level, message):
    return ('%s [12345:0xabc] app %-5s %s '
            '[in worker @ /srv/beakon/src/worker.py:42]' %
            (timestamp, level, message))


SAMPLE_LOG = '\n'.join([
    'unparsed startup output',
    log_line('2026-11-24 08:00:00,000', 'DEBUG', 'Cache warmed'),
    log_line('2026-11-24 09:00:00,000', 'INFO',
             'Alice signed in <script>alert("xss")</script>'),
    log_line('2026-11-24 10:00:00,000', 'WARNING', 'Disk usage high'),
    log_line('2026-11-24 10:00:01,000', 'ERROR', 'Request failed'),
    'Traceback (most recent call last):',
    'ValueError: broken',
    log_line('2026-11-24 11:00:00,000', 'CRITICAL', 'Database unavailable'),
]) + '\n'


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
def log_path(flask_app, tmp_path, monkeypatch):
    logs_dir = tmp_path / 'logs'
    logs_dir.mkdir()
    path = logs_dir / 'beakon.log'
    path.write_text(SAMPLE_LOG)
    monkeypatch.setattr(flask_app, 'instance_path', str(tmp_path))
    return path


@pytest.fixture
def admin_client(flask_app, log_path):
    make_user(flask_app, 'logs_admin', is_admin=True)
    return login(flask_app, 'logs_admin')


def test_api_returns_structured_entries_newest_first(admin_client):
    response = admin_client.get('/admin/api/logs', base_url=HTTPS)

    assert response.status_code == 200
    data = response.get_json()
    assert data['total'] == 6
    assert data['offset'] == 0
    assert data['next_cursor'] is None
    assert data['snapshot']
    assert [entry['level'] for entry in data['entries']] == [
        'CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'UNKNOWN']
    assert 'Traceback' in data['entries'][1]['message']
    assert 'ValueError: broken' in data['entries'][1]['raw']


def test_api_filters_severity_text_and_time(admin_client):
    response = admin_client.get('/admin/api/logs', query_string={
        'level': 'warning', 'q': 'disk'}, base_url=HTTPS)
    assert response.status_code == 200
    assert [entry['message'] for entry in response.get_json()['entries']] == [
        'Disk usage high']

    response = admin_client.get('/admin/api/logs', query_string={
        'start': '2026-11-24T10:00', 'end': '2026-11-24T10:00'}, base_url=HTTPS)
    data = response.get_json()
    assert response.status_code == 200
    assert [entry['level'] for entry in data['entries']] == ['ERROR', 'WARNING']


def test_api_paginates_matching_entries(admin_client):
    first = admin_client.get('/admin/api/logs', query_string={'limit': 2},
                             base_url=HTTPS).get_json()
    second = admin_client.get('/admin/api/logs', query_string={
        'limit': 2, 'cursor': first['next_cursor'],
        'snapshot': first['snapshot']}, base_url=HTTPS).get_json()

    assert first['total'] == 6
    assert first['next_cursor']
    assert len(first['entries']) == 2
    assert len(second['entries']) == 2
    assert second['offset'] == 2
    assert second['next_cursor']
    assert first['entries'][0]['message'] != second['entries'][0]['message']


def test_cursor_pagination_ignores_new_appended_entries(admin_client, log_path):
    first = admin_client.get('/admin/api/logs', query_string={'limit': 2},
                             base_url=HTTPS).get_json()
    with log_path.open('a') as stream:
        stream.write(log_line('2026-11-24 12:00:00,000', 'CRITICAL', 'brand new') + '\n')

    second = admin_client.get('/admin/api/logs', query_string={
        'limit': 2, 'cursor': first['next_cursor'],
        'snapshot': first['snapshot']}, base_url=HTTPS).get_json()

    assert second['total'] == first['total'] == 6
    assert 'brand new' not in [entry['message'] for entry in second['entries']]
    assert not ({entry['key'] for entry in first['entries']} &
                {entry['key'] for entry in second['entries']})


def test_snapshot_survives_rotation_and_excludes_same_millisecond_appends(
        admin_client, log_path):
    first = admin_client.get('/admin/api/logs', query_string={'limit': 2},
                             base_url=HTTPS).get_json()
    rotated = log_path.with_name('beakon.log.1')
    log_path.rename(rotated)
    log_path.write_text(
        log_line('2026-11-24 11:00:00,000', 'CRITICAL', 'same millisecond append') + '\n')

    second = admin_client.get('/admin/api/logs', query_string={
        'limit': 2, 'cursor': first['next_cursor'],
        'snapshot': first['snapshot']}, base_url=HTTPS).get_json()

    assert second['total'] == first['total'] == 6
    assert 'same millisecond append' not in [entry['message'] for entry in second['entries']]
    assert rotated.exists()


def test_expired_snapshot_returns_a_refreshable_error(admin_client, log_path):
    first = admin_client.get('/admin/api/logs', query_string={'limit': 2},
                             base_url=HTTPS).get_json()
    log_path.unlink()

    response = admin_client.get('/admin/api/logs', query_string={
        'limit': 2, 'cursor': first['next_cursor'],
        'snapshot': first['snapshot']}, base_url=HTTPS)

    assert response.status_code == 400
    assert 'expired' in response.get_json()['error'].lower()
    assert 'refresh' in response.get_json()['error'].lower()


def test_cursor_pagination_does_not_skip_duplicate_records(admin_client, log_path):
    duplicate = log_line('2026-11-24 07:00:00,000', 'DEBUG', 'same message')
    with log_path.open('a') as stream:
        stream.write(duplicate + '\n' + duplicate + '\n')

    seen = []
    cursor = None
    snapshot = None
    while True:
        query = {'limit': 1}
        if cursor:
            query.update(cursor=cursor, snapshot=snapshot)
        page = admin_client.get('/admin/api/logs', query_string=query,
                                base_url=HTTPS).get_json()
        assert page['entries'], 'pagination repeated a cursor without progress'
        seen.extend(page['entries'])
        snapshot = page['snapshot']
        cursor = page['next_cursor']
        if cursor is None:
            break

    keys = [entry['key'] for entry in seen]
    assert len(keys) == len(set(keys)) == 8
    assert [entry['message'] for entry in seen].count('same message') == 2


def test_page_refresh_at_cursor_starts_after_the_previous_page(admin_client):
    first = admin_client.get('/admin/api/logs', query_string={'limit': 2},
                             base_url=HTTPS).get_json()
    response = admin_client.get('/admin/audit', query_string={
        'limit': 2, 'cursor': first['next_cursor'],
        'snapshot': first['snapshot']}, base_url=HTTPS)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-count="4"' in html
    assert 'data-next-cursor="%s"' % first['next_cursor'] not in html


def test_api_includes_rotated_backups(admin_client, log_path):
    log_path.with_name('beakon.log.1').write_text(
        log_line('2026-11-23 12:00:00,000', 'INFO', 'rotated entry') + '\n')

    data = admin_client.get('/admin/api/logs', base_url=HTTPS).get_json()

    assert data['total'] == 7, data
    # Undated/unparsed output sorts before every timestamped backup.
    assert data['entries'][-2]['message'] == 'rotated entry'
    assert data['entries'][-1]['level'] == 'UNKNOWN'


@pytest.mark.parametrize('query', [
    {'level': 'TRACE'},
    {'start': 'not-a-date'},
    {'start': '2026-11-24T10:00:30.500'},
    {'start': '2026-11-24T10:00:00Z'},
    {'end': '2026-99-99T10:00'},
    {'start': '2026-11-25T10:00', 'end': '2026-11-24T10:00'},
    {'limit': '0'},
    {'limit': '501'},
    {'limit': 'many'},
    {'cursor': 'not-a-cursor'},
    {'snapshot': 'not-a-snapshot'},
    {'cursor': '2026-11-24 10:00:00,000|' + 'a' * 64 + '|00000000000000000000'},
])
def test_api_rejects_invalid_filters(admin_client, query):
    response = admin_client.get('/admin/api/logs', query_string=query, base_url=HTTPS)
    assert response.status_code == 400
    assert response.get_json()['error']


def test_missing_log_file_returns_empty_results(admin_client, log_path):
    log_path.unlink()
    response = admin_client.get('/admin/api/logs', base_url=HTTPS)
    assert response.status_code == 200
    assert response.get_json()['entries'] == []
    assert response.get_json()['total'] == 0


def test_read_failure_has_safe_error_response(admin_client, monkeypatch):
    def fail_read(*args, **kwargs):
        raise PermissionError('denied')
    monkeypatch.setattr(beakon_app, 'read_log_snapshot', fail_read)

    api_response = admin_client.get('/admin/api/logs', base_url=HTTPS)
    assert api_response.status_code == 500
    assert api_response.get_json()['error'] == 'Unable to read the application logs.'

    page_response = admin_client.get('/admin/audit', base_url=HTTPS)
    assert page_response.status_code == 200
    assert b'Unable to read the application logs.' in page_response.data


def test_page_renders_filters_and_escapes_log_content(admin_client):
    response = admin_client.get('/admin/audit', query_string={'q': 'Alice'},
                                base_url=HTTPS)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="log-filters"' in html
    assert 'id="log-search"' in html
    assert 'value="Alice"' in html
    assert 'js/admin_logs.js' in html
    assert '&lt;script&gt;alert(&#34;xss&#34;)&lt;/script&gt;' in html
    assert '<script>alert("xss")</script>' not in html
    assert 'Disk usage high' not in html


def test_non_admin_cannot_read_logs(flask_app, log_path):
    make_user(flask_app, 'logs_plain', is_admin=False)
    client = login(flask_app, 'logs_plain')

    assert client.get('/admin/audit', base_url=HTTPS).status_code == 403
    assert client.get('/admin/api/logs', base_url=HTTPS).status_code == 403


def test_anonymous_user_is_redirected(flask_app):
    client = flask_app.test_client()
    for path in ('/admin/audit', '/admin/api/logs'):
        response = client.get(path, base_url=HTTPS)
        assert response.status_code in (301, 302, 303)
        assert '/login' in response.headers['Location']
