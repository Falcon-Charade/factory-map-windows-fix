"""Live map (mod 0.4+): the mod streams changed chunks into map.jsonl; this keeps every explored chunk and
publishes a fresh map about once a minute while something changes.

How it fits the rest of the site: every publish is an ordinary snapshot with a new id (tiles, lookup, a row
in `snapshots`), so the tile cache, overlays and ore history work unchanged. Most versions are temporary
(frame=0) and are deleted ten minutes after a newer one replaces them; one version per game hour is kept
as a timelapse frame (frame=1). Unchanged map tiles are hard-linked, so a version costs almost no disk.

map.jsonl lines (each write starts with {"ep", "tick"}):
    {"ep": epoch, "tick": t}          which numbering (palette) the following lines use, and the game tick
    {"pal": [entries], "from": n}     palette entries n, n+1, ... ({"k": key, "c": colour, "l": layer, ...})
    {"s", "x", "y", "t"?, "e"}        one chunk: ground (only when it was read) and buildings, as in snapshots
    {"surf": {...}}                   a surface's name / kind / zone and explored demolisher territories
    {"gone": [s, cx, cy]}             a chunk was deleted;  {"drop": s}  a whole surface was
    {"start": t} ... {"ready": t}     a full re-read began / finished (chunks not seen in between are gone)

The mod's palette numbers depend on its save (the "epoch"); here every chunk is stored with this site's own
numbers, so loading another save or reinstalling the mod never mixes colours up.
"""

import gzip
import json
import os
import pickle
import shutil
import threading
import time
import traceback
from array import array

from .config import LOOKUP, OUT, SITE, STATE, TILES, log
from .lookup import build_lookup
from .render import draw_surface, palette_arrays, render_lock, thin_old
from .state import STATE_MEM
from .store import as_list, db, db_lock

STORE_FILE = STATE / "live" / "store.pickle.gz"
PUBLISH_EVERY = 60         # seconds: at most one new map version a minute
FRAME_TICKS = int(float(SITE.get("timelapse_frame_hours", 1)) * 216000)  # keep a timelapse frame every game hour
KEEP_REPLACED = 600        # seconds a replaced version stays readable (someone may still be looking at it)
EF = 6                     # numbers per building in a chunk line: x, y, w, h, palette, ore amount


def _empty():
    return {"palette": [], "pidx": {}, "maps": {}, "epoch": None, "tick": 0,
            "chunks": {},     # surface -> {(cx, cy): (tiles_rle with our numbers or None, array of building numbers)}
            "surfaces": {},   # surface -> {"index", "name", "kind", "zone", "parent", "terr"}
            "dirty": set(),   # surfaces changed since the last publish
            "seen": set(),    # (surface, cx, cy) read since the last {"start"}
            "ready": False}   # a full read has finished: the store holds the whole map


S = _empty()
_lock = threading.Lock()      # the reader thread and web requests (active()) share S
_loaded = False
_ingested = []                # map.*.ingest files already read, deleted once the store is saved
_last_publish = 0.0
_warned = set()


def active():
    """True once the mod's live map is in use (the website then says "updated X ago" instead of "snapshot")."""
    return S["ready"]


def _load():
    global S
    try:
        with gzip.open(STORE_FILE, "rb") as f:
            S = pickle.load(f)
        log(f"live map: loaded {sum(len(c) for c in S['chunks'].values())} chunks")
    except FileNotFoundError:
        pass
    except Exception:
        traceback.print_exc()
        log("live map: stored map unreadable, starting empty (the mod's next full read fills it)")


def _save():
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_FILE.with_suffix(".tmp")
    with gzip.open(tmp, "wb", compresslevel=1) as f:
        pickle.dump(S, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(STORE_FILE)
    # the lines are safely stored now: drop the files they came from (a crash before this re-reads them)
    for p in _ingested:
        p.unlink(missing_ok=True)
    _ingested.clear()


def _warn(what):
    if what not in _warned:
        _warned.add(what)
        log(f"live map: {what}")


def _remap_rle(t, m):
    """Ground tiles '3*12,5,...' from the mod's numbers to ours; None if a number is unknown."""
    out = []
    for part in t.split(","):
        v, star, n = part.partition("*")
        v = int(v)
        w = m.get(v) if v else 0
        if w is None:
            return None
        out.append(f"{w}*{n}" if star else str(w))
    return ",".join(out)


def _remap_ents(e, m):
    """Buildings 'x,y,w,h,p,amount,...' with our palette numbers, as a compact int array; None if unknown."""
    vals = as_list(e)
    for k in range(4, len(vals), EF):
        w = m.get(vals[k])
        if w is None:
            return None
        vals[k] = w
    return array("i", vals)


def _line(d):
    if "x" in d and "s" in d:
        m = S["maps"].get(S["epoch"])
        if m is None:
            return _warn("chunk before any palette (it is sent again after /map-refresh)")
        si, key = d["s"], (d["x"], d["y"])
        cs = S["chunks"].setdefault(si, {})
        ents = _remap_ents(d.get("e"), m)
        tiles = _remap_rle(d["t"], m) if d.get("t") else (cs[key][0] if key in cs else None)
        if ents is None or (d.get("t") and tiles is None):
            return _warn("chunk with an unknown palette number skipped (fixed by the next read of that chunk)")
        S["seen"].add((si,) + key)
        if cs.get(key) != (tiles, ents):  # the sweep mostly finds chunks unchanged: no new map for those
            cs[key] = (tiles, ents)
            S["dirty"].add(si)
    elif "ep" in d:
        S["epoch"] = d["ep"]
        S["tick"] = d.get("tick") or S["tick"]
    elif "pal" in d:
        m = S["maps"].setdefault(S["epoch"], {})
        for i, e in enumerate(as_list(d["pal"]), start=int(d.get("from") or 1)):
            k = e.get("k")
            w = S["pidx"].get(k)
            if w is None:
                S["palette"].append(dict(e))
                w = len(S["palette"])
                S["pidx"][k] = w
            else:
                S["palette"][w - 1].update(e)
            m[i] = w
        while len(S["maps"]) > 8:  # numbering of saves long gone
            S["maps"].pop(next(iter(S["maps"])))
    elif "surf" in d:
        info = d["surf"]
        S["surfaces"][info["index"]] = info
        S["dirty"].add(info["index"])
    elif "gone" in d:
        si, cx, cy = d["gone"]
        S["chunks"].get(si, {}).pop((cx, cy), None)
        S["dirty"].add(si)
    elif "drop" in d:
        S["chunks"].pop(d["drop"], None)
        S["surfaces"].pop(d["drop"], None)
        S["dirty"].add(d["drop"])
    elif "start" in d:
        S["seen"] = set()
    elif "ready" in d:
        # a full read finished: whatever it didn't see isn't explored any more (another save, new map)
        for si in list(S["chunks"]):
            cs = S["chunks"][si]
            for key in [k for k in cs if (si,) + k not in S["seen"]]:
                del cs[key]
            if not cs:
                del S["chunks"][si]
        S["dirty"].update(S["chunks"])
        S["seen"] = set()
        if not S["ready"]:
            log("live map: first full read done, the map now updates by itself")
        S["ready"] = True


def ingest():
    """Read whatever the mod wrote since last time (called every few seconds by the reader thread)."""
    global _loaded
    with _lock:
        if not _loaded:
            _loaded = True
            _load()
        src = OUT / "map.jsonl"
        if src.exists():
            try:
                src.rename(OUT / f"map.{int(time.time() * 1000)}.ingest")  # the mod starts a new file on its next write
            except OSError:
                pass
        for p in sorted(OUT.glob("map.*.ingest")):
            if p in _ingested:
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    try:
                        _line(d)
                    except Exception:
                        traceback.print_exc()
            _ingested.append(p)


_last_save = time.time()


def tidy():
    """Save the store now and then even when nothing changed, so files the mod already wrote get deleted
    (a quiet base could otherwise pile up the sweep's unchanged re-reads for hours)."""
    global _last_save
    if _ingested and (len(_ingested) > 200 or time.time() - _last_save >= 600) and not S["dirty"]:
        with _lock:
            _save()
        _last_save = time.time()


def due():
    """Time to publish: the full read is done, something changed, and the last publish was a minute ago."""
    return S["ready"] and bool(S["dirty"]) and time.time() - _last_publish >= PUBLISH_EVERY


def publish():
    """Draw the current map as a new snapshot version, keep it as a timelapse frame if one is due,
    and delete replaced versions nobody needs any more."""
    global _last_publish
    if not render_lock.acquire(blocking=False):
        return
    try:
        STATE_MEM["rendering"] = "live map"
        _last_publish = time.time()
        _publish()
    except Exception:
        traceback.print_exc()
    finally:
        STATE_MEM["rendering"] = None
        render_lock.release()


def _publish():
    global _last_save
    t0 = time.time()
    with _lock:  # copy what's needed so the reader can go on meanwhile
        tick = S["tick"]
        palette = [dict(p) for p in S["palette"]]
        surfaces = {si: dict(S["surfaces"].get(si) or {"index": si, "name": str(si)}) for si in S["chunks"]}
        chunks = {si: {k: (t or "0*1024", e) for k, (t, e) in cs.items()} for si, cs in S["chunks"].items() if cs}
        S["dirty"] = set()
    with db_lock:
        con = db()
        sid = tick
        while con.execute("SELECT 1 FROM snapshots WHERE id=?", (sid,)).fetchone():
            sid += 1
        prev = (con.execute("SELECT id FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone() or (None,))[0]
        last_frame = con.execute("SELECT MAX(tick) FROM snapshots WHERE frame=1 AND tick<=?", (tick,)).fetchone()[0]
        con.close()
    frame = last_frame is None or tick - last_frame >= FRAME_TICKS
    arrays = palette_arrays(palette)
    rows = []
    for si, cs in chunks.items():
        info = surfaces[si]
        z, bounds, count = draw_surface(sid, si, cs, palette, arrays, EF, prev)
        try:
            build_lookup(sid, si, cs, palette, EF, as_list(info.get("terr")))
        except Exception:
            traceback.print_exc()
        rows.append((sid, si, time.time(), tick, info.get("name"), info.get("kind"), info.get("zone"), z,
                     json.dumps(bounds), count, 1 if frame else 0))
    now = time.time()
    with db_lock:
        con = db()
        con.executemany("INSERT OR REPLACE INTO snapshots (id, surface, ts, tick, name, kind, zone, minz, bounds, tiles, frame) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        old = [r[0] for r in con.execute("SELECT DISTINCT id FROM snapshots WHERE frame=0 AND id!=? AND ts<?",
                                         (sid, now - KEEP_REPLACED))]
        for o in old:
            con.execute("DELETE FROM snapshots WHERE id=?", (o,))
            con.execute("DELETE FROM patch_hist WHERE sid=?", (o,))
        con.commit()
        thin_old(con)
        con.close()
    for o in old:
        shutil.rmtree(TILES / str(o), ignore_errors=True)
        shutil.rmtree(LOOKUP / str(o), ignore_errors=True)
    # a 0.3 snapshot that was still being captured when the mod was updated never finishes
    for d in OUT.glob("snap-*"):
        if not (d / "done.json").exists():
            shutil.rmtree(d, ignore_errors=True)
    with _lock:
        _save()
    _last_save = time.time()
    n = sum(len(c) for c in chunks.values())
    log(f"live map {sid}{' (timelapse frame)' if frame else ''}: {n} chunks on {len(chunks)} surfaces "
        f"in {time.time() - t0:.1f}s, {len(old)} old versions removed")
