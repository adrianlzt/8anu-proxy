import json, statistics as st, math
R = {r["a8"]["key"]: r for r in json.load(open("cmp.json"))}; S = json.load(open("ss_local.json"))
rows = []
for k, d in S.items():
    o = R[k]["ours"]
    for h in o["hours"]:
        date, hr = h["time"][:10], int(h["time"][11:13])
        if date < "2026-11-19" or date not in d["sendscores_hourly"] or not h["day"]: continue
        ss = d["sendscores_hourly"][date][hr]
        rows.append(dict(k=k.split("/")[-1], date=date, hr=hr, us=h["score"], ss=ss, t=h["temp"], rh=h["rh"], w=h["wind"], g=h["gust"], ew=h["eff_wind"], mm=h["precip"], rock=h["rock"], comfort=h["comfort"], irr=h["irr"], cloud=h["cloud"], band=d["rain_bands_hourly"][date][hr]))
json.dump(rows, open("rows.json", "w"))
def corr(a, b):
    ma, mb = st.mean(a), st.mean(b); return sum((x-ma)*(y-mb) for x, y in zip(a, b)) / math.sqrt(sum((x-ma)**2 for x in a) * sum((y-mb)**2 for y in b))
print("n", len(rows), "corr", round(corr([r["us"] for r in rows], [r["ss"] for r in rows]), 2), "mean us", round(st.mean(r["us"] for r in rows)), "ss", round(st.mean(r["ss"] for r in rows)))
dry = [r for r in rows if r["rock"] == "dry" and r["mm"] == 0]
print("dry hours", len(dry), "mean us", round(st.mean(r["us"] for r in dry)), "ss", round(st.mean(r["ss"] for r in dry)))
for var, edges in [("t", [0, 8, 11, 14, 17, 20, 30]), ("w", [0, 8, 15, 22, 30, 60]), ("ew", [0, 5, 10, 15, 20, 40]), ("rh", [0, 50, 65, 80, 90, 101]), ("cloud", [0, 30, 70, 101]), ("irr", [0, 1, 200, 500, 2000])]:
    print(var)
    for a, b in zip(edges, edges[1:]):
        x = [r for r in dry if a <= r[var] < b]
        if x: print(f"  {a}-{b}: n={len(x):4} us={st.mean(r['us'] for r in x):5.1f} ss={st.mean(r['ss'] for r in x):5.1f}")
