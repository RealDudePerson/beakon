# REQUIREMENTS — Serve Beakon's own companion APK from Beakon

Repo: `~/projects/beakon` (branch **`master`** — NOT `main`). 74 tests passing before you start.

## Correction

The overflow menu added previously points at the **wrong app**. It serves
`static/scavenge-companion-debug.apk`, which is the Scavenge *hunter* app — a different
project with its own site (`fruitsaladday.com`). Beakon's own companion app was never offered.

The menu on a Beakon instance must offer **Beakon's own companion app**. Fix it.

## Required changes

1. **Swap the served file.**
   - Remove `static/scavenge-companion-debug.apk`.
   - Add `static/beakon-companion-debug.apk`, copied from
     `/home/hermes/projects/beakon-android/app/build/outputs/apk/debug/app-debug.apk`
     — **12,785,921 bytes, sha256 `50076393b0873ae2899c9e716be4922a6158b883d7ee009f2e94f7ba7e8c3c0f`**.
     Verify size and hash after copying rather than trusting the copy.
   - Package name is `com.beakon.companion` — verify it from the APK with `aapt2 dump badging`
     and confirm you shipped the right file. Shipping a different project's binary is exactly
     the mistake being corrected; prove you did not repeat it.

2. **Point the menu at it.** `templates/layout.html` — the `url_for('static', …)` filename
   changes to the Beakon APK. Keep the menu where it is: **outside** the `top_menu_buttons`
   block (dashboard, map, presence, speed, admin, login, register and shared_map override that
   block — inside it the menu disappears on those pages).

3. **Label it unambiguously.** The current label is "Download Android app", which is what made
   the mistake possible. Use something that names the app, e.g. **"Download the Beakon app"**.
   Keep the `download` attribute.

4. **Keep the MIME pinning** in `src/app.py` (`application/vnd.android.package-archive`).
   Confirm it still applies to the new filename.

5. **Update `.gitignore`** so it ignores the Beakon APK, and confirm the Scavenge one is no
   longer present in `static/`. Keep ignoring the binary rather than committing it — but see
   the open question below and state what you did.

6. **README** — correct the note about which APK this is and where it comes from
   (`~/projects/beakon-android`), and that it must be refreshed when that app is rebuilt.

## Tests

- The served filename is the Beakon APK, and it is the correct binary (size + sha256).
- The **package name inside the served APK is `com.beakon.companion`** — this is the test that
  would have caught the original mistake. Add it.
- The Scavenge APK is no longer served from this repo.
- The menu still renders on a page that overrides `top_menu_buttons` **and** on one that does
  not.
- Content type is still `application/vnd.android.package-archive`.

Report the test count before and after.

## Open question — do NOT decide it silently

A Beakon instance that is freshly cloned will **not** have this APK, because the binary is
git-ignored. So "downloadable from Beakon instances" is only true for *this* instance.

Do **not** solve this by committing the 12.7 MB binary — it bloats every clone. Instead,
**report** what you would recommend (a GitHub release artifact fetched at deploy time, a
documented copy step, or committing it) and leave the current behaviour in place. The owner
will decide.

## Out of scope
- The scavenge server, the scavenge app, and the Android app itself.

## Honesty
State what you verified versus what you could not. There is no browser here, so a real
dropdown click cannot be observed — say so rather than implying it works.
