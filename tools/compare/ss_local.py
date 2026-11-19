import json, urllib.request, time
R = json.load(open("cmp.json")); out = {}
for r in R:
    if "error" in r["ours"]: continue
    s = r["ss"]
    u = f"https://www.sendscore.info/api/local_crags?ref_latitude={s['lat']}&ref_longitude={s['lon']}&limit=1"
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=60))[0]
        if d["id"] == s["id"]: out[r["a8"]["key"]] = d
    except Exception as e: print(e)
    time.sleep(0.7)
json.dump(out, open("ss_local.json", "w")); print(len(out))
