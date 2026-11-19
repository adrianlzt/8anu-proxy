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


def test_viento_por_modelo():
    times = [f"2026-11-17T{h:02d}:00" for h in range(24)]
    key = "ecmwf_ifs025"
    hourly = {"time": times, f"temperature_2m_{key}": [10] * 24, f"precipitation_{key}": [0] * 24,
              f"wind_speed_10m_{key}": [10] * 24, f"wind_gusts_10m_{key}": [20] * 23 + [90],
              f"wind_direction_10m_{key}": [350] * 12 + [10] * 12}
    d = meteo.model_days(hourly)["models"]["ECMWF"][0]
    assert d["wind"] == 10 and d["gust"] == 20  # la racha de las 23 h queda fuera de 8–20 h
    assert d["wind_dir"] in (0, 360) or d["wind_dir"] < 5 or d["wind_dir"] > 355  # media vectorial de 350° y 10°: norte


def test_paso_real_de_cada_modelo():
    times = [f"2026-11-19T{h:02d}:00" for h in range(24)] + [f"2026-11-20T{h:02d}:00" for h in range(24)]
    hourly_rain = [0] * 10 + [0.2, 1.4, 0.3] + [0] * 35
    # Bloques de 3 h que acaban en 00, 03… UTC; con UTC+1, las horas locales 2-3-4, 5-6-7…
    block_rain = [0] * 11 + [0.4, 0.4, 0.4] + [0] * 34
    multi = {"time": times,
             "temperature_2m_ecmwf_ifs025": [10] * 48, "precipitation_ecmwf_ifs025": block_rain,
             "temperature_2m_icon_seamless": [10] * 48, "precipitation_icon_seamless": hourly_rain}
    ens = {"time": times, "temperature_2m": [10] * 48, "precipitation": block_rain,
           "temperature_2m_member01": [11] * 48, "precipitation_member01": [0] * 48}
    mh = meteo.model_hours(multi, ens, 3600)
    assert mh["dates"] == ["2026-11-19", "2026-11-20"]
    assert mh["models"]["ECMWF"]["step"] == [3, 3]  # el día seco toma el paso del día con lluvia
    assert mh["models"]["ICON"]["step"] == [1, 1]
    assert mh["ens"]["step"] == [3, 3]
    assert mh["ens"]["pp"][12] == 50 and mh["ens"]["pp"][0] == 0
