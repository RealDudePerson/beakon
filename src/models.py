import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin, LoginManager

login = LoginManager()
db = SQLAlchemy()

# Timestamps are stored as Unix epoch seconds (UTC). Existing rows were written
# by ``datetime.now()``, so they are naive *local* time with no zone recorded;
# they must be interpreted in the host's timezone — not as UTC — and per row so
# a value on the other side of a DST change is still correct. These helpers make
# every read tolerant of both the old text form and the new integer form, so the
# code and the data migration can land in either order.

_HOST_ZONE = None


def _discover_host_zone():
    """The host's timezone as a ``ZoneInfo`` (DST applied per timestamp)."""
    candidates = [os.environ.get('TZ')]
    try:
        with open('/etc/timezone') as handle:
            candidates.append(handle.read().strip())
    except OSError:
        pass
    try:
        link = os.readlink('/etc/localtime')
        if 'zoneinfo/' in link:
            candidates.append(link.split('zoneinfo/', 1)[1])
    except OSError:
        pass
    for name in candidates:
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except Exception:
            continue
    # Last resort: the process's current offset (no DST history).
    return datetime.now().astimezone().tzinfo


def host_zone():
    """The host timezone, discovered once and cached."""
    global _HOST_ZONE
    if _HOST_ZONE is None:
        _HOST_ZONE = _discover_host_zone()
    return _HOST_ZONE


def _parse_datetime(text):
    """Parse a stored timestamp string, or return ``None``."""
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def to_epoch(value, tz=None):
    """Epoch seconds from an int, a naive datetime, or text.

    - ``int``/``float`` are already epoch and returned unchanged, which is what
      lets one code path read a migrated *and* an unmigrated database;
    - a ``datetime`` is converted with ``.timestamp()``, treating a naive value
      as local time in ``tz`` (the host zone by default) so DST is per row;
    - a ``str`` is parsed and then treated as local;
    - ``None`` stays ``None``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, datetime):
        moment = value
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=tz or host_zone())
        return moment.timestamp()
    if isinstance(value, str):
        parsed = _parse_datetime(value)
        return None if parsed is None else to_epoch(parsed, tz)
    return None


def now_epoch():
    """Current time as integer Unix epoch seconds (the writer helper)."""
    return int(datetime.now().timestamp())

class UserModel(UserMixin, db.Model):
    __tablename__ = 'users'
    # B9: SQLite reuses the highest rowid after the row holding it is deleted,
    # so a new account could inherit a departed user's id (and therefore their
    # incoming sharing permissions). AUTOINCREMENT makes the id monotonic by
    # tracking it in sqlite_sequence. Existing databases need the table-rebuild
    # migration in migrations/003_users_autoincrement.py; this table arg only
    # takes effect for databases created from scratch.
    __table_args__ = {'sqlite_autoincrement': True}

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True)
    password_hash = db.Column(db.String())
    api_token_hash = db.Column(db.String(), unique=True)
    
    # Relationships
    locations = db.relationship('LocationsModel', backref='user', foreign_keys='LocationsModel.userid', cascade="all, delete-orphan")
    user_data = db.relationship('UserDataModel', backref='user', uselist=False, foreign_keys='UserDataModel.id', cascade="all, delete-orphan")
    sharing_permissions = db.relationship('SharingPermissionModel', foreign_keys='SharingPermissionModel.data_owner_id', backref='owner', cascade="all, delete-orphan")
    known_places = db.relationship('KnownPlaceModel', backref='user', foreign_keys='KnownPlaceModel.userid', cascade="all, delete-orphan")
 
    def set_password(self,password):
        self.password_hash = generate_password_hash(password)
     
    def check_password(self,password):
        return check_password_hash(self.password_hash,password)

    def set_api_token(self,api_token):
        self.api_token_hash = generate_password_hash(api_token)

    def check_api_token(self,api_token):
        # An account may legitimately have no API token yet (api_token_hash is
        # NULL). Treat that exactly like a wrong token instead of letting
        # check_password_hash raise AttributeError and turn into a 500.
        if not self.api_token_hash:
            return False
        return check_password_hash(self.api_token_hash,api_token)

    def get_username(self):
        return self.username

@login.user_loader
def load_user(id):
    return UserModel.query.get(int(id))

class LocationsModel(db.Model):
    __tablename__ = 'locations_table'

    id = db.Column(db.Integer, primary_key = True)
    lat = db.Column(db.Float())
    lon = db.Column(db.Float())
    acc = db.Column(db.Float())
    timestamp = db.Column(db.Integer())
    userid = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    batt = db.Column(db.Integer)
    ischarging = db.Column(db.Boolean)

    __table_args__ = (
        db.Index('idx_userid_timestamp', 'userid', 'timestamp'),
        db.Index('idx_locations_lat_lon', 'lat', 'lon'),
    )

    def set_lat(self, lat):
        self.lat = lat
    def get_lat(self):
        return self.lat
    def set_lon(self, lon):
        self.lon = lon
    def get_lon(self):
        return self.lon
    def set_acc(self, acc):
        self.acc = acc
    def get_acc(self):
        return self.acc
    def set_timestamp(self, timestamp):
        self.timestamp = timestamp
    def get_timestamp(self):
        return self.timestamp
    def set_userid(self, userid):
        self.userid = userid
    def get_userid(self):
        return self.userid
    def get_batt(self):
        return self.batt
    def set_batt(self, batt):
        self.batt = batt
    def get_ischarging(self):
        return self.ischarging
    def set_ischarging(self, ischarging):
        self.ischarging = ischarging


class UserDataModel(db.Model):
    __tablename__ = 'user_data'
    id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True) #this ID is tied to the user ID when setting user data from /updateinfo
    fname = db.Column(db.String(30))
    lname = db.Column(db.String(40))
    access_to = db.Column(db.String(100))
    is_admin = db.Column(db.Boolean())

    def set_fname(self,fname):
        self.fname = fname
    
    def get_fname(self):
        return self.fname

    def set_lname(self, lname):
        self.lname = lname

    def get_lname(self):
        return self.lname
    
    def check_admin(self):
        return self.is_admin

    def check_access(self):
        return self.access_to
    
    def set_admin(self):
        self.is_admin = True

class SharingPermissionModel(db.Model):
    __tablename__ = 'sharing_permission'
    id = db.Column(db.Integer, primary_key=True)
    data_owner_username = db.Column(db.String(100))
    data_owner_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    shared_with_username = db.Column(db.String(100))
    shared_with_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    def set_data_owner_username(self,data_owner_username):
        self.data_owner_username = data_owner_username
    
    def get_data_owner_username(self):
        return self.data_owner_username

    def set_data_owner_id(self,data_owner_id):
        self.data_owner_id = data_owner_id
    
    def get_data_owner_id(self):
        return self.data_owner_id

    def set_shared_with_username(self,shared_with_username):
        self.shared_with_username = shared_with_username

    def get_shared_with_username(self):
        return self.shared_with_username
    
    def set_shared_with_id(self,shared_with_id):
        self.shared_with_id = shared_with_id

    def get_shared_with_id(self):
        return self.shared_with_id

class KnownPlaceModel(db.Model):
    __tablename__ = 'known_places'
    id = db.Column(db.Integer, primary_key=True)
    userid = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    name = db.Column(db.String(100))
    lat = db.Column(db.Float())
    lon = db.Column(db.Float())
    radius = db.Column(db.Integer, default=100)
    webhook_url = db.Column(db.String(255))
    enabled = db.Column(db.Boolean, default=True)
    is_inside = db.Column(db.Boolean, default=False)
    last_webhook_time = db.Column(db.Integer())
    last_webhook_status = db.Column(db.String(20))
    include_coords_in_webhook = db.Column(db.Boolean, default=False)


class AppSettingModel(db.Model):
    """Runtime-adjustable settings, edited by admins and stored in the database.

    Settings live here (rather than in ``config.cfg``) so an admin change takes
    effect immediately and survives a restart, without rewriting the operator's
    config file — which also holds secrets. A missing key means "no override":
    callers fall back to their config default. The table is created on startup
    by ``db.create_all()``, so no migration is needed for existing databases.
    """
    __tablename__ = 'app_settings'
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(255))


def get_app_setting(key, default=None):
    """Return the stored value for ``key``, or ``default`` when unset."""
    row = db.session.get(AppSettingModel, key)
    return default if row is None else row.value


def set_app_setting(key, value):
    """Store ``value`` for ``key`` (insert or update) and commit."""
    row = db.session.get(AppSettingModel, key)
    if row is None:
        row = AppSettingModel(key=key, value=value)
        db.session.add(row)
    else:
        row.value = value
    db.session.commit()
    return row.value
