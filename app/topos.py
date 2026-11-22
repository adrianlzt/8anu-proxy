"""Croquis en el proxy: los premium de 8a.nu importados (tools/eight/import_topos.py), los de papel que suba Adrián y,
los de theCrag (de thecrag.adrianlzt.com, guardados aquí con sus fotos para no volver a pedírselos).

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
- `GET /_proxy/topos/thecrag?country=&area=&crag=[&sector=&crag_slug=&tc=&refresh=1]` → los croquis de theCrag de esa
  escuela: se piden una vez al proxy de theCrag (`THECRAG_PROXY`) y se guardan `THECRAG_TTL_DAYS` días (180); si theCrag corta
  o el proxy no contesta, se sirve lo guardado aunque sea viejo (`stale`). `refresh=1` (o `tc=`, que fija la página a
  mano) vuelve a pedirlos. Las fotos se reescriben a `/_proxy/topos/img/tc/<sha1>.jpg`, que se bajan de
  image.thecrag.com la primera vez y se quedan en disco. Para no llenarlo: si las fotos pasan de `THECRAG_IMG_MAX_MB`
  (2000) o al disco le quedan menos de `THECRAG_MIN_FREE_GB` (20), se borran las usadas hace más tiempo; con el disco
  por debajo de ese mínimo las fotos nuevas se sirven sin guardarlas.

Encadenes: los pone el visor con la sesión de 8a.nu de quien mira (`/api/dotnet/me` y la lista de ascensiones de ese
usuario por el proxy, con su cookie), así cada uno ve los suyos; el servidor no guarda ascensiones de nadie.
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import shutil
import struct
import time
from pathlib import Path

import httpx
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from starlette.routing import Route

from . import store

log = logging.getLogger("8anu-proxy.topos")

TOPOS_DIR = Path(os.environ.get("TOPOS_DIR", store.DATA_DIR / "topos"))
SLUG = re.compile(r"[a-z0-9-]{1,120}")
HASH = re.compile(r"[a-z0-9-]{4,80}")
CATS = {0: "sportclimbing", 1: "bouldering", "sportclimbing": "sportclimbing", "bouldering": "bouldering"}


# --- utilidades -------------------------------------------------------------------------------------------------------

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


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def image_size(path: Path) -> tuple[int, int] | None:
    return jpeg_size(path) or png_size(path)


def crag_key(cat: str, country: str, crag: str) -> str:
    return f"{country}/{crag}"


# --- consultas ------------------------------------------------------------------------------------------------------------

def image_url(h: str) -> str:
    return f"/_proxy/topos/img/{h}.jpg"


_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


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
            asc = r["ascents"]
            rs.append({
                "id": r["rid"], "num": r["num"] or "", "name": r["name"] or "", "grade": r["grade"] or "",
                "points": pts, "ascents": asc, "in8a": True,
                "pop": round(100 * math.sqrt(asc / max_asc)) if asc else 0,
                "height": f"{r['height']}m" if r["height"] else "",
                "url8a": f"/crags/{cat}/{country}/{crag}/sectors/{r['sector'] or t['sector']}/routes/{r['slug']}" if r["slug"] else "",
                "slug": r["slug"] or "", "sector": r["sector"] or t["sector"],
            })
        rs.sort(key=lambda r: (int(re.sub(r"\D", "", r["num"]) or 0) if r["num"] else 10**6, r["name"]))
        topos.append({
            "tid": t["hash"], "source": t["source"], "heading": t["heading"] or t["sector_name"] or "",
            "sector": t["sector"], "sector_name": t["sector_name"] or "", "width": t["width"], "height": t["height"],
            "image": image_url(t["hash"]), "baked": bool(t["baked"]) and not t["blank"],
            "blank": image_url(t["hash"]) + "?blank=1" if t["blank"] else None, "routes": rs,
            "editable": True,
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
    if data is None:  # sin nada en la base: lo justo para que el visor deje subir un croquis de papel
        data = {"crag": {"key": "/".join(k), "name": request.query_params.get("name", "") or k[2], "cat": k[0],
                         "country": k[1], "slug": k[2], "area": request.query_params.get("area", ""), "premium": {}},
                "access": [], "topos": [], "sectors": [], "empty": True}
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
    return FileResponse(path, media_type=_MEDIA.get(path.suffix.lower(), "image/jpeg"),
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
        f'<script src="/_proxy/topos/static/topos.js?v={v}"></script></head><body><div id="host"></div>'
        f'<script>nuTopos.render(document.getElementById("host"), {{key: {json.dumps(key)}, sector: {json.dumps(sector)}, standalone: true, version: {json.dumps(v)}}});</script>'
        "</body></html>",
        headers={"x-robots-tag": "noindex"},
    )


# --- croquis de theCrag: del proxy de theCrag, guardados aquí con sus fotos -------------------------------------------------

THECRAG_PROXY = os.environ.get("THECRAG_PROXY", "http://127.0.0.1:8471").rstrip("/")
THECRAG_TTL = float(os.environ.get("THECRAG_TTL_DAYS", "180")) * 86400
THECRAG_IMG_MAX = float(os.environ.get("THECRAG_IMG_MAX_MB", "2000")) * 1024 * 1024
THECRAG_MIN_FREE = float(os.environ.get("THECRAG_MIN_FREE_GB", "20")) * 1024 ** 3
TC_PARAMS = ("country", "area", "crag", "sector", "crag_slug")
TC_IMG_URL = re.compile(r"https://image\.thecrag\.com/[^\s\"'<>]{1,500}")
TC_ID = re.compile(r"[0-9a-f]{40}")
TC_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
_tc_img_sem = asyncio.Semaphore(2)


def _tc_rewrite(db, d: dict) -> None:
    """Las fotos de image.thecrag.com pasan a servirse desde aquí (`/_proxy/topos/img/tc/<sha1>.jpg`)."""
    for t in d.get("topos") or []:
        for f in ("image", "thumb"):
            u = t.get(f)
            if not u or u.startswith("/_proxy/"):
                continue
            if not TC_IMG_URL.fullmatch(u):
                t[f] = None
                continue
            i = hashlib.sha1(u.encode()).hexdigest()
            db.execute("INSERT OR IGNORE INTO thecrag_img (id, url) VALUES (?, ?)", (i, u))
            t[f] = f"/_proxy/topos/img/tc/{i}.jpg"


async def thecrag(request: Request) -> Response:
    if (d := _denied(request)) is not None:
        return d
    qp = request.query_params
    q = {k: qp[k].strip()[:200] for k in TC_PARAMS if qp.get(k, "").strip()}
    tc = qp.get("tc", "")
    if not (q.get("crag") or tc):
        return JSONResponse({"error": "falta crag"}, status_code=400)
    key = json.dumps({k: v.lower() for k, v in q.items()}, sort_keys=True, ensure_ascii=False)
    with store.connect() as db:
        row = db.execute("SELECT at, data FROM thecrag_cache WHERE key = ?", (key,)).fetchone()
    headers = {"cache-control": "no-store"}
    if row and not tc and qp.get("refresh") != "1" and time.time() - row["at"] < THECRAG_TTL:
        return JSONResponse(dict(json.loads(row["data"]), cached=row["at"]), headers=headers)

    def stale(why: str) -> Response:
        return JSONResponse(dict(json.loads(row["data"]), cached=row["at"], stale=why), headers=headers)

    try:
        async with httpx.AsyncClient(timeout=150) as c:
            r = await c.get(THECRAG_PROXY + "/_proxy/topos", params=dict(q, **({"tc": tc} if tc else {})))
        d = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("proxy de theCrag: %s", e)
        if row:
            return stale("thecrag_proxy")
        return JSONResponse({"error": "thecrag_proxy", "detail": str(e)[:200]}, headers=headers)
    if not isinstance(d, dict):
        return JSONResponse({"error": "thecrag_proxy"}, headers=headers)
    if d.get("error"):
        if row and d["error"] == "thecrag_blocked":
            return stale("thecrag_blocked")
        return JSONResponse(d, headers=headers)  # los errores (no encontrada…) no se guardan
    if row and d.get("partial") and not tc:
        return stale("partial")  # theCrag cortó a medias: mejor lo completo de antes
    now = time.time()
    with store.connect() as db:
        _tc_rewrite(db, d)
        db.execute("INSERT OR REPLACE INTO thecrag_cache (key, at, data) VALUES (?, ?, ?)", (key, now, json.dumps(d, ensure_ascii=False)))
    return JSONResponse(dict(d, cached=now), headers=headers)


def _disk_low(d: Path) -> bool:
    try:
        return shutil.disk_usage(d if d.exists() else TOPOS_DIR).free < THECRAG_MIN_FREE
    except OSError:
        return False


def tc_prune(d: Path | None = None) -> int:
    """Borra las fotos de theCrag usadas hace más tiempo (mtime: se toca al servirlas) hasta quedar por debajo del 80 %
    de THECRAG_IMG_MAX y, si al disco le falta espacio, hasta recuperar el mínimo o vaciarla. Devuelve cuántas borra."""
    d = d or TOPOS_DIR / "thecrag"
    if not d.exists():
        return 0
    files = []
    for f in d.glob("*.jpg"):
        try:
            st = f.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, f))
    files.sort()
    total = sum(x[1] for x in files)
    n = 0
    for _, size, f in files:
        if total <= THECRAG_IMG_MAX * 0.8 and not _disk_low(d):
            break
        f.unlink(missing_ok=True)
        total -= size
        n += 1
    if n:
        log.info("caché de fotos de theCrag: %d borradas, quedan %.0f MB", n, total / 1024 / 1024)
    return n


_tc_last_prune = 0.0


def _sniff(b: bytes) -> str:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


async def thecrag_image(request: Request) -> Response:
    if (d := _denied(request)) is not None:
        return d
    i = request.path_params["id"]
    if not TC_ID.fullmatch(i):
        return PlainTextResponse("no", status_code=404)
    path = TOPOS_DIR / "thecrag" / f"{i}.jpg"
    if not path.exists():
        with store.connect() as db:
            row = db.execute("SELECT url FROM thecrag_img WHERE id = ?", (i,)).fetchone()
        if not row or not TC_IMG_URL.fullmatch(row["url"]):
            return PlainTextResponse("no", status_code=404)
        async with _tc_img_sem:
            if not path.exists():
                try:
                    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
                        r = await c.get(row["url"], headers={"User-Agent": TC_UA, "Referer": "https://www.thecrag.com/"})
                except Exception as e:  # noqa: BLE001
                    log.warning("foto de theCrag %s: %s", row["url"], e)
                    return PlainTextResponse("upstream", status_code=502)
                if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/") or len(r.content) < 500:
                    return PlainTextResponse("upstream", status_code=502)
                path.parent.mkdir(parents=True, exist_ok=True)
                if _disk_low(path.parent):  # sin sitio: se sirve sin guardar y se hace hueco
                    await asyncio.to_thread(tc_prune, path.parent)
                    return Response(r.content, media_type=_sniff(r.content[:12]), headers={"cache-control": "private, max-age=86400", "x-robots-tag": "noindex"})
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(r.content)
                tmp.replace(path)
                global _tc_last_prune
                if time.time() - _tc_last_prune > 60:
                    _tc_last_prune = time.time()
                    await asyncio.to_thread(tc_prune, path.parent)
    elif time.time() - path.stat().st_mtime > 86400:
        os.utime(path)  # usada: la última en borrarse
    if not path.exists():
        return PlainTextResponse("no", status_code=404)
    with open(path, "rb") as f:
        mt = _sniff(f.read(12))
    return FileResponse(path, media_type=mt, headers={"cache-control": "private, max-age=31536000, immutable", "x-robots-tag": "noindex"})


# --- croquis de papel: subida y edición de trazos -------------------------------------------------------------------------

MAX_UPLOAD = 25 * 1024 * 1024
_RID = re.compile(r"[\w.-]{1,60}")


async def upload(request: Request) -> Response:
    """`PUT …/upload?heading=&sector=&baked=1` con la imagen (JPEG o PNG) en el cuerpo. Crea un croquis `paper` sin
    vías; las pone después `PUT /_proxy/topos/<hash>/routes`."""
    if (d := _denied(request)) is not None:
        return d
    k = _key(request)
    if not k:
        return JSONResponse({"error": "bad crag"}, status_code=400)
    sector = request.query_params.get("sector", "")
    if sector and not SLUG.fullmatch(sector):
        return JSONResponse({"error": "bad sector"}, status_code=400)
    body = await request.body()
    if not body or len(body) > MAX_UPLOAD:
        return JSONResponse({"error": "empty or too big (25 MB max)"}, status_code=413 if body else 400)
    ext = ".png" if body[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg" if body[:2] == b"\xff\xd8" else None
    if not ext:
        return JSONResponse({"error": "JPEG or PNG only"}, status_code=415)
    h = "paper-" + os.urandom(6).hex()
    out = TOPOS_DIR / "paper"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{h}{ext}"
    path.write_bytes(body)
    wh = image_size(path)
    if not wh:
        path.unlink(missing_ok=True)
        return JSONResponse({"error": "can't read the image size"}, status_code=415)
    key = crag_key(*k)
    heading = request.query_params.get("heading", "").strip()[:120] or "Paper topo"
    baked = request.query_params.get("baked", "1") != "0"
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM crag_info WHERE crag = ?", (key,)).fetchone():
            db.execute("INSERT INTO crag_info (crag, cat, name, data, updated) VALUES (?, ?, ?, '{}', ?)",
                       (key, k[0], request.query_params.get("crag_name", "").strip()[:120] or k[2], time.time()))
        sname = request.query_params.get("sector_name", "").strip()[:120]
        db.execute(
            "INSERT INTO topo (hash, source, kind, crag, sector, sector_name, heading, file, width, height, baked, blank, "
            "ordering, status, updated) VALUES (?, 'paper', 'topo', ?, ?, ?, ?, ?, ?, ?, ?, NULL, 10000, 'ok', ?)",
            (h, key, sector, sname, heading, str(path), wh[0], wh[1], int(baked), time.time()))
    return JSONResponse({"ok": True, "hash": h, "image": image_url(h), "width": wh[0], "height": wh[1]})


async def set_routes(request: Request) -> Response:
    """`PUT /_proxy/topos/<hash>/routes` con `[{id, num, name, grade, points: [{x, y}], slug, sector, height, ascents}]`:
    sustituye las vías del croquis (de papel o de 8a.nu, para corregirlo)."""
    if (d := _denied(request)) is not None:
        return d
    h = request.path_params["hash"]
    try:
        rows = await request.json()
    except ValueError:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if not HASH.fullmatch(h) or not isinstance(rows, list) or len(rows) > 500:
        return JSONResponse({"error": "bad request"}, status_code=400)
    clean = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            return JSONResponse({"error": f"route {i}"}, status_code=400)
        rid = str(r.get("id") or f"p{i + 1}")
        pts = []
        for p in r.get("points") or []:
            try:
                pts.append([round(float(p["x"] if isinstance(p, dict) else p[0]), 1), round(float(p["y"] if isinstance(p, dict) else p[1]), 1)])
            except (KeyError, TypeError, ValueError, IndexError):
                return JSONResponse({"error": f"route {i}: points"}, status_code=400)
        if not _RID.fullmatch(rid):
            return JSONResponse({"error": f"route {i}: id"}, status_code=400)
        asc = r.get("ascents")
        clean.append((h, rid, str(r.get("num") or "")[:10], str(r.get("name") or "")[:120], str(r.get("grade") or "")[:12],
                      json.dumps(pts), str(r.get("slug") or "")[:120], str(r.get("sector") or "")[:120],
                      int(r["height"]) if str(r.get("height") or "").isdigit() else None, int(asc) if isinstance(asc, (int, float)) else None))
    with store.connect() as db:
        if not db.execute("SELECT 1 FROM topo WHERE hash = ?", (h,)).fetchone():
            return JSONResponse({"error": "not_found"}, status_code=404)
        db.execute("DELETE FROM topo_route WHERE topo = ?", (h,))
        db.executemany("INSERT OR REPLACE INTO topo_route (topo, rid, num, name, grade, points, slug, sector, height, ascents) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", clean)
        db.execute("UPDATE topo SET updated = ? WHERE hash = ?", (time.time(), h))
    return JSONResponse({"ok": True, "routes": len(clean)})


async def patch_topo(request: Request) -> Response:
    """`PATCH /_proxy/topos/<hash>` con {heading, sector, sector_name, baked, status: ok|deleted}; DELETE lo retira."""
    if (d := _denied(request)) is not None:
        return d
    h = request.path_params["hash"]
    if not HASH.fullmatch(h):
        return JSONResponse({"error": "bad hash"}, status_code=400)
    body = {"status": "deleted"} if request.method == "DELETE" else await request.json()
    sets, vals = [], []
    for col in ("heading", "sector", "sector_name", "status"):
        if col in body:
            v = str(body[col] or "")[:120]
            if col == "status" and v not in ("ok", "deleted") or col == "sector" and v and not SLUG.fullmatch(v):
                return JSONResponse({"error": col}, status_code=400)
            sets.append(f"{col} = ?")
            vals.append(v)
    if "baked" in body:
        sets.append("baked = ?")
        vals.append(1 if body["baked"] else 0)
    if not sets:
        return JSONResponse({"error": "nothing to change"}, status_code=400)
    with store.connect() as db:
        cur = db.execute(f"UPDATE topo SET {', '.join(sets)}, updated = ? WHERE hash = ?", (*vals, time.time(), h))
        if not cur.rowcount:
            return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True})


# --- sin red: service worker con ámbito /_proxy/topos/ ------------------------------------------------------------------

STATIC = Path(__file__).parent / "static"


async def sw(_: Request) -> Response:
    return FileResponse(STATIC / "topos-sw.js", media_type="text/javascript",
                        headers={"cache-control": "no-cache", "service-worker-allowed": "/_proxy/topos/"})


async def static_js(request: Request) -> Response:
    cache = "public, max-age=31536000, immutable" if request.query_params.get("v") else "no-cache"
    return FileResponse(STATIC / "topos.js", media_type="text/javascript", headers={"cache-control": cache})


async def saved_index(request: Request) -> Response:
    """Lista de escuelas guardadas para sin red (la lee el JS de las cachés del service worker)."""
    if (d := _denied(request)) is not None:
        return d
    return HTMLResponse(
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Saved topos</title><style>body{margin:0;padding:12px;background:#f3f3ef;font:16px system-ui,sans-serif}"
        "li{margin:8px 0}a{color:#8b1d8b}</style></head><body><h2>Topos saved for offline</h2><ul id=l></ul>"
        "<p id=m>Reading…</p><script>(async()=>{const ul=document.getElementById('l'),m=document.getElementById('m');"
        "if(!('caches' in window)){m.textContent='No cache support';return}const c=await caches.open('nut-v1');"
        "const ks=(await c.keys()).map(r=>new URL(r.url).pathname+new URL(r.url).search).filter(p=>/[/]page([?]|$)/.test(p));"
        "m.textContent=ks.length?'':'Nothing saved yet: open a crag page and tap «Save offline».';"
        "ks.sort().forEach(p=>{const li=document.createElement('li'),a=document.createElement('a');a.href=p;"
        "a.textContent=decodeURIComponent(p.replace('/_proxy/topos/','').replace('/page','').replace('?sector=',' · '));li.appendChild(a);ul.appendChild(li)})})()</script></body></html>",
        headers={"x-robots-tag": "noindex"},
    )


CRAG = "{cat}/{country}/{crag}"
routes = [
    Route("/_proxy/topos/", saved_index),
    Route("/_proxy/topos/sw.js", sw),
    Route("/_proxy/topos/static/topos.js", static_js),
    Route("/_proxy/topos/thecrag", thecrag),
    Route("/_proxy/topos/img/tc/{id}.jpg", thecrag_image),
    Route(f"/_proxy/topos/{CRAG}/upload", upload, methods=["PUT", "POST"]),
    Route("/_proxy/topos/{hash}/routes", set_routes, methods=["PUT"]),
    Route("/_proxy/topos/{hash}", patch_topo, methods=["PATCH", "DELETE"]),
    Route(f"/_proxy/topos/{CRAG}", crag_json),
    Route(f"/_proxy/topos/{CRAG}/page", page),
    Route("/_proxy/topos/img/{hash}.jpg", image),
]
