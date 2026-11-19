"""Orientación de escuelas y sectores en theCrag, navegando despacio con Chrome (Playwright) desde arc2.

    uv run -q --with playwright python tools/thecrag/scrape.py crags.json salida/

crags.json: [{"key": "sportclimbing/spain/patones", "name": "Patones", "lat": …, "lon": …}, …] (escuelas de 8a.nu).
Por cada escuela busca su zona en theCrag (buscador, la más cercana con nombre parecido), recorre sus subzonas
(hasta MAX_PAGES páginas) y guarda en salida/<slug>.json cada zona con su orientación propia (no heredada):
{"crag": key, "url": …, "nodes": [{"id", "name", "depth", "aspect": {"N": 1, …}, "inherited": 0|1}, …]}.
Una página cada PAUSE segundos; si theCrag contesta 403/429 o con un reto, para del todo (no reintenta).
Las escuelas que ya tienen fichero se saltan: se puede relanzar.
"""

import asyncio
import difflib
import json
import random
import re
import sys
import unicodedata
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "https://www.thecrag.com"
PAUSE = 8.0
MAX_PAGES = 30
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
PROFILE = Path.home() / ".cache" / "tc-clearance-profile"  # receta de ~/Documentos/escalada/thecrag-access


SECTORS: dict[str, list[str]] = {}


class Blocked(Exception):
    pass


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s)


def parse(html: str) -> dict:
    m = re.search(r"aspectchart\(\{.*?current: (\{.*?\}).*?inherited: (\d)", html, re.S)
    name = re.search(r"<title>(.*?)(?:,| \|)", html, re.S)
    nid = re.search(r'data-nid="(\d+)"', html)
    return {
        "id": nid.group(1) if nid else None,
        "name": name.group(1).strip() if name else "",
        "aspect": json.loads(m.group(1)) if m else None,
        "inherited": int(m.group(2)) if m else None,
    }


async def fetch(pg, url: str) -> str:
    """Una página, con PAUSE de cortesía. El 403 de theCrag sale a rachas (reputación por frecuencia): se espera 1 y
    luego 3 minutos y se reintenta; si sigue, Blocked."""
    for wait in (0, 240, 540, None):
        await asyncio.sleep(PAUSE + random.random() * 2)
        r = await pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        html = await pg.content()
        blocked = r is None or r.status in (403, 429) or any(x in html[:3000] for x in ("cf-challenge", "Just a moment", "Attention Required"))
        if not blocked:
            print(f"  {url[len(BASE):]}", flush=True)
            return html
        if wait is None:
            raise Blocked(f"{r.status if r else '?'} {url}")
        print(f"  403 en {url[len(BASE):]}: espero {wait + 60} s", flush=True)
        await asyncio.sleep(wait + 60)
    raise Blocked(url)


async def find_area(pg, crag: dict) -> str | None:
    html = await fetch(pg, f"{BASE}/en/climbing/world/search?only=areas&S={crag['name'].split('/')[0].split(',')[0].strip()}")
    best = None
    Path("/tmp/tc/last_search.html").write_text(html)
    # Tarjetas de resultados: <a class="card card-crag…" href="/en/climbing/spain/…"> … node-summary__name <b>Nombre</b>
    cards = re.findall(r'<a class="card[^"]*" data-nid="\d+" href="(/en/climbing/spain/[^"#?]+)">.*?node-summary__name[^>]*>\s*<b>\s*(.*?)</b>', html, re.S)
    for href, label in cards:
        label = re.sub(r"<[^>]+>", "", label).strip()
        parts = [crag["name"]] + re.split(r"[/,(&:-]| y ", crag["name"])
        sim = max(difflib.SequenceMatcher(None, norm(label), norm(x)).ratio() for x in parts if norm(x))
        if sim >= 0.6 and (best is None or sim > best[0] or (sim == best[0] and href.count("/") < best[1].count("/"))):
            best = (sim, href)
    return BASE + best[1] if best else None


def matches(label: str, sectors: list[str]) -> bool:
    n = norm(label)
    return bool(n) and any(difflib.SequenceMatcher(None, n, norm(x)).ratio() >= 0.8 for x in sectors if norm(x))


async def crawl(pg, root: str, out: list[dict], sectors: list[str]) -> list[dict]:
    """Recorre la zona de la escuela: siempre la raíz y sus hijas directas (suelen ser grupos de sectores); más abajo,
    solo las zonas cuyo nombre se parece a un sector de 8a.nu. Cada nodo guarda su padre, para heredar orientación."""
    seen, queue = set(), [(root, 0, None, "")]
    prefix = re.sub(r"/area/\d+$", "", root[len(BASE):])
    while queue and len(out) < MAX_PAGES:
        url, depth, parent, label = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        html = await fetch(pg, url)
        node = {**parse(html), "url": url, "depth": depth, "parent": parent, "label": label}
        out.append(node)
        if depth >= 4:
            continue
        for href, text in re.findall(rf'<a[^>]+href="({re.escape(prefix)}(?:/[a-z0-9-]+)*/area/\d+)"[^>]*>(.*?)</a>', html, re.S):
            text = re.sub(r"<[^>]+>|\s+", " ", text).strip()
            if BASE + href in seen or not text:
                continue
            if depth == 0 or matches(text, sectors):
                queue.append((BASE + href, depth + 1, node["id"], text))
    return out


async def main() -> None:
    global SECTORS
    crags = json.load(open(sys.argv[1]))
    # Sectores de 8a.nu por escuela ({key: [{"name", "slug"}]}), junto a crags.json: sectors.json
    sf = Path(sys.argv[1]).with_name("sectors.json")
    SECTORS = {k: [x["name"] for x in v if x.get("name")] for k, v in json.load(open(sf)).items()} if sf.exists() else {}
    outdir = Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch_persistent_context(
            str(PROFILE), channel="chrome", headless=True, args=["--disable-blink-features=AutomationControlled"],
            user_agent=UA, locale="es-ES", viewport={"width": 1366, "height": 850})
        pg = b.pages[0] if b.pages else await b.new_page()
        for c in crags:
            dest = outdir / (c["key"].split("/")[-1] + ".json")
            if dest.exists():
                continue
            area, partial = None, []
            try:
                area = await find_area(pg, c)
                nodes = await crawl(pg, area, partial, SECTORS.get(c["key"], [])) if area else []
            except Blocked as e:
                # Se guarda lo que haya (cuenta como hecha) y se sigue con la siguiente tras 15 min de descanso.
                print(f"{c['key']}: theCrag bloquea ({e}); guardo {len(partial)} zonas y descanso 15 min", flush=True)
                nodes = partial
                await asyncio.sleep(900)
                if not nodes:  # ni siquiera se encontró la zona: no se marca como hecha, se reintenta en otra pasada
                    continue
            dest.write_text(json.dumps({"crag": c["key"], "url": area, "nodes": nodes}, ensure_ascii=False))
            with_aspect = sum(1 for n in nodes if n["aspect"] and any(n["aspect"].values()) and not n["inherited"])
            print(f"{c['key']}: {area} · {len(nodes)} zonas, {with_aspect} con orientación", flush=True)
        await b.close()


asyncio.run(main())
