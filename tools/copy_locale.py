#!/usr/bin/env python3
"""Copies the base game's English names (items, buildings, research...) into web/cache/locale,
so the website shows "Assembling machine 2" instead of "assembling-machine-2".
Mod names are read straight from the mod zips; only the base game needs this, once per game update.

Usage:  python3 tools/copy_locale.py <Factorio install folder>      (Windows: py tools\\copy_locale.py ...)

The install folder (not the user data folder with mods/ and saves/) is usually:
  Windows, Steam:       "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Factorio"
  Windows, standalone:  "C:\\Program Files\\Factorio"
  macOS, Steam:         ~/Library/Application Support/Steam/steamapps/common/Factorio/factorio.app
  macOS, standalone:    /Applications/factorio.app
  Linux, Steam:         ~/.steam/steam/steamapps/common/Factorio
  Linux, standalone / headless:  the extracted folder, e.g. ~/factorio or /opt/factorio
  Docker (factoriotools/factorio):  docker cp <container>:/opt/factorio/data ./factorio-data
                                    then pass ./factorio-data
Its data/ folder (or factorio.app/Contents/data on macOS) works too.
"""
import shutil
import sys
from pathlib import Path


def find_data(src):
    """The folder that holds base/locale/en: the path itself, its data/, or a macOS bundle's Contents/data."""
    for d in (src, src / "data", src / "Contents" / "data", src / "factorio.app" / "Contents" / "data"):
        if (d / "base" / "locale" / "en").is_dir():
            return d
    return None


def main():
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    src = Path(sys.argv[1]).expanduser()
    data = find_data(src)
    if not data:
        sys.exit(f"no base/locale/en found under {src} (give the Factorio install folder, see --help)")
    dst = Path(__file__).resolve().parent.parent / "web" / "cache" / "locale"
    for en in sorted(data.glob("*/locale/en")):
        name = en.parent.parent.name
        (dst / name).mkdir(parents=True, exist_ok=True)
        for f in en.glob("*.cfg"):
            shutil.copy2(f, dst / name / f.name)
        print("copied", name)


if __name__ == "__main__":
    main()
