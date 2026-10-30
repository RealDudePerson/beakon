#!/usr/bin/env python3
"""Convert location and webhook timestamps to Unix epoch seconds (005).

``locations_table.timestamp`` and ``known_places.last_webhook_time`` were
written by ``datetime.now()``, so they are naive *local* time with no zone
recorded. This migration reinterprets each value as local time in the host's
timezone (DST applied per row via ``zoneinfo``), converts it to epoch seconds,
and rebuilds both tables so the *declared* column type is ``INTEGER`` — matching
what ``db.create_all()`` produces on a fresh install. SQLite does not enforce
types, so the data would work without the rebuild, but a migrated database
should not carry a schema that disagrees with a fresh one.

Because the existing values are naive local time, reading them as UTC would
shift every row by the host's offset (for example 7 hours on a PDT host). The
timezone is therefore explicit: the host zone by default, overridable with
``--timezone`` so the choice is recorded and reproducible.

Sub-second precision is preserved (a value with microseconds is stored as a
REAL epoch, a whole-second value as an INTEGER) so that the API's ``timestamp``
display strings stay byte-identical across the migration.

The script is safe to run more than once: if the columns are already declared
``INTEGER`` it prints a message and changes nothing.

Usage::

    python migrations/005_timestamps_unix_epoch.py [path/to/beakon.db]
    python migrations/005_timestamps_unix_epoch.py [path] --timezone America/Los_Angeles
    python migrations/005_timestamps_unix_epoch.py [path] --dry-run

The default target is ``instance/beakon.db``. A timestamped copy of the target
is written next to it *before* any change, and its path is printed.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(REPO_ROOT, 'instance', 'beakon.db')

# table -> the column holding a naive-local timestamp
TIMESTAMP_COLUMNS = {
    'locations_table': 'timestamp',
    'known_places': 'last_webhook_time',
}


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
    return datetime.now().astimezone().tzinfo


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


def _column_type(conn, table, column):
    for row in conn.execute('PRAGMA table_info("%s")' % table):
        if row[1] == column:
            return (row[2] or '').upper()
    return None


def already_migrated(conn):
    """True when every timestamp column is already declared INTEGER."""
    for table, column in TIMESTAMP_COLUMNS.items():
        ctype = _column_type(conn, table, column)
        if ctype is None or 'INT' not in ctype:
            return False
    return True


def _make_converter(tz):
    """A function mapping one stored value to its epoch representation."""
    def convert(value):
        if value is None or isinstance(value, bool):
            return value
        # Already migrated (or already numeric): leave untouched. This is what
        # makes the whole script idempotent at the row level too.
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, bytes):
            value = value.decode('utf-8', 'replace')
        if isinstance(value, str):
            parsed = _parse_datetime(value)
            if parsed is None:
                return value  # unparseable: leave it for a human to inspect
            epoch = parsed.replace(tzinfo=tz).timestamp()
            return int(epoch) if float(epoch).is_integer() else epoch
        return value
    return convert


def _count(conn, table):
    return conn.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]


def _create_sql(conn, table, override):
    """A ``CREATE TABLE <table>_new`` statement mirroring the current table.

    Column order, NOT NULL, defaults and foreign keys are copied verbatim so no
    data is lost; only the overridden timestamp column changes type.
    """
    parts = []
    primary_key = []
    for _cid, name, ctype, notnull, default, ispk in conn.execute(
            'PRAGMA table_info("%s")' % table):
        ctype = override.get(name, ctype)
        piece = '"%s"' % name
        if ctype:
            piece += ' %s' % ctype
        if notnull:
            piece += ' NOT NULL'
        if default is not None:
            piece += ' DEFAULT %s' % default
        if ispk:
            primary_key.append(name)
        parts.append(piece)
    if primary_key:
        parts.append('PRIMARY KEY (%s)'
                     % ', '.join('"%s"' % c for c in primary_key))
    for fk in conn.execute('PRAGMA foreign_key_list("%s")' % table):
        parts.append('FOREIGN KEY("%s") REFERENCES "%s" ("%s")'
                     % (fk[3], fk[2], fk[4]))
    return 'CREATE TABLE "%s_new" (\n    %s\n)' % (table, ',\n    '.join(parts))


def _rebuild(conn, table, column, convert, dry_run):
    """Convert one table's timestamp column, rebuilding it when not a dry run.

    Returns ``(row_count, first_pair, last_pair, index_count)``.
    """
    columns = [row[1] for row in conn.execute('PRAGMA table_info("%s")' % table)]
    position = columns.index(column)
    rows = conn.execute('SELECT * FROM "%s"' % table).fetchall()

    converted = []
    first_pair = last_pair = None
    for row in rows:
        record = list(row)
        before = record[position]
        after = convert(before)
        record[position] = after
        if first_pair is None:
            first_pair = (before, after)
        last_pair = (before, after)
        converted.append(tuple(record))

    if dry_run:
        return len(rows), first_pair, last_pair, 0

    create = _create_sql(conn, table, {column: 'INTEGER'})
    index_sqls = [row[0] for row in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
        "AND sql IS NOT NULL", (table,))]

    conn.execute(create)
    placeholders = ', '.join('?' * len(columns))
    conn.executemany('INSERT INTO "%s_new" VALUES (%s)' % (table, placeholders),
                     converted)
    conn.execute('DROP TABLE "%s"' % table)
    conn.execute('ALTER TABLE "%s_new" RENAME TO "%s"' % (table, table))
    for sql in index_sqls:
        conn.execute(sql)
    return len(converted), first_pair, last_pair, len(index_sqls)


def migrate(db_path, tz_name=None, dry_run=False):
    if not os.path.exists(db_path):
        print('ERROR: database not found: %s' % db_path)
        return 2

    tz = ZoneInfo(tz_name) if tz_name else _discover_host_zone()
    print('Interpreting existing text timestamps as local time in: %s' % tz)

    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # explicit BEGIN/COMMIT control
    try:
        present = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        tables = [t for t in TIMESTAMP_COLUMNS if t in present]
        if not tables:
            print('Neither timestamp table is present; nothing to do.')
            return 0

        if not dry_run and already_migrated(conn):
            print('Timestamps already stored as INTEGER epoch; nothing to do '
                  '(clean no-op).')
            return 0

        convert = _make_converter(tz)

        if dry_run:
            for table in tables:
                count, first, last, _ = _rebuild(
                    conn, table, TIMESTAMP_COLUMNS[table], convert, True)
                print('DRY RUN %s: %d row(s)' % (table, count))
                if first is not None:
                    print('  first: %r -> %r' % first)
                    print('  last:  %r -> %r' % last)
            print('Dry run: no changes written.')
            return 0

        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = '%s.%s.bak' % (db_path, stamp)
        shutil.copy2(db_path, backup)
        print('Backup written to: %s' % backup)

        before_counts = {t: _count(conn, t) for t in tables}
        print('Row counts before: %s' % before_counts)

        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('BEGIN')
        try:
            for table in tables:
                count, first, last, indexes = _rebuild(
                    conn, table, TIMESTAMP_COLUMNS[table], convert, False)
                print('%s: converted %d row(s), recreated %d index(es)'
                      % (table, count, indexes))
                if first is not None:
                    print('  first: %r -> %r' % first)
                    print('  last:  %r -> %r' % last)
            conn.execute('COMMIT')
        except Exception:
            conn.execute('ROLLBACK')
            raise
        finally:
            conn.execute('PRAGMA foreign_keys=ON')

        after_counts = {t: _count(conn, t) for t in tables}
        print('Row counts after:  %s' % after_counts)

        problems = []
        if before_counts != after_counts:
            problems.append('row counts changed: %s -> %s'
                            % (before_counts, after_counts))
        fk_violations = conn.execute('PRAGMA foreign_key_check').fetchall()
        if fk_violations:
            problems.append('foreign key violations: %s' % fk_violations)
        for table, column in TIMESTAMP_COLUMNS.items():
            if table not in tables:
                continue
            ctype = _column_type(conn, table, column)
            sample = conn.execute(
                'SELECT "%s" FROM "%s" WHERE "%s" IS NOT NULL LIMIT 1'
                % (column, table, column)).fetchone()
            print('Sample %s.%s = %r (declared %s)'
                  % (table, column, sample[0] if sample else None, ctype))
            if 'INT' not in ctype:
                problems.append('%s.%s is declared %s, not INTEGER'
                                % (table, column, ctype))

        if problems:
            print('VERIFICATION FAILED:')
            for problem in problems:
                print('  - %s' % problem)
            return 1

        print('Verification OK: row counts unchanged, timestamps are INTEGER '
              'epoch, no foreign-key violations.')
        return 0
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('db_path', nargs='?', default=DEFAULT_DB,
                        help='path to beakon.db (default: instance/beakon.db)')
    parser.add_argument('--timezone', default=None,
                        help='IANA zone for the existing naive-local values '
                             '(default: the host timezone)')
    parser.add_argument('--dry-run', action='store_true',
                        help='print the first and last conversion; write nothing')
    args = parser.parse_args(argv)
    return migrate(args.db_path, tz_name=args.timezone, dry_run=args.dry_run)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
