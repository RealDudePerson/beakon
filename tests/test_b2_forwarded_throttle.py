"""Regression tests for review finding B2: the throttle must key on the real
client, not on the proxy address.

The app is reached only through nginx on a separate host, so
``request.remote_addr`` is the proxy for every client. These tests prove that:

1. With no trusted proxy configured, behaviour is unchanged.
2. A configured trusted proxy plus a distinct ``X-Forwarded-For`` per client
   gives each client its own bucket.
3. A direct (non-trusted) caller cannot escape or influence a bucket with a
   forged ``X-Forwarded-For``.
4. Five failures from one forwarded client still throttle that client.
5. A trusted proxy sending no usable header fails closed to ``remote_addr``.

Re-introducing the bug (keying on ``request.remote_addr``) makes tests 2, 4 and
the fail-closed cases fail.
"""

import pytest

from app import _attempts
from models import UserModel, UserDataModel, db

HTTPS = 'https://localhost'
PROXY_IP = '10.0.0.1'


@pytest.fixture(autouse=True)
def _clear_throttle():
    """The throttle is process-global; keep tests independent of each other."""
    _attempts.clear()
    yield
    _attempts.clear()


def make_user(flask_app, username, password='password123'):
    with flask_app.app_context():
        user = UserModel(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        db.session.add(UserDataModel(id=user.id, fname='Test', lname='User'))
        db.session.commit()


def post_login(client, username, remote, xff=None):
    """POST a wrong-password login from ``remote`` with an optional XFF."""
    headers = {}
    if xff is not None:
        headers['X-Forwarded-For'] = xff
    return client.post('/login',
                       data={'username': username, 'password': 'wrong-password'},
                       headers=headers,
                       environ_base={'REMOTE_ADDR': remote},
                       base_url=HTTPS)


# --------------------------------------------------------------------------
# 1. No trusted proxy configured -> behaviour is exactly as before.
# --------------------------------------------------------------------------
def test_no_trusted_proxy_ignores_forwarded_header(flask_app):
    make_user(flask_app, 'b2_no_proxy')
    client = flask_app.test_client()

    # Five failures arrive from one remote_addr with varying forged XFF.
    for i in range(5):
        r = post_login(client, 'b2_no_proxy', '127.0.0.1',
                       xff='203.0.113.%d' % i)
        assert r.status_code == 200, r.status_code

    # The header must not create a fresh bucket: the sixth request is throttled
    # even though it presents a brand-new XFF, proving only remote_addr is used.
    r = post_login(client, 'b2_no_proxy', '127.0.0.1', xff='198.51.100.250')
    assert r.status_code == 429, r.status_code


# --------------------------------------------------------------------------
# 2. Trusted proxy + distinct XFF -> one client's throttle must not hit another.
# --------------------------------------------------------------------------
def test_forwarded_clients_have_separate_buckets(flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, 'TRUSTED_PROXY_IPS', PROXY_IP)
    make_user(flask_app, 'b2_separate')
    client = flask_app.test_client()

    for _ in range(5):
        r = post_login(client, 'b2_separate', PROXY_IP, xff='203.0.113.5')
        assert r.status_code == 200, r.status_code
    assert post_login(client, 'b2_separate', PROXY_IP,
                      xff='203.0.113.5').status_code == 429

    # A different forwarded client behind the same proxy is unaffected.
    other = post_login(client, 'b2_separate', PROXY_IP, xff='203.0.113.9')
    assert other.status_code == 200, other.status_code


# --------------------------------------------------------------------------
# 3. A direct caller cannot escape or influence its own bucket.
# --------------------------------------------------------------------------
def test_direct_client_cannot_spoof_forwarded_header(flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, 'TRUSTED_PROXY_IPS', PROXY_IP)
    make_user(flask_app, 'b2_direct')
    client = flask_app.test_client()

    # A direct attacker (not the trusted proxy) forges a victim address and
    # varies it on every request; none of it is trusted.
    for i in range(5):
        r = post_login(client, 'b2_direct', '192.168.2.50',
                       xff='203.0.113.5, 203.0.113.%d' % i)
        assert r.status_code == 200, r.status_code

    # It cannot escape its own bucket by changing the header.
    r = post_login(client, 'b2_direct', '192.168.2.50', xff='203.0.113.77')
    assert r.status_code == 429, r.status_code

    # Nor did it poison the forwarded bucket it spoofed: a real request through
    # the trusted proxy for that address is still allowed.
    victim = post_login(client, 'b2_direct', PROXY_IP, xff='203.0.113.5')
    assert victim.status_code == 200, victim.status_code

    # A different direct client sharing the forged header is also unaffected.
    neighbour = post_login(client, 'b2_direct', '192.168.2.51',
                           xff='203.0.113.5')
    assert neighbour.status_code == 200, neighbour.status_code


# --------------------------------------------------------------------------
# 4. Five failures from one forwarded client still throttle that client.
# --------------------------------------------------------------------------
def test_five_failures_throttle_the_forwarded_client(flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, 'TRUSTED_PROXY_IPS', PROXY_IP)
    make_user(flask_app, 'b2_throttled')
    client = flask_app.test_client()

    for _ in range(5):
        r = post_login(client, 'b2_throttled', PROXY_IP, xff='198.51.100.7')
        assert r.status_code == 200, r.status_code

    r = post_login(client, 'b2_throttled', PROXY_IP, xff='198.51.100.7')
    assert r.status_code == 429, r.status_code
    assert r.headers.get('Retry-After') is not None
    assert 0 < int(r.headers['Retry-After']) <= 300

    # The failures filled the forwarded client's bucket, not the proxy's own
    # fallback bucket: an unforwarded request from the proxy is still allowed.
    assert post_login(client, 'b2_throttled', PROXY_IP,
                      xff=None).status_code == 200


# --------------------------------------------------------------------------
# 5. Fail closed: absent or unparseable XFF from the trusted proxy.
# --------------------------------------------------------------------------
def test_trusted_proxy_absent_xff_falls_back_to_remote(flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, 'TRUSTED_PROXY_IPS', PROXY_IP)
    make_user(flask_app, 'b2_absent')
    client = flask_app.test_client()

    for _ in range(5):
        r = post_login(client, 'b2_absent', PROXY_IP, xff=None)
        assert r.status_code == 200, r.status_code
    # The absent header must not evade the limit: the proxy's own address is
    # the bucket.
    assert post_login(client, 'b2_absent', PROXY_IP, xff=None).status_code == 429

    # A genuinely forwarded client is a separate bucket, proving the fallback
    # used remote_addr rather than a bogus/blank value.
    assert post_login(client, 'b2_absent', PROXY_IP,
                      xff='203.0.113.30').status_code == 200


def test_trusted_proxy_unparseable_xff_falls_back_to_remote(flask_app,
                                                            monkeypatch):
    monkeypatch.setitem(flask_app.config, 'TRUSTED_PROXY_IPS', PROXY_IP)
    make_user(flask_app, 'b2_garbage')
    client = flask_app.test_client()

    for _ in range(5):
        r = post_login(client, 'b2_garbage', PROXY_IP, xff='not-an-ip')
        assert r.status_code == 200, r.status_code
    r = post_login(client, 'b2_garbage', PROXY_IP, xff='not-an-ip')
    assert r.status_code == 429, r.status_code

    assert post_login(client, 'b2_garbage', PROXY_IP,
                      xff='203.0.113.40').status_code == 200


# --------------------------------------------------------------------------
# 6. /api/recordlocation uses the same real-client key (Android companion).
# --------------------------------------------------------------------------
def test_api_recordlocation_uses_forwarded_client(flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, 'TRUSTED_PROXY_IPS', PROXY_IP)
    client = flask_app.test_client()

    def bad_ingest(xff):
        return client.post(
            '/api/recordlocation',
            headers={'username': 'nobody-b2', 'secret': 'x',
                     'X-Forwarded-For': xff},
            json={'lat': 1.0, 'lon': 2.0},
            environ_base={'REMOTE_ADDR': PROXY_IP},
            base_url=HTTPS)

    for _ in range(5):
        assert bad_ingest('203.0.113.60').status_code == 401
    assert bad_ingest('203.0.113.60').status_code == 429

    # The Android client on a different address is not locked out.
    assert bad_ingest('203.0.113.61').status_code == 401
