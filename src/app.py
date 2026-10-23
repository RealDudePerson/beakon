import logging
import os
import sys
import math
import mimetypes
import random
import re
import ipaddress
import tempfile
import threading
import time
import requests
from collections import defaultdict
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, request, redirect, session, Response, abort
from functools import wraps
from models import UserModel,db,login,UserDataModel,LocationsModel,SharingPermissionModel,KnownPlaceModel
from flask_login import login_required, current_user, login_user, logout_user
from flask_talisman import Talisman
from flask_apscheduler import APScheduler
from datetime import datetime, timedelta
from geofencing import check_geofences, haversine_meters

# Define app
app = Flask(__name__, template_folder='../templates', static_folder='../static', instance_path=os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), 'instance'))

# The Android companion app is hosted as a static file and must be delivered as
# an installer package. Some hosts' mime.types do not know .apk (or map it to
# application/octet-stream), so pin the type rather than trusting the platform.
# Verified: mimetypes already guesses this on the current host, but registering
# it keeps the download correct everywhere. See static/beakon-companion-debug.apk.
mimetypes.add_type('application/vnd.android.package-archive', '.apk')

def _default_config_path():
    project_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    # BEAKON_CONFIG lets tests (or alternate deployments) point the app at a
    # different config file; it defaults to the project-root config.cfg.
    return os.environ.get('BEAKON_CONFIG') or os.path.join(project_root, 'config.cfg')

# Ensure instance directory and config exist before loading config
def ensure_paths(app):
    instance_path = app.instance_path
    os.makedirs(instance_path, exist_ok=True)
    logs_path = os.path.join(instance_path, 'logs')
    os.makedirs(logs_path, exist_ok=True)
    config_path = _default_config_path()
    if not os.path.exists(config_path):
        raise SystemExit("config.cfg not found at %s — run ./setup.sh first." % config_path)
    return instance_path, logs_path, config_path

ensure_paths(app)

# Set up logging to instance/logs/
logs_path = os.path.join(app.instance_path, 'logs')
file_handler = RotatingFileHandler(os.path.join(logs_path, 'beakon.log'), 'a', 1 * 1024 * 1024, 10)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(process)-5d:%(thread)#x] %(name)s %(levelname)-5s %(message)s [in %(module)s @ %(pathname)s:%(lineno)d]'))
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.DEBUG)

# The historical config format is ``KEY = VALUE`` with bare (unquoted) values
# such as ``TRUSTED_PROXY_IPS = 192.168.2.8``. Flask's ``from_pyfile`` executes
# the file as Python, so bare values are normalised to string literals before
# loading. Quoted values, Python booleans and the legacy yes/no/on/off/1/0
# words keep their previous meaning.
_BOOL_WORDS = {
    'true': True, 'false': False, 'yes': True, 'no': False,
    'on': True, 'off': False, '1': True, '0': False,
}


def _normalise_config_source(text):
    """Turn a legacy ``KEY = VALUE`` file into valid Python source."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            lines.append(line)
            continue
        key, _, value = stripped.partition('=')
        key = key.strip()
        value = value.strip()
        if not key.isidentifier():
            lines.append(line)
            continue
        if not value:
            lines.append('%s = %r' % (key, ''))
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            lines.append('%s = %s' % (key, value))
        elif value.lower() in _BOOL_WORDS:
            lines.append('%s = %r' % (key, _BOOL_WORDS[value.lower()]))
        else:
            lines.append('%s = %r' % (key, value))
    return '\n'.join(lines) + '\n'


def load_config(app, config_path):
    """Populate ``app.config`` from a ``KEY = VALUE`` config file.

    Loading is delegated to :meth:`flask.Config.from_pyfile` so that a key is
    applied whenever the file *names* it, even when the value is empty. An
    empty value is a legitimate setting (for example an unset
    ``MAPBOX_API_KEY``) and must survive the loader; dropping it leaves later
    subscripts to raise ``KeyError`` and turn into HTTP 500s. The legacy
    bare-value syntax is normalised to valid Python first (see
    ``_normalise_config_source``).
    """
    with open(config_path, 'r') as f:
        source = _normalise_config_source(f.read())
    fd, normalised_path = tempfile.mkstemp(prefix='beakon-config-', suffix='.py')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(source)
        app.config.from_pyfile(normalised_path)
    finally:
        os.unlink(normalised_path)

# Load config
load_config(app, _default_config_path())

# S8: reject oversized request bodies instead of buffering them. The largest
# legitimate payload here is a small JSON location fix or known-place record (a
# few hundred bytes), so 64 KiB leaves ~100x headroom while bounding the memory
# a single request can consume. Overridable via config if ever needed.
app.config['MAX_CONTENT_LENGTH'] = app.config.get('MAX_CONTENT_LENGTH') or 64 * 1024

# S6: minimum length for interactive passwords (registration, password change,
# admin-created accounts). 8 characters is the commonly recommended floor (NIST
# SP 800-63B). It is never applied to the Android client's API secret/token.
MIN_PASSWORD_LENGTH = 8

# Initiate the database and login
db.init_app(app)
login.init_app(app)
login.login_view = 'login'

# Ensure all database tables exist
with app.app_context():
    from models import UserModel, LocationsModel, UserDataModel, SharingPermissionModel, KnownPlaceModel
    db.create_all()

# Tier 3 item 7: retention is config-driven and defaults to OFF. A window of 0
# (or an unset/non-numeric value) means "keep everything"; the purge only runs
# when the operator opts in via RETENTION_DAYS in config.cfg.
def _retention_days():
    """Configured retention window in days; 0 means keep everything."""
    raw = app.config.get('RETENTION_DAYS', 0)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        app.logger.warning(
            'Ignoring non-numeric RETENTION_DAYS=%r; retention disabled.', raw)
        return 0
    return days if days > 0 else 0


def purge_old_locations(days=None, now=None):
    """Delete location rows older than ``days`` days; return the count removed.

    A missing or non-positive window is a no-op, so the default configuration
    never deletes anything. ``now`` is injectable for tests.
    """
    if days is None:
        days = _retention_days()
    if days <= 0:
        return 0
    cutoff = (now or datetime.now()) - timedelta(days=days)
    deleted = LocationsModel.query.filter(
        LocationsModel.timestamp < cutoff
    ).delete(synchronize_session=False)
    db.session.commit()
    if deleted:
        app.logger.info('Retention purge removed %d location row(s) older '
                        'than %s.', deleted, cutoff)
    return deleted


# Initialize Scheduler
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()
geofence_interval = int(app.config.get('GEOFENCE_CHECK_INTERVAL', 5))
scheduler.add_job(id='geofencing_task', func=check_geofences, args=[app], trigger='interval', minutes=geofence_interval)
retention_days = _retention_days()
if retention_days > 0:
    scheduler.add_job(id='retention_task', func=purge_old_locations,
                      trigger='interval', hours=24)
    app.logger.info('Location retention enabled: %d day(s).', retention_days)

# Cookie security settings
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_SECURE'] = True
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'

# Initialize Talisman with security headers
talisman = Talisman(app,
    content_security_policy={
        'default-src': "'self'",
        # 'unsafe-inline' is gone: all inline <script> bodies now live under
        # static/js/ and every inline on*= handler is wired with
        # addEventListener. 'unsafe-eval' stays for Flatpickr.
        'script-src': "'self' 'unsafe-eval'",
        # style-src keeps 'unsafe-inline': templates still carry static
        # style="..." attributes and layout.html has an inline <style> block.
        'style-src': "'self' 'unsafe-inline'",
        'img-src': "'self' data: https://*.tiles.mapbox.com https://api.mapbox.com",
        'connect-src': "'self' https://api.mapbox.com",
    },
    x_xss_protection='1; mode=block'
)

@app.after_request
def add_cross_origin_headers(response):
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Resource-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Embedder-Policy'] = 'credentialless'
    return response

@app.errorhandler(413)
def request_entity_too_large(error):
    return {'error': 'Request body too large.'}, 413

# ponytail: in-memory per-IP throttle. Correct for a single-process self-hosted
# app; would need shared storage (e.g. redis) if ever run multi-worker.
_attempts = defaultdict(list)
_attempts_lock = threading.Lock()

def _too_many_attempts(key, limit=5, window=300):
    now = time.time()
    with _attempts_lock:
        _attempts[key] = [t for t in _attempts[key] if now - t < window]
        return len(_attempts[key]) >= limit

def _record_attempt(key):
    with _attempts_lock:
        _attempts[key].append(time.time())

def _retry_after_seconds(key, limit=5, window=300):
    """Seconds until ``key``'s throttle window drops back below ``limit``."""
    now = time.time()
    with _attempts_lock:
        times = sorted(t for t in _attempts[key] if now - t < window)
    if len(times) < limit:
        return 0
    # The oldest attempts expire first; the count drops below ``limit`` once the
    # (len - limit + 1)-th oldest attempt leaves the window.
    index = len(times) - limit
    return max(1, int(math.ceil(window - (now - times[index]))))

def _throttled_response(retry_after, api=False):
    """A 429 carrying ``Retry-After`` and an actionable body (S7)."""
    headers = {'Retry-After': str(retry_after)}
    if api:
        return ({'error': 'Too many requests. Retry in %d seconds.' % retry_after},
                429, headers)
    return Response('Too many requests. Please wait %d seconds and try again.'
                    % retry_after, status=429, mimetype='text/plain',
                    headers=headers)

def _client_address():
    """Return the real client address to key the throttle on (B2).

    Behind a proxy on a separate host the TCP source address (``remote_addr``)
    is the proxy for every client, which collapses the per-IP throttle into a
    single global bucket. When the request arrives from a configured trusted
    proxy we therefore use the last hop of ``X-Forwarded-For``: nginx appends
    the address it actually observed with ``$proxy_add_x_forwarded_for``, so
    the final entry is the trustworthy one and earlier entries are whatever the
    caller sent.

    ``TRUSTED_PROXY_IPS`` is a comma-separated list in ``config.cfg`` and
    defaults to empty. When it is empty, behaviour is exactly as before: only
    ``remote_addr`` is used. A direct (non-trusted) connection can never
    influence the key with a header.

    Fails closed: if the trusted proxy sends no usable ``X-Forwarded-For`` the
    value falls back to ``remote_addr`` (never to an attacker-chosen address)
    and the misconfiguration is logged.
    """
    remote = request.remote_addr or 'unknown'
    raw = app.config.get('TRUSTED_PROXY_IPS') or ''
    trusted = ({ip.strip() for ip in raw.split(',') if ip.strip()}
               if isinstance(raw, str) else set())
    if not trusted or request.remote_addr not in trusted:
        return remote
    forwarded = request.headers.get('X-Forwarded-For', '')
    hops = [hop.strip() for hop in forwarded.split(',') if hop.strip()]
    if not hops:
        app.logger.warning(
            'Trusted proxy %s sent no X-Forwarded-For; using remote_addr '
            'for rate limiting.', remote)
        return remote
    candidate = hops[-1]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        app.logger.warning(
            'Trusted proxy %s sent unparseable X-Forwarded-For %r; using '
            'remote_addr for rate limiting.', remote, candidate)
        return remote

def _finite_float(value, field):
    """Coerce ``value`` to a finite float, else return an error naming ``field``."""
    if isinstance(value, bool):
        return None, '%s must be a number' % field
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, '%s must be a number' % field
    if not math.isfinite(number):
        return None, '%s must be a finite number' % field
    return number, None

def _validate_place_fields(data):
    """Validate the numeric fields of a known-place payload (B3).

    Returns ``(cleaned, error)``: ``cleaned`` holds coerced values for whichever
    of ``lat``/``lon``/``radius`` were present, and ``error`` is an actionable
    message naming the offending field (or ``None``).
    """
    cleaned = {}
    if 'lat' in data:
        lat, error = _finite_float(data['lat'], 'lat')
        if error:
            return None, error
        if not -90.0 <= lat <= 90.0:
            return None, 'lat must be between -90 and 90'
        cleaned['lat'] = lat
    if 'lon' in data:
        lon, error = _finite_float(data['lon'], 'lon')
        if error:
            return None, error
        if not -180.0 <= lon <= 180.0:
            return None, 'lon must be between -180 and 180'
        cleaned['lon'] = lon
    if 'radius' in data:
        raw = data['radius']
        if isinstance(raw, bool):
            return None, 'radius must be an integer'
        try:
            radius = int(raw)
        except (TypeError, ValueError):
            return None, 'radius must be an integer'
        if isinstance(raw, float) and not raw.is_integer():
            return None, 'radius must be an integer'
        if radius < 1:
            return None, 'radius must be at least 1'
        cleaned['radius'] = radius
    return cleaned, None

def _validate_location_fields(data):
    """Validate a location-ingest payload (B7).

    ``lat``/``lon`` are required and range-checked; ``batt`` is validated as a
    number when present. Returns ``(cleaned, error)``.
    """
    if 'lat' not in data or 'lon' not in data:
        return None, 'lat and lon are required'
    lat, error = _finite_float(data['lat'], 'lat')
    if error:
        return None, error
    if not -90.0 <= lat <= 90.0:
        return None, 'lat must be between -90 and 90'
    lon, error = _finite_float(data['lon'], 'lon')
    if error:
        return None, error
    if not -180.0 <= lon <= 180.0:
        return None, 'lon must be between -180 and 180'
    cleaned = {'lat': lat, 'lon': lon}
    if data.get('batt') is not None:
        batt, error = _finite_float(data['batt'], 'batt')
        if error:
            return None, error
        cleaned['batt'] = batt
    return cleaned, None

def _log_request_summary(route, status, fields=()):
    """Emit a redacted request summary (S9).

    Only the HTTP method, the route, the response status and the *names* of the
    fields present are logged. Request bodies carry coordinates and API tokens;
    their values must never reach the log.
    """
    names = ', '.join(sorted(str(f) for f in fields)) if fields else 'none'
    app.logger.debug('%s %s -> %s (fields present: %s)',
                     request.method, route, status, names)


def format_timestamp(ts, time_only=False):
    if time_only:
        return ts.strftime('%H:%M:%S')
    diff = datetime.now() - ts
    if diff.days < 1:
        hours_ago = diff.seconds // 3600
        if hours_ago >= 1:
            return f"{hours_ago} hour{'s' if hours_ago != 1 else ''} ago"
        minutes_ago = diff.seconds // 60
        if minutes_ago >= 1:
            return f"{minutes_ago} minute{'s' if minutes_ago != 1 else ''} ago"
        return "just now"
    return str(ts)

def get_filtered_locations(userid, max_locations=10, min_distance_meters=91):
    all_locations = LocationsModel.query.filter_by(userid=userid).order_by(LocationsModel.timestamp.desc()).yield_per(100)
    filtered = []
    last_loc = None
    for loc in all_locations:
        if last_loc:
            dist = haversine_meters(last_loc.get_lat(), last_loc.get_lon(), loc.get_lat(), loc.get_lon())
            if dist < min_distance_meters:
                continue
        filtered.append(loc)
        last_loc = loc
        if len(filtered) >= max_locations:
            break
    return filtered

def get_filtered_locations_grouped(userids, max_locations=10, min_distance_meters=91):
    """Fetch and filter locations for several users with ONE grouped query.

    Applies exactly the :func:`get_filtered_locations` semantics per user
    (newest first, drop a point closer than ``min_distance_meters`` to the
    previously kept point, keep at most ``max_locations``) but after a single
    query over every ``userid``. The number of queries therefore does not grow
    with the number of users; the one-call design is about efficiency, not
    rate limiting.
    """
    grouped = defaultdict(list)
    if not userids:
        return grouped
    last_by_user = {}
    rows = (LocationsModel.query
            .filter(LocationsModel.userid.in_(list(userids)))
            .order_by(LocationsModel.userid.asc(),
                      LocationsModel.timestamp.desc())
            .yield_per(100))
    for loc in rows:
        userid = loc.get_userid()
        kept = grouped[userid]
        if len(kept) >= max_locations:
            continue
        last = last_by_user.get(userid)
        if last is not None:
            dist = haversine_meters(last.get_lat(), last.get_lon(),
                                    loc.get_lat(), loc.get_lon())
            if dist < min_distance_meters:
                continue
        kept.append(loc)
        last_by_user[userid] = loc
    return grouped

def get_shared_people(user_id):
    """People who currently share their location with ``user_id``.

    Fail-closed: the permitted set is exactly the ``sharing_permission`` rows
    whose ``shared_with_id`` is ``user_id``. A user without such a row is never
    returned, whatever other data exists. Display names come from a single
    query over all permitted ids (never one query per person). Returns a list
    of ``{'id', 'username', 'display_name'}`` sorted by username.
    """
    permissions = SharingPermissionModel.query.filter_by(shared_with_id=user_id).all()
    owner_ids = []
    usernames = {}
    for permission in permissions:
        owner_id = permission.get_data_owner_id()
        if owner_id is None or owner_id in usernames:
            continue
        owner_ids.append(owner_id)
        usernames[owner_id] = permission.get_data_owner_username()
    display_names = {}
    if owner_ids:
        for data in UserDataModel.query.filter(UserDataModel.id.in_(owner_ids)).all():
            full = ' '.join(part for part in (data.get_fname(), data.get_lname())
                            if part).strip()
            if full:
                display_names[data.id] = full
    people = [{
        'id': owner_id,
        'username': usernames[owner_id],
        'display_name': display_names.get(owner_id, usernames[owner_id]),
    } for owner_id in owner_ids]
    people.sort(key=lambda person: person['username'])
    return people

def get_locations_for_date(userid, date_str):
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        abort(400)
    start_of_day = datetime.combine(date_obj, datetime.min.time())
    end_of_day = datetime.combine(date_obj, datetime.max.time())
    return LocationsModel.query.filter(
        LocationsModel.userid == userid,
        LocationsModel.timestamp >= start_of_day,
        LocationsModel.timestamp <= end_of_day
    ).order_by(LocationsModel.timestamp.asc()).all()

# Context processor to make user admin status available globally to all templates
@app.context_processor
def inject_admin_status():
    is_admin = False
    if current_user.is_authenticated:
        user_data = UserDataModel.query.filter_by(id=current_user.id).first()
        if user_data and user_data.is_admin:
            is_admin = True
    return dict(is_admin=is_admin)

# Admin required decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect('/login')
        user_data = UserDataModel.query.filter_by(id=current_user.id).first()
        if not user_data or not user_data.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

# API login decorator: returns JSON 401 instead of redirecting to /login,
# so fetch()-based callers can parse the error instead of choking on HTML.
def _api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return {'error': 'Not authenticated'}, 401
        return f(*args, **kwargs)
    return decorated

# App routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect('/dashboard')
    return redirect('/login')

# Login page
# TODO: Possibly add some sort of spam prevention
@app.route("/login", methods=['POST','GET'])
def login():
    if current_user.is_authenticated:
        return redirect('/dashboard')
    if request.method == 'POST':
        rate_key = 'login:' + _client_address()
        if _too_many_attempts(rate_key):
            return _throttled_response(_retry_after_seconds(rate_key))
        username = request.form['username'].lower()
        user = UserModel.query.filter_by(username = username).first()
        if user is not None and user.check_password(request.form['password']):
            login_user(user,remember=True)
            app.logger.info('%s logged in successfully', username)
            return redirect('/dashboard')
        _record_attempt(rate_key)
    return render_template('login.html')
    
# Default page for logged in users
@app.route('/dashboard', methods=['POST','GET'])
@login_required
def dashboard():
    id = current_user.get_id()
    username = current_user.get_username()
    userData = UserDataModel.query.filter_by(id=id).first()
    if userData is not None:
        fname = userData.get_fname()
        lname = userData.get_lname()
    else:
        fname = ""
        lname = ""
    
    locations = get_filtered_locations(id)
    known_places = KnownPlaceModel.query.filter_by(userid=id).all()
    places_data = [{'name': p.name, 'lat': p.lat, 'lon': p.lon, 'radius': p.radius, 'enabled': p.enabled} for p in known_places]
    
    sharing_permission = SharingPermissionModel.query.filter_by(shared_with_id=id).all()
    if sharing_permission:
        sharing_permission_count = len(sharing_permission)
    else:
        sharing_permission_count = 0
    sharing_permission_list = []
    for permission in sharing_permission:
        sharing_permission_list.append(permission.get_data_owner_username())
    
    if locations:
        location = locations[0]
        lat = location.get_lat()
        lon = location.get_lon()
        batt = location.get_batt()
        ischarging = location.get_ischarging()
        timestamp = format_timestamp(location.get_timestamp())
        
        locations_data = []
        for i, loc in enumerate(locations):
            locations_data.append({
                'lat': loc.get_lat(),
                'lon': loc.get_lon(),
                'timestamp': format_timestamp(loc.get_timestamp()),
                'batt': loc.get_batt(),
                'ischarging': loc.get_ischarging(),
                'index': i
            })
        
        return render_template('dashboard.html',
            username=username, fname=fname, lname=lname,
            lat=lat, lon=lon, timestamp=timestamp,
            mapboxapi=app.config.get('MAPBOX_API_KEY', ''),
            sharing_permission_list=sharing_permission_list,
            sharing_permission_count=sharing_permission_count,
            batt=batt, ischarging=ischarging,
            locations=locations_data,
            known_places=places_data)
    
    return render_template('dashboard.html',
        fname=fname, lname=lname, username=username,
        sharing_permission_count=sharing_permission_count,
        sharing_permission_list=sharing_permission_list,
        known_places=places_data)

# Used for seeing userid
@app.route('/checkid')
@login_required
def checkid():
    id = current_user.get_id()
    return(str(id))

# Logout page
@app.route('/logout')
def logout():
    logout_user()
    return redirect('/login')

# Registration page
# TODO: set password requirements
# TODO: return error messages in the request when passwords do not match
# TODO: Possibly add some sort of spam prevention
@app.route('/register', methods=['POST','GET'])
def register():
    if app.config.get('REGISTRATION_ENABLED', False):
        if current_user.is_authenticated:
            return redirect('/dashboard')
        
        if request.method == 'POST':
            rate_key = 'register:' + _client_address()
            if _too_many_attempts(rate_key):
                return _throttled_response(_retry_after_seconds(rate_key))
            _record_attempt(rate_key)
            username = request.form['username'].lower()
            password = request.form['password']

            if not re.fullmatch(r'[a-z0-9_.-]{2,32}', username):
                return ('Invalid username. Use 2-32 chars: a-z 0-9 _ . -')

            if len(password) < MIN_PASSWORD_LENGTH:
                return ('Password must be at least %d characters.' % MIN_PASSWORD_LENGTH, 400)

            if UserModel.query.filter_by(username=username).first():
                return ('Username already present')
            
            # Check if this is the first user
            is_first_user = UserModel.query.count() == 0
            
            user = UserModel(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit() # Commit to get the user.id
            
            # Create UserDataModel with admin status
            user_data = UserDataModel(id=user.id, is_admin=is_first_user)
            db.session.add(user_data)
            db.session.commit()
            
            app.logger.info('%s registered successfully (admin: %s)', username, is_first_user)
            return redirect('/login')
        return render_template('register.html')
    else:
        if 'User-Agent' in request.headers:
            app.logger.info('Registration page accessed: %s', request.headers['User-Agent'])
        else:
            app.logger.info('Registration page accessed by IP without user-agent.')
        return render_template('registration_closed.html')

# Browser based location updater, uses javascript to ping browser for location
@app.route('/recordlocation', methods=['POST','GET'])
@login_required
def record_location():
    if request.method == 'POST':
        request_data = request.get_json(silent=True) or {}
        cleaned, error = _validate_location_fields(request_data)
        if error:
            return {'error': error}, 400
        location = LocationsModel()
        location.set_lat(cleaned['lat'])
        location.set_lon(cleaned['lon'])
        location.set_acc(request_data.get('acc'))
        location.set_timestamp(datetime.now())
        location.set_userid(current_user.get_id())
        
        if 'batt' in cleaned:
            location.set_batt(cleaned['batt'])
        
        ischarging = request_data.get('ischarging', False)
        location.set_ischarging(ischarging)
        
        db.session.add(location)
        db.session.commit()
        _log_request_summary('/recordlocation', 201, request_data.keys())
        return Response(status=201)
    return render_template('recordlocation.html')

# Used to set the API token
@app.route('/api/updatetoken', methods=['POST','GET'])
@login_required
def update_token():
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    if request.method=='POST':
        api_token = request.form['secret']
        if api_token is not None:
            user = UserModel.query.filter_by(id=id).first()
            user.set_api_token(api_token)
            db.session.add(user)
            db.session.commit()
            return redirect('/account')
        else:
            return("Error, api_token incorrect or not found.")
    else:
        return("Error, method is not post")
    
# Api based location updater.
# Use any app to update location with the following headers and data
# headers:
#   username: [username]
#   secret: [API Key created from the 'account/updateinfo' page]
#   Content-Type: application/json
# body:
#   {"lat":"33","lon":"133","acc":"3"}
@app.route('/api/recordlocation', methods=["GET","POST"])
def api_record_location():
    status_code = Response(status=401)
    if request.method == 'POST':
        rate_key = 'recordlocation:' + _client_address()
        if _too_many_attempts(rate_key):
            return _throttled_response(_retry_after_seconds(rate_key), api=True)
        # ponytail: uniform 401 for all auth failures — no 500 oracle for unknown
        # users or missing headers. Skipping dummy-hash timing equalization;
        # theoretical here, pbkdf2 dominates response time for valid users.
        username = request.headers.get('username', '')
        api_token = request.headers.get('secret', '')
        user = UserModel.query.filter_by(username=username).first() if username else None
        if user is not None and api_token:
            api_token_check = user.check_api_token(api_token)
            if api_token_check == True:
                request_data = request.get_json(silent=True) or {}
                cleaned, error = _validate_location_fields(request_data)
                if error:
                    return {'error': error}, 400
                lat = cleaned['lat']
                lon = cleaned['lon']
                acc = request_data.get('acc')
                batt = cleaned.get('batt')
                ischarging = False
                if 'ischarging' in request_data:
                    ischarging = request_data['ischarging'] in [True, 'true', 'True']
                location = LocationsModel()
                location.set_lat(lat)
                location.set_lon(lon)
                location.set_acc(acc)
                location.set_timestamp(datetime.now())
                location.set_userid(user.get_id())
                if batt:
                    location.set_batt(batt)
                if ischarging:
                    location.set_ischarging(ischarging)
                db.session.add(location)
                db.session.commit()
                status_code = Response(status=201)
                _log_request_summary('/api/recordlocation', 201,
                                     request_data.keys())
                app.logger.info('%s updated their Location.', username)
                return status_code
        _record_attempt(rate_key)
    return status_code

@app.route('/api/locations', methods=['GET'])
@_api_login_required
def api_get_locations():
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    app.logger.debug('%s fetched their own locations via API', username)
    
    date_str = request.args.get('date')
    if date_str:
        locations = get_locations_for_date(id, date_str)
        time_only = True
    else:
        locations = get_filtered_locations(id)
        time_only = False
    
    if locations:
        latest = locations[-1] if date_str else locations[0]
        return {
            'locations': [{
                'lat': loc.get_lat(),
                'lon': loc.get_lon(),
                'timestamp': format_timestamp(loc.get_timestamp(), time_only),
                'batt': loc.get_batt(),
                'ischarging': loc.get_ischarging()
            } for loc in locations],
            'latest': {
                'lat': latest.get_lat(),
                'lon': latest.get_lon(),
                'timestamp': format_timestamp(latest.get_timestamp(), time_only),
                'batt': latest.get_batt(),
                'ischarging': latest.get_ischarging()
            }
        }
    return {'locations': [], 'latest': None}

@app.route('/api/locations/<map_username>', methods=['GET'])
@_api_login_required
def api_get_user_locations(map_username):
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    map_user = UserModel.query.filter_by(username=map_username).first()
    if map_user is None:
        return {'error': 'User not found'}, 404
    has_permission = SharingPermissionModel.query.filter_by(
        data_owner_id=map_user.get_id(), shared_with_id=id
    ).first()
    if has_permission is None:
        return {'error': 'Permission denied'}, 403
    app.logger.info("%s viewed %s's location via API", username, map_username)
    
    date_str = request.args.get('date')
    if date_str:
        locations = get_locations_for_date(map_user.get_id(), date_str)
        time_only = True
    else:
        locations = get_filtered_locations(map_user.get_id())
        time_only = False
    
    if locations:
        latest = locations[-1] if date_str else locations[0]
        return {
            'locations': [{
                'lat': loc.get_lat(),
                'lon': loc.get_lon(),
                'timestamp': format_timestamp(loc.get_timestamp(), time_only),
                'batt': loc.get_batt(),
                'ischarging': loc.get_ischarging()
            } for loc in locations],
            'latest': {
                'lat': latest.get_lat(),
                'lon': latest.get_lon(),
                'timestamp': format_timestamp(latest.get_timestamp(), time_only),
                'batt': latest.get_batt(),
                'ischarging': latest.get_ischarging()
            }
        }
    return {'locations': [], 'latest': None}

@app.route('/api/locations/shared', methods=['GET'])
@_api_login_required
def api_get_shared_locations():
    """Everyone who currently shares their location with the caller.

    Fail-closed: the permitted set is exactly the ``sharing_permission`` rows
    whose ``shared_with_id`` is the caller. A user with no such row never
    appears, even if they have locations. The one-call shape is an efficiency
    choice: the locations of every permitted person are fetched with a single
    grouped query, so the query count does not grow with the number of sharers.
    A permitted user with no locations is omitted, not shown as a broken marker.

    The group view also reports a numeric ``age_seconds`` on ``latest`` (the
    ``format_timestamp()`` string is kept for display). The client receives only
    strings, so it cannot derive an age itself; this new endpoint is the only
    one that adds ``age_seconds`` — the existing endpoints are untouched.
    """
    id = current_user.get_id()
    try:
        people = get_shared_people(id)
        if not people:
            return {'people': []}

        grouped = get_filtered_locations_grouped([p['id'] for p in people])

        result = []
        for person in people:
            locations = grouped.get(person['id'])
            if not locations:
                # Permitted but no locations yet: simply absent.
                continue
            latest = locations[0]
            result.append({
                'username': person['username'],
                'display_name': person['display_name'],
                'latest': {
                    'lat': latest.get_lat(),
                    'lon': latest.get_lon(),
                    'timestamp': format_timestamp(latest.get_timestamp()),
                    # Numeric age for the group view's staleness rule. The
                    # single-person endpoint deliberately does NOT expose this.
                    'age_seconds': int(
                        (datetime.now() - latest.get_timestamp()).total_seconds()),
                    'batt': latest.get_batt(),
                    'ischarging': latest.get_ischarging(),
                },
                'trail': [{
                    'lat': loc.get_lat(),
                    'lon': loc.get_lon(),
                    'timestamp': format_timestamp(loc.get_timestamp()),
                } for loc in locations],
            })
        return {'people': result}
    except Exception:
        # Fail closed: never fall back to "show everything". The error is
        # surfaced (HTTP 500 + message) rather than swallowed.
        app.logger.exception('Failed to build shared locations for user %s', id)
        return {'people': [], 'error': 'Could not load shared locations.'}, 500

# Known Places API
@app.route('/api/places', methods=['GET', 'POST', 'PUT', 'DELETE'])
@_api_login_required
def api_places():
    id = current_user.get_id()
    if request.method == 'GET':
        places = KnownPlaceModel.query.filter_by(userid=id).all()
        return {'places': [{'id': p.id, 'name': p.name, 'lat': p.lat, 'lon': p.lon, 'radius': p.radius, 'webhook_url': p.webhook_url, 'enabled': p.enabled, 'include_coords_in_webhook': p.include_coords_in_webhook} for p in places]}
    elif request.method == 'POST':
        data = request.get_json(silent=True) or {}
        try:
            name = data['name'][:100]
            webhook_url = data['webhook_url']
        except (KeyError, TypeError):
            return {'error': 'name and webhook_url are required'}, 400
        cleaned, error = _validate_place_fields(data)
        if error:
            return {'error': error}, 400
        if 'lat' not in cleaned or 'lon' not in cleaned:
            return {'error': 'lat and lon are required'}, 400
        place = KnownPlaceModel(userid=id, name=name, lat=cleaned['lat'], lon=cleaned['lon'], radius=cleaned.get('radius', 100), webhook_url=webhook_url, enabled=data.get('enabled', True), include_coords_in_webhook=data.get('include_coords_in_webhook', False))
        db.session.add(place)
        db.session.commit()
        return Response(status=201)
    elif request.method == 'PUT':
        data = request.get_json(silent=True) or {}
        place = KnownPlaceModel.query.filter_by(id=data.get('id'), userid=id).first()
        if not place:
            return Response(status=404)
        cleaned, error = _validate_place_fields(data)
        if error:
            return {'error': error}, 400
        if 'name' in data: place.name = data['name']
        if 'lat' in cleaned: place.lat = cleaned['lat']
        if 'lon' in cleaned: place.lon = cleaned['lon']
        if 'radius' in cleaned: place.radius = cleaned['radius']
        if 'webhook_url' in data: place.webhook_url = data['webhook_url']
        if 'enabled' in data: place.enabled = data['enabled']
        if 'include_coords_in_webhook' in data: place.include_coords_in_webhook = data['include_coords_in_webhook']
        db.session.commit()
        return Response(status=200)
    elif request.method == 'DELETE':
        data = request.get_json(silent=True) or {}
        KnownPlaceModel.query.filter_by(id=data.get('id'), userid=id).delete()
        db.session.commit()
        return Response(status=200)
    return Response(status=405)

@app.route('/api/places/<int:place_id>/test', methods=['POST'])
@_api_login_required
def api_test_place(place_id):
    id = current_user.get_id()
    place = KnownPlaceModel.query.filter_by(id=place_id, userid=id).first()
    if not place:
        return Response(status=404)
    
    payload = {
        "event": "test",
        "place": place.name,
        "username": current_user.get_username(),
        "fname": "Test",
        "lname": "User"
    }
    if place.include_coords_in_webhook:
        latest = LocationsModel.query.filter_by(userid=id).order_by(LocationsModel.id.desc()).first()
        if latest and latest.lat is not None and latest.lon is not None:
            payload["lat"] = latest.lat
            payload["lon"] = latest.lon
    try:
        response = requests.post(place.webhook_url, json=payload, timeout=5)
        return {'status': response.status_code}, 200
    except Exception as e:
        app.logger.error(f"Test webhook failed for place {place.name}: {e}")
        return {'error': str(e)}, 500

# This is where account information can be set and updated
# including adding and removing location permissions
# setting API Token
# and updating display names
@app.route('/account', methods=['GET'])
@login_required
def account():
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    sharing_permission = SharingPermissionModel.query.filter_by(data_owner_id=id).all()
    sharing_permission_count = len(sharing_permission)
    sharing_permission_list = []
    for user in sharing_permission:
        tmp_username = user.get_shared_with_username()
        sharing_permission_list.append(tmp_username)
    fname = UserDataModel.query.filter_by(id=id).first()
    if fname is not None:
        fname = fname.get_fname()
    lname = UserDataModel.query.filter_by(id=id).first()
    if lname is not None:
        lname = lname.get_lname()
    return render_template('account.html',username=username,sharing_permission_list=sharing_permission_list,sharing_permission_count=sharing_permission_count,fname=fname,lname=lname,id=id, mapboxapi=app.config.get('MAPBOX_API_KEY', ''))

# This is the logic for updating fname, lname
# as well as setting location sharing permissions.
@app.route('/account/<action>', methods=['POST'])
@login_required
def account_action(action):
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    if action == "add_permission":
        request_data = request.get_json()
        add_permission_username = False
        shared_with_user = None
        if "username" in request_data:
            add_permission_username = request_data['username'].lower()
            shared_with_user = UserModel.query.filter_by(username=add_permission_username).first()
        if shared_with_user is not None:
            #Check if attempting to add permission to self, ignore if so.
            if add_permission_username != username:
                #Only add permission if it does not exist
                permission_record = SharingPermissionModel.query.filter_by(data_owner_id=id,shared_with_username=add_permission_username).first()
                if permission_record is None:
                    add_permission = SharingPermissionModel()
                    add_permission.set_data_owner_username(username)
                    add_permission.set_data_owner_id(id)
                    add_permission.set_shared_with_username(add_permission_username)
                    add_permission.set_shared_with_id(UserModel.query.filter_by(username=add_permission_username).first().get_id())
                    db.session.add(add_permission)
                    db.session.commit()
                    app.logger.info('%s allowed %s to view their location.', username, add_permission_username)
                    return Response(status=201)
        else:
            return Response(status=400)
    elif action == "remove_permission":
        request_data = request.get_json(silent=True) or {}
        raw_username = request_data.get('username')
        remove_permission_username = str(raw_username).lower() if raw_username else ''
        if not remove_permission_username:
            return {'error': 'username is required'}, 400
        delete_row = SharingPermissionModel.query.filter_by(data_owner_id=id,shared_with_username=remove_permission_username).delete(synchronize_session=False)
        db.session.commit()
        app.logger.info('%s removed %s from viewing their location.', username, remove_permission_username)
        if(delete_row>0):
            return Response(status=201)
        return Response(status=400)
    elif action == "update_name":
        request_data = request.get_json(silent=True) or {}
        fname = request_data.get('fname')
        lname = request_data.get('lname')
        if not str(fname or '').strip() or not str(lname or '').strip():
            app.logger.info('%s attempted to set their name, but sent a bad request.', username)
            return {'error': 'Both first name and last name are required.'}, 400
        fname = str(fname)[:30]
        lname = str(lname)[:40]
        userData = UserDataModel.query.filter_by(id=id).first()
        #If this is the first time the user is setting their information, a userdata db record must be created.
        if userData is None:
            userData = UserDataModel(id=id)
        app.logger.info('%s updated their full name to %s %s', username, fname, lname)
        userData.set_fname(fname)
        userData.set_lname(lname)
        db.session.add(userData)
        db.session.commit()
        return Response(status=201)
    elif action == "update_password":
        request_data = request.get_json(silent=True) or {}
        current_password = request_data.get('current_password')
        new_password = request_data.get('new_password')
        if not current_password or not new_password:
            app.logger.info('%s attempted to update their password, but sent a bad request.', username)
            return {'error': 'current_password and new_password are required.'}, 400
        if len(new_password) < MIN_PASSWORD_LENGTH:
            return {'error': 'Password must be at least %d characters.' % MIN_PASSWORD_LENGTH}, 400
        if current_user.check_password(current_password):
            current_user.set_password(new_password)
            app.logger.info('%s updated their password', username)
            db.session.add(current_user)
            db.session.commit()
            return Response(status=201)
        else:
            app.logger.info('%s attempted to update their password, but sent a bad current password.', username)
            return Response(status=401)
    elif action == "delete_locations":
        LocationsModel.query.filter_by(userid=id).delete(synchronize_session=False)
        db.session.commit()
        app.logger.info('%s deleted their location data.', username)
        return Response(status=201)
    else:
        return Response(status=404)

#Show the location of a single user on a map
@app.route('/map/<map_username>')
@login_required
def map(map_username):
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    map_user = UserModel.query.filter_by(username=map_username).first()
    if map_user is not None:
        map_user_data = UserDataModel.query.filter_by(id=map_user.get_id()).first()
        has_permission = SharingPermissionModel.query.filter_by(data_owner_id=map_user.get_id(),shared_with_id=id).first()
        if has_permission is not None:
            try:
                fname = map_user_data.get_fname()
            except:
                fname = None
            try:
                lname = map_user_data.get_lname()
            except:
                lname = None
            
            locations = get_filtered_locations(map_user.get_id())
            app.logger.info("%s viewed %s's location", username, map_username)
            
            if locations:
                location = locations[0]
                lat = location.get_lat()
                lon = location.get_lon()
                timestamp = format_timestamp(location.get_timestamp())
                batt = location.get_batt()
                ischarging = location.get_ischarging()
                
                locations_data = []
                for i, loc in enumerate(locations):
                    locations_data.append({
                        'lat': loc.get_lat(),
                        'lon': loc.get_lon(),
                        'timestamp': format_timestamp(loc.get_timestamp()),
                        'batt': loc.get_batt(),
                        'ischarging': loc.get_ischarging(),
                        'index': i
                    })
                
                return render_template('map.html',
                    fname=fname, lname=lname, lat=lat, lon=lon,
                    timestamp=timestamp, mapboxapi=app.config.get('MAPBOX_API_KEY', ''),
                    batt=batt, ischarging=ischarging, locations=locations_data,
                    map_username=map_username)
            
            return render_template('map.html', fname=fname, lname=lname)
        else:
            return redirect('/dashboard')
    else:
        return redirect('/dashboard')

@app.route('/map')
@login_required
def shared_map():
    """The "everyone" map: one marker per person who shares with you.

    Purely additive to the per-person ``/map/<username>`` view. The permitted
    set comes only from ``sharing_permission`` rows where ``shared_with_id`` is
    the caller (fail-closed); markers themselves are drawn from
    ``/api/locations/shared`` and refreshed every 10s by ``shared_map.js``.
    """
    id = current_user.get_id()
    username = UserModel.query.filter_by(id=id).first().get_username()
    people = get_shared_people(id)
    app.logger.info('%s opened the shared map (%d sharer(s))', username, len(people))
    return render_template('shared_map.html',
                           mapboxapi=app.config.get('MAPBOX_API_KEY', ''),
                           people=people,
                           username=username)

@app.route('/speed')
@login_required
def speed():
    return render_template('speed.html')

# Admin Dashboard Routes

@app.route('/admin', methods=['GET'])
@login_required
@admin_required
def admin_dashboard():
    users = UserModel.query.all()
    user_list = []
    for u in users:
        u_data = UserDataModel.query.filter_by(id=u.id).first()
        is_admin = u_data.is_admin if u_data else False
        fname = u_data.fname if u_data else ''
        lname = u_data.lname if u_data else ''
        user_list.append({
            'id': u.id,
            'username': u.username,
            'fname': fname,
            'lname': lname,
            'is_admin': is_admin
        })
    return render_template('admin.html', users=user_list)

@app.route('/admin/health', methods=['GET'])
@login_required
@admin_required
def admin_health():
    # Scheduler Status
    job = scheduler.get_job('geofencing_task')
    next_run = job.next_run_time if job else "Not scheduled"
    
    # Webhook Status
    places = KnownPlaceModel.query.all()
    
    # Last location update
    last_loc = LocationsModel.query.order_by(LocationsModel.timestamp.desc()).first()
    last_loc_time = last_loc.timestamp if last_loc else "No data"
    
    return render_template('admin_health.html', 
                           next_run=next_run, 
                           places=places, 
                           last_loc_time=last_loc_time)

@app.route('/admin/audit', methods=['GET'])
@login_required
@admin_required
def admin_audit():
    log_path = os.path.join(app.instance_path, 'logs', 'beakon.log')
    logs = []
    if os.path.exists(log_path):
        with open(log_path, 'r') as f:
            logs = f.readlines()[-100:] # Last 100 lines
    return render_template('admin_audit.html', logs=logs)

@app.route('/admin/users/create', methods=['POST'])
@login_required
@admin_required
def admin_create_user():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '').lower()
    password = data.get('password')
    if not username:
        return {'error': 'username is required'}, 400
    if not password:
        return {'error': 'password is required'}, 400
    if len(password) < MIN_PASSWORD_LENGTH:
        return {'error': 'Password must be at least %d characters.' % MIN_PASSWORD_LENGTH}, 400
    if UserModel.query.filter_by(username=username).first():
        return {'error': 'Username exists'}, 400
    
    user = UserModel(username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    
    user_data = UserDataModel(id=user.id, fname=data.get('fname'), lname=data.get('lname'), is_admin=data.get('is_admin', False))
    db.session.add(user_data)
    db.session.commit()
    return Response(status=201)

@app.route('/admin/users/<int:user_id>/update', methods=['POST'])
@login_required
@admin_required
def admin_update_user(user_id):
    data = request.get_json(silent=True) or {}
    user_data = UserDataModel.query.filter_by(id=user_id).first()
    
    if not user_data:
        app.logger.info(f"Creating missing user_data record for user {user_id}")
        user_data = UserDataModel(id=user_id)
        db.session.add(user_data)
    
    new_is_admin = data.get('is_admin', user_data.is_admin)
    if user_data.is_admin and not new_is_admin:
        admin_count = UserDataModel.query.filter_by(is_admin=True).count()
        if admin_count <= 1:
            _log_request_summary('/admin/users/<id>/update', 400, data.keys())
            return {'error': 'Cannot remove the last admin'}, 400

    user_data.fname = data.get('fname', user_data.fname)
    user_data.lname = data.get('lname', user_data.lname)
    user_data.is_admin = new_is_admin
    db.session.commit()
    _log_request_summary('/admin/users/<id>/update', 200, data.keys())
    return Response(status=200)

@app.route('/admin/users/<int:user_id>/reset-token', methods=['POST'])
@login_required
@admin_required
def admin_reset_token(user_id):
    user = UserModel.query.get(user_id)
    if not user:
        return {'error': 'User not found'}, 404
    import secrets
    new_token = secrets.token_hex(16)
    user.set_api_token(new_token)
    db.session.commit()
    return {'token': new_token}, 200

@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(user_id):
    user = UserModel.query.get(user_id)
    if not user:
        return {'error': 'User not found'}, 404
    if int(current_user.get_id()) == user_id:
        return {'error': 'Cannot delete your own account'}, 400
    target_data = UserDataModel.query.filter_by(id=user_id).first()
    if target_data and target_data.is_admin and UserDataModel.query.filter_by(is_admin=True).count() <= 1:
        return {'error': 'Cannot delete the last admin'}, 400
    # B9: drop every sharing_permission row that references the user, in either
    # direction, in the same transaction as the account deletion. The
    # data_owner_id side is also covered by the ORM cascade, but shared_with_id
    # has no relationship and would otherwise be left orphaned -- and, because
    # SQLite reuses ids, could be inherited by a future account. (users.id now
    # uses AUTOINCREMENT, but the cleanup is still required for existing rows.)
    SharingPermissionModel.query.filter(
        (SharingPermissionModel.data_owner_id == user_id) |
        (SharingPermissionModel.shared_with_id == user_id)
    ).delete(synchronize_session=False)
    db.session.delete(user)
    db.session.commit()
    return Response(status=200)

# Presence search: "when was this person within radius R of point P recently?"
# Returns the 10 most recent calendar days on which the person logged at least one
# location inside the circle, however far back that spans. Streaming DESC with early
# exit at 10 hit-days; worst case reads the user's whole history once.
PRESENCE_DAYS = 10

@app.route('/presence')
@login_required
def presence():
    id = current_user.get_id()
    shared = SharingPermissionModel.query.filter_by(shared_with_id=id).all()
    usernames = [s.get_data_owner_username() for s in shared]
    return render_template('presence.html',
                           mapboxapi=app.config.get('MAPBOX_API_KEY', ''),
                           usernames=usernames,
                           username=current_user.get_username())

@app.route('/api/presence')
@_api_login_required
def api_presence():
    id = current_user.get_id()
    username = request.args.get('user') or current_user.get_username()
    try:
        lat = float(request.args.get('lat', ''))
        lon = float(request.args.get('lon', ''))
        radius = max(1, int(request.args.get('radius', '100')))
    except (TypeError, ValueError):
        return Response(status=400)
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return Response(status=400)

    map_user = UserModel.query.filter_by(username=username).first()
    if map_user is None:
        return {'error': 'User not found'}, 404
    if map_user.get_id() != id:
        has_permission = SharingPermissionModel.query.filter_by(
            data_owner_id=map_user.get_id(), shared_with_id=id
        ).first()
        if has_permission is None:
            return {'error': 'Permission denied'}, 403

    days = {}
    for loc in (LocationsModel.query
                .filter_by(userid=map_user.get_id())
                .order_by(LocationsModel.timestamp.desc())
                .yield_per(100)):
        if loc.lat is None or loc.lon is None:
            continue
        if haversine_meters(lat, lon, loc.lat, loc.lon) >= radius:
            continue
        day = loc.timestamp.date()
        if day not in days:
            if len(days) >= PRESENCE_DAYS:
                break
            days[day] = {'date': day.isoformat(), 'hits': 0,
                         'first': loc.timestamp, 'last': loc.timestamp,
                         'last_lat': loc.lat, 'last_lon': loc.lon}
        entry = days[day]
        entry['hits'] += 1
        entry['first'] = min(entry['first'], loc.timestamp)
        entry['last'] = max(entry['last'], loc.timestamp)

    result = [{'date': e['date'], 'hits': e['hits'],
               'first': e['first'].strftime('%Y-%m-%d %H:%M:%S'),
               'last': e['last'].strftime('%Y-%m-%d %H:%M:%S'),
               'last_lat': e['last_lat'], 'last_lon': e['last_lon']}
              for e in days.values()]
    return {'user': username, 'lat': lat, 'lon': lon, 'radius': radius, 'days': result}

if __name__ == '__main__':
    app.run(ssl_context="adhoc",host='0.0.0.0')
