#!/usr/bin/env python3
"""Rebuild the ``users`` table with AUTOINCREMENT (Tier 3 item 3 / B9).

SQLite cannot add ``AUTOINCREMENT`` with ``ALTER TABLE``, so the table is
rebuilt with the documented procedure: create a new table, copy every row, drop
the old table, rename the new one. Foreign keys are disabled for the duration
so the child tables keep referencing ``users`` throughout; every row and every
id is preserved.

The script is safe to run more than once: if ``users`` already has
AUTOINCREMENT it prints a message and changes nothing.

Usage::

    python migrations/003_users_autoincrement.py [path/to/beakon.db]

The default target is ``instance/beakon.db``. A timestamped copy of the target
is written next to it *before* any change, and its path is printed.
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(REPO_ROOT, 'instance', 'beakon.db')

# Matches what SQLAlchemy emits for UserModel with sqlite_autoincrement=True,
# so a fresh install and a migrated database have the same schema.
NEW_USERS_TABLE = """
CREATE TABLE users_new (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(100),
    password_hash VARCHAR,
    api_token_hash VARCHAR,
    UNIQUE (username),
    UNIQUE (api_token_hash)
)
"""


def _user_tables(conn):
    return [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()]


def _counts(conn, tables):
    return {t: conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            for t in tables}


def _users_sql(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    return row[0] if row else None


def already_migrated(conn):
    sql = _users_sql(conn)
    return bool(sql) and 'AUTOINCREMENT' in sql.upper()


def _users_by_id(conn):
    return conn.execute('SELECT id, username FROM users ORDER BY id').fetchall()


def _daniel_location_count(conn):
    row = conn.execute(
        "SELECT COUNT(*) FROM locations_table WHERE userid = "
        "(SELECT id FROM users WHERE username = 'daniel')"
    ).fetchone()
    return row[0]


def migrate(db_path):
    if not os.path.exists(db_path):
        print('ERROR: database not found: %s' % db_path)
        return 2

    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # explicit BEGIN/COMMIT control
    try:
        if already_migrated(conn):
            print('users.id already has AUTOINCREMENT; nothing to do '
                  '(clean no-op).')
            return 0

        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = '%s.%s.bak' % (db_path, stamp)
        shutil.copy2(db_path, backup)
        print('Backup written to: %s' % backup)

        tables = _user_tables(conn)
        before_counts = _counts(conn, tables)
        before_users = _users_by_id(conn)
        before_daniel = _daniel_location_count(conn)
        print('Row counts before: %s' % before_counts)
        print('Users before: %s' % before_users)

        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('BEGIN')
        try:
            conn.execute(NEW_USERS_TABLE)
            conn.execute(
                'INSERT INTO users_new '
                '(id, username, password_hash, api_token_hash) '
                'SELECT id, username, password_hash, api_token_hash '
                'FROM users')
            conn.execute('DROP TABLE users')
            conn.execute('ALTER TABLE users_new RENAME TO users')
            conn.execute('COMMIT')
        except Exception:
            conn.execute('ROLLBACK')
            raise
        finally:
            conn.execute('PRAGMA foreign_keys=ON')

        after_counts = _counts(conn, tables)
        after_users = _users_by_id(conn)
        after_daniel = _daniel_location_count(conn)
        print('Row counts after:  %s' % after_counts)
        print('Users after:  %s' % after_users)

        sequence = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='users'").fetchone()
        print('sqlite_sequence[users] = %s' % (sequence[0] if sequence else None))

        problems = []
        if before_counts != after_counts:
            problems.append('row counts changed: %s -> %s'
                            % (before_counts, after_counts))
        if before_users != after_users:
            problems.append('user ids/usernames changed: %s -> %s'
                            % (before_users, after_users))
        if before_daniel != after_daniel:
            problems.append("daniel's location count changed: %d -> %d"
                            % (before_daniel, after_daniel))
        if sequence is None:
            problems.append('sqlite_sequence has no row for users')
        fk_violations = conn.execute('PRAGMA foreign_key_check').fetchall()
        if fk_violations:
            problems.append('foreign key violations: %s' % fk_violations)

        if problems:
            print('VERIFICATION FAILED:')
            for problem in problems:
                print('  - %s' % problem)
            return 1

        print('Verification OK: row counts unchanged, ids unchanged, '
              "daniel's %d locations intact, sqlite_sequence tracks users, "
              'no foreign-key violations.' % after_daniel)
        return 0
    finally:
        conn.close()


def main(argv):
    db_path = argv[1] if len(argv) > 1 else DEFAULT_DB
    return migrate(db_path)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
