# Tier 3 — beakon

Source of truth: `REVIEW.md` in this repo. The line numbers are against master at
the time of writing; re-check them, the file has moved since the review.

Implement **only** the items below. Do not re-do Tier 1 or B2 — they are already
merged and deployed.

## Hard constraints — violating any fails the task

1. Do NOT add `ProxyFix`. Do NOT change the nginx upstream away from `https://`.
   The app is TLS-only and Talisman has `force_https` on.
2. Do NOT change `REGISTRATION_ENABLED` semantics — fail-closed, False in production.
3. **Do NOT restrict webhook destinations.** Local/LAN webhook targets are
   **intentional and in use** — the owner has explicitly said so. S1 is wontfix.
   Do not touch webhook URL handling.
4. Do NOT change `TRUSTED_PROXY_IPS` handling or the `_client_address()` helper.
5. Never fail silently — every rejection gets an actionable message.
6. Do NOT read, log, or print secret values from `config.cfg`.
7. Do NOT modify any file outside this repo.

## The work

### 1. S4 — stored XSS in `account.js`
The known-places list is built by concatenating user-controlled `name` /
`webhook_url` into `innerHTML`. A place name containing markup executes in the
owner's browser. Rebuild those rows with `textContent` / `createElement` (or
`data-*` attributes plus event listeners) so no user value is ever parsed as HTML.
Cover every user-controlled field rendered there, not just `name`.

### 2. S9 — stop logging request bodies
`app.py` logs request bodies at DEBUG (around the record-location path). Bodies
contain coordinates and tokens. Log a redacted summary instead — method, route,
status, and which fields were present — never the values. Keep the rest of the
logging as-is.

### 3. B9 — delete-user hygiene and id reuse
Two distinct problems:
- `admin_delete_user` leaves rows behind: `sharing_permission` rows referencing the
  deleted user survive, and `users.id` values are **reused** by SQLite, so a new
  account can silently inherit a departed user's sharing permissions.
- Fix both: clean up every referencing row on delete (in one transaction), and give
  `users.id` `AUTOINCREMENT` so ids are never reused.

The `AUTOINCREMENT` change needs a **table rebuild** — SQLite cannot add it with
`ALTER`. Requirements for the migration:
- Take a **timestamped backup of `instance/beakon.db` first** and print its path.
- Rebuild inside a transaction with foreign keys handled correctly, preserving every
  existing row and id exactly. Existing `locations_table.userid` / `known_places.userid`
  values must still resolve.
- Verify afterwards: the row counts per table are unchanged, `daniel` (id 1) still
  has its locations, and `sqlite_sequence` now tracks `users`.
- **Prove it is safe to re-run**: running the migration twice must be a no-op the
  second time, not an error and not data loss.

### 4. Pin `requirements.txt`
The file is unpinned, and it also **omits `pyOpenSSL`**, which the app needs for
`ssl_context='adhoc'` — a clean install cannot start without it. Pin every
dependency to the version currently installed in `.venv`, and add `pyOpenSSL`.
Keep the file readable (one per line, sorted).

### 5. Replace the hand-rolled config parser with `app.config.from_pyfile`
The current loader must keep its **one critical behaviour**: a key that is *named*
must be set even when its value is empty. `setup.sh` writes `MAPBOX_API_KEY = ''`,
and the original bug was that an empty value meant the key was never added at all,
causing `KeyError` → HTTP 500. `from_pyfile` sets named keys regardless of value, so
it preserves this — but **verify it explicitly** with a test, because this is the
exact regression the project already suffered once.

Keep the `BEAKON_CONFIG` env override working (the tests depend on it).

### 6. CSP — move inline JS to static files and drop `'unsafe-inline'`
The CSP currently needs `'unsafe-inline'` because templates contain inline
`<script>` blocks and inline `on*=` handlers.

- Move inline `<script>` bodies into files under `static/js/` and load them with
  `<script src=...>`.
- Replace inline `on*=` attributes with `addEventListener` in those files. Pass any
  server data they need via `data-*` attributes or a single JSON blob in a
  `<script type="application/json">` element.
- Then remove `'unsafe-inline'` from `script-src` (and from `style-src` only if you
  have genuinely removed all inline styles — if not, leave `style-src` alone and say so).

**This is the highest-risk item: I cannot click through the UI to verify it.** So
verify it by exhaustive grep instead, and report the evidence:
- every remaining `on(click|change|submit|input|load|error|keyup|keydown|focus|blur)=`
  in `templates/`
- every remaining inline `<script>` with a non-empty body
Both lists must be empty (or every survivor justified in writing).

### 7. Retention — implement the mechanism, default OFF
Add a config-driven retention setting (e.g. `RETENTION_DAYS`, default **0/empty =
keep everything**) plus the plumbing to delete locations older than N days. Expose
it on `/account` only if it can be done without ambiguity; otherwise implement the
server-side mechanism and leave the UI alone, saying why.
**Do not delete any data by default, and do not run a purge against the live DB.**
The policy choice is the owner's; your job is the mechanism and its tests.

## Out of scope — do NOT do these
- **UTC timestamps.** Changing the meaning of stored/displayed times is a
  data-semantics decision for the owner, not an implementation detail. Leave it.
- Anything webhook-related (S1). Anything CSRF-related (S3). Any Tier 1 or B2 item.

## Acceptance criteria — run these yourself and report the real output

1. **Full suite passes.** Report the count. It was 38 before this work.
2. **S4:** add a test that a place whose `name` contains `<img src=x onerror=alert(1)>`
   is rendered as **text** in the generated markup, not as an element. Prove it
   red-capable.
3. **S9:** add a test that a logged record-location request does **not** contain the
   submitted coordinates or token value in the emitted log record. Prove it red-capable.
4. **B9:** add a test that deleting a user removes its `sharing_permission` rows and
   leaves no orphans. Prove it red-capable.
5. **Migration:** run it on a **copy** of the DB. Show before/after row counts per
   table, that ids are unchanged, that `sqlite_sequence` tracks `users`, and that a
   second run is a clean no-op. Report the backup path.
6. **Requirements:** `pip install -r requirements.txt` into a **throwaway venv**
   succeeds and `python -c "import OpenSSL"` works — proving the file is now
   installable and complete. Report the command output.
7. **Config:** a test proving that a config file naming `MAPBOX_API_KEY = ''` still
   results in `app.config['MAPBOX_API_KEY'] == ''` (present, not missing). Prove it
   red-capable by reintroducing the original `if key and value:` condition.
8. **CSP:** the two grep lists from item 6 are empty, with the exact command and
   output reported. State plainly that a browser click-through was **not** performed.
9. **Retention:** with the setting unset/0, no purge occurs (test it). With it set,
   the purge removes only rows older than the cutoff and nothing else. Prove the
   "unset = no purge" case red-capable.
10. **No regressions:** login, dashboard, record-location, `/api/locations`,
    `/api/presence`, places CRUD, and the admin pages still work.

## Rules
- Work on branch `fix/tier3-longer-term`. Commit there. Do NOT merge to master and
  do NOT deploy — the owner deploys.
- Do NOT run the live service, and do NOT touch the live `instance/beakon.db`
  (work on a copy for the migration).
- Prove each new test can fail (reintroduce the bug, show red, restore). Report the
  observed failures.
- Report honestly: what passed, what you could not verify, and anything you chose
  differently and why.
