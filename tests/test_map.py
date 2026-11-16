import asyncio

import app.map_api as m
from app import aspect


def test_load_crags_de_map_items(monkeypatch, tmp_path):
    item = {"name": "Urdón", "slug": "urdon", "latitude": 43.267, "longitude": -4.632, "category": 0,
            "country_slug": "spain", "country_name": "Spain", "area_name": "Hermida", "avg_rating": 4.13,
            "total_ascendables": 80, "total_ascents": 1271, "grades": {"project": 2, "29": 6, "32": 3}}
    area = {"name": "Madrid", "slug": "madrid", "area_type": 0}  # las zonas no traen grados: fuera

    async def fake(app, box, category):
        return [item, area] if category == "sportclimbing" and box == m.WORLD[0] else []

    monkeypatch.setattr(m, "_items", fake)
    monkeypatch.setattr(m, "CACHE", tmp_path / "crags.json")
    m._crags.update(at=0, list=[], by_key={})
    crags = asyncio.run(m.load_crags(None))
    assert [c["key"] for c in crags] == ["sportclimbing/spain/urdon"]
    g = crags[0]["g"]
    assert len(g) == 39 and g[28] == 6 and g[31] == 3 and g[34] == 0  # 8a (29), 8b+ (32), ningún 9a (35)
    assert (tmp_path / "crags.json").exists()


def test_cliff_aspect_cara_a_la_derecha_de_la_linea():
    lat, lon = 45.0, 10.0
    # Acantilado dibujado de oeste a este justo al norte del punto: arriba a la izquierda (norte), cara al sur.
    line = [(lat + 0.0005, lon - 0.001), (lat + 0.0005, lon + 0.001)]
    deg, length = aspect.cliff_aspect([line], lat, lon)
    assert abs(deg - 180) < 1 and length > 100
    assert aspect.cliff_aspect([[(lat + 0.01, lon), (lat + 0.01, lon + 0.001)]], lat, lon) is None  # a 1 km: no cuenta
