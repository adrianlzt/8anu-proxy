"""Orientación de escuelas y sectores en theCrag, navegando despacio con Chrome (Playwright) desde arc2.

    uv run -q --with playwright python tools/thecrag/scrape.py crags.json salida/ [--cuenta internet/thecrag] [--paginas 20]

Con --cuenta entra con la cuenta de Adrián (usuario y contraseña de esa entrada de gopass, sin imprimirlos) en un perfil
propio (~/.cache/tc-adrian-profile): sin sesión theCrag manda casi todo al «swamp» (403). Con sesión hay un cupo de
unas 20-50 páginas por cuenta cada 13 h (429 «Your session has ended»), compartido con lo que él mire en theCrag, así
que cada tanda gasta como mucho --paginas y para al primer 429/swamp. Una escuela a medias se guarda en
salida/<slug>.partial.json (cola y nodos) y la siguiente tanda sigue por ahí. Las escuelas van por «mine» (sus
encadenes) de mayor a menor. salida/status.json resume la última tanda.

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
import time
import unicodedata
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "https://www.thecrag.com"
PAUSE = 15.0  # lo que aguanta el WAF compartido con thecrag-proxy (los croquis de Adrián)
MAX_PAGES = 15
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
PROFILE = Path.home() / ".cache" / "tc-clearance-profile"  # receta de ~/Documentos/escalada/thecrag-access


SECTORS: dict[str, list[str]] = {}


class Blocked(Exception):
    pass


class Quota(Exception):
    """Cupo de la tanda gastado, o theCrag corta la sesión (429/swamp): se para hasta la siguiente tanda."""


BUDGET = {"left": 10**9, "used": 0}


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
        if BUDGET["left"] <= 0:
            raise Quota("cupo de la tanda gastado")
        await asyncio.sleep(PAUSE + random.random() * 2)
        r = await pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        BUDGET["left"] -= 1
        BUDGET["used"] += 1
        html = await pg.content()
        if (r and r.status == 429) or "swamp" in pg.url or "throttled" in pg.url or "Your session has ended" in html[:20000]:
            raise Quota(f"theCrag corta: {r.status if r else '?'} {pg.url[:80]}")
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


async def crawl(pg, root: str, out: list[dict], sectors: list[str], state: dict | None = None) -> list[dict]:
    """Recorre la zona de la escuela: siempre la raíz y sus hijas directas (suelen ser grupos de sectores); más abajo,
    solo las zonas cuyo nombre se parece a un sector de 8a.nu. Cada nodo guarda su padre, para heredar orientación.
    state ({"seen", "queue"}) se actualiza en el sitio para poder seguir otra vez si se corta."""
    state = state if state is not None else {}
    state.setdefault("seen", [])
    state.setdefault("queue", [[root, 0, None, ""]])
    seen, queue = state["seen"], state["queue"]
    prefix = re.sub(r"/area/\d+$", "", root[len(BASE):])
    while queue and len(out) < MAX_PAGES:
        url, depth, parent, label = queue[0]
        if url in seen:
            queue.pop(0)
            continue
        html = await fetch(pg, url)
        queue.pop(0)
        seen.append(url)
        node = {**parse(html), "url": url, "depth": depth, "parent": parent, "label": label}
        out.append(node)
        if depth >= 4:
            continue
        for href, text in re.findall(rf'<a[^>]+href="({re.escape(prefix)}(?:/[a-z0-9-]+)*/area/\d+)"[^>]*>(.*?)</a>', html, re.S):
            text = re.sub(r"<[^>]+>|\s+", " ", text).strip()
            if BASE + href in seen or not text:
                continue
            if depth == 0 or matches(text, sectors):
                queue.append([BASE + href, depth + 1, node["id"], text])
    return out


def secret(entry: str, field: str | None = None) -> str:
    import subprocess
    args = ["gopass", "show"] + ([entry, field] if field else ["-o", entry])
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()


async def login(pg, entry: str) -> None:
    """Entra con la cuenta de la entrada de gopass si el perfil no tiene ya sesión. No imprime nada de la cuenta."""
    await pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
    BUDGET["used"] += 1
    if await pg.evaluate("() => !!(document.querySelector('#loggedInAccount') || {}).value"):
        return
    await asyncio.sleep(PAUSE)
    await pg.goto(BASE + "/processmap/login", wait_until="domcontentloaded", timeout=60000)
    await pg.fill("#loginInputLogin", secret(entry, "user"))
    await pg.fill("#loginInputPassword", secret(entry))
    await pg.click("#btnLogin")
    await pg.wait_for_load_state("domcontentloaded")
    await asyncio.sleep(3)
    if "acceptterms" in pg.url:
        raise Quota("theCrag pide aceptar los términos de uso: que entre Adrián una vez a mano")
    if not await pg.evaluate("() => !!(document.querySelector('#loggedInAccount') || {}).value"):
        raise Quota(f"no ha entrado la sesión ({pg.url[:80]})")


async def main() -> None:
    global SECTORS
    argv = sys.argv[1:]
    opt = {}
    for k in ("--cuenta", "--paginas"):
        if k in argv:
            i = argv.index(k)
            opt[k] = argv[i + 1]
            del argv[i:i + 2]
    crags = sorted(json.load(open(argv[0])), key=lambda c: -(c.get("mine") or 0))
    # Sectores de 8a.nu por escuela ({key: [{"name", "slug"}]}), junto a crags.json: sectors.json
    sf = Path(argv[0]).with_name("sectors.json")
    SECTORS = {k: [x["name"] for x in v if x.get("name")] for k, v in json.load(open(sf)).items()} if sf.exists() else {}
    outdir = Path(argv[1])
    outdir.mkdir(parents=True, exist_ok=True)
    if "--paginas" in opt:
        BUDGET["left"] = int(opt["--paginas"])
    # Un perfil por cuenta: si no, login() vería la sesión de otra y seguiría con ella.
    cuenta = opt.get("--cuenta")
    profile = PROFILE if not cuenta else Path.home() / ".cache" / (
        "tc-adrian-profile" if cuenta == "internet/thecrag" else "tc-profile-" + re.sub(r"[^a-z0-9]+", "-", cuenta.lower()))
    status = {"inicio": time.strftime("%Y-%m-%d %H:%M"), "hechas": [], "motivo": "terminado"}
    async with async_playwright() as p:
        b = await p.chromium.launch_persistent_context(
            str(profile), channel="chrome", headless=True, args=["--disable-blink-features=AutomationControlled"],
            user_agent=UA, locale="es-ES", viewport={"width": 1366, "height": 850})
        pg = b.pages[0] if b.pages else await b.new_page()
        try:
            if "--cuenta" in opt:
                await login(pg, opt["--cuenta"])
            for c in crags:
                dest = outdir / (c["key"].split("/")[-1] + ".json")
                part = dest.with_suffix(".partial.json")
                if dest.exists():
                    continue
                st = json.load(open(part)) if part.exists() else {"area": None, "nodes": [], "state": None, "searched": False}
                try:
                    if not st["searched"]:
                        st["area"] = await find_area(pg, c)
                        st["searched"] = True
                    if st["area"]:
                        st["state"] = st["state"] or {}
                        await crawl(pg, st["area"], st["nodes"], SECTORS.get(c["key"], []), st["state"])
                except (Quota, Blocked) as e:
                    part.write_text(json.dumps(st, ensure_ascii=False))
                    status["motivo"] = f"{type(e).__name__}: {e}"
                    print(f"{c['key']}: paro ({e}); {len(st['nodes'])} zonas guardadas a medias", flush=True)
                    break
                nodes = st["nodes"]
                dest.write_text(json.dumps({"crag": c["key"], "url": st["area"], "nodes": nodes}, ensure_ascii=False))
                part.unlink(missing_ok=True)
                status["hechas"].append(c["key"])
                with_aspect = sum(1 for n in nodes if n["aspect"] and any(n["aspect"].values()) and not n["inherited"])
                print(f"{c['key']}: {st['area']} · {len(nodes)} zonas, {with_aspect} con orientación", flush=True)
        except Quota as e:
            status["motivo"] = f"Quota: {e}"
            print(f"paro: {e}", flush=True)
        await b.close()
    status["paginas"] = BUDGET["used"]
    status["fin"] = time.strftime("%Y-%m-%d %H:%M")
    status["quedan"] = sum(1 for c in crags if not (outdir / (c["key"].split("/")[-1] + ".json")).exists())
    (outdir / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1))
    print(json.dumps(status, ensure_ascii=False), flush=True)


asyncio.run(main())
