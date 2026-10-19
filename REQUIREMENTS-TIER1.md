# Tier 1 fixes — beakon

Source of truth for the findings: `REVIEW.md` in this repo. Read it first. The
line numbers below are against `master` @ `186e56a`.

Implement **only** the items in this file. Do not do Tier 2/3 work (no CSRF, no
webhook SSRF restriction, no id `AUTOINCREMENT` migration, no CSP changes, no
`ProxyFix`).

## Hard constraints — violating any of these fails the task

1. Do NOT add `ProxyFix`. Do NOT change the nginx upstream away from `https://`.
   The app is TLS-only and Talisman has `force_https` on; terminating TLS upstream
   causes a redirect loop.
2. Do NOT change `REGISTRATION_ENABLED` behaviour — it must stay fail-closed
   (`app.config.get('REGISTRATION_ENABLED', False)`), and production has it False.
3. Do NOT touch `config.cfg` or any live secret.
4. **Never fail silently.** Every rejection must return an actionable message the
   caller can act on, not a bare status or a 500.
5. Do NOT modify any file outside this repo.
6. Do NOT read or log secret values.

## The work

### 1. B1 — null API-token hash causes an unhandled 500
`models.py:31-32` — `check_api_token` calls `check_password_hash(self.api_token_hash, …)`
with no guard. A user with `api_token_hash = NULL` makes Werkzeug raise
`AttributeError: 'NoneType' object has no attribute 'split'` (reproduced).
`app.py:428-430` reaches it whenever a `username` header resolves to a user and a
non-empty `secret` is sent.

Fix: in `UserModel.check_api_token`, return `False` when `api_token_hash` is falsy.
The endpoint must then behave exactly like a wrong token (401), with no 500 and no
timing/status oracle distinguishing "tokenless account" from "unknown user".

### 2. B3 + B7 — unvalidated writes can break geofencing for everyone
`PUT /api/places` (`app.py:570-576`) copies `lat`/`lon`/`radius`/`webhook_url`
straight through with no coercion or range check, while the `POST` branch
(`app.py:551-560`) does validate. Location ingest (`app.py:363-368` for
`/recordlocation` and `app.py:431-436` for `/api/recordlocation`) likewise accepts
non-numeric `lat`/`lon`/`batt`.

SQLite stores garbage in a FLOAT/INTEGER column as TEXT; `geofencing.py:66` then
calls `haversine_meters(…, place.lat, …)` → `math.radians('abc')` raises
`TypeError`, and the only `try` in that file wraps the webhook POST
(`geofencing.py:90-97`), so **one bad row aborts the whole job run for all users**.

Fix:
- One shared validator (finite float; lat ∈ [-90, 90]; lon ∈ [-180, 180];
  radius ≥ 1 integer; `batt` numeric if present) applied to `PUT /api/places` and
  to both location-ingest paths. Reject with **400 and a message naming the bad
  field**.
- Wrap the per-place body of `check_geofences` (`geofencing.py:57-99`) so one
  malformed row is logged and skipped rather than aborting the run for everyone.
- Existing valid data must be unaffected; do not migrate or rewrite stored rows.

### 3. B4 + B5 — partial payloads produce 500s instead of 400s
- `app.py:676-698` — `fname`/`lname` are only bound when present, then read
  unconditionally at line 686 → `UnboundLocalError` on `{"lname":"x"}`.
- `app.py:702-720` — same pattern for `current_password`/`new_password` at 708.
- `app.py:669` — `request_data['username']` → `KeyError` → 500.
- `app.py:845-846` — `admin_create_user` reads `data['username']`/`data['password']`.

Fix: use `.get(...)`, validate, and return **400 with an actionable message** for
missing/blank fields. Preserve the existing success behaviour and the existing
validation messages where they already exist.

### 4. B6 — dashboard shows a sharer's username instead of the user's
`app.py:240` sets `username = current_user.get_username()`, then the loop at
`app.py:259-261` reassigns `username = user.get_data_owner_username()` each
iteration, so `dashboard.html:81` prints the **last sharer's** name to anyone who
has someone sharing with them.

Fix: stop clobbering the variable (rename the loop local). The dashboard hint must
show the **logged-in user's own** username.

### 5. B10 — an admin can demote the last admin
`admin_delete_user` already refuses to delete the last admin (`app.py:899-903`),
but `admin_update_user` can clear `is_admin` with no such guard.

Fix: apply the same last-admin protection before clearing `is_admin`, and return an
actionable error rather than silently demoting.

### 6. B8 + B12 — two advertised features are non-functional
- `account.html:163` — the "Update API Key" button is `type="button"` inside the
  `update_token` form, so it never submits.
- `POST /admin/users/create` is unreachable because `openAddModal` is dead
  (`app.py:845` area / the admin template).

Fix both so the controls actually perform their action. Keep the existing styling
and layout.

### 7. S7 — emit `Retry-After` on 429, with a usable body
The throttle currently `abort(429)`s. The Android client already parses the header
(`BeakonClient.kt:195-196`).

Fix: return **429** with a `Retry-After` header (seconds until the window clears)
and a short human-readable message on the HTML routes and a small JSON body on the
API routes.

### 8. S8 + S6 — request-size cap and password minimum
- Set `MAX_CONTENT_LENGTH` to a sane value (choose one and justify it) so oversized
  bodies are rejected with 413 rather than buffered.
- Enforce a minimum password length on registration and password change, with an
  actionable message. Do NOT apply it to the API `secret`/token path (the Android
  client's format must keep working), and do NOT retroactively reject existing
  passwords.

## Acceptance criteria — run these yourself and report the real output

All of the following must hold. Where a test is behavioural, actually exercise the
endpoint (the existing suite under `tests/` shows the pattern; the app supports
`BEAKON_CONFIG` to point at a temp config file).

1. **Existing tests still pass.** Run the full suite; report the count.
2. **B1:** a request with `username: <tokenless-user>` + any `secret` returns
   **401**, not 500. Add a regression test asserting the status and that no
   exception escapes.
3. **B1 oracle:** the response for a tokenless account is indistinguishable in
   status from an unknown user (both 401).
4. **B3:** `PUT /api/places` with `{"id":N,"lat":"abc"}` returns **400** and the
   stored row is unchanged. Add a regression test.
5. **B3 resilience:** with a deliberately malformed row present, `check_geofences`
   completes and still evaluates other users' places (no aborted run). Add a test.
6. **B7:** `POST /api/recordlocation` with `lat:"x"` returns **400** and stores
   nothing.
7. **B4:** `POST /account/update_name` with `{"lname":"x"}` returns **400**, not 500.
8. **B5:** `POST /account/remove_permission` with `{}` returns **400**, not 500.
9. **B6:** the dashboard rendered for a user with an incoming share contains that
   user's **own** username. Add a test.
10. **B10:** demoting the only remaining admin is refused with an actionable error.
11. **429:** a throttled response carries a `Retry-After` header and a non-empty body.
12. **Oversized body:** a body above `MAX_CONTENT_LENGTH` returns **413**.
13. **Short password:** registering/changing to a too-short password is rejected
    with an actionable message; a valid one still works.
14. **No regressions in the happy path:** login, dashboard, record-location,
    `/api/locations`, `/api/presence`, and the places CRUD all still work.

## Rules

- **Prove the tests can fail.** For each new regression test, temporarily
  re-introduce the bug, show the test goes red, then restore. Report which ones you
  did this for and the observed failure.
- Work on a branch named `fix/tier1-review-findings`. Commit there. Do NOT merge to
  master and do NOT deploy.
- Do NOT run the live service. Tests only.
- Report honestly: what passed, what you could not verify, and anything you chose
  to do differently and why.
