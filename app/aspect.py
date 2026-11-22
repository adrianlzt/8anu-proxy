"""Orientación de la pared de cada escuela, por este orden (cada fuente es una fila aparte en la SQLite, `aspect`, y
ninguna pisa a otra: se elige al leer):

1. la que ha puesto el usuario («Know this crag?»);
2. theCrag (tools/thecrag);
3. OpenStreetMap: los acantilados (`natural=cliff`) a menos de RADIUS m de la escuela. En OSM un acantilado se
   dibuja con la parte de arriba a la izquierda, así que la pared mira a la derecha del sentido de la línea; se hace
   la media de los tramos, pesando por longitud y cercanía;
4. el relieve con modelos digitales de elevación (fuente «dem», tools/dem/aspect.py: MDT05 del IGN en España,
   Copernicus GLO-30 en el resto), con varias caras si las hay y de dónde sale (modelo, resolución, método, fecha).
   Se calcula de una pasada para todas las escuelas del mapa; las que falten, con el mismo script, una a una;
5. «terrain»: lo que se calculó antes con las elevaciones de Open-Meteo (ya no se pide; queda por si no hay DEM);
6. si nada de eso se ha podido calcular todavía, desconocida («?»; para la puntuación se supone sur, y la página
   enseña ese sol solo como posible).

Los acantilados se piden a Overpass por cuadrículas de TILE grados (una petición sirve para todas las escuelas de la
zona) y se guardan en disco. Overpass limita peticiones: un solo trabajador en segundo plano las va haciendo despacio.
"""

import asyncio
import json
import logging
import math
import os
import time
from pathlib import Path

from . import conditions as cond
from . import store

log = logging.getLogger("uvicorn.error").getChild("aspect")  # sale en el journal con el resto de uvicorn
OVERPASS = os.environ.get(
    "OVERPASS_URLS",
    "https://overpass-api.de/api/interpreter https://overpass.private.coffee/api/interpreter "
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
).split()
TILE = 0.25
RADIUS = 250
CLIFF_DIR = Path(os.environ.get("CACHE_DIR", Path.home() / ".cache" / "8anu-proxy")) / "cliffs"
CLIFF_TTL = 60 * 24 * 3600

DEM_TOOL = Path(__file__).resolve().parents[1] / "tools" / "dem" / "aspect.py"
DEM_PYTHON = os.environ.get("DEM_PYTHON", "/usr/bin/python3")  # el del sistema, con GDAL y numpy
DEM_TIMEOUT = 90
PRIORITY = ("user", "thecrag", "osm", "dem", "terrain")

# Dos colas: el DEM es rápido (~1 s, sin cupos) y da algo enseguida; OSM es lento (Overpass) y lo mejora.
_queues: dict[str, asyncio.Queue] = {}
_pending: dict[str, set[str]] = {"dem": set(), "osm": set()}


def current(key: str) -> dict:
    """La mejor orientación guardada de la escuela `key`, sin pedir nada fuera."""
    rows = store.get_aspects(key)
    # Sectores: los de theCrag (tools/thecrag) y encima los que marca el usuario.
    sectors, sector_src = {}, {}
    for source in ("thecrag", "user"):
        for r in rows:
            if r["sector"] and r["source"] == source:
                sectors[r["sector"]], sector_src[r["sector"]] = r["deg"], source
    crag = {r["source"]: r for r in rows if r["sector"] == ""}
    for source in PRIORITY:
        r = crag.get(source)
        if r:
            out = {"deg": r["deg"], "source": source, "compass": cond.compass(r["deg"]), "slope": round(r["slope"] or 0),
                   "sectors": sectors, "sector_src": sector_src, "spread": r.get("spread"),
                   "faces": json.loads(r["faces"]) if r.get("faces") else [],
                   "meta": json.loads(r["meta"]) if r.get("meta") else None}  # fmt: skip
            dem = crag.get("dem")
            if source != "dem" and dem:
                # El relieve como segunda opinión: si discrepa mucho de lo elegido, la página lo dice
                diff = abs((dem["deg"] - r["deg"] + 180) % 360 - 180)
                out["dem"] = {"deg": dem["deg"], "compass": cond.compass(dem["deg"]), "diff": round(diff)}
            return out
    return {"deg": 180.0, "source": "default", "compass": "?", "slope": 0, "sectors": sectors, "sector_src": sector_src,
            "spread": None, "faces": [], "meta": None}  # fmt: skip


def request(key: str, lat: float, lon: float, source: str) -> None:
    """Pide en segundo plano la orientación de una escuela: `dem` (si no la tiene) u `osm`."""
    q = _queues.get(source)
    if q is None or key in _pending[source]:
        return
    _pending[source].add(key)
    q.put_nowait((key, lat, lon))


# --- OpenStreetMap -------------------------------------------------------------------------------------------------


def _tile(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat / TILE), math.floor(lon / TILE)


async def _cliffs(app, tile: tuple[int, int]) -> list[list[tuple[float, float]]]:
    """Los acantilados de una cuadrícula (lista de líneas [(lat, lon)…]), de disco o de Overpass."""
    CLIFF_DIR.mkdir(parents=True, exist_ok=True)
    path = CLIFF_DIR / f"{tile[0]}_{tile[1]}.json"
    if path.exists() and time.time() - path.stat().st_mtime < CLIFF_TTL:
        return json.loads(path.read_text())
    s, w = tile[0] * TILE, tile[1] * TILE
    bbox = f"{s - 0.01},{w - 0.01},{s + TILE + 0.01},{w + TILE + 0.01}"
    query = f"[out:json][timeout:60];way[natural=cliff]({bbox});out geom;"
    for attempt in range(len(OVERPASS) * 2):
        url = OVERPASS[attempt % len(OVERPASS)]
        try:
            r = await app.state.plain.post(url, data={"data": query}, headers={"user-agent": "8anu-proxy (personal)"},
                                           timeout=90)  # fmt: skip
        except Exception as e:  # noqa: BLE001 — Overpass a veces no contesta: se prueba otro servidor
            log.info("overpass %s: %s", url, e)
            continue
        if r.status_code == 200:
            lines = [[(p["lat"], p["lon"]) for p in el.get("geometry", [])] for el in r.json().get("elements", [])]
            path.write_text(json.dumps(lines))
            await asyncio.sleep(2)  # Overpass pide no encadenar peticiones
            return lines
        log.info("overpass %s: %s", url, r.status_code)
        await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError("overpass")


def cliff_aspect(lines: list[list[tuple[float, float]]], lat: float, lon: float) -> tuple[float, float] | None:
    """(orientación, metros de acantilado tenidos en cuenta) de los tramos a menos de RADIUS m, o None."""
    kx = 111320 * math.cos(math.radians(lat))
    vx = vy = total = 0.0
    for line in lines:
        for (a_lat, a_lon), (b_lat, b_lon) in zip(line, line[1:]):
            mx, my = ((a_lon + b_lon) / 2 - lon) * kx, ((a_lat + b_lat) / 2 - lat) * 111320
            dist = math.hypot(mx, my)
            if dist > RADIUS:
                continue
            dx, dy = (b_lon - a_lon) * kx, (b_lat - a_lat) * 111320
            length = math.hypot(dx, dy)
            if not length:
                continue
            face = math.radians(math.degrees(math.atan2(dx, dy)) + 90)  # a la derecha del sentido de la línea
            w = length / (1 + dist / 50)
            vx += w * math.sin(face)
            vy += w * math.cos(face)
            total += length
    if total < 15:
        return None
    return math.degrees(math.atan2(vx, vy)) % 360, total


# --- Trabajador --------------------------------------------------------------------------------------------------------


_dem_sem = asyncio.Semaphore(2)


async def resolve_dem(key: str, lat: float, lon: float) -> None:
    """Orientación por el relieve de una escuela que no la tiene (tools/dem/aspect.py en otro proceso: GDAL y numpy
    no están en el entorno del proxy)."""
    if any(r["sector"] == "" and r["source"] == "dem" for r in store.get_aspects(key)):
        return
    async with _dem_sem:
        proc = await asyncio.create_subprocess_exec(
            DEM_PYTHON, "-I", str(DEM_TOOL), "--crag", key, str(lat), str(lon),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "DATA_DIR": str(store.DATA_DIR)},
        )  # fmt: skip
        try:
            out, err = await asyncio.wait_for(proc.communicate(), DEM_TIMEOUT)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("dem: timeout") from None
    if proc.returncode:
        raise RuntimeError(f"dem: {err.decode(errors='replace').strip().splitlines()[-1:]}")


async def resolve_osm(app, key: str, lat: float, lon: float) -> None:
    rows = store.get_aspects(key)
    if any(r["sector"] == "" and r["source"] in ("osm", "user", "thecrag") for r in rows):
        return
    found = cliff_aspect(await _cliffs(app, _tile(lat, lon)), lat, lon)
    if found:
        store.set_aspect(key, "", found[0], "osm", None)


async def resolve(app, key: str, lat: float, lon: float) -> None:
    """Todo seguido (para la página de una escuela): OSM y el relieve."""
    try:
        await resolve_osm(app, key, lat, lon)
    finally:
        await resolve_dem(key, lat, lon)


async def worker(app, source: str) -> None:
    q = _queues[source] = asyncio.Queue()
    while True:
        key, lat, lon = await q.get()
        try:
            if source == "dem":
                await resolve_dem(key, lat, lon)
            else:
                await resolve_osm(app, key, lat, lon)
            log.info("orientación (%s) de %s: %s (%d en cola)", source, key, current(key)["compass"], q.qsize())
        except Exception as e:  # noqa: BLE001 — se reintenta la próxima vez que se pida
            log.warning("orientación (%s) de %s: %s", source, key, e)
        finally:
            _pending[source].discard(key)
        await asyncio.sleep(0.2)
