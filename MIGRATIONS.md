# Backing up and migrating a Beakon database

How to upgrade an **existing** Beakon database in place without losing data, and how to
move the stored timestamps to Unix epoch.

A fresh install (`./setup.sh`) creates the current schema directly and needs none of this —
these instructions are for a database that already has data in it.

## 0. Rules

- **Stop the service before you touch the database.** Beakon runs SQLite; a writer holding
  the file during a table rebuild is how you get a half-migrated database.
- **Back up first, every time.** Every procedure below assumes you have a copy you can go
  back to. Both existing migration scripts take their own backup, but do not rely on that
  alone.
- **Migrations are not automatic.** There is no runner: nothing in the application applies
  them on startup. A fresh database gets the current schema from `db.create_all()`, and an
  existing one is brought forward by running the files in `migrations/` by hand, in order.
- **Migrations are one-way.** There are no down-migrations. Rolling back means restoring
  your backup.

## 1. Back up

Two things are worth keeping: the database and the config.

```sh
systemctl --user stop beakon.service

cd ~/projects/beakon
sqlite3 "file:instance/beakon.db?mode=ro" ".backup "instance/beakon.db.$(date +%Y%m%d-%H%M%S).bak"

cp config.cfg "config.cfg.bak.$(date +%Y%m%d-%H%M%S)"
```

`.backup` is the safe way to copy a SQLite database: it takes a consistent snapshot through
the SQLite API rather than copying bytes out from under a writer. It works against a
read-only handle, so it cannot damage the source. A plain `cp` of a live database can
capture a torn page and produce a file that looks fine until you query it.

Verify the copy before trusting it:

```sh
sqlite3 instance/beakon.db.20260101-120000.bak "PRAGMA integrity_check;"   # expect: ok
sqlite3 instance/beakon.db.20260101-120000.bak "SELECT COUNT(*) FROM locations_table;"
sqlite3 instance/beakon.db                       "SELECT COUNT(*) FROM locations_table;"
```

The two counts must match. The `.bak` files already in `instance/` follow this naming, so
keep it — it makes the newest backup obvious.

## 2. Apply a migration in place

There are two kinds of migration in `migrations/`, and they are run differently.

**Simple column additions — plain SQL.** These are safe to run directly:

```sh
sqlite3 instance/beakon.db < migrations/001_known_places_webhook_columns.sql
sqlite3 instance/beakon.db < migrations/002_known_places_include_coords_in_webhook.sql
```

Both are `ALTER TABLE ... ADD COLUMN`, which SQLite supports in place. Running one twice
fails with "duplicate column name" and changes nothing — that failure is harmless, it just
means it is already applied.

**Table rebuilds — Python scripts.** SQLite cannot change a column type, add
`AUTOINCREMENT`, or drop a constraint with `ALTER TABLE`, so those migrations rebuild the
table: create a new one, copy every row, drop the old, rename the new.

```sh
python migrations/003_users_autoincrement.py [path/to/beakon.db]
```

Notes, learned the hard way:

- **The path is positional.** It is `python migrations/003_users_autoincrement.py instance/beakon.db`,
  not `--db instance/beakon.db`.
- The default target is `instance/beakon.db`.
- The script writes its own timestamped backup beside the target **before** changing
  anything, prints the path, and verifies row counts. It is safe to run twice: if the work
  is already done it says so and exits without changing anything.

**Why 003 matters.** SQLite reuses the highest `rowid` once the row holding it is deleted, so
without `AUTOINCREMENT` a newly registered account can inherit a departed user's id — and
therefore that user's incoming sharing permissions. `UserModel.__table_args__` fixes this for
databases created from scratch; an existing database needs this migration. If you are
upgrading a database that predates it, run it.

## 3. Full in-place upgrade

```sh
# 1. Stop the service
systemctl --user stop beakon.service

# 2. Back up (section 1) and verify the copy

# 3. Apply migrations in order, oldest first
sqlite3 instance/beakon.db < migrations/001_known_places_webhook_columns.sql
sqlite3 instance/beakon.db < migrations/002_known_places_include_coords_in_webhook.sql
python  migrations/003_users_autoincrement.py

# 4. Confirm the schema matches what the code expects
sqlite3 instance/beakon.db ".schema users"
sqlite3 instance/beakon.db ".schema known_places"

# 5. Start it again and watch the log
systemctl --user start beakon.service
systemctl --user status  beakon.service
tail -f instance/logs/beakon.log
```

Then load the app and check the pages that read each affected table: the location list, a
history view, and the known-places page.

## 4. Migrating timestamps to Unix epoch

**Where things stand today.** `locations_table.timestamp` is declared `DATETIME` and holds
**text**, not numbers:

```
2026-10-23 18:47:40.278027
```

Those values are written by `datetime.now()` in `app.py`, so they are **naive local time** —
no timezone is stored anywhere. Consequences: `format_timestamp()` does `datetime.now() - ts`
and is therefore wrong across a DST change or if the host's timezone moves, and anything
comparing stored times to UTC has to guess.

**This is a decision before it is a migration.** Converting the stored text to epoch requires
answering: *what timezone are the existing values in?* SQLite cannot tell you, and getting it
wrong shifts every historical row by the host's UTC offset. On a PDT host the same row is
either `1792781260` (read as UTC) or `1792806460` (read as local) — a 7-hour difference
across all 2738 rows. Pick deliberately, and record the choice.

**The migration, verified against a copy of the real database:**

```sh
# Variant A — the stored values were meant as UTC
sqlite3 instance/beakon.db \
  "UPDATE locations_table SET timestamp = CAST(strftime('%s', timestamp) AS INTEGER);"

# Variant B — the stored values are local time and should become the correct UTC instant
sqlite3 instance/beakon.db \
  "UPDATE locations_table SET timestamp = CAST(strftime('%s', timestamp, 'utc') AS INTEGER);"
```

Both were run against a copy of the live database and produce integers, e.g.
`2026-10-23 18:47:40.278027` → `1792781260` (A) / `1792806460` (B).

**This migration is not complete on its own.** SQLite stores an integer happily in a
`DATETIME` column, but the application still has to change, in the same release:

- `LocationsModel.timestamp` must become `db.Column(db.Integer)` — otherwise SQLAlchemy
  tries to parse the integers back into `datetime` objects on read.
- The writers (`location.set_timestamp(datetime.now())`) must write epoch seconds.
- The readers and filters must treat the column as an integer: the cutoff in the pruning
  query, the day-range filters, and `format_timestamp()`.

Converting the data without the code change leaves the app unable to read its own rows.
Do both, or neither.

**Also note:** a fresh install will not produce epoch timestamps until the model changes, so
the migration and the schema change have to land together for new and existing databases to
agree.

## 5. Rollback

```sh
systemctl --user stop beakon.service
cp instance/beakon.db.20260101-120000.bak instance/beakon.db
systemctl --user start beakon.service
```

Restore the whole file rather than reversing statements by hand. There is no down-migration
to run.

## 6. Verify an upgrade

```sh
# Schema is what you expect
sqlite3 instance/beakon.db ".schema" | head -40

# Nothing was lost
sqlite3 instance/beakon.db "SELECT COUNT(*) FROM locations_table;"
sqlite3 instance/beakon.db "SELECT COUNT(*) FROM users;"

# For an epoch migration: the column is numeric and in a sane range
sqlite3 instance/beakon.db "SELECT typeof(timestamp), MIN(timestamp), MAX(timestamp) FROM locations_table;"
sqlite3 instance/beakon.db "SELECT datetime(MIN(timestamp),'unixepoch'), datetime(MAX(timestamp),'unixepoch') FROM locations_table;"
```

The last query converts the extremes back to readable UTC — if those dates do not match the
oldest and newest records you expect, the timezone decision in section 4 was the wrong one,
and you should restore the backup and redo it.
