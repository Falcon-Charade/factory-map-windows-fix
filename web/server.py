#!/usr/bin/env python3
"""Public Factorio server website: live map, timelapse, stats and event log.

Reads what the Factory Map mod (factory-map) writes to script-output/factory-map/,
renders map snapshots to PNG tiles and serves a read-only site plus a JSON API.
Nothing here can change the game or the server.

This file only starts things. The code lives in the webapp/ package:

    config.py     settings (site.json + environment variables) and paths
    store.py      the sqlite database and small file/parameter helpers
    gamenames.py  English names for items, entities, research... from locale files
    state.py      latest live data kept in memory (players, stats)
    ingest.py     background reader: picks up the mod's files every few seconds
    livemap.py    live map: keeps every explored chunk, publishes a new map version every minute
    render.py     turns chunks into PNG map tiles (live map and old whole-map snapshots)
    lookup.py     "what is at this spot" files for each map snapshot
    patches.py    ore patches: grouping, outlines, drills, history
    api_map.py    JSON for the Map tab
    api_stats.py  JSON for the Factory, Events and Players tabs
    handler.py    the HTTP server: page, static files, tiles, API routes

Run:  python3 server.py   (or the factory-map-web systemd user service)
"""
import os
import socket
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer

from webapp import gamenames
from webapp.config import BIND, OUT, PORT, SERVER_NAME, log
from webapp.handler import Handler
from webapp.ingest import ingest_loop
from webapp.lookup import migrate_lookups
from webapp.store import db, db_lock

def lan_ip():
    """This computer's address on the home network (no traffic is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("192.0.2.1", 9))
            return sk.getsockname()[0]
    except OSError:
        return None


if __name__ == "__main__":
    with db_lock:
        db().close()  # creates or upgrades the tables before anything else touches them
    log(f"names loaded: {sum(len(v) for v in gamenames.NAMES.d.values())}; reading {OUT}")
    if not OUT.is_dir():
        log("No map data yet. Install the Factory Map mod, then host a game (Multiplayer > Host) or start your server;"
            " the site fills in while someone plays. Wrong folder? Set \"factorio_data\" in site.json.")
    try:
        httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    except OSError as e:
        sys.exit(f"Can't use port {PORT} ({e.strerror}). Is the website already running? Otherwise set \"port\" in site.json.")
    threading.Thread(target=ingest_loop, daemon=True).start()
    migrate_lookups()
    url = f"http://127.0.0.1:{PORT}"
    log(f"{SERVER_NAME} website: open {url} (keep this window open)")
    if BIND in ("0.0.0.0", "") and lan_ip():
        log(f"Other devices on your network: http://{lan_ip()}:{PORT}")
    if os.environ.get("OPEN_BROWSER") == "1":
        threading.Timer(1.5, webbrowser.open, (url,)).start()
    httpd.serve_forever()
