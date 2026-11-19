import json, math, time, urllib.request, urllib.parse
def tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y
z = 8
x0, y0 = tile(44, -9.5, z); x1, y1 = tile(36, 3.5, z)
coords = [f"{x}/{y}" for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
out = {}
for i in range(0, len(coords), 20):
    q = urllib.parse.urlencode({"z": z, "tiles": ",".join(c.replace("/", "_") for c in coords[i:i+20])})
    req = urllib.request.Request(f"https://www.sendscore.info/api/crags_tiles_batch?{q}", headers={"User-Agent": "Mozilla/5.0"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=60))
    except Exception as e:
        print("err", e); break
    if i == 0: print(type(d), str(d)[:300])
    items = d.values() if isinstance(d, dict) else [d]
    for v in items:
        for c in (v if isinstance(v, list) else v.get("crags", []) if isinstance(v, dict) else []):
            out[c["id"]] = c
    time.sleep(1)
print(len(out)); json.dump(list(out.values()), open("ss_es.json", "w"))
