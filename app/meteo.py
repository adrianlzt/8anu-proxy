"""Meteorología detallada de una escuela (sección plegada de la página de condiciones, se pide solo al abrirla).

- Meteograma de 72 h con variables que el modelo principal no enseña: nubes por capas, punto de rocío, CAPE e índice
  de elevación (tormentas), isocero, visibilidad, déficit de presión de vapor (lo que seca la roca), presión, rachas, UV.
- Incertidumbre: el ensemble de ECMWF (51 miembros) da la banda de temperatura p10–p90 y la probabilidad de lluvia de
  cada hora.
- Modelos: ECMWF IFS, ICON (DWD), GFS (NOAA), Météo-France (AROME 1,3 km donde llega, que incluye el norte de España;
  ARPEGE fuera) y UKMO. Resumen por día de cada uno (temperaturas, lluvia y viento de 8 a 20 h: medio, racha máxima y
  dirección dominante) y lo que se parecen.
- Avisos: tormentas, helada/nieve (isocero cerca de la altitud de la escuela), niebla, rachas fuertes, UV alto y calor.
Todo de Open-Meteo, que ya corrige la temperatura por la altitud del punto (modelo digital del terreno de 90 m).
"""

import math
import statistics
import time
from urllib.parse import urlencode

from . import conditions as cond

ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"
MODELS = {
    "ecmwf_ifs025": "ECMWF",
    "icon_seamless": "ICON",
    "gfs_seamless": "GFS",
    "meteofrance_seamless": "Météo-France",
    "ukmo_seamless": "UKMO",
}
DETAIL = (
    "temperature_2m,dew_point_2m,relative_humidity_2m,precipitation,precipitation_probability,cloud_cover_low,"
    "cloud_cover_mid,cloud_cover_high,cape,lifted_index,freezing_level_height,visibility,vapour_pressure_deficit,"
    "pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index,is_day,weather_code"
)
TTL = 30 * 60
HOURS = 72

_cache: dict[tuple, tuple[float, dict]] = {}


async def _get(app, url: str, params: dict) -> dict:
    r = await app.state.plain.get(f"{url}?{urlencode(params)}")
    if r.status_code != 200:
        raise RuntimeError(f"Open-Meteo {r.status_code}: {r.text[:200]}")
    return r.json()


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def ensemble_stats(hourly: dict) -> dict:
    """p10/p50/p90 de temperatura y probabilidad de lluvia (≥0,2 mm/h) y p90 de lluvia, hora a hora."""
    t_keys = [k for k in hourly if k.startswith("temperature_2m")]
    p_keys = [k for k in hourly if k.startswith("precipitation")]
    out = {"time": hourly["time"], "t10": [], "t50": [], "t90": [], "pp": [], "p90": []}
    for i in range(len(hourly["time"])):
        ts = [hourly[k][i] for k in t_keys if hourly[k][i] is not None]
        ps = [hourly[k][i] for k in p_keys if hourly[k][i] is not None]
        out["t10"].append(round(_pct(ts, 0.1), 1) if ts else None)
        out["t50"].append(round(_pct(ts, 0.5), 1) if ts else None)
        out["t90"].append(round(_pct(ts, 0.9), 1) if ts else None)
        out["pp"].append(round(100 * sum(p >= 0.2 for p in ps) / len(ps)) if ps else None)
        out["p90"].append(round(_pct(ps, 0.9), 1) if ps else None)
    out["members"] = len(t_keys)
    return out


def _wind(hourly: dict, key: str, idx: list[int]) -> dict:
    """Viento de un modelo en las horas de escalada (8–20 h): medio, racha máxima y dirección dominante (media
    vectorial pesada por la velocidad, de donde viene)."""
    sp = hourly.get(f"wind_speed_10m_{key}") or []
    gu = hourly.get(f"wind_gusts_10m_{key}") or []
    di = hourly.get(f"wind_direction_10m_{key}") or []
    pairs = [(sp[i], di[i]) for i in idx if i < len(sp) and i < len(di) and sp[i] is not None and di[i] is not None]
    gusts = [gu[i] for i in idx if i < len(gu) and gu[i] is not None]
    if not pairs:
        return {}
    vx = sum(v * math.sin(math.radians(d)) for v, d in pairs)
    vy = sum(v * math.cos(math.radians(d)) for v, d in pairs)
    return {
        "wind": round(sum(v for v, _ in pairs) / len(pairs)),
        "gust": round(max(gusts)) if gusts else None,
        "wind_dir": round(math.degrees(math.atan2(vx, vy)) % 360),
    }


def model_days(hourly: dict) -> dict:
    """Por modelo y día: máxima, mínima y lluvia. Y por día, cuánto se parecen (dispersión de máximas y de lluvia)."""
    times = hourly["time"]
    dates = sorted({t[:10] for t in times})
    per_model = {}
    for key, name in MODELS.items():
        temps, rains = hourly.get(f"temperature_2m_{key}"), hourly.get(f"precipitation_{key}")
        if not temps or all(v is None for v in temps):
            continue
        days = []
        for d in dates:
            idx = [i for i, t in enumerate(times) if t.startswith(d)]
            ts = [temps[i] for i in idx if temps[i] is not None]
            ps = [rains[i] for i in idx if rains and rains[i] is not None]
            if len(ts) < 20:
                days.append(None)
                continue
            day = {"tmax": round(max(ts)), "tmin": round(min(ts)), "rain": round(sum(ps), 1) if ps else None}
            day.update(_wind(hourly, key, [i for i in idx if 8 <= int(times[i][11:13]) < 20]))
            days.append(day)
        per_model[name] = days
    agreement = []
    for i in range(len(dates)):
        vals = [m[i] for m in per_model.values() if i < len(m) and m[i]]
        if len(vals) < 3:
            agreement.append(None)
            continue
        tspread = max(v["tmax"] for v in vals) - min(v["tmax"] for v in vals)
        rains = [v["rain"] for v in vals if v["rain"] is not None]
        wet = sum(r >= 1 for r in rains)
        rain_agree = wet == 0 or wet == len(rains)
        level = "high" if tspread <= 2 and rain_agree else "low" if tspread > 5 or (0 < wet < len(rains) / 2 + 1 and not rain_agree) else "medium"
        agreement.append({"level": level, "tspread": tspread, "wet_models": wet, "models": len(rains)})
    return {"dates": dates, "models": per_model, "agreement": agreement}


def risks(h: dict, elevation: float) -> list[dict]:
    """Avisos por día a partir del meteograma: tormentas, helada/nieve, niebla, rachas, UV y calor."""
    out = []
    dates = sorted({t[:10] for t in h["time"]})
    for d in dates:
        idx = [i for i, t in enumerate(h["time"]) if t.startswith(d)]
        get = lambda k: [h[k][i] for i in idx if h[k][i] is not None]  # noqa: E731
        hour = lambda i: h["time"][i][11:16]  # noqa: E731
        storm = [i for i in idx if (h["cape"][i] or 0) >= 800 and (h["lifted_index"][i] or 99) <= -2]
        if storm or any(c >= 95 for c in get("weather_code")):
            when = f"from {hour(storm[0])}" if storm else ""
            out.append({"date": d, "kind": "storm", "level": "high" if any((h["cape"][i] or 0) > 1500 for i in idx) else "medium",
                        "text": f"Thunderstorm risk {when} (CAPE up to {max(get('cape')):.0f} J/kg)"})  # fmt: skip
        fz = get("freezing_level_height")
        if fz and min(fz) < elevation + 300:
            low = min(fz)
            out.append({"date": d, "kind": "freeze", "level": "high" if low < elevation else "medium",
                        "text": f"Freezing level down to {low:.0f} m (crag at {elevation:.0f} m): ice or snow possible"})  # fmt: skip
        fog = [i for i in idx if (h["visibility"][i] if h["visibility"][i] is not None else 1e9) < 1000 and h["is_day"][i]]
        if fog:
            out.append({"date": d, "kind": "fog", "level": "medium",
                        "text": f"Fog or low cloud ({hour(fog[0]) + '–' + hour(fog[-1]) if len(fog) > 1 else 'around ' + hour(fog[0])}, visibility under 1 km): damp rock"})  # fmt: skip
        g = get("wind_gusts_10m")
        if g and max(g) >= 60:
            out.append({"date": d, "kind": "wind", "level": "high" if max(g) >= 80 else "medium",
                        "text": f"Gusts up to {max(g):.0f} km/h"})  # fmt: skip
        uv = get("uv_index")
        if uv and max(uv) >= 8:
            out.append({"date": d, "kind": "uv", "level": "medium", "text": f"Very high UV ({max(uv):.0f})"})
        t = get("temperature_2m")
        if t and max(t) >= 32:
            out.append({"date": d, "kind": "heat", "level": "high" if max(t) >= 36 else "medium",
                        "text": f"Heat: up to {max(t):.0f} °C"})  # fmt: skip
    return out


async def detail(app, key: str, lat: float, lon: float) -> dict:
    hit = _cache.get((key,))
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    base = {"latitude": lat, "longitude": lon, "timezone": "auto", "wind_speed_unit": "kmh"}
    main = await _get(app, f"{cond.OPEN_METEO}/forecast", {**base, "hourly": DETAIL, "forecast_days": 4})
    ens = await _get(app, ENSEMBLE, {**base, "hourly": "temperature_2m,precipitation", "models": "ecmwf_ifs025",
                                     "forecast_days": 4})  # fmt: skip
    multi = await _get(app, f"{cond.OPEN_METEO}/forecast", {**base, "hourly": "temperature_2m,precipitation,"
                                                            "wind_speed_10m,wind_gusts_10m,wind_direction_10m",
                                                            "models": ",".join(MODELS), "forecast_days": 7})  # fmt: skip
    h = main["hourly"]
    # 72 h desde la hora actual (local de la escuela).
    now = time.strftime("%Y-%m-%dT%H:00", time.gmtime(time.time() + main.get("utc_offset_seconds", 0)))
    start = next((i for i, t in enumerate(h["time"]) if t >= now), 0)
    sl = slice(start, start + HOURS)
    hours = {k: v[sl] for k, v in h.items()}
    e = ensemble_stats(ens["hourly"])
    e_idx = {t: i for i, t in enumerate(e["time"])}
    ens_cut = {k: [e[k][e_idx[t]] if t in e_idx else None for t in hours["time"]] for k in ("t10", "t50", "t90", "pp", "p90")}
    elevation = main.get("elevation") or 0
    vpd = [v for v in hours["vapour_pressure_deficit"][:24] if v is not None]
    out = {
        "elevation": elevation,
        "hours": hours,
        "ensemble": {**ens_cut, "members": e["members"]},
        "models": model_days(multi["hourly"]),
        "risks": risks(h, elevation),
        "summary": {
            "pressure_trend": round(hours["pressure_msl"][min(23, len(hours["pressure_msl"]) - 1)] - hours["pressure_msl"][0], 1),
            "vpd_24h": round(statistics.mean(vpd), 2) if vpd else None,
        },
    }
    _cache[(key,)] = (time.time(), out)
    return out
