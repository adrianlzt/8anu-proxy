import math
from datetime import datetime, timedelta

from app import conditions as cond


def test_sun_position_madrid_mediodia_solar():
    # 21 de junio, ~12:15 UTC en Madrid (40.4N, -3.7E): sol al sur y alto (~73°).
    el, az = cond.sun_position(datetime(2026, 6, 21, 12, 15), 40.4, -3.7)
    assert 70 < el < 75
    assert 170 < az < 190


def test_sun_on_wall_por_orientacion():
    day = datetime(2026, 11, 16)
    south = cond.wall_sun(day, 3600, 45.93, 10.90, 180)
    north = cond.wall_sun(day, 3600, 45.93, 10.90, 0)
    east = cond.wall_sun(day, 3600, 45.93, 10.90, 90)
    assert south["sun"] and not north["sun"]
    # Una pared al este tiene sol por la mañana y la pierde antes que una al sur.
    assert east["sun"][0][1] < south["sun"][-1][1]
    assert east["sun"][0][0] < 12 * 60


def test_rays_aspect_ladera_que_sube_al_norte():
    pts = cond.terrain_rays(45.0, 10.0)
    # El terreno sube hacia el norte (y > 0): la pared mira al sur.
    elev = [100 + d * math.cos(math.radians(b)) * 0.5 for b, d, *_ in pts]
    deg, slope = cond.rays_aspect(pts, elev)
    assert abs(deg - 180) < 15
    assert slope > 15


def _weather(rain_hours=(), temp=15.0, rh=60):
    start = datetime(2026, 11, 14)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(24 * 5)]
    n = len(times)
    hour = [int(t[11:13]) for t in times]
    return {
        "utc_offset_seconds": 3600,
        "hourly": {
            "time": times,
            "temperature_2m": [temp] * n,
            "relative_humidity_2m": [rh] * n,
            "precipitation": [2.0 if i in rain_hours else 0.0 for i in range(n)],
            "precipitation_probability": [90 if i in rain_hours else 0 for i in range(n)],
            "weather_code": [61 if i in rain_hours else 1 for i in range(n)],
            "cloud_cover": [20] * n,
            "wind_speed_10m": [5] * n,
            "wind_direction_10m": [180] * n,
            "is_day": [1 if 7 <= h < 17 else 0 for h in hour],
            "shortwave_radiation": [300] * n,
        },
    }


CRAG = {"cragName": "Test", "areaName": "Area", "location": {"latitude": 45.93, "longitude": 10.9}, "rainSafety": False}
ASPECT = {"deg": 180, "source": "user", "compass": "S", "sectors": {}}


def test_build_dia_seco_tiene_ventana_y_lluvia_la_quita():
    d = cond.build(CRAG, [], _weather(), ASPECT, cond.Prefs(12, 22), now=datetime(2026, 11, 15, 9))
    assert len(d["days"]) == 5
    today = d["days"][d["today"]]
    assert today["date"] == "2026-11-15"
    assert today["window"] and today["cat"] in ("great", "good")
    assert today["rock"] == "Rock dry"
    assert d["now"]["state"] in ("Sun", "Shade")

    # Lluvia toda la mañana del 16: roca mojada y sin buena ventana ese día.
    rain = set(range(48, 48 + 13))
    d = cond.build(CRAG, [], _weather(rain), ASPECT, cond.Prefs(12, 22), now=datetime(2026, 11, 15, 9))
    wet = next(x for x in d["days"] if x["date"] == "2026-11-16")
    assert "window" not in wet and wet["cat"] == "bad"
    assert wet["rain"]["mm"] == 26.0


def test_preferencias_cambian_la_puntuacion():
    cold = cond.build(CRAG, [], _weather(temp=5), ASPECT, cond.Prefs(12, 22), now=datetime(2026, 11, 15, 9))
    fine = cond.build(CRAG, [], _weather(temp=5), ASPECT, cond.Prefs(0, 10), now=datetime(2026, 11, 15, 9))
    assert fine["days"][1]["score"] > cold["days"][1]["score"]


def test_viento_efectivo_segun_orientacion():
    face, _ = cond.effective_wind(20, 30, 180, 180)  # viento del sur contra una pared al sur
    back, _ = cond.effective_wind(20, 30, 0, 180)  # del norte: la pared queda a sotavento
    assert face == 20 and back == 12  # a sotavento llega el 60 %


def test_condensacion_con_roca_fria_y_aire_humedo():
    w = _weather(temp=8, rh=98)
    w["hourly"]["dew_point_2m"] = [7.8] * len(w["hourly"]["time"])
    w["hourly"]["cloud_cover"] = [0] * len(w["hourly"]["time"])
    d = cond.build(CRAG, [], w, {**ASPECT, "deg": 0}, cond.Prefs(4, 14), now=datetime(2026, 11, 15, 9))
    morning = [h for h in d["hours"] if h["time"].startswith("2026-11-15T0")]
    assert any(h["condensing"] for h in morning)
    assert all(h["f"]["condensation"] < 1 for h in morning if h["condensing"])


def test_ventana_vale_su_peor_hora():
    hours = [{"day": True, "score": s} for s in (90, 90, 30, 70, 70, 70)]
    a, b, low = cond.best_window(hours)
    assert (a, b, low) == (3, 6, 70)
