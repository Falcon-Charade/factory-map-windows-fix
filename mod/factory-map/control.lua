-- factory-map: exports map + stats for the server website.
--
-- Everything here only READS the game. Files are written with for_player = 0,
-- so only the server writes them (clients run the same code, as Factorio requires,
-- but write nothing). Output lands in script-output/factory-map/.

local OUT = "factory-map/"

-- Crash guard: an error in this mod must never take the server down. Every handler runs in pcall;
-- errors go to the server log (first 20 only, so a repeating error can't flood it).
local errors_logged = 0
local function safe(fn)
  return function(...)
    local ok, err = pcall(fn, ...)
    if not ok and errors_logged < 20 then
      errors_logged = errors_logged + 1
      log("Factory Map error (ignored, game continues): " .. tostring(err))
    end
  end
end
local function on_event(ev, fn, filters) script.on_event(ev, safe(fn), filters) end
local function on_nth_tick(n, fn) script.on_nth_tick(n, safe(fn)) end
local function add_command(name, help, fn)
  commands.add_command(name, help, function(cmd)
    local ok, err = pcall(fn, cmd)
    if not ok then
      local msg = "Factory Map: command failed: " .. tostring(err)
      local p = cmd.player_index and game.get_player(cmd.player_index)
      if p then p.print(msg) else rcon.print(msg) end
    end
  end)
end
local LIVE_EVERY = 300       -- 5 s: player + train positions
local STATS_EVERY = 3600     -- 60 s: production totals, research, players
local POLL_EVERY = 18000     -- 5 min: pollution cloud map (read a few dozen chunks per tick, never all at once)
local POLL_PER_TICK = 64
local POWER_REPICK = 432000  -- 2 h: re-pick which electric network is "the main one" (also when its pole is removed)

local function write(name, data, append)
  helpers.write_file(OUT .. name, data, append or false, 0)
end

local function event_out(t)
  t.tick = game.tick
  write("events.jsonl", helpers.table_to_json(t) .. "\n", true)
end

local function player_force()
  return game.forces["player"]
end

-- Admin switches (settings.lua): what the website may show.
local function shared(what)
  local s = settings.global["factory-map-" .. what]
  return s == nil or s.value  -- missing setting (shouldn't happen) = on, like the defaults
end

-- Enemies are whatever force the players are at war with: biters ("enemy"), and any extra
-- enemy forces other mods add. Returns {force_index = true}. Cheap: there are only a few forces.
local function enemy_forces(force)
  local set = {}
  for _, f in pairs(game.forces) do
    if f ~= force and f.name ~= "neutral" and force.is_enemy(f) then set[f.index] = true end
  end
  return set
end

-- Entity types that count as enemies on the Factory tab's kill counter (vanilla, Space Age and mods:
-- mods' biters, spitters, nests and worms reuse these types, so they're caught whatever they're named).
local ENEMY_TYPES = {["unit"] = true, ["unit-spawner"] = true, ["turret"] = true,
                     ["segmented-unit"] = true, ["spider-unit"] = true}

-- ------------------------------------------------------------------ surfaces

local function se_call(fn, args)
  if not remote.interfaces["space-exploration"] or not remote.interfaces["space-exploration"][fn] then return nil end
  local ok, res = pcall(remote.call, "space-exploration", fn, args)
  if ok then return res end
  return nil
end

-- SE zone info never changes for a surface; remote calls copy SE's zone tables, so cache them.
-- (Derived purely from game state, so every peer computes the same values.)
local surface_cache = {}

local function surface_kind(surface)
  local c = surface_cache[surface.index]
  if c and c.kind ~= nil then return c.kind or nil end
  local kind = se_call("get_surface_type", {surface_index = surface.index})
  if not kind then  -- no SE: base game / Space Age planets and space platforms
    if surface.platform then kind = "platform"
    elseif surface.planet or surface.index == 1 then kind = "planet" end
  end
  surface_cache[surface.index] = c or {}
  surface_cache[surface.index].kind = kind or false
  return kind
end

local SKIP_KINDS = {starmap = true}

local function surface_eligible(surface)
  local n = surface.name
  if n:find("^bpsb%-") or n:find("^EE_") or n:find("sandbox") then return false end
  if SKIP_KINDS[surface_kind(surface) or ""] then return false end
  return true
end

local function surface_info(surface)
  local c = surface_cache[surface.index] or {}
  surface_cache[surface.index] = c
  if c.zone == nil then
    local zone = se_call("get_zone_from_surface_index", {surface_index = surface.index})
    c.zone = zone and zone.name or false
    c.parent = zone and zone.parent_index or false
  end
  return {index = surface.index, name = surface.name, kind = surface_kind(surface),
          zone = c.zone or (surface.platform and surface.platform.valid and surface.platform.name) or nil,
          parent = c.parent or nil}
end

-- ------------------------------------------------------------------ live map
--
-- The website's map is kept up to date a little at a time instead of in big snapshots:
--   * explored chunks are tracked as they get charted (on_chunk_charted), so the mod never has to list
--     every chunk at once (that listing was a ~45 ms freeze on a big map);
--   * building, mining, deaths, landfill etc. mark their chunk "changed"; changed chunks are re-read a
--     second later, buildings only (cheap, ~0.06 ms) unless the ground changed too;
--   * a slow sweep re-reads every explored chunk in turn (ground + buildings, ~1.2 ms each, one every few
--     ticks), which picks up what no event reports: ore being mined down, trees, biters spreading;
--   * on install and after /map-refresh, a faster pass (chunks-per-tick setting) reads everything once.
-- Changed chunks are appended to map.jsonl; the website re-draws the map from them every minute.

-- Prototype info cache (derived purely from prototypes, so identical on every peer).
local proto_cache = {}
local SKIP_TYPES = {
  ["character"] = true, ["entity-ghost"] = true, ["tile-ghost"] = true, ["item-request-proxy"] = true,
  ["character-corpse"] = true, ["corpse"] = true, ["rail-remnants"] = true, ["item-entity"] = true,
  ["projectile"] = true, ["unit"] = true, ["construction-robot"] = true, ["logistic-robot"] = true,
  ["combat-robot"] = true, ["highlight-box"] = true, ["speech-bubble"] = true, ["smoke-with-trigger"] = true,
  ["fire"] = true, ["sticker"] = true, ["explosion"] = true, ["particle-source"] = true, ["arrow"] = true,
  ["beam"] = true, ["stream"] = true, ["artillery-flare"] = true, ["artillery-projectile"] = true,
  ["cliff"] = true, ["deconstructible-tile-proxy"] = true, ["spider-leg"] = true, ["fish"] = true,
  ["simple-entity"] = true, ["resource"] = false, ["tree"] = false, ["capture-robot"] = true,
  -- Space Age things that move or drift (demolishers are shown by territory instead, see territories)
  ["segmented-unit"] = true, ["segment"] = true, ["spider-unit"] = true, ["asteroid"] = true,
  -- trains and vehicles move: read a chunk at a time they'd leave copies behind. The website draws
  -- them from their live positions instead.
  ["locomotive"] = true, ["cargo-wagon"] = true, ["fluid-wagon"] = true, ["artillery-wagon"] = true,
  ["infinity-cargo-wagon"] = true, ["car"] = true, ["spider-vehicle"] = true,
}
local MOVING_TYPES = {["locomotive"] = true, ["cargo-wagon"] = true, ["fluid-wagon"] = true, ["artillery-wagon"] = true,
                      ["infinity-cargo-wagon"] = true, ["car"] = true, ["spider-vehicle"] = true}

local function color_hex(c)
  if not c then return nil end
  local r, g, b = c.r or c[1] or 0, c.g or c[2] or 0, c.b or c[3] or 0
  if r <= 1 and g <= 1 and b <= 1 then r, g, b = r * 255, g * 255, b * 255 end
  return string.format("%02x%02x%02x", math.floor(r + 0.5), math.floor(g + 0.5), math.floor(b + 0.5))
end

local function proto(name)
  local p = proto_cache[name]
  if p then return p end
  local ep = prototypes.entity[name]
  p = {type = ep.type}
  p.friendly = color_hex(ep.friendly_map_color) or color_hex(ep.map_color)
  p.neutral = color_hex(ep.map_color)
  p.enemy = color_hex(ep.enemy_map_color) or color_hex(ep.map_color)
  if ep.type == "ammo-turret" or ep.type == "electric-turret" or ep.type == "fluid-turret" or ep.type == "artillery-turret" then
    local ok, ap = pcall(function() return ep.attack_parameters end)
    if ok and ap then p.range = ap.range end
  elseif ep.type == "roboport" then
    local ok1, lr = pcall(function() return ep.logistic_radius end)
    local ok2, cr = pcall(function() return ep.construction_radius end)
    p.lr, p.cr = ok1 and lr or nil, ok2 and cr or nil
  end
  if ep.type == "resource" then
    p.infinite = ep.infinite_resource or nil
    p.normal = ep.normal_resource_amount
    p.category = ep.resource_category
  end
  proto_cache[name] = p
  return p
end

-- Palette: every ground tile / building / ore name gets a number, which the chunk lines use.
-- Kept in storage.live; each new entry is sent to the website before the first line that uses it.
local function pal(snap, key, hex, layer)
  local i = snap.pal[key]
  if i then return i end
  snap.npal = snap.npal + 1
  i = snap.npal
  snap.pal[key] = i
  snap.palette[i] = {k = key, c = hex, l = layer}
  return i
end

local function tile_index(snap, name)
  local i = snap.pal["t:" .. name]
  if i then return i end
  local tp = prototypes.tile[name]
  return pal(snap, "t:" .. name, color_hex(tp and tp.map_color) or "000000", 0)
end

-- One chunk as a JSON line: {s, x, y, t = ground (only when with_tiles), e = buildings, ores, trees, nests}.
-- `enemies`: {force_index = true} from enemy_forces()
local function scan_chunk(snap, surface, cx, cy, fi_player, enemies, fi_neutral, with_tiles)
  local x0, y0 = cx * 32, cy * 32
  local area = {{x0, y0}, {x0 + 32, y0 + 32}}

  local tile_rle
  if with_tiles then
    -- tiles -> 32x32 grid, run-length encoded "idx*count,idx,..."
    local grid = {}
    local tiles = surface.find_tiles_filtered{area = area}
    local n_t = #tiles
    local function at(i, x, y) local p = tiles[i].position return p.x == x0 + x and p.y == y0 + y end
    if n_t == 1024 and at(1, 0, 0) and at(32, 31, 0) and at(33, 0, 1) and at(1024, 31, 31) then
      -- tiles come back row by row; skip asking each tile for its position (halves the cost)
      local last_name, last_idx
      for i = 1, 1024 do
        local nm = tiles[i].name
        if nm ~= last_name then last_name, last_idx = nm, tile_index(snap, nm) end
        grid[i] = last_idx
      end
    else
      for _, t in pairs(tiles) do
        local p = t.position
        grid[(p.y - y0) * 32 + (p.x - x0) + 1] = tile_index(snap, t.name)
      end
    end
    local parts, n, last, run = {}, 0, nil, 0
    for i = 1, 1024 do
      local v = grid[i] or 0
      if v == last then run = run + 1
      else
        if last then n = n + 1; parts[n] = run > 1 and (last .. "*" .. run) or tostring(last) end
        last, run = v, 1
      end
    end
    n = n + 1; parts[n] = run > 1 and (last .. "*" .. run) or tostring(last)
    tile_rle = table.concat(parts, ",")
  end

  -- entities -> flat list of x,y,w,h,palette (relative to chunk origin)
  local ents, m = {}, 0
  for _, e in pairs(surface.find_entities_filtered{area = area}) do
    local pos = e.position
    if pos.x >= x0 and pos.x < x0 + 32 and pos.y >= y0 and pos.y < y0 + 32 then
      local name = e.name
      local p = proto(name)
      local fi = e.force_index
      local idx
      if fi == fi_neutral then
        if p.type == "resource" then
          idx = pal(snap, "r:" .. name, p.neutral or "8c6239", 1)
          local pe = snap.palette[idx]
          if pe.cat == nil then pe.cat, pe.inf, pe.norm = p.category or "", p.infinite, p.normal end
        elseif p.type == "tree" then idx = pal(snap, "v:" .. name, p.neutral or "2c4a22", 1) end
      elseif enemies[fi] then
        -- any enemy building (nests, worms, egg rafts, mods' enemy turrets); moving units are skipped.
        -- The type goes along so the website can tell nests from defences whatever a mod names them.
        if not SKIP_TYPES[p.type] then
          idx = pal(snap, "x:" .. name, p.enemy or "d22b2b", 2)
          local pe = snap.palette[idx]
          if not pe.t then pe.t = p.type end
          if p.type == "unit-spawner" then  -- which kinds of nest each planet has: {surface = {name = true}}
            local row = snap.nests[surface.index]
            if not row then row = {}; snap.nests[surface.index] = row end
            row[name] = true
          end
        end
      elseif not SKIP_TYPES[p.type] then
        idx = pal(snap, "e:" .. name, p.friendly, 3)  -- nil colour -> website picks a default by type
        local pe = snap.palette[idx]
        if not pe.t then pe.t, pe.r, pe.lr, pe.cr = p.type, p.range, p.lr, p.cr end
      end
      if idx then
        local bb = e.bounding_box
        local x1, y1 = math.floor(bb.left_top.x - x0), math.floor(bb.left_top.y - y0)
        local x2, y2 = math.ceil(bb.right_bottom.x - x0), math.ceil(bb.right_bottom.y - y0)
        if x2 <= x1 then x2 = x1 + 1 end
        if y2 <= y1 then y2 = y1 + 1 end
        ents[m + 1], ents[m + 2], ents[m + 3], ents[m + 4], ents[m + 5] = x1, y1, x2 - x1, y2 - y1, idx
        ents[m + 6] = (fi == fi_neutral and p.type == "resource") and e.amount or 0
        m = m + 6
      end
    end
  end
  -- entities as a "x,y,w,h,p,..." string: table_to_json writes large arrays as {"1":..} objects
  return helpers.table_to_json{s = surface.index, x = cx, y = cy, t = tile_rle, e = table.concat(ents, ",")}
end

-- Space Age: demolishers live in territories (fixed areas they patrol and defend). The map shows the
-- explored part of each one, with what lives there. Empty without Space Age (no territories exist).
-- `charted(cx, cy)`: whether the players have explored that chunk.
local function territories(s, charted)
  local ok, list = pcall(function() return s.get_territories() end)
  if not ok or not list or #list == 0 then return nil end
  local out = {}
  for _, t in pairs(list) do
    if t.valid then
      local cs, n = {}, 0
      for _, c in pairs(t.get_chunks()) do
        local p = c.position or c
        if charted(p.x, p.y) then cs[n + 1], cs[n + 2] = p.x, p.y; n = n + 2 end
      end
      if n > 0 then
        local units = {}
        for _, u in pairs(t.get_segmented_units()) do
          if u.valid then units[#units + 1] = u.prototype.name end
        end
        out[#out + 1] = {c = table.concat(cs, ","), u = units}
      end
    end
  end
  return #out > 0 and out or nil
end

local SWEEP_MIN_GAP = 4   -- the sweep reads at most one chunk every 4 ticks (~0.3 ms per tick on average)
local DIRTY_DELAY = 60    -- re-read a changed chunk 1 s after its first change, so a burst of building is read once
local DIRTY_PER_TICK = 1  -- buildings-only re-reads per tick (0.05-0.4 ms each; 60 a second is plenty)

-- A chunk position as one number (storage-friendly table key) and back.
local function ckey(cx, cy) return (cx + 32768) * 65536 + (cy + 32768) end
local function cpos(k)
  local hx = math.floor(k / 65536)
  return hx - 32768, k - hx * 65536 - 32768
end

-- storage.live (L):
--   surf[si] = {list = {key, ...}, at = {key = position in list}, n = count, cur = sweep position}
--   skip[si] = true for surfaces never shown (SE starmap, editor sandboxes)
--   q / qh / qt: changed chunks waiting to be re-read, oldest first: {si, key, ground_changed, due_tick}
--   qset[si * 2^32 + key] = position in q (each chunk is queued once)
--   fast = chunks left in the first pass (0 = normal slow sweep), gap = ticks between sweep reads
--   pal / palette / npal / pal_sent: the palette and how much of it the website already has
--   nests[si] = {spawner name = true}: which kinds of nest each planet has (for the evolution card)
--   buf = lines waiting to be written at the end of the tick (kept in storage so every peer agrees)
local function new_live()
  return {epoch = game.tick, surf = {}, skip = {}, total = 0, q = {}, qh = 1, qt = 0, qset = {},
          fast = 0, gap = SWEEP_MIN_GAP, next_full = 0, pal = {}, palette = {}, npal = 0, pal_sent = 0,
          nests = {}, buf = {}}
end

local function emit(L, t) L.buf[#L.buf + 1] = helpers.table_to_json(t) end

local function is_charted(S)
  return function(cx, cy) return S.at[ckey(cx, cy)] ~= nil end
end

-- name / kind / SE zone of a surface plus its explored demolisher territories
local function surface_line(L, si)
  local s, S = game.get_surface(si), L.surf[si]
  if not (s and S) then return end
  local info = surface_info(s)
  info.n = S.n
  info.terr = territories(s, is_charted(S))
  emit(L, {surf = info})
end

local function track_chunk(L, si, cx, cy)  -- true if the chunk is new
  local S = L.surf[si]
  if not S then S = {list = {}, at = {}, n = 0, cur = 1}; L.surf[si] = S end
  local k = ckey(cx, cy)
  if S.at[k] then return false end
  S.n = S.n + 1
  S.list[S.n], S.at[k] = k, S.n
  L.total = L.total + 1
  return true
end

local function untrack_chunk(L, si, cx, cy)
  local S = L.surf[si]
  local k = ckey(cx, cy)
  local i = S and S.at[k]
  if not i then return end
  local last = S.list[S.n]
  S.list[i], S.at[last] = last, i
  S.list[S.n], S.at[k] = nil, nil
  S.n = S.n - 1
  L.total = L.total - 1
  emit(L, {gone = {si, cx, cy}})
end

local function drop_surface(L, si)
  local S = L.surf[si]
  if not S then return end
  L.total = L.total - S.n
  L.surf[si] = nil
  L.nests[si] = nil
  emit(L, {drop = si})
end

-- Queue a chunk to be re-read. `ground`: tiles changed too (landfill, concrete, new chunk).
local function mark(si, cx, cy, ground)
  local L = storage.live
  local S = L and L.surf[si]
  if not S then return end
  local k = ckey(cx, cy)
  if not S.at[k] then return end
  local qk = si * 4294967296 + k
  local i = L.qset[qk]
  if i then
    if ground then L.q[i][3] = true end
    return
  end
  L.qt = L.qt + 1
  L.q[L.qt] = {si, k, ground or false, game.tick + DIRTY_DELAY}
  L.qset[qk] = L.qt
end

local function mark_entity(ent)
  if not (ent and ent.valid) or SKIP_TYPES[ent.type] then return end
  local p = ent.position
  mark(ent.surface_index, math.floor(p.x / 32), math.floor(p.y / 32), false)
end

local function mark_tiles(e)
  local L = storage.live
  if not (L and e.surface_index and L.surf[e.surface_index] and e.tiles) then return end
  local last
  for _, t in pairs(e.tiles) do
    local p = t.position or t
    local cx, cy = math.floor((p.x or p[1]) / 32), math.floor((p.y or p[2]) / 32)
    local k = ckey(cx, cy)
    if k ~= last then last = k; mark(e.surface_index, cx, cy, true) end
  end
end

-- Read every explored chunk once, quickly (chunks-per-tick setting); the website shows the new map when done.
local function start_refresh(L)
  L.fast = L.total
  L.pal_sent = 0  -- send the whole palette again (harmless, and fixes a website that lost it)
  emit(L, {start = game.tick, chunks = L.total})
  if L.total == 0 then emit(L, {ready = game.tick}) end
end

-- New install / update from a version without the live map: list what's explored once (while the game loads).
local function init_live()
  local L = new_live()
  storage.live = L
  local force = player_force()
  for _, s in pairs(game.surfaces) do
    if surface_eligible(s) then
      for c in s.get_chunks() do
        if force.is_chunk_charted(s, {c.x, c.y}) then track_chunk(L, s.index, c.x, c.y) end
      end
    else
      L.skip[s.index] = true
    end
  end
  for si in pairs(L.surf) do surface_line(L, si) end
  start_refresh(L)
end

local function sweep_gap(L)
  local minutes = settings.global["factory-map-sweep-minutes"].value
  return math.max(SWEEP_MIN_GAP, math.floor(minutes * 3600 / math.max(1, L.total)))
end

-- Called every tick: re-read due changed chunks, then let the sweep read its next chunk, then write.
local function live_step(tick)
  local L = storage.live
  local fi_player, enemies, fi_neutral
  local function scan(si, k, ground)
    local s = game.get_surface(si)
    if not s then return end
    if not enemies then
      local force = player_force()
      fi_player, enemies, fi_neutral = force.index, enemy_forces(force), game.forces["neutral"].index
    end
    local cx, cy = cpos(k)
    L.buf[#L.buf + 1] = scan_chunk(L, s, cx, cy, fi_player, enemies, fi_neutral, ground)
  end

  if tick % 600 == 0 then  -- the map grows: keep a full round near the set minutes
    L.gap = sweep_gap(L)
    if L.next_full > tick + L.gap then L.next_full = tick + L.gap end
  end
  -- Reads with ground (the expensive kind, ~1.1 ms): during a full refresh chunks-per-tick of them,
  -- otherwise at most one per tick: a changed chunk whose ground changed (at most every SWEEP_MIN_GAP ticks),
  -- else the sweep's next chunk when its turn has come.
  local fast = L.fast > 0
  local full_left = fast and settings.global["factory-map-chunks-per-tick"].value or 0
  if not fast and tick >= (L.next_qfull or 0) then full_left = 1 end
  local did_full = false

  -- 1. changed chunks, oldest first
  local ent_left = DIRTY_PER_TICK
  while L.qh <= L.qt do
    local e = L.q[L.qh]
    if e[4] > tick then break end
    if e[3] then
      if full_left <= 0 then break end
      full_left = full_left - 1
      did_full = true
    else
      if ent_left <= 0 then break end
      ent_left = ent_left - 1
    end
    L.q[L.qh] = nil
    L.qset[e[1] * 4294967296 + e[2]] = nil
    L.qh = L.qh + 1
    local S = L.surf[e[1]]
    if S and S.at[e[2]] then scan(e[1], e[2], e[3]) end
  end
  if L.qh > L.qt then L.q, L.qh, L.qt = {}, 1, 0 end
  if did_full and not fast then L.next_qfull = tick + SWEEP_MIN_GAP end
  if not fast then
    full_left = 0
    if not did_full and tick >= L.next_full then  -- the sweep's turn (waits a tick if a changed chunk just went)
      full_left = 1
      L.next_full = tick + L.gap
    end
  end

  -- 2. the sweep: next explored chunk, surface by surface
  local guard = 0
  while full_left > 0 and guard < 4 do
    local si = L.sweep_si
    local S = si and L.surf[si]
    if not S then
      si = next(L.surf)
      if not si then break end
      L.sweep_si, S = si, L.surf[si]
      S.cur = 1
    end
    if S.cur > S.n then
      surface_line(L, si)  -- once per round: name, explored demolisher territories
      S.cur = 1
      L.sweep_si = next(L.surf, si) or next(L.surf)
      guard = guard + 1
    else
      local k = S.list[S.cur]
      S.cur = S.cur + 1
      scan(si, k, true)
      full_left = full_left - 1
      if L.fast > 0 then
        L.fast = L.fast - 1
        if L.fast == 0 then emit(L, {ready = tick}) end
      end
    end
  end

  -- 3. write (new palette entries first, so the website knows every number used below)
  if #L.buf > 0 then
    local head = helpers.table_to_json{ep = L.epoch, tick = tick}
    if L.npal > L.pal_sent then
      local list = {}
      for i = L.pal_sent + 1, L.npal do list[#list + 1] = L.palette[i] end
      head = head .. "\n" .. helpers.table_to_json{pal = list, from = L.pal_sent + 1}
      L.pal_sent = L.npal
    end
    write("map.jsonl", head .. "\n" .. table.concat(L.buf, "\n") .. "\n", true)
    L.buf = {}
  end
end

local function live_status()
  local L = storage.live
  if not L then return "Live map not started." end
  local ns = 0
  for _ in pairs(L.surf) do ns = ns + 1 end
  local round = L.total * (L.gap or SWEEP_MIN_GAP) / 3600
  local msg = string.format("Live map: %d explored chunks on %d surfaces. %d changed chunks waiting. Sweep: one chunk every %d ticks (full round ~%.0f min).",
    L.total, ns, L.qt - L.qh + 1, L.gap or SWEEP_MIN_GAP, round)
  if L.fast > 0 then msg = msg .. string.format(" Full refresh running: %d chunks left.", L.fast) end
  return msg
end

-- ------------------------------------------------------------------ live positions

-- ------------------------------------------------------------------ vehicles (cars, tanks, spidertrons)

local VEHICLE_TYPES = {car = true, ["spider-vehicle"] = true}

local function track_vehicle(ent)
  if ent and ent.valid and VEHICLE_TYPES[ent.type] and ent.unit_number then
    storage.vehicles = storage.vehicles or {}
    storage.vehicles[ent.unit_number] = ent
  end
end

-- one full search, only on install/mod update (the same cost as one snapshot listing)
local function find_all_vehicles()
  storage.vehicles = {}
  for _, s in pairs(game.surfaces) do
    for _, e in pairs(s.find_entities_filtered{type = {"car", "spider-vehicle"}}) do track_vehicle(e) end
  end
end

local function vehicles_out()
  local out = {}
  for un, e in pairs(storage.vehicles or {}) do
    if e.valid then
      local p = e.position
      local d = e.get_driver()
      local driver = d and (d.object_name == "LuaPlayer" and d or d.player)
      out[#out + 1] = {u = un, s = e.surface.index, x = math.floor(p.x), y = math.floor(p.y), n = e.name,
                       l = e.entity_label, t = e.type == "spider-vehicle" and "spider" or "car",
                       d = driver and shared("share-positions") and driver.name or nil, f = e.force.name ~= "player" and e.force.name or nil}
    else
      storage.vehicles[un] = nil
    end
  end
  return out
end

-- per-player counters (built, mined, crafted, kills, walked, driven)
local function pstat(index, key, n)
  local p = index and game.get_player(index)
  if not p then return end
  storage.pstats = storage.pstats or {}
  local t = storage.pstats[p.name]
  if not t then t = {}; storage.pstats[p.name] = t end
  t[key] = (t[key] or 0) + (n or 1)
end

local function pos_of(player)
  local ok, s, p = pcall(function() return player.physical_surface, player.physical_position end)
  if ok and s then return s, p end
  return player.surface, player.position
end

-- `leaving`: index of a player who is disconnecting right now (they can still be listed as connected)
local function write_live(leaving)
  local players = {}
  for _, pl in pairs(game.connected_players) do
    if pl.index ~= leaving then
      local s, p = pos_of(pl)
      storage.lastpos = storage.lastpos or {}
      local lp = storage.lastpos[pl.index]
      if lp and lp.s == s.index then
        local d = math.sqrt((p.x - lp.x) ^ 2 + (p.y - lp.y) ^ 2)
        if d >= 1 and d < 600 then pstat(pl.index, pl.vehicle and "dr" or "w", math.floor(d)) end  -- bigger jumps are teleports
      end
      storage.lastpos[pl.index] = {s = s.index, x = p.x, y = p.y}
      if shared("share-positions") then  -- off: nobody is drawn on the map (play time etc. still counts)
        players[#players + 1] = {n = pl.name, s = s.index, x = math.floor(p.x * 10) / 10, y = math.floor(p.y * 10) / 10,
                                 v = pl.vehicle and pl.vehicle.name or nil, dead = pl.character == nil or nil,
                                 c = color_hex(pl.chat_color)}
      end
    end
  end
  local trains = {}
  local ok, list = pcall(function() return game.train_manager.get_trains{force = player_force()} end)
  if ok and list then
    for _, t in pairs(list) do
      local loco = t.front_stock
      if loco and loco.valid then
        local p = loco.position
        trains[#trains + 1] = {i = t.id, s = loco.surface.index, x = math.floor(p.x), y = math.floor(p.y),
                               m = t.manual_mode or nil, sp = math.floor(math.abs(t.speed) * 216 + 0.5),
                               st = t.station and t.station.backer_name or nil, n = #t.carriages}
      end
    end
  end
  -- map tags (the pins players place on the map)
  local tags = {}
  local force = player_force()
  for _, s in pairs(game.surfaces) do
    local ok, list = pcall(force.find_chart_tags, s)
    if ok and list then
      for _, t in pairs(list) do
        local p = t.position
        tags[#tags + 1] = {s = s.index, x = math.floor(p.x), y = math.floor(p.y), t = t.text ~= "" and t.text or nil,
                           i = t.icon and t.icon.name or nil, it = t.icon and t.icon.type or nil,
                           by = t.last_user and t.last_user.name or nil}
      end
    end
  end
  local attacks = {}
  if not shared("record-attacks") then storage.attacks = nil end  -- switched off: forget ongoing ones too
  for key, a in pairs(storage.attacks or {}) do
    if game.tick - a.last > 18000 then storage.attacks[key] = nil  -- forget after 5 min of quiet
    else attacks[#attacks + 1] = {s = a.s, x = a.x, y = a.y, lost = a.lost, first = a.first, last = a.last, what = a.what} end
  end
  write("live.json", helpers.table_to_json{tick = game.tick, players = players, trains = trains, tags = tags, attacks = attacks,
                                           vehicles = vehicles_out()})
end

-- ------------------------------------------------------------------ stats


local function flow_totals(getter)
  local i, o = {}, {}
  local force = player_force()
  for _, s in pairs(game.surfaces) do
    local ok, st = pcall(getter, force, s)
    if ok and st then
      for k, v in pairs(st.input_counts) do i[k] = (i[k] or 0) + v end
      for k, v in pairs(st.output_counts) do o[k] = (o[k] or 0) + v end
    end
  end
  return {i = i, o = o}
end

local function tech_counts()
  local r, t = 0, 0
  for _, tech in pairs(player_force().technologies) do
    if tech.enabled then
      t = t + 1
      if tech.researched then r = r + 1 end
    end
  end
  return {r = r, t = t}
end

-- what each enemy spawner can produce, by evolution (from prototypes, so mods are respected); built once per load
local spawn_table
local function get_spawn_table()
  if spawn_table then return spawn_table end
  spawn_table = {}
  for name, proto in pairs(prototypes.get_entity_filtered{{filter = "type", type = "unit-spawner"}}) do
    local ok, units = pcall(function() return proto.result_units end)
    if ok and units and #units > 0 then
      local list = {}
      for _, u in pairs(units) do
        local pts = {}
        for _, sp in pairs(u.spawn_points or {}) do
          pts[#pts + 1] = {sp.evolution_factor, sp.weight}
        end
        list[#list + 1] = {u = u.unit, p = pts}
      end
      spawn_table[name] = list
    end
  end
  return spawn_table
end

local function evolution_info(surface)
  local enemy = game.forces["enemy"]
  if not (enemy and surface) then return nil end
  -- only the nests this planet really has (Space Age: Gleba's egg rafts don't attack Nauvis);
  -- until the live map has read the planet nobody knows yet, so list them all
  local spawn = get_spawn_table()
  local here = storage.live and storage.live.nests[surface.index]
  if here and next(here) then
    local only = {}
    for name, list in pairs(spawn) do if here[name] then only[name] = list end end
    if next(only) then spawn = only end
  end
  local function f(fn) local ok, v = pcall(function() return enemy[fn](surface) end); return ok and v or nil end
  local ev = game.map_settings.enemy_evolution
  local ex = game.map_settings.enemy_expansion
  return {
    time = f("get_evolution_factor_by_time"),
    pollution = f("get_evolution_factor_by_pollution"),
    spawners = f("get_evolution_factor_by_killing_spawners"),
    settings = {enabled = ev.enabled, time = ev.time_factor, pollution = ev.pollution_factor, destroy = ev.destroy_factor,
                expansion = ex.enabled},
    spawn = spawn,
  }
end

-- ------------------------------------------------------------------ pollution cloud (chunk map)

-- Surfaces where no pollution can exist (e.g. Space Age platforms) are never read.
local function has_pollution(s)
  local ok, t = pcall(function() return s.pollutant_type end)
  return not ok or t ~= nil
end

local function poll_start()
  if not game.map_settings.pollution.enabled then
    storage.poll = nil
    write("pollution.json", helpers.table_to_json{tick = game.tick, done = game.tick, surfaces = {}, totals = {}, off = true})
    return
  end
  local L = storage.live
  if not L then return end
  local queue = {}
  for si, S in pairs(L.surf) do
    local s = game.get_surface(si)
    if s and S.n > 0 and has_pollution(s) then queue[#queue + 1] = si end
  end
  if #queue == 0 then return end  -- nothing explored that can pollute
  storage.poll = {queue = queue, qi = 1, ci = 1, out = {}, parts = {}, tick = game.tick}
end

-- POLL_PER_TICK explored chunks per tick (from the live map's chunk lists), one surface after another
local function poll_step()
  local P = storage.poll
  local si = P.queue[P.qi]
  local s = si and game.get_surface(si)
  local S = s and storage.live and storage.live.surf[si]
  if not S then
    if si then P.qi = P.qi + 1; P.ci = 1; return end
    -- done: one string per surface, "cx,cy,amount,..." for polluted chunks only
    local surfaces, totals = {}, {}
    for k, v in pairs(P.out) do surfaces[tostring(k)] = table.concat(v, ",") end
    for k, v in pairs(P.parts) do totals[tostring(k)] = v end
    write("pollution.json", helpers.table_to_json{tick = P.tick, done = game.tick, surfaces = surfaces, totals = totals})
    storage.poll = nil
    return
  end
  local out = P.out[si] or {}
  P.out[si] = out
  local last = math.min(S.n, P.ci + POLL_PER_TICK - 1)
  local total = P.parts[si] or 0
  for i = P.ci, last do
    local cx, cy = cpos(S.list[i])
    local v = s.get_pollution({cx * 32 + 16, cy * 32 + 16})
    if v >= 0.5 then
      out[#out + 1] = cx; out[#out + 1] = cy; out[#out + 1] = math.floor(v + 0.5)
      total = total + v
    end
  end
  P.parts[si] = total
  P.ci = last + 1
  if P.ci > S.n then P.qi = P.qi + 1; P.ci = 1 end
end

-- ------------------------------------------------------------------ power (main electric network)

local PROBE_TYPES = {"assembling-machine", "furnace", "lab", "mining-drill"}

local function power_pick()
  local s = game.surfaces["nauvis"] or game.get_surface(1)
  local force = player_force()
  local by_net = {}
  -- look around spawn first (a whole-surface search costs ~5 ms on a big map); fall back to anywhere
  local sp = force.get_spawn_position(s)
  local poles = s.find_entities_filtered{type = "electric-pole", force = force, limit = 60,
                                         area = {{sp.x - 256, sp.y - 256}, {sp.x + 256, sp.y + 256}}}
  if #poles == 0 then poles = s.find_entities_filtered{type = "electric-pole", force = force, limit = 20} end
  for _, p in pairs(poles) do
    local id = p.electric_network_id
    if id and not by_net[id] then by_net[id] = p end
  end
  local best, bestv = nil, -1
  for _, p in pairs(by_net) do
    local ok, st = pcall(function() return p.electric_network_statistics end)
    if ok and st then
      local v = 0
      for _, c in pairs(st.input_counts) do v = v + c end
      if v > bestv then best, bestv = p, v end
    end
  end
  local probes = {}
  if best then
    local id = best.electric_network_id
    local bp = best.position
    for _, e in pairs(s.find_entities_filtered{type = PROBE_TYPES, force = force, limit = 200,
                                                area = {{bp.x - 160, bp.y - 160}, {bp.x + 160, bp.y + 160}}}) do
      if e.electric_network_id == id then
        probes[#probes + 1] = e
        if #probes >= 24 then break end
      end
    end
  end
  storage.power = {pole = best, probes = probes, picked = game.tick}
end

-- Most a generator can make, in watts (solar panels: in full daylight). From prototypes only, so cached.
local gen_max_cache = {}
local function gen_max(name)
  local v = gen_max_cache[name]
  if v then return v end
  local p = prototypes.entity[name]
  v = 0
  if p then
    local ok, x = pcall(function() return p.get_max_energy_production() end)
    if not (ok and x) then ok, x = pcall(function() return p.max_energy_production end) end
    if ok and type(x) == "number" then v = x * 60 end  -- per tick -> per second
  end
  gen_max_cache[name] = v
  return v
end

-- What the generators on the main network could make together: {steady = W, solar = W}.
-- Counts every built generator of the kinds that feed this network (the game has no per-network count).
local function power_capacity(st)
  local force = player_force()
  local steady, solar = 0, 0
  for name in pairs(st.output_counts) do
    local p = prototypes.entity[name]
    if p and p.type ~= "accumulator" then
      local per = gen_max(name)
      local ok, n = pcall(function() return force.get_entity_count(name) end)
      if per > 0 and ok and n and n > 0 then
        if p.type == "solar-panel" then solar = solar + per * n else steady = steady + per * n end
      end
    end
  end
  return steady, solar
end

local function power_info()
  local P = storage.power
  if not P or not (P.pole and P.pole.valid) or game.tick - (P.picked or 0) >= POWER_REPICK then
    power_pick()
    P = storage.power
  end
  if not (P.pole and P.pole.valid) then return nil end
  local st = P.pole.electric_network_statistics
  local low, working = 0, 0
  local S = defines.entity_status
  for _, e in pairs(P.probes) do
    if e.valid then
      local status = e.status
      if status == S.low_power or status == S.no_power then low = low + 1
      elseif status == S.working then working = working + 1 end
    end
  end
  local okc, steady, solar = pcall(power_capacity, st)
  return {id = P.pole.electric_network_id, i = st.input_counts, o = st.output_counts, low = low, working = working,
          probes = #P.probes, cap = okc and steady or nil, solar = okc and solar > 0 and solar or nil}
end

-- Which pollutant a surface has: "pollution", "spores" (Space Age Gleba), a mod's own, or nil (none, e.g. platforms).
local function pollutant_of(s)
  local ok, t = pcall(function() return s.pollutant_type end)
  return ok and t and t.name or nil
end

-- Space Age platform: where it is. {loc = "nauvis"} when parked at a planet, or
-- {from = "nauvis", to = "vulcanus", d = 0.4} on the way. nil for anything that isn't a platform.
local function platform_where(s)
  local ok, res = pcall(function()
    local p = s.platform
    if not (p and p.valid) then return nil end
    if p.space_location then return {loc = p.space_location.name} end
    local c = p.space_connection
    if c then return {from = c.from.name, to = c.to.name, d = p.distance and math.floor(p.distance * 100) / 100 or nil} end
    return nil
  end)
  return ok and res or nil
end

-- Evolution on every planet where the live map has seen enemy nests (Space Age: evolution is per planet).
local function evolution_by_surface()
  local out, enemy = {}, game.forces["enemy"]
  local nests = (storage.live and storage.live.nests) or {}
  for si in pairs(nests) do
    local s = game.get_surface(si)
    if enemy and s then
      local ok, v = pcall(function() return enemy.get_evolution_factor(s) end)
      if ok then out[tostring(si)] = v end
    end
  end
  return next(out) and out or nil
end

-- Type of each thing in the kill counter, so the website can count enemies by type, not by name.
-- Derived from prototypes only (identical on every peer), so caching it is safe.
local ktype_cache = {}
local function kill_types(kills)
  local out = {}
  for name in pairs(kills.i) do
    local t = ktype_cache[name]
    if t == nil then
      local p = prototypes.entity[name]
      t = p and p.type or false
      ktype_cache[name] = t
    end
    if t and ENEMY_TYPES[t] then out[name] = t end
  end
  return out
end

-- How many of each kind of building the players have: {type = n, _all = n}. force.get_entity_count is
-- instant (~1 µs), so this asks once per buildable kind every minute (~1 ms) instead of counting during map
-- reads. The list of kinds is made once per session (~7 ms with SE + K2, when the first stats are written).
local count_names  -- {{name, type}, ...} from prototypes (the same on every peer)
local function building_counts()
  if not count_names then
    count_names = {}
    for name, p in pairs(prototypes.entity) do
      local t = p.type
      if (not SKIP_TYPES[t] or MOVING_TYPES[t]) and t ~= "resource" and t ~= "tree" and t ~= "unit-spawner"
          and t ~= "turret" and p.items_to_place_this then  -- only things players can build (~900 of ~5600 with SE + K2)
        count_names[#count_names + 1] = {name, t}
      end
    end
  end
  local force, total, all = player_force(), {}, 0
  for _, nt in pairs(count_names) do
    local ok, n = pcall(force.get_entity_count, nt[1])
    if ok and n and n > 0 then
      total[nt[2]] = (total[nt[2]] or 0) + n
      all = all + n
    end
  end
  total._all = all
  return {tick = game.tick, total = total}
end

local function write_stats(leaving)
  local force = player_force()
  local kills = flow_totals(function(f, s) return f.get_kill_count_statistics(s) end)
  local players = {}
  for _, pl in pairs(game.players) do
    local s = pos_of(pl)
    players[#players + 1] = {n = pl.name, on = pl.connected and pl.index ~= leaving, ot = pl.online_time, afk = pl.afk_time,
                             last = pl.last_online, s = s and shared("share-positions") and s.index or nil,
                             deaths = storage.deaths[pl.name] or 0, admin = pl.admin, st = (storage.pstats or {})[pl.name]}
  end
  local cur = force.current_research
  local queue = {}
  for i, t in pairs(force.research_queue or {}) do
    queue[i] = type(t) == "string" and t or t.name
  end
  if not storage.techs then storage.techs = tech_counts() end
  local researched, total = storage.techs.r, storage.techs.t
  local surfaces = {}
  for _, s in pairs(game.surfaces) do
    if surface_eligible(s) then
      local info = surface_info(s)
      info.at = platform_where(s)
      surfaces[#surfaces + 1] = info
    end
  end
  local known = se_call("get_known_zones", {force_name = force.name})
  local nkn = 0
  if known then for _ in pairs(known) do nkn = nkn + 1 end end
  local nauvis = game.surfaces["nauvis"] or game.get_surface(1)
  local ok_evo, evo = pcall(function() return game.forces["enemy"].get_evolution_factor(nauvis) end)

  write("stats.json", helpers.table_to_json{
    tick = game.tick,
    players = players,
    research = {current = cur and cur.name or nil, level = cur and cur.level or nil,
                progress = force.research_progress, queue = queue, researched = researched, total = total,
                units = cur and cur.research_unit_count or nil,
                ing = cur and (function()
                  local t = {}
                  for _, x in pairs(cur.research_unit_ingredients) do t[#t + 1] = {n = x.name, a = x.amount} end
                  return t
                end)() or nil},
    -- Totals of the main pollutant (Nauvis' "pollution"). Other kinds (Space Age: spores on Gleba)
    -- are kept apart in `other`, because adding spores to pollution would be meaningless.
    pollution = (function()
      local main = pollutant_of(nauvis) or "pollution"
      local i, o, other = {}, {}, {}
      for _, s in pairs(game.surfaces) do
        local kind = pollutant_of(s)
        local ok, st = pcall(function() return game.get_pollution_statistics(s) end)
        if kind and ok and st then
          local ti, to = i, o
          if kind ~= main then
            other[kind] = other[kind] or {i = {}, o = {}, air = 0}
            ti, to = other[kind].i, other[kind].o
            local ok2, air = pcall(function() return s.get_total_pollution() end)
            if ok2 and air then other[kind].air = other[kind].air + air end
          end
          for k, v in pairs(st.input_counts) do ti[k] = (ti[k] or 0) + v end
          for k, v in pairs(st.output_counts) do to[k] = (to[k] or 0) + v end
        end
      end
      local ok, air = pcall(function() return nauvis.get_total_pollution() end)
      return {i = i, o = o, air = ok and air or nil, other = next(other) and other or nil}
    end)(),
    power = (function() local ok, v = pcall(power_info); return ok and v or nil end)(),
    items = flow_totals(function(f, s) return f.get_item_production_statistics(s) end),
    fluids = flow_totals(function(f, s) return f.get_fluid_production_statistics(s) end),
    kills = kills,
    ktypes = kill_types(kills),
    share = {chat = shared("share-chat"), positions = shared("share-positions"), attacks = shared("record-attacks")},
    evolution = ok_evo and evo or nil,
    evo_surfaces = evolution_by_surface(),
    evo = evolution_info(nauvis),
    tpd = nauvis and nauvis.ticks_per_day or nil,
    rockets = force.rockets_launched,
    se = known and {zones_known = nkn,
                    cargo_rockets = se_call("get_rockets_launched", {force = force}),
                    satellites = se_call("get_satellites_launched", {force = force})} or nil,
    counts = building_counts(),
    stations = (function()
      local out = {}
      local ok, list = pcall(function() return game.train_manager.get_train_stops{force = force} end)
      if ok and list then
        for _, st in pairs(list) do
          if st.valid then
            local p = st.position
            out[#out + 1] = {s = st.surface.index, x = math.floor(p.x), y = math.floor(p.y), n = st.backer_name}
          end
        end
      end
      return out
    end)(),
    surfaces = surfaces,
    livemap = storage.live and {chunks = storage.live.total, waiting = storage.live.qt - storage.live.qh + 1,
                                refresh_left = storage.live.fast > 0 and storage.live.fast or nil} or nil,
  })
end

-- ------------------------------------------------------------------ events

local function pname(i)
  local p = i and game.get_player(i)
  return p and p.name or nil
end

local function reverse(enum, v)
  for k, x in pairs(enum) do if x == v then return k end end
end

local function register_se_events()
  local id = se_call("get_on_cargo_rocket_launched_event")
  if id then
    on_event(id, function(e)
      event_out{type = "cargo_rocket", from = e.zone_name, to = e.destination_zone_name}
    end)
  end
end

on_event(defines.events.on_player_joined_game, function(e)
  event_out{type = "join", player = pname(e.player_index)}
  write_live()
end)

on_event(defines.events.on_player_left_game, function(e)
  event_out{type = "leave", player = pname(e.player_index), reason = reverse(defines.disconnect_reason, e.reason)}
  -- the server pauses once it's empty: write a final state (without the leaver) so the website isn't stale
  write_live(e.player_index)
  write_stats(e.player_index)
end)

on_event(defines.events.on_console_chat, function(e)
  if not shared("share-chat") then return end
  event_out{type = "chat", player = pname(e.player_index) or "<server>", message = e.message}
end)

on_event(defines.events.on_player_died, function(e)
  local name = pname(e.player_index)
  if name then storage.deaths[name] = (storage.deaths[name] or 0) + 1 end
  local cause, killer
  if e.cause and e.cause.valid then
    cause = e.cause.name
    if e.cause.type == "character" and e.cause.player then killer = e.cause.player.name end
  end
  local p = game.get_player(e.player_index)
  local s = p and p.surface
  event_out{type = "death", player = name, cause = cause, killer = killer, surface = s and s.index or nil}
end)

-- Attacks: our buildings destroyed by an enemy force (biters, or any enemy force a mod adds).
-- One event per 128x128 area per 2 minutes.
on_event(defines.events.on_entity_died, function(e)
  local ent = e.entity
  if not (ent and ent.valid) then return end
  mark_entity(ent)
  local c = e.cause
  local pf = player_force()
  if c and c.valid and ent.force ~= pf and pf.is_enemy(ent.force) then
    if c.type == "character" then
      if c.player then pstat(c.player.index, "k") end
    elseif c.type == "car" or c.type == "spider-vehicle" then
      local d = c.get_driver()
      if d and d.valid then
        local p = d.object_name == "LuaPlayer" and d or d.player
        if p then pstat(p.index, "k") end
      end
    end
  end
  if not (e.force and ent.force == pf and pf.is_enemy(e.force)) then return end
  if not shared("record-attacks") then return end
  local s, pos = ent.surface.index, ent.position
  local key = s .. ":" .. math.floor(pos.x / 128) .. ":" .. math.floor(pos.y / 128)
  storage.attacks = storage.attacks or {}
  local a = storage.attacks[key]
  if a and game.tick - a.last < 7200 then
    a.lost, a.last = a.lost + 1, game.tick
    return
  end
  storage.attacks[key] = {s = s, x = math.floor(pos.x), y = math.floor(pos.y), lost = 1, first = game.tick, last = game.tick, what = ent.name}
  event_out{type = "attack", surface = s, x = math.floor(pos.x), y = math.floor(pos.y), what = ent.name,
            cause = e.cause and e.cause.valid and e.cause.name or nil}
end)

on_event(defines.events.on_built_entity, function(e)
  pstat(e.player_index, "b")
  track_vehicle(e.entity)
  mark_entity(e.entity)
end, {{filter = "ghost", invert = true}})
for _, ev in pairs({"on_robot_built_entity", "script_raised_built", "script_raised_revive"}) do
  on_event(defines.events[ev], function(e)
    track_vehicle(e.entity)
    mark_entity(e.entity)
  end, {{filter = "ghost", invert = true}})
end
on_event(defines.events.on_player_mined_entity, function(e)
  pstat(e.player_index, "m")
  mark_entity(e.entity)
end)
-- more things that change what a chunk looks like on the map
for _, ev in pairs({"on_robot_mined_entity", "script_raised_destroy", "on_resource_depleted", "on_biter_base_built",
                    "on_player_rotated_entity", "on_player_flipped_entity"}) do
  if defines.events[ev] then on_event(defines.events[ev], function(e) mark_entity(e.entity) end) end
end
for _, ev in pairs({"on_player_built_tile", "on_robot_built_tile", "on_player_mined_tile", "on_robot_mined_tile",
                    "script_raised_set_tiles", "on_space_platform_built_tile", "on_space_platform_mined_tile"}) do
  if defines.events[ev] then on_event(defines.events[ev], mark_tiles) end
end

-- newly explored chunks join the live map (radars re-chart the same chunks all the time: ignored)
on_event(defines.events.on_chunk_charted, function(e)
  local L = storage.live
  if not L or e.force ~= player_force() then return end
  local si = e.surface_index
  if L.skip[si] then return end
  local is_new_surface = not L.surf[si]
  if is_new_surface then
    local s = game.get_surface(si)
    if not (s and surface_eligible(s)) then L.skip[si] = true return end
  end
  local p = e.position
  if track_chunk(L, si, p.x, p.y) then
    if is_new_surface then surface_line(L, si) end
    mark(si, p.x, p.y, true)
  end
end)
on_event(defines.events.on_chunk_deleted, function(e)
  local L = storage.live
  if not L then return end
  for _, p in pairs(e.positions) do untrack_chunk(L, e.surface_index, p.x, p.y) end
end)
if defines.events.on_surface_cleared then
  on_event(defines.events.on_surface_cleared, function(e)
    if storage.live then drop_surface(storage.live, e.surface_index) end
  end)
end
on_event(defines.events.on_player_crafted_item, function(e) pstat(e.player_index, "c", e.item_stack.count) end)

if defines.events.on_research_reversed then
  on_event(defines.events.on_research_reversed, function() storage.techs = nil end)
end

on_event(defines.events.on_research_finished, function(e)
  storage.techs = nil  -- recount on the next stats write
  if e.by_script then return end
  local r = e.research
  event_out{type = "research", name = r.name, level = r.level}
end)

on_event(defines.events.on_rocket_launched, function(e)
  event_out{type = "rocket", surface = e.rocket_silo and e.rocket_silo.valid and e.rocket_silo.surface.index or nil}
end)

for _, ev in pairs({"on_surface_deleted", "on_surface_renamed", "on_surface_imported"}) do
  if defines.events[ev] then
    on_event(defines.events[ev], function(e)
      surface_cache[e.surface_index] = nil
      local L = storage.live
      if L then
        if ev == "on_surface_deleted" then
          drop_surface(L, e.surface_index)
          L.skip[e.surface_index] = nil  -- the number may be reused by a new surface
        elseif L.surf[e.surface_index] then
          surface_line(L, e.surface_index)  -- new name
        end
      end
    end)
  end
end

on_event(defines.events.on_surface_created, function(e)
  surface_cache[e.surface_index] = nil
  local s = game.get_surface(e.surface_index)
  if s and surface_eligible(s) then event_out{type = "surface", surface = s.index, name = s.name} end
end)

-- ------------------------------------------------------------------ scheduling

local function init_storage()
  storage.deaths = storage.deaths or {}
  storage.attacks = storage.attacks or {}
  -- 0.3.x snapshots are replaced by the live map
  storage.snap, storage.next_snapshot, storage.chunk_lists, storage.counts = nil, nil, nil, nil
  if storage.poll and not storage.poll.queue then storage.poll = nil end
  if storage.live then
    storage.live.pal_sent = 0  -- after an update, send the palette again
  else
    init_live()
  end
end

script.on_init(function()
  init_storage()
  find_all_vehicles()
  register_se_events()
end)
script.on_load(register_se_events)
script.on_configuration_changed(function()
  storage.poll = nil  -- its chunk lists came from the old snapshot code
  init_storage()
  find_all_vehicles()
  storage.techs = nil
  write_live()
  write_stats()
end)

on_event(defines.events.on_tick, function(e)
  if storage.live then live_step(e.tick) end
  if storage.poll then poll_step() end
end)

on_nth_tick(POLL_EVERY, function()
  if not storage.poll then poll_start() end
end)

on_nth_tick(LIVE_EVERY, write_live)
on_nth_tick(STATS_EVERY, write_stats)

local function admin_cmd(cmd)  -- the player who typed it (nil from RCON / server console), and whether allowed
  local p = cmd.player_index and game.get_player(cmd.player_index)
  if p and not p.admin then p.print("Admins only.") return p, false end
  return p, true
end
local function reply(p, msg) if p then p.print(msg) else rcon.print(msg) end end

local function refresh_cmd(cmd)
  local p, ok = admin_cmd(cmd)
  if not ok then return end
  if not storage.live then init_live() else start_refresh(storage.live) end
  reply(p, string.format("Re-reading all %d explored chunks for the website map (%d per tick).", storage.live.total,
                         settings.global["factory-map-chunks-per-tick"].value))
end
add_command("map-refresh", "Re-read the whole website map now (admin only). It also updates by itself.", refresh_cmd)
add_command("map-snapshot", "Same as /map-refresh (older name).", refresh_cmd)

add_command("map-export", "Write the website's live/stats files now (admin only).", function(cmd)
  local p = cmd.player_index and game.get_player(cmd.player_index)
  if p and not p.admin then p.print("Admins only.") return end
  write_live()
  write_stats()
  if not storage.poll then poll_start() end
  if p then p.print("Exported.") else rcon.print("Exported.") end
end)

-- Timing of each part of the mod (admin/RCON only). Doesn't change anything except writing the usual files.
add_command("map-bench", "Time each part of the website export (admin only).", function(cmd)
  local pl = cmd.player_index and game.get_player(cmd.player_index)
  if pl and not pl.admin then return end
  local out = function(m) if pl then pl.print(m) else rcon.print(m) end end
  local function time(label, f)
    local pr = helpers.create_profiler()
    f()
    pr.stop()
    out({"", label, ": ", pr})
  end
  time("live (positions/tags/trains)", write_live)
  time("stats", write_stats)
  time("power network re-pick", power_pick)
  time("pollution map, whole pass in one go (normally spread over ~1 s)", function()
    poll_start()
    while storage.poll do poll_step() end
  end)
  -- chunk read cost: up to 100 explored chunks near the centre of nauvis
  local L, s = storage.live, game.surfaces["nauvis"]
  local S = L and s and L.surf[s.index]
  local list = {}
  for _, k in pairs(S and S.list or {}) do
    local cx, cy = cpos(k)
    if math.abs(cx) < 12 and math.abs(cy) < 12 then list[#list + 1] = {cx, cy} end
    if #list >= 100 then break end
  end
  if #list == 0 then out("No explored chunks near the map centre to time the chunk reads on.") return end
  local fake = {pal = {}, palette = {}, npal = 0, nests = {}}  -- own palette: the real one stays untouched
  local force = player_force()
  local en, fn = enemy_forces(force), game.forces.neutral.index
  local pf, pe = helpers.create_profiler(), helpers.create_profiler(true)
  for _, c in pairs(list) do scan_chunk(fake, s, c[1], c[2], force.index, en, fn, true) end
  pf.stop()
  for _, c in pairs(list) do pe.restart(); scan_chunk(fake, s, c[1], c[2], force.index, en, fn, false); pe.stop() end
  pf.divide(#list) pe.divide(#list)
  out({"", "chunk read with ground (sweep / landfill), avg over ", #list, ": ", pf})
  out({"", "chunk read, buildings only (after building / mining): ", pe})
  out(string.format("The sweep does one ground read every %d ticks; up to %d building reads per tick while things change.",
                    L.gap or 4, DIRTY_PER_TICK))
end)

add_command("map-status", "Show how the website map is doing.", function(cmd)
  local p = cmd.player_index and game.get_player(cmd.player_index)
  reply(p, live_status())
end)
