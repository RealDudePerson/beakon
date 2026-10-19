# Shared map: show everyone who shares with you

Repo: `/home/hermes/projects/beakon`. Approved design: `PROPOSAL-shared-map.md`.
Implement that proposal, with the corrections below.

## Corrections to the proposal (these win)

1. **Do NOT add any staleness/dimming logic.** The owner said "don't change stale
   threshold". The current map has **no** staleness concept — no threshold, no
   dimming, no opacity rule. It shows a timestamp string produced by
   `format_timestamp()` ("just now", "N minutes ago") and refreshes every 10s
   (`static/js/map.js:128 REFRESH_INTERVAL_MS = 10000`). **Match that exactly.**
   Do not invent a stale rule, do not dim, do not grey out. If you think something
   looks stale, show its timestamp text like the existing view does.
2. The proposal's claim that the locations endpoint is throttled is **not
   established** — the throttle keys are login/register/recordlocation. The
   one-call design is still required, for efficiency (N sharers × a 10s refresh is
   wasteful), but do not describe it as a rate-limit workaround in comments.

## What to build

### 1. `GET /api/locations/shared`
One call returns everyone who shares with the logged-in user.

```json
{"people": [{"username": "...", "display_name": "...",
             "latest": {"lat":..,"lon":..,"timestamp":"...","batt":..,"ischarging":..},
             "trail": [{"lat":..,"lon":..,"timestamp":".."}, ...]}]}
```

- **Fail-closed.** The permitted set comes from
  `SharingPermissionModel.query.filter_by(shared_with_id=<me>)`. A user with no
  such row must **never** appear, under any circumstances. If the permitted set is
  empty, return `{"people": []}` — not an error.
- **One query for all locations**, grouped by `userid` — not one query per person.
  Resolve the permitted owner ids first, then fetch their locations together.
- Reuse `get_filtered_locations` semantics per person (`max_locations=10`,
  `min_distance_meters=91`) so trails match the single-person view.
- A permitted user with no locations is simply absent from `people`.
- Use `format_timestamp()` for the timestamp strings, exactly as the existing
  routes do.

### 2. `/map` — the everyone view
- One marker per person at their **latest** position, plus their trail.
- **A distinct colour per person** and the name as a label.
- **A legend listing each person with a show/hide toggle.** With 3+ people a map
  becomes a knot of overlapping trails; the toggles are what make it usable.
- **Refresh every 10 seconds**, same as `static/js/map.js`. On each refresh, people
  who are no longer shared with must **disappear** — never cached across polls.
- Each legend entry links to that person's existing `/map/<username>` view.
- Empty state when nobody shares with you: a clear message, not a blank map.
- **No staleness/dimming** (see corrections).

### 3. Entry point
Link the new `/map` from the dashboard, next to the existing sharing list
(`app.py:501-508` builds that list), so it is discoverable where the shares already
appear.

### 4. Do not change the existing per-person view
`/map/<username>`, `/api/locations/<map_username>` and their templates/JS keep
working exactly as they do now. This feature is purely additive.

## Hard constraints — violating any fails the task

1. **No schema change, no migration.** `sharing_permission` is the only source of
   truth for visibility.
2. **No new sharing capability.** This renders existing shares; it must not make
   anyone visible to anyone who could not already see them.
3. **Fail closed.** An error resolving permissions must yield an empty result, never
   "show everything".
4. **Never fail silently** — errors surface an actionable message.
5. The 90s tracking interval, the throttle logic, `TRUSTED_PROXY_IPS`, and the
   webhook handling are all **untouched**. Webhook destinations stay unrestricted.
6. Do not touch `config.cfg`.

## Acceptance criteria — run these yourself and report real output

1. **Full suite passes.** Report the count. It was 51 before this work.
2. **Fail-closed is proven**: add a test that a user who does **not** share with the
   caller does not appear in `/api/locations/shared` — even when they have
   locations. Prove it red-capable.
3. **Positive case**: a user who **does** share appears, with a non-empty trail and
   a `latest` entry.
4. **Empty set**: a caller with no incoming shares gets `{"people": []}` and a 200,
   not an error.
5. **Permitted-but-empty**: a sharer with no locations is absent (or has no marker)
   — assert whichever you implement.
6. **No N+1**: demonstrate the implementation issues a bounded number of queries
   regardless of how many people share (e.g. assert query count for 3 sharers equals
   that for 1). If you cannot instrument query counts, say so plainly instead of
   claiming it.
7. **The existing per-person route is unchanged**: its tests still pass and its
   response shape is byte-compatible with before.
8. **No staleness logic added**: state plainly that you added none, and show that
   the group view renders `format_timestamp()` strings as the single-person view does.
9. **Page renders**: `/map` returns 200 for a logged-in user with shares, and the
   legend lists exactly the permitted people.

## Rules
- Work on branch `feat/shared-map`. Commit there. Do NOT merge to main and do NOT
  push — the owner merges after review.
- Do NOT deploy and do NOT restart the live service. Tests only.
- Report honestly: what passed, what you could not verify, and anything you chose
  differently and why.
