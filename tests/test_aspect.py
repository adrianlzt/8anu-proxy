import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from app import aspect, store
from app import conditions as cond

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    return tmp_path


def test_prioridad_de_fuentes_y_segunda_opinion_del_relieve(data_dir):
    store.set_aspect("x/y/z", "", 90, "terrain", 30)
    assert aspect.current("x/y/z")["source"] == "terrain"
    faces = [{"deg": 200, "weight": 0.7, "spread": 15, "slope": 50}, {"deg": 20, "weight": 0.3, "spread": 20, "slope": 40}]
    store.set_aspect("x/y/z", "", 200, "dem", 50, spread=15, faces=faces, meta={"dem": "ign-mdt05", "res_m": 5})
    a = aspect.current("x/y/z")
    assert (a["source"], a["spread"], a["meta"]["dem"], len(a["faces"])) == ("dem", 15, "ign-mdt05", 2)
    store.set_aspect("x/y/z", "", 10, "osm")
    a = aspect.current("x/y/z")
    assert a["source"] == "osm" and a["dem"]["diff"] == 170
    store.set_aspect("x/y/z", "", 180, "user")
    assert aspect.current("x/y/z")["source"] == "user"  # lo del usuario no lo pisa nada
    assert {r["source"] for r in store.get_aspects("x/y/z")} == {"terrain", "dem", "osm", "user"}


def test_otra_cara_sale_como_sol_posible():
    day = datetime(2026, 11, 16)
    north = {"deg": 0, "source": "dem", "spread": 10, "faces": [{"deg": 0, "weight": 0.6}]}
    assert not cond.faces_sun(day, 3600, 42.8, -1.8, north)["possible"]
    u = {**north, "faces": [{"deg": 0, "weight": 0.6}, {"deg": 180, "weight": 0.4}]}
    s = cond.faces_sun(day, 3600, 42.8, -1.8, u)
    assert not s["sun"] and s["possible"]  # la principal no da sol, la cara sur sí


def _system_numpy() -> bool:
    return subprocess.run([aspect.DEM_PYTHON, "-c", "import numpy, osgeo"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _system_numpy(), reason="sin numpy/GDAL en el Python del sistema")
def test_dem_analyze_pared_y_dos_caras():
    # analyze() con superficies sintéticas, en el Python del sistema (el del proxy no tiene numpy)
    code = """
import json, sys
sys.path.insert(0, 'tools/dem')
import numpy as np, aspect as A
y, x = np.mgrid[0:81, 0:81]
# Escarpe que sube hacia el norte (filas pequeñas): la pared mira al sur
z = 100 + 80 / (1 + np.exp((y - 40) / 2.0))
one = A.analyze(z, 5, 5)
# Espolón norte-sur: caras al este y al oeste
v = 200 - 1.5 * np.abs(x - 40) * 5
two = A.analyze(v.astype(float), 5, 5)
print(json.dumps([one["deg"], len(one["faces"]), sorted(f["deg"] for f in two["faces"])]))
"""
    out = subprocess.run([aspect.DEM_PYTHON, "-I", "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)
    import json

    one, n, two = json.loads(out.stdout)
    assert abs(one - 180) < 10 and n == 1
    assert len(two) == 2 and abs(two[0] - 90) < 10 and abs(two[1] - 270) < 10
