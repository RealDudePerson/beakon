# Beakon — Code Review

Scope: `master` @ `186e56a`. Review only; no code changed. Every claim below is
either marked **verified** (read in source and, where noted, reproduced) or
**suspected** (reasoning only, not confirmed).

Method: read all of `src/`, all 13 templates, `static/js/*.js`, `tests/`,
`setup.sh`, migrations, and the deployed DB/schema (`instance/beakon.db`,
read-only). Also read the companion client in `~/projects/beakon-android` to
check API interop. I did **not** start the service, and I did **not** read the
values in `config.cfg` (only confirmed which keys exist).

---

## 1. State of the app

**What it is.** A single-process Flask app that stores GPS fixes in SQLite and
serves them back on Leaflet/Mapbox maps. Auth is session-cookie based
(`Flask-Login`); the companion Android app and GPSLogger push fixes with
`username` + `secret` headers.

**Architecture.**
- All routing lives in `src/app.py` (~38 routes). Models in `src/models.py`,
  geofence evaluation in `src/geofencing.py`, a demo seeder in `src/seed.py`.
- `src/app.py:22` builds the app; `src/app.py:31-41` requires `config.cfg` and
  exits if absent (good — no placeholder secret). `src/app.py:51-75` is a
  hand-rolled `KEY = VALUE` loader (note: empty values are intentionally kept,
  `app.py:66-72`). Schema is created with `db.create_all()` at import
  (`app.py:83-85`); there is no migration runner.
- An APScheduler interval job runs `check_geofences` every
  `GEOFENCE_CHECK_INTERVAL` minutes (`app.py:88-92`). The job pushes webhooks to
  user-configured URLs on enter/leave transitions (`geofencing.py:50-101`).
- Talisman enforces HTTPS (`app.py:102-111`); the app itself runs the Werkzeug
  dev server with `ssl_context='adhoc'` (`app.py:978`) — self-signed TLS, which
  is why the nginx upstream must stay `https://` with verification off. Cookie
  flags are set at `app.py:95-99` (`Secure`, `SameSite=Lax`, `HttpOnly` is the
  Flask default and is not disabled).
- In-memory per-IP throttle at `app.py:122-133`, applied to `/login`,
  `/register`, and `/api/recordlocation`.

**Data model** (`models.py`): `users` (password + API-token hashes), `user_data`
(names + `is_admin`), `locations_table` (one row per fix, indexed on
`(userid, timestamp)`, `models.py:53-55`), `sharing_permission` (owner → grantee),
`known_places` (geofences + webhook config). The live DB is small (2 users, 97
locations, 0 places) so anything scale-related below is latent, not yet felt.

**Auth/authorisation.** `@login_required` on every page except `/login`,
`/register`, `/logout`, `/api/recordlocation` (`app.py:188-207` defines
`admin_required` and the JSON-returning `_api_login_required`). Sharing is
enforced on both the HTML map (`app.py:738`) and the JSON API
(`app.py:506-510`), and again in `/api/presence` (`app.py:943-947`). That is
consistent and looks correct.

**Positives worth stating plainly (verified):**
- Password and API tokens are hashed with Werkzeug's default scrypt
  (`models.py:22-32`; default method confirmed as `scrypt:32768:8:1`).
- No raw SQL anywhere — every query is ORM-parameterised, so **no SQL injection
  was found**.
- `REGISTRATION_ENABLED` fails closed: `app.config.get('REGISTRATION_ENABLED',
  False)` (`app.py:317`) and the loader leaves an empty value as `''` (falsy).
- Admin delete refuses self-delete and last-admin delete (`app.py:899-903`).
- The previously-reported Mapbox token issue is not repeated here; the token is
  origin-restricted and the code degrades cleanly when it is absent
  (`layout.html:56-67`, `app.py:285`).

**Robustness posture.** The app is functionally solid on the happy path, but
error handling is uneven: several JSON endpoints trust the shape of the request
body and turn missing keys into 500s, and two shared-IP effects (see B1/B2)
undermine the access controls. No XSS-to-session-hijack path was found, but the
CSP is weak (S5) and one template builds HTML by string concatenation (S4).

---

## 2. Bugs and correctness problems

Ordered by severity. "Verified" = reproduced by reading and/or running an
isolated snippet; line numbers are against `186e56a`.

### B1 — `/api/recordlocation` returns 500 for any account with no API token (**verified, high**)
`models.py:31-32` calls `check_password_hash(self.api_token_hash, api_token)`
with no null guard, and `app.py:428-430` calls it whenever a `username` header
resolves to a user and any non-empty `secret` is sent. Werkzeug's
`check_password_hash` starts with `pwhash.split("$", 2)` and only catches
`ValueError`; a `NULL` hash raises `AttributeError: 'NoneType' object has no
attribute 'split'`. I reproduced that exact exception with the pinned Werkzeug
(3.1.6), and the deployed DB copy has a user with a `NULL` `api_token_hash`.

Effect: `POST /api/recordlocation` with `username: <tokenless-user>` and any
`secret` header → unhandled 500. It also contradicts the comment at
`app.py:422-424` ("uniform 401 … no 500 oracle") and is a user-existence oracle:
unknown user → fast 401, tokenless user → 500, user-with-token → 401 after a
scrypt computation.

Confirm: `python -c "from werkzeug.security import check_password_hash as c;
c(None,'x')"` → AttributeError. Then send one POST with a tokenless username.

### B2 — The rate limiter keys on `request.remote_addr`, which behind nginx is the proxy's IP (**verified from topology, high**)
`app.py:223`, `322`, `419` build the throttle key from `request.remote_addr`.
The app is reached only through nginx on a **separate host** proxying to
`https://192.168.2.8:5000`, and per the hard constraints there is no `ProxyFix`.
A proxy on another host cannot preserve the client's source IP at the TCP layer,
so `remote_addr` is the nginx host for **every** client.

Consequences:
- `login:<proxy-ip>` is a single global bucket. Five failed logins by *anyone*
  (or one user fat-fingering their password five times) makes `/login` return
  429 for **all** users for 300 s (`app.py:224-225`). The admin cannot log in
  either.
- `recordlocation:<proxy-ip>` is likewise global: five bad API-auth attempts
  from any client block `/api/recordlocation` for everyone for 300 s —
  including the Android companion, whose only failure mode then is
  `SendResult.RateLimited`.
- The intended "5 per IP" control is therefore not per-attacker; it is a
  shared kill-switch.

I could not inspect the nginx config, so I cannot say whether it sets
`X-Forwarded-For`. The fix must read a forwarded header only from the trusted
proxy address (see Recommended fixes), because the app also listens on
`0.0.0.0:5000` and is reachable directly on the LAN, where such a header is
spoofable.

### B3 — `PUT /api/places` stores unvalidated types, which can break geofencing for everyone (**verified, high**)
`POST /api/places` validates (`app.py:551-560`: `float()`, range check,
`max(1, int(...))`), but `PUT` copies values straight through
(`app.py:570-576`) with no coercion or range check. SQLite has no strict typing:
I confirmed with an in-memory DB that a non-numeric string inserted into a
`FLOAT`/`INTEGER` column is stored as `TEXT` (numeric strings are coerced,
garbage is not).

`geofencing.py:66` then calls `haversine_meters(latest_loc.lat, …, place.lat, …)`,
and `math.radians('abc')` raises `TypeError`. The exception is **not** caught
(the only `try` is around the webhook POST, `geofencing.py:90-97`), so the whole
job run aborts at that place and no later user's geofences fire. One malformed
place is a cross-user geofence outage. The same bad row also 500s the owner's
dashboard (`app.py:155`) and `/api/presence` (`app.py:956`).

Confirm: `PUT /api/places {"id":N,"lat":"abc"}` (200), then wait for the
geofence job or `GET /api/presence`.

### B4 — `UnboundLocalError` → 500 on partial name/password updates (**verified, medium**)
`app.py:676-698`: `fname`/`lname` are only bound when present in the JSON, then
line 686 reads both unconditionally. A body of `{"lname":"x"}` raises
`UnboundLocalError`. Identical pattern for `current_password`/`new_password` at
`app.py:702-720` (a body with only one key raises at line 708). The shipped
`account.js` always sends both, so the UI never trips it, but the API accepts it.

Confirm: `POST /account/update_name` with `{"lname":"x"}` as a logged-in user.

### B5 — Missing JSON keys → 500 (should be 400) (**verified, medium**)
- `app.py:669`: `request_data['username']` in `remove_permission` — plain
  `KeyError` → 500.
- `app.py:845-846`: `admin_create_user` reads `data['username']`/`data['password']`
  → 500 on omission.

(For contrast, `request.form['…']` sites such as `app.py:226`/`395` raise
Werkzeug's `BadRequestKeyError`, which Flask maps to 400, not 500 — so those are
not bugs, just unhelpful.)

Confirm: `POST /account/remove_permission` with `{}`.

### B6 — Dashboard clobbers `username` with a sharer's name (**verified, medium**)
`app.py:240` sets `username = current_user.get_username()`, then the loop at
`app.py:259-261` reassigns `username = user.get_data_owner_username()` on every
iteration. The template is rendered with the *last sharer's* username, so
`dashboard.html:81` ("Tell them to add '{{username}}'") prints the wrong name to
any user who has someone sharing with them.

Confirm: log in as a user who has ≥1 incoming share and read the dashboard hint.

### B7 — Location ingest accepts non-numeric coordinates (**verified, medium**)
Neither `POST /recordlocation` (`app.py:363-368`) nor `/api/recordlocation`
(`app.py:431-436`) coerces or range-checks `lat`/`lon`. Same class as B3: a
string coordinate is stored as `TEXT` and then poisons the owner's dashboard and
the geofence job. `batt` is likewise unvalidated.

Confirm: `POST /recordlocation {"lat":"x","lon":"y"}` while logged in, then load
`/dashboard`.

### B8 — The "Update API Key" button in the web UI does nothing (**verified, medium**)
`account.html:163` is `<button type="button">` inside the `update_token` form
with no `id` and no handler; `static/js/account.js` wires every other account
button but not this one (grep: no `updatetoken` reference in `static/`). Clicking
is a no-op. The form may still submit via implicit Enter submission in some
browsers, but the advertised action is broken.

Confirm: open `/account`, type a key, click the button, watch no request.

### B9 — Deleting a user leaves orphaned `sharing_permission` rows, and SQLite reuses the id (**verified, medium**)
`models.py:19` defines a cascade only for `data_owner_id`; there is no
relationship for `shared_with_id`, and SQLite foreign keys are not enforced
(no `PRAGMA foreign_keys=ON`; confirmed via schema). So
`admin_delete_user` (`app.py:892-906`) deletes the user and their owned shares
but leaves rows where the deleted user was the grantee. SQLite's rowid reuse
means a newly created user can be assigned the deleted id and then pass the
`shared_with_id` checks at `app.py:506-508`, `738`, `943-945` — inheriting the
deleted user's incoming shares. The current DB has zero share rows, so this is
latent.

Confirm: create A→B share, delete B, create C, see whether C's id collides and
whether C can read A.

### B10 — An admin can demote the last admin (**verified, low**)
`app.py:875` sets `is_admin` from the request with no "last admin" guard, unlike
delete (`app.py:902`). A single-admin install can be locked out of `/admin`
entirely.

Confirm: `POST /admin/users/<id>/update {"is_admin": false}` on the only admin.

### B11 — Date-based views send time-only timestamps, so client clustering is broken (**verified, low**)
`app.py:484` passes `time_only=True` for `?date=`, which formats as `%H:%M:%S`
(`app.py:135-137`). `dashboard.html:160` / `map.html:101` do
`new Date(curr.timestamp)`; `new Date("12:30:00")` is `Invalid Date`, so
`timeDiff` is `NaN` and the "hide points <5 min apart" rule silently degrades to
distance-only. The stats panel also shows only a time with no date.

Confirm: pick a date on the dashboard and observe clustering behaviour / the
`timestamp` values in the `/api/locations?date=…` response.

### B12 — `POST /admin/users/create` is unreachable; `openAddModal` is dead (**verified, low**)
`admin.html` has no button that calls `openAddModal()` (`static/js/admin.js:62`),
so the route at `app.py:841-858` cannot be reached from the UI — admins can edit,
reset, and delete but not add.

### B13 — `/api/places/<id>/test` echoes the raw exception (**verified, low**)
`app.py:609-611` returns `{'error': str(e)}, 500`, leaking internal exception
text (URLs, resolver errors) to the caller. It is also the oracle half of the
SSRF finding (S1).

### B14 — Minor / nits (**verified, low**)
- `_attempts` (`app.py:122-133`) only prunes a key when that key is checked
  again; keys never revisited stay forever. Unbounded (slow) growth.
- `app.py:393, 467, 502, 621, 642, 734` call `.first().get_username()` with no
  `None` check; a session referencing a since-deleted user 500s.
- `update_token` (`app.py:389-405`): GET returns a 200 text body; `if api_token
  is not None` is always true; no minimum length or entropy check on the token.
- `registration_closed.html:17` has a stray `</div>`.

---

## 3. Security findings

### S1 — SSRF via `webhook_url`, with a status-code oracle (**verified, medium-high**)
Any authenticated user sets `webhook_url` (`app.py:555`, `561`) and the server
POSTs attacker-controlled JSON to it from `geofencing.py:91` and
`app.py:607`. There is no scheme/host validation, and `requests` follows
redirects by default. On a LAN deployment this reaches loopback, RFC1918 hosts,
and link-local metadata endpoints. `/api/places/<id>/test` returns the
downstream status (`app.py:608`), turning it into a service/port prober. The
README assumes "registered users are good actors", but registration is the only
barrier, so a single account is enough. (This was raised in `improvements.md`
SEV-4 and remains unfixed.)

### S2 — User enumeration via timing and the B1 500 (**verified, medium**)
For a valid username the server runs scrypt before returning 401; for an invalid
username it returns immediately. The tokenless-account 500 (B1) is an even
cleaner oracle. `app.py:422-424` explicitly claims this is not an issue; it is.

### S3 — No CSRF tokens on cookie-authenticated POSTs (**verified, low, mitigated**)
There is no `Flask-WTF`/`CSRFProtect` and no token on any state-changing route
(`/account/<action>`, `/api/places`, `/admin/*`, `/api/updatetoken`).
`SESSION_COOKIE_SAMESITE='Lax'` (`app.py:96`) blocks cookies on cross-site POSTs
in current browsers, so classic CSRF is largely neutralised — but `Lax` does not
cover same-site subdomains, and `/logout` is a state change on GET
(`app.py:306-309`). Not exploitable today as far as I can tell; noted for
completeness.

### S4 — Stored self-XSS in the Known Places table (**verified, low**)
`static/js/account.js:265-278` builds rows with `innerHTML +=` and interpolates
`place.name` and `place.webhook_url` unescaped; the `onclick` handlers escape
only `'`, so a name containing `"` also breaks out of the attribute. CSP allows
inline handlers (S5), so a payload executes. Impact is limited: places are
per-user and rendered only on the owner's own account page, so this is
self-XSS, not cross-account. Still a real injection bug (and `account.js:273`
also omits a closing quote in one handler).

### S5 — Weak CSP: `script-src 'unsafe-inline' 'unsafe-eval'` (**verified, low**)
`app.py:105`. Documented as a Flatpickr/legacy-JS necessity, but it removes the
CSP backstop for S4 and any future injection. Inline scripts are spread across
every template (`dashboard.html:89`, `map.html:54`, `presence.html:38`,
`speed.html:66`, `account.html:235`).

### S6 — No password policy; empty passwords accepted (**verified, low**)
Registration has no length/complexity check (`app.py:327-346`), password change
accepts anything (`app.py:702-714`), and `admin_create_user` will store an empty
password if the JSON provides `""` (`app.py:851`). Usernames are validated only
at `/register` (`app.py:329`); `admin_create_user` does not validate them at all.

### S7 — Error paths that are not actionable (**verified, low-medium; violates constraint #4**)
Failed login renders the login page with no message — the user cannot tell
"wrong password" from "server broken" (`app.py:218-233`, `login.html` has no
error region). `abort(429)` (`app.py:225`, `324`) and `abort(403)`
(`app.py:195`) return Flask's generic HTML. The 429s also carry no `Retry-After`
header, even though the Android client parses one
(`BeakonClient.kt:195-196`).

### S8 — No request-size limit (**verified, low**)
`MAX_CONTENT_LENGTH` is unset, so an oversized JSON body is buffered before
parsing. Trivial DoS surface on a dev server.

### S9 — Audit log shows payloads, and one debug line logs request bodies (**verified, low**)
`/admin/audit` renders the last 100 log lines (`app.py:830-839`). `app.py:865`
logs the full `admin_update_user` payload at DEBUG, which for the admin UI
includes a `password` field. Only admins see it, and the field is blanked in the
edit dialog, but it is unnecessary exposure.

### Accepted risks (not counted as findings)
Per the README threat model and by design: API token sent as a plaintext header
over TLS (stored hashed); webhook payloads include `fname`/`lname`; no 2FA/email
verification; first registered user becomes admin.

---

## 4. Recommended fixes

Each item: **effort** S/M/L, **live risk**, and whether it is safe to apply to
the running service. Nothing here violates the hard constraints (no ProxyFix, no
plaintext upstream, registration stays closed, fail-closed).

1. **Guard null token hashes (B1).** In `UserModel.check_api_token` return
   `False` when `api_token_hash` is falsy (mirroring the `if not user: 401`
   pattern). *Why:* removes an unauthenticated 500 and the enumeration oracle.
   **S. Risk: very low. Live-safe** (code change + restart).
2. **Make the throttle see real clients (B2).** Do not add `ProxyFix`. Add a
   helper that returns `request.remote_addr` unless it equals the trusted nginx
   address, in which case use the last hop of `X-Forwarded-For`; wire it into
   `app.py:223/322/419`. Configure nginx to set `X-Forwarded-For`. *Why:* turns
   a global kill-switch back into a per-client limit and fixes the Android
   lockout. **M. Risk: medium** — a spoofed header is possible for anything that
   can reach `:5000` directly, so the trusted-proxy check matters. **Live-safe
   if nginx is updated in the same window;** otherwise keep the current
   behaviour for direct connections.
3. **Validate and coerce on write (B3, B7).** Route all lat/lon/radius/batt
   writes through one validator (finite float, lat ∈ [-90,90], lon ∈
   [-180,180], radius ≥ 1) for `/api/places` PUT as well as POST, and for both
   location ingest paths; reject with 400. Additionally wrap the per-place body
   in `check_geofences` in `try/except` so one bad row cannot abort the batch
   (`geofencing.py:57-99`). *Why:* closes a cross-user geofence outage.
   **S. Risk: low. Live-safe.**
4. **Fix the partial-payload 500s (B4, B5).** Initialise `fname`/`lname`/
   `current_password`/`new_password` to `None`, use `data.get(...)`, and return
   400 with a short message on missing fields. *Why:* actionable errors
   (constraint #4). **S. Risk: low. Live-safe.**
5. **Stop clobbering `username` (B6).** Rename the loop variable in
   `app.py:259-261`. *Why:* correct sharing hint. **S. Risk: none. Live-safe.**
6. **Restrict webhooks (S1).** Require `https`, resolve the host, and reject
   loopback/private/link-local ranges; make `/test` return only pass/fail rather
   than the downstream status. *Why:* removes SSRF and the probe oracle.
   **M. Risk: low-medium** (could block a legitimate LAN webhook — confirm with
   the user first). **Live-safe.**
7. **Delete-user hygiene (B9).** Add a `shared_with` relationship with
   `delete-orphan` cascade (or a manual delete in `admin_delete_user`), and stop
   reusing ids by giving `users.id` `AUTOINCREMENT` in a migration. *Why:*
   prevents silent permission inheritance. **M. Risk: low. Live-safe** (the
   migration is an `ALTER`/rebuild; do it in a maintenance window).
8. **Last-admin guard (B10).** Reuse the `app.py:902` check in
   `admin_update_user` before clearing `is_admin`. **S. Risk: none. Live-safe.**
9. **Fix the dead API-key button (B8)** and add an "Add User" button wired to
   `openAddModal` (B12). *Why:* two advertised features are non-functional.
   **S. Risk: none. Live-safe.**
10. **Emit `Retry-After` on 429** and return a small JSON/HTML message instead
    of bare `abort(429)` (S7). *Why:* the Android client already reads it
    (`BeakonClient.kt:195-196`), and users get actionable feedback. **S. Risk:
    none. Live-safe.**
11. **Escape place data in `account.js` (S4)** by building rows with
    `textContent`/`createElement` (or `data-*` + listeners) instead of
    `innerHTML` string concatenation. **S. Risk: low. Live-safe.**
12. **Add CSRF protection (S3)** — Flask-WTF `CSRFProtect`, with the token
    injected into the existing fetch calls and the two forms. *Why:* removes
    reliance on `SameSite=Lax` alone. **M. Risk: medium** (every POST call site
    must send the token or it will break). **Live-safe only with the full
    frontend change in one deploy.**
13. **Set `MAX_CONTENT_LENGTH`** (S8) and add a password minimum length (S6).
    **S. Risk: low. Live-safe.**
14. **Trim debug logging of request bodies** (`app.py:865`) and optionally
    redact the audit view (S9). **S. Risk: none. Live-safe.**

Longer-term (not urgent): move inline JS to static files and drop
`'unsafe-inline'` (S5); replace the hand-rolled config parser with
`app.config.from_pyfile`; pin `requirements.txt`; decide on a location-retention
policy; standardise on UTC timestamps.

---

## 5. Feature ideas

1. **Per-token API keys (Android companion).** Today a user has exactly one
   `secret` and rotating it kills every client at once (`/api/updatetoken`
   overwrites it). Support multiple named tokens with a last-seen timestamp so a
   phone and GPSLogger can coexist and be revoked independently. Directly helps
   the companion's Setup screen and removes the "rotate breaks everything" trap.
2. **`Retry-After` + a machine-readable 429 body.** The Android client already
   has `RateLimitBackoff`/`parseRetryAfter` (`BeakonClient.kt:138,195-196`);
   the server never sends the header. Pairing these makes backoff exact instead
   of guessed.
3. **Server-side location upload batching / `If-None-Match` polling.**
   `/api/locations` returns the whole filtered set every 10 s
   (`dashboard.html:264`). An ETag or `?since=<timestamp>` delta would cut
   bandwidth for both the browser and the companion's history view.
4. **Retention + "delete data older than N days"** exposed in `/account`
   (alongside the existing "delete all", `app.py:721-725`). Value: limits the
   blast radius of a stolen DB and keeps SQLite small.
5. **Geofence webhook retry** with backoff (already in `TODO.md`): on failure,
   `is_inside` is set before the POST and never retried until the user leaves and
   returns (`geofencing.py:69-99`). A bounded retry queue would make arrival
   notifications reliable.
6. **Per-geofence notification history** in `/admin/health`, building on the
   existing `last_webhook_time`/`last_webhook_status` columns.
7. **Export/import of known places** as JSON, so the companion (or a second
   browser) can restore geofences after a reinstall.
8. **Non-map fallback for `/dashboard`** — the app already degrades gracefully
   without a Mapbox key (`layout.html:56-67`); adding a simple coordinate list
   would make that degradation actually useful.

---

## What I could not determine

- **The nginx config.** I cannot confirm whether it sets `X-Forwarded-For` or
  `X-Real-IP`; the correct fix for B2 depends on it. The B2 conclusion holds
  regardless, because a different-host proxy cannot preserve the client source
  IP.
- **Whether production still has a tokenless account.** I verified the code path
  and found a `NULL api_token_hash` in the local `instance/beakon.db` copy, but
  that file may not be the production DB.
- **Whether `REGISTRATION_ENABLED` is currently `False` in the deployed
  `config.cfg`.** I confirmed the key exists but deliberately did not read its
  value. The code is fail-closed either way.
- **`config.cfg` contents generally** (secret key, DB URI, Mapbox key) — not
  inspected.
- **Whether the self-signed cert is regenerated per restart** — `app.run(ssl_context='adhoc')`
  (`app.py:978`) implies yes, but I did not start the service to observe it.
