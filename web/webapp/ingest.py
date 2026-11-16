"""Background reader: picks up the mod's files (events, stats, live positions) every few seconds,
stores history, starts map renders and keeps the database tidy."""

import json
import os
import sqlite3
import threading
import time
import traceback
from pathlib import Path

from . import gamenames, livemap
from .config import FACTORIO, FLOW_EVERY_TICKS, OUT, log
from .gamenames import Names
from .render import render_pending
from .state import STATE_MEM, read_live, tick_to_ts
from .store import as_list, db, db_lock, kv_get, kv_set, read_json


def ingest_events(con):
    """Store new chat/join/death/... events. The file is renamed first so the mod starts a fresh one."""
    src = OUT / "events.jsonl"
    if src.exists():
        try:
            src.rename(OUT / f"events.{int(time.time() * 1000)}.ingest")  # the mod starts a new file on its next write
        except OSError:
            pass
    # also batches a crash left behind (renamed but never stored)
    for tmp in sorted(OUT.glob("events.*.ingest")):
        ingest_event_file(con, tmp)


def ingest_event_file(con, tmp):
    """Store one renamed events file in the database, then delete it."""
    rows = []
    for line in tmp.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        rows.append((tick_to_ts(e.get("tick")), e.get("tick"), e.get("type"), json.dumps(e)))
        if e.get("type") in ("join", "leave") and e.get("player"):
            con.execute("INSERT OR REPLACE INTO players VALUES (?,?)", (e["player"], rows[-1][0]))
    con.executemany("INSERT INTO events (ts, tick, type, data) VALUES (?,?,?,?)", rows)
    tmp.unlink()


def ingest_stats(con):
    """Store production totals and a stats sample when stats.json has a newer tick than last time."""
    s = read_json(OUT / "stats.json")
    if not s or not isinstance(s.get("tick"), (int, float)):
        return
    old = STATE_MEM["stats"]
    if old and old["tick"] == s["tick"]:
        return
    if old and s["tick"] < old["tick"]:
        # an older save was loaded or a new map started: rates across the jump would be nonsense
        log(f"game tick went back ({old['tick']} -> {s['tick']}): older save or new map, starting rates over")
        STATE_MEM["flows"].clear()
    now = time.time()
    STATE_MEM["stats"] = s
    flows = {k: s.get(k) or {"i": {}, "o": {}} for k in ("items", "fluids", "kills", "pollution")}
    pw = s.get("power")
    if pw:  # electric stats count consumers as input and generators as output: flip to made/used like items
        flows["power"] = {"i": pw.get("o") or {}, "o": pw.get("i") or {}, "id": pw.get("id")}
    # other pollutants (Space Age: spores on Gleba) get their own rates under "pol:<name>"
    for kind, v in ((s.get("pollution") or {}).get("other") or {}).items():
        if isinstance(v, dict):
            flows["pol:" + kind] = v
    for k in flows:  # the mod writes {} for empty dicts, which is fine
        flows[k] = dict(flows[k], i=flows[k].get("i") or {}, o=flows[k].get("o") or {})
    STATE_MEM["flows"].append((s["tick"], now, flows))
    r = rates()
    if r and r["window_min"] >= MIN_RATE_MIN:
        kv_set(con, "last_rates", r)  # shown while the server is paused / after a web restart

    players = as_list(s.get("players"))
    for p in players:
        if p.get("on"):
            con.execute("INSERT OR REPLACE INTO players VALUES (?,?)", (p["n"], now))

    counts = (s.get("counts") or {}).get("total") or {}
    sample = {"evo": s.get("evolution"), "researched": (s.get("research") or {}).get("researched"),
              "rockets": s.get("rockets"), "online": sum(1 for p in players if p.get("on")),
              "counts": counts, "se": s.get("se"),
              "pmax": (pw.get("cap") or 0) + (pw.get("solar") or 0) if pw and pw.get("cap") else None}
    con.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?)", (s["tick"], now, json.dumps(sample)))

    last_flow = kv_get(con, "last_flow_tick", None)
    if last_flow is None or s["tick"] - last_flow >= FLOW_EVERY_TICKS or s["tick"] < last_flow:
        rows = []
        for kind, key in (("i", "items"), ("f", "fluids"), ("k", "kills"), ("p", "pollution"), ("e", "power")):
            f = flows.get(key)
            if not f:
                continue
            for name in set(f["i"]) | set(f["o"]):
                rows.append((s["tick"], now, kind, name, f["i"].get(name, 0), f["o"].get(name, 0)))
            if kind in ("p", "e"):  # totals, for the history charts
                rows.append((s["tick"], now, kind, "_total" if kind == "p" else f"_total_{f.get('id')}",
                             sum(f["i"].values()), sum(f["o"].values())))
        con.executemany("INSERT OR REPLACE INTO flow VALUES (?,?,?,?,?,?)", rows)
        kv_set(con, "last_flow_tick", s["tick"])

    # milestones: the first time an item/fluid is ever produced
    baseline = kv_get(con, "baseline_done", False)
    for kind, key in (("i", "items"), ("f", "fluids")):
        for name, c in flows[key]["i"].items():
            if c > 0:
                cur = con.execute("INSERT OR IGNORE INTO first_seen VALUES (?,?,?,?,?)",
                                  (kind, name, s["tick"], now, 0 if baseline else 1))
                if cur.rowcount and baseline:
                    con.execute("INSERT INTO events (ts, tick, type, data) VALUES (?,?,?,?)",
                                (now, s["tick"], "first", json.dumps({"type": "first", "kind": kind, "name": name})))
    if not baseline:
        kv_set(con, "baseline_done", True)


def thin_history(con):
    """History older than 30 days keeps one point per hour of play (graphs stay right, the database stays small).
    Production numbers are running totals, so dropping points only makes old graphs coarser, never wrong."""
    cut = time.time() - 30 * 86400
    hour = 216000
    con.execute("DELETE FROM flow WHERE ts < ? AND tick NOT IN "
                "(SELECT MIN(tick) FROM flow WHERE ts < ? GROUP BY tick / ?)", (cut, cut, hour))
    con.execute("DELETE FROM samples WHERE ts < ? AND tick NOT IN "
                "(SELECT MIN(tick) FROM samples WHERE ts < ? GROUP BY tick / ?)", (cut, cut, hour))


def mods_stamp():
    """Changes whenever a mod is added, removed, updated or switched on/off on the server."""
    try:
        mods = FACTORIO / "mods"
        return (mods / "mod-list.json").stat().st_mtime, tuple(sorted(z.name for z in mods.glob("*.zip")))
    except OSError:
        return None


def ingest_loop():
    """Runs forever in a background thread: read the mod's files, store them, update / render the map."""
    last_render = 0
    last_thin = 0
    stamp = mods_stamp()
    while True:
        try:
            read_live()
            with db_lock:
                con = db()
                ingest_events(con)
                ingest_stats(con)
                con.commit()
                con.close()
            st = mods_stamp()
            if st != stamp:  # new / changed mods: pick up their English names without a restart
                stamp = st
                gamenames.NAMES = Names()
                log(f"mods changed, names reloaded: {sum(len(v) for v in gamenames.NAMES.d.values())}")
            if time.time() - last_thin > 86400:
                last_thin = time.time()
                with db_lock:
                    con = db()
                    thin_history(con)
                    con.commit()
                    con.close()
            livemap.ingest()  # mod 0.4+: changed chunks
            livemap.tidy()
            if livemap.due() and not STATE_MEM["rendering"]:
                threading.Thread(target=livemap.publish, daemon=True).start()
            elif time.time() - last_render > 20 and not STATE_MEM["rendering"]:
                last_render = time.time()
                threading.Thread(target=render_pending, daemon=True).start()  # mod 0.3 and older: whole snapshots
        except Exception:
            traceback.print_exc()
        time.sleep(5)


MIN_RATE_MIN = 5  # rates over fewer game minutes than this jump around too much to show


def rates(window_ticks=36000):
    """Per-minute production/consumption from two in-memory samples ~10 game minutes apart."""
    fl = list(STATE_MEM["flows"])
    if len(fl) < 2:
        return None
    t1, _, a = fl[-1]
    base = fl[0]
    for f in fl:
        if t1 - f[0] <= window_ticks:
            base = f
            break
    t0, _, b = base
    if t1 <= t0:
        return None
    mul = 3600 / (t1 - t0)
    out = {}
    for key in ["items", "fluids", "kills", "pollution", "power"] + sorted(k for k in a if k.startswith("pol:")):
        out[key] = {}
        fa, fb = a.get(key), b.get(key)
        if not fa or not fb or fa.get("id") != fb.get("id"):
            continue
        for name in set(fa["i"]) | set(fa["o"]):
            p = max(0, fa["i"].get(name, 0) - fb["i"].get(name, 0)) * mul
            c = max(0, fa["o"].get(name, 0) - fb["o"].get(name, 0)) * mul
            if p > 0.01 or c > 0.01:
                out[key][name] = (p, c)
    out["window_min"] = (t1 - t0) / 3600
    return out
