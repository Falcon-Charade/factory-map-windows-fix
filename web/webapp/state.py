"""In-memory state shared by the reader thread and web requests: latest live/stats data."""

import time
from collections import deque

from . import gamenames
from .config import OUT
from .store import as_list, read_json


STATE_MEM = {"stats": None, "live": None, "live_t": 0, "flows": deque(maxlen=15), "rendering": None}


def tick_to_ts(tick):
    """Real time for a game tick, assuming the game ran continuously since then (it pauses when empty)."""
    live = STATE_MEM["live"]
    if live and tick is not None:
        return min(time.time(), STATE_MEM["live_t"] - max(0, live["tick"] - tick) / 60)
    return time.time()


def read_live():
    """Latest live.json (re-read at most every 2 s). live_t remembers when the game tick last moved,
    which is how the site tells "running" from "paused"."""
    if time.time() - STATE_MEM["live_t"] < 2:
        return STATE_MEM["live"]
    p = OUT / "live.json"
    d = read_json(p)
    if d:
        if not STATE_MEM["live"]:
            STATE_MEM["live_t"] = p.stat().st_mtime  # after a restart: as old as the file, not "just now"
        elif d["tick"] != STATE_MEM["live"]["tick"]:
            STATE_MEM["live_t"] = time.time()
        STATE_MEM["live"] = d
    return STATE_MEM["live"]


def display_name(name, zone=None):
    """What to call a surface: SE's zone name / a platform's name, else the planet's English name
    (Space Age "vulcanus" -> "Vulcanus"), else the raw surface name."""
    if zone:
        return zone
    return gamenames.NAMES.d["space-location-name"].get(name) and gamenames.NAMES.get(name, ("space-location-name",)) or name


def platform_place(at):
    """A platform's whereabouts from the mod ({loc} or {from, to, d}) as words, e.g. "at Nauvis"."""
    if not isinstance(at, dict):
        return None
    if at.get("loc"):
        return "at " + display_name(at["loc"])
    if at.get("from") and at.get("to"):
        pct = f" ({round(at['d'] * 100)}%)" if isinstance(at.get("d"), (int, float)) else ""
        return f"travelling {display_name(at['from'])} → {display_name(at['to'])}{pct}"
    return None


def surface_names():
    """Surface index -> {name, kind, at}, from the latest stats. `at` = where a Space Age platform is."""
    s = STATE_MEM["stats"] or {}
    out = {}
    for x in as_list(s.get("surfaces")):
        out[x["index"]] = {"name": display_name(x["name"], x.get("zone")), "kind": x.get("kind")}
        place = platform_place(x.get("at"))
        if place:
            out[x["index"]]["at"] = place
    return out
