"""Coordenadas de OpenStreetMap para las escuelas de 8a.nu que no las tienen, casando por nombre (fuente «osm» en
crag_coords).

    .venv/bin/python tools/coords/list_crags.py              # antes: CACHE_DIR/all-crags.json
    .venv/bin/python tools/coords/osm_match.py               # solo enseña lo que casaría
    .venv/bin/python tools/coords/osm_match.py --write       # lo guarda en DATA_DIR/data.db

- Qué escuelas: las de all-crags.json que no están en el mapa (map-crags.json, que solo trae las que tienen
  coordenadas) ni tienen ya coordenadas guardadas.
- Dónde buscar: los sitios de escalada de OSM (`climbing=crag/area`, lo que dibuja openclimbing.org) se bajan de las
  teselas de zoom 9 de openclimbing.org (/api/climbing-tiles/tile, GeoJSON sacado de OSM y servido por Cloudflare;
  Overpass va saturado y no aguanta un país entero) alrededor de las escuelas que sí tienen coordenadas (su tesela y
  las vecinas), guardadas en CACHE_DIR/osm-climbing/<z>_<x>_<y>.json. Cada escuela solo se casa con sitios dentro del
  recuadro de su país, que sale de las escuelas de ese país con coordenadas (percentiles 1–99 y medio grado de
  margen): así no hace falta saber el código ISO ni los límites.
- Cómo casar: nombre normalizado (sin acentos ni palabras genéricas como sector, escuela, crag, falaise…) con el de
  OSM; vale si es igual (o las mismas palabras en otro orden). Si la zona de 8a.nu (areaName) tiene escuelas con
  coordenadas, el sitio tiene que caer a menos de AREA_KM de alguna. Si casan varios sitios a más de AMBIGUOUS_KM
  entre sí y ninguno destaca, no se guarda (dos «La Cueva» en el mismo país).
"""

import argparse
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

from curl_cffi import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import store  # noqa: E402

CACHE = Path(os.environ.get("CACHE_DIR", Path.home() / ".cache" / "8anu-proxy"))
OSM_DIR = CACHE / "osm-climbing"
TILES = "https://openclimbing.org/api/climbing-tiles/tile"
ZOOM = 9  # el único con todas las escuelas y zonas (sin vías); 0 y 6 son muestras
AREA_KM = 30
AMBIGUOUS_KM = 5
GENERIC = set("""sector sectors sektor secteur crag crags escuela escola climbing climb bouldering boulder boulders bloc
blocs area zona wall walls pared paret rocher rochers roche falaise falaises site klettergarten fels felsen gebiet
la el les le lo los las de del d l the of di da do das dos des du y i e et and und a en settore voie voies grande
grandes multipitch north south east west nord sud est ouest norte sur este oeste upper lower superior inferior unknown
main left right""".split())


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w and w not in GENERIC]
    return " ".join(words)


def variants(name: str) -> set[str]:
    """«Patones / Pontón de la Oliva», «Rodellar (Mascún)» → cada parte por separado y entera."""
    parts = {name} | {p for p in re.split(r"\s*[/|()]\s*|\s+-\s+", name) if p}
    return {n for n in map(norm, parts) if len(n.replace(" ", "")) >= 4}


def osm_names(tags: dict) -> set[str]:
    keys = [k for k in tags if k in ("name", "alt_name", "official_name", "short_name", "loc_name", "old_name")
            or k.startswith("name:")]  # fmt: skip
    out = set()
    for k in keys:
        for v in tags[k].split(";"):
            out |= variants(v)
    return out


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot((a[0] - b[0]) * 111.32, (a[1] - b[1]) * 111.32 * math.cos(math.radians(a[0])))


def country_boxes(known: list[dict]) -> dict[str, tuple[float, float, float, float]]:
    pts = defaultdict(list)
    for c in known:
        pts[c["key"].split("/")[1]].append((c["lat"], c["lon"]))
    boxes = {}
    for country, ps in pts.items():
        lats, lons = sorted(p[0] for p in ps), sorted(p[1] for p in ps)
        lo, hi = int(len(ps) * 0.01), max(0, math.ceil(len(ps) * 0.99) - 1)
        boxes[country] = (lats[lo] - 0.5, lons[lo] - 0.5, lats[hi] + 0.5, lons[hi] + 0.5)
    return boxes


def _tile(lat: float, lon: float) -> tuple[int, int]:
    return int((lon + 180) / 360 * 2**ZOOM), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2**ZOOM)


def _get_tile(sess, x: int, y: int) -> list[dict]:
    path = OSM_DIR / f"{ZOOM}_{x}_{y}.json"
    if path.exists() and time.time() - path.stat().st_mtime < 90 * 24 * 3600:
        return json.loads(path.read_text())
    for attempt in range(4):
        try:
            r = sess.get(f"{TILES}?z={ZOOM}&x={x}&y={y}", headers={"user-agent": "8anu-proxy (personal)"}, timeout=60)
            if r.status_code == 200:
                break
        except Exception:  # noqa: BLE001 — se reintenta
            pass
        time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"openclimbing {x}/{y}")
    out = []
    for f in r.json().get("features", []):
        pr = f.get("properties") or {}
        if pr.get("type") not in ("crag", "area") or not pr.get("name") or f["geometry"]["type"] != "Point":
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        out.append({"id": str(f["id"]), "lat": lat, "lon": lon, "name": pr["name"], "kind": pr["type"],
                    "routes": pr.get("routeCount") or 0, "names": sorted(variants(pr["name"]))})  # fmt: skip
    path.write_text(json.dumps(out, ensure_ascii=False))
    return out


def osm_features(known: list[dict], workers: int = 3) -> list[dict]:
    """Todos los sitios de escalada de OSM alrededor de las escuelas conocidas (teselas de openclimbing.org)."""
    from concurrent.futures import ThreadPoolExecutor

    OSM_DIR.mkdir(parents=True, exist_ok=True)
    own = {_tile(c["lat"], c["lon"]) for c in known}
    tiles = sorted({(x + i, y + j) for x, y in own for i in (-1, 0, 1) for j in (-1, 0, 1)})
    feats, seen, done = [], set(), 0
    with ThreadPoolExecutor(workers) as ex:
        for got in ex.map(lambda t: _get_tile(requests.Session(impersonate="chrome"), *t), tiles):
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(tiles)} teselas", flush=True)
            for f in got:
                if f["id"] not in seen:
                    seen.add(f["id"])
                    feats.append(f)
    return feats


KIND_RANK = {"crag": 0, "area": 1}


def _key(name: str) -> str:
    return " ".join(sorted(name.split()))


def index(feats: list[dict]) -> dict[str, list[tuple[str, dict]]]:
    """Nombre normalizado con las palabras ordenadas → [(nombre normalizado, sitio)]."""
    idx = defaultdict(list)
    for f in feats:
        for n in f["names"]:
            idx[_key(n)].append((n, f))
    return idx


def match(crag: dict, feats, area_pts: list[tuple[float, float]] | None = None) -> tuple[dict | None, str]:
    """El sitio de OSM con el mismo nombre (normalizado; mismas palabras en otro orden vale un poco menos). Sin nombres
    parecidos a medias: probado con 3.000 escuelas de ubicación conocida, aceptarlos (difflib ≥ 0,9) mete un 10 % de
    sitios a más de 10 km. `area_pts`: escuelas conocidas de la misma zona de 8a.nu; si el sitio cae a más de AREA_KM
    de todas, es otro con el mismo nombre."""
    idx = feats if isinstance(feats, dict) else index(feats)
    names = variants(crag["name"])
    if not names:
        return None, "nombre corto"
    hits = {}
    for a in names:
        for n, f in idx.get(_key(a), []):
            sc = 1.0 if n == a else 0.98
            if sc > hits.get(f["id"], (0,))[0]:
                hits[f["id"]] = (sc, -KIND_RANK.get(f["kind"], 2), f)
    if not hits:
        return None, "sin candidato"
    ranked = sorted(hits.values(), key=lambda h: (h[0], h[1], h[2]["routes"] if "routes" in h[2] else 0), reverse=True)
    if area_pts:
        ranked = [h for h in ranked if min(km(p, (h[2]["lat"], h[2]["lon"])) for p in area_pts) <= AREA_KM]
        if not ranked:
            return None, "lejos de su zona"
    top = ranked[0]
    far = [h for h in ranked[1:] if km((h[2]["lat"], h[2]["lon"]), (top[2]["lat"], top[2]["lon"])) > AMBIGUOUS_KM]
    if far and far[0][0] >= top[0]:
        return None, f"ambiguo ({len(ranked)} sitios)"
    near = [h for h in ranked if km((h[2]["lat"], h[2]["lon"]), (top[2]["lat"], top[2]["lon"])) <= AMBIGUOUS_KM]
    f = top[2]
    return {"lat": round(f["lat"], 5), "lon": round(f["lon"], 5), "osm": f["id"], "name": f["name"], "kind": f["kind"],
            "score": top[0], "candidates": len(near), "area_checked": bool(area_pts)}, "ok"  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--country", help="solo este país (slug de 8a.nu)")
    args = ap.parse_args()
    known = json.loads((CACHE / "map-crags.json").read_text())
    have = {c["key"] for c in known}
    with store.connect() as db:
        have |= {r[0] for r in db.execute("SELECT crag FROM crag_coords")}
    todo = [c for c in json.loads((CACHE / "all-crags.json").read_text()) if c["key"] not in have]
    boxes = country_boxes(known)
    by_country = defaultdict(list)
    for c in todo:
        by_country[c["key"].split("/")[1]].append(c)
    stats = defaultdict(int)
    area = defaultdict(list)
    for c in known:
        if c.get("area"):
            area[(c["key"].split("/")[1], norm(c["area"]))].append((c["lat"], c["lon"]))
    feats_all = osm_features(known)
    print(f"{len(feats_all)} sitios de escalada en OSM", flush=True)
    for country, crags in sorted(by_country.items(), key=lambda kv: -len(kv[1])):
        if args.country and country != args.country:
            continue
        if country not in boxes:
            stats["país sin recuadro"] += len(crags)
            continue
        s_, w_, n_, e_ = boxes[country]
        feats = index([f for f in feats_all if s_ <= f["lat"] <= n_ and w_ <= f["lon"] <= e_])
        found = 0
        for c in crags:
            m, why = match(c, feats, area.get((country, norm(c.get("area") or ""))) if c.get("area") else None)
            stats[why.split(" (")[0]] += 1
            if m:
                found += 1
                print(f"  {c['key']:60s} {c['name'][:30]:30s} → {m['name'][:30]:30s} {m['score']} {m['kind']}")
                if args.write:
                    store.set_coords(c["key"], "osm", m["lat"], m["lon"], {**m, "method": "name-v1",
                                     "computed": date.today().isoformat()})  # fmt: skip
        print(f"{country}: {found}/{len(crags)} casadas ({len(feats)} sitios de OSM en su recuadro)", flush=True)
    print(dict(stats))


if __name__ == "__main__":
    main()
