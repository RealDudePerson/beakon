#!/usr/bin/env bash
#
# fetch-companion-apk.sh — download and verify Beakon's companion APK.
#
# Reads the pinned release tag / filename / size / sha256 from the tracked
# companion-apk.txt at the repository root, downloads the matching GitHub
# release asset, verifies it byte-for-byte, and only then installs it as
# static/beakon-companion-debug.apk.
#
# The release is public, so NO credentials are used or required. This script
# deliberately never reads GITHUB_TOKEN (or any other token): the asset URL is
# anonymous. If you ever find yourself adding an Authorization header here,
# stop — it is not needed and it would leak a secret to a public download.
#
# This script never fails silently: every failure prints what went wrong and
# what to do, and exits non-zero. It never prints success unless the verified
# file is actually in place.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Repository whose releases host the asset, and the default asset base URL.
REPO_SLUG="${BEAKON_APK_REPO_SLUG:-RealDudePerson/beakon}"
DEFAULT_BASE_URL="${BEAKON_APK_BASE_URL:-https://github.com/${REPO_SLUG}/releases/download}"

DEFAULT_MANIFEST="$REPO_ROOT/companion-apk.txt"
DEFAULT_DEST="$REPO_ROOT/static/beakon-companion-debug.apk"

MANIFEST="${BEAKON_APK_MANIFEST:-$DEFAULT_MANIFEST}"
DEST="${BEAKON_APK_DEST:-$DEFAULT_DEST}"
BASE_URL="$DEFAULT_BASE_URL"
FORCE=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [--force] [--dest PATH] [--manifest PATH] [--base-url URL]

Download the companion APK pinned in companion-apk.txt, verify its size and
sha256, and install it at static/beakon-companion-debug.apk.

Options:
  --force            Re-download even if the destination already matches.
  --dest PATH        Install to PATH instead of static/beakon-companion-debug.apk.
  --manifest PATH    Read the pin from PATH instead of ./companion-apk.txt.
  --base-url URL     Asset base URL (default the public GitHub release URL).
                     Override only for mirrors/tests; the default needs no token.
  -h, --help         Show this help.

Environment overrides: BEAKON_APK_DEST, BEAKON_APK_MANIFEST, BEAKON_APK_BASE_URL.
No credentials are used or required.
EOF
}

die() {
    printf 'fetch-companion-apk: ERROR: %s\n' "$*" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --force)
            FORCE=1
            shift
            ;;
        --dest)
            [ $# -ge 2 ] || die "--dest requires a path argument"
            DEST="$2"
            shift 2
            ;;
        --dest=*)
            DEST="${1#--dest=}"
            shift
            ;;
        --manifest)
            [ $# -ge 2 ] || die "--manifest requires a path argument"
            MANIFEST="$2"
            shift 2
            ;;
        --manifest=*)
            MANIFEST="${1#--manifest=}"
            shift
            ;;
        --base-url)
            [ $# -ge 2 ] || die "--base-url requires a URL argument"
            BASE_URL="$2"
            shift 2
            ;;
        --base-url=*)
            BASE_URL="${1#--base-url=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            usage >&2
            die "unknown option: $1"
            ;;
        *)
            usage >&2
            die "unexpected argument: $1 (did you mean --dest $1?)"
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Read and validate the pinned build manifest.
# ---------------------------------------------------------------------------
[ -f "$MANIFEST" ] || die "manifest not found: $MANIFEST
  companion-apk.txt is the single source of truth for the pinned APK build.
  Restore it from git and retry:  git checkout -- companion-apk.txt"

manifest_value() {
    awk -v key="$1" '
        /^[[:space:]]*#/ { next }
        {
            line = $0
            sub(/\r$/, "", line)
            if (line ~ "^[[:space:]]*" key "[[:space:]]*=") {
                sub(/^[^=]*=[[:space:]]*/, "", line)
                sub(/[[:space:]]+$/, "", line)
                print line
                exit
            }
        }
    ' "$MANIFEST"
}

TAG="$(manifest_value tag)"
FILENAME="$(manifest_value filename)"
SIZE="$(manifest_value size)"
SHA256="$(manifest_value sha256)"

[ -n "$TAG" ] || die "companion-apk.txt ($MANIFEST) is missing the 'tag' key.
  Expected a line like:  tag=<release tag>"
[ -n "$FILENAME" ] || die "companion-apk.txt ($MANIFEST) is missing the 'filename' key.
  Expected a line like:  filename=beakon-companion-debug.apk"
[ -n "$SIZE" ] || die "companion-apk.txt ($MANIFEST) is missing the 'size' key.
  Expected a line like:  size=<byte count>"
[ -n "$SHA256" ] || die "companion-apk.txt ($MANIFEST) is missing the 'sha256' key.
  Expected a line like:  sha256=<64 hex chars>"

case "$SIZE" in
    ''|*[!0-9]*)
        die "companion-apk.txt ($MANIFEST) has an invalid 'size' ($SIZE): expected a byte count." ;;
esac

SHA256="$(printf '%s' "$SHA256" | tr 'A-F' 'a-f')"
case "$SHA256" in
    *[!0-9a-f]*)
        die "companion-apk.txt ($MANIFEST) has an invalid 'sha256' ($SHA256): expected 64 hex characters." ;;
esac
[ "${#SHA256}" -eq 64 ] || die "companion-apk.txt ($MANIFEST) has a sha256 of ${#SHA256} characters; expected 64."

URL="${BASE_URL%/}/${TAG}/${FILENAME}"

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        die "no sha256 tool available (need 'sha256sum' or 'shasum')."
    fi
}

file_size() {
    wc -c < "$1" | tr -d ' '
}

# ---------------------------------------------------------------------------
# Idempotency: a destination that already matches the pin needs no download.
# ---------------------------------------------------------------------------
if [ "$FORCE" -eq 0 ] && [ -f "$DEST" ]; then
    if [ "$(file_size "$DEST")" = "$SIZE" ] && [ "$(sha256_of "$DEST")" = "$SHA256" ]; then
        echo "fetch-companion-apk: $DEST already matches $TAG (size $SIZE, sha256 $SHA256) — nothing to do."
        echo "  (use --force to re-download anyway)"
        exit 0
    fi
    echo "fetch-companion-apk: $DEST exists but does not match the pinned $TAG build; re-fetching." >&2
fi

# ---------------------------------------------------------------------------
# Download to a temporary file inside the destination directory (so the final
# move is atomic on the same filesystem), then verify before installing.
# ---------------------------------------------------------------------------
DEST_DIR="$(dirname "$DEST")"
mkdir -p "$DEST_DIR" || die "cannot create destination directory: $DEST_DIR"

TMP="$(mktemp "$DEST_DIR/.beakon-apk.XXXXXX")" || die "cannot create a temporary file in $DEST_DIR"
cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

echo "fetch-companion-apk: downloading $URL (anonymous; no credentials)"
set +e
curl_err="$(curl --fail --location --silent --show-error --output "$TMP" "$URL" 2>&1)"
curl_rc=$?
set -e

if [ "$curl_rc" -ne 0 ]; then
    case "$curl_rc" in
        22)
            die "download failed: the server returned an HTTP error (often 404) for
  $URL
  The release asset may have moved, or the tag/filename pinned in $MANIFEST
  may be wrong. Check companion-apk.txt against the release page:
  https://github.com/${REPO_SLUG}/releases/tag/${TAG}" ;;
        6|7|28|35|56)
            die "download failed: could not reach the asset host (curl exit $curl_rc).
  Check this host's network/DNS and retry. Details: $curl_err" ;;
        37)
            die "download failed: could not read the local asset (curl exit 37).
  Details: $curl_err" ;;
        *)
            die "download failed (curl exit $curl_rc) for
  $URL
  Details: $curl_err" ;;
    esac
fi

# ---------------------------------------------------------------------------
# Verify size and hash BEFORE the file can reach its destination.
# ---------------------------------------------------------------------------
actual_size="$(file_size "$TMP")"
if [ "$actual_size" != "$SIZE" ]; then
    rm -f "$TMP"
    die "verification failed: downloaded $actual_size bytes but $MANIFEST pins $SIZE.
  The asset changed or companion-apk.txt is corrupt. Nothing was installed."
fi

actual_sha="$(sha256_of "$TMP")"
if [ "$actual_sha" != "$SHA256" ]; then
    rm -f "$TMP"
    die "verification failed: downloaded sha256 $actual_sha but $MANIFEST pins $SHA256.
  The asset changed or companion-apk.txt is corrupt. Nothing was installed."
fi

# Verified — install it. mktemp creates 0600; a static web asset wants 0644.
chmod 0644 "$TMP"
mv -f "$TMP" "$DEST"
trap - EXIT

[ -f "$DEST" ] || die "internal error: $DEST is missing after install."

final_size="$(file_size "$DEST")"
final_sha="$(sha256_of "$DEST")"
if [ "$final_size" != "$SIZE" ] || [ "$final_sha" != "$SHA256" ]; then
    die "internal error: the installed file failed re-verification ($final_size bytes, sha256 $final_sha)."
fi

echo "fetch-companion-apk: installed verified $FILENAME ($TAG) to $DEST"
echo "  size:   $SIZE"
echo "  sha256: $SHA256"
