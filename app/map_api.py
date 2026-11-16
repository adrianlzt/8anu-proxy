"""Mapa de condiciones: todas las escuelas de 8a.nu en un mapa, filtrables (estilo, grados, vías, valoración,
aproximación, lluvia…) y coloreadas por las condiciones del día elegido.

- /_proxy/map: la página (Leaflet + OSM, static/map.js).
- /_proxy/map/crags: las escuelas (ver load_crags), cacheadas un día.
- /_proxy/map/conditions?keys=…: resumen por día (categoría, puntuación, mejor ventana) de hasta MAX_KEYS escuelas,
  con una sola llamada a Open-Meteo para todas. La orientación es la guardada (usuario o relieve) si la hay; si no, se
  supone sur y se marca, para no pedir el relieve de cada escuela del mapa.
"""

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from . import conditions as cond
from . import store

STATIC = Path(__file__).parent / "static"
MAX_KEYS = 60
CRAGS_TTL = 24 * 3600
CATEGORY = {0: "sportclimbing", 1: "bouldering"}
KEY_RE = re.compile(r"(sportclimbing|bouldering)/[a-z0-9-]{1,120}/[a-z0-9-]{1,120}")

_crags: dict = {"at": 0.0, "list": [], "by_key": {}}
_crags_lock = asyncio.Lock()
_wx: dict[str, tuple[float, dict]] = {}


async def _get_json(app, path: str):
    from .main import UPSTREAM, fetch  # evitar import circular

    r = await fetch(app, "GET", UPSTREAM + path, {"accept": "application/json"}, b"", "")
    if r.status != 200:
        raise RuntimeError(f"8a.nu {path}: {r.status}")
    return json.loads(r.content)


async def load_crags(app) -> list[dict]:
    """Todas las escuelas: /api/dotnet/crags/map con un recuadro que cubre el mundo (lo que pide el mapa de 8a.nu al
    moverse, ~9500 escuelas con coordenadas y rango de grados) completado con el GeoJSON (/crags/mapbox), que trae
    «rainSafety», «familyFriendly» y la aproximación de algunas."""
    async with _crags_lock:
        if _crags["list"] and time.time() - _crags["at"] < CRAGS_TTL:
            return _crags["list"]
        world, geo = await asyncio.gather(
            _get_json(app, "/api/dotnet/crags/map?swLat=-89&swLng=-179.9&neLat=89&neLng=179.9"),
            _get_json(app, "/api/dotnet/crags/mapbox"),
        )
        props = {
            (p["countrySlug"], p["cragSlug"], p["category"]): p
            for f in geo.get("features", [])
            if (p := f.get("properties") or {}).get("cragSlug")
        }
        out = []
        for c in world.get("crags", []):
            loc = c.get("location") or {}
            if not loc.get("latitude") or c.get("category") not in CATEGORY or not c.get("cragSlug"):
                continue
            extra = props.get((c["countrySlug"], c["cragSlug"], c["category"]), {})
            g = c.get("gradeExtrems") or [None, None]
            out.append(
                {
                    "key": f"{CATEGORY[c['category']]}/{c['countrySlug']}/{c['cragSlug']}",
                    "name": c.get("cragName"),
                    "area": c.get("areaName"),
                    "country": c.get("countryName"),
                    "lat": round(loc["latitude"], 5),
                    "lon": round(loc["longitude"], 5),
                    "cat": c["category"],
                    "routes": c.get("totalZlaggables") or 0,
                    "ascents": c.get("totalAscents") or 0,
                    "rating": round(c.get("averageRating") or 0, 2),
                    "rain": bool(c.get("rainSafety") or extra.get("rainSafety")),
                    "family": bool(c.get("familyFriendly") or extra.get("familyFriendly")),
                    "walk": c.get("walkingTime") or extra.get("walkingTime"),
                    "gmin": g[0] if g[0] is not None and g[0] > 0 else None,
                    "gmax": g[1] if g[1] is not None and g[1] > 0 else None,
                }
            )
        _crags.update(at=time.time(), list=out, by_key={c["key"]: c for c in out})
        return out


async def crags_json(request: Request) -> Response:
    try:
        crags = await load_crags(request.app)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return JSONResponse(crags, headers={"cache-control": "public, max-age=3600"})


async def _weather_many(app, crags: list[dict]) -> dict[str, dict]:
    """Previsión de varias escuelas en una sola llamada a Open-Meteo (caché de 30 min por escuela)."""
    now = time.time()
    out = {c["key"]: _wx[c["key"]][1] for c in crags if c["key"] in _wx and now - _wx[c["key"]][0] < cond.WEATHER_TTL}
    todo = [c for c in crags if c["key"] not in out]
    if todo:
        params = {
            "latitude": ",".join(str(c["lat"]) for c in todo),
            "longitude": ",".join(str(c["lon"]) for c in todo),
            "hourly": cond.HOURLY, "forecast_days": 7, "timezone": "auto", "wind_speed_unit": "kmh",
        }  # fmt: skip
        r = await app.state.plain.get(f"{cond.OPEN_METEO}/forecast?{urlencode(params)}")
        if r.status_code != 200:
            raise RuntimeError(f"Open-Meteo {r.status_code}: {r.text[:200]}")
        data = r.json()
        if isinstance(data, dict):
            data = [data]
        fetched = datetime.now().strftime("%H:%M")
        for c, w in zip(todo, data):
            w["_fetched"] = fetched
            _wx[c["key"]] = (now, w)
            out[c["key"]] = w
    return out


def summarize(crag: dict, weather: dict, aspect: dict, p: cond.Prefs) -> dict:
    model_crag = {"cragName": crag["name"], "areaName": crag["area"], "rainSafety": crag["rain"],
                  "location": {"latitude": crag["lat"], "longitude": crag["lon"]}}  # fmt: skip
    d = cond.build(model_crag, [], weather, aspect, p)
    return {
        "aspect": aspect.get("compass"),
        "aspect_known": aspect.get("source") != "default",
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
        weather = await _weather_many(request.app, crags)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    out = {}
    for c in crags:
        rows = store.get_aspects(c["key"])
        user = next((r for r in rows if r["sector"] == "" and r["source"] == "user"), None)
        dem = next((r for r in rows if r["sector"] == "" and r["source"] == "terrain"), None)
        src = user or dem
        aspect = (
            {"deg": src["deg"], "source": src["source"], "compass": cond.compass(src["deg"])}
            if src
            else {"deg": 180, "source": "default", "compass": "S?"}
        )
        out[c["key"]] = await asyncio.to_thread(summarize, c, weather[c["key"]], aspect, p)
    return JSONResponse(out, headers={"cache-control": "no-store"})


async def page(_: Request) -> Response:
    return FileResponse(STATIC / "map.html", headers={"cache-control": "no-cache"})


routes = [
    Route("/_proxy/map", page),
    Route("/_proxy/map/crags", crags_json),
    Route("/_proxy/map/conditions", conditions_json),
]
