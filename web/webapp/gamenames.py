"""English names for items, entities, technologies... read from the game's and the mods' locale files."""

import json
import re
import zipfile

from .config import FACTORIO, HERE


class Names:
    """Looks up English names in the base game's and every enabled mod's locale files."""
    SECTIONS = ("item-name", "entity-name", "fluid-name", "technology-name", "tile-name", "equipment-name",
                "space-location-name", "virtual-signal-name", "airborne-pollutant-name")

    def __init__(self):
        self.d = {s: {} for s in self.SECTIONS}
        self.load()

    def _parse(self, text):
        section = None
        for line in text.splitlines():
            line = line.strip()
            if not line or line[0] in ";#":
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if section in self.d and "=" in line:
                k, _, v = line.partition("=")
                self.d[section][k.strip()] = v.strip()

    def load(self):
        for f in sorted((HERE / "cache" / "locale").glob("*/*.cfg")):
            self._parse(f.read_text(encoding="utf-8", errors="replace"))
        mods = FACTORIO / "mods"
        try:
            enabled = {m["name"] for m in json.loads((mods / "mod-list.json").read_text(encoding="utf-8"))["mods"] if m["enabled"]}
        except (OSError, ValueError, KeyError):
            enabled = None
        for z in sorted(mods.glob("*.zip")):
            if enabled is not None and z.name.rsplit("_", 1)[0] not in enabled:
                continue
            try:
                with zipfile.ZipFile(z) as zf:
                    for n in zf.namelist():
                        if re.search(r"/locale/en/[^/]+\.cfg$", n):
                            self._parse(zf.read(n).decode("utf-8", "replace"))
            except (OSError, zipfile.BadZipFile):
                pass

    def _clean(self, v, depth=0):
        def ref(m):
            sec = {"ITEM": "item-name", "ENTITY": "entity-name", "FLUID": "fluid-name", "TILE": "tile-name"}.get(m.group(1))
            return self.get(m.group(2), (sec,), depth + 1) if sec and depth < 4 else m.group(2)
        v = re.sub(r"__(ITEM|ENTITY|FLUID|TILE)__([^_]+(?:_[^_]+)*?)__", ref, v)
        v = re.sub(r"\[(?:item|entity|fluid|technology|virtual-signal|img|font|color)=[^\]]*\]|\[/(?:font|color)\]", "", v)
        return v.replace("\\n", " ").strip()

    @staticmethod
    def pretty(name):
        n = re.sub(r"^(se|kr|aai)-", "", name).replace("-", " ")
        return n[:1].upper() + n[1:]

    def get(self, name, sections=("item-name", "entity-name", "fluid-name", "equipment-name", "tile-name"), depth=0):
        if not name:
            return name
        for s in sections:
            v = self.d.get(s, {}).get(name)
            if v:
                return self._clean(v, depth)
        return self.pretty(name)

    def rich(self, text):
        """Replace Factorio rich text like [item=iron-plate] with readable names; drop other tags."""
        if not text:
            return text
        def sub(m):
            kind, name = m.group(1), m.group(2).split(",")[0]
            if kind == "technology":
                return self.tech(name)
            if kind in ("item", "fluid", "entity", "virtual-signal", "recipe", "tile"):
                return self.get(name) if kind != "virtual-signal" else self.pretty(name.replace("signal-", ""))
            return ""
        text = re.sub(r"\[(item|fluid|entity|virtual-signal|recipe|tile|technology|img|gps|special-item|planet|space-location|quality)=([^\]]*)\]", sub, text)
        text = re.sub(r"\[/?(color|font)[^\]]*\]", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def tech(self, name, level=None):
        v = self.d["technology-name"].get(name)
        if v:
            return self._clean(v)
        m = re.match(r"^(.*)-(\d+)$", name)
        if m and m.group(1) in self.d["technology-name"]:
            return f"{self._clean(self.d['technology-name'][m.group(1)])} {level or m.group(2)}"
        return self.pretty(name)


NAMES = Names()
