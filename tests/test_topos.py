import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import app.main as main
from app import store, topos

ROOT = Path(__file__).resolve().parents[1]
HASH = "a" * 32
ACCESS = "b" * 32


def tiny_jpeg(w: int, h: int) -> bytes:
    """Un JPEG mínimo (solo cabeceras) con un SOF0 que diga w×h."""
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"\0\0" + sof + b"\xff\xd9"


def test_jpeg_size(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(tiny_jpeg(640, 480))
    assert topos.jpeg_size(p) == (640, 480)
    p.write_bytes(b"nope")
    assert topos.jpeg_size(p) is None


def test_norm_and_grades():
    assert topos.norm("Espàrrecs i Coliflors") == "esparrecs i coliflors"
    assert topos.norm("The Egg") == "egg"
    assert topos.grade_index("7a+") - topos.grade_index("7a") == 1
    assert topos.grade_index("6b/6b+") == topos.grade_index("6b")


def test_ticks_match():
    t = topos.Ticks()
    t.set([{"crag_name": "Coll de Nargó", "zlaggable_name": "Espàrrecs i Coliflors", "difficulty": "7a+", "d": "2024-01-02"},
           {"crag_name": "Otra", "zlaggable_name": "Repetida", "difficulty": "6b", "d": "2023-05-05"}], "test")
    assert t.match("Esparrecs i coliflors", "7a+", "Coll de Nargo")["status"] == "sent"
    assert t.match("Repetida", "6b+", "Coll de Nargo")["status"] == "maybe"
    assert t.match("Repetida", "8a", "Coll de Nargo") is None
    assert t.match("Nada", "6a", "Coll de Nargo") is None


@pytest.fixture
def src(tmp_path):
    """Una carpeta como ~/tmp/8a-alias con una escuela, un croquis, una foto de acceso y una vía con trazo."""
    s = tmp_path / "src"
    (s / "datos").mkdir(parents=True)
    crag = {"countrySlug": "spain", "cragSlug": "nargo", "cragName": "Coll de Nargó", "category": 0, "areaName": "Lleida",
            "premium_approach": "Walk __Sector A:__ 5 min", "walkingTime": 15, "rock_type": "Limestone",
            "crag_coords_premium": {"latitude": 42.1, "longitude": 1.3},
            "gallery": {"access": [ACCESS]},
            "sectors": [{"sectorSlug": "bel-canto", "sectorName": "Bel Canto", "ordering": 2, "topos": [HASH]},
                        {"sectorSlug": "vacio", "sectorName": "Vacío", "ordering": 1, "topos": []}]}
    (s / "datos" / "escuelas.jsonl").write_text(json.dumps(crag) + "\n")
    (s / "crops" / "spain" / "nargo").mkdir(parents=True)
    (s / "crops" / "spain" / "nargo" / f"bel-canto--{HASH}.jpg").write_bytes(tiny_jpeg(1200, 700))
    (s / "accesos" / "spain" / "nargo").mkdir(parents=True)
    (s / "accesos" / "spain" / "nargo" / f"acceso--{ACCESS}.jpg").write_bytes(tiny_jpeg(800, 600))
    via = {"zlaggableId": 77, "zlaggableName": "Espàrrecs i Coliflors", "zlaggableSlug": "esparrecs", "difficulty": "7a+",
           "topo": HASH, "topo_num": "30", "path": [[300, 500], [310, 100]], "reference_width": 600, "height": 30,
           "totalAscents": 269, "sectorSlug": "bel-canto", "countrySlug": "spain", "cragSlug": "nargo"}
    (s / "datos" / "vias.jsonl").write_text(json.dumps(via) + "\n")
    return s


def run_import(src: Path, data_dir: Path, *extra: str) -> str:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "eight" / "import_topos.py"), str(src), "--data-dir", str(data_dir), *extra],
                       capture_output=True, text=True, check=True, cwd=ROOT)
    return r.stdout


@pytest.fixture
def data_dir(tmp_path, monkeypatch, src):
    d = tmp_path / "data"
    monkeypatch.setattr(store, "DATA_DIR", d)
    out = run_import(src, d)
    assert "croquis 1" in out and "fotos de acceso 1" in out and "vías con trazo 1" in out
    return d


def test_import_and_crag_data(data_dir):
    assert (data_dir / "topos" / f"{HASH}.jpg").exists()
    d = topos.crag_data("spain", "nargo")
    assert d["crag"]["name"] == "Coll de Nargó" and d["crag"]["key"] == "sportclimbing/spain/nargo"
    assert d["crag"]["premium"]["premium_approach"].startswith("Walk")
    assert [a["image"] for a in d["access"]] == [f"/_proxy/topos/img/{ACCESS}.jpg"]
    assert len(d["topos"]) == 1
    t = d["topos"][0]
    assert t["source"] == "8a" and t["baked"] is True and t["width"] == 1200 and t["sector"] == "bel-canto"
    r = t["routes"][0]
    assert r["num"] == "30" and r["name"] == "Espàrrecs i Coliflors" and r["ascents"] == 269 and r["pop"] == 100
    assert r["points"][0] == {"x": 600.0, "y": 1000.0, "t": ""}  # escalado 1200/600
    assert r["url8a"] == "/crags/sportclimbing/spain/nargo/sectors/bel-canto/routes/esparrecs"
    # Un sector sin croquis propios: los de la escuela.
    assert len(topos.crag_data("spain", "nargo", "vacio")["topos"]) == 1
    assert topos.crag_data("spain", "otra") is None
    # Repetir el importador no duplica.
    run_import(data_dir.parent / "src", data_dir)
    assert len(topos.crag_data("spain", "nargo")["topos"]) == 1


def test_sent_in_crag_data(data_dir, monkeypatch):
    topos.ticks.set([{"crag_name": "Coll de Nargó", "zlaggable_name": "Espàrrecs i Coliflors", "difficulty": "7a+", "d": "2024-01-02"}], "test")
    try:
        r = topos.crag_data("spain", "nargo")["topos"][0]["routes"][0]
        assert r["sent"] == "sent" and r["sent_info"]["date"] == "2024-01-02"
    finally:
        topos.ticks.set([], "none")


@pytest.fixture
def client(data_dir, monkeypatch):
    monkeypatch.setattr(topos.Ticks, "run", lambda self, app: __import__("asyncio").sleep(0))
    with TestClient(main.app, base_url="https://8a.adrianlzt.com") as c:
        yield c


def test_api(client):
    r = client.get("/_proxy/topos/sportclimbing/spain/nargo?sector=bel-canto")
    assert r.status_code == 200 and r.json()["topos"][0]["heading"] == "Bel Canto"
    assert client.get("/_proxy/topos/sportclimbing/spain/nope").status_code == 404
    assert client.get("/_proxy/topos/sportclimbing/spain/nargo?sector=BAD!").status_code == 400
    r = client.get(f"/_proxy/topos/img/{HASH}.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and "immutable" in r.headers["cache-control"]
    assert client.get("/_proxy/topos/img/zzzz.jpg").status_code == 404
    r = client.get("/_proxy/topos/sportclimbing/spain/nargo/page")
    assert r.status_code == 200 and "nuTopos.render" in r.text and "topos.js?v=" in r.text


def test_password_protects_topos(client, monkeypatch):
    monkeypatch.setattr(main, "PASSWORD", "secreto")
    assert client.get("/_proxy/topos/sportclimbing/spain/nargo").status_code == 401
    assert client.get(f"/_proxy/topos/img/{HASH}.jpg").status_code == 401
    assert client.get("/_proxy/topos/sportclimbing/spain/nargo", auth=("x", "secreto")).status_code == 200
