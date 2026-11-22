"""Escuelas de 8a.nu (todas, también las que no tienen coordenadas) con su número de ascensiones.

    .venv/bin/python tools/coords/list_crags.py [--min-ascents 10]

La API pública de listados (/api/unification/outdoor/v1/web/crags) va de 20 en 20 y se rompe pasadas las 10.000
(repite la misma página), así que se pide por país y categoría, de más a menos ascensiones, hasta bajar de
--min-ascents (hay ~93.000 «escuelas», la mayoría con una ascensión y un nombre mal escrito). Sale en
CACHE_DIR/all-crags.json: [{"key", "name", "area", "country", "ascents", "routes"}].
"""

import argparse
import json
import os
import time
from pathlib import Path

from curl_cffi import requests

API = "https://www.8a.nu/api/unification/outdoor/v1/web"
OUT = Path(os.environ.get("CACHE_DIR", Path.home() / ".cache" / "8anu-proxy")) / "all-crags.json"
CATEGORY = {0: "sportclimbing", 1: "bouldering"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-ascents", type=int, default=10)
    args = ap.parse_args()
    s = requests.Session(impersonate="chrome")

    def get(url: str) -> dict | list:
        for attempt in range(5):
            r = s.get(url, headers={"accept": "application/json"}, timeout=30)
            if r.status_code == 200:
                return r.json()
            time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"{url}: {r.status_code}")

    out, seen = [], set()
    for cat, slug in CATEGORY.items():
        for country in get(f"{API}/crags/{cat}/countries"):
            page = 0
            while True:
                d = get(f"{API}/crags?pageIndex={page}&sortField=totalascents&order=desc&category={cat}"
                        f"&countrySlug={country['countrySlug']}")  # fmt: skip
                low = False
                for it in d["items"]:
                    if (it.get("totalAscents") or 0) < args.min_ascents:
                        low = True
                        break
                    key = f"{slug}/{it['countrySlug']}/{it['cragSlug']}"
                    if key not in seen:
                        seen.add(key)
                        out.append({"key": key, "name": it["cragName"], "area": it.get("areaName") or "",
                                    "country": it.get("countryName") or "", "ascents": it.get("totalAscents") or 0,
                                    "routes": it.get("totalZlaggables") or 0})  # fmt: skip
                if low or not d["pagination"].get("hasNext") or page >= 490:
                    break
                page += 1
                time.sleep(0.3)
        print(slug, len(out), flush=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False))
    print(f"{len(out)} escuelas en {OUT}")


if __name__ == "__main__":
    main()
