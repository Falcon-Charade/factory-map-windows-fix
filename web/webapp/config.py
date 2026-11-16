"""Settings and paths. Values come from site.json next to server.py; environment variables override them."""

import json
import os
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent.parent  # the web/ folder: server.py, site.json, static/, cache/
# Per-site settings live in web/site.json (see site.example.json); env vars override them.
try:
    SITE = json.loads((HERE / "site.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    SITE = {}


def setting(key, env, default=""):
    """A site setting: environment variable first, then site.json, then the default."""
    return os.environ.get(env, SITE.get(key, default))


FACTORIO = Path(os.environ.get("FACTORIO_DATA", HERE.parent / "data"))
MOD_NAME = setting("mod_name", "MOD_NAME", "factory-map")  # the exporter mod's internal name
OUT = Path(os.environ.get("FACTORIO_OUT", FACTORIO / "script-output" / MOD_NAME))
STATE = Path(os.environ.get("WEB_STATE", HERE / "state"))
TILES = STATE / "tiles"
DB_PATH = STATE / "web.db"
BIND = os.environ.get("WEB_BIND", "127.0.0.1")
PORT = int(os.environ.get("WEB_PORT", "8480"))
SERVER_NAME = setting("server_name", "SERVER_NAME", "Factorio server")
SERVER_TAGLINE = setting("tagline", "SERVER_TAGLINE")    # small text under the name, e.g. the modpack; "" hides it
COPYRIGHT = setting("copyright", "SITE_COPYRIGHT")      # footer on the Players tab; "" hides it
SOURCE_URL = setting("source_url", "SITE_SOURCE_URL")   # e.g. the GitHub repo; "" hides the link
KEEP_FULL_SNAPSHOTS = int(os.environ.get("KEEP_FULL_SNAPSHOTS", "12"))
FLOW_EVERY_TICKS = 36000  # store production totals every 10 game minutes
TICKS_PER_DAY = 25000

STATE.mkdir(parents=True, exist_ok=True)
TILES.mkdir(parents=True, exist_ok=True)


def log(*a):
    """Print a timestamped line to the service log (journalctl --user -u factory-map-web)."""
    print(time.strftime("%H:%M:%S"), *a, flush=True)


LOOKUP = STATE / "lookup"
