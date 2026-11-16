"""Condiciones para escalar en una escuela: tiempo, sol en la pared, puntuación por horas y mejor ventana.

Fuentes: la escuela (coordenadas, sectores, «rainSafety») de la API pública de 8a.nu; previsión horaria de
Open-Meteo (5 días pasados + 7 de previsión); orientación de la pared de lo que haya dicho el usuario («Know this
crag?») o, si no, estimada con el relieve (elevaciones de Open-Meteo alrededor de la escuela). El sol en la pared se
calcula con la posición del sol (NOAA) y esa orientación.

La puntuación (v2, idea de SendScore) son reglas físicas aproximadas, no un modelo ajustado con ascensiones: ver
«Puntuación (v2)» más abajo.
"""

import functools
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

OPEN_METEO = "https://api.open-meteo.com/v1"
HOURLY = (
    "temperature_2m,relative_humidity_2m,dew_point_2m,precipitation,precipitation_probability,weather_code,"
    "cloud_cover,wind_speed_10m,wind_gusts_10m,wind_direction_10m,is_day,shortwave_radiation"
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


@functools.lru_cache(maxsize=4096)
def sun_track(day: str, utc_offset: int, lat: float, lon: float, step_min: int = 5) -> tuple:
    """(minuto local, elevación, azimut) cada `step_min` minutos de un día. Se cachea por día y lugar (redondeado a
    0,05°: el sol no cambia apreciablemente en 5 km), así las orientaciones y escuelas cercanas la comparten."""
    start = datetime.fromisoformat(day) - timedelta(seconds=utc_offset)
    return tuple(
        (m, *sun_position(start + timedelta(minutes=m), lat, lon)) for m in range(0, 24 * 60 + step_min, step_min)
    )


def sun_intervals(day: datetime, utc_offset: int, lat: float, lon: float, aspect: float, step_min: int = 5):
    """Tramos (inicio, fin) en minutos locales del día con sol en la pared, y los de luz de día."""
    track = sun_track(day.strftime("%Y-%m-%d"), utc_offset, round(lat * 20) / 20, round(lon * 20) / 20, step_min)
    sun, light = [], []
    cur_sun = cur_light = None
    for m, el, az in track:
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


# --- Puntuación (v2) ------------------------------------------------------------------------------------------------
#
# Por horas se simula la roca: temperatura con inercia (sol en la pared, aire, viento, enfriamiento de noche con cielo
# despejado), humedad acumulada (lluvia según tipo de roca y abrigo, condensación si la roca está por debajo del punto
# de rocío, secado por sol, viento y aire seco) y viento efectivo en la pared (dirección respecto a la orientación).
# Cada factor da 0–1 y la puntuación es 100 × su producto. Un día vale lo que la peor hora de su mejor ventana de 3 h.

# Tipo de roca → (cuánto la moja la lluvia, velocidad de secado). Valores aproximados: la arenisca absorbe y tarda,
# el granito suelta el agua enseguida.
ROCKS = {
    "sandstone": (1.6, 0.55), "gritstone": (1.4, 0.7), "conglomerate": (1.2, 0.8), "limestone": (1.0, 1.0),
    "dolomite": (1.0, 1.0), "gneiss": (0.7, 1.3), "basalt": (0.7, 1.2), "granite": (0.6, 1.4), "quartzite": (0.6, 1.3),
}  # fmt: skip


@dataclass
class Prefs:
    tmin: float = 12
    tmax: float = 22


@dataclass
class Rock:
    type: str = "limestone"
    sheltered: bool = False  # «rainSafety» en 8a.nu: desplomes que apenas se mojan

    @property
    def absorb(self) -> float:
        return ROCKS.get(self.type, ROCKS["limestone"])[0] * (0.35 if self.sheltered else 1.0)

    @property
    def drying(self) -> float:
        return ROCKS.get(self.type, ROCKS["limestone"])[1]


def dew_point(t: float, rh: float) -> float:
    """Punto de rocío (fórmula de Magnus)."""
    a, b = 17.62, 243.12
    g = math.log(max(rh, 1) / 100) + a * t / (b + t)
    return b * g / (a - g)


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


def effective_wind(wind: float, gust: float, wind_dir: float, aspect: float) -> tuple[float, float]:
    """Viento que llega a la pared: entero si sopla de cara, un tercio si viene de detrás o de lado."""
    exposure = 0.35 + 0.65 * max(0.0, math.cos(math.radians(wind_dir - aspect)))
    return wind * exposure, gust * exposure


def wind_score(eff: float, gust: float, rh: float) -> float:
    """Una brisa ligera es lo ideal (seca la roca y el sudor); el aire quieto y húmedo es pegajoso; el viento fuerte y
    las rachas penalizan."""
    if eff < 3:
        s = 0.92 if rh > 70 else 1.0
    elif eff <= 18:
        s = 1.0
    else:
        s = max(0.4, 1 - (eff - 18) / 17 * 0.4)
    if gust > 45:
        s *= max(0.6, 1 - (gust - 45) / 50)
    return s


def wall_irradiance(rad: float | None, sun_frac: float, cloud: float) -> float:
    """W/m² aproximados de sol directo en la pared: la radiación global de Open-Meteo, solo cuando el sol le da."""
    return min(900.0, (rad or 0) * sun_frac * 1.3 * (1 - 0.5 * cloud / 100))


def score_hours(hours: list[dict], p: Prefs, rock: Rock, aspect: float, now_idx: int = 0) -> None:
    """Añade a cada hora: rock_t (roca), feels (sensación), comfort, dew, eff_wind, gust, irr, wet,
    rock (dry/damp/wet), condensing, f (factores 0–1), score (0–100) y cat."""
    wet = 0.0
    rock_t = hours[0]["temp"] if hours else 0
    for i, h in enumerate(hours):
        t, rh, cloud = h["temp"], h["rh"], h["cloud"]
        dew = h.get("dew")
        if dew is None:
            dew = dew_point(t, rh)
        eff, gust = effective_wind(h["wind"], h.get("gust") or h["wind"] * 1.5, h["wind_dir"], aspect)
        irr = wall_irradiance(h.get("rad"), h["sun"], cloud)
        # Roca: tiende a una temperatura de equilibrio con unas 3 h de inercia.
        night_cool = 0 if h["day"] else 2.5 * (1 - cloud / 100)
        t_eq = t + irr / 55 - eff * 0.06 - night_cool
        rock_t += (t_eq - rock_t) * 0.35
        # Sensación: aire + sol en la piel + bochorno en días calurosos − viento.
        muggy = max(0.0, (rh - 50) / 10 * 0.6) if t > 20 else 0.0
        feels = t + irr / 90 + muggy - min(eff, 40) * 0.12
        comfort = (feels + rock_t) / 2
        # Humedad de la roca.
        rain = h["precip"] or 0
        condense = max(0.0, min(1.0, (dew + 0.5 - rock_t) / 2))
        dry = (0.05 + irr / 1500 + min(eff, 30) / 120 + max(0, 1 - rh / 100) * 0.3) * rock.drying
        wet = min(6.0, max(0.0, wet + rain * rock.absorb + condense * 0.12 - dry * (1 - condense)))
        state = "wet" if wet > 0.6 else "damp" if wet > 0.15 else "dry"
        f = {
            "temp": temp_score(comfort, p),
            "humidity": humidity_score(rh),
            "wind": wind_score(eff, gust, rh),
            "sun": 1.0,
            "condensation": 1 - 0.6 * condense,
            "moisture": {"dry": 1.0, "damp": 0.6, "wet": 0.2}[state],
            "rain": 1.0,
            "daylight": 1.0,
        }
        if h["day"] and irr > 250 and comfort > p.tmax:
            f["sun"] = 0.85  # sol de lleno en un día de calor
        elif h["day"] and irr < 50 and comfort < p.tmin and cloud < 50:
            f["sun"] = 0.92  # día frío y despejado con la pared a la sombra
        if rain >= 0.1:
            f["rain"] = 0.4 if rock.sheltered else 0.15
        elif i > now_idx and h.get("prob"):
            f["rain"] = 1 - 0.35 * h["prob"] / 100
        prev_day = hours[i - 1]["day"] if i else h["day"]
        next_day = hours[i + 1]["day"] if i + 1 < len(hours) else h["day"]
        if h["day"] and (not prev_day or not next_day):
            f["daylight"] = 0.85
        score = 100.0
        for v in f.values():
            score *= v
        h.update(
            rock_t=round(rock_t, 1), feels=round(feels, 1), comfort=round(comfort, 1), dew=round(dew, 1),
            eff_wind=round(eff), gust=round(gust), irr=round(irr), wet=round(wet, 2), rock=state,
            condensing=condense > 0.2, f={k: round(v, 2) for k, v in f.items()}, score=round(score),
            cat="night" if not h["day"] else category(score),
        )  # fmt: skip


def best_window(hours: list[dict], length: int = 3) -> tuple[int, int, float] | None:
    """Mejor ventana de `length` horas seguidas de día, medida por su PEOR hora (lo que la pared aguanta toda la
    sesión, no un pico): (índice inicio, índice fin exclusivo, puntuación de la peor hora)."""
    best = None
    for i in range(len(hours) - length + 1):
        win = hours[i : i + length]
        if not all(h["day"] for h in win):
            continue
        low = min(h["score"] for h in win)
        mean = sum(h["score"] for h in win) / length
        if best is None or (low, mean) > (best[2], best[3]):
            best = (i, i + length, low, mean)
    return best[:3] if best else None


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
    if win and win[2] >= 45:
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
        "factors": factors(win_hours or [h for h in dh if h["day"]] or dh),
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


FACTOR_LABELS = {
    "temp": "Temperature", "humidity": "Humidity", "wind": "Wind at the wall", "sun": "Sun", "rain": "Rain",
    "moisture": "Rock moisture", "condensation": "Condensation", "daylight": "Daylight",
}  # fmt: skip


def factors(hs: list[dict]) -> list[dict]:
    """«Why this score?»: cada factor (0–1, la media en las horas `hs`) con una frase de lo que lo explica."""
    n = len(hs)
    avg = lambda k: sum(h[k] for h in hs) / n  # noqa: E731
    fav = lambda k: sum(h["f"][k] for h in hs) / n  # noqa: E731
    comfort, rh = avg("comfort"), avg("rh")
    eff, gust = avg("eff_wind"), max(h["gust"] for h in hs)
    sun_h = sum(h["sun"] for h in hs)
    rain_mm = sum(h["precip"] or 0 for h in hs)
    prob = max(h["prob"] or 0 for h in hs)
    rock_t, dew = min(h["rock_t"] for h in hs), max(h["dew"] for h in hs)
    states = {h["rock"] for h in hs}
    text = {
        "temp": f"feels {comfort:.0f} °C at the wall (air {avg('temp'):.0f}°, rock {avg('rock_t'):.0f}°)",
        "humidity": f"{rh:.0f} % relative humidity",
        "wind": f"{eff:.0f} km/h at the wall, gusts {gust} km/h",
        "sun": f"{sun_h:.1f} h of sun on the wall, {avg('irr'):.0f} W/m²",
        "rain": f"{rain_mm:.1f} mm" + (f", {prob} % chance" if prob else ""),
        "moisture": "dry" if states == {"dry"} else "wet" if "wet" in states else "damp",
        "condensation": f"rock {rock_t:.0f}° vs dew point {dew:.0f}°"
        + (" — condensing" if any(h["condensing"] for h in hs) else ""),
        "daylight": "full daylight" if fav("daylight") > 0.99 else "near sunrise or sunset",
    }
    out = [{"key": k, "label": FACTOR_LABELS[k], "value": round(fav(k), 2), "text": text[k]} for k in FACTOR_LABELS]
    return sorted(out, key=lambda x: x["value"])


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
                "dew": (h.get("dew_point_2m") or [None] * len(h["time"]))[i],
                "gust": (h.get("wind_gusts_10m") or [None] * len(h["time"]))[i],
            }
        )
    return [x for x in out if x["temp"] is not None]


def build(
    crag: dict, sectors: list[dict], weather: dict, aspect: dict, p: Prefs, now: datetime | None = None,
    rock_type: str | None = None,
) -> dict:  # fmt: skip
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
    rock = Rock(rock_type or "limestone", bool(crag.get("rainSafety")))
    score_hours(hours, p, rock, aspect["deg"], now_idx)

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
            "rock": rock.type,
            "rock_known": rock_type is not None,
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
