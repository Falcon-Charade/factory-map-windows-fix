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
import threading
from http.server import ThreadingHTTPServer

from webapp import gamenames
from webapp.config import BIND, OUT, PORT, SERVER_NAME, log
from webapp.handler import Handler
from webapp.ingest import ingest_loop
from webapp.lookup import migrate_lookups
from webapp.store import db, db_lock

if __name__ == "__main__":
    with db_lock:
        db().close()  # creates or upgrades the tables before anything else touches them
    log(f"names loaded: {sum(len(v) for v in gamenames.NAMES.d.values())}; reading {OUT}")
    threading.Thread(target=ingest_loop, daemon=True).start()
    migrate_lookups()
    log(f"{SERVER_NAME} web on http://{BIND}:{PORT}")
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
