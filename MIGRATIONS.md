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
- **Check what your clients do with unknown fields before you update the server.** The
  timestamp work adds a field to the location responses. A client that ignores unknown JSON
  fields is unaffected; one that rejects them breaks the moment the server updates, with no
  error on the server side at all. See section 4.

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

`migrations/004_locations_lat_lon_index.sql` is the same kind of additive migration: it
creates `idx_locations_lat_lon (lat, lon)`, the bounding-box index used by the Presence
search prefilter. It is `CREATE INDEX IF NOT EXISTS`, so running it twice is a clean no-op.

```sh
sqlite3 instance/beakon.db < migrations/004_locations_lat_lon_index.sql
```

**New tables — nothing to run.** A migration file is only needed when an *existing* table has
to change. `db.create_all()` runs on every startup and creates any table that is missing, so a
release that only *adds* a table brings an existing database forward on the next restart with
no migration file and no manual step. `app_settings` (the admin-controlled registration flag)
is one of these: it appears empty on first start after the upgrade and needs no action.

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
sqlite3 instance/beakon.db < migrations/004_locations_lat_lon_index.sql
python  migrations/005_timestamps_unix_epoch.py --timezone <your-host-zone>

# 4. Give the query planner statistics, or the index from 004 will not be chosen
sqlite3 instance/beakon.db "ANALYZE;"

# 5. Confirm the schema matches what the code expects
sqlite3 instance/beakon.db ".schema users"
sqlite3 instance/beakon.db ".schema known_places"

# 6. Start it again and watch the log
systemctl --user start beakon.service
systemctl --user status  beakon.service
tail -f instance/logs/beakon.log
```

Three things about that order:

- **`--timezone` is the decision from section 4.** Pass it explicitly rather than letting the
  host zone be used silently, so the choice is recorded in your shell history.
- **004 and 005 can be run in either order.** 005 rebuilds `locations_table` and replays every
  index it finds, so it preserves the index 004 creates; and 004 is
  `CREATE INDEX IF NOT EXISTS`, so it is happy either way. Oldest-first is still the
  convention.
- **Do not skip step 4.** Without `ANALYZE` the planner has no statistics and will not use
  the lat/lon index for the Presence prefilter — it falls back to scanning by user, which is
  the cost the index exists to remove. See section 6.

Before step 6, read section 4's note on client compatibility: if your instance serves an
Android build older than v1.2, updating the server will break that app.

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

**The migration is scripted** — `migrations/005_timestamps_unix_epoch.py`:

```sh
# Rehearse first: prints the first and last conversion, writes nothing
python migrations/005_timestamps_unix_epoch.py --dry-run

# Then convert. A timestamped backup is written before anything changes.
python migrations/005_timestamps_unix_epoch.py

# Record the choice explicitly rather than relying on the host zone
python migrations/005_timestamps_unix_epoch.py --timezone America/Los_Angeles
```

It interprets the existing text as local time in the host's timezone (overridable with
`--timezone` so the choice is recorded and reproducible), converts both
`locations_table.timestamp` and `known_places.last_webhook_time`, rebuilds both tables so the
declared column type is `INTEGER`, recreates the indexes, and verifies row counts, foreign
keys and the declared type before reporting success. It is transactional: a failure rolls
back. Re-running it is a clean no-op.

**The application code changed with it.** `to_epoch()` in `src/models.py` accepts an integer,
a `datetime`, or text, and every *read* goes through it, so per-row rendering works against a
migrated and an unmigrated database alike. The writers use `now_epoch()`.

**But the code and the data must change together — do not deploy one without the other.**
Two SQL-level comparisons still run against the raw column, and SQLite sorts TEXT above
numbers, so a half-done upgrade breaks them:

| State | What breaks |
|---|---|
| New code, text data | The day-range filter matches nothing. Measured on the live data: the filter for 2026-10-20 returns **0 rows** against text and **789** after the migration. The prune cutoff also silently stops matching. |
| Old code, integer data | `datetime.now() - <int>` raises `TypeError`, so timestamps stop rendering at all. |

So the sequence is: **stop the service, migrate the data, start the service.** Not "deploy the
code first" — that leaves the date filter quietly returning nothing, which is worse than an
error because it looks like there is simply no data.

**Stored values are whole integer seconds.** The converter truncates toward zero, so every
row — including one whose source text carried microseconds — becomes an INTEGER epoch. The
only consequence is that a location older than a day loses the sub-second part of its
`timestamp` display string (`2026-10-23 18:47:40.278027` becomes `2026-10-23 18:47:40`).
`timestamp_unix` in the API is, as before, a whole integer.

**Your clients must tolerate an unknown field.** The location responses now carry
`timestamp_unix` alongside `timestamp`. The change is additive, so a client that ignores
unknown JSON fields is completely unaffected — but a client that *rejects* them throws on the
new key, breaks its location view, and produces **no server-side error at all**, so the server
log looks healthy while the app is broken. Beakon's own Android app only ignores unknown fields
from **v1.2** onward (`ignoreUnknownKeys` in `BeakonClient.kt`), so an instance serving an older
pinned APK — `companion-v1.0.2`, for example — will break that app the moment the server is
updated. Check the pin in `companion-apk.txt` and get the app to v1.2 or later first, or update
the server and the pin together. Newer builds are unaffected in either direction, because the
app falls back to `timestamp` when `timestamp_unix` is absent.

**A fresh install** gets the same schema from `db.create_all()` and writes epoch from the
start, so new and migrated databases agree.

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

# The date filter is the one that fails SILENTLY — check it, not just the column type.
# Pick a day you know has rows and confirm it returns them. With text data and new
# code this returns 0, and the page just looks empty. Adjust the bounds to your zone.
sqlite3 instance/beakon.db "SELECT COUNT(*) FROM locations_table
  WHERE timestamp >= strftime('%s','2026-10-20 00:00:00')
    AND timestamp <  strftime('%s','2026-10-21 00:00:00');"

# The lat/lon index is actually reachable (this needs ANALYZE to have been run).
# Expect: SEARCH locations_table USING INDEX idx_locations_lat_lon (lat>? AND lat<?)
# If it says ix_locations_table_userid instead, the planner has no statistics.
sqlite3 instance/beakon.db "EXPLAIN QUERY PLAN SELECT count(*) FROM locations_table
  WHERE userid=1 AND lat>=37.7 AND lat<=37.8 AND lon>=-122.5 AND lon<=-122.4;"
```

The `datetime(...,'unixepoch')` query converts the extremes back to readable UTC — if those
dates do not match the oldest and newest records you expect, the timezone decision in section 4
was the wrong one, and you should restore the backup and redo it. If the `EXPLAIN QUERY PLAN`
check names `ix_locations_table_userid` instead of `idx_locations_lat_lon`, run
`sqlite3 instance/beakon.db "ANALYZE;"` and check again.
