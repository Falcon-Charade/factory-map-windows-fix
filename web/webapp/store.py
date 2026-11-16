"""Database (sqlite) and small helpers for reading files and request parameters."""

import json
import sqlite3
import threading

from .config import DB_PATH


db_lock = threading.Lock()


def db():
    """Open the database (creates any missing tables). Callers hold db_lock and close it when done."""
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, tick INTEGER, type TEXT, data TEXT);
    CREATE INDEX IF NOT EXISTS ev_type ON events(type, id);
    CREATE TABLE IF NOT EXISTS samples (tick INTEGER PRIMARY KEY, ts REAL, data TEXT);
    CREATE TABLE IF NOT EXISTS flow (tick INTEGER, ts REAL, kind TEXT, name TEXT, produced REAL, consumed REAL,
                                     PRIMARY KEY (kind, name, tick));
    CREATE TABLE IF NOT EXISTS first_seen (kind TEXT, name TEXT, tick INTEGER, ts REAL, baseline INTEGER,
                                           PRIMARY KEY (kind, name));
    CREATE TABLE IF NOT EXISTS snapshots (id INTEGER, surface INTEGER, ts REAL, tick INTEGER, name TEXT, kind TEXT,
                                          zone TEXT, minz INTEGER, bounds TEXT, tiles INTEGER, thinned INTEGER DEFAULT 0,
                                          PRIMARY KEY (id, surface));
    CREATE TABLE IF NOT EXISTS players (name TEXT PRIMARY KEY, last_seen REAL);
    CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS patch_hist (sid INTEGER, surface INTEGER, data TEXT, PRIMARY KEY (sid, surface));
    """)
    if not _migrated:
        # frame: 1 = kept for the timelapse, 0 = a live-map version that gets replaced a minute later
        cols = [r[1] for r in con.execute("PRAGMA table_info(snapshots)")]
        if "frame" not in cols:
            con.execute("ALTER TABLE snapshots ADD COLUMN frame INTEGER DEFAULT 1")
            con.commit()
        _migrated.append(True)
    return con


_migrated = []


def kv_get(con, k, default=None):
    """Read a small stored value (JSON) from the kv table."""
    r = con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def kv_set(con, k, v):
    """Store a small value (JSON) in the kv table."""
    con.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, json.dumps(v)))


def read_json(p):
    """Read a JSON file; None if it's missing or half-written."""
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def as_list(v):
    """Factorio's table_to_json writes big Lua arrays as {"1":..,"2":..} objects; accept every form."""
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return [v[k] for k in sorted(v, key=int)]
    if isinstance(v, str):
        return [int(x) for x in v.split(",")] if v else []
    return []


def qint(q, k, default):
    """Integer query parameter; junk, inf/nan or huge numbers give the default / a clamped value, never an error."""
    try:
        return max(-2 ** 62, min(2 ** 62, int(float(q[k][0]))))
    except (KeyError, ValueError, IndexError, OverflowError):
        return default
