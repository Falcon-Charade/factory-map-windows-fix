-- Server-wide settings (Settings > Mod settings > Map, admins only). All can be changed while the game runs.
data:extend({
  {type = "int-setting", name = "factory-map-sweep-minutes", setting_type = "runtime-global",
   default_value = 30, minimum_value = 5, maximum_value = 1440, order = "a"},
  {type = "int-setting", name = "factory-map-chunks-per-tick", setting_type = "runtime-global",
   default_value = 1, minimum_value = 1, maximum_value = 16, order = "b"},
  -- what the website may show: off = the mod doesn't write it at all
  {type = "bool-setting", name = "factory-map-share-chat", setting_type = "runtime-global", default_value = true, order = "c"},
  {type = "bool-setting", name = "factory-map-share-positions", setting_type = "runtime-global", default_value = true, order = "d"},
  {type = "bool-setting", name = "factory-map-record-attacks", setting_type = "runtime-global", default_value = true, order = "e"},
})
