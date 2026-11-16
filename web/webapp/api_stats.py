"""JSON API for the Factory, Events and Players tabs: overview numbers, events, history graphs."""

import json
import re
import time

from . import gamenames
from .api_map import place_near
from .config import SERVER_NAME, TICKS_PER_DAY
from .ingest import MIN_RATE_MIN, rates
from .state import STATE_MEM, read_live, surface_names
from .store import as_list, db, db_lock, kv_get, qint


def is_science(name):
    """True for science packs and Krastorio tech cards (anything named *science-pack* or *tech-card*, not blank cards)."""
    return bool(re.search(r"science-pack|tech-card", name)) and "blank" not in name


SCIENCE_ORDER = ["automation", "logistic", "military", "chemical", "se-rocket", "production", "utility", "space",
                 "se-astronomic", "se-biological", "se-energy", "se-material", "se-deep-space", "kr-"]


def science_key(n):
    """Sort key that puts science packs in research order, unknown mod packs last."""
    for i, p in enumerate(SCIENCE_ORDER):
        if n.startswith(p):
            return (i, n)
    return (99, n)


def evo_info(e):
    """The mod's evolution breakdown (time / pollution / spawner kills, map settings) plus, per spawner,
    which enemies it sends at which evolution, with English names."""
    if not e:
        return None
    spawn = {}
    for spawner, units in (e.get("spawn") or {}).items():
        spawn[spawner] = [{"u": u["u"], "label": gamenames.NAMES.get(u["u"], ("entity-name",)),
                           "p": [list(pt.values()) if isinstance(pt, dict) else pt for pt in as_list(u.get("p"))]}
                          for u in as_list(units)]
    return {"time": e.get("time"), "pollution": e.get("pollution"), "spawners": e.get("spawners"),
            "settings": e.get("settings"), "spawn": spawn}


LONG_TICKS = 6 * 216000  # "over a longer time": the last 6 hours of play


def long_view(short):
    """For each shortage, the average over the last few hours of play (flow totals are stored every 10 game
    minutes), so a real, lasting shortage can be told apart from a machine that just started or a short blip."""
    if not short:
        return
    with db_lock:
        con = db()
        for x in short:
            last = con.execute("SELECT tick, produced, consumed FROM flow WHERE kind=? AND name=? ORDER BY tick DESC LIMIT 1",
                               (x["kind"], x["name"])).fetchone()
            if not last:
                continue
            first = con.execute("SELECT tick, produced, consumed FROM flow WHERE kind=? AND name=? AND tick>=? ORDER BY tick LIMIT 1",
                                (x["kind"], x["name"], last[0] - LONG_TICKS)).fetchone()
            span = last[0] - first[0]
            if span < 3 * 36000 or last[1] < first[1] or last[2] < first[2]:  # under 30 min of play, or another save
                continue
            m = 3600 / span
            x["long"] = {"made_pm": (last[1] - first[1]) * m, "used_pm": (last[2] - first[2]) * m, "hours": span / 216000}
        con.close()


def api_overview():
    """Everything the header and Factory tab show (/api/overview), from the latest stats in memory."""
    s = STATE_MEM["stats"]
    if not s:
        return {"ok": False}
    live = read_live() or {}
    sn = surface_names()
    r = rates()
    res = s.get("research") or {}
    rates_old = False
    players = as_list(s.get("players"))
    with db_lock:
        con = db()
        seen = dict(con.execute("SELECT name, last_seen FROM players").fetchall())
        day_ago = con.execute("SELECT data FROM samples WHERE ts <= ? ORDER BY tick DESC LIMIT 1",
                              (time.time() - 86400,)).fetchone()
        first = con.execute("SELECT data FROM samples ORDER BY tick LIMIT 1").fetchone()
        if not r or r["window_min"] < MIN_RATE_MIN:
            # just after a restart only a minute or two of data exists: show the last proper average instead
            saved = kv_get(con, "last_rates")
            if saved and saved.get("window_min", 0) > ((r or {}).get("window_min") or 0):
                r, rates_old = saved, True
        con.close()
    then = json.loads((day_ago or first)[0]) if (day_ago or first) else {}

    totals = s.get("items") or {}
    science = []
    for name in sorted({n for n in (totals.get("i") or {}) if is_science(n)}, key=science_key):
        rr = (r or {}).get("items", {}).get(name, (0, 0))
        science.append({"name": name, "label": gamenames.NAMES.get(name), "made": totals["i"].get(name, 0),
                        "used": (totals.get("o") or {}).get(name, 0), "made_pm": rr[0], "used_pm": rr[1]})
    top = []
    if r:
        for key, kind in (("items", "i"), ("fluids", "f")):
            for name, (p, c) in r[key].items():
                top.append({"name": name, "kind": kind, "label": gamenames.NAMES.get(name), "made_pm": p, "used_pm": c})
        top.sort(key=lambda x: -x["made_pm"])
    # research ETA: labs eat every ingredient at the same pace, so the slowest-fed pack sets the speed
    eta = None
    if res.get("current") and res.get("units") and r:
        ups = []
        for ing in as_list(res.get("ing")):
            used = (r.get("items") or {}).get(ing["n"], (0, 0))[1]
            ups.append(used / max(ing.get("a") or 1, 1))
        if ups and min(ups) > 0:
            eta = res["units"] * (1 - (res.get("progress") or 0)) / min(ups)

    # shortages: things used faster than they're made (fed from storage, or about to run dry)
    short = []
    for key, kind in (("items", "i"), ("fluids", "f")):
        for name, (pm, cm) in ((r or {}).get(key) or {}).items():
            if cm - pm >= 1 and cm > pm * 1.1:
                short.append({"name": name, "kind": kind, "label": gamenames.NAMES.get(name), "made_pm": pm, "used_pm": cm})
    short.sort(key=lambda x: -(x["used_pm"] - x["made_pm"]))
    long_view(short[:12])

    def plabel(n):
        return {"tree-proxy": "Trees", "tree-dying-proxy": "Dying trees", "tile-proxy": "Ground (tiles)"}.get(n) or gamenames.NAMES.get(n)
    pol = None
    pr = (r or {}).get("pollution") or {}
    if s.get("pollution"):
        src = sorted(((n, v[0]) for n, v in pr.items() if v[0] > 0), key=lambda x: -x[1])
        ab = sorted(((n, v[1]) for n, v in pr.items() if v[1] > 0), key=lambda x: -x[1])
        pol = {"air": (s["pollution"] or {}).get("air"), "made_pm": sum(v for _, v in src), "absorbed_pm": sum(v for _, v in ab),
               "sources": [{"name": n, "label": plabel(n), "pm": v} for n, v in src[:12]],
               "absorbers": [{"name": n, "label": plabel(n), "pm": v} for n, v in ab[:8]]}
        # other pollutants (Space Age: spores on Gleba), each with its own totals
        other = []
        for kind, v in ((s["pollution"] or {}).get("other") or {}).items():
            orr = (r or {}).get("pol:" + kind) or {}
            other.append({"kind": kind, "label": gamenames.NAMES.get(kind, ("airborne-pollutant-name",)),
                          "air": (v or {}).get("air") if isinstance(v, dict) else None,
                          "made_pm": sum(x[0] for x in orr.values()), "absorbed_pm": sum(x[1] for x in orr.values()),
                          "sources": [{"name": n, "label": plabel(n), "pm": x[0]}
                                      for n, x in sorted(orr.items(), key=lambda i: -i[1][0]) if x[0] > 0][:6]})
        if other:
            pol["other"] = other
    power = None
    pw = s.get("power")
    if pw:
        er = (r or {}).get("power") or {}
        gens = sorted(((n, v[0] / 60) for n, v in er.items() if v[0] > 0), key=lambda x: -x[1])  # J/min -> W
        uses = sorted(((n, v[1] / 60) for n, v in er.items() if v[1] > 0), key=lambda x: -x[1])
        power = {"id": pw.get("id"), "made_w": sum(v for _, v in gens), "used_w": sum(v for _, v in uses),
                 "sources": [{"name": n, "label": gamenames.NAMES.get(n), "w": v} for n, v in gens[:10]],
                 "consumers": [{"name": n, "label": gamenames.NAMES.get(n), "w": v} for n, v in uses[:10]],
                 "low": pw.get("low") or 0, "probes": pw.get("probes") or 0, "working": pw.get("working") or 0,
                 # mod 0.4+: what all generators of the kinds on this network could make (solar: full daylight)
                 "cap_w": pw.get("cap"), "solar_w": pw.get("solar")}

    kills = s.get("kills") or {}
    # the kill counter also counts trees, rocks and our own deconstructed junk: keep the enemies.
    # The mod (0.3.0+) says which names are enemies by entity type, so mods' enemies count too;
    # older mod versions only give names, so fall back to the usual words.
    ktypes = s.get("ktypes")
    if isinstance(ktypes, dict):
        enemy_kills = {k: v for k, v in (kills.get("i") or {}).items() if k in ktypes}
    else:
        enemy_kills = {k: v for k, v in (kills.get("i") or {}).items() if re.search(r"biter|spitter|worm|spawner|nest|hive", k)}
    # Space Age: evolution is per planet; listed when more than one planet has nests
    evo_planets = sorted(({"name": sn.get(int(i), {}).get("name") or str(i), "evo": v}
                          for i, v in (s.get("evo_surfaces") or {}).items() if str(i).isdigit() and isinstance(v, (int, float))),
                         key=lambda x: -x["evo"])
    counts = (s.get("counts") or {}).get("total") or {}
    live_now = time.time() - (STATE_MEM["live_t"] or 0) < 30
    online = [p["n"] for p in players if p.get("on")] if live_now else []
    return {
        "ok": True, "server": SERVER_NAME, "tick": s["tick"], "day": s["tick"] // (s.get("tpd") or TICKS_PER_DAY) + 1,
        "hours": s["tick"] / 216000, "updated": STATE_MEM["live_t"] or None,
        "live": live_now,
        "online": online,
        "research": {"current": res.get("current"), "label": gamenames.NAMES.tech(res["current"], res.get("level")) if res.get("current") else None,
                     "progress": res.get("progress"), "researched": res.get("researched"), "total": res.get("total"),
                     "queue": [{"name": q, "label": gamenames.NAMES.tech(q)} for q in (res.get("queue") or [])],
                     "eta_min": eta, "units": res.get("units"),
                     "ing": [{"name": x["n"], "label": gamenames.NAMES.get(x["n"]), "a": x.get("a")} for x in as_list(res.get("ing"))]},
        "shortages": short[:12], "pollution": pol, "power": power,
        "science": science, "top": top[:60], "rate_window_min": (r or {}).get("window_min"),
        "counts": counts, "counts_then": then.get("counts") or {},
        "evolution": s.get("evolution"), "evo": evo_info(s.get("evo")), "evo_planets": evo_planets,
        "share": s.get("share") or {}, "rockets": s.get("rockets"), "se": s.get("se"),
        "kills": {"killed": sum(enemy_kills.values()), "lost": sum((kills.get("o") or {}).values()),
                  "top": sorted(({"name": k, "label": gamenames.NAMES.get(k), "n": v} for k, v in enemy_kills.items()),
                                key=lambda x: -x["n"])[:8]},
        "players": sorted([{"name": p["n"], "online": p.get("on"), "hours": (p.get("ot") or 0) / 216000,
                            "afk_min": (p.get("afk") or 0) / 3600, "deaths": p.get("deaths", 0),
                            "surface": sn.get(p.get("s"), {}).get("name"), "last_seen": seen.get(p["n"]),
                            "admin": p.get("admin"), "st": p.get("st") or {}} for p in players], key=lambda x: -x["hours"]),
        "snapshot": s.get("snapshot"), "rendering": STATE_MEM["rendering"],
        "surfaces": {i: x for i, x in sn.items() if x["kind"] or i == 1},  # skip mods' hidden helper surfaces
        "rates_old": rates_old,
    }


def describe(e):
    """One plain-English line for an event (chat, death, research done, rocket, ...)."""
    t = e.get("type")
    n = gamenames.NAMES
    if t == "research":
        e["label"] = n.tech(e.get("name"), e.get("level"))
    elif t == "death" and e.get("cause"):
        e["cause_label"] = n.get(e["cause"], ("entity-name", "item-name"))
    elif t == "first":
        e["label"] = n.get(e.get("name"))
    elif t == "attack":
        e["what_label"] = n.get(e.get("what"), ("entity-name", "item-name"))
        e["cause_label"] = n.get(e["cause"], ("entity-name",)) if e.get("cause") else None
        e["near"] = place_near(e.get("surface"), e.get("x"), e.get("y"))
    if e.get("surface"):
        e["surface_name"] = surface_names().get(e["surface"], {}).get("name")
    return e


def api_events(q):
    """The event log (/api/events), newest first, filtered by type, paged with ?before=<id>."""
    before = qint(q, "before", 0) or 2 ** 62
    types = [t for t in q.get("types", [""])[0].split(",") if re.fullmatch(r"[a-z_]+", t)]
    sql = "SELECT id, ts, data FROM events WHERE id < ?"
    args = [before]
    if types:
        sql += f" AND type IN ({','.join('?' * len(types))})"
        args += types
    sql += " ORDER BY id DESC LIMIT 100"
    with db_lock:
        con = db()
        rows = con.execute(sql, args).fetchall()
        con.close()
    return {"events": [dict(describe(json.loads(d)), id=i, ts=ts) for i, ts, d in rows]}


RANGE_S = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "all": 10 ** 10}


def api_history(q):
    """Graph data (/api/history) for one item/fluid or the totals, over the chosen time range."""
    rng = RANGE_S.get(q.get("range", ["7d"])[0], 7 * 86400)
    since = time.time() - rng
    names = [x for x in q.get("names", [""])[0].split(",") if re.fullmatch(r"[\w\-.]+", x)][:12]
    if q.get("power"):  # power totals: one series per network id over time, stitched together
        names = ["_power"]
    kind = q.get("kind", ["i"])[0] if q.get("kind", ["i"])[0] in ("i", "f", "k", "p", "e") else "i"
    with db_lock:
        con = db()
        if not names and q.get("science"):
            names = sorted({r[0] for r in con.execute("SELECT DISTINCT name FROM flow WHERE kind='i'") if is_science(r[0])},
                           key=science_key)
        series = {}
        for nm in names:
            if nm == "_power":
                rows = con.execute("SELECT tick, ts, produced, consumed FROM flow WHERE kind='e' AND name LIKE '\\_total\\_%' ESCAPE '\\' "
                                   "AND ts>=? ORDER BY tick", (since - 3600,)).fetchall()
            else:
                rows = con.execute("SELECT tick, ts, produced, consumed FROM flow WHERE kind=? AND name=? AND ts>=? ORDER BY tick",
                                   (kind, nm, since - 3600)).fetchall()
            pts = []
            for (t0, _, p0, c0), (t1, ts1, p1, c1) in zip(rows, rows[1:]):
                if t1 > t0 and ts1 >= since:
                    m = 3600 / (t1 - t0)
                    pts.append([ts1, round(max(0, p1 - p0) * m, 2), round(max(0, c1 - c0) * m, 2)])
            series[nm] = {"label": gamenames.NAMES.get(nm), "points": pts}
        samples = con.execute("SELECT ts, data FROM samples WHERE ts>=? ORDER BY tick", (since,)).fetchall()
        con.close()
    step = max(1, len(samples) // 400)
    smp = []
    for ts, d in samples[::step]:
        d = json.loads(d)
        smp.append({"ts": ts, "evo": d.get("evo"), "researched": d.get("researched"), "online": d.get("online"),
                    "buildings": (d.get("counts") or {}).get("_all"), "belts": (d.get("counts") or {}).get("transport-belt"), "pmax": d.get("pmax")})
    return {"series": series, "samples": smp}


def api_items(q):
    """All item/fluid names ever produced, for the search box."""
    with db_lock:
        con = db()
        rows = con.execute("SELECT kind, name FROM first_seen").fetchall()
        con.close()
    return {"items": sorted(({"kind": k, "name": n, "label": gamenames.NAMES.get(n)} for k, n in rows), key=lambda x: x["label"].lower())}
