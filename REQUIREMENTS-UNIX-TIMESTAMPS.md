# Requirements — store timestamps as Unix epoch (server + Android app)

Status: approved for build. Owner decision recorded below.

## Goal

Store location and webhook times as **Unix epoch seconds (UTC)** instead of naive local
`DATETIME` text, so that:

- stored times are unambiguous and comparable across timezone/DST changes and hosts;
- `format_timestamp()`'s `datetime.now() - ts` arithmetic stops being wrong across a DST
  boundary;
- clients can compute ages themselves instead of parsing a display string.

## The decision that had to be made first

Existing values were written by `datetime.now()`, so they are **naive local time** with no
timezone recorded. Converting them requires interpreting them as local time in the host's
timezone — **not** as UTC. Read as UTC, every row would land 7 hours off on this host
(`2026-10-23 18:47:40.278027` → `1792781260` read as UTC vs `1792806460` read as local).

Decision: **interpret existing values as local time in the host's timezone**, and use
`zoneinfo` so DST is applied per row rather than using today's offset for every row.

## Non-goals

- No change to what any client *displays*. The API keeps emitting the same `timestamp`
  display strings, so the deployed Android app keeps working untouched.
- No change to the location payload clients send (`lat`/`lon`/`acc`/`batt`); the server
  assigns the time, as it does today.
- No paid-exit style rewrite: this is a storage and contract-hygiene change.

## The compatibility contract (this is the part that must not break)

`timestamp` is currently a **human string**: `"just now"`, `"5 minutes ago"`,
`"3 hours ago"`, or `str(ts)` (e.g. `2026-10-23 18:47:40.278027`) for anything older than a
day. Three consumers depend on that:

| Consumer | How it uses it | Must keep working |
|---|---|---|
| Android app (`Format.serverTime`) | Displays the string verbatim; also a dedupe key in HistoryScreen | Yes — no app release required |
| `static/js/dashboard.js`, `map.js` | Displays it, and does `new Date(loc.timestamp)` for gap math | Display yes; Date math becomes correct |
| `static/js/speed.js`, `shared_map.js` | Display / speed math | Yes |

**Therefore: the strings must stay byte-identical.** In particular `str(ts)` must not become
`str(1792781260)`.

## Design

### 1. Storage

- `LocationsModel.timestamp` → `db.Column(db.Integer)`; the `idx_userid_timestamp` composite
  index stays as-is.
- `KnownPlaceModel.last_webhook_time` → `db.Column(db.Integer)`.
- Both hold **epoch seconds, UTC**.

### 2. A tolerant reader (this is what makes the upgrade safe)

Add one conversion helper and route **every** read through it:

```python
def to_epoch(value):
    """Epoch seconds from an int, a naive datetime (interpreted as local), or text."""
```

It must accept:
- `int` / `float` → returned as-is (already migrated);
- `datetime` → `.timestamp()` treating naive values as local;
- `str` → parsed, then treated as local;
- `None` → `None`.

**Why this matters:** it keeps per-row rendering working against both formats, so a mistake in
sequencing degrades rather than corrupts. It is **not** a licence to deploy the code without
the data: two SQL-level comparisons still run against the raw column and SQLite sorts TEXT
above numbers, so with new code and text data the day-range filter silently matches nothing
(measured: 0 rows for 2026-10-20 against text, 789 after the migration). See the correction in
`MIGRATIONS.md`.

Writers use one helper too: `now_epoch()` → `int(datetime.now().timestamp())`.

### 3. Readers to update (all of them)

Every site that assumes a `datetime`:

- `format_timestamp()` — must produce the identical strings (see the contract above).
  `time_only` → local `HH:MM:SS`; relative branches unchanged; the older-than-a-day branch
  must render the local datetime, not the integer.
- The pruning cutoff (`_prune`/`days` comparison) → compare epoch to epoch.
- The day-range filter (dashboard date picker) → compute local day bounds, then compare as
  epoch. "View a specific day" must keep meaning *local* days.
- Ordering by `timestamp` — unaffected (integers sort correctly), but the `.desc()`/`.asc()`
  sites should be reviewed.
- `/api/presence` — day bucketing must stay **local**; `first`/`last` output must remain
  `%Y-%m-%d %H:%M:%S` local strings.
- The template value passed as `timestamp` (dashboard/map "Last updated") and
  `last_loc_time` on the admin/health view → must render as they do today.
- `templates/admin_health.html` renders `place.last_webhook_time` raw. As a datetime it
  prints `2026-10-23 18:47:40.278027`; as an epoch integer it would print `1792781260`.
  Format it so the page does not regress.

### 4. API — additive only

Keep `timestamp` exactly as-is and **add** `timestamp_unix` (integer epoch seconds) to:

- `/api/locations`
- `/api/locations/<map_username>`
- `/api/locations/shared` — both `latest` and each `trail` point
- `/api/presence` — alongside `first`/`last` (as `first_unix`/`last_unix`)

`age_seconds` on the shared endpoint stays and is computed from epoch.

Additive means: an old client ignores the new field, a new client falls back to the string if
the field is absent. Both directions work.

### 5. GUI

- `dashboard.js` / `map.js`: use `timestamp_unix` for the `new Date(...)` gap math. Today
  that code receives a display string, so `new Date("5 minutes ago")` is `NaN` and the gap
  rule silently does nothing — using the epoch field fixes it. Display strings unchanged.
- `speed.js`: same treatment if it parses the string for timing.
- `shared_map.js`: unchanged (displays the string).

### 6. Android app

Additive and backward compatible:

- `Models.kt`: add `timestampUnix: Long? = null` mapped from `timestamp_unix`.
- Keep displaying the server's `timestamp` string (unchanged behaviour when the field is
  missing).
- Where a real age is wanted, compute it from `timestampUnix` when present.
- `HistoryScreen`'s dedupe key must not break when only one of the two fields exists.

The app must work against **both** an old server (no `timestamp_unix`) and a new one.

### 7. Migration

`migrations/005_timestamps_unix_epoch.py`, following the conventions of
`003_users_autoincrement.py`:

- positional db path argument, defaulting to `instance/beakon.db`;
- writes a timestamped backup beside the target **before** any change and prints the path;
- converts `locations_table.timestamp` and `known_places.last_webhook_time` from text to
  integer epoch using `zoneinfo` with the host timezone by default, overridable with a
  timezone argument so the choice is explicit and reproducible;
- **idempotent**: rows already integers are skipped, so a re-run is a no-op;
- `--dry-run` that prints the first and last conversion without writing;
- verifies row counts before and after, and prints a sample of before/after values;
- rebuilds the two tables so the **declared** column type is `INTEGER`, matching what
  `db.create_all()` produces on a fresh install (SQLite does not enforce types, so the data
  would work without this — but a migrated database should not carry a schema that disagrees
  with a fresh one). The `idx_userid_timestamp` index must be recreated.

### 8. Upgrade procedure

**The code and the data must change together.** Not "deploy the code first": with new code and
text data the day-range filter silently returns nothing (measured above), and with old code and
integer data timestamps raise `TypeError`. Either half-state is broken, so close the window:

1. Back up (see `MIGRATIONS.md`).
2. Stop the service.
3. Run the migration.
4. Start the service on the new code.
5. Verify (section 9). Rollback is restoring the backup.

## Acceptance criteria

1. `python -m pytest -q` passes with **more** tests than before, including new tests for:
   `to_epoch()` on int/datetime/text/None; `format_timestamp()` producing identical strings
   for the same instant before and after migration; the day-range filter still selecting
   local days; `/api/presence` still bucketing by local day.
2. A round trip on a **copy** of the real database: migrate, then confirm the API's
   `timestamp` strings are unchanged for the same rows, and `timestamp_unix` matches the
   expected epoch.
3. The migration is idempotent — running it twice changes nothing the second time.
4. `to_epoch()` handles a database that has not been migrated (text) and one that has (int).
5. No endpoint loses a field, and every existing field keeps its format.
6. The Android app compiles, and its tests pass, against a server that omits
   `timestamp_unix` (i.e. the fallback path is exercised).

## Verification

- Server: full pytest run; a migration rehearsal against a copy; diff the JSON of
  `/api/locations` and `/api/locations/shared` before/after migration to prove the `timestamp`
  strings are unchanged.
- App: `./gradlew test` and `./gradlew assembleDebug` with the same JDK/ANDROID_HOME as
  `~/projects/beakon-android`'s existing builds.
- Report explicitly: what was verified, and that nothing has been run against a real device.

## Amendment

The owner chose **pure integer seconds** over preserving sub-second precision. The migration
therefore truncates every converted value toward zero and stores an INTEGER epoch, so no
stored value is a fractional REAL. This supersedes the byte-identical-string contract above:
a `timestamp` display string for a location older than a day loses its sub-second part
(`2026-10-23 18:47:40.278027` becomes `2026-10-23 18:47:40`). `to_epoch()` stays tolerant and
may still accept a float; only the stored value becomes an integer.
