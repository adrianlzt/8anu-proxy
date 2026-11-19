import json, math, unicodedata, re, difflib
ss = json.load(open("ss_es.json")); a8 = json.load(open("8a_es.json"))
norm = lambda s: re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode())
def km(a, b): return 111 * math.hypot(a["lat"] - b["lat"], (a["lon"] - b["lon"]) * math.cos(math.radians(a["lat"])))
pairs = []
for s in ss:
    if s.get("discipline") != "Sportclimbing": continue
    best = None
    for c in a8:
        if not c["key"].startswith("sportclimbing/"): continue
        d = km(s, c)
        if d > 3: continue
        sim = difflib.SequenceMatcher(None, norm(s["name"]), norm(c["name"])).ratio()
        if sim >= 0.7 and (best is None or c["ascents"] > best[0]["ascents"]):
            best = (c, d, sim)
    if best: pairs.append({"ss": s, "a8": best[0], "km": round(best[1], 2)})
pairs.sort(key=lambda p: -p["a8"]["ascents"])
print(len(pairs))
for p in pairs[:40]: print(p["a8"]["key"], p["a8"]["ascents"], p["km"], p["ss"]["name"])
json.dump(pairs, open("pairs.json", "w"))
