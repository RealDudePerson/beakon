# REQUIREMENTS — Make companion-apk.txt the only place the build is pinned

Repo: `~/projects/beakon` (branch **`master`** — NOT `main`).

## What just happened

`companion-apk.txt` was bumped to `companion-v1.0.1` (the release-optimized build):
`size=9112325`, `sha256=85e9b7cb80088475e178ecaa1fe948388478d710c9c77e00494f88ea8d8029d6`.

The fetch script picked it up correctly, but **5 tests now fail**, including
`test_real_fetch_to_a_temp_destination`. The cause is that
`tests/test_apk_download_menu.py` still hard-codes the *previous* build:

```python
APK_SIZE = 12785921
APK_SHA256 = '50076393b0873ae2899c9e716be4922a6158b883d7ee009f2e94f7ba7e8c3c0f'
```

The "they must agree" test is working as intended — it caught the drift. But it means every
app bump requires editing **two** files, which is the exact two-sources-of-truth problem the
manifest was introduced to remove. A guard that fires on every legitimate bump is a guard
people learn to bypass by editing the constant, which defeats it.

## Required change

**Make `companion-apk.txt` the single place the build is pinned.**

- `tests/test_apk_download_menu.py` must **read** `companion-apk.txt` for the expected size and
  sha256 instead of hard-coding them. Keep asserting the *served* file matches those values —
  the test's job is unchanged, only its source of truth moves.
- Do the same anywhere else that pins the build: grep the repo for the old sha256
  (`50076393…`) and the old size (`12785921`) and remove every remaining hard-coded copy.
  There should be **no** literal build hash or size outside `companion-apk.txt` when you are
  done — except inside tests that deliberately use a fake value to prove failure paths, which
  must be obviously fake (e.g. all-`1`s or all-`0`s) and never a real build's.
- Keep the existing "manifest and the pinned values agree" test if it still has meaning, or
  delete it if it is now tautological — say which you chose and why. A tautological test is
  worse than no test because it looks like coverage.
- Do **not** weaken any failure-path test: a tampered manifest must still fail non-zero and
  leave no file behind, and the real-fetch test must still run (deselectable with
  `-m "not network"`, never silently skipped).

## Prove it

- Full suite green. Report the count before and after (before: 85 passed / 5 failed).
- **Prove the single-source property**: change only `companion-apk.txt` (e.g. flip the sha256
  to all-`1`s), run the suite, and show it fails; then restore and show it passes. That is the
  evidence that the pin now lives in exactly one place.
- Prove red-capability of the tamper path again if you touched it.

## Out of scope
- The Android app, the release itself (already published as `companion-v1.0.1`), the scavenge
  projects, and `static/beakon-companion-debug.apk` (already correct — do not re-download or
  replace it unless a test requires it).

## Honesty
State what you verified versus what you could not, and any deviation and why.
