#!/usr/bin/env python3
"""Seed demo sharers for daniel, to exercise the shared group map (/map).

Creates three users who each share their location with daniel (user id 1), with
synthetic trails placed near daniel's most recent fix so they land in the same map
view.

Deliberate detail: one of the three is given a **stale** latest fix (older than
STALE_AFTER_SECONDS = 600) so the group map's dimming is visible, and the other two
are fresh. Without that, the feature's dimming cannot be seen at all.

Usage:
    python scripts/seed_shared_demo.py [path/to/beakon.db]

Writes to the live DB by default, so it takes a timestamped backup first and prints
its path. Safe to re-run: it removes its own previous demo users (and everything
referencing them) before recreating them, and never touches daniel's data or any
other account.

Passwords are randomly generated, printed once, and also written to a 0600 file next
to the database so the accounts can be logged into if needed.
"""
import os
import random
import secrets
import shutil
import sqlite3
import string
import sys
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The demo accounts. Prefixed so they cannot collide with src/seed.py's
# alice/bob/charlie, or with any real account.
DEMO_USERS = [
    # username, first, last, seconds_ago_of_latest_fix
    ("test_alice", "Alice", "Nguyen", 120),      # fresh
    ("test_bob", "Bob", "Okafor", 45),           # fresh
    ("test_carol", "Carol", "Lindqvist", 2400),  # STALE (>600s) -> renders dimmed
]

TRAIL_POINTS = 10


def backup(db_path):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = f"{db_path}.{stamp}.bak"
    shutil.copy2(db_path, dest)
    return dest


def remove_previous(conn):
    """Remove any prior run's demo users and everything referencing them."""
    cur = conn.cursor()
    ids = [r[0] for r in cur.execute(
        "select id from users where username like 'test_%'").fetchall()]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    cur.execute(f"delete from locations_table where userid in ({marks})", ids)
    cur.execute(f"delete from known_places where userid in ({marks})", ids)
    cur.execute(
        f"delete from sharing_permission where data_owner_id in ({marks}) "
        f"or shared_with_id in ({marks})", ids + ids)
    cur.execute(f"delete from user_data where id in ({marks})", ids)
    cur.execute(f"delete from users where id in ({marks})", ids)
    return len(ids)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    db_path = args[0] if args else os.path.join(REPO_ROOT, "instance", "beakon.db")
    if not os.path.exists(db_path):
        print(f"ERROR: database not found: {db_path}")
        return 1

    # Import the app's own hashing so stored hashes match what the app verifies.
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from werkzeug.security import generate_password_hash

    if "--remove" in sys.argv:
        print(f"Backup written to: {backup(db_path)}")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys=ON")
        removed = remove_previous(conn)
        conn.commit()
        conn.close()
        creds_path = os.path.join(os.path.dirname(db_path),
                                  "demo-sharer-credentials.txt")
        if os.path.exists(creds_path):
            os.remove(creds_path)
        print(f"Removed {removed} demo user(s), their locations and their shares.")
        return 0

    print(f"Backup written to: {backup(db_path)}")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.cursor()

    daniel = cur.execute(
        "select id, username from users where username='daniel'").fetchone()
    if daniel is None:
        print("ERROR: no 'daniel' account - refusing to guess a share target")
        return 1
    daniel_id = daniel[0]

    # Anchor the demo trails near daniel's most recent fix so they share a view.
    anchor = cur.execute(
        "select lat, lon from locations_table where userid=? "
        "order by timestamp desc limit 1", (daniel_id,)).fetchone()
    if anchor is None:
        print("ERROR: daniel has no locations - cannot anchor the demo trails")
        return 1
    base_lat, base_lon = anchor
    print(f"Anchoring demo trails near daniel's latest fix: "
          f"{base_lat:.5f}, {base_lon:.5f}")

    removed = remove_previous(conn)
    if removed:
        print(f"Removed {removed} previous demo user(s) and their data")

    now = datetime.now()
    creds = []
    for username, fname, lname, latest_age_s in DEMO_USERS:
        password = "".join(secrets.choice(string.ascii_letters + string.digits)
                           for _ in range(16))
        cur.execute(
            "insert into users (username, password_hash, api_token_hash) "
            "values (?,?,?)", (username, generate_password_hash(password), None))
        uid = cur.lastrowid

        cur.execute(
            "insert into user_data (id, fname, lname, access_to, is_admin) "
            "values (?,?,?,?,?)", (uid, fname, lname, None, 0))

        # Share this user's location WITH daniel.
        cur.execute(
            "insert into sharing_permission "
            "(data_owner_username, data_owner_id, shared_with_username, "
            " shared_with_id) values (?,?,?,?)",
            (username, uid, daniel[1], daniel_id))

        # A short trail, walking away from the anchor in a small arc, oldest
        # first, ending at the age that decides fresh vs stale.
        for i in range(TRAIL_POINTS):
            # i=0 oldest .. i=TRAIL_POINTS-1 newest
            age_s = latest_age_s + (TRAIL_POINTS - 1 - i) * 180
            step = i - (TRAIL_POINTS - 1)
            lat = base_lat + step * 0.0016 + random.uniform(-0.0004, 0.0004)
            lon = base_lon + step * 0.0019 + random.uniform(-0.0004, 0.0004)
            cur.execute(
                "insert into locations_table "
                "(lat, lon, acc, timestamp, userid, batt, ischarging) "
                "values (?,?,?,?,?,?,?)",
                (lat, lon, 10.0, (now - timedelta(seconds=age_s)).isoformat(" "),
                 uid, random.randint(35, 95), 0))

        creds.append((username, password, latest_age_s))
        print(f"  {username:12s} (id {uid}) shares with daniel; "
              f"{TRAIL_POINTS} locations; latest {latest_age_s}s old"
              f"{' [STALE -> dimmed]' if latest_age_s > 600 else ''}")

    conn.commit()

    # Report what the app will now see.
    print()
    print("Verification (read back from the DB):")
    for username, _, _ in creds:
        uid = cur.execute("select id from users where username=?",
                          (username,)).fetchone()[0]
        n = cur.execute("select count(*) from locations_table where userid=?",
                        (uid,)).fetchone()[0]
        shared = cur.execute(
            "select count(*) from sharing_permission where data_owner_id=? "
            "and shared_with_id=?", (uid, daniel_id)).fetchone()[0]
        print(f"  {username}: {n} locations, sharing_with_daniel={bool(shared)}")
    total_sharers = cur.execute(
        "select count(*) from sharing_permission where shared_with_id=?",
        (daniel_id,)).fetchone()[0]
    print(f"  daniel now has {total_sharers} incoming share(s)")
    conn.close()

    creds_path = os.path.join(os.path.dirname(db_path),
                              "demo-sharer-credentials.txt")
    with open(creds_path, "w") as fh:
        fh.write("Demo sharer accounts (seed_shared_demo.py)\n")
        fh.write("These share their location with daniel. Delete with:\n")
        fh.write("  python scripts/seed_shared_demo.py --remove\n\n")
        for username, password, _ in creds:
            fh.write(f"{username}: {password}\n")
    os.chmod(creds_path, 0o600)
    print(f"\nCredentials written to {creds_path} (0600)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
