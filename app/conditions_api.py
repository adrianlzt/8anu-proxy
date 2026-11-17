"""Rutas de la página de condiciones: /_proxy/conditions/… (JSON y página suelta) y /_proxy/static/… (JS y CSS).

El widget (static/conditions.js) se inyecta en todas las páginas HTML de 8a.nu y se pinta en la pestaña
«Conditions» de cada escuela (/crags/<cat>/<país>/<escuela>/conditions), en lugar del «Sign in» de 8a.nu.
"""

import asyncio
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from curl_cffi.requests import AsyncSession
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import aspect
from . import conditions as cond
from . import meteo
from . import store

STATIC = Path(__file__).parent / "static"
SLUG = re.compile(r"[a-z0-9-]{1,120}")
STATIC_FILES = ("conditions.js", "conditions.css", "meteo.js", "map.js", "map.css", "map.html")


def version() -> str:
    """Huella de los ficheros estáticos (fecha y tamaño): cambia con cada cambio de código, aunque no se reinicie el
    servicio (se sirven del disco). Va en las URLs (?v=) para saltarse la caché del navegador, y el JS la consulta en
    /_proxy/version para avisar de que hay versión nueva en una pestaña que lleva tiempo abierta."""
    parts = []
    for name in STATIC_FILES:
        st = (STATIC / name).stat()
        parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def inject_tags() -> bytes:
    v = version()
    return (
        f'<link rel="stylesheet" href="/_proxy/static/conditions.css?v={v}">'
        f'<script src="/_proxy/static/meteo.js?v={v}" defer></script>'
        f'<script src="/_proxy/static/conditions.js?v={v}" data-nuc-version="{v}" defer></script></head>'
    ).encode()

_cache = cond.Cache()
OSM_WAIT = 8
_locks: dict[str, asyncio.Lock] = {}


def inject(html: bytes) -> bytes:
    return html.replace(b"</head>", inject_tags(), 1)


def _key(request: Request) -> str | None:
    p = request.path_params
    parts = [p["cat"], p["country"], p["crag"]]
    if not all(SLUG.fullmatch(x) for x in parts):
        return None
    return "/".join(parts)


async def _om_get(app, path: str, params: dict) -> dict:
    session: AsyncSession = app.state.plain
    r = await session.get(f"{cond.OPEN_METEO}/{path}?{urlencode(params)}")
    if r.status_code != 200:
        raise RuntimeError(f"Open-Meteo {path}: {r.status_code} {r.text[:200]}")
    return r.json()


async def _crag(app, key: str) -> tuple[dict, list[dict], str | None]:
    from .main import UPSTREAM, fetch  # evitar import circular

    hit = _cache.get(("crag", key), cond.CRAG_TTL)
    if hit:
        return hit
    base = f"{UPSTREAM}/api/unification/outdoor/v1/web/crags/{key}"
    hdr = {"accept": "application/json"}
    r = await fetch(app, "GET", base, hdr, b"", "")
    if r.status != 200:
        raise LookupError(f"8a.nu {r.status}")
    crag = json.loads(r.content)["crag"]
    rs = await fetch(app, "GET", base + "/sectors", hdr, b"", "")
    sectors = json.loads(rs.content) if rs.status == 200 else []
    # Tipo de roca: lo da el resumen de condiciones de 8a.nu (público, aunque el resto esté bloqueado).
    rc = await fetch(app, "GET", base + "/conditions", hdr, b"", "")
    rock = None
    if rc.status == 200:
        rock = ((json.loads(rc.content).get("rock") or {}).get("type") or "").lower() or None
    _cache.put(("crag", key), (crag, sectors, rock))
    return crag, sectors, rock


async def _weather(app, lat: float, lon: float) -> dict:
    k = ("wx", round(lat, 3), round(lon, 3))
    hit = _cache.get(k, cond.WEATHER_TTL)
    if hit:
        return hit
    w = await _om_get(
        app,
        "forecast",
        {"latitude": lat, "longitude": lon, "hourly": cond.HOURLY, "past_days": 5, "forecast_days": 8,
         "timezone": "auto", "wind_speed_unit": "kmh"},
    )  # fmt: skip
    w["_fetched"] = datetime.now().strftime("%H:%M")
    _cache.put(k, w)
    return w


async def _aspect(app, key: str, lat: float, lon: float) -> dict:
    """Orientación (ver app/aspect.py). La primera vez que se abre una escuela: el relieve enseguida (una llamada a
    Open-Meteo) y los acantilados de OSM como mucho OSM_WAIT s; si Overpass tarda más, sigue en segundo plano."""
    a = aspect.current(key)
    if a["source"] == "default":
        try:
            await asyncio.wait_for(aspect.resolve_terrain(app, key, lat, lon), 10)
        except Exception as e:  # noqa: BLE001 — se usa lo que haya
            aspect.log.warning("orientación (terrain) de %s: %s", key, e)
    if aspect.current(key)["source"] in ("default", "terrain"):
        try:
            await asyncio.wait_for(asyncio.shield(aspect.resolve_osm(app, key, lat, lon)), OSM_WAIT)
        except Exception:  # noqa: BLE001 — Overpass lento o caído: que lo termine el trabajador
            aspect.request(key, lat, lon, "osm")
    return aspect.current(key)


async def conditions_json(request: Request) -> Response:
    key = _key(request)
    if not key:
        return JSONResponse({"error": "bad crag"}, status_code=400)
    try:
        p = cond.Prefs(float(request.query_params.get("tmin", 12)), float(request.query_params.get("tmax", 22)))
    except ValueError:
        return JSONResponse({"error": "bad prefs"}, status_code=400)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        try:
            crag, sectors, rock = await _crag(request.app, key)
        except LookupError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        if not crag.get("location"):
            return JSONResponse({"error": "8a.nu has no coordinates for this crag"}, status_code=404)
        lat, lon = crag["location"]["latitude"], crag["location"]["longitude"]
        weather, aspect = await asyncio.gather(_weather(request.app, lat, lon), _aspect(request.app, key, lat, lon))
    data = cond.build(crag, sectors, weather, aspect, p, rock_type=rock)
    # Sol de cada sector con orientación propia (para «Sun on the wall»).
    for s in data["crag"]["sectors"]:
        deg = aspect["sectors"].get(s["slug"])
        if deg is not None:
            s["aspect"] = deg
            s["compass"] = cond.compass(deg)
            off = weather.get("utc_offset_seconds", 0)
            s["sun"] = {
                d["date"]: cond.wall_sun(datetime.fromisoformat(d["date"]), off, lat, lon, deg) for d in data["days"]
            }
    data["webcams"] = store.get_webcams(key)
    return JSONResponse(data, headers={"cache-control": "no-store"})


async def meteo_json(request: Request) -> Response:
    """Meteorología detallada (sección plegada): meteograma, ensemble, modelos y avisos. Ver app/meteo.py."""
    key = _key(request)
    if not key:
        return JSONResponse({"error": "bad crag"}, status_code=400)
    try:
        crag, _, _ = await _crag(request.app, key)
        if not crag.get("location"):
            return JSONResponse({"error": "8a.nu has no coordinates for this crag"}, status_code=404)
        lat, lon = crag["location"]["latitude"], crag["location"]["longitude"]
        data = await meteo.detail(request.app, key, lat, lon)
    except (LookupError, RuntimeError) as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return JSONResponse(data, headers={"cache-control": "no-store"})


async def set_aspect(request: Request) -> Response:
    key = _key(request)
    if not key:
        return JSONResponse({"error": "bad crag"}, status_code=400)
    body = await request.json()
    try:
        deg = float(body["deg"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "deg"}, status_code=400)
    sector = str(body.get("sector") or "")
    if sector and not SLUG.fullmatch(sector):
        return JSONResponse({"error": "sector"}, status_code=400)
    store.set_aspect(key, sector, deg, "user")
    return JSONResponse({"ok": True})


async def add_webcam(request: Request) -> Response:
    key = _key(request)
    body = await request.json()
    url = str(body.get("url") or "").strip()
    if not key or not re.fullmatch(r"https?://\S{4,500}", url):
        return JSONResponse({"error": "url"}, status_code=400)
    store.add_webcam(key, url, str(body.get("title") or "").strip()[:80])
    return JSONResponse({"ok": True})


async def standalone(request: Request) -> Response:
    """Página suelta con solo las condiciones (para probar o si la inyección en 8a.nu falla)."""
    key = _key(request)
    if not key:
        return PlainTextResponse("bad crag", status_code=400)
    return HTMLResponse(
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
        f'initial-scale=1"><title>Conditions</title>{inject_tags().decode()}<body class="nuc-standalone">'
        f'<div id="nuc-root" data-crag="{key}"></div></body></html>'
    )


async def static(request: Request) -> Response:
    name = request.path_params["name"]
    if name not in STATIC_FILES or name.endswith(".html"):
        return PlainTextResponse("no", status_code=404)
    # Con ?v= la URL cambia con cada versión: se puede guardar para siempre. Sin ella, que se revalide siempre.
    cache = "public, max-age=31536000, immutable" if request.query_params.get("v") else "no-cache"
    return FileResponse(STATIC / name, headers={"cache-control": cache})


async def version_json(_: Request) -> Response:
    return JSONResponse({"v": version()}, headers={"cache-control": "no-store"})


CRAG = "{cat}/{country}/{crag}"
routes = [
    Route(f"/_proxy/conditions/{CRAG}/data", conditions_json),
    Route(f"/_proxy/conditions/{CRAG}/meteo", meteo_json),
    Route(f"/_proxy/conditions/{CRAG}/aspect", set_aspect, methods=["POST"]),
    Route(f"/_proxy/conditions/{CRAG}/webcams", add_webcam, methods=["POST"]),
    Route(f"/_proxy/conditions/{CRAG}", standalone),
    Route("/_proxy/static/{name}", static),
    Route("/_proxy/version", version_json),
]
