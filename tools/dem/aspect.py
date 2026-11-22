"""Orientación de las escuelas con modelos digitales de elevación (fuente «dem» en la tabla aspect).

    /usr/bin/python3 -I tools/dem/aspect.py --all                   # todas las escuelas del mapa sin «dem» todavía
    /usr/bin/python3 -I tools/dem/aspect.py --all --force           # recalcularlas todas
    /usr/bin/python3 -I tools/dem/aspect.py --crag <clave> <lat> <lon> [--dry-run]   # una (lo usa el proxy)

Va con el Python del sistema porque necesita GDAL y numpy (paquetes python-gdal y python-numpy de Arch), que no
están en el entorno del proxy. No se baja ninguna tesela entera ni se guarda nada en disco aparte del resultado:

- España: MDT05 del IGN/CNIG (paso de 5 m) por su WCS INSPIRE, una ventana pequeña por escuela (~50–150 KB).
- Resto (y si el IGN falla o no tiene datos): Copernicus DEM GLO-30 (30 m) de los COG públicos de AWS
  (s3://copernicus-dem-30m, sin cuenta), leyendo con peticiones parciales solo los bloques de 1024×1024 que tocan
  escuelas (~2 MB cada uno); GLO-90 donde falta GLO-30 (unas pocas teselas no publicadas).

Método («steep-v1»): pendiente y orientación de cada celda (diferencias centrales) en un disco de RADIUS m
alrededor de la escuela; se quedan las celdas más empinadas (las paredes; en un DEM de 30 m una pared vertical sale
como una ladera de 35–60°), pesadas por pendiente y cercanía, y se agrupan por orientación: cada grupo con peso
suficiente es una cara («faces»), la principal es la orientación y su dispersión el margen («spread»). Con 30 m no
se ven paredes pequeñas ni desplomes, y las coordenadas de 8a.nu a veces son el aparcamiento: por eso va por debajo
de OSM, theCrag y el usuario (ver app/aspect.py).
"""

import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import numpy as np
from osgeo import gdal

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import store  # noqa: E402

gdal.UseExceptions()
gdal.SetConfigOption("AWS_NO_SIGN_REQUEST", "YES")
gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "4")
gdal.SetConfigOption("GDAL_HTTP_RETRY_DELAY", "2")
gdal.SetCacheMax(1024 * 1024 * 1024)

METHOD = "steep-v1"
RADIUS = 200  # m alrededor de la escuela en los que se buscan paredes
MARGIN = 60  # m de más en la ventana para las diferencias
CACHE = Path(os.environ.get("CACHE_DIR", Path.home() / ".cache" / "8anu-proxy")) / "map-crags.json"
IGN_WCS = "https://servicios.idee.es/wcs-inspire/mdt"
# España peninsular y Baleares, Canarias (el MDT05 no tiene datos fuera de España: devuelve vacío y se usa Copernicus)
SPAIN = [(35.8, 44.0, -9.5, 4.6), (27.5, 29.5, -18.3, -13.3), (35.2, 36.0, -5.5, -2.8)]


def in_spain(lat: float, lon: float) -> bool:
    return any(s <= lat <= n and w <= lon <= e for s, n, w, e in SPAIN)


# --- Ventanas de elevación -----------------------------------------------------------------------------------------


def ign_window(lat: float, lon: float, r: float) -> tuple[np.ndarray, float, float, dict] | None:
    """Ventana del MDT05 del IGN centrada en el punto: (z, metros por píxel al este, al norte, meta) o None."""
    dlat = r / 111320
    dlon = r / (111320 * math.cos(math.radians(lat)))
    url = (f"{IGN_WCS}?SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=Elevacion4258_5&FORMAT=image/tiff"
           f"&SUBSET=Lat({lat - dlat:.6f},{lat + dlat:.6f})&SUBSET=Long({lon - dlon:.6f},{lon + dlon:.6f})")  # fmt: skip
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            break
        except Exception:  # noqa: BLE001 — el WCS a veces tarda: se reintenta y si no, Copernicus
            time.sleep(2 * (attempt + 1))
    else:
        return None
    if not data.startswith((b"II*", b"MM\x00*")):
        return None
    path = f"/vsimem/ign_{threading.get_ident()}.tif"
    gdal.FileFromMemBuffer(path, data)
    try:
        ds = gdal.Open(path)
        z = ds.GetRasterBand(1).ReadAsArray().astype("float64")
        nodata = ds.GetRasterBand(1).GetNoDataValue()
        gt = ds.GetGeoTransform()
    finally:
        gdal.Unlink(path)
    bad = (z <= -100) | (z > 9000) | ((z == nodata) if nodata is not None else False)
    if z.size < 100 or bad.mean() > 0.05:
        return None
    return z, gt[1] * 111320 * math.cos(math.radians(lat)), -gt[5] * 111320, {"dem": "ign-mdt05", "res_m": 5}


def _cop_name(lat: float, lon: float, res: int) -> str:
    la, lo = math.floor(lat), math.floor(lon)
    ns, ew = ("N" if la >= 0 else "S"), ("E" if lo >= 0 else "W")
    return f"Copernicus_DSM_COG_{10 if res == 30 else 30}_{ns}{abs(la):02d}_00_{ew}{abs(lo):03d}_00_DEM"


class Copernicus:
    """Teselas de Copernicus abiertas (una por hilo: los datasets de GDAL no se comparten entre hilos)."""

    def __init__(self):
        self.ds: dict[tuple, gdal.Dataset | None] = {}

    def open(self, lat: float, lon: float):
        key = (math.floor(lat), math.floor(lon))
        if key not in self.ds:
            self.ds[key] = None
            for res, bucket in ((30, "copernicus-dem-30m"), (90, "copernicus-dem-90m")):
                name = _cop_name(lat, lon, res)
                try:
                    self.ds[key] = (gdal.Open(f"/vsis3/{bucket}/{name}/{name}.tif"), res)
                    break
                except RuntimeError:
                    continue
        return self.ds[key]

    def window(self, lat: float, lon: float, r: float):
        opened = self.open(lat, lon)
        if not opened:
            return None  # mar, o sin tesela
        ds, res = opened
        gt = ds.GetGeoTransform()
        mx = r / (111320 * math.cos(math.radians(lat))) / gt[1]
        my = r / 111320 / -gt[5]
        px, py = (lon - gt[0]) / gt[1], (lat - gt[3]) / gt[5]
        x0, y0 = max(0, int(px - mx)), max(0, int(py - my))
        x1, y1 = min(ds.RasterXSize, int(px + mx) + 2), min(ds.RasterYSize, int(py + my) + 2)
        z = ds.GetRasterBand(1).ReadAsArray(x0, y0, x1 - x0, y1 - y0).astype("float64")
        # La escuela no siempre queda en el centro (borde de tesela): se devuelve dónde cae
        center = (py - y0, px - x0)
        return z, gt[1] * 111320 * math.cos(math.radians(lat)), -gt[5] * 111320, {"dem": f"copernicus-glo{res}", "res_m": res}, center


# --- Análisis ------------------------------------------------------------------------------------------------------


def analyze(z: np.ndarray, dx: float, dy: float, center: tuple[float, float] | None = None) -> dict | None:
    """Orientación de las paredes alrededor de `center` (fila, columna; por defecto el centro de la ventana)."""
    if z.shape[0] < 5 or z.shape[1] < 5:
        return None
    cy, cx = center if center else ((z.shape[0] - 1) / 2, (z.shape[1] - 1) / 2)
    gy, gx = np.gradient(z)
    dzdx, dzdn = gx / dx, -gy / dy  # x al este, n al norte (las filas van hacia el sur)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdn)))
    face = np.degrees(np.arctan2(-dzdx, -dzdn)) % 360  # hacia donde baja = hacia donde mira la pared
    yy, xx = np.indices(z.shape)
    dist = np.hypot((xx - cx) * dx, (yy - cy) * dy)
    disk = (dist <= RADIUS) & (yy > 0) & (xx > 0) & (yy < z.shape[0] - 1) & (xx < z.shape[1] - 1)
    if disk.sum() < 5:
        return None
    s = slope[disk]
    max_slope = float(s.max())
    res = max(dx, dy)
    floor = 25 if res > 15 else 40  # con 5 m las paredes salen mucho más empinadas
    thr = max(min(floor, max_slope * 0.8), float(np.percentile(s, 80)))
    steep = disk & (slope >= thr) & (slope > 5)
    if steep.sum() < 2:
        return {"deg": None, "slope": round(max_slope), "spread": None, "faces": [], "confidence": 0.0}
    w = np.tan(np.radians(np.minimum(slope[steep], 80))) * np.exp(-dist[steep] / 120)
    a = face[steep]
    # Histograma circular de 10° suavizado → picos = caras
    hist = np.bincount((a // 10).astype(int) % 36, weights=w, minlength=36)
    k = np.array([1, 2, 3, 2, 1], float)
    smooth = sum(np.roll(hist, i - 2) * k[i] for i in range(5))
    peaks = [i for i in range(36) if smooth[i] >= smooth[i - 1] and smooth[i] > smooth[(i + 1) % 36]]
    peaks.sort(key=lambda i: -smooth[i])
    faces, total = [], w.sum()
    for p in peaks:
        if smooth[p] < 0.3 * smooth[peaks[0]]:
            break
        centre = p * 10 + 5
        if any(abs((centre - f["deg"] + 180) % 360 - 180) < 60 for f in faces):
            continue
        sel = np.abs((a - centre + 180) % 360 - 180) <= 45
        ws, aa = w[sel], np.radians(a[sel])
        vx, vy = (ws * np.sin(aa)).sum(), (ws * np.cos(aa)).sum()
        rlen = math.hypot(vx, vy) / ws.sum()
        faces.append({
            "deg": round(math.degrees(math.atan2(vx, vy)) % 360, 1), "weight": round(float(ws.sum() / total), 2),
            "spread": round(min(45.0, max(10.0, math.degrees(math.sqrt(max(0.0, -2 * math.log(rlen))))))),
            "slope": round(float(slope[steep][sel].max())),
        })  # fmt: skip
    faces = [f for f in faces if f["weight"] >= 0.15] or faces[:1]
    faces.sort(key=lambda f: -f["weight"])
    aa = np.radians(a)
    resultant = math.hypot((w * np.sin(aa)).sum(), (w * np.cos(aa)).sum()) / total
    confidence = round(float(min(1.0, resultant * min(1.0, max_slope / (45 if res > 15 else 60)) * (faces[0]["weight"] + 0.3))), 2)
    main = faces[0]
    return {"deg": main["deg"], "slope": round(max_slope), "spread": main["spread"], "faces": faces,
            "confidence": confidence}  # fmt: skip


# --- Escuelas ------------------------------------------------------------------------------------------------------


def compute(lat: float, lon: float, cop: Copernicus, ign: bool = True) -> dict | None:
    out = None
    if ign and in_spain(lat, lon):
        got = ign_window(lat, lon, RADIUS + MARGIN)
        if got:
            z, dx, dy, meta = got
            out = analyze(z, dx, dy)
    if out is None:
        got = cop.window(lat, lon, RADIUS + MARGIN)
        if not got:
            return None
        z, dx, dy, meta, center = got
        out = analyze(z, dx, dy, center)
        if out is None:
            return None
    out["meta"] = {**meta, "method": METHOD, "radius_m": RADIUS, "computed": date.today().isoformat(),
                   "confidence": out.pop("confidence")}  # fmt: skip
    return out


def save(key: str, r: dict) -> None:
    if r.get("deg") is None:
        return
    store.set_aspect(key, "", r["deg"], "dem", r["slope"], spread=r["spread"], faces=r["faces"], meta=r["meta"])


def done_keys() -> set[str]:
    with store.connect() as db:
        rows = db.execute("SELECT crag, meta FROM aspect WHERE source = 'dem' AND sector = ''").fetchall()
    return {r["crag"] for r in rows if METHOD in (r["meta"] or "")}


def run_all(force: bool, workers: int, limit: int | None, dry: bool) -> None:
    crags = json.loads(CACHE.read_text())
    skip = set() if force else done_keys()
    todo = [c for c in crags if c["key"] not in skip][:limit]
    # Por tesela de 1°: cada hilo abre la suya y los bloques que comparten escuelas vecinas se bajan una vez
    tiles = defaultdict(list)
    for c in todo:
        tiles[(math.floor(c["lat"]), math.floor(c["lon"]))].append(c)
    print(f"{len(todo)} escuelas en {len(tiles)} teselas", flush=True)
    stats = defaultdict(int)
    lock = threading.Lock()
    t0 = time.time()

    def tile_job(cs):
        cop = Copernicus()
        for c in sorted(cs, key=lambda c: (-c["lat"], c["lon"])):
            try:
                r = compute(c["lat"], c["lon"], cop)
            except Exception as e:  # noqa: BLE001 — una escuela que falla no para la pasada
                with lock:
                    stats["error"] += 1
                print(f"error {c['key']}: {e}", file=sys.stderr, flush=True)
                continue
            with lock:
                if not r or r["deg"] is None:
                    stats["sin pared"] += 1
                else:
                    stats[r["meta"]["dem"]] += 1
                    if not dry:
                        save(c["key"], r)
                stats["n"] += 1
                if stats["n"] % 200 == 0:
                    print(f"{stats['n']}/{len(todo)} en {time.time() - t0:.0f} s {dict(stats)}", flush=True)

    with ThreadPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(tile_job, cs) for cs in tiles.values()]):
            f.result()
    print(f"hecho en {time.time() - t0:.0f} s: {dict(stats)}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--crag", nargs=3, metavar=("KEY", "LAT", "LON"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-ign", action="store_true", help="solo Copernicus (para comparar)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    if args.crag:
        key, lat, lon = args.crag[0], float(args.crag[1]), float(args.crag[2])
        r = compute(lat, lon, Copernicus(), ign=not args.no_ign)
        if r and not args.dry_run:
            save(key, r)
        print(json.dumps(r))
    elif args.all:
        run_all(args.force, args.workers, args.limit, args.dry_run)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
