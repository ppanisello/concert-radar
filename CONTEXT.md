# CONTEXT.md — Registro de trabajo con Claude

## Archivos modificados y cambios realizados

### Commit `bd545f3` — Initial commit: project structure and config
- Creación de la estructura del proyecto: carpetas `bands/`, `events/`, `reports/`, `config/`, `.github/workflows/`.
- `config/settings.md`: configuración inicial con frontmatter (cluster_window_days: 7, cluster_min_shows: 2, lookahead_days: 180, priority_filter: todas).

### Commit `cf109ca` — Add Helloween band profile
- `bands/helloween.md`: primer perfil de banda creado como ejemplo.

### Commit `af36634` — Add 130 band profiles with priority levels
- 130 archivos `.md` en `bands/`: cada uno con frontmatter (name, active, priority, genres) y tabla de tour log.
- Distribución: 15 prioridad alta, 21 media, 94 baja.

### Commit `09a896c` — Add genres to all 130 band profiles
- Actualización de los 130 perfiles para incluir el campo `genres` en el frontmatter.

### Commit `28f5113` — Add radar.py script and .gitignore
- `scripts/radar.py`: script inicial que usaba la API de Bandsintown para escanear eventos y generar reportes con análisis de Claude.
- `.gitignore`: agregado con entradas básicas.

### Commit `a2bd590` — Switch to Ticketmaster API and generate first report
- `scripts/radar.py`: reescritura completa para usar Ticketmaster Discovery API en lugar de Bandsintown. Incluye manejo de rate limiting, filtrado de atracciones por nombre exacto, y generación de `upcoming-raw.md`.
- `bands/*.md` (8 archivos): corrección de entradas de género R&B corruptas (beverley-knight, emeli-sand, gary-clark-jr, jools-holland, kc-and-the-sunshine-band, nile-rodgers-chic, sugababes, the-doobie-brothers).
- `.gitignore`: agregado `.songkick_cache.json`.
- `events/upcoming-raw.md`: generado con 880 eventos de 130 bandas escaneadas vía Ticketmaster.
- `reports/2026-W12.md`: primer reporte de clusters generado por Claude (8 clusters identificados).

### Cambios sin commitear (working tree)
- `scripts/radar.py`: dos mejoras pendientes:
  1. **Geolocalización precisa:** agrega state/province a las ciudades de US/CA (ej: "Las Vegas, NV" en lugar de solo "Las Vegas") para evitar ambigüedad.
  2. **Prompt de Claude mejorado:** reglas de agrupación más estrictas — geolocalización exacta (no agrupar ciudades distintas), score ponderado por prioridad (alta=3, media=2, baja=1), detección de residencias (3+ fechas mismo venue), y formato de salida con columna de Notas.

## Corridas del script y resultados

### Corrida 1 (commit `a2bd590`)
- **Bandas escaneadas:** 130
- **Eventos encontrados:** 880
- **Ciudades:** 318
- **Países:** 17
- **Fuente:** Ticketmaster Discovery API
- **Resultado:** reporte `reports/2026-W12.md` con 8 clusters:
  1. Las Vegas, USA — 20 shows, score 8/10 (Helloween, Cheap Trick, Journey, Duran Duran, Quiet Riot)
  2. New York, USA — 10 shows, score 7/10 (Helloween en Silver Spring, Boston y NYC agrupados erróneamente)
  3. London, GB — 15 shows, score 6/10
  4. Manchester, GB — 8 shows, score 6/10
  5. Plzeň, CZ — 6 shows, score 7/10 (Metalfest: Accept, Avantasia, Gotthard)
  6. Edinburgh, GB — 4 shows, score 5/10
  7. Glasgow, GB — 6 shows, score 5/10
  8. Toronto, CA — 6 shows, score 7/10 (Foreigner, Iron Maiden)

## Errores encontrados

1. **Agrupación geográfica incorrecta en el reporte W12:** Claude agrupó "Silver Spring", "Boston" y "New York" como un solo cluster "New York". Esto viola la regla de agrupar solo por ciudad exacta. Se corrigió en el prompt mejorado (cambios sin commitear).

2. **Score sin ponderar por prioridad:** el primer reporte usaba un score genérico (1-10) sin considerar las prioridades (alta/media/baja). Se corrigió agregando fórmula explícita al prompt.

3. **Ciudades ambiguas en US/CA:** "Las Vegas" sin estado podía confundirse; no se diferenciaban ciudades homónimas. Se corrigió agregando el stateCode al nombre de ciudad para US y Canadá.

4. **Géneros R&B corruptos:** 8 archivos de bandas tenían el campo `genres` con caracteres corruptos para "R&B". Corregido en commit `a2bd590`.

## Qué queda pendiente

1. **Commitear los cambios de radar.py:** las mejoras de geolocalización y prompt están sin commitear.
2. **Re-ejecutar el script** con las mejoras para generar un reporte W12 corregido (o W13 según la fecha).
3. **Validar el reporte corregido:** verificar que los clusters respeten geolocalización exacta y que los scores sean coherentes con la fórmula de prioridad.
4. **GitHub Actions workflow:** la carpeta `.github/workflows/` existe pero está vacía — falta configurar la automatización semanal del script.
5. **Falsos positivos de Ticketmaster:** la API de keyword search es fuzzy; el filtro por `attractions` ayuda, pero artistas como "Adele" devuelven tributos. Podría mejorarse con matching más estricto o filtro por attraction ID.
6. **Cobertura de artistas:** de 130 bandas, algunas pueden no tener presencia en Ticketmaster (especialmente artistas argentinos como Nito Mestre, JAF, Hilda Lizarazu). Evaluar fuentes complementarias.
7. **Manejo de duplicados en Ticketmaster:** algunos eventos aparecen duplicados (ej: Accept en Plzeň con dos tickets distintos para el mismo show/festival). El script no deduplica.
