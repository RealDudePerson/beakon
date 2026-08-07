# Beakon — Findings

Full-repo review (security, bugs, ops hygiene). Ranked within each section; triage top-down.
Each finding: location → what → why → fix. No fixes applied in this pass — report only.

---

## Security

### SEV-1 — Stored XSS in admin panel via user-controlled name/username
`templates/admin.html:43`
User fields are interpolated into a JS string inside an `onclick` attribute:
`onclick="openEditModal({id: {{user.id}}, username: '{{user.username}}', fname: '{{user.fname}}', ...})"`.
Jinja autoescape HTML-escapes, but the HTML parser entity-decodes attribute values *before* the JS runs, so a single quote breaks out of the JS string. `fname`/`lname` are set via `/account/update_name` with zero validation (`src/app.py:601-626`), and username charset is unvalidated at register (`src/app.py:275`). A registered user sets their name to a payload; when an admin opens `/admin`, the injected JS runs in the admin's browser. CSP `'unsafe-inline'` (SEV-9) permits the inline handler to execute.
**Fix:** stop interpolating into JS contexts — use `data-*` attributes + `addEventListener` in `admin.js`, or pass values through `{{ ... | tojson }}`. Also validate username charset at register (`^[a-z0-9_.-]{2,32}$`) and length-cap fname/lname. **Status: fixed.**

### SEV-2 — Session cookie is not HttpOnly
`src/app.py:80` — `SESSION_COOKIE_HTTPONLY = False`. Nothing in the frontend JS reads the session cookie, so this appears to be a mistake. It makes the Flask session cookie readable from JS, turning SEV-1 (or any XSS) into full session hijack.
**Fix:** delete the line (Flask default is `True`). **Status: fixed.**

### SEV-3 — `DEBUG=True` baked into generated config
`setup.sh:142` writes `DEBUG=True` into every `config.cfg` it generates. `app.run()` picks it up, enabling the Werkzeug interactive debugger on `0.0.0.0` — a Python console in the browser (PIN-protected, but the PIN prints to the console/logs and the debugger is a documented RCE vector).
**Fix:** write `DEBUG=False` in `setup.sh`; enable debug only in local dev by hand. **Status: fixed.**

### SEV-4 — SSRF via `webhook_url`, with a status-code oracle
`src/app.py:486,499` (set URL) → `src/geofencing.py:91` and `src/app.py:532` (server POSTs to it). Any authenticated user can point a webhook at loopback / RFC1918 / link-local (e.g. `http://169.254.169.254/`) and the server will POST to it on every geofence transition. Worse, `/api/places/<id>/test` returns `{'status': response.status_code}` (`src/app.py:533`), leaking the downstream status code — a usable internal port/service probe. Threat model assumes trusted users, so this is medium, not critical.
**Fix:** validate scheme is `https`, resolve the host and reject private/loopback/link-local ranges; or document the risk and restrict the test endpoint to not echo downstream status.

### SEV-5 — Fallback config writes a known SECRET_KEY
`src/app.py:28-30` — if `config.cfg` is missing, the app writes one containing `SECRET_KEY = 'changeme'` and continues booting. A misconfigured deploy then signs sessions with a publicly-known key → trivial session forgery (including forging an admin session).
**Fix:** fail fast — raise if `config.cfg` is absent; never auto-mint a placeholder secret. **Status: fixed.**

### SEV-6 — User-enumeration / error oracle in `/api/recordlocation`
`src/app.py:359-396`. Missing `secret`/`username` headers → `KeyError` → 500; unknown username → `AttributeError` on `user.check_api_token` → 500; valid user + wrong token → 401. Status code (and timing — pbkdf2 only runs for valid users) distinguishes valid usernames.
**Fix:** use `request.headers.get(...)`, `if not user: return 401`, return a uniform 401 for all auth failures. **Status: fixed.**

### SEV-7 — No rate limiting on auth endpoints
`/login` (`src/app.py:175`), `/register` (`src/app.py:268`), `/api/recordlocation` accept unlimited attempts. The code already has TODOs acknowledging this. API-token guessing is self-mitigated by pbkdf2 cost; password login is not.
**Fix:** Flask-Limiter, or a per-IP/user failed-attempt counter with backoff. Low priority given the threat model. **Status: fixed (hand-rolled in-memory per-IP throttle in `app.py`).**

### SEV-8 — No CSRF tokens on cookie-authed POSTs
All state-changing routes rely on the session cookie with no CSRF token (no Flask-WTF in the stack). `SESSION_COOKIE_SAMESITE = 'Lax'` (`src/app.py:81`) blocks cross-site POSTs in modern browsers, so real-world exposure is low — noted for completeness.
**Fix:** accept the Lax mitigation (document it), or add Flask-WTF if the app is ever exposed more broadly.

### SEV-9 — CSP allows `'unsafe-inline'` and `'unsafe-eval'` for scripts
`src/app.py:90` — documented in AGENTS.md (Flatpickr/legacy inline JS). This is what makes SEV-1 executable; it also means no CSP backstop for any future XSS.
**Fix:** long-term — move inline JS into static files, drop `'unsafe-inline'`, add nonces. Not a quick change; track as debt.

---

## Bugs

### BUG-1 — `admin_reset_token` discards the new token
`src/app.py:803-813`. Generates `secrets.token_hex(16)`, stores only the hash, returns bare 200. The plaintext is never shown to anyone — the old token dies and the new one is unrecoverable. The feature cannot work as built.
**Fix:** return the plaintext once in the JSON response and display it in `admin.js`.

### BUG-2 — `admin_update_user` strips admin when `is_admin` is omitted
`src/app.py:799` — `user_data.is_admin = data.get('is_admin', False)`. Compare to fname/lname on the lines above, which correctly default to the current value. Any API caller that omits the field silently demotes an admin. (Current `admin.js` always sends it, so this is latent.)
**Fix:** `data.get('is_admin', user_data.is_admin)`.

### BUG-3 — `setup.sh` database-init block is broken (and redundant)
`setup.sh:165-169`: unquoted `src` in the Python `-c` string (NameError), nested double quotes in `print("...")` terminate the shell string early, a stray `"` on line 167, errors swallowed by `2>/dev/null`, and "Database initialized" printed twice. Harmless today only because `src/app.py:67-69` runs `db.create_all()` at startup.
**Fix:** delete the block. The app self-initializes.

### BUG-4 — Jinja `!= Null` cargo cult, plus two latent 500s
`Null` is not a Jinja construct — it's an undefined name, so every `{% if x != Null %}` compares against `Undefined` and works only by accident. Sites: `dashboard.html:19,25,41,46,87`, `map.html:13,19,36,43,50,61`, `account.html:217`. Two landmines currently masked by this:
- `dashboard.html:44` references `url_for('update_info')` — that endpoint **does not exist** (the route is `/account`). If that branch ever renders → `BuildError` → 500.
- `map.html:50` uses `sharing_permission_list`, which the `/map/<username>` route never passes — hidden today because `Undefined != Undefined` is False.
Also `account.html:217`: `sharing_permission_list` is `[]` when empty → `[] != Null` is True → "Location Shared With" heading renders over an empty list.
**Fix:** replace with `is not none` / truthiness; point the link at `url_for('account')`; delete or fix the dead map.html block.

### BUG-5 — `ischarging` never true for JSON clients
`src/app.py:379` — `request_data['ischarging'] in ['true','True']` only matches strings. A well-behaved JSON client sending `"ischarging": true` (boolean) gets `False`. Only string senders (GPSLogger's `%CHARGING%`) work.
**Fix:** accept both, e.g. `str(request_data.get('ischarging', '')).lower() == 'true' or request_data.get('ischarging') is True`.

### BUG-6 — Duplicate `loadPlaces()`; the dead copy has broken string concat
`static/js/account.js:162-187` and `276-302`. The second definition wins; the first is dead code. The dead copy's `editPlace` row HTML is also malformed (drops the closing `\'` after `webhook_url.replace(...)`, ~line 180).
**Fix:** delete the first definition.

### BUG-7 — `/speed` is publicly reachable
`src/app.py:708-710` — no `@login_required`. No server data is exposed (page reads browser GPS client-side only), but it's inconsistent with every other page.
**Fix:** add the decorator.

### BUG-8 — `record_location` POST returns a full HTML page
`src/app.py:329` — returns `render_template('recordlocation.html')` on POST. Callers (dashboard tracking loop, every 10 s) ignore the body. Wasteful and confusing.
**Fix:** return `Response(status=201)`.

### BUG-9 — Missing input validation → 500s instead of 400s
- `/api/recordlocation`: `request_data['lat']` etc. → `KeyError` on missing keys (`src/app.py:370-372`).
- `/api/places` POST: `data['name']`, `data['lat']`... same (`src/app.py:486`); no range checks (negative radius never fires; SQLite ignores `VARCHAR(100)` so `name` is unbounded).
- `get_locations_for_date`: bad `date` param → `ValueError` → 500 (`src/app.py:135`).
- `/login`, `/register`: missing form fields → `KeyError`.
**Fix:** validate and return 400 with a message. Small helper or per-route `.get()` + presence checks.

### BUG-10 — Version badge never renders
`templates/layout.html:30` — `{{ VERSION }}`. Config values are not template globals and no context processor injects it, so it's always `Undefined` and the badge is permanently hidden. The "version display" feature is dead.
**Fix:** add `VERSION` to the existing `inject_admin_status` context processor (rename it), or use `{{ config.VERSION }}`.

### BUG-11 — Dead `app.logger_name` assignment
`src/app.py:36` — setting `app.logger_name = "WEBSRVR"` after app creation does nothing; logs still show `app`.
**Fix:** delete the line.

### BUG-12 — Admin self-delete / last-admin delete unguarded
`src/app.py:815-824` — an admin can delete their own account mid-session, and can delete the last remaining admin, permanently locking the admin panel (no admin → no way to promote anyone without DB surgery).
**Fix:** refuse self-delete and refuse deleting the last admin.

### BUG-13 — Template nits
- `login.html:4` — `</buttonn>` typo.
- `dashboard.html:30` — `href="{{url_for('record_location')}}""` stray double quote.
- `registration_closed.html:2` — title block says "Dashboard".
- `map.html:54` — "View" buttons are `href="#"` dead links.
- `recordlocation.html:66` — `watchPosition` with no throttle floods `/recordlocation` on every GPS tick (the dashboard tracking loop throttles to 10 s; this page doesn't).

---

## Improvements / ops

### OPS-1 — `.venv` files tracked in git
`.venv/include/site/python3.12/greenlet/greenlet.h`, `.venv/lib64`, `.venv/pyvenv.cfg` are committed (from `4f84557`). `.gitignore` covers `.venv/` but tracked files are immune to ignore rules.
**Fix:** `git rm --cached` the three paths and commit.

### OPS-2 — ~1 GB of stale DB backups; main DB grows unbounded
`instance/` holds `beakon.db.bak` (407 MB), `beakon.db.bak2` (407 MB), and legacy `users.db` (265 MB). Live `beakon.db` is 400 MB+ with no retention or vacuum strategy.
**Fix:** delete/archive the backups; decide on a location-retention policy (e.g. drop rows older than N days) and/or periodic `VACUUM`.

### OPS-3 — pytest in requirements, zero tests in repo
`requirements.txt:14-15` pulls in `pytest` + `pytest-flask`; there is no test directory.
**Fix:** add a minimal smoke suite (register → login → recordlocation auth path) or drop the deps.

### OPS-4 — Hand-rolled config parser
`src/app.py:47-59` reimplements `app.config.from_pyfile()` — and `config.cfg` is already valid Python syntax. The custom parser also has a quirk: empty values (`MAPBOX_API_KEY = ''`) are silently dropped by the `if key and value` check.
**Fix:** replace with `app.config.from_pyfile(config_path)`.

### OPS-5 — Geofence job: per-place commits after network I/O; one bad place kills the batch
`src/geofencing.py:90-99` — each loop iteration does up to 5 s of HTTP then `db.session.commit()`. Only `requests` exceptions are caught; any other exception (DB, hydration, etc.) aborts the entire run for all remaining places, and APScheduler logs job exceptions where nobody looks.
**Fix:** wrap the per-place body in try/except, collect outcomes, single commit at the end with rollback on failure.

### OPS-6 — Dev server + adhoc TLS in production
`src/app.py:827` and the systemd unit in `setup.sh:219` both use `app.run(ssl_context='adhoc')` — Werkzeug dev server with a self-signed cert regenerated each start, under Talisman HSTS.
**Fix:** run under waitress/gunicorn behind a reverse proxy with a real cert; or at minimum pin a persistent self-signed cert. Document whichever is chosen.

### OPS-7 — No version pins / lockfile
Everything in `requirements.txt` is `>=`. Builds drift; supply-chain risk.
**Fix:** pin the deployed set exactly (or pip-compile).

### OPS-8 — Naive datetimes everywhere
`datetime.now()` (server-local) used for location timestamps and webhook bookkeeping. DST transitions make "x minutes ago" math lie twice a year; moving the server across timezones corrupts history ordering.
**Fix:** standardize on `datetime.now(timezone.utc)` when there's appetite for the migration. Low priority.

### OPS-9 — TODO.md vs. new migrations/ convention drift
`TODO.md` still plans a Flask-Migrate transition referencing `migration.sql`; the repo now has the `migrations/NNN_*.sql` convention (AGENTS.md). Decide: keep the Alembic plan or update the TODO to reflect the convention.

---

## Accepted risks (per README threat model — do not re-litigate)

- Registered users are assumed to be good actors; the app is not intended to be public-facing.
- API token sent as a plaintext header (over TLS only).
- Webhook payloads include `fname`/`lname` PII, sent to whatever URL the user configured — by design.
- No 2FA / no email verification.
- First registered user becomes admin automatically — documented behavior.
