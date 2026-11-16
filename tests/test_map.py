import asyncio

import app.map_api as m


def test_load_crags_une_las_dos_fuentes(monkeypatch):
    world = {"crags": [
        {"countrySlug": "spain", "cragSlug": "patones", "cragName": "Patones", "category": 0, "areaName": "Madrid",
         "countryName": "Spain", "location": {"latitude": 40.89, "longitude": -3.43}, "totalZlaggables": 1232,
         "totalAscents": 36000, "averageRating": 3.67, "gradeExtrems": [-1, 35]},
        {"countrySlug": "spain", "cragSlug": "sin-coords", "cragName": "X", "category": 0,
         "location": {"latitude": 0.0, "longitude": 0.0}},
    ]}  # fmt: skip
    geo = {"features": [{"properties": {"countrySlug": "spain", "cragSlug": "patones", "category": 0, "rainSafety": True}}]}

    async def fake(app, path):
        return world if "crags/map?" in path else geo

    monkeypatch.setattr(m, "_get_json", fake)
    m._crags.update(at=0, list=[], by_key={})
    crags = asyncio.run(m.load_crags(None))
    assert [c["key"] for c in crags] == ["sportclimbing/spain/patones"]
    c = crags[0]
    assert c["rain"] is True and c["gmin"] is None and c["gmax"] == 35 and c["routes"] == 1232
