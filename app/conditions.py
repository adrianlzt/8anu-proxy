"""Condiciones para escalar en una escuela: tiempo, sol en la pared, puntuación por horas y mejor ventana.

Fuentes: la escuela (coordenadas, sectores, «rainSafety») de la API pública de 8a.nu; previsión horaria de
Open-Meteo (5 días pasados + 7 de previsión); orientación de la pared de lo que haya dicho el usuario («Know this
crag?») o, si no, estimada con el relieve (elevaciones de Open-Meteo alrededor de la escuela). El sol en la pared se
calcula con la posición del sol (NOAA) y esa orientación.

La puntuación (v1) son reglas, no un modelo ajustado: temperatura de la roca respecto al rango ideal del usuario,
humedad, roca mojada (balance de lluvia y secado hora a hora), viento, sol en la pared y noche.
"""

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

OPEN_METEO = "https://api.open-meteo.com/v1"
HOURLY = (
    "temperature_2m,relative_humidity_2m,precipitation,precipitation_probability,weather_code,cloud_cover,"
    "wind_speed_10m,wind_direction_10m,is_day,shortwave_radiation"
)
WEATHER_TTL = 30 * 60
CRAG_TTL = 24 * 3600

CATEGORIES = [(80, "great"), (65, "good"), (45, "ok"), (0, "bad")]
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
COMPASS8 = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


def compass(deg: float | None) -> str:
    if deg is None:
        return ""
    return COMPASS[round(deg % 360 / 22.5) % 16]


def category(score: float) -> str:
    return next(name for limit, name in CATEGORIES if score >= limit)


# --- Sol ---------------------------------------------------------------------------------------------------------


def sun_position(when_utc: datetime, lat: float, lon: float) -> tuple[float, float]:
    """(elevación, azimut) en grados, azimut desde el norte en sentido horario (algoritmo de NOAA)."""
    doy = when_utc.timetuple().tm_yday
    hour = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )  # fmt: skip
    decl = (
        0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g)
    )  # fmt: skip
    tst = hour * 60 + eqtime + 4 * lon
    ha = math.radians(tst / 4 - 180)
    phi = math.radians(lat)
    cos_zen = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    zen = math.acos(max(-1.0, min(1.0, cos_zen)))
    az = math.degrees(math.atan2(math.sin(ha), math.cos(ha) * math.sin(phi) - math.tan(decl) * math.cos(phi))) + 180
    return 90 - math.degrees(zen), az % 360


def sun_on_wall(elev: float, az: float, aspect: float) -> bool:
    """Sol directo en una pared vertical orientada a `aspect`: el sol por encima del horizonte (con un margen por el
    relieve) y por delante de la pared (incidencia de al menos ~5°)."""
    if elev < 3:
        return False
    return math.cos(math.radians(elev)) * math.cos(math.radians(az - aspect)) > 0.09


def sun_intervals(day: datetime, utc_offset: int, lat: float, lon: float, aspect: float, step_min: int = 5):
    """Tramos (inicio, fin) en minutos locales del día con sol en la pared, y los de luz de día."""
    sun, light = [], []
    start = day - timedelta(seconds=utc_offset)
    cur_sun = cur_light = None
    for m in range(0, 24 * 60 + step_min, step_min):
        el, az = sun_position(start + timedelta(minutes=m), lat, lon)
        on = m < 24 * 60 and sun_on_wall(el, az, aspect)
        lit = m < 24 * 60 and el > -0.83
        if on and cur_sun is None:
            cur_sun = m
        if not on and cur_sun is not None:
            sun.append((cur_sun, m))
            cur_sun = None
        if lit and cur_light is None:
            cur_light = m
        if not lit and cur_light is not None:
            light.append((cur_light, m))
            cur_light = None
    return sun, light


def wall_sun(day: datetime, utc_offset: int, lat: float, lon: float, aspect: float, spread: float = 15):
    """Sol en la pared con incertidumbre: lo seguro (en las tres orientaciones) y lo posible (en alguna)."""
    main, light = sun_intervals(day, utc_offset, lat, lon, aspect)
    lo, _ = sun_intervals(day, utc_offset, lat, lon, aspect - spread)
    hi, _ = sun_intervals(day, utc_offset, lat, lon, aspect + spread)
    alls = main + lo + hi
    if not alls:
        return {"sun": [], "possible": [], "daylight": light, "margin": 0}
    first, last = min(a for a, _ in alls), max(b for _, b in alls)
    margin = 0
    if main:
        margin = max(main[0][0] - first, last - main[-1][1])
    return {"sun": main, "possible": [(first, last)], "daylight": light, "margin": round(margin / 10) * 10}


def hourly_sun_fraction(sun: list[tuple[int, int]], hour: int) -> float:
    a, b = hour * 60, hour * 60 + 60
    return sum(max(0, min(b, e) - max(a, s)) for s, e in sun) / 60


# --- Orientación por el relieve ----------------------------------------------------------------------------------


RAY_DISTANCES = (60, 120, 200, 300)


def terrain_rays(lat: float, lon: float) -> list[tuple[float, float, float, float]]:
    """El punto de la escuela y 16 rayos (cada 22,5°) a 60, 120, 200 y 300 m: (rumbo, distancia, lat, lon)."""
    pts = [(0.0, 0.0, lat, lon)]
    for k in range(16):
        b = math.radians(k * 22.5)
        for d in RAY_DISTANCES:
            pts.append((k * 22.5, d, lat + d * math.cos(b) / 111320, lon + d * math.sin(b) / (111320 * math.cos(math.radians(lat)))))
    return pts


def rays_aspect(pts: list[tuple[float, float, float, float]], elevations: list[float]) -> tuple[float, float]:
    """La pared mira al lado contrario de donde más sube el terreno: suma vectorial de la subida máxima (pendiente)
    en cada rumbo → (orientación, pendiente media de subida en grados)."""
    z0 = elevations[0]
    vx = vy = 0.0
    best = 0.0
    for k in range(16):
        rises = [(elevations[1 + k * 4 + i] - z0) / d for i, d in enumerate(RAY_DISTANCES)]
        r = max(0.0, max(rises))
        b = math.radians(k * 22.5)
        vx += r * math.sin(b)
        vy += r * math.cos(b)
        best = max(best, r)
    uphill = math.degrees(math.atan2(vx, vy))
    return (uphill + 180) % 360, math.degrees(math.atan(best))


def terrain_grid(lat: float, lon: float, step_m: float = 90, n: int = 5):
    half = n // 2
    pts = []
    for j in range(-half, half + 1):
        for i in range(-half, half + 1):
            pts.append((i * step_m, j * step_m, lat + j * step_m / 111320, lon + i * step_m / (111320 * math.cos(math.radians(lat)))))
    return pts


def fit_aspect(points: list[tuple[float, float]], elevations: list[float]) -> tuple[float, float]:
    """Plano z = a + b·x + c·y por mínimos cuadrados (x al este, y al norte, en metros) → (orientación, pendiente)."""
    n = len(points)
    sx = sum(p[0] for p in points) / n
    sy = sum(p[1] for p in points) / n
    sz = sum(elevations) / n
    sxx = sum((p[0] - sx) ** 2 for p in points)
    syy = sum((p[1] - sy) ** 2 for p in points)
    sxy = sum((p[0] - sx) * (p[1] - sy) for p in points)
    sxz = sum((p[0] - sx) * (z - sz) for p, z in zip(points, elevations))
    syz = sum((p[1] - sy) * (z - sz) for p, z in zip(points, elevations))
    det = sxx * syy - sxy**2
    b = (sxz * syy - syz * sxy) / det
    c = (syz * sxx - sxz * sxy) / det
    aspect = math.degrees(math.atan2(-b, -c)) % 360  # hacia donde baja el terreno = hacia donde mira la pared
    slope = math.degrees(math.atan(math.hypot(b, c)))
    return aspect, slope


# --- Puntuación ----------------------------------------------------------------------------------------------------


@dataclass
class Prefs:
    tmin: float = 12
    tmax: float = 22


def temp_score(t: float, p: Prefs) -> float:
    if p.tmin <= t <= p.tmax:
        return 1.0
    d = p.tmin - t if t < p.tmin else t - p.tmax
    return max(0.0, 1 - d / 9)


def humidity_score(rh: float) -> float:
    """Fricción: perfecta por debajo del 55 %, ~0,75 al 85 %, ~0,55 al 98 %."""
    if rh <= 55:
        return 1.0
    return max(0.5, 1 - (rh - 55) / 30 * 0.25 - max(0, rh - 85) / 13 * 0.2)


def wind_score(w: float) -> float:
    if w <= 20:
        return 1.0
    return max(0.3, 1 - (w - 20) / 35)


def rock_temperature(t: float, sun_frac: float, cloud: float, wind: float) -> float:
    """Temperatura «sentida» en la roca: el sol directo la calienta, el viento la enfría."""
    return t + sun_frac * (1 - cloud / 100) * 7 - min(wind, 40) / 10


def score_hours(hours: list[dict], p: Prefs, rain_safe: bool) -> None:
    """Añade a cada hora: rock_t, wet (0…), rock (dry/damp/wet), score (0–100) y cat."""
    wet = 0.0
    for h in hours:
        rain = h["precip"] or 0
        dry_rate = 0.06 + 0.25 * h["sun"] * (1 - h["cloud"] / 100) + min(h["wind"], 30) / 30 * 0.12
        dry_rate += max(0, 1 - h["rh"] / 100) * 0.25
        wet = max(0.0, wet + rain * (0.35 if rain_safe else 1.0) - dry_rate)
        wet = min(wet, 6)
        h["wet"] = round(wet, 2)
        h["rock"] = "wet" if wet > 0.6 else "damp" if wet > 0.15 else "dry"
        rock_t = rock_temperature(h["temp"], h["sun"], h["cloud"], h["wind"])
        h["rock_t"] = round(rock_t, 1)
        s = 100 * temp_score(rock_t, p) * humidity_score(h["rh"]) * wind_score(h["wind"])
        if h["rock"] == "wet":
            s = min(s, 20)
        elif h["rock"] == "damp":
            s *= 0.65
        if rain >= 0.1:
            s = min(s, 35 if rain_safe else 15)
        h["score"] = round(s)
        h["cat"] = "night" if not h["day"] else category(s)


def best_window(hours: list[dict], length: int = 3) -> tuple[int, int, float] | None:
    """Mejor ventana de `length` horas seguidas de día: (índice inicio, índice fin exclusivo, puntuación media)."""
    best = None
    for i in range(len(hours) - length + 1):
        win = hours[i : i + length]
        if not all(h["day"] for h in win):
            continue
        mean = sum(h["score"] for h in win) / length
        if best is None or mean > best[2] + 0.5:
            best = (i, i + length, mean)
    return best


# --- Resumen ---------------------------------------------------------------------------------------------------------


def _rock_text(day_hours: list[dict]) -> str:
    lit = [h for h in day_hours if h["day"]] or day_hours
    states = [h["rock"] for h in lit]
    if all(s == "dry" for s in states):
        return "Rock dry"
    if all(s != "dry" for s in states):
        return "Rock wet" if any(s == "wet" for s in states) else "Rock damp"
    first_dry = next(i for i, s in enumerate(states) if s == "dry")
    if all(s == "dry" for s in states[first_dry:]):
        return f"Dry after {lit[first_dry]['time'][11:16]}"
    first_wet = next(i for i, s in enumerate(states) if s != "dry")
    return f"Damp from {lit[first_wet]['time'][11:16]}"


def _showers(day_hours: list[dict]) -> dict:
    wet = [h for h in day_hours if (h["precip"] or 0) >= 0.1]
    prob = max((h["prob"] or 0) for h in day_hours)
    out = {"mm": round(sum(h["precip"] or 0 for h in day_hours), 1), "prob": prob}
    if wet:
        out["from"] = wet[0]["time"][11:16]
        out["to"] = f"{int(wet[-1]['time'][11:13]) + 1:02d}:00"
    return out


def summarize_day(date: str, day_hours: list[dict], sun: dict) -> dict:
    win = best_window(day_hours)
    winds = [h["wind"] for h in day_hours if h["day"]] or [h["wind"] for h in day_hours]
    dirs = [h["wind_dir"] for h in day_hours if h["day"]] or [h["wind_dir"] for h in day_hours]
    out = {
        "date": date,
        "tmax": round(max(h["temp"] for h in day_hours)),
        "tmin": round(min(h["temp"] for h in day_hours)),
        "wind": round(sum(winds) / len(winds)),
        "wind_from": compass(dirs[0]),
        "wind_to": compass(dirs[-1]),
        "rh": round(sum(h["rh"] for h in day_hours) / len(day_hours)),
        "code": max(h["code"] or 0 for h in day_hours if h["day"]) if any(h["day"] for h in day_hours) else 0,
        "cloud": round(sum(h["cloud"] for h in day_hours if h["day"]) / max(1, sum(1 for h in day_hours if h["day"]))),
        "rain": _showers(day_hours),
        "rock": _rock_text(day_hours),
        "sun": sun,
    }
    if win and win[2] >= 40:
        a, b, mean = win
        out["window"] = {"from": day_hours[a]["time"][11:16], "to": f"{int(day_hours[b - 1]['time'][11:13]) + 1:02d}:00"}
        out["cat"] = category(mean)
        out["score"] = round(mean)
    else:
        out["cat"] = "bad"
        out["score"] = round(win[2]) if win else 0
    return out


def explain(days: list[dict], hours: list[dict], now_idx: int, sel: int, p: Prefs) -> dict:
    """«Why these conditions?» para el día `sel`."""
    d = days[sel]
    dh = [h for h in hours if h["time"].startswith(d["date"])]
    win_hours = [h for h in dh if d.get("window") and d["window"]["from"] <= h["time"][11:16] < d["window"]["to"]]
    t_win = round(sum(h["rock_t"] for h in win_hours) / len(win_hours)) if win_hours else None
    t_high = round(max(h["rock_t"] for h in dh))
    ref = t_win if t_win is not None else t_high
    temp_state = "In your range" if p.tmin <= ref <= p.tmax else ("Too cold" if ref < p.tmin else "Too hot")
    if temp_state != "In your range" and abs(ref - (p.tmin if ref < p.tmin else p.tmax)) <= 2:
        temp_state = "Close to your range"
    now = hours[now_idx]
    last6 = hours[max(0, now_idx - 5) : now_idx + 1]
    clear = sum(1 for h in dh if h["day"] and h["cloud"] < 35)
    cloud = d["cloud"]
    sky = "Sunny" if cloud < 25 else "Partly cloudy" if cloud < 65 else "Overcast"
    since = None
    for i in range(now_idx, -1, -1):
        if (hours[i]["precip"] or 0) >= 0.2:
            since = now_idx - i
            break
    mm3 = round(sum(h["precip"] or 0 for h in hours[max(0, now_idx - 72) : now_idx + 1]), 1)
    if since == 0:
        last_rain = "Raining now"
    elif since is None:
        last_rain = "No rain in 5 days"
    elif since < 24:
        last_rain = f"{since} h ago"
    else:
        last_rain = f"{since // 24} days ago"
    rock_state = {"dry": "Dry", "damp": "Damp", "wet": "Wet"}[now["rock"]]
    return {
        "temperature": {
            "state": temp_state,
            "window": t_win,
            "high": t_high,
            "tuned": [p.tmin, p.tmax],
        },
        "rock": {"state": rock_state, "rh_now": now["rh"], "rh_6h": round(sum(h["rh"] for h in last6) / len(last6))},
        "sun": {"state": sky, "clear_hours": clear},
        "rain": {"state": last_rain, "mm_3d": mm3},
    }


# --- Orquestación ----------------------------------------------------------------------------------------------------


class Cache:
    def __init__(self):
        self.data: dict = {}

    def get(self, key, ttl):
        v = self.data.get(key)
        if v and time.time() - v[0] < ttl:
            return v[1]
        return None

    def put(self, key, value):
        self.data[key] = (time.time(), value)


def parse_weather(w: dict) -> list[dict]:
    h = w["hourly"]
    out = []
    for i, t in enumerate(h["time"]):
        out.append(
            {
                "time": t,
                "temp": h["temperature_2m"][i],
                "rh": h["relative_humidity_2m"][i],
                "precip": h["precipitation"][i],
                "prob": h["precipitation_probability"][i],
                "code": h["weather_code"][i],
                "cloud": h["cloud_cover"][i],
                "wind": h["wind_speed_10m"][i],
                "wind_dir": h["wind_direction_10m"][i],
                "day": bool(h["is_day"][i]),
                "rad": h["shortwave_radiation"][i],
            }
        )
    return [x for x in out if x["temp"] is not None]


def build(crag: dict, sectors: list[dict], weather: dict, aspect: dict, p: Prefs, now: datetime | None = None) -> dict:
    lat, lon = crag["location"]["latitude"], crag["location"]["longitude"]
    offset = weather.get("utc_offset_seconds", 0)
    hours = parse_weather(weather)
    local_now = (now or datetime.utcnow()) + timedelta(seconds=offset)
    now_key = local_now.strftime("%Y-%m-%dT%H:00")
    now_idx = next((i for i, h in enumerate(hours) if h["time"] == now_key), 0)

    dates = sorted({h["time"][:10] for h in hours})
    suns = {}
    for d in dates:
        suns[d] = wall_sun(datetime.fromisoformat(d), offset, lat, lon, aspect["deg"])
    for h in hours:
        h["sun"] = round(hourly_sun_fraction(suns[h["time"][:10]]["sun"], int(h["time"][11:13])), 2)
    score_hours(hours, p, bool(crag.get("rainSafety")))

    days = []
    for d in dates:
        dh = [h for h in hours if h["time"].startswith(d)]
        if len(dh) < 24:
            continue
        days.append(summarize_day(d, dh, suns[d]))
    today = local_now.strftime("%Y-%m-%d")
    today_idx = next((i for i, d in enumerate(days) if d["date"] == today), 0)
    cur = hours[now_idx]
    sun_now = hourly_sun_fraction(suns[cur["time"][:10]]["sun"], local_now.hour) > 0 and cur["cloud"] < 80
    state = "Dark" if not cur["day"] else ("Sun" if sun_now else "Shade")
    return {
        "crag": {
            "name": crag["cragName"],
            "area": crag.get("areaName"),
            "lat": lat,
            "lon": lon,
            "sectors": [{"name": s["sectorName"], "slug": s["sectorSlug"]} for s in sectors],
            "rain_safe": bool(crag.get("rainSafety")),
            "season": crag.get("season"),
        },
        "aspect": aspect,
        "prefs": {"tmin": p.tmin, "tmax": p.tmax},
        "now": {
            "time": cur["time"],
            "index": now_idx,
            "temp": round(cur["temp"]),
            "rh": cur["rh"],
            "wind": round(cur["wind"]),
            "rock": cur["rock"],
            "state": state,
            "cat": cur["cat"],
        },
        "fetched": weather.get("_fetched"),
        "today": today_idx,
        "days": days,
        "hours": hours,
        "why": [explain(days, hours, now_idx if i == today_idx else _noon(hours, d["date"]), i, p) for i, d in enumerate(days)],
    }


def _noon(hours: list[dict], date: str) -> int:
    return next((i for i, h in enumerate(hours) if h["time"] == f"{date}T13:00"), 0)
