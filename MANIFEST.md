# MANIFEST — Proyecto Concert Radar

> Archivo de entrada. Cualquier IA que trabaje aquí debe leerlo primero.
> Ver también `CONTEXT.md` para el historial de cambios y resultados de corridas.

---

## Qué es

Radar automático de conciertos: escanea eventos de 130+ bandas seguidas usando la API de Ticketmaster, detecta clusters geográficos (varias bandas en la misma ciudad en fechas cercanas) y genera reportes semanales con análisis de Claude.

**Caso de uso:** planificar viajes para ver múltiples bandas en una misma ciudad.

---

## Stack

- **Python** — script principal `scripts/radar.py`
- **Ticketmaster Discovery API** — fuente de eventos
- **Claude API** — análisis y generación de reportes
- **GitHub Actions** — automatización (pendiente configurar para corrida semanal)

---

## Estructura de carpetas

```
concert-radar/
├── MANIFEST.md                  ← este archivo
├── CONTEXT.md                   ← historial de cambios, corridas y errores (changelog)
│
├── bands/                       ← 130+ perfiles de bandas (YAML frontmatter + tour log)
│   └── helloween.md             ← ejemplo de perfil
│
├── config/
│   └── settings.md              ← configuración (cluster_window_days, lookahead_days, etc.)
│
├── events/
│   └── upcoming-raw.md          ← eventos crudos de la última corrida
│
├── reports/                     ← reportes semanales generados
│   └── 2026-W12.md              ← ejemplo de reporte
│
├── scripts/
│   ├── radar.py                 ← script principal
│   ├── lookup_ids.py            ← busca IDs de artistas en Ticketmaster
│   └── lookup_seatgeek_ids.py   ← busca IDs en SeatGeek (alternativo)
│
└── docs/                        ← documentación
```

---

## Configuración (`config/settings.md`)

| Parámetro | Valor | Descripción |
|-----------|-------|-------------|
| `cluster_window_days` | 7 | Ventana de días para agrupar shows en un cluster |
| `cluster_min_shows` | 2 | Mínimo de shows para considerar un cluster |
| `lookahead_days` | 180 | Horizonte de búsqueda en días |
| `priority_filter` | todas | Prioridades a incluir (alta/media/baja) |

---

## Bandas — prioridades

- **Alta (15 bandas):** las que sí o sí ver si hay cluster
- **Media (21 bandas):** ver si cae bien con otras
- **Baja (94 bandas):** bonus si aparecen en un cluster bueno

Score de cluster: alta=3pts, media=2pts, baja=1pt por show.

---

## Cómo correr

```bash
cd scripts/
export $(cat ../.env | tr -d '\r' | xargs)
python radar.py
```

El `.env` tiene `TICKETMASTER_API_KEY` y `ANTHROPIC_API_KEY`.

---

## Instrucciones para la IA

1. El historial de cambios y errores anteriores está en `CONTEXT.md` — leerlo antes de tocar `radar.py`
2. Los perfiles de bandas en `bands/` siguen el formato con frontmatter YAML
3. Para agregar una banda: crear `bands/nombre-banda.md` con el formato estándar
4. El script genera `events/upcoming-raw.md` y luego usa Claude para crear el reporte en `reports/`
5. Reportes van en `reports/YYYY-WNN.md` (año + número de semana ISO)
