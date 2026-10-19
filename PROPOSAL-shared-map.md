# Proposal: one map showing everyone who shares with you

**Status: pitch for review. Nothing implemented.**

## The idea

Today the map is strictly one person at a time: `/map/<username>` renders a single
sharer's trail, and you have to navigate back and forth to see two people. Replace
that with a single map showing **everyone who shares their location with you**, each
in their own colour with a label, so the whole group is legible at a glance.

The per-person view stays — this adds the "everyone" view rather than replacing
anything.

## Why it fits the existing design

The permission model already contains everything needed. `sharing_permission` maps
`data_owner_id` → `shared_with_id`, and the dashboard already queries exactly this
list for the "people sharing with you" panel:

```python
SharingPermissionModel.query.filter_by(shared_with_id=id).all()   # app.py:501
```

So the feature is "render that same list as markers" — no new permissions, no new
trust decisions, and **no schema change**.

## Design

### API: `GET /api/locations/shared`
One call returns everyone. One call matters: the endpoint is behind the per-IP
throttle, and a naive implementation would fire one request per sharer.

```json
{
  "people": [
    {
      "username": "alice",
      "display_name": "Alice S",
      "latest": {"lat": 40.7, "lon": -74.0, "timestamp": "just now",
                 "batt": 77, "ischarging": false},
      "trail": [ {"lat": ..., "lon": ..., "timestamp": ...}, ... ]
    }
  ]
}
```

- **Fail-closed**: only users with an explicit `sharing_permission` row where
  `shared_with_id` is the logged-in user. No row → not in the response, ever.
- **One query, not N.** Resolve the permitted owner ids first, then fetch all their
  locations in a single query grouped by `userid`. The existing per-user path does
  one query per person; a group map multiplies that by the number of sharers.
- Reuse the existing filtering semantics (`max_locations=10`,
  `min_distance_meters=91`) so trails stay small and comparable to today.
- The existing `?date=` behaviour can be supported per person later; **out of scope
  here** (see below).

### UI
- One marker per person at their **latest** position, plus their trail.
- **A distinct colour per person** with the name as a label — the whole point is
  telling people apart at a glance.
- **A legend / toggle list**: each person can be shown or hidden. With three or more
  sharers a map becomes a knot of overlapping trails; toggles are what keep it usable.
- **Freshness**: a sharer whose last fix is old is dimmed/marked stale rather than
  presented as current. (The app's own UI has the same problem today, so the rule
  should match whatever `/map/<username>` does now for consistency.)
- Clicking a person links to their existing single-person map, so the group view is
  a hub rather than a dead end.

### Entry points
- New route `/map` (the "everyone" view). Existing `/map/<username>` unchanged.
- Link it from the dashboard next to the sharing list, so the group view is
  discoverable from where the shares are already listed.

## Edge cases to handle

| Case | Behaviour |
|---|---|
| Nobody shares with you | Explicit empty state, not a blank map |
| A sharer has no locations yet | Omit them (or show them with no marker), never a broken marker |
| A sharer revoked while the map is open | Next poll drops them; never cache across polls |
| Many sharers | Toggles + a cap on simultaneous trails |
| Trail points | Reuse the existing 10-point / 91m filter |

## Effort and risk

- **Effort: M.** One new API endpoint, one new route/template, a legend component,
  and a colour assignment. The map JS was recently extracted to `static/js/map.js`,
  so there is a clean place to add it.
- **Risk: low.** Purely additive — a new route and a new endpoint. No schema change,
  no migration, no change to the existing per-person view or its permissions.
- **Deployable independently** of anything else outstanding.

## Deliberately out of scope

- Live push/websocket updates (the app polls; keep it that way for now).
- Per-person date/time scrubbing on the group map.
- Any change to who can see whom — this feature adds **no** new sharing capability,
  it only renders shares that already exist.
