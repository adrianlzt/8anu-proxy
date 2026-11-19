"""Pasa a la base del proxy la orientación sacada de theCrag con scrape.py (fuente «thecrag»).

    .venv/bin/python tools/thecrag/import_aspects.py salida/ sectors.json          # solo enseña lo que haría
    .venv/bin/python tools/thecrag/import_aspects.py salida/ sectors.json --write  # escribe en DATA_DIR/data.db

- Escuela: la orientación propia (no heredada) de la zona raíz en theCrag, si es una sola dirección o direcciones
  vecinas (p. ej. E y SE → media). Si son opuestas (Patones: E y W) no hay una pared principal: no se guarda y
  siguen OpenStreetMap o el relieve.
- Sector de 8a.nu: la zona de theCrag de nombre más parecido (≥ 0,8); su orientación propia o, si no tiene, la de
  la zona padre más cercana que la tenga (sin contar la raíz si es múltiple). Lo que marque el usuario va por encima.
"""

import difflib
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app import store  # noqa: E402

DEG = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s)


def direction(aspect: dict | None) -> float | None:
    """Una dirección si las marcadas caben en 90° (media circular); None si no hay o son dispares."""
    dirs = [DEG[k] for k, v in (aspect or {}).items() if v and k in DEG]
    if not dirs:
        return None
    x = sum(math.sin(math.radians(d)) for d in dirs)
    y = sum(math.cos(math.radians(d)) for d in dirs)
    mean = math.degrees(math.atan2(x, y)) % 360
    if any(abs((d - mean + 180) % 360 - 180) > 45 for d in dirs):
        return None
    return mean


def own(n: dict) -> float | None:
    return None if n.get("inherited") else direction(n.get("aspect"))


def main() -> None:
    outdir, sectors_file = Path(sys.argv[1]), Path(sys.argv[2])
    write = "--write" in sys.argv
    sectors = json.load(open(sectors_file))
    total_c = total_s = 0
    for f in sorted(outdir.glob("*.json")):
        d = json.load(open(f))
        nodes = d["nodes"]
        if not nodes:
            continue
        key = d["crag"]
        by_id = {n["id"]: n for n in nodes if n.get("id")}
        root = nodes[0]
        crag_deg = own(root)
        out = []
        if crag_deg is not None:
            out.append(("", crag_deg, root["name"]))
        for s in sectors.get(key, []):
            best = None
            for n in nodes[1:]:
                for label in (n.get("label"), n.get("name")):
                    sim = difflib.SequenceMatcher(None, norm(label), norm(s["name"])).ratio() if norm(label) else 0
                    if sim >= 0.8 and (best is None or sim > best[0]):
                        best = (sim, n)
            if not best:
                continue
            n, deg = best[1], None
            while n is not None and deg is None:
                deg = own(n)
                n = by_id.get(n.get("parent"))
            if deg is not None:
                out.append((s["slug"], deg, best[1]["name"]))
        if out:
            total_c += 1
            total_s += sum(1 for x in out if x[0])
            print(f"{key}: " + ", ".join(f"{slug or '(escuela)'}={deg:.0f}°" for slug, deg, _ in out))
        if write:
            for slug, deg, _ in out:
                store.set_aspect(key, slug, deg, "thecrag")
    print(f"{total_c} escuelas, {total_s} sectores{' guardados' if write else ' (sin --write: no se ha guardado nada)'}")


main()
