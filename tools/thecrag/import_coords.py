"""Pasa a la base del proxy las coordenadas sacadas de theCrag con scrape.py coords/crags.json coords/out --coords (fuente «thecrag»).

    .venv/bin/python tools/thecrag/import_coords.py coords/out/ map-crags.json          # solo enseña lo que haría
    .venv/bin/python tools/thecrag/import_coords.py coords/out/ map-crags.json --write  # escribe en DATA_DIR/data.db

La zona de theCrag se casó solo por nombre, así que puede ser otro sitio que se llame igual. Se guarda si cae a menos
de AREA_KM de alguna escuela de 8a.nu de la misma zona («area» del mapa, map-crags.json) o, si la zona no tiene
ninguna situada, dentro de España.
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import store  # noqa: E402

AREA_KM = 30
SPAIN = (27.5, 44.0, -18.5, 4.5)  # lat, lat, lon, lon (con Canarias y Baleares)


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def main() -> None:
    outdir, mapfile = Path(sys.argv[1]), Path(sys.argv[2])
    write = "--write" in sys.argv
    areas: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for c in json.load(open(mapfile)):
        if c.get("area") and c.get("lat") is not None:
            areas.setdefault((c["country"], c["area"]), []).append((c["lat"], c["lon"]))
    info = {c["key"]: c for c in json.load(open(outdir.parent / "crags.json"))}  # la lista que se pasó a scrape.py
    ok = bad = none = 0
    for f in sorted(outdir.glob("*.json")):
        d = json.load(open(f))
        if "crag" not in d:
            continue
        if d.get("lat") is None:
            none += 1
            continue
        p = (d["lat"], d["lon"])
        c = info.get(d["crag"], {})
        pts = areas.get((c.get("country"), c.get("area")), [])
        near = min((km(p, q) for q in pts), default=None)
        good = near <= AREA_KM if near is not None else SPAIN[0] <= p[0] <= SPAIN[1] and SPAIN[2] <= p[1] <= SPAIN[3]
        why = f"{near:.0f} km de su zona «{c.get('area')}»" if near is not None else "sin zona situada, dentro de España" if good else "fuera de España"
        print(f"{'OK ' if good else 'NO '} {d['crag']}: «{d['name']}» {p[0]:.4f}, {p[1]:.4f} ({why}, nombre {d.get('sim')})")
        if not good:
            bad += 1
            continue
        ok += 1
        if write:
            store.set_coords(d["crag"], "thecrag", p[0], p[1], {"url": d["url"], "name": d["name"], "sim": d.get("sim")})
    print(f"{ok} escuelas {'guardadas' if write else 'que se guardarían (sin --write)'}, {bad} descartadas por estar lejos, {none} sin ubicación en theCrag")


if __name__ == "__main__":
    main()
