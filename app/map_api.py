"""Mapa de condiciones: todas las escuelas de 8a.nu en un mapa, filtrables (estilo, vías en un rango de grados,
valoración, condiciones…) y coloreadas por las condiciones del día elegido.

- /_proxy/map: la página (Leaflet + agrupación de marcadores, static/map.js).
- /_proxy/map/crags: las escuelas con su histograma de grados. Salen de la API del mapa de 8a.nu
  (`/api/unification/collection/v1/web/map/items`), pedida por recuadros grandes que cubren el mundo; se guardan en
  disco un día.
- /_proxy/map/conditions?keys=…: resumen por día (categoría, puntuación, mejor ventana) de hasta MAX_KEYS escuelas.
  Para no hacer muchas llamadas: el tiempo se pide por celdas de CELL grados (las escuelas cercanas comparten
  previsión), con hasta OM_BATCH celdas por llamada a Open-Meteo y caché de una hora por celda; los resúmenes se
  cachean por escuela, preferencias y orientación. La orientación es la de app/aspect.py; las que faltan se piden en
  segundo plano y el mapa vuelve a preguntar.
"""

import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from . import aspect
from . import conditions as cond

STATIC = Path(__file__).parent / "static"
CACHE = Path(os.environ.get("CACHE_DIR", Path.home() / ".cache" / "8anu-proxy")) / "map-crags.json"
MAX_KEYS = 400
PAGE = 2000
CELL = 0.1
OM_BATCH = 100
CRAGS_TTL = 24 * 3600
WX_TTL = 3600
CATEGORY = {0: "sportclimbing", 1: "bouldering"}
KEY_RE = re.compile(r"(sportclimbing|bouldering)/[a-z0-9-]{1,120}/[a-z0-9-]{1,120}")
# Recuadros (lat_sur, lon_oeste, lat_norte, lon_este) que cubren las zonas con escuelas.
WORLD = [(lat, lon, lat + 30, lon + 60) for lat in (-60, -30, 0, 30, 60) for lon in range(-180, 180, 60)]

_crags: dict = {"at": 0.0, "list": [], "by_key": {}}
_crags_lock = asyncio.Lock()
_wx: dict[tuple[float, float], tuple[float, dict]] = {}
_summary: dict[tuple, tuple[float, dict]] = {}


async def _items(app, box: tuple, category: str, depth: int = 0) -> list[dict]:
    """Escuelas de un recuadro. 8a.nu da error al pedir la segunda página, así que si el recuadro tiene más escuelas
    de las que caben en una (o falla) se parte en cuatro."""
    from .main import UPSTREAM, fetch  # evitar import circular

    params = {"south_west_latitude": box[0], "south_west_longitude": box[1], "north_east_latitude": box[2],
              "north_east_longitude": box[3], "zoom": 14, "categories": category, "page_index": 0,
              "page_size": PAGE}  # fmt: skip
    r = await fetch(app, "GET", f"{UPSTREAM}/api/unification/collection/v1/web/map/items?{urlencode(params)}",
                    {"accept": "application/json"}, b"", "")  # fmt: skip
    data = json.loads(r.content) if r.status == 200 else {}
    full = (data.get("counts") or {}).get("locations", 0) > PAGE - 50
    if (r.status != 200 or full) and depth < 6:
        s, w, n, e = box
        mlat, mlon = (s + n) / 2, (w + e) / 2
        quads = [(s, w, mlat, mlon), (s, mlon, mlat, e), (mlat, w, n, mlon), (mlat, mlon, n, e)]
        out = []
        for q in quads:
            out += await _items(app, q, category, depth + 1)
        return out
    if r.status != 200:
        raise RuntimeError(f"8a.nu map/items {r.status}")
    return data.get("items", [])


def _crag(it: dict) -> dict | None:
    if "grades" not in it or it.get("category") not in CATEGORY or not it.get("latitude"):
        return None
    g = it["grades"]
    return {
        "key": f"{CATEGORY[it['category']]}/{it['country_slug']}/{it['slug']}",
        "name": it.get("name"),
        "area": it.get("area_name"),
        "country": it.get("country_name"),
        "lat": round(it["latitude"], 5),
        "lon": round(it["longitude"], 5),
        "cat": it["category"],
        "routes": it.get("total_ascendables") or 0,
        "ascents": it.get("total_ascents") or 0,
        "rating": round(it.get("avg_rating") or 0, 2),
        # Vías por índice de grado de 8a.nu (1…39); el mapa cuenta las que caen en el rango del filtro.
        "g": [g.get(str(i), 0) for i in range(1, 40)],
    }


async def _download(app) -> list[dict]:
    crags, seen = [], set()
    for box in WORLD:
        for category in CATEGORY.values():
            for it in await _items(app, box, category):
                c = _crag(it)
                if c and c["key"] not in seen:
                    seen.add(c["key"])
                    crags.append(c)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(crags, separators=(",", ":")))
    tmp.replace(CACHE)
    return crags


async def _refresh(app) -> None:
    try:
        crags = await _download(app)
        _crags.update(at=time.time(), list=crags, by_key={c["key"]: c for c in crags})
    except Exception as e:  # noqa: BLE001 — se sigue con la copia vieja
        aspect.log.warning("lista de escuelas: %s", e)
    finally:
        _crags["refreshing"] = False


async def load_crags(app) -> list[dict]:
    """La lista de escuelas: bajarla entera son cientos de peticiones a 8a.nu (~6 min), así que se sirve la copia de
    disco aunque sea vieja y, si tiene más de un día, se renueva en segundo plano. Solo la primera vez se espera."""
    async with _crags_lock:
        if not _crags["list"] and CACHE.exists():
            crags = json.loads(CACHE.read_text())
            _crags.update(at=CACHE.stat().st_mtime, list=crags, by_key={c["key"]: c for c in crags})
        if not _crags["list"]:
            crags = await _download(app)
            _crags.update(at=time.time(), list=crags, by_key={c["key"]: c for c in crags})
        elif time.time() - _crags["at"] > CRAGS_TTL and not _crags.get("refreshing"):
            _crags["refreshing"] = True
            asyncio.create_task(_refresh(app))
        return _crags["list"]


async def crags_json(request: Request) -> Response:
    try:
        crags = await load_crags(request.app)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return JSONResponse(crags, headers={"cache-control": "public, max-age=3600"})


def _cell(c: dict) -> tuple[float, float]:
    return round(round(c["lat"] / CELL) * CELL, 3), round(round(c["lon"] / CELL) * CELL, 3)


async def _weather_cells(app, cells: set[tuple[float, float]]) -> dict[tuple[float, float], dict]:
    now = time.time()
    out = {k: _wx[k][1] for k in cells if k in _wx and now - _wx[k][0] < WX_TTL}
    todo = sorted(cells - out.keys())
    fetched = datetime.now().strftime("%H:%M")
    for i in range(0, len(todo), OM_BATCH):
        chunk = todo[i : i + OM_BATCH]
        params = {"latitude": ",".join(str(c[0]) for c in chunk), "longitude": ",".join(str(c[1]) for c in chunk),
                  "hourly": cond.HOURLY, "forecast_days": 7, "timezone": "auto", "wind_speed_unit": "kmh"}  # fmt: skip
        r = await app.state.plain.get(f"{cond.OPEN_METEO}/forecast?{urlencode(params)}")
        if r.status_code != 200:
            raise RuntimeError(f"Open-Meteo {r.status_code}: {r.text[:200]}")
        data = r.json()
        for cell, w in zip(chunk, data if isinstance(data, list) else [data]):
            w["_fetched"] = fetched
            _wx[cell] = (now, w)
            out[cell] = w
    return out


def summarize(crag: dict, weather: dict, asp: dict, p: cond.Prefs) -> dict:
    model_crag = {"cragName": crag["name"], "areaName": crag["area"], "rainSafety": False,
                  "location": {"latitude": crag["lat"], "longitude": crag["lon"]}}  # fmt: skip
    d = cond.build(model_crag, [], weather, asp, p)
    return {
        "aspect": asp["compass"],
        "aspect_source": asp["source"],
        "days": [
            {"date": x["date"], "cat": x["cat"], "score": x["score"], "window": x.get("window"), "tmax": x["tmax"],
             "tmin": x["tmin"], "rain": x["rain"]["mm"], "rock": x["rock"]}
            for x in d["days"]
        ],
    }  # fmt: skip


async def conditions_json(request: Request) -> Response:
    keys = [k for k in request.query_params.get("keys", "").split(",") if KEY_RE.fullmatch(k)][:MAX_KEYS]
    try:
        p = cond.Prefs(float(request.query_params.get("tmin", 12)), float(request.query_params.get("tmax", 22)))
    except ValueError:
        return JSONResponse({"error": "prefs"}, status_code=400)
    await load_crags(request.app)
    crags = [_crags["by_key"][k] for k in keys if k in _crags["by_key"]]
    if not crags:
        return JSONResponse({})
    try:
        weather = await _weather_cells(request.app, {_cell(c) for c in crags})
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    out, now = {}, time.time()
    for c in crags:
        asp = aspect.current(c["key"])
        if asp["source"] == "default":
            aspect.request(c["key"], c["lat"], c["lon"], "terrain")
        if asp["source"] in ("default", "terrain"):
            aspect.request(c["key"], c["lat"], c["lon"], "osm")
        ck = (c["key"], p.tmin, p.tmax, round(asp["deg"]), _wx[_cell(c)][0])
        hit = _summary.get(ck)
        if not hit:
            hit = (now, await asyncio.to_thread(summarize, c, weather[_cell(c)], asp, p))
            _summary[ck] = hit
        out[c["key"]] = hit[1]
    if len(_summary) > 50000:
        _summary.clear()
    return JSONResponse(out, headers={"cache-control": "no-store"})


async def page(_: Request) -> Response:
    return FileResponse(STATIC / "map.html", headers={"cache-control": "no-cache"})


routes = [
    Route("/_proxy/map", page),
    Route("/_proxy/map/crags", crags_json),
    Route("/_proxy/map/conditions", conditions_json),
]
