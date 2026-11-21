#!/usr/bin/env python3
"""Baja de 8a.nu la versión SIN líneas de cada croquis premium (`blank=true`), para que el visor pinte él las líneas
(encadenadas en verde, grosor por popularidad, ocultar líneas…). Hace falta la sesión premium de la cuenta alias:
la cookie en una línea (`ck.txt` de ~/tmp/8a-alias, renovable con su navegador; ver ESTADO.md allí).

    uv run tools/eight/fetch_blank.py --cookie ~/tmp/8a-alias/ck.txt [--data-dir …] [--pace 1.5] [--width 2000]

Va despacio (una petición cada `--pace` s; si 8a.nu da 429 espera 5 min) y se puede cortar y repetir: salta los que
ya están. Deja cada foto en DATA_DIR/topos/blank/<hash>.jpg y apunta `blank` en la tabla `topo`.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from curl_cffi import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import store  # noqa: E402
from app.topos import jpeg_size  # noqa: E402

API = "https://www.8a.nu/api/unification/outdoor/v1/web/images"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookie", type=Path, required=True)
    ap.add_argument("--data-dir", type=Path, default=store.DATA_DIR)
    ap.add_argument("--pace", type=float, default=1.5)
    ap.add_argument("--width", type=int, default=2000)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    store.DATA_DIR = a.data_dir
    out = a.data_dir / "topos" / "blank"
    out.mkdir(parents=True, exist_ok=True)
    cookie = a.cookie.expanduser().read_text().strip()
    db = store.connect()
    rows = db.execute("SELECT hash, status, width FROM topo WHERE kind = 'topo' AND source = '8a' AND blank IS NULL ORDER BY status DESC, crag, ordering").fetchall()
    todo = [(r["hash"], r["status"], r["width"]) for r in rows]
    print(f"{len(todo)} croquis sin versión en blanco ({sum(1 for t in todo if t[1] == 'missing')} sin ninguna imagen)", flush=True)
    s = requests.Session(impersonate="firefox", timeout=60)
    n_ok = n_fail = 0
    for i, (h, status, old_w) in enumerate(todo):
        if a.limit and n_ok >= a.limit:
            break
        dst = out / f"{h}.jpg"
        if not dst.exists():
            try:
                r = s.get(f"{API}/{h}?blank=true&width={a.width}&format=jpeg", headers={"Cookie": cookie, "User-Agent": UA})
            except Exception as e:  # noqa: BLE001
                print(f"  {h}: {e}", flush=True)
                n_fail += 1
                time.sleep(30)
                continue
            if r.status_code == 429:
                print("  429: 5 min de pausa", flush=True)
                time.sleep(300)
                continue
            ct = r.headers.get("content-type", "")
            if r.status_code != 200 or not ct.startswith("image/") or len(r.content) < 5000:
                print(f"  {h}: {r.status_code} {ct} {len(r.content)} B", flush=True)
                n_fail += 1
                time.sleep(a.pace)
                continue
            tmp = dst.with_suffix(".tmp")
            tmp.write_bytes(r.content)
            tmp.replace(dst)
            time.sleep(a.pace)
        wh = jpeg_size(dst)
        if not wh:
            dst.unlink(missing_ok=True)
            n_fail += 1
            continue
        if status == "missing":
            # Sin foto pintada: la blanca es la única. Los trazos estaban en píxeles de reference_width (width):
            # se reescalan al ancho real.
            k = wh[0] / old_w if old_w else 1.0
            if k != 1.0:
                for r in db.execute("SELECT rid, points FROM topo_route WHERE topo = ?", (h,)).fetchall():
                    pts = [[round(p[0] * k, 1), round(p[1] * k, 1)] for p in json.loads(r["points"] or "[]")]
                    db.execute("UPDATE topo_route SET points = ? WHERE topo = ? AND rid = ?", (json.dumps(pts), h, r["rid"]))
            db.execute("UPDATE topo SET blank = ?, file = ?, width = ?, height = ?, status = 'ok', updated = ? WHERE hash = ?",
                       (str(dst), str(dst), wh[0], wh[1], time.time(), h))
        else:
            # El trazo de las vías está en píxeles de la foto pintada: si la blanca tiene otro tamaño, se reescala en el
            # visor con width/height (viewBox) porque la proporción es la misma.
            db.execute("UPDATE topo SET blank = ?, updated = ? WHERE hash = ?", (str(dst), time.time(), h))
        db.commit()
        n_ok += 1
        if n_ok % 100 == 0:
            print(f"  {n_ok} bajadas, {n_fail} fallos, {len(todo) - i - 1} quedan", flush=True)
    print(f"hecho: {n_ok} bajadas, {n_fail} fallos")


if __name__ == "__main__":
    main()
