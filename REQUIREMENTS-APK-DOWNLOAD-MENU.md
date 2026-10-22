# REQUIREMENTS — Overflow menu with the Android app download

Repo: `~/projects/beakon` (branch **`master`** — NOT `main`). 131 tests passing before you start.

## Goal

Add an **overflow menu** ("⋮") to the Beakon top bar containing a **download item for the
Android companion app**, so the app can be handed out from the site the family already uses.

## Where it goes

`templates/layout.html` — the shared base every page extends. The top bar is
`nav.top-bar.topbar-responsive`, and the menu items live in the
`{% block top_menu_buttons %}` block (lines ~34-44: Dashboard / Speed / Presence / Admin /
Logout).

The overflow menu must appear **on every page** that extends `layout.html`, and must work at
both desktop and mobile widths — the top bar collapses behind `#topbar-responsive` on small
screens, so the menu has to behave there too.

## The menu

- A "⋮" (vertical ellipsis) button at the right end of the menu row, after Logout.
- Clicking it opens a small dropdown containing: **Download Android app**.
- The project uses **Foundation** (`foundation.css` / `foundation.js` are already loaded, as is
  jQuery). Use Foundation's own dropdown/dropdown-menu component rather than hand-rolling
  click handling — but note the existing `<ul class="menu simple vertical medium-horizontal">`
  has no `data-dropdown-menu` attribute, so check what adding it does to the existing items
  before committing to it. If Foundation's dropdown-menu would disturb the current nav, use
  Foundation's plain `Dropdown` (`data-toggle`) or a minimal, self-contained implementation —
  and say which you chose and why.
- Accessible: real `<button>` (not a div), `aria-haspopup` / `aria-expanded`, closable with
  Escape and by clicking away, and reachable by keyboard.
- Match the existing visual style — `static/css/beakon.css` is the project's own stylesheet;
  put any new rules there rather than inline where practical.

## Serving the APK

**Host the file on Beakon itself** — do not link out to another site.

- Copy the current build to `static/scavenge-companion-debug.apk`:
  source `/home/hermes/projects/scavenge/static/scavenge-companion-debug.apk`
  (12,836,013 bytes, sha256 `49c0f62d2255e8a9d4c23a11241c7d1fe281d4e84e776cb68cc6518d895d0ab2`).
- Link to it via `url_for('static', filename='scavenge-companion-debug.apk')`, not a
  hardcoded path.
- The download must carry the right content type so Android treats it as an installer
  (`application/vnd.android.package-archive`). Verify what Beakon actually serves and set it
  if Flask guesses wrong.
- Add a short line in the README (or a comment near the top of `layout.html`) noting that this
  copy must be refreshed when the app is rebuilt — otherwise it silently goes stale.

## Do not break

- The existing nav items and the `top_menu_buttons` block that every template overrides.
  Several templates override that block (dashboard, map, presence, speed, admin, login,
  register, shared_map, ...) — **check that the overflow menu still renders on those pages**,
  since a template that overrides the block replaces its contents. If the menu must live
  outside the block to survive those overrides, put it outside the block and say so.
- `is_admin` / authentication conditionals.
- The map pages, which load Leaflet.

## Tests

- The download URL is reachable and returns the file with the correct content type and length.
- The overflow menu markup is present on a page that overrides `top_menu_buttons`
  (e.g. the dashboard) **and** on one that does not — this is the regression that matters.
- Existing suite still passes.

State the test count before and after.

## Out of scope
- The scavenge server and the Android app — do not modify either.
- The APK's contents.

## Honesty
Report what you verified versus what you could not (there is no browser here, so a real
dropdown interaction cannot be observed — say so rather than implying it works).
