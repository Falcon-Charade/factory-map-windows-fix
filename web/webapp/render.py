"""Turning a finished map capture from the mod into PNG map tiles (one renderer at a time)."""

import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
import traceback

import numpy as np
from PIL import Image

from .config import KEEP_FULL_SNAPSHOTS, OUT, TILES, log
from .lookup import build_lookup
from .state import STATE_MEM
from .store import as_list, db, db_lock, read_json


DEFAULT_TYPE_COLORS = {
    "straight-rail": "8e9aa3", "curved-rail-a": "8e9aa3", "curved-rail-b": "8e9aa3", "half-diagonal-rail": "8e9aa3",
    "legacy-straight-rail": "8e9aa3", "legacy-curved-rail": "8e9aa3", "rail-signal": "c7d2da",
    "rail-chain-signal": "c7d2da", "transport-belt": "c9a43c", "underground-belt": "c9a43c", "splitter": "c9a43c",
    "loader": "c9a43c", "loader-1x1": "c9a43c", "linked-belt": "c9a43c", "pipe": "3a6ea5", "pipe-to-ground": "3a6ea5",
    "pump": "3a6ea5", "storage-tank": "3a6ea5", "wall": "c4cccc", "gate": "c4cccc", "electric-pole": "a5a5a5",
    "locomotive": "e0703a", "cargo-wagon": "e0703a", "fluid-wagon": "e0703a", "artillery-wagon": "e0703a",
    "car": "e0703a", "spider-vehicle": "e0703a",
}
DEFAULT_FRIENDLY = "4f86b0"
# map colours that read badly on the website (SE core seams are pure black and look like holes)
KEY_COLOR_OVERRIDES = [(re.compile(r"^r:se-core-fragment"), "e040fb")]


def hex_rgba(h, a=255):
    """'rrggbb' or 'rrggbbaa' -> (r, g, b, a)."""
    h = (h or DEFAULT_FRIENDLY)[:6]
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def decode_rle(s):
    """Unpack the mod's run-length tile list ('3*12,5,...') into one palette index per tile."""
    out = []
    for part in s.split(","):
        if "*" in part:
            v, n = part.split("*")
            out.extend([int(v)] * int(n))
        else:
            out.append(int(part))
    return out


def palette_arrays(palette):
    """Colours (RGBA per palette number), draw layers and the set of rail numbers for a palette."""
    pal = np.zeros((len(palette) + 1, 4), dtype=np.uint8)
    layer = np.zeros(len(palette) + 1, dtype=np.int8)
    for i, p in enumerate(palette, start=1):
        c = p.get("c") or DEFAULT_TYPE_COLORS.get(p.get("t"), DEFAULT_FRIENDLY)
        for rx, col in KEY_COLOR_OVERRIDES:
            if rx.match(p.get("k", "")):
                c = col
        pal[i] = hex_rgba(c)
        layer[i] = p.get("l", 3)
    # rails drawn before other buildings so stations/signals stay visible
    rails = {i for i, p in enumerate(palette, start=1) if "rail" in (p.get("t") or "") and "signal" not in (p.get("t") or "")}
    return pal, layer, rails


def draw_surface(sid, si, cs, palette, arrays, ef, prev):
    """Draw one surface's chunks {(cx, cy): (tiles_rle, [entity numbers])} into tiles/<sid>/<si>/<z>/<x>_<y>.png,
    every zoom level. Tiles that look the same as in snapshot `prev` are hard-linked to its files.
    Returns (lowest zoom level, bounds in game tiles, number of tiles)."""
    pal, layer, rails = arrays
    out_root = TILES / str(sid) / str(si)
    prev_root = TILES / str(prev) / str(si) if prev is not None else None
    # native level 0: 256 px = 8x8 chunks, 1 px per game tile
    by_tile = {}
    for (cx, cy) in cs:
        by_tile.setdefault((cx // 8, cy // 8), []).append((cx, cy))
    level = {}
    for (tx, ty), members in by_tile.items():
        img = np.zeros((256, 256, 4), dtype=np.uint8)
        ox, oy = tx * 256, ty * 256
        for (cx, cy) in members:
            vals = decode_rle(cs[(cx, cy)][0] or "0*1024")[:1024]
            tiles = np.array(vals + [0] * (1024 - len(vals)), dtype=np.int32).reshape(32, 32)
            tiles[tiles > len(palette)] = 0
            px, py = cx * 32 - ox, cy * 32 - oy
            img[py:py + 32, px:px + 32] = pal[tiles]
        # entities from this tile's chunks plus a 1-chunk margin, so buildings on tile borders aren't cut
        rects = []
        for cx in range(tx * 8 - 1, tx * 8 + 9):
            for cy in range(ty * 8 - 1, ty * 8 + 9):
                ch = cs.get((cx, cy))
                if not ch:
                    continue
                e = ch[1]
                bx, by = cx * 32 - ox, cy * 32 - oy
                for k in range(0, len(e) - ef + 1, ef):
                    idx = e[k + 4]
                    if 0 < idx <= len(palette):
                        lv = layer[idx] if idx not in rails else 2.5
                        rects.append((lv, bx + e[k], by + e[k + 1], e[k + 2], e[k + 3], idx))
        rects.sort(key=lambda r: r[0])
        for _, x, y, w, h, idx in rects:
            x1, y1, x2, y2 = max(0, x), max(0, y), min(256, x + w), min(256, y + h)
            if x1 < x2 and y1 < y2:
                img[y1:y2, x1:x2] = pal[idx]
        level[(tx, ty)] = Image.fromarray(img, "RGBA")
    xs = [k[0] for k in level]
    ys = [k[1] for k in level]
    bounds = [min(xs) * 256, min(ys) * 256, (max(xs) + 1) * 256, (max(ys) + 1) * 256]
    z, count = 0, 0
    while True:
        zdir = out_root / str(z)
        zdir.mkdir(parents=True, exist_ok=True)
        for (tx, ty), im in level.items():
            buf = io.BytesIO()
            im.save(buf, "PNG", compress_level=6)
            data = buf.getvalue()
            dst = zdir / f"{tx}_{ty}.png"
            src_prev = prev_root / str(z) / f"{tx}_{ty}.png" if prev_root else None
            if src_prev and src_prev.exists() and src_prev.stat().st_size == len(data) \
                    and hashlib.md5(src_prev.read_bytes()).digest() == hashlib.md5(data).digest():
                try:
                    os.link(src_prev, dst)  # unchanged area: share the file with the previous snapshot
                except OSError:
                    dst.write_bytes(data)
            else:
                dst.write_bytes(data)
            count += 1
        if (max(xs) - min(xs) <= 1 and max(ys) - min(ys) <= 1) or z <= -12:
            break
        parent = {}
        for (tx, ty), im in level.items():
            parent.setdefault((tx // 2, ty // 2), []).append(((tx % 2) * 256, (ty % 2) * 256, im))
        level = {}
        for k, parts in parent.items():
            big = Image.new("RGBA", (512, 512))
            for x, y, im in parts:
                big.paste(im, (x, y))
            level[k] = big.reduce(2)
        xs = [k[0] for k in level]
        ys = [k[1] for k in level]
        z -= 1
    return z, bounds, count


def render_snapshot(snap_dir):
    """Draw one finished capture (mod 0.3 and older) into map tiles, write its lookup files and record it."""
    meta = read_json(snap_dir / "done.json")
    if not meta:
        return
    sid = meta["id"]
    t0 = time.time()
    captured = (snap_dir / "done.json").stat().st_mtime  # when the game finished the capture (wall clock)
    palette = as_list(meta.get("palette"))
    arrays = palette_arrays(palette)

    surfaces = as_list(meta.get("surfaces"))
    ef = int(meta.get("ef") or 5)
    chunks = {}  # surface -> {(cx,cy): (tiles, ents)}
    if not (snap_dir / "chunks.jsonl").exists():
        log(f"{snap_dir.name}: nothing explored yet, skipped")
        shutil.rmtree(snap_dir, ignore_errors=True)
        return
    with open(snap_dir / "chunks.jsonl", encoding="utf-8") as f:
        for line in f:
            try:
                c = json.loads(line)
            except ValueError:
                continue
            chunks.setdefault(c["s"], {})[(c["x"], c["y"])] = (c["t"], as_list(c.get("e")))
    got, want = sum(len(v) for v in chunks.values()), meta.get("chunks") or 0
    if want and got < want * 0.98:
        # part of the capture went missing (e.g. its folder was cleaned up mid-way): don't publish a map with holes
        log(f"{snap_dir.name}: only {got} of {want} chunks, skipped")
        snap_dir.rename(snap_dir.with_name("incomplete-" + snap_dir.name))
        return

    prev = None
    with db_lock:
        con = db()
        # newest by capture time, not by game tick: ticks go backwards when an older save is loaded
        r = con.execute("SELECT id FROM snapshots WHERE id != ? ORDER BY ts DESC LIMIT 1", (sid,)).fetchone() or (None,)
        prev = r[0]
        con.close()

    done_rows = []
    for s in surfaces:
        si = s["index"]
        cs = chunks.get(si)
        if not cs:
            continue
        z, bounds, count = draw_surface(sid, si, cs, palette, arrays, ef, prev)
        done_rows.append((sid, si, captured, meta.get("tick"), s.get("name"), s.get("kind"),
                          s.get("zone"), z, json.dumps(bounds), count))
        try:
            build_lookup(sid, si, cs, palette, ef, as_list(s.get("terr")))
        except Exception:
            traceback.print_exc()
        log(f"snapshot {sid} surface {si} ({s.get('zone') or s.get('name')}): {len(cs)} chunks, {count} tiles, minz {z}")

    with db_lock:
        con = db()
        con.executemany("INSERT OR REPLACE INTO snapshots (id, surface, ts, tick, name, kind, zone, minz, bounds, tiles) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)", done_rows)
        con.execute("INSERT INTO events (ts, tick, type, data) VALUES (?,?,?,?)",
                    (time.time(), meta.get("tick"), "snapshot",
                     json.dumps({"type": "snapshot", "id": sid, "chunks": meta.get("chunks")})))
        con.commit()
        thin_old(con)
        con.close()
    shutil.rmtree(snap_dir, ignore_errors=True)
    log(f"snapshot {sid} rendered in {time.time() - t0:.1f}s")


def thin_old(con):
    """Older snapshots keep only the zoomed-out levels (enough for the timelapse)."""
    ids = [r[0] for r in con.execute("SELECT id FROM snapshots WHERE thinned=0 AND frame=1 GROUP BY id ORDER BY MAX(ts) DESC")]
    for sid in ids[KEEP_FULL_SNAPSHOTS:]:
        for d in (TILES / str(sid)).glob("*/0"):
            shutil.rmtree(d, ignore_errors=True)
        for d in (TILES / str(sid)).glob("*/-1"):
            shutil.rmtree(d, ignore_errors=True)
        con.execute("UPDATE snapshots SET thinned=1 WHERE id=?", (sid,))
    con.commit()


render_lock = threading.Lock()


def render_pending():
    """Render every finished capture waiting in the output folder (never two renders at once)."""
    if not render_lock.acquire(blocking=False):
        return  # another render is still running
    try:
        _render_pending()
    finally:
        render_lock.release()


def _render_pending():
    """render_pending's work, oldest capture first (called with render_lock held)."""
    def finished_at(d):  # oldest capture first (by when it finished, not its game tick)
        try:
            return (d / "done.json").stat().st_mtime
        except OSError:
            return float("inf")
    for d in sorted(OUT.glob("snap-*"), key=finished_at):
        if (d / "done.json").exists():
            STATE_MEM["rendering"] = d.name
            try:
                render_snapshot(d)
            except Exception:
                traceback.print_exc()
                d.rename(d.with_name("failed-" + d.name))
            finally:
                STATE_MEM["rendering"] = None
        elif time.time() - d.stat().st_mtime > 30 * 86400:
            # abandoned (mod removed / save rolled back). Not sooner: a capture can sit half-done for days
            # while nobody plays (the game pauses) and then finish when someone joins.
            shutil.rmtree(d, ignore_errors=True)
