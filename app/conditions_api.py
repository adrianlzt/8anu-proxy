"""Rutas de la página de condiciones: /_proxy/conditions/… (JSON y página suelta) y /_proxy/static/… (JS y CSS).

El widget (static/conditions.js) se inyecta en todas las páginas HTML de 8a.nu y se pinta en la pestaña
«Conditions» de cada escuela (/crags/<cat>/<país>/<escuela>/conditions), en lugar del «Sign in» de 8a.nu.
"""

import asyncio
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
from . import store

STATIC = Path(__file__).parent / "static"
SLUG = re.compile(r"[a-z0-9-]{1,120}")
INJECT = b'<link rel="stylesheet" href="/_proxy/static/conditions.css"><script src="/_proxy/static/conditions.js" defer></script></head>'

_cache = cond.Cache()
_locks: dict[str, asyncio.Lock] = {}


def inject(html: bytes) -> bytes:
    return html.replace(b"</head>", INJECT, 1)


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
    """Orientación (ver app/aspect.py). Si aún no hay una buena (OSM o del usuario), se calcula ya: es una escuela."""
    a = aspect.current(key)
    if a["source"] in ("default", "terrain"):
        try:
            await asyncio.wait_for(aspect.resolve(app, key, lat, lon), 30)
        except Exception as e:  # noqa: BLE001 — se usa lo que haya
            aspect.log.warning("orientación de %s: %s", key, e)
        a = aspect.current(key)
    return a


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
        f'initial-scale=1"><title>Conditions</title>{INJECT.decode()}<body class="nuc-standalone">'
        f'<div id="nuc-root" data-crag="{key}"></div></body></html>'
    )


async def static(request: Request) -> Response:
    name = request.path_params["name"]
    if name not in ("conditions.js", "conditions.css", "map.js", "map.css"):
        return PlainTextResponse("no", status_code=404)
    return FileResponse(STATIC / name, headers={"cache-control": "no-cache"})


CRAG = "{cat}/{country}/{crag}"
routes = [
    Route(f"/_proxy/conditions/{CRAG}/data", conditions_json),
    Route(f"/_proxy/conditions/{CRAG}/aspect", set_aspect, methods=["POST"]),
    Route(f"/_proxy/conditions/{CRAG}/webcams", add_webcam, methods=["POST"]),
    Route(f"/_proxy/conditions/{CRAG}", standalone),
    Route("/_proxy/static/{name}", static),
]
