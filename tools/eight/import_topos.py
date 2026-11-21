#!/usr/bin/env python3
"""Importa en la base del proxy los croquis premium de 8a.nu bajados con la cuenta alias (~/tmp/8a-alias).

    uv run tools/eight/import_topos.py ~/tmp/8a-alias [--data-dir ~/.local/share/8anu-proxy] [--country spain]

Lee:
- datos/escuelas.jsonl: una ficha por escuela (acceso, aproximación, coordenadas, sectores con sus croquis, galería).
- crops/<país>/<escuela>/<sector>--<hash>.jpg: los croquis (con las líneas pintadas). Si falta, se busca en
  accesos/<país>/<escuela>/topo--<hash>.jpg.
- accesos/<país>/<escuela>/acceso--<hash>.jpg: las fotos de acceso.
- datos/vias.jsonl (si ya existe): cada vía con su trazo (`path`, en píxeles de `reference_width`) y su croquis.
- blank/<hash>.jpg (si existe, lo baja tools/eight/fetch_blank.py): la foto sin líneas.

Las imágenes se enlazan en duro en DATA_DIR/topos/<hash>.jpg (mismo disco: no ocupan más; si no se puede, se copian).
Se puede repetir: INSERT OR REPLACE por hash; los trazos se escalan al ancho real de la imagen.
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import store  # noqa: E402
from app.topos import CATS, jpeg_size  # noqa: E402


def link(src: Path, dst: Path) -> bool:
    if dst.exists():
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("--data-dir", type=Path, default=store.DATA_DIR)
    ap.add_argument("--country", help="solo este país (slug)")
    ap.add_argument("--only-routes", action="store_true", help="solo datos/vias.jsonl (fichas e imágenes ya importadas)")
    a = ap.parse_args()
    store.DATA_DIR = a.data_dir
    topos_dir = a.data_dir / "topos"
    topos_dir.mkdir(parents=True, exist_ok=True)
    src: Path = a.src.expanduser()
    now = time.time()
    db = store.connect()
    n_crags = n_topos = n_access = n_missing = 0
    sizes: dict[str, tuple[int, int]] = {}

    if not a.only_routes:
        for line in open(src / "datos" / "escuelas.jsonl", encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            country, slug = d.get("countrySlug"), d.get("cragSlug")
            if not country or not slug or (a.country and country != a.country):
                continue
            key = f"{country}/{slug}"
            cat = CATS.get(d.get("category"), "sportclimbing")
            sectors = d.get("sectors") or []
            info = {k: d.get(k) for k in (
                "unifiedId", "areaName", "areaSlug", "premium_description", "premium_approach", "premium_access",
                "premium_notes", "walkingTime", "altitude", "rock_type", "rock_quality", "heightRange", "exposition",
                "crag_coords_premium", "parking_coords_premium", "crag_coords_community", "parking_coords_community",
                "location", "premium", "image")}
            info["sectors"] = [{"slug": s.get("sectorSlug"), "name": (s.get("sectorName") or "").strip(),
                                "topos": s.get("topos") or [], "lat": s.get("latitude"), "lon": s.get("longitude")}
                               for s in sectors]
            db.execute("INSERT OR REPLACE INTO crag_info (crag, cat, name, data, updated) VALUES (?, ?, ?, ?, ?)",
                       (key, cat, d.get("cragName") or slug, json.dumps(info, ensure_ascii=False), now))
            n_crags += 1
            for i, s in enumerate(sorted(sectors, key=lambda s: (s.get("ordering") or 0, s.get("sectorSlug") or ""))):
                ss = s.get("sectorSlug") or ""
                sname = (s.get("sectorName") or "").strip()
                hashes = s.get("topos") or []
                for j, h in enumerate(hashes):
                    f = src / "crops" / country / slug / f"{ss}--{h}.jpg"
                    if not f.exists():
                        f = src / "accesos" / country / slug / f"topo--{h}.jpg"
                    heading = sname + (f" · {j + 1}/{len(hashes)}" if len(hashes) > 1 else "")
                    blank = topos_dir / "blank" / f"{h}.jpg"
                    wh = None
                    if f.exists():
                        dst = topos_dir / f"{h}.jpg"
                        link(f, dst)
                        wh = jpeg_size(dst)
                    if not wh:
                        # Sin imagen pintada: queda apuntado («missing») para que fetch_blank.py baje la foto en blanco y
                        # el visor pinte las líneas; hasta entonces no se enseña.
                        if not db.execute("SELECT 1 FROM topo WHERE hash = ? AND status = 'ok'", (h,)).fetchone():
                            db.execute(
                                "INSERT OR REPLACE INTO topo (hash, source, kind, crag, sector, sector_name, heading, file, width, "
                                "height, baked, blank, ordering, status, updated) VALUES (?, '8a', 'topo', ?, ?, ?, ?, '', NULL, NULL, 1, NULL, ?, 'missing', ?)",
                                (h, key, ss, sname, heading, i * 100 + j, now))
                        n_missing += 1
                        continue
                    sizes[h] = wh
                    db.execute(
                        "INSERT OR REPLACE INTO topo (hash, source, kind, crag, sector, sector_name, heading, file, width, "
                        "height, baked, blank, ordering, status, updated) VALUES (?, '8a', 'topo', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'ok', ?)",
                        (h, key, ss, sname, heading, str(dst), wh[0], wh[1], str(blank) if blank.exists() else None,
                         i * 100 + j, now))
                    n_topos += 1
            for h in (d.get("gallery") or {}).get("access") or []:
                f = src / "accesos" / country / slug / f"acceso--{h}.jpg"
                if not f.exists():
                    continue
                dst = topos_dir / f"{h}.jpg"
                link(f, dst)
                wh = jpeg_size(dst)
                if not wh:
                    continue
                db.execute(
                    "INSERT OR REPLACE INTO topo (hash, source, kind, crag, sector, sector_name, heading, file, width, height, "
                    "baked, blank, ordering, status, updated) VALUES (?, '8a', 'access', ?, '', '', 'Access', ?, ?, ?, 1, NULL, 0, 'ok', ?)",
                    (h, key, str(dst), wh[0], wh[1], now))
                n_access += 1
            if n_crags % 500 == 0:
                db.commit()
                print(f"  {n_crags} escuelas, {n_topos} croquis, {n_access} fotos de acceso", flush=True)
        db.commit()
        print(f"escuelas {n_crags}, croquis {n_topos}, fotos de acceso {n_access}, sin imagen (missing) {n_missing}")

    vias = src / "datos" / "vias.jsonl"
    if not vias.exists():
        print("sin datos/vias.jsonl todavía: los trazos se importan cuando exista (--only-routes)")
        return
    if not sizes:
        sizes = {r["hash"]: (r["width"], r["height"]) for r in db.execute("SELECT hash, width, height FROM topo WHERE status = 'ok'")}
    # Los croquis sin imagen aún: los trazos se guardan en píxeles de reference_width (y ese ancho en la tabla);
    # fetch_blank.py los reescala al bajar la foto.
    missing = {r["hash"] for r in db.execute("SELECT hash FROM topo WHERE status = 'missing'")}
    n_routes = n_skipped = 0
    for line in open(vias, encoding="utf-8"):
        try:
            v = json.loads(line)
        except ValueError:
            continue
        h = v.get("topo")
        if not h or (h not in sizes and h not in missing) or (a.country and v.get("countrySlug") != a.country):
            n_skipped += 1
            continue
        rid = str(v.get("zlaggableId") or v.get("unifiedId") or "")
        if not rid:
            continue
        ref = v.get("reference_width") or 0
        if h in sizes:
            w = sizes[h][0]
            k = w / ref if ref else 1.0
        else:
            k = 1.0
            if ref:
                db.execute("UPDATE topo SET width = ? WHERE hash = ? AND status = 'missing'", (ref, h))
        pts = [[round(p[0] * k, 1), round(p[1] * k, 1)] for p in (v.get("path") or []) if isinstance(p, list) and len(p) >= 2]
        db.execute(
            "INSERT OR REPLACE INTO topo_route (topo, rid, num, name, grade, points, slug, sector, height, ascents) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (h, rid, str(v.get("topo_num") or ""), v.get("zlaggableName") or "", v.get("difficulty") or "",
             json.dumps(pts), v.get("zlaggableSlug") or "", v.get("sectorSlug") or "", v.get("height"),
             v.get("totalAscents")))
        n_routes += 1
    db.commit()
    print(f"vías con trazo {n_routes}, saltadas {n_skipped}")


if __name__ == "__main__":
    main()
