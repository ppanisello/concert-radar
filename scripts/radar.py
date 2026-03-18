#!/usr/bin/env python3
"""Concert Radar — scans Ticketmaster for upcoming events and generates
cluster reports via Claude."""

import os
import sys
import time
import glob as globmod
from datetime import datetime, timedelta, timezone

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
        bands.append({
            "name": post["name"],
            "priority": priority,
        })
    return bands


# ── 2. TICKETMASTER API ─────────────────────────────────────────────

def fetch_events(band, api_key, cutoff_date):
    today = datetime.now(timezone.utc).date()
    params = {
        "keyword": band["name"],
        "classificationName": "music",
        "apikey": api_key,
        "size": 50,
        "sort": "date,asc",
        "startDateTime": today.strftime("%Y-%m-%dT00:00:00Z"),
        "endDateTime": cutoff_date.strftime("%Y-%m-%dT23:59:59Z"),
    }
    try:
        resp = requests.get(TM_BASE, params=params, timeout=15)
        if resp.status_code == 429:
            print(f"  Rate limit, waiting 60s...")
            time.sleep(60)
            resp = requests.get(TM_BASE, params=params, timeout=15)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"  x {band['name']}: {exc}")
        return []

    embedded = data.get("_embedded")
    if not embedded:
        return []

    raw_events = embedded.get("events", [])
    events = []
    for ev in raw_events:
        # Check the event is actually for this artist (keyword search is fuzzy)
        attractions = []
        ev_embedded = ev.get("_embedded", {})
        for attr in ev_embedded.get("attractions", []):
            attractions.append(attr.get("name", "").lower())
        if attractions and band["name"].lower() not in attractions:
            continue

        dates = ev.get("dates", {})
        start = dates.get("start", {})
        date_str = start.get("localDate")
        if not date_str:
            continue

        venues = ev_embedded.get("venues", [{}])
        venue = venues[0] if venues else {}

        city_obj = venue.get("city", {})
        state_obj = venue.get("state", {})
        country_obj = venue.get("country", {})

        city_name = city_obj.get("name", "")
        state_name = state_obj.get("stateCode", "") or state_obj.get("name", "")
        country_name = country_obj.get("name", "")

        # Build precise location: "City, State" for US/CA, "City" otherwise
        if state_name and country_name in ("United States Of America", "Canada"):
            precise_city = f"{city_name}, {state_name}"
        else:
            precise_city = city_name

        events.append({
            "date": date_str,
            "city": precise_city,
            "country": country_name,
            "venue": venue.get("name", ""),
            "ticket_url": ev.get("url", ""),
        })
    return events


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


# ── 4. ANALISIS CON CLAUDE ──────────────────────────────────────────

def analyze_with_claude(raw_path, settings):
    with open(raw_path, "r", encoding="utf-8") as f:
        raw_content = f.read()

    settings_path = os.path.join(ROOT, "config", "settings.md")
    with open(settings_path, "r", encoding="utf-8") as f:
        settings_content = f.read()

    prompt = f"""Sos un analista de conciertos. Te paso dos archivos:

## config/settings.md
{settings_content}

## events/upcoming-raw.md
{raw_content}

Tu tarea: agrupar eventos en clusters y generar un reporte.

### Reglas de agrupacion

1. **Geolocalizacion estricta:** Agrupar SOLO por la combinacion exacta de City + Country tal como aparece en los datos. NO agrupar ciudades distintas aunque esten geograficamente cerca. Ejemplo: "Silver Spring" y "Boston" son clusters separados, NO se agrupan como "New York". Cada evento pertenece unicamente al cluster de su city+country exacto.

2. **Ventana temporal:** Dentro de cada city+country, agrupar eventos que caigan dentro de una ventana de **{settings['cluster_window_days']} dias**. Si hay eventos separados por mas de {settings['cluster_window_days']} dias en la misma ciudad, son clusters distintos.

3. **Minimo de artistas distintos:** Un cluster solo es valido si tiene al menos **{settings['cluster_min_shows']} artistas DISTINTOS**. Multiples fechas del mismo artista no cuentan como artistas adicionales.

### Calculo de score

4. **Score ponderado por prioridad:**
   - Cada artista UNICO en el cluster suma puntos segun su prioridad: dream=3, alta=3, media=2, baja=1
   - Si un artista tiene multiples fechas en el cluster, cuenta UNA sola vez para el score
   - Score = (suma de puntos de artistas unicos) / (cantidad de artistas unicos * 3) * 10
   - Redondear a un decimal

5. **Penalizacion por residencias:** Si un mismo artista tiene 3+ fechas en el mismo venue dentro del cluster, ese artista es una "residencia" y se marca con (R) en la tabla. Para el conteo de "shows unicos" del cluster, una residencia cuenta como 1.

### Formato de salida

Ordenar clusters por score descendente. Solo el contenido Markdown, sin frontmatter ni explicaciones.

## 1. City, Country (N artistas unicos / M eventos totales)
**Fechas:** YYYY-MM-DD -> YYYY-MM-DD
**Score:** X.X/10

| Banda | Fecha | Venue | Prioridad | Notas |
|-------|-------|-------|-----------|-------|
| ... | ... | ... | ... | (R) si es residencia |

---

Si no hay clusters que cumplan el minimo de artistas distintos, indicalo claramente."""

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ── 5. ESCRIBIR REPORTE SEMANAL ─────────────────────────────────────

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

    # 5. Claude analysis
    print("\nAnalyzing with Claude...")
    analysis = analyze_with_claude(raw_path, settings)

    # 6. Write report
    report_path = write_report(analysis)

    print(f"\nDone! Report ready at {report_path}")


if __name__ == "__main__":
    main()
