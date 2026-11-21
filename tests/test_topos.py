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


@pytest.fixture
def src(tmp_path):
    """Una carpeta como ~/tmp/8a-alias con una escuela, un croquis, una foto de acceso y una vía con trazo."""
    s = tmp_path / "src"
    (s / "datos").mkdir(parents=True)
    crag = {"countrySlug": "spain", "cragSlug": "nargo", "cragName": "Coll de Nargó", "category": 0, "areaName": "Lleida",
            "premium_approach": "Walk __Sector A:__ 5 min", "walkingTime": 15, "rock_type": "Limestone",
            "crag_coords_premium": {"latitude": 42.1, "longitude": 1.3},
            "gallery": {"access": [ACCESS]},
            "sectors": [{"sectorSlug": "bel-canto", "sectorName": "Bel Canto", "ordering": 2, "topos": [HASH, "c" * 32]},
                        {"sectorSlug": "vacio", "sectorName": "Vacío", "ordering": 1, "topos": []}]}
    (s / "datos" / "escuelas.jsonl").write_text(json.dumps(crag) + "\n")
    (s / "crops" / "spain" / "nargo").mkdir(parents=True)
    (s / "crops" / "spain" / "nargo" / f"bel-canto--{HASH}.jpg").write_bytes(tiny_jpeg(1200, 700))
    (s / "accesos" / "spain" / "nargo").mkdir(parents=True)
    (s / "accesos" / "spain" / "nargo" / f"acceso--{ACCESS}.jpg").write_bytes(tiny_jpeg(800, 600))
    via = {"zlaggableId": 77, "zlaggableName": "Espàrrecs i Coliflors", "zlaggableSlug": "esparrecs", "difficulty": "7a+",
           "topo": HASH, "topo_num": "30", "path": [[300, 500], [310, 100]], "reference_width": 600, "height": 30,
           "totalAscents": 269, "sectorSlug": "bel-canto", "countrySlug": "spain", "cragSlug": "nargo"}
    via2 = dict(via, zlaggableId=78, zlaggableName="Sin foto", topo="c" * 32, topo_num="31", reference_width=500)
    (s / "datos" / "vias.jsonl").write_text(json.dumps(via) + "\n" + json.dumps(via2) + "\n")
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
    assert "croquis 1" in out and "fotos de acceso 1" in out and "vías con trazo 2" in out and "(missing) 1" in out
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
    assert r["url8a"] == "/crags/sportclimbing/spain/nargo/sectors/bel-canto/routes/esparrecs" and r["slug"] == "esparrecs"
    # Un sector sin croquis propios: los de la escuela.
    assert len(topos.crag_data("spain", "nargo", "vacio")["topos"]) == 1
    assert topos.crag_data("spain", "otra") is None
    # Repetir el importador no duplica.
    run_import(data_dir.parent / "src", data_dir)
    assert len(topos.crag_data("spain", "nargo")["topos"]) == 1


def test_missing_image_kept_aside(data_dir):
    with store.connect() as db:
        row = db.execute("SELECT status, width, file FROM topo WHERE hash = ?", ("c" * 32,)).fetchone()
        pts = db.execute("SELECT points FROM topo_route WHERE topo = ?", ("c" * 32,)).fetchone()[0]
    assert row["status"] == "missing" and row["width"] == 500 and row["file"] == ""
    assert json.loads(pts)[0] == [300, 500]  # sin escalar: en píxeles de reference_width
    assert [t["tid"] for t in topos.crag_data("spain", "nargo")["topos"]] == [HASH]


@pytest.fixture
def client(data_dir, monkeypatch):
    with TestClient(main.app, base_url="https://8a.adrianlzt.com") as c:
        yield c


def test_api(client):
    r = client.get("/_proxy/topos/sportclimbing/spain/nargo?sector=bel-canto")
    assert r.status_code == 200 and r.json()["topos"][0]["heading"] == "Bel Canto · 1/2"
    assert client.get("/_proxy/topos/sportclimbing/spain/nope").json()["empty"] is True
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


def test_empty_crag_lets_upload(client):
    r = client.get("/_proxy/topos/sportclimbing/spain/nope?name=Nope")
    assert r.status_code == 200 and r.json()["empty"] and r.json()["crag"]["name"] == "Nope"


def test_paper_upload_edit_delete(client):
    q = "heading=Guidebook&sector=bel-canto&sector_name=Bel%20Canto&baked=1"
    assert client.put("/_proxy/topos/sportclimbing/spain/nargo/upload?" + q, content=b"nope").status_code == 415
    r = client.put("/_proxy/topos/sportclimbing/spain/nargo/upload?" + q, content=tiny_jpeg(900, 600))
    assert r.status_code == 200, r.text
    h = r.json()["hash"]
    assert h.startswith("paper-") and r.json()["width"] == 900
    d = client.get("/_proxy/topos/sportclimbing/spain/nargo?sector=bel-canto").json()
    t = [x for x in d["topos"] if x["tid"] == h][0]
    assert t["source"] == "paper" and t["heading"] == "Guidebook" and t["editable"] and t["routes"] == []
    assert client.get(f"/_proxy/topos/img/{h}.jpg").status_code == 200
    # Vías: una de 8a.nu (con id) y una suelta.
    body = [{"id": "77", "num": "1", "name": "Espàrrecs i Coliflors", "grade": "7a+", "points": [{"x": 10, "y": 500}, {"x": 12.26, "y": 100}], "slug": "esparrecs", "sector": "bel-canto", "ascents": 269},
            {"num": "2", "name": "Sin nombre", "grade": "6a", "points": [[100, 500], [110, 90]]}]
    r = client.put(f"/_proxy/topos/{h}/routes", json=body)
    assert r.status_code == 200 and r.json()["routes"] == 2
    rs = [x for x in client.get("/_proxy/topos/sportclimbing/spain/nargo").json()["topos"] if x["tid"] == h][0]["routes"]
    assert [x["num"] for x in rs] == ["1", "2"] and rs[0]["points"][1] == {"x": 12.3, "y": 100.0, "t": ""}
    assert rs[0]["url8a"].endswith("/sectors/bel-canto/routes/esparrecs") and rs[1]["id"] == "p2"
    assert client.put(f"/_proxy/topos/{h}/routes", json=[{"points": [["x", 1]]}]).status_code == 400
    assert client.put("/_proxy/topos/paper-000000000000/routes", json=[]).status_code == 404
    # Retocar y borrar (soft).
    assert client.patch(f"/_proxy/topos/{h}", json={"heading": "Page 12", "baked": False}).status_code == 200
    t = [x for x in client.get("/_proxy/topos/sportclimbing/spain/nargo").json()["topos"] if x["tid"] == h][0]
    assert t["heading"] == "Page 12" and t["baked"] is False
    assert client.delete(f"/_proxy/topos/{h}").status_code == 200
    assert not [x for x in client.get("/_proxy/topos/sportclimbing/spain/nargo").json()["topos"] if x["tid"] == h]


def test_offline_bits(client):
    r = client.get("/_proxy/topos/sw.js")
    assert r.status_code == 200 and r.headers["service-worker-allowed"] == "/_proxy/topos/" and "nut-v1" in r.text
    assert client.get("/_proxy/topos/static/topos.js?v=1").status_code == 200
    assert "Saved topos" in client.get("/_proxy/topos/").text
    assert "/_proxy/topos/static/topos.js" in client.get("/_proxy/topos/sportclimbing/spain/nargo/page").text
