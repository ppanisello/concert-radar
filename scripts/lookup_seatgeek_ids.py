#!/usr/bin/env python3
"""Look up SeatGeek performer IDs for all active bands and update .md files."""

import os
import sys
import time
import glob

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
import frontmatter
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

SG_CLIENT_ID = os.environ.get("SEATGEEK_CLIENT_ID")
SG_BASE = "https://api.seatgeek.com/2"


def find_performer_id(name):
    """Search SeatGeek performers API and return best match (id, sg_name) or (None, None)."""
    params = {
        "q": name,
        "client_id": SG_CLIENT_ID,
        "per_page": 10,
    }
    resp = requests.get(f"{SG_BASE}/performers", params=params, timeout=15)
    if resp.status_code == 429:
        print("  Rate limit, waiting 60s...")
        time.sleep(60)
        resp = requests.get(f"{SG_BASE}/performers", params=params, timeout=15)
    if resp.status_code != 200:
        return None, None

    performers = resp.json().get("performers", [])
    if not performers:
        return None, None

    name_lower = name.lower()

    # Exact match first
    for p in performers:
        if p["name"].lower() == name_lower:
            return p["id"], p["name"]

    # Match ignoring "The" prefix
    for p in performers:
        p_clean = p["name"].lower().removeprefix("the ").strip()
        n_clean = name_lower.removeprefix("the ").strip()
        if p_clean == n_clean:
            return p["id"], p["name"]

    return None, None


def main():
    if not SG_CLIENT_ID:
        print("x Set SEATGEEK_CLIENT_ID environment variable")
        sys.exit(1)

    pattern = os.path.join(ROOT, "bands", "*.md")
    files = sorted(glob.glob(pattern))

    found = 0
    skipped = 0
    not_found = 0

    for i, path in enumerate(files, 1):
        post = frontmatter.load(path)
        if not post.get("active", False):
            continue

        name = post["name"]

        if post.get("seatgeek_id"):
            print(f"[{i}/{len(files)}] {name} — already has SG ID: {post['seatgeek_id']}")
            skipped += 1
            continue

        sg_id, sg_name = find_performer_id(name)

        if sg_id:
            found += 1
            post["seatgeek_id"] = sg_id
            with open(path, "w", encoding="utf-8") as f:
                f.write(frontmatter.dumps(post))
            match_note = "" if sg_name.lower() == name.lower() else f" (SG name: {sg_name})"
            print(f"[{i}/{len(files)}] {name} -> {sg_id}{match_note}")
        else:
            not_found += 1
            print(f"[{i}/{len(files)}] {name} — NOT FOUND")

        time.sleep(0.25)

    print(f"\nDone! Found: {found}, Skipped: {skipped}, Not found: {not_found}")


if __name__ == "__main__":
    main()
