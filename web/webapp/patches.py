"""Ore patches: grouping resource tiles into patches, their outlines, drill counts and history."""

import json

from . import gamenames
from .store import db, db_lock


def patch_drills(lk):
    """patch id -> number of mining drills / pumpjacks / core miners working it (cached on the lookup)."""
    if "_drills" in lk:
        return lk["_drills"]
    keys, types = lk["keys"], lk.get("types") or []
    owner, drills = {}, []
    for c in lk["chunks"].values():
        for r in c["e"]:
            i = r[4] - 1
            if len(r) > 6 and r[6] is not None and r[6] >= 0:
                for dx in range(r[2]):
                    for dy in range(r[3]):
                        owner[(r[0] + dx, r[1] + dy)] = r[6]
            elif 0 <= i < len(types) and types[i] == "mining-drill":
                drills.append(r)
    out = {}
    for r in drills:  # a drill belongs to the patch with the most ore under it (its footprint plus a 1-tile rim)
        votes = {}
        for x in range(r[0] - 1, r[0] + r[2] + 1):
            for y in range(r[1] - 1, r[1] + r[3] + 1):
                pid = owner.get((x, y))
                if pid is not None:
                    votes[pid] = votes.get(pid, 0) + 1
        if votes:
            pid = max(votes, key=votes.get)
            out[pid] = out.get(pid, 0) + 1
    lk["_drills"] = out
    return out


def save_patch_hist(sid, si, lk):
    """Small per-snapshot summary of every patch, so 'how fast is it running out' never needs old lookups."""
    dr = patch_drills(lk)
    rows = [[p["name"], p["bbox"], p["amount"], dr.get(p["id"], 0)] for p in lk["patches"]]
    with db_lock:
        con = db()
        con.execute("INSERT OR REPLACE INTO patch_hist VALUES (?,?,?)", (sid, si, json.dumps(rows, separators=(",", ":"))))
        con.commit()
        con.close()


PATCH_VERSION = 5  # bump when grouping changes: stored lookups get regrouped at startup
ORE_LINK = 4       # ore tiles this close (in tiles) belong to the same patch
ORE_ABSORB = 16    # a small cluster this close to a big patch of the same ore joins it
ORE_SMALL = 60     # "small cluster" = fewer tiles than this
FLUID_LINK = 12    # oil wells are spread out


def cluster_tiles(recs, d):
    """Group tile positions into clusters: tiles closer than `link` tiles end up in the same cluster."""
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    grid = {}
    for i, r in enumerate(recs):
        grid.setdefault((r[0] // d, r[1] // d), []).append(i)
    for (gx, gy), members in grid.items():
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                for j in grid.get((gx + ox, gy + oy), ()):
                    for i in members:
                        if i < j and abs(recs[i][0] - recs[j][0]) <= d and abs(recs[i][1] - recs[j][1]) <= d:
                            a, b = find(i), find(j)
                            if a != b:
                                parent[a] = b
    groups = {}
    for i in range(len(recs)):
        groups.setdefault(find(i), []).append(recs[i])
    return list(groups.values())


def _absorb_small(groups):
    """Fold small ore clusters into the nearest big patch of the same ore, like the eye does in game."""
    big = [g for g in groups if len(g) >= ORE_SMALL]
    if not big:
        return groups
    cell = ORE_ABSORB
    grid = {}
    for bi, g in enumerate(big):
        for r in g:
            grid.setdefault((r[0] // cell, r[1] // cell), []).append((r[0], r[1], bi))
    lim = ORE_ABSORB * ORE_ABSORB
    out = [list(g) for g in big]
    for g in groups:
        if len(g) >= ORE_SMALL:
            continue
        best, bd = None, lim + 1
        for r in g:
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    for qx, qy, bi in grid.get((r[0] // cell + ox, r[1] // cell + oy), ()):
                        dd = (qx - r[0]) ** 2 + (qy - r[1]) ** 2
                        if dd < bd:
                            best, bd = bi, dd
            if bd <= 4:
                break
        if best is not None and bd <= lim:
            out[best].extend(g)
        else:
            out.append(g)
    return out


def make_patches(res, meta_of):
    """res: resource key -> tile records [x, y, w, h, idx, amount, pid]. Sets pid, returns patch list."""
    patches = []
    for key, recs in sorted(res.items()):
        fluid, color, norm, *rest = meta_of(key, recs)
        inf = bool(rest and rest[0])
        groups = cluster_tiles(recs, FLUID_LINK if fluid else ORE_LINK)
        if not fluid:
            groups = _absorb_small(groups)
        name = key[2:]
        for g in groups:
            pid = len(patches)
            for r in g:
                r[6] = pid
            total = sum(r[5] for r in g)
            xs, ys = [r[0] + r[2] / 2 for r in g], [r[1] + r[3] / 2 for r in g]  # centres (core seams and oil wells are bigger than 1 tile)
            label = gamenames.NAMES.get(name)
            if name.startswith("se-core-fragment-"):  # "Core fragment" alone doesn't say which ore
                ore = name[len("se-core-fragment-"):].removesuffix("-sealed")
                label = ("Omni" if ore == "omni" else gamenames.NAMES.get(ore)) + " core seam" + (" (sealed)" if name.endswith("-sealed") else "")
            p = {"id": pid, "name": name, "label": label, "fluid": fluid, "inf": inf, "tiles": len(g), "amount": total,
                 "hull": hull([(r[0] + dx, r[1] + dy) for r in g for dx, dy in ((0, 0), (r[2], 0), (0, r[3]), (r[2], r[3]))],
                              pad=4 if fluid else 1),
                 "bbox": [min(r[0] for r in g), min(r[1] for r in g), max(r[0] + r[2] for r in g), max(r[1] + r[3] for r in g)],
                 "x": round(sum(xs) / len(g), 1), "y": round(sum(ys) / len(g), 1), "color": color}
            if norm:
                p["yield"] = round(total / len(g) / norm * 100, 1)  # average per well, like the in-game tooltip
            patches.append(p)
    return patches


def hull(points, pad=0):
    """Convex hull (monotone chain), pushed out by `pad` tiles so the outline sits around the patch."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return [list(p) for p in pts]
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    h = lower[:-1] + upper[:-1]
    if pad:
        cx = sum(p[0] for p in h) / len(h)
        cy = sum(p[1] for p in h) / len(h)
        out = []
        for x, y in h:
            dx, dy = x - cx, y - cy
            d = (dx * dx + dy * dy) ** .5 or 1
            out.append([round(x + dx / d * pad, 1), round(y + dy / d * pad, 1)])
        return out
    return [list(p) for p in h]
