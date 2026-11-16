"""The web server: serves the page, static files, map tiles and the JSON API."""

import hashlib
import html
import json
import zipfile
import re
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from .api_map import api_at, api_attack_map, api_changes, api_live, api_maps, api_nests, api_patch_info, api_patches, api_pollution, api_structures
from .api_stats import api_events, api_history, api_items, api_overview
from .store import db, db_lock
from .config import COPYRIGHT, FACTORIO, HERE, MOD_NAME, SERVER_NAME, SERVER_TAGLINE, SOURCE_URL, TILES


def asset_version():
    """Short fingerprint of the site's own CSS/JS files. index.html links them as app.css?v=<this>,
    so after an update browsers fetch the new files instead of using a cached copy."""
    files = sorted((HERE / "static").glob("css/*.css")) + sorted((HERE / "static").glob("js/*.js"))
    stamp = "|".join(f"{p.name}:{p.stat().st_mtime_ns}:{p.stat().st_size}" for p in files)
    return hashlib.sha1(stamp.encode()).hexdigest()[:10]


_titles = {}


def mod_title(z):
    """The mod's display name ("Factory Map") from info.json inside its zip; the internal name if unreadable."""
    key = (z, z.stat().st_mtime)
    if key not in _titles:
        title = MOD_NAME
        try:
            with zipfile.ZipFile(z) as zf:
                info = next(n for n in zf.namelist() if n.count("/") == 1 and n.endswith("/info.json"))
                title = json.loads(zf.read(info)).get("title") or MOD_NAME
        except Exception:
            pass
        _titles[key] = title
    return _titles[key]


def index_page():
    """index.html with the per-site values filled in: server name, tagline, footer and asset version."""
    page = (HERE / "static" / "index.html").read_text(encoding="utf-8").replace("{{V}}", asset_version())
    zips = sorted((FACTORIO / "mods").glob(f"{MOD_NAME}_*.zip"), key=lambda p: p.stat().st_mtime)
    foot = {"copyright": COPYRIGHT, "source": SOURCE_URL, "mod": mod_title(zips[-1]) if zips else MOD_NAME,
            "version": zips[-1].stem[len(MOD_NAME) + 1:] if zips else ""}
    page = page.replace("{{FOOTER_JSON}}", json.dumps(foot).replace("<", "\\u003c"))
    tag = f"<small>{html.escape(SERVER_TAGLINE)}</small>" if SERVER_TAGLINE else ""
    return (page.replace("{{TAGLINE}}", tag).replace("{{SERVER}}", html.escape(SERVER_NAME))
            .replace("{{MOD}}", html.escape(MOD_NAME))).encode()


STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
                ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".map": "application/json",
                ".webmanifest": "application/manifest+json"}
TILE_RE = re.compile(r"^/tiles/(\d+)/(\d+)/(-?\d+)/(-?\d+)_(-?\d+)\.png$")
STATIC_RE = re.compile(r"^/static/([\w\-.]+(?:/[\w\-.]+)?)$")



def newest_snapshot(si):
    with db_lock:
        con = db()
        r = con.execute("SELECT id FROM snapshots WHERE surface=? ORDER BY ts DESC LIMIT 1", (si,)).fetchone()
        con.close()
    return r and r[0]


class Handler(BaseHTTPRequestHandler):
    """One web request. The site is read-only: GET and HEAD are answered, anything else is refused."""
    server_version = "factorio-web"
    sys_version = ""

    def log_message(self, *a):
        pass

    def send_body(self, body, ctype, cache="no-cache", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, obj):
        self.send_body(json.dumps(obj, separators=(",", ":")).encode(), "application/json")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self.send_body(index_page(), STATIC_TYPES[".html"])
            m = TILE_RE.match(u.path)
            if m:
                p = TILES / m.group(1) / m.group(2) / m.group(3) / f"{m.group(4)}_{m.group(5)}.png"
                if p.is_file():
                    return self.send_body(p.read_bytes(), "image/png", "public, max-age=31536000, immutable")
                if not (TILES / m.group(1)).is_dir():
                    # a page left open a long time can still ask for a live-map version that was replaced and
                    # deleted: show the same spot from the newest version instead of a black hole
                    newest = newest_snapshot(int(m.group(2)))
                    q = newest and TILES / str(newest) / m.group(2) / m.group(3) / f"{m.group(4)}_{m.group(5)}.png"
                    if q and q.is_file():
                        return self.send_body(q.read_bytes(), "image/png", "no-cache")
                    return self.send_body(b"", "image/png", "no-cache", 204)
                return self.send_body(b"", "image/png", "public, max-age=600", 204)
            m = STATIC_RE.match(u.path)
            if m and ".." not in m.group(1):
                p = HERE / "static" / m.group(1)
                if p.suffix == ".webmanifest" and p.is_file():
                    body = p.read_text(encoding="utf-8").replace("{{SERVER}}", json.dumps(SERVER_NAME)[1:-1]).encode()
                    return self.send_body(body, STATIC_TYPES[".webmanifest"], "public, max-age=3600")
                if p.is_file():
                    return self.send_body(p.read_bytes(), STATIC_TYPES.get(p.suffix, "application/octet-stream"),
                                          "public, max-age=3600")
            routes = {"/api/overview": lambda: api_overview(), "/api/live": lambda: api_live(),
                      "/api/maps": lambda: api_maps(), "/api/events": lambda: api_events(q),
                      "/api/history": lambda: api_history(q), "/api/items": lambda: api_items(q),
                      "/api/at": lambda: api_at(q), "/api/patches": lambda: api_patches(q),
                      "/api/pollution": lambda: api_pollution(q), "/api/changes": lambda: api_changes(q),
                      "/api/attackmap": lambda: api_attack_map(q), "/api/nests": lambda: api_nests(q),
                      "/api/patchinfo": lambda: api_patch_info(q),
                      "/api/structures": lambda: api_structures(q)}
            if u.path in routes:
                return self.send_json(routes[u.path]())
            self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self.send_error(500)

    def do_POST(self):
        self.send_error(405)
