#!/usr/bin/env python3
"""Concert Radar — scans Bandsintown for upcoming events and generates
cluster reports via Claude."""

import os
import sys
import time
import glob as globmod
from datetime import datetime, timedelta

import frontmatter
import requests
import anthropic

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 1. CONFIGURACIÓN ────────────────────────────────────────────────

def load_settings():
    path = os.path.join(ROOT, "config", "settings.md")
    post = frontmatter.load(path)
    return {
        "cluster_window_days": int(post.get("cluster_window_days", 7)),
        "cluster_min_shows": int(post.get("cluster_min_shows", 2)),
        "lookahead_days": int(post.get("lookahead_days", 180)),
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
            "bandsintown_id": post.get("bandsintown_id", post["name"]),
            "priority": priority,
        })
    return bands


# ── 2. BANDSINTOWN API ──────────────────────────────────────────────

def fetch_events(band, app_id, cutoff_date):
    url = (
        f"https://rest.bandsintown.com/artists/"
        f"{band['bandsintown_id']}/events"
    )
    params = {"app_id": app_id}
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 429:
            print(f"  ⏳ Rate limit hit, waiting 60s…")
            time.sleep(60)
            resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 404:
            print(f"  ⚠ {band['name']}: not found on Bandsintown")
            return []
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return []
    except requests.RequestException as exc:
        print(f"  ✗ {band['name']}: {exc}")
        return []

    today = datetime.utcnow().date()
    events = []
    for ev in data:
        try:
            dt = datetime.strptime(ev["datetime"], "%Y-%m-%dT%H:%M:%S").date()
        except (ValueError, KeyError):
            continue
        if today <= dt <= cutoff_date:
            venue = ev.get("venue", {})
            events.append({
                "date": dt.isoformat(),
                "city": venue.get("city", ""),
                "country": venue.get("country", ""),
                "venue": venue.get("name", ""),
                "ticket_url": ev.get("url", ""),
            })
    return events


def scan_all(bands, app_id, lookahead_days):
    cutoff = datetime.utcnow().date() + timedelta(days=lookahead_days)
    results = {}
    for i, band in enumerate(bands, 1):
        print(f"[{i}/{len(bands)}] {band['name']}…")
        events = fetch_events(band, app_id, cutoff)
        if events:
            results[band["name"]] = {
                "priority": band["priority"],
                "events": events,
            }
            print(f"  ✓ {len(events)} events")
        else:
            print(f"  – no upcoming events")
        # Be polite with the API
        if i < len(bands):
            time.sleep(0.5)
    return results


# ── 3. ESCRIBIR upcoming-raw.md ─────────────────────────────────────

def write_raw(results, bands_scanned):
    all_events = [e for info in results.values() for e in info["events"]]
    cities = {e["city"] for e in all_events if e["city"]}
    countries = {e["country"] for e in all_events if e["country"]}

    lines = [
        "---",
        f"generated_at: {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"bands_scanned: {bands_scanned}",
        f"events_found: {len(all_events)}",
        f"cities_scanned: {len(cities)}",
        f"countries_scanned: {len(countries)}",
        "source: Bandsintown API",
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
    print(f"\n📄 Wrote {path}")
    return path


# ── 4. ANÁLISIS CON CLAUDE ──────────────────────────────────────────

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

Tu tarea:
1. Agrupar los eventos por **ciudad** dentro de una ventana de **{settings['cluster_window_days']} días**.
2. Identificar clusters que tengan al menos **{settings['cluster_min_shows']} shows** de bandas distintas.
3. Generar un reporte en Markdown ordenado por cantidad de shows **descendente**.

Para cada cluster incluí:
- Ciudad y país
- Rango de fechas del cluster
- Cantidad de shows
- Lista de bandas con fecha, venue y prioridad
- Un score de atractivo (1-10) basado en prioridad de las bandas y cantidad

Formato de salida (solo el contenido, sin frontmatter):

## 1. Ciudad, País (N shows)
**Fechas:** YYYY-MM-DD → YYYY-MM-DD
**Score:** X/10

| Banda | Fecha | Venue | Prioridad |
|-------|-------|-------|-----------|
| ... | ... | ... | ... |

---

Si no hay clusters que cumplan el mínimo, indicalo claramente.
Respondé solo con el reporte en Markdown, sin explicaciones adicionales."""

    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ── 5. ESCRIBIR REPORTE SEMANAL ─────────────────────────────────────

def write_report(analysis):
    now = datetime.utcnow()
    week = now.isocalendar()[1]
    filename = f"{now.year}-W{week:02d}.md"

    lines = [
        "---",
        f"generated_at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"week: {now.year}-W{week:02d}",
        "type: cluster-report",
        "---",
        "",
        f"# Concert Radar — Semana {now.year}-W{week:02d}",
        "",
        analysis,
    ]

    path = os.path.join(ROOT, "reports", filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"📊 Wrote {path}")
    return path


# ── MAIN ─────────────────────────────────────────────────────────────

def main():
    app_id = os.environ.get("BANDSINTOWN_APP_ID")
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not app_id:
        print("✗ Set BANDSINTOWN_APP_ID environment variable")
        sys.exit(1)
    if not api_key:
        print("✗ Set ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    print("═" * 50)
    print("  Concert Radar 🎸")
    print("═" * 50)

    # 1. Config
    settings = load_settings()
    print(f"\n⚙ Settings: lookahead={settings['lookahead_days']}d, "
          f"cluster_window={settings['cluster_window_days']}d, "
          f"min_shows={settings['cluster_min_shows']}, "
          f"filter={settings['priority_filter']}")

    # 2. Bands
    bands = load_bands(settings["priority_filter"])
    print(f"🎤 Loaded {len(bands)} active bands\n")

    if not bands:
        print("No bands to scan.")
        sys.exit(0)

    # 3. Scan Bandsintown
    results = scan_all(bands, app_id, settings["lookahead_days"])

    if not results:
        print("\nNo upcoming events found for any band.")
        sys.exit(0)

    # 4. Write raw events
    raw_path = write_raw(results, len(bands))

    # 5. Claude analysis
    print("\n🤖 Analyzing with Claude…")
    analysis = analyze_with_claude(raw_path, settings)

    # 6. Write report
    report_path = write_report(analysis)

    print(f"\n✅ Done! Report ready at {report_path}")


if __name__ == "__main__":
    main()
