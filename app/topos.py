"""Croquis en el proxy: los premium de 8a.nu importados (tools/eight/import_topos.py), los de papel que suba Adrián y,
en el visor, los de theCrag (que siguen llegando de thecrag.adrianlzt.com).

Modelo único: un croquis es una foto más una lista de vías con su trazo (puntos en píxeles de la foto), el mismo que
usa el visor de theCrag. Los de 8a.nu vienen de dos sitios: la imagen de `/images/<hash>` (que 8a.nu sirve con las
líneas, números y grados ya pintados: `baked`) y la API de vías (`zlaggables`), que da el trazo de cada vía (`path`,
en píxeles de `reference_width`) y en qué croquis va. Si hay versión en blanco de la foto (`blank`, bajada aparte),
el visor pinta él las líneas.

Rutas (todas con la contraseña del proxy, si la hay: son contenido de pago):
- `GET /_proxy/topos/<cat>/<país>/<escuela>[?sector=<slug>]` → ficha premium (acceso, aproximación, parking), fotos
  de acceso y croquis con sus vías; `sent` por vía según las ascensiones de Adrián en 8a.nu.
- `GET /_proxy/topos/img/<hash>.jpg` → la foto (inmutable).
- `GET /_proxy/topos/<cat>/<país>/<escuela>/page` → página suelta con el visor (para probar y para guardar sin red).

Encadenes: las ascensiones salen del Postgres de 8anu-dashboard por la API de su Grafana (lectura anónima), como en
el proxy de theCrag; se guardan en `DATA_DIR/ascents.json` y se refrescan cada `TICKS_REFRESH_H` horas. Las vías de
8a.nu se cruzan por nombre de escuela y de vía (ambos vienen de 8a.nu, así que casan tal cual); si solo coincide el
nombre y el grado anda cerca, `maybe`.
"""

import asyncio
import json
import logging
import math
import os
import re
import struct
import time
import unicodedata
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import store

log = logging.getLogger("8anu-proxy.topos")

TOPOS_DIR = Path(os.environ.get("TOPOS_DIR", store.DATA_DIR / "topos"))
GRAFANA = os.environ.get("TICKS_GRAFANA", "https://8anu.adrianlzt.com").rstrip("/")
DS_UID = os.environ.get("TICKS_DS_UID", "aet0jntondqtcc")
USER_SLUG = os.environ.get("TICKS_USER", "adrian-lopez-lrxzc")
REFRESH_H = float(os.environ.get("TICKS_REFRESH_H", "6"))
SLUG = re.compile(r"[a-z0-9-]{1,120}")
HASH = re.compile(r"[a-z0-9-]{4,80}")
CATS = {0: "sportclimbing", 1: "bouldering", "sportclimbing": "sportclimbing", "bouldering": "bouldering"}
FR = ["1", "2", "3", "4", "4+", "5", "5+", "5a", "5b", "5c", "6a", "6a+", "6b", "6b+", "6c", "6c+", "7a", "7a+", "7b",
      "7b+", "7c", "7c+", "8a", "8a+", "8b", "8b+", "8c", "8c+", "9a", "9a+", "9b", "9b+", "9c"]  # fmt: skip
_FR_INDEX = {g: i for i, g in enumerate(FR)}
_ARTICLES = ("the", "la", "el", "los", "las", "les", "le", "l", "il", "lo", "der", "die", "das")


# --- utilidades -------------------------------------------------------------------------------------------------------

def norm(s: str) -> str:
    """Nombre comparable: sin acentos ni signos, minúsculas, solo letras latinas y dígitos, sin artículo inicial."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", s)
    if len(words) > 1 and words[0] in _ARTICLES:
        words = words[1:]
    return " ".join(words)


def grade_index(grade: str) -> int | None:
    g = str(grade or "").strip().lower().split("/")[0].split(" ")[0]
    return _FR_INDEX.get(g)


def jpeg_size(path: Path) -> tuple[int, int] | None:
    """Ancho y alto de un JPEG leyendo solo las cabeceras (sin PIL)."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"\xff\xd8":
                return None
            while True:
                b = f.read(1)
                while b and b != b"\xff":
                    b = f.read(1)
                while b == b"\xff":
                    b = f.read(1)
                if not b:
                    return None
                m = b[0]
                if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    f.read(3)
                    h, w = struct.unpack(">HH", f.read(4))
                    return w, h
                if m == 0x01 or 0xD0 <= m <= 0xD8:
                    continue
                (n,) = struct.unpack(">H", f.read(2))
                f.seek(n - 2, 1)
    except (OSError, struct.error):
        return None


def crag_key(cat: str, country: str, crag: str) -> str:
    return f"{country}/{crag}"


# --- ascensiones de Adrián (Grafana) ------------------------------------------------------------------------------------

class Ticks:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.by_name: dict[str, list[dict]] = {}
        self.loaded_at = 0.0
        self.source = "none"

    @property
    def cache(self) -> Path:
        return store.DATA_DIR / "ascents.json"

    def set(self, rows: list[dict], source: str) -> None:
        self.rows = rows
        self.by_name = {}
        for r in rows:
            r["_name"] = norm(r.get("zlaggable_name", ""))
            r["_crag"] = norm(r.get("crag_name", ""))
            r["_grade"] = grade_index(r.get("difficulty", ""))
            self.by_name.setdefault(r["_name"], []).append(r)
        self.loaded_at = time.time()
        self.source = source

    def load_cache(self) -> bool:
        try:
            d = json.loads(self.cache.read_text())
        except (OSError, ValueError):
            return False
        self.set(d["ascents"], "cache")
        self.loaded_at = d.get("at", 0)
        return True

    async def refresh(self, app) -> bool:
        if not USER_SLUG:
            return False
        sql = ("select crag_name, sector_name, zlaggable_name, difficulty, type, to_char(date,'YYYY-MM-DD') as d "
               f"from ascents where user_slug = '{USER_SLUG}' and project = false")
        body = {"queries": [{"refId": "A", "datasource": {"uid": DS_UID}, "rawSql": sql, "format": "table"}],
                "from": "now-1h", "to": "now"}
        try:
            r = await app.state.plain.post(f"{GRAFANA}/api/ds/query", json=body, timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            frame = r.json()["results"]["A"]["frames"][0]
            names = [f["name"] for f in frame["schema"]["fields"]]
            rows = [dict(zip(names, vals)) for vals in zip(*frame["data"]["values"])]
        except Exception as e:  # noqa: BLE001 — red, Grafana caída, formato: seguimos con lo que haya
            log.warning("ascensiones de 8a.nu: no se pudieron refrescar (%s)", e)
            return False
        self.set(rows, "grafana")
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps({"at": self.loaded_at, "ascents": [
                {k: v for k, v in row.items() if not k.startswith("_")} for row in rows]}))
        except OSError as e:
            log.warning("no se pudo guardar %s: %s", self.cache, e)
        log.info("ascensiones de 8a.nu: %d (%s)", len(rows), USER_SLUG)
        return True

    async def run(self, app) -> None:
        self.load_cache()
        while True:
            if time.time() - self.loaded_at > REFRESH_H * 3600 - 60:
                await self.refresh(app)
            await asyncio.sleep(600)

    def match(self, name: str, grade: str, crag_name: str) -> dict | None:
        """`{"status": sent|maybe, crag, grade, date}` o None."""
        cands = self.by_name.get(norm(name))
        if not cands:
            return None
        c = norm(crag_name)
        g = grade_index(grade)
        best = None
        for r in cands:
            if r["_crag"] == c:
                st = "sent"
            elif g is not None and r["_grade"] is not None and abs(g - r["_grade"]) <= 2:
                st = "maybe"
            else:
                continue
            if best is None or (st == "sent" and best["status"] != "sent") or (st == best["status"] and (r.get("d") or "") > (best.get("date") or "")):
                best = {"status": st, "crag": r.get("crag_name"), "sector": r.get("sector_name"),
                        "grade": r.get("difficulty"), "date": r.get("d"), "type": r.get("type")}
        return best


ticks = Ticks()


# --- consultas ------------------------------------------------------------------------------------------------------------

def image_url(h: str) -> str:
    return f"/_proxy/topos/img/{h}.jpg"


def crag_data(country: str, crag: str, sector: str = "") -> dict | None:
    """Lo que hay en la base de una escuela: ficha, fotos de acceso y croquis con sus vías, listos para el visor."""
    key = f"{country}/{crag}"
    with store.connect() as db:
        info = db.execute("SELECT * FROM crag_info WHERE crag = ?", (key,)).fetchone()
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM topo WHERE crag = ? AND status = 'ok' ORDER BY kind, ordering, hash", (key,))]
        routes = [dict(r) for r in db.execute(
            "SELECT r.* FROM topo_route r JOIN topo t ON t.hash = r.topo WHERE t.crag = ?", (key,))]
    if not info and not rows:
        return None
    data = json.loads(info["data"]) if info else {}
    cat = (info["cat"] if info else None) or "sportclimbing"
    name = info["name"] if info else crag
    by_topo: dict[str, list[dict]] = {}
    for r in routes:
        by_topo.setdefault(r["topo"], []).append(r)
    max_asc = max([r["ascents"] or 0 for r in routes] + [1])
    topos, access = [], []
    for t in rows:
        if t["kind"] == "access":
            access.append({"hash": t["hash"], "image": image_url(t["hash"]), "width": t["width"], "height": t["height"]})
            continue
        if sector and t["sector"] and t["sector"] != sector:
            continue
        rs = []
        for r in by_topo.get(t["hash"], []):
            try:
                pts = [{"x": p[0], "y": p[1], "t": ""} for p in json.loads(r["points"] or "[]")]
            except (ValueError, TypeError, IndexError):
                pts = []
            m = ticks.match(r["name"] or "", r["grade"] or "", name)
            asc = r["ascents"]
            rs.append({
                "id": r["rid"], "num": r["num"] or "", "name": r["name"] or "", "grade": r["grade"] or "",
                "points": pts, "ascents": asc, "in8a": True,
                "pop": round(100 * math.sqrt(asc / max_asc)) if asc else 0,
                "height": f"{r['height']}m" if r["height"] else "",
                "url8a": f"/crags/{cat}/{country}/{crag}/sectors/{r['sector'] or t['sector']}/routes/{r['slug']}" if r["slug"] else "",
                "sent": m["status"] if m else None, "sent_info": m,
            })
        rs.sort(key=lambda r: (int(re.sub(r"\D", "", r["num"]) or 0) if r["num"] else 10**6, r["name"]))
        topos.append({
            "tid": t["hash"], "source": t["source"], "heading": t["heading"] or t["sector_name"] or "",
            "sector": t["sector"], "sector_name": t["sector_name"] or "", "width": t["width"], "height": t["height"],
            "image": image_url(t["hash"]), "baked": bool(t["baked"]) and not t["blank"],
            "blank": image_url(t["hash"]) + "?blank=1" if t["blank"] else None, "routes": rs,
        })
    if sector and not topos:  # el sector no tiene croquis propios (o el slug no casa): todos los de la escuela
        return crag_data(country, crag)
    sectors = data.get("sectors") or []
    return {
        "crag": {"key": f"{cat}/{country}/{crag}", "name": name, "cat": cat, "country": country, "slug": crag,
                 "area": data.get("areaName") or "", "premium": {k: data.get(k) for k in (
                     "premium_description", "premium_approach", "premium_access", "premium_notes", "walkingTime",
                     "altitude", "rock_type", "crag_coords_premium", "parking_coords_premium", "crag_coords_community",
                     "parking_coords_community", "heightRange", "exposition")}},
        "access": access, "topos": topos,
        "sectors": [{"slug": s.get("slug"), "name": s.get("name"), "topos": len(s.get("topos") or [])} for s in sectors],
        "ticks": ticks.source, "ticks_at": ticks.loaded_at,
    }


# --- rutas ----------------------------------------------------------------------------------------------------------------

def _denied(request: Request) -> Response | None:
    from .main import ROBOTS, _authorized  # evitar import circular

    if _authorized(request):
        return None
    return PlainTextResponse("Autenticación requerida", status_code=401,
                             headers={"www-authenticate": 'Basic realm="8anu-proxy"', "x-robots-tag": ROBOTS})


def _key(request: Request) -> tuple[str, str, str] | None:
    p = request.path_params
    parts = (p["cat"], p["country"], p["crag"])
    if not all(SLUG.fullmatch(x) for x in parts):
        return None
    return parts


async def crag_json(request: Request) -> Response:
    if (d := _denied(request)) is not None:
        return d
    k = _key(request)
    if not k:
        return JSONResponse({"error": "bad crag"}, status_code=400)
    sector = request.query_params.get("sector", "")
    if sector and not SLUG.fullmatch(sector):
        return JSONResponse({"error": "bad sector"}, status_code=400)
    data = await asyncio.to_thread(crag_data, k[1], k[2], sector)
    if data is None:
        return JSONResponse({"error": "not_found", "topos": [], "access": []}, status_code=404,
                            headers={"cache-control": "no-store"})
    return JSONResponse(data, headers={"cache-control": "no-store"})


async def image(request: Request) -> Response:
    if (d := _denied(request)) is not None:
        return d
    h = request.path_params["hash"]
    if not HASH.fullmatch(h):
        return PlainTextResponse("no", status_code=404)
    with store.connect() as db:
        row = db.execute("SELECT file, blank FROM topo WHERE hash = ?", (h,)).fetchone()
    if not row:
        return PlainTextResponse("no", status_code=404)
    path = Path(row["blank"] if request.query_params.get("blank") and row["blank"] else row["file"])
    if not path.exists():
        return PlainTextResponse("no", status_code=404)
    return FileResponse(path, media_type="image/jpeg",
                        headers={"cache-control": "private, max-age=31536000, immutable", "x-robots-tag": "noindex"})


async def page(request: Request) -> Response:
    """Página suelta con el visor."""
    from .conditions_api import version  # evitar import circular

    if (d := _denied(request)) is not None:
        return d
    k = _key(request)
    if not k:
        return PlainTextResponse("bad crag", status_code=400)
    key = "/".join(k)
    sector = request.query_params.get("sector", "")
    v = version()
    return HTMLResponse(
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
        f'initial-scale=1"><title>Topos · {k[2]}</title>'
        '<style>body{margin:0;background:#f3f3ef;font:15px system-ui,sans-serif}#host{max-width:900px;margin:0 auto;padding:8px}</style>'
        f'<script src="/_proxy/static/topos.js?v={v}"></script></head><body><div id="host"></div>'
        f'<script>nuTopos.render(document.getElementById("host"), {{key: {json.dumps(key)}, sector: {json.dumps(sector)}, standalone: true}});</script>'
        "</body></html>",
        headers={"x-robots-tag": "noindex"},
    )


CRAG = "{cat}/{country}/{crag}"
routes = [
    Route(f"/_proxy/topos/{CRAG}", crag_json),
    Route(f"/_proxy/topos/{CRAG}/page", page),
    Route("/_proxy/topos/img/{hash}.jpg", image),
]
