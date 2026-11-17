from app import meteo


def test_ensemble_percentiles_y_probabilidad():
    hourly = {"time": ["2026-11-17T00:00"], "temperature_2m": [10.0], "precipitation": [0.0]}
    for m in range(1, 11):
        hourly[f"temperature_2m_member{m:02d}"] = [10.0 + m]
        hourly[f"precipitation_member{m:02d}"] = [1.0 if m <= 3 else 0.0]
    e = meteo.ensemble_stats(hourly)
    assert e["members"] == 11
    assert e["t10"][0] < e["t50"][0] < e["t90"][0]
    assert e["pp"][0] == 27  # 3 de 11 miembros con lluvia


def _models(rains, tmax):
    times = [f"2026-11-17T{h:02d}:00" for h in range(24)]
    hourly = {"time": times}
    for (key, _), r, t in zip(meteo.MODELS.items(), rains, tmax):
        hourly[f"temperature_2m_{key}"] = [t - 5] * 23 + [t]
        hourly[f"precipitation_{key}"] = [r / 24] * 24
    return meteo.model_days(hourly)


def test_acuerdo_entre_modelos():
    assert _models([0, 0, 0, 0, 0], [15, 15, 16, 15, 14])["agreement"][0]["level"] == "high"
    assert _models([0, 0, 10, 0, 0], [15, 15, 16, 15, 14])["agreement"][0]["level"] != "high"
    assert _models([0, 0, 0, 0, 0], [10, 15, 18, 12, 20])["agreement"][0]["level"] == "low"


def test_avisos_tormenta_y_helada():
    n = 24
    h = {"time": [f"2026-11-17T{i:02d}:00" for i in range(n)], "cape": [0] * n, "lifted_index": [5] * n,
         "weather_code": [1] * n, "freezing_level_height": [3000] * n, "visibility": [20000] * n, "is_day": [1] * n,
         "wind_gusts_10m": [20] * n, "uv_index": [3] * n, "temperature_2m": [15] * n}
    h["cape"][15], h["lifted_index"][15] = 1200, -4
    h["freezing_level_height"][6] = 900
    kinds = {r["kind"] for r in meteo.risks(h, elevation=800)}
    assert kinds == {"storm", "freeze"}
