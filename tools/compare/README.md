# Comparar nuestras condiciones con SendScore y 8a.nu (escuelas de España)

Scripts de usar y tirar, con rutas relativas al directorio de trabajo (p. ej. `/tmp/ss`):

1. `fetch_ss.py` — escuelas de SendScore en España (teselas z8 de su mapa, `/api/crags_tiles_batch`) → `ss_es.json`.
   Antes, `curl localhost:8472/_proxy/map/crags` filtrado a España → `8a_es.json`.
2. `match.py` — empareja por distancia (< 3 km) y nombre → `pairs.json`.
3. `ours.py` — nuestras condiciones (`/_proxy/conditions/…/data`) y el `conditionsSummary` de 8a.nu → `cmp.json`.
4. `ss_local.py` — horas de SendScore (`/api/local_crags`) → `ss_local.json`.
5. `analyze.py` — correlación hora a hora y medias por tramos de temperatura, viento, humedad, nubes y sol.

Ojo: el estado de roca de 8a.nu (`/conditions` → `weather.rock`) es una foto de Open-Meteo de hace horas
(`provenance.snapshotAt`): compararlo con nuestra hora de esa foto, no con «ahora». Su «wet» es casi «está lloviendo».
