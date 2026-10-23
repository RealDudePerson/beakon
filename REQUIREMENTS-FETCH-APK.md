# REQUIREMENTS — Fetch the companion APK at deploy time

Repo: `~/projects/beakon` (branch **`master`** — NOT `main`). 76 tests passing.

## Context — already done, verified

The companion APK is now published as a **GitHub release asset** and confirmed downloadable
**anonymously** (the repo is public, so no credentials are needed):

- Release: `companion-v1.0.0` (id `421323463`)
- Asset: `beakon-companion-debug.apk`
- URL: `https://github.com/RealDudePerson/beakon/releases/download/companion-v1.0.0/beakon-companion-debug.apk`
- package `com.beakon.companion`, **12,785,921 bytes**,
  sha256 `50076393b0873ae2899c9e716be4922a6158b883d7ee009f2e94f7ba7e8c3c0f`
- Verified by fetching with `curl` and **no Authorization header**: HTTP 200,
  `application/vnd.android.package-archive`, byte-identical, correct package name.

The binary stays **git-ignored**. A freshly cloned instance currently has no APK, so the
overflow menu item 404s — that is the gap this closes.

## Required changes

1. **`companion-apk.txt`** (tracked, repo root) — the single source of truth for which build an
   instance should serve. Keep it small and machine-readable, e.g.:

   ```
   tag=companion-v1.0.0
   filename=beakon-companion-debug.apk
   size=12785921
   sha256=50076393b0873ae2899c9e716be4922a6158b883d7ee009f2e94f7ba7e8c3c0f
   ```

   Bumping the app means editing this one file. `scripts/fetch-companion-apk.sh` reads it, so
   the tag and hash are never duplicated in code.

2. **`scripts/fetch-companion-apk.sh`** — fetches the asset to
   `static/beakon-companion-debug.apk`:
   - `set -euo pipefail`; resolve paths relative to the script so it runs from anywhere.
   - **No credentials.** Do not require or use a token. If someone has `GITHUB_TOKEN` set, do
     not silently start sending it — this URL is public.
   - **Verify size and sha256 against `companion-apk.txt` before installing the file.** Download
     to a temporary path, verify, then move into place. A truncated or wrong artifact must never
     end up in `static/`.
   - **Never fail silently** — this is a standing rule for this project. On a network failure,
     a 404, a hash mismatch, or a missing/corrupt `companion-apk.txt`, print a specific message
     saying what went wrong and what to do, and exit non-zero. Do not print success unless the
     file is in place and verified.
   - **Idempotent**: if `static/beakon-companion-debug.apk` already matches the pinned size and
     hash, say so and exit 0 without re-downloading. Add `--force` to re-fetch anyway.
   - Support overriding the destination via an argument or env var, so the script is testable
     without touching `static/`.

3. **Wire it into deploy.** In `readme.md`, a short "Deploying a fresh instance" section: clone,
   install deps, **run `scripts/fetch-companion-apk.sh`**, then start. Make clear that without
   this step the menu's download item 404s. If the repo has a Makefile or deploy script, add a
   target there too rather than only documenting it.

4. **Keep the existing test consistent.** `tests/test_apk_download_menu.py` pins the same sha256
   and size. Do not leave two sources of truth that can drift: either have that test read
   `companion-apk.txt`, or assert that the two agree. State which you chose.

## Tests

- The script exists, is executable, and its shebang is valid.
- `companion-apk.txt` parses and its size/sha256 **agree with the APK actually served** and with
  whatever the existing test pins.
- Run the script against a **temporary destination** with `--force` and assert it produces a
  file whose size and sha256 match the pinned values — this is a real network call, so mark it
  so it can be deselected if offline, but do NOT skip it silently.
- A tampered `companion-apk.txt` (wrong hash) makes the script **fail non-zero and leave no file
  behind** — prove this, and prove red-capability.

Report the test count before and after.

## Out of scope
- The scavenge server, the scavenge app, the Android app, and the release itself (already
  published — do not create another release or re-upload the asset).

## Honesty
State what you verified versus what you could not, and any deviation and why.
