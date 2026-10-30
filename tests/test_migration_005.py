"""Tests for migrations/005_timestamps_unix_epoch.py.

Exercised against a throwaway database built with the *old* schema and text
timestamps, so the migration, its idempotency, its ``--dry-run`` and the
truncated display string are all proven without touching any real
database.
"""

import importlib.util
import os
import sqlite3

from app import format_timestamp
from models import host_zone, to_epoch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION_PATH = os.path.join(REPO_ROOT, 'migrations',
                              '005_timestamps_unix_epoch.py')

OLD_SCHEMA = """
CREATE TABLE users (
    id INTEGER NOT NULL PRIMARY KEY,
    username VARCHAR(100)
);
CREATE TABLE locations_table (
    id INTEGER NOT NULL,
    lat FLOAT, lon FLOAT, acc FLOAT,
    timestamp DATETIME,
    userid INTEGER, batt INTEGER, ischarging BOOLEAN,
    PRIMARY KEY (id),
    FOREIGN KEY(userid) REFERENCES users (id)
);
CREATE INDEX idx_userid_timestamp ON locations_table (userid, timestamp);
CREATE INDEX ix_locations_table_userid ON locations_table (userid);
CREATE TABLE known_places (
    id INTEGER NOT NULL,
    userid INTEGER, name VARCHAR(100), lat FLOAT, lon FLOAT, radius INTEGER,
    webhook_url VARCHAR(255), enabled BOOLEAN, is_inside BOOLEAN,
    last_webhook_time DATETIME, last_webhook_status VARCHAR(20),
    include_coords_in_webhook BOOLEAN,
    PRIMARY KEY (id),
    FOREIGN KEY(userid) REFERENCES users (id)
);
CREATE INDEX ix_known_places_userid ON known_places (userid);
"""

LOCATIONS = [
    (1, 40.0, -70.0, 5.0, '2021-06-01 12:34:56.789012', 1, 50, 0),
    (2, 41.0, -71.0, 5.0, '2021-06-02 00:00:30', 1, 60, 1),
]


def _load_migration():
    spec = importlib.util.spec_from_file_location('migration_005', MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_old_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO users (id, username) VALUES (1, 'daniel')")
    conn.executemany(
        "INSERT INTO locations_table "
        "(id, lat, lon, acc, timestamp, userid, batt, ischarging) "
        "VALUES (?,?,?,?,?,?,?,?)", LOCATIONS)
    conn.execute(
        "INSERT INTO known_places "
        "(id, userid, name, last_webhook_time, last_webhook_status) "
        "VALUES (1, 1, 'Home', '2021-06-03 08:15:00.000001', 'success')")
    conn.commit()
    conn.close()


def _column_type(path, table, column):
    conn = sqlite3.connect(path)
    try:
        for row in conn.execute('PRAGMA table_info("%s")' % table):
            if row[1] == column:
                return (row[2] or '').upper()
        return None
    finally:
        conn.close()


def _rows(path, table):
    conn = sqlite3.connect(path)
    try:
        return conn.execute('SELECT * FROM "%s" ORDER BY id' % table).fetchall()
    finally:
        conn.close()


def _index_names(path, table):
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (table,))}
    finally:
        conn.close()


def _backups(path):
    directory = os.path.dirname(path)
    base = os.path.basename(path)
    return [name for name in os.listdir(directory) if name.startswith(base + '.')]


def test_migration_converts_and_is_idempotent(tmp_path):
    migration = _load_migration()
    db_path = str(tmp_path / 'beakon.db')
    _make_old_db(db_path)
    tz_name = host_zone().key

    assert migration.migrate(db_path, tz_name=tz_name) == 0

    # Row counts are preserved and the declared type is now INTEGER.
    assert [row[0] for row in _rows(db_path, 'locations_table')] == [1, 2]
    assert _column_type(db_path, 'locations_table', 'timestamp') == 'INTEGER'
    assert _column_type(db_path, 'known_places', 'last_webhook_time') == 'INTEGER'
    # The composite index (and the userid index) were recreated.
    assert 'idx_userid_timestamp' in _index_names(db_path, 'locations_table')
    assert 'ix_locations_table_userid' in _index_names(db_path, 'locations_table')

    # Every stored value is a whole-second INTEGER, so a sub-second source value
    # is truncated and its display string loses the sub-second part.
    conn = sqlite3.connect(db_path)
    stored = dict(conn.execute(
        'SELECT id, timestamp FROM locations_table').fetchall())
    types = dict(conn.execute(
        'SELECT id, typeof(timestamp) FROM locations_table').fetchall())
    webhook = conn.execute(
        'SELECT last_webhook_time FROM known_places WHERE id=1').fetchone()[0]
    conn.close()
    assert types[1] == 'integer'
    assert types[2] == 'integer'
    assert format_timestamp(stored[1]) == '2021-06-01 12:34:56'
    assert format_timestamp(stored[2]) == '2021-06-02 00:00:30'
    assert int(webhook) == int(to_epoch('2021-06-03 08:15:00.000001'))

    # Re-running is a clean no-op: identical rows and no second backup.
    after_first = {t: _rows(db_path, t)
                   for t in ('locations_table', 'known_places')}
    backups_after_first = _backups(db_path)
    assert migration.migrate(db_path, tz_name=tz_name) == 0
    assert {t: _rows(db_path, t) for t in after_first} == after_first
    assert _backups(db_path) == backups_after_first


def test_migration_handles_an_empty_table(tmp_path):
    """The real known_places table can be empty; the sample print must not blow up."""
    migration = _load_migration()
    db_path = str(tmp_path / 'beakon.db')
    _make_old_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute('DELETE FROM known_places')
    conn.commit()
    conn.close()

    assert migration.migrate(db_path, tz_name=host_zone().key) == 0
    assert _column_type(db_path, 'known_places', 'last_webhook_time') == 'INTEGER'
    assert _rows(db_path, 'known_places') == []
    assert migration.migrate(db_path, tz_name=host_zone().key) == 0


def test_migration_dry_run_writes_nothing(tmp_path):
    migration = _load_migration()
    db_path = str(tmp_path / 'beakon.db')
    _make_old_db(db_path)

    assert migration.migrate(db_path, tz_name=host_zone().key, dry_run=True) == 0

    # The column type and the values are untouched, and no backup was written.
    assert _column_type(db_path, 'locations_table', 'timestamp') == 'DATETIME'
    assert _backups(db_path) == []
    conn = sqlite3.connect(db_path)
    types = {row[0] for row in conn.execute(
        'SELECT typeof(timestamp) FROM locations_table')}
    conn.close()
    assert types == {'text'}


def test_migration_creates_a_backup_before_changing_anything(tmp_path):
    migration = _load_migration()
    db_path = str(tmp_path / 'beakon.db')
    _make_old_db(db_path)

    assert migration.migrate(db_path, tz_name=host_zone().key) == 0
    backups = _backups(db_path)
    assert len(backups) == 1
    # The backup still holds the pre-migration text.
    backup = os.path.join(os.path.dirname(db_path), backups[0])
    assert _column_type(backup, 'locations_table', 'timestamp') == 'DATETIME'
