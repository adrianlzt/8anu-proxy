import json, urllib.request, concurrent.futures as cf
pairs = json.load(open("pairs.json"))
seen, uniq = set(), []
for p in sorted(pairs, key=lambda p: p["km"]):
    if p["a8"]["key"] in seen: continue
    seen.add(p["a8"]["key"]); uniq.append(p)
uniq.sort(key=lambda p: -p["a8"]["ascents"]); uniq = uniq[:70]
def get(p):
    k = p["a8"]["key"]
    try:
        o = json.load(urllib.request.urlopen(f"http://127.0.0.1:8472/_proxy/conditions/{k}/data?tmin=12&tmax=22", timeout=120))
    except Exception as e:
        o = {"error": str(e)}
    try:
        c = json.load(urllib.request.urlopen(f"http://127.0.0.1:8472/api/unification/outdoor/v1/web/crags/{k}", timeout=60))["crag"].get("conditionsSummary")
    except Exception as e:
        c = None
    return {**p, "ours": o, "a8cond": c}
with cf.ThreadPoolExecutor(4) as ex: res = list(ex.map(get, uniq))
json.dump(res, open("cmp.json", "w")); print(len(res), sum("error" in r["ours"] for r in res))
