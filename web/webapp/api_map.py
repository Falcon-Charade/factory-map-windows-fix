"""JSON API for the Map tab: what's at a spot, ore patches, live positions, overlays (pollution, biters, ...)."""

import json
import time

from . import gamenames, livemap
from .config import OUT, TICKS_PER_DAY
from .lookup import load_lookup, tile_name_at
from .patches import cluster_tiles, hull, patch_drills
from .state import STATE_MEM, display_name, read_live, surface_names, tick_to_ts
from .store import as_list, db, db_lock, qint, read_json


def api_at(q):
    """What is at a map spot (/api/at): the building or ore there, the ground tile and the nearest name tag."""
    sid, si, x, y = (qint(q, k, None) for k in ("snap", "s", "x", "y"))
    if None in (sid, si, x, y):
        return {"error": "bad request"}
    lk = load_lookup(sid, si)
    if not lk:
        return {"error": "no data for this snapshot"}
    cx, cy = x // 32, y // 32
    best, res = None, None
    for ox in (-1, 0, 1):
        for oy in (-1, 0, 1):
            ch = lk["chunks"].get(f"{cx + ox},{cy + oy}")
            for r in (ch or {}).get("e", ()):
                if r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]:
                    key = lk["keys"][r[4] - 1]
                    if key.startswith("r:"):
                        res = r
                    elif best is None or (key.startswith("e:") and not lk["keys"][best[4] - 1].startswith("e:")):
                        best = r
    if res is None:
        r = max(1, min(48, qint(q, "r", 3)))
        bd = r * r + 1
        for ox in range(-(r // 32) - 1, r // 32 + 2):
            for oy in range(-(r // 32) - 1, r // 32 + 2):
                for rec in (lk["chunks"].get(f"{cx + ox},{cy + oy}") or {}).get("e", ()):
                    if lk["keys"][rec[4] - 1].startswith("r:"):
                        dx = max(rec[0] - x, 0, x - (rec[0] + rec[2] - 1))
                        dy = max(rec[1] - y, 0, y - (rec[1] + rec[3] - 1))
                        d = dx * dx + dy * dy
                        if d < bd:
                            bd, res = d, rec
    out = {"x": x, "y": y}
    ch = lk["chunks"].get(f"{cx},{cy}")
    if ch:
        t = tile_name_at(ch["t"], x - cx * 32, y - cy * 32, lk["keys"])
        if t:
            out["tile"] = gamenames.NAMES.get(t, ("tile-name",))
    if best:
        key = lk["keys"][best[4] - 1]
        out["entity"] = {"name": key[2:], "label": gamenames.NAMES.get(key[2:], ("entity-name", "item-name")),
                         "kind": {"e": "building", "x": "enemy"}.get(key[0], key[0]), "type": lk["types"][best[4] - 1],
                         "bbox": [best[0], best[1], best[0] + best[2], best[1] + best[3]]}
    if res is not None:
        out["resource"] = {"name": lk["keys"][res[4] - 1][2:], "amount": res[5]}
        if res[6] >= 0:
            out["patch"] = lk["patches"][res[6]]
    out["has_amounts"] = lk.get("has_amounts")
    return out


def api_patches(q):
    """All ore patches of a snapshot (/api/patches): outline, amount, colour and label position."""
    lk = load_lookup(qint(q, "snap", 0), qint(q, "s", 1))
    if not lk:
        return {"patches": [], "has_amounts": None}
    dr = patch_drills(lk)
    return {"patches": [dict(p, drills=dr.get(p["id"], 0)) for p in lk["patches"]], "has_amounts": lk.get("has_amounts")}


def api_patch_info(q):
    """Drills on one patch and how its amount changed over the earlier map snapshots."""
    sid, si, pid = qint(q, "snap", 0), qint(q, "s", 1), qint(q, "id", -1)
    lk = load_lookup(sid, si)
    if not lk or not 0 <= pid < len(lk["patches"]):
        return {"error": "no such patch"}
    p = lk["patches"][pid]
    out = {"drills": patch_drills(lk).get(pid, 0), "hist": []}
    if not lk.get("has_amounts") or p.get("inf"):
        return out
    with db_lock:
        con = db()
        rows = con.execute("SELECT h.sid, s.tick, h.data FROM patch_hist h JOIN snapshots s ON s.id = h.sid AND s.surface = h.surface "
                           "WHERE h.surface = ? AND s.ts <= (SELECT ts FROM snapshots WHERE id = ? AND surface = ?) "
                           "ORDER BY s.ts", (si, sid, si)).fetchall()
        con.close()
    x0, y0, x1, y1 = p["bbox"]
    pad = 8
    # this patch's neighbours of the same resource: an older patch belongs to whichever current patch is nearest,
    # so two patches side by side don't both claim (and double count) the same old amounts
    mates = [(q["x"], q["y"], i) for i, q in enumerate(lk["patches"]) if q["name"] == p["name"]]

    def nearest(cx, cy):
        return min(mates, key=lambda m: (m[0] - cx) ** 2 + (m[1] - cy) ** 2)[2]

    for osid, tick, data in rows:  # the same patch in an older snapshot: same resource, centre inside this one's box
        amt = 0
        for name, bb, a, _ in json.loads(data):
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            if name == p["name"] and x0 - pad <= cx <= x1 + pad and y0 - pad <= cy <= y1 + pad and nearest(cx, cy) == pid:
                amt += a
        if amt:
            out["hist"].append({"tick": tick, "day": tick // TICKS_PER_DAY + 1, "amount": amt})
    h = out["hist"]
    if len(h) >= 2 and not p["fluid"]:
        now = h[-1]
        # rate from the most recent stretch of at least 30 min of play, so newly placed drills show up quickly
        # (an average over a whole day lags far behind a growing base). A total that went UP means more of
        # the patch got explored, not mined, so such a stretch can't be used.
        older = [o for o in h[:-1] if now["tick"] - o["tick"] >= 108000]
        if older:
            o = older[-1]
            hours = (now["tick"] - o["tick"]) / 216000
            used = o["amount"] - now["amount"]
            if used > 0 and hours > 0:
                out["per_hour"] = used / hours
                out["hours_left"] = now["amount"] / out["per_hour"]
                out["rate_from_day"] = o["tick"] // TICKS_PER_DAY + 1
                out["rate_hours"] = round(hours, 1)
        first = h[0]
        if first["amount"] > now["amount"]:
            out["down_pct"] = (1 - now["amount"] / first["amount"]) * 100
            out["since_day"] = first["day"]
    return out


def api_live():
    """Live positions (/api/live): players, vehicles, trains, map tags, stations and recent attacks."""
    live = read_live() or {}
    sn = surface_names()
    players = live.get("players") or []
    trains = live.get("trains") or []
    fresh = time.time() - (STATE_MEM["live_t"] or 0) < 30
    if not fresh:
        players = []  # the server only pauses when it's empty; old positions would be ghosts
    pw = (STATE_MEM["stats"] or {}).get("power") or {}
    power_low = bool(fresh and pw.get("probes") and pw.get("low", 0) >= max(3, pw["probes"] * 0.25))
    return {"tick": live.get("tick"), "fresh": fresh, "power_low": power_low,
            "players": [dict(pl, vl=gamenames.NAMES.get(pl["v"], ("entity-name",)) if pl.get("v") else None) for pl in as_list(players)],
            "trains": as_list(trains), "surfaces": sn,
            "vehicles": [dict(v, label=v.get("l") or gamenames.NAMES.get(v.get("n"), ("entity-name",))) for v in as_list(live.get("vehicles"))],
            "tags": [{"s": t.get("s"), "x": t.get("x"), "y": t.get("y"), "by": t.get("by"),
                      "text": gamenames.NAMES.rich(t.get("t")) or (gamenames.NAMES.get(t["i"], ("virtual-signal-name", "item-name", "entity-name", "fluid-name")) if t.get("i") else ""),
                      "icon_only": not t.get("t"),
                      # Space Exploration marks where a player died and their corpse (with items) is still lying
                      "corpse": "corpse" in (t.get("i") or ""),
                      # tags Space Exploration places by itself (core seams, planet name): not player tags
                      "auto": not t.get("by") and (t.get("i") or "") in ("se-core-seam", "se-planet", "se-moon", "se-star", "se-asteroid-belt", "se-asteroid-field")}
                     for t in as_list(live.get("tags"))],
            "stations": [{"s": st.get("s"), "x": st.get("x"), "y": st.get("y"), "n": gamenames.NAMES.rich(st.get("n"))}
                         for st in as_list((STATE_MEM["stats"] or {}).get("stations"))],
            "attacks": [dict(a, what_label=gamenames.NAMES.get(a.get("what"), ("entity-name", "item-name")),
                             near=place_near(a.get("s"), a.get("x"), a.get("y")),
                             active=(live.get("tick") or 0) - (a.get("last") or 0) < 7200)
                        for a in as_list(live.get("attacks"))]}


def place_near(s, x, y, max_d=400):
    """Name of the closest player-written map tag, e.g. 'TSMC'."""
    best, bd = None, max_d ** 2
    for t in as_list((STATE_MEM["live"] or {}).get("tags")):
        if t.get("s") == s and t.get("t") and x is not None:
            d = (t["x"] - x) ** 2 + (t["y"] - y) ** 2
            if d < bd:
                best, bd = gamenames.NAMES.rich(t["t"]), d
    return best


def api_maps():
    """The map picker (/api/maps): every surface with its snapshots."""
    with db_lock:
        con = db()
        rows = con.execute("SELECT id, surface, ts, tick, name, kind, zone, minz, bounds, thinned, frame FROM snapshots "
                           "ORDER BY surface, ts").fetchall()
        con.close()
    out = {}
    for i, (sid, si, ts, tick, name, kind, zone, minz, bounds, thinned, frame) in enumerate(rows):
        newest = i + 1 == len(rows) or rows[i + 1][1] != si
        if not frame and not newest:
            continue  # a live-map version that has been replaced (kept a few minutes for people still on it)
        o = out.setdefault(si, {"index": si, "name": display_name(name, zone), "sname": name, "kind": kind, "snapshots": []})
        o["snapshots"].append({"id": sid, "ts": ts, "tick": tick, "day": (tick or 0) // TICKS_PER_DAY + 1,
                               "minz": minz, "bounds": json.loads(bounds), "maxz": -2 if thinned else 0,
                               "live": bool(newest and livemap.active())})
    lst = sorted(out.values(), key=lambda x: (x["index"] != 1, -len(x["snapshots"]), x["name"] or ""))
    return {"surfaces": lst}


_poll_cache = {"mt": None, "d": None}


def read_pollution():
    """The mod's pollution.json; keeps the last good copy if the file is being rewritten."""
    p = OUT / "pollution.json"
    try:
        mt = p.stat().st_mtime
    except OSError:
        return None
    if _poll_cache["mt"] != mt:
        d = read_json(p)
        if d is not None:  # caught mid-write: keep the last good copy and retry next time
            _poll_cache["d"], _poll_cache["mt"] = d, mt
    return _poll_cache["d"]


def api_pollution(q):
    """Pollution per chunk (from the mod's 5-minute pass) + enemy nests inside the cloud (from the latest snapshot)."""
    si = qint(q, "s", 1)
    d = read_pollution() or {}
    raw = [int(float(v)) for v in str((d.get("surfaces") or {}).get(str(si), "")).split(",") if v != ""]
    chunks = [raw[i:i + 3] for i in range(0, len(raw) - 2, 3)]
    polluted = {(c[0], c[1]): c[2] for c in chunks}
    nests = []
    with db_lock:
        con = db()
        row = con.execute("SELECT id FROM snapshots WHERE surface=? ORDER BY ts DESC LIMIT 1", (si,)).fetchone()
        con.close()
    lk = load_lookup(row[0], si) if row else None
    if lk and polluted:
        keys, types = lk.get("keys") or [], lk.get("types") or []
        nest_idx = {i + 1 for i, k in enumerate(keys)
                    if enemy_role(k, types[i] if i < len(types) else None) == "nest"}
        for key, c in lk["chunks"].items():
            for r in c["e"]:
                if r[4] in nest_idx:
                    # nests soak up pollution, so their own chunk reads low: count the cloud touching it
                    cx, cy = r[0] // 32, r[1] // 32
                    v = max((polluted.get((cx + ox, cy + oy), 0) for ox in (-1, 0, 1) for oy in (-1, 0, 1)), default=0)
                    if v:
                        nests.append([r[0] + r[2] / 2, r[1] + r[3] / 2, v])
    return {"tick": d.get("tick"), "ts": tick_to_ts(d.get("tick")) if d.get("tick") else None, "chunks": chunks,
            "max": max((c[2] for c in chunks), default=0), "nests": nests}


_nest_cache = {}
BASE_LINK = 40  # nests/worms this close (tiles) count as one biter base


def enemy_role(key, typ):
    """'nest', 'worm' or None for a palette key ("x:<name>" = on an enemy force) and its entity type.
    Newer snapshots carry the type, so mods' enemies count whatever they're called; older ones
    only have the name, so fall back to the usual words."""
    if not key.startswith("x:"):
        return None
    if typ:
        return "nest" if typ == "unit-spawner" else "worm"  # worms are type "turret"; any other enemy building defends too
    n = key[2:]
    return "nest" if ("spawner" in n or "nest" in n or "hive" in n) else "worm"


def territories(lk):
    """Space Age demolisher territories from a lookup: explored chunks + who lives there."""
    out = []
    for t in as_list(lk.get("terr")):
        raw = [int(v) for v in str(t.get("c") or "").split(",") if v.strip().lstrip("-").isdigit()]
        chunks = [raw[i:i + 2] for i in range(0, len(raw) - 1, 2)]
        if not chunks:
            continue
        units = {}
        for u in as_list(t.get("u")):
            units[u] = units.get(u, 0) + 1
        out.append({"chunks": chunks,
                    "units": [[gamenames.NAMES.get(u, ("entity-name",)), n] for u, n in sorted(units.items())],
                    "x": round(sum(c[0] for c in chunks) / len(chunks) * 32 + 16, 1),
                    "y": round(sum(c[1] for c in chunks) / len(chunks) * 32 + 16, 1)})
    return out


def api_nests(q):
    """Enemy bases from a snapshot: nests + worms grouped into bases, with an outline and what's in them."""
    si, sid = qint(q, "s", 1), qint(q, "snap", 0)
    lk = load_lookup(sid, si)
    if not lk:
        return {"bases": [], "territories": []}
    bases = _nest_cache.get((sid, si))  # read once: another request may swap the cache meanwhile
    if bases is None:
        keys, types = lk["keys"], lk.get("types") or []
        role = {i + 1: enemy_role(k, types[i] if i < len(types) else None) for i, k in enumerate(keys)}
        recs = []
        for c in lk["chunks"].values():
            for r in c["e"]:
                if role.get(r[4]):
                    recs.append([r[0], r[1], r[2], r[3], keys[r[4] - 1][2:], role[r[4]] == "nest"])
        bases = []
        for g in cluster_tiles(recs, BASE_LINK):
            kinds = {}
            for r in g:
                kinds[r[4]] = kinds.get(r[4], 0) + 1
            nests = sum(1 for r in g if r[5])
            worms = len(g) - nests
            xs, ys = [r[0] + r[2] / 2 for r in g], [r[1] + r[3] / 2 for r in g]
            bases.append({"x": round(sum(xs) / len(g), 1), "y": round(sum(ys) / len(g), 1), "nests": nests, "worms": worms,
                          "hull": hull([(r[0] + dx, r[1] + dy) for r in g for dx, dy in ((0, 0), (r[2], 0), (0, r[3]), (r[2], r[3]))], pad=6),
                          "kinds": sorted(([gamenames.NAMES.get(k, ("entity-name",)), v] for k, v in kinds.items()), key=lambda x: -x[1]),
                          "pts": [[round(x, 1), round(y, 1), 1 if r[5] else 0] for x, y, r in zip(xs, ys, g)]})
        bases = {"bases": bases, "terr": territories(lk)}
        _nest_cache.clear()
        _nest_cache[(sid, si)] = bases
    # which bases the pollution cloud reaches right now (they're the ones that will attack)
    d = read_pollution() or {}
    raw = [int(float(v)) for v in str((d.get("surfaces") or {}).get(str(si), "")).split(",") if v != ""]
    polluted = {(raw[i], raw[i + 1]) for i in range(0, len(raw) - 2, 3)}
    out = []
    for b in bases["bases"]:
        hit = any((int(x) // 32 + ox, int(y) // 32 + oy) in polluted for x, y, _ in b["pts"] for ox in (-1, 0, 1) for oy in (-1, 0, 1))
        out.append(dict(b, polluted=hit))
    return {"bases": out, "has_pollution": bool(polluted), "territories": bases["terr"]}


# ranges for snapshots taken before the mod recorded them (K2 changes the gun turret to 25)
DEFAULT_RANGE = {"ammo-turret": 18, "electric-turret": 24, "fluid-turret": 30, "artillery-turret": 224}
NAME_RANGE = {"gun-turret": 25}
KEY_BUILDINGS = [  # (match on type or name, label, icon)
    (lambda t, n: t == "rocket-silo", "Rocket silo", "🚀"),
    (lambda t, n: "rocket-launch-pad" in n, "Cargo rocket pad", "🚀"),
    (lambda t, n: "landing-pad" in n, "Landing pad", "🛬"),
    (lambda t, n: "space-elevator" in n, "Space elevator", "🛗"),
    (lambda t, n: t in ("reactor", "fusion-reactor"), "Reactor", "☢"),
    (lambda t, n: "core-miner" in n, "Core miner", "⛏"),
    (lambda t, n: t == "lab", "Labs", "🔬"),
]
_struct_cache = {}


def api_structures(q):
    """Turret coverage, roboport coverage and key buildings from a snapshot."""
    si, sid = qint(q, "s", 1), qint(q, "snap", 0)
    hit = _struct_cache.get((sid, si))
    if hit is not None:
        return hit
    lk = load_lookup(sid, si)
    if not lk:
        return {"turrets": [], "roboports": [], "key": []}
    keys, types = lk["keys"], lk.get("types") or []
    ranges = lk.get("ranges") or []
    turrets, robo, keyrecs = [], [], {}
    for c in lk["chunks"].values():
        for r in c["e"]:
            i = r[4] - 1
            if not 0 <= i < len(keys) or not keys[i].startswith("e:"):
                continue
            t, n = types[i] if i < len(types) else None, keys[i][2:]
            rg = ranges[i] if i < len(ranges) else None
            cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
            if t in DEFAULT_RANGE:
                turrets.append([round(cx, 1), round(cy, 1), (rg and rg[0]) or NAME_RANGE.get(n) or DEFAULT_RANGE[t],
                                "art" if t == "artillery-turret" else "t"])
            elif t == "roboport":
                robo.append([round(cx, 1), round(cy, 1), (rg and rg[1]) or 25, (rg and rg[2]) or 55])
            else:
                for j, (m, label, icon) in enumerate(KEY_BUILDINGS):
                    if m(t or "", n):
                        keyrecs.setdefault(j, []).append([r[0], r[1], r[2], r[3], n])
                        break
    key = []
    for j, recs in keyrecs.items():
        _, label, icon = KEY_BUILDINGS[j]
        for g in cluster_tiles(recs, 40):
            xs, ys = [r[0] + r[2] / 2 for r in g], [r[1] + r[3] / 2 for r in g]
            key.append({"label": label, "icon": icon, "n": len(g), "x": round(sum(xs) / len(g), 1), "y": round(sum(ys) / len(g), 1)})
    out = {"turrets": turrets, "roboports": robo, "key": key}
    _struct_cache.clear()
    _struct_cache[(sid, si)] = out
    return out


_changes_cache = {}


def api_changes(q):
    """What was built/removed between a snapshot and the one before it, per chunk."""
    si, sid = qint(q, "s", 1), qint(q, "snap", 0)
    with db_lock:
        con = db()
        # the timelapse frame before it (live-map versions in between are only a minute apart)
        prev = con.execute("SELECT id FROM snapshots WHERE surface=? AND frame=1 AND "
                           "ts < (SELECT ts FROM snapshots WHERE id=? AND surface=?) ORDER BY ts DESC LIMIT 1",
                           (si, sid, si)).fetchone()
        con.close()
    if not prev:
        return {"chunks": [], "prev": None}
    key = (sid, prev[0], si)
    hit = _changes_cache.get(key)
    if hit is None:
        def ents(lk):
            keys = lk["keys"]
            out = {}
            for c in lk["chunks"].values():
                for r in c["e"]:
                    k = keys[r[4] - 1] if 0 < r[4] <= len(keys) else ""
                    if k.startswith("e:"):  # our buildings only (not ore, rocks, nests)
                        out[(r[0], r[1], k)] = 1
            return out
        a, b = load_lookup(sid, si), load_lookup(prev[0], si)
        if not a or not b:
            return {"chunks": [], "prev": prev[0]}
        ea, eb = ents(a), ents(b)
        per = {}
        for (x, y, _k) in ea.keys() - eb.keys():
            per.setdefault((x // 32, y // 32), [0, 0])[0] += 1
        for (x, y, _k) in eb.keys() - ea.keys():
            per.setdefault((x // 32, y // 32), [0, 0])[1] += 1
        hit = [[cx, cy, ad, rm] for (cx, cy), (ad, rm) in per.items()]
        _changes_cache.clear()
        _changes_cache[key] = hit
    return {"chunks": hit, "prev": prev[0]}


def api_attack_map(q):
    """Every attack ever logged, binned into 64x64 tile cells."""
    si = qint(q, "s", 1)
    with db_lock:
        con = db()
        rows = con.execute("SELECT ts, data FROM events WHERE type='attack'").fetchall()
        con.close()
    cells = {}
    for ts, d in rows:
        d = json.loads(d)
        if d.get("surface") != si or d.get("x") is None:
            continue
        c = cells.setdefault((d["x"] // 64, d["y"] // 64), [0, 0])
        c[0] += 1
        c[1] = max(c[1], ts or 0)
    return {"cells": [[cx * 64 + 32, cy * 64 + 32, n, last] for (cx, cy), (n, last) in cells.items()]}
