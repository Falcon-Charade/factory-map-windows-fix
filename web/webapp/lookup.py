"""Per-snapshot lookup files: what is at each map position, plus the ore patches. Loaded on demand and cached."""

import json
import threading
import traceback

from .config import LOOKUP, log
from .patches import PATCH_VERSION, make_patches, save_patch_hist
from .store import db, db_lock


def build_lookup(sid, si, cs, palette, ef, territories=None):
    """Per-snapshot click lookup (what is at x,y) and ore patches (grouped resource tiles with totals).
    `territories`: Space Age demolisher areas from the mod, [{"c": "cx,cy,cx,cy,...", "u": [unit names]}]."""
    keys = [p.get("k", "") for p in palette]
    chunks, res = {}, {}
    for (cx, cy), (tiles, e) in cs.items():
        bx, by = cx * 32, cy * 32
        recs = []
        for k in range(0, len(e) - ef + 1, ef):
            idx = e[k + 4]
            if not 0 < idx <= len(palette):
                continue
            key = keys[idx - 1]
            if key.startswith("v:"):
                continue  # trees: not worth clicking
            amt = e[k + 5] if ef >= 6 else 0
            rec = [bx + e[k], by + e[k + 1], e[k + 2], e[k + 3], idx, amt, -1]
            recs.append(rec)
            if key.startswith("r:"):
                res.setdefault(key, []).append(rec)
        chunks[f"{cx},{cy}"] = {"t": tiles, "e": recs}

    def meta_of(key, recs):
        m = palette[recs[0][4] - 1]
        fluid = "fluid" in (m.get("cat") or "")  # crude oil, mineral water (pumpjacks)
        inf = bool(m.get("inf"))                 # SE core seams: infinite, shown as yield like oil
        return (fluid, m.get("c"), m.get("norm") if (fluid or inf) else None, inf)
    patches = make_patches(res, meta_of)
    out = LOOKUP / str(sid)
    out.mkdir(parents=True, exist_ok=True)
    import gzip
    with gzip.open(out / f"{si}.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"keys": keys, "types": [p.get("t") for p in palette], "chunks": chunks, "patches": patches,
                   "ranges": [[p.get("r"), p.get("lr"), p.get("cr")] if (p.get("r") or p.get("lr")) else None for p in palette],
                   "has_amounts": ef >= 6, "pv": PATCH_VERSION, "terr": territories or []}, f, separators=(",", ":"))
    with _lookup_lock:
        _lookup_cache.pop((sid, si), None)
    if ef >= 6:
        save_patch_hist(sid, si, {"chunks": chunks, "keys": keys, "types": [p.get("t") for p in palette], "patches": patches})


_lookup_cache = {}


def migrate_lookups():
    """Regroup patches in stored lookups made with older grouping rules (raw snapshot data is gone by then)."""
    import gzip
    with db_lock:
        con = db()
        have_hist = {(a, b) for a, b in con.execute("SELECT sid, surface FROM patch_hist")}
        con.close()
    for f in LOOKUP.glob("*/*.json.gz"):
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                d = json.load(fh)
            sid_, si_ = int(f.parent.name), int(f.name.split(".")[0])
            if d.get("pv", 1) >= PATCH_VERSION or not d["patches"]:
                if d.get("has_amounts") and d["patches"] and (sid_, si_) not in have_hist:
                    save_patch_hist(sid_, si_, d)
                continue
            old = {}
            for p in d["patches"]:  # recover per-resource colour / fluid / normal amount from the old patches
                o = old.setdefault(p["name"], [p["fluid"], p.get("color"), None])
                if p.get("yield"):
                    o[2] = p["amount"] / p["tiles"] / (p["yield"] / 100)
            res = {}
            for c in d["chunks"].values():
                for r in c["e"]:
                    if r[6] >= 0:
                        res.setdefault(d["keys"][r[4] - 1], []).append(r)
            n0 = len(d["patches"])
            def meta(key, recs):
                fluid, color, norm = old.get(key[2:], [False, None, None])
                core = key[2:].startswith("se-core-fragment")  # infinite core seam, not a fluid
                return (fluid and not core, color, norm, core)
            d["patches"] = make_patches(res, meta)
            d["pv"] = PATCH_VERSION
            tmp = f.with_suffix(".tmp")
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(d, fh, separators=(",", ":"))
            tmp.replace(f)
            with _lookup_lock:
                _lookup_cache.pop((int(f.parent.name), int(f.name.split(".")[0])), None)
            if d.get("has_amounts"):
                save_patch_hist(sid_, si_, d)
            log(f"regrouped ore patches in {f.parent.name}/{f.name}: {n0} -> {len(d['patches'])}")
        except Exception:
            traceback.print_exc()


_lookup_lock = threading.Lock()


def load_lookup(sid, si):
    """The lookup for one snapshot and surface (cached; None if it doesn't exist)."""
    with _lookup_lock:  # many requests at once share this cache
        return _load_lookup(sid, si)


def _load_lookup(sid, si):
    """Read and unpack a lookup file (called with _lookup_lock held by load_lookup)."""
    import gzip
    k = (sid, si)
    if k not in _lookup_cache:
        p = LOOKUP / str(sid) / f"{si}.json.gz"
        if not p.exists():
            return None
        with gzip.open(p, "rt", encoding="utf-8") as f:
            _lookup_cache[k] = json.load(f)
        while len(_lookup_cache) > 4:
            _lookup_cache.pop(next(iter(_lookup_cache)))
    return _lookup_cache[k]


def tile_name_at(tiles_rle, lx, ly, keys):
    """Ground tile name at a map position, from a chunk's run-length encoded tiles."""
    i = ly * 32 + lx
    pos = 0
    for part in tiles_rle.split(","):
        v, _, n = part.partition("*")
        n = int(n or 1)
        if pos + n > i:
            v = int(v)
            return keys[v - 1][2:] if 0 < v <= len(keys) else None
        pos += n
    return None
