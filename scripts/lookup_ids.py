#!/usr/bin/env python3
"""Look up Ticketmaster attraction IDs for all active bands and update .md files."""

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

TM_API_KEY = os.environ.get("TICKETMASTER_API_KEY")
ATTRACTIONS_URL = "https://app.ticketmaster.com/discovery/v2/attractions.json"


def find_attraction_id(name):
    """Search TM attractions API and return best match (id, tm_name) or (None, None)."""
    params = {
        "keyword": name,
        "classificationName": "music",
        "apikey": TM_API_KEY,
        "size": 10,
    }
    resp = requests.get(ATTRACTIONS_URL, params=params, timeout=15)
    if resp.status_code == 429:
        print("  Rate limit, waiting 60s...")
        time.sleep(60)
        resp = requests.get(ATTRACTIONS_URL, params=params, timeout=15)
    if resp.status_code != 200:
        return None, None

    data = resp.json()
    attractions = data.get("_embedded", {}).get("attractions", [])
    if not attractions:
        return None, None

    # Try exact match (case-insensitive) first
    name_lower = name.lower()
    for a in attractions:
        if a["name"].lower() == name_lower:
            return a["id"], a["name"]

    # Try match ignoring "The" prefix
    for a in attractions:
        a_clean = a["name"].lower().removeprefix("the ").strip()
        n_clean = name_lower.removeprefix("the ").strip()
        if a_clean == n_clean:
            return a["id"], a["name"]

    return None, None


def main():
    if not TM_API_KEY:
        print("x Set TICKETMASTER_API_KEY")
        sys.exit(1)

    pattern = os.path.join(ROOT, "bands", "*.md")
    files = sorted(glob.glob(pattern))

    found = 0
    skipped = 0
    not_found = 0
    updated = 0

    for i, path in enumerate(files, 1):
        post = frontmatter.load(path)
        if not post.get("active", False):
            continue

        name = post["name"]

        if post.get("ticketmaster_id"):
            print(f"[{i}/{len(files)}] {name} — already has ID: {post['ticketmaster_id']}")
            skipped += 1
            continue

        tm_id, tm_name = find_attraction_id(name)

        if tm_id:
            found += 1
            post["ticketmaster_id"] = tm_id
            with open(path, "w", encoding="utf-8") as f:
                f.write(frontmatter.dumps(post))
            updated += 1
            match_note = "" if tm_name.lower() == name.lower() else f" (TM name: {tm_name})"
            print(f"[{i}/{len(files)}] {name} -> {tm_id}{match_note}")
        else:
            not_found += 1
            print(f"[{i}/{len(files)}] {name} — NOT FOUND")

        # Rate limit: 5 req/sec
        time.sleep(0.25)

    print(f"\nDone! Found: {found}, Skipped: {skipped}, Not found: {not_found}, Updated: {updated}")


if __name__ == "__main__":
    main()
