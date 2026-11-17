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


def _factorio_data():
    """FACTORIO_DATA / site.json "factorio_data" if set, else data/ next to web/ (server layouts), else the
    game's own folder found automatically (Windows %APPDATA%\\Factorio, macOS Application Support, Linux ~/.factorio)."""
    given = setting("factorio_data", "FACTORIO_DATA")
    if given:
        return Path(given).expanduser()
    if (HERE.parent / "data" / "mods").is_dir():
        return HERE.parent / "data"
    from .findgame import user_data
    return user_data() or HERE.parent / "data"


FACTORIO = _factorio_data()
MOD_NAME = setting("mod_name", "MOD_NAME", "factory-map")  # the exporter mod's internal name
OUT = Path(os.environ.get("FACTORIO_OUT", FACTORIO / "script-output" / MOD_NAME))
STATE = Path(os.environ.get("WEB_STATE", HERE / "state"))
TILES = STATE / "tiles"
DB_PATH = STATE / "web.db"
BIND = setting("bind", "WEB_BIND", "127.0.0.1")   # "0.0.0.0" = other devices on your network can open it
PORT = int(setting("port", "WEB_PORT", 8480))
SERVER_NAME = setting("server_name", "SERVER_NAME", "Factorio server")
SERVER_TAGLINE = setting("tagline", "SERVER_TAGLINE")    # small text under the name, e.g. the modpack; "" hides it
COPYRIGHT = setting("copyright", "SITE_COPYRIGHT")      # footer on the Players tab; "" hides it
SOURCE_URL = setting("source_url", "SITE_SOURCE_URL")   # e.g. the GitHub repo; "" hides the link
KEEP_FULL_SNAPSHOTS = int(os.environ.get("KEEP_FULL_SNAPSHOTS", "12"))
FLOW_EVERY_TICKS = 36000  # store production totals every 10 game minutes
TICKS_PER_DAY = 25000

STATE.mkdir(parents=True, exist_ok=True)
TILES.mkdir(parents=True, exist_ok=True)


def _ensure_locale():
    """English item/building names: copied once from the game's install folder (found automatically, or
    site.json "factorio_install" / FACTORIO_INSTALL). Without them the site shows tidied-up internal names."""
    dst = HERE / "cache" / "locale"
    from .findgame import copy_locale, install_data
    data = install_data(setting("factorio_install", "FACTORIO_INSTALL") or None)
    have = dst / "base" / "base.cfg"
    if have.exists() and (not data or have.stat().st_mtime >= (data / "base/locale/en/base.cfg").stat().st_mtime):
        return  # already copied, and the game hasn't been updated since
    if data:
        print(time.strftime("%H:%M:%S"), "English names copied from", data, ":", ", ".join(copy_locale(data, dst)), flush=True)
    else:
        print(time.strftime("%H:%M:%S"), "Factorio install folder not found: item names stay as internal names. "
              'Set "factorio_install" in site.json to fix that.', flush=True)


_ensure_locale()


def log(*a):
    """Print a timestamped line to the service log (journalctl --user -u factory-map-web)."""
    print(time.strftime("%H:%M:%S"), *a, flush=True)


LOOKUP = STATE / "lookup"
