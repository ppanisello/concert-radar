#!/usr/bin/env python3
"""Concert Radar — scans Ticketmaster for upcoming events, clusters them
by city+country within a time window, and generates reports via Claude."""

import json
import os
import sys
import time
import glob as globmod
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
import frontmatter
import requests
import anthropic

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

TM_BASE = "https://app.ticketmaster.com/discovery/v2/events.json"


# ── 1. CONFIGURACION ────────────────────────────────────────────────

def load_settings():
    path = os.path.join(ROOT, "config", "settings.md")
    post = frontmatter.load(path)
    return {
        "cluster_window_days": int(post.get("cluster_window_days", 7)),
        "cluster_min_shows": int(post.get("cluster_min_shows", 2)),
        "lookahead_days": int(post.get("lookahead_days", 180)),
        "lookahead_dream_days": int(post.get("lookahead_dream_days", 227)),
        "priority_filter": post.get("priority_filter", "todas"),
    }


def load_bands(priority_filter):
    bands = []
    pattern = os.path.join(ROOT, "bands", "*.md")
    for path in sorted(globmod.glob(pattern)):
        post = frontmatter.load(path)
        if not post.get("active", False):
            continue
        priority = post.get("priority", "baja")
        if priority_filter != "todas" and priority != priority_filter:
            continue
        band = {
            "name": post["name"],
            "priority": priority,
        }
        if post.get("ticketmaster_id"):
            band["ticketmaster_id"] = post["ticketmaster_id"]
        bands.append(band)
    return bands


# ── 2. TICKETMASTER API ─────────────────────────────────────────────

def _fetch_page(params, page=0):
    """Fetch a single page from Ticketmaster, handling rate limits."""
    params = {**params, "page": page}
    resp = requests.get(TM_BASE, params=params, timeout=15)
    if resp.status_code == 429:
        print(f"  Rate limit, waiting 60s...")
        time.sleep(60)
        resp = requests.get(TM_BASE, params=params, timeout=15)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def fetch_events(band, api_key, cutoff_date):
    today = datetime.now(timezone.utc).date()
    params = {
        "classificationName": "music",
        "apikey": api_key,
        "size": 50,
        "sort": "date,asc",
        "startDateTime": today.strftime("%Y-%m-%dT00:00:00Z"),
        "endDateTime": cutoff_date.strftime("%Y-%m-%dT23:59:59Z"),
    }
    # Use attractionId for precise matching when available
    if band.get("ticketmaster_id"):
        params["attractionId"] = band["ticketmaster_id"]
    else:
        params["keyword"] = band["name"]

    # Paginate through all results (max 5 pages = 250 events)
    raw_events = []
    for page in range(5):
        try:
            data = _fetch_page(params, page)
        except requests.RequestException as exc:
            print(f"  x {band['name']}: {exc}")
            return []
        if data is None:
            break
        embedded = data.get("_embedded")
        if not embedded:
            break
        raw_events.extend(embedded.get("events", []))
        total_pages = data.get("page", {}).get("totalPages", 1)
        if page + 1 >= total_pages:
            break
        time.sleep(0.2)
    events = []
    for ev in raw_events:
        # Check the event is actually for this artist (keyword search is fuzzy)
        ev_embedded = ev.get("_embedded", {})
        attraction_names = [
            attr.get("name", "").lower()
            for attr in ev_embedded.get("attractions", [])
        ]
        # Skip events with no attractions — likely mismatches (city names, etc.)
        if not attraction_names:
            continue
        if band["name"].lower() not in attraction_names:
            continue

        dates = ev.get("dates", {})
        start = dates.get("start", {})
        date_str = start.get("localDate")
        if not date_str:
            continue

        # Geolocation comes strictly from the venue object
        venues = ev_embedded.get("venues", [{}])
        venue = venues[0] if venues else {}

        venue_city = venue.get("city", {}).get("name", "")
        venue_state = venue.get("state", {})
        state_code = venue_state.get("stateCode", "") or venue_state.get("name", "")
        venue_country = venue.get("country", {}).get("name", "")

        if not venue_city or not venue_country:
            continue

        # Coordinates from venue
        location = venue.get("location", {})
        lat = float(location.get("latitude", 0)) if location.get("latitude") else 0
        lng = float(location.get("longitude", 0)) if location.get("longitude") else 0

        # Build precise location: "City, State" for US/CA, "City" otherwise
        if state_code and venue_country in ("United States Of America", "Canada"):
            precise_city = f"{venue_city}, {state_code}"
        else:
            precise_city = venue_city

        events.append({
            "date": date_str,
            "city": precise_city,
            "country": venue_country,
            "venue": venue.get("name", ""),
            "ticket_url": ev.get("url", ""),
            "lat": lat,
            "lng": lng,
        })

    # Deduplicate by (date, city, country)
    seen = set()
    deduped = []
    for ev in events:
        key = (ev["date"], ev["city"], ev["country"])
        if key not in seen:
            seen.add(key)
            deduped.append(ev)
    return deduped


def scan_all(bands, api_key, lookahead_days, lookahead_dream_days):
    cutoff_default = datetime.now(timezone.utc).date() + timedelta(days=lookahead_days)
    cutoff_dream = datetime.now(timezone.utc).date() + timedelta(days=lookahead_dream_days)
    results = {}

    for i, band in enumerate(bands, 1):
        print(f"[{i}/{len(bands)}] {band['name']}...")
        cutoff = cutoff_dream if band["priority"] in ("dream", "alta") else cutoff_default
        events = fetch_events(band, api_key, cutoff)
        if events:
            results[band["name"]] = {
                "priority": band["priority"],
                "events": events,
            }
            print(f"  + {len(events)} events")
        else:
            print(f"  - no upcoming events")
        # Ticketmaster rate limit: 5 req/sec
        if i < len(bands):
            time.sleep(0.25)
    return results


# ── 3. ESCRIBIR upcoming-raw.md ─────────────────────────────────────

def write_raw(results, bands_scanned):
    all_events = [e for info in results.values() for e in info["events"]]
    cities = {e["city"] for e in all_events if e["city"]}
    countries = {e["country"] for e in all_events if e["country"]}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        "---",
        f"generated_at: {now}",
        f"bands_scanned: {bands_scanned}",
        f"events_found: {len(all_events)}",
        f"cities_scanned: {len(cities)}",
        f"countries_scanned: {len(countries)}",
        "source: Ticketmaster Discovery API",
        "---",
        "",
    ]

    for band_name in sorted(results.keys()):
        info = results[band_name]
        lines.append(f"## {band_name} (prioridad: {info['priority']})")
        lines.append("")
        lines.append("| Date | City | Country | Venue | Tickets |")
        lines.append("|------|------|---------|-------|---------|")
        for ev in sorted(info["events"], key=lambda e: e["date"]):
            ticket = f"[link]({ev['ticket_url']})" if ev["ticket_url"] else ""
            lines.append(
                f"| {ev['date']} | {ev['city']} | {ev['country']} "
                f"| {ev['venue']} | {ticket} |"
            )
        lines.append("")

    path = os.path.join(ROOT, "events", "upcoming-raw.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nWrote {path}")
    return path


# ── 4. CLUSTERING EN PYTHON ─────────────────────────────────────────

def cluster_events(results, settings):
    """Group events by city+country, then split into time-windowed clusters."""
    window = settings["cluster_window_days"]
    min_artists = settings["cluster_min_shows"]

    # Flatten all events with their band info
    flat = []
    for band_name, info in results.items():
        for ev in info["events"]:
            flat.append({
                "band": band_name,
                "priority": info["priority"],
                "date": ev["date"],
                "city": ev["city"],
                "country": ev["country"],
                "venue": ev["venue"],
                "ticket_url": ev.get("ticket_url", ""),
            })

    # Group by city+country
    by_location = defaultdict(list)
    for ev in flat:
        key = (ev["city"], ev["country"])
        by_location[key].append(ev)

    clusters = []
    for (city, country), events in by_location.items():
        # Sort by date
        events.sort(key=lambda e: e["date"])

        # Split into time windows
        current_cluster = [events[0]]
        cluster_start = date.fromisoformat(events[0]["date"])

        for ev in events[1:]:
            ev_date = date.fromisoformat(ev["date"])
            if (ev_date - cluster_start).days <= window:
                current_cluster.append(ev)
            else:
                # Close current cluster and start new one
                clusters.append(_build_cluster(city, country, current_cluster))
                current_cluster = [ev]
                cluster_start = ev_date

        # Don't forget the last cluster
        clusters.append(_build_cluster(city, country, current_cluster))

    # Filter: at least min_artists distinct artists
    valid = [c for c in clusters if c["unique_artists"] >= min_artists]

    print(f"\nClustering: {len(clusters)} raw clusters -> "
          f"{len(valid)} valid (>= {min_artists} distinct artists)")

    return valid


def _build_cluster(city, country, events):
    """Build a cluster dict from a list of events in the same city+window."""
    dates = [e["date"] for e in events]
    unique = {}
    for e in events:
        if e["band"] not in unique:
            unique[e["band"]] = e["priority"]

    return {
        "city": city,
        "country": country,
        "date_from": min(dates),
        "date_to": max(dates),
        "events": events,
        "unique_artists": len(unique),
        "total_events": len(events),
        "artist_priorities": unique,
    }


# ── 5. ANALISIS CON CLAUDE ──────────────────────────────────────────

def analyze_with_claude(clusters):
    """Send pre-computed clusters as JSON; Claude scores, detects residencies,
    flags tributes, and formats the Markdown report."""

    clusters_json = json.dumps(clusters, ensure_ascii=False, indent=2)

    prompt = f"""Sos un analista de conciertos. Te paso clusters de eventos ya agrupados por ciudad y ventana temporal (el agrupamiento ya esta hecho, NO lo cambies).

## Clusters (JSON)
```json
{clusters_json}
```

Tu tarea: calcular scores, detectar residencias, marcar tributos, y formatear el reporte.

### Reglas

1. **NO reagrupar.** Cada cluster del JSON es un cluster final. No combines ni separes clusters.

2. **Score ponderado por prioridad:**
   - Cada artista UNICO suma puntos: dream=3, alta=3, media=2, baja=1
   - Si un artista tiene multiples fechas, cuenta UNA sola vez
   - Artistas marcados como posible tributo (?) NO suman al score
   - Score = (suma de puntos) / (cantidad de artistas que suman * 3) * 10
   - Redondear a un decimal

3. **Residencias:** Si un mismo artista tiene 3+ fechas en el mismo venue dentro del cluster, marcarlo con (R) en Notas.

4. **Posibles tributos:** Si un artista tiene prioridad alta o dream y el venue es un bar, brewery, pub, o club pequeno (no un teatro, arena, estadio o sala de conciertos reconocida), marcarlo con (?) en Notas. NO contarlo para el score.

### Formato de salida

Ordenar clusters por score descendente. Solo el contenido Markdown, sin frontmatter ni explicaciones.

## 1. City, Country (N artistas unicos / M eventos totales)
**Fechas:** YYYY-MM-DD -> YYYY-MM-DD
**Score:** X.X/10

| Banda | Fecha | Venue | Prioridad | Notas |
|-------|-------|-------|-----------|-------|
| ... | ... | ... | ... | (R) y/o (?) |

---

Si no hay clusters, indicalo claramente."""

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16384,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ── 6. ESCRIBIR REPORTE SEMANAL ─────────────────────────────────────

def write_report(analysis):
    now = datetime.now(timezone.utc)
    week = now.isocalendar()[1]
    filename = f"{now.year}-W{week:02d}.md"

    lines = [
        "---",
        f"generated_at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"week: {now.year}-W{week:02d}",
        "type: cluster-report",
        "---",
        "",
        f"# Concert Radar -- Semana {now.year}-W{week:02d}",
        "",
        analysis,
    ]

    path = os.path.join(ROOT, "reports", filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote report: {path}")
    return path


# ── 7. GENERAR MAPA ──────────────────────────────────────────────────

def generate_map(results):
    """Aggregate events by city, produce docs/index.html with Leaflet map."""
    PRIORITY_ORDER = {"dream": 0, "alta": 1, "media": 2, "baja": 3}

    cities = {}
    for band_name, info in results.items():
        for ev in info["events"]:
            key = (ev["city"], ev["country"])
            if key not in cities:
                cities[key] = {
                    "city": ev["city"],
                    "country": ev["country"],
                    "lat": ev.get("lat", 0),
                    "lng": ev.get("lng", 0),
                    "bands": {},
                    "total_events": 0,
                }
            c = cities[key]
            c["total_events"] += 1
            # Keep best coordinates (non-zero)
            if ev.get("lat") and ev.get("lng") and not c["lat"]:
                c["lat"] = ev["lat"]
                c["lng"] = ev["lng"]
            if band_name not in c["bands"]:
                c["bands"][band_name] = {
                    "priority": info["priority"],
                    "dates": [],
                    "venues": set(),
                }
            c["bands"][band_name]["dates"].append(ev["date"])
            c["bands"][band_name]["venues"].add(ev["venue"])

    data = []
    for key, c in cities.items():
        if not c["lat"] or not c["lng"]:
            continue
        bands_list = []
        best_priority = "baja"
        for name, b in sorted(c["bands"].items()):
            if PRIORITY_ORDER.get(b["priority"], 3) < PRIORITY_ORDER.get(best_priority, 3):
                best_priority = b["priority"]
            bands_list.append({
                "name": name,
                "priority": b["priority"],
                "dates": sorted(set(b["dates"])),
                "venues": sorted(b["venues"]),
            })
        data.append({
            "city": c["city"],
            "country": c["country"],
            "lat": c["lat"],
            "lng": c["lng"],
            "total_events": c["total_events"],
            "unique_artists": len(c["bands"]),
            "best_priority": best_priority,
            "bands": bands_list,
        })

    import re

    data_json = json.dumps(data, ensure_ascii=False)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    docs_dir = os.path.join(ROOT, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    path = os.path.join(docs_dir, "index.html")

    with open(path, "r", encoding="utf-8") as f:
        html = f.read()

    # Replace the DATA placeholder or existing DATA array
    html = re.sub(
        r'const DATA = .+?;',
        f'const DATA = {data_json};',
        html,
        count=1,
        flags=re.DOTALL,
    )
    # Update the timestamp
    html = re.sub(
        r'Updated .+? [·&]',
        f'Updated {now} ·',
        html,
        count=1,
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {path}")
    return path


# ── MAIN ─────────────────────────────────────────────────────────────

def main():
    tm_key = os.environ.get("TICKETMASTER_API_KEY")
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not tm_key:
        print("x Set TICKETMASTER_API_KEY environment variable")
        sys.exit(1)
    if not api_key:
        print("x Set ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    print("=" * 50)
    print("  Concert Radar")
    print("=" * 50)

    # 1. Config
    settings = load_settings()
    print(f"\nSettings: lookahead={settings['lookahead_days']}d, "
          f"cluster_window={settings['cluster_window_days']}d, "
          f"min_shows={settings['cluster_min_shows']}, "
          f"filter={settings['priority_filter']}")

    # 2. Bands
    bands = load_bands(settings["priority_filter"])
    print(f"Loaded {len(bands)} active bands\n")

    if not bands:
        print("No bands to scan.")
        sys.exit(0)

    # 3. Scan Ticketmaster
    results = scan_all(bands, tm_key, settings["lookahead_days"], settings["lookahead_dream_days"])

    if not results:
        print("\nNo upcoming events found for any band.")
        sys.exit(0)

    # 4. Write raw events
    raw_path = write_raw(results, len(bands))

    # 5. Cluster in Python
    clusters = cluster_events(results, settings)

    if not clusters:
        print("\nNo valid clusters found.")
        sys.exit(0)

    # 6. Claude analysis (score + format only)
    print("\nAnalyzing with Claude...")
    analysis = analyze_with_claude(clusters)

    # 7. Write report
    report_path = write_report(analysis)

    # 8. Generate map
    print("\nGenerating map...")
    generate_map(results)
    print("Map ready at docs/index.html")

    print(f"\nDone! Report ready at {report_path}")


if __name__ == "__main__":
    main()
