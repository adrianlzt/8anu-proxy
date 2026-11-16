"""Lo que aporta el usuario en la página de condiciones (orientación de la escuela y de sus sectores, webcams) y la
orientación calculada por el relieve, en una SQLite (DATA_DIR, por defecto ~/.local/share/8anu-proxy)."""

import os
import sqlite3
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path.home() / ".local" / "share" / "8anu-proxy"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS aspect (
    crag TEXT NOT NULL, sector TEXT NOT NULL DEFAULT '', deg REAL NOT NULL, source TEXT NOT NULL,
    slope REAL, updated REAL NOT NULL, PRIMARY KEY (crag, sector, source)
);
CREATE TABLE IF NOT EXISTS webcam (
    id INTEGER PRIMARY KEY AUTOINCREMENT, crag TEXT NOT NULL, url TEXT NOT NULL, title TEXT, added REAL NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "data.db")
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def get_aspects(crag: str) -> list[dict]:
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM aspect WHERE crag = ?", (crag,))]


def set_aspect(crag: str, sector: str, deg: float, source: str, slope: float | None = None) -> None:
    with connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO aspect (crag, sector, deg, source, slope, updated) VALUES (?, ?, ?, ?, ?, ?)",
            (crag, sector, deg % 360, source, slope, time.time()),
        )


def get_webcams(crag: str) -> list[dict]:
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT id, url, title FROM webcam WHERE crag = ? ORDER BY id", (crag,))]


def add_webcam(crag: str, url: str, title: str) -> None:
    with connect() as db:
        db.execute("INSERT INTO webcam (crag, url, title, added) VALUES (?, ?, ?, ?)", (crag, url, title, time.time()))
