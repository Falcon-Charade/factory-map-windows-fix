"""Finds Factorio's folders on Windows, macOS and Linux, so most people never have to type a path.

user_data()     the folder with mods/ and script-output/ (where the mod writes)
install_data()  the game's data/ folder with base/locale/en (English item names)
copy_locale()   copies those names into web/cache/locale once
"""
import os
import re
import shutil
import sys
from pathlib import Path

HOME = Path.home()


def _steam_roots():
    if sys.platform == "win32":
        return [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"]
    if sys.platform == "darwin":
        return [HOME / "Library/Application Support/Steam"]
    return [HOME / ".steam/steam", HOME / ".local/share/Steam",
            HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]


def _steam_games():
    """Factorio in every Steam library (Steam lists extra libraries, e.g. on other drives, in libraryfolders.vdf)."""
    out = []
    for root in _steam_roots():
        libs = [root]
        try:
            vdf = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
            libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
        except OSError:
            pass
        out += [lib / "steamapps" / "common" / "Factorio" for lib in libs]
    return out


def _installs():
    if sys.platform == "win32":
        pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        other = [pf / "Factorio"]
    elif sys.platform == "darwin":
        other = [Path("/Applications/factorio.app"), HOME / "Applications/factorio.app"]
    else:
        other = [HOME / "factorio", Path("/opt/factorio")]
    return _steam_games() + other


def user_data():
    """The first existing folder that has a mods/ folder: the normal user data folder, or a standalone/headless install."""
    if sys.platform == "win32":
        normal = [Path(os.environ.get("APPDATA", HOME / "AppData/Roaming")) / "Factorio"]
    elif sys.platform == "darwin":
        normal = [HOME / "Library/Application Support/factorio"]
    else:
        normal = [HOME / ".factorio"]
    for d in normal + [p for p in _installs() if not p.name.endswith(".app")]:
        if (d / "mods").is_dir():
            return d
    return None


def _data_dir(p):
    for d in (p, p / "data", p / "Contents" / "data", p / "factorio.app" / "Contents" / "data"):
        if (d / "base" / "locale" / "en").is_dir():
            return d
    return None


def install_data(hint=None):
    """The game's data folder (with base/locale/en), from a hint path or the usual install places."""
    for p in ([Path(hint).expanduser()] if hint else []) + _installs():
        d = _data_dir(p)
        if d:
            return d
    return None


def copy_locale(data, dst):
    """Copy every <mod>/locale/en/*.cfg from the game's data folder to dst/<mod>/. Returns the names copied."""
    done = []
    for en in sorted(data.glob("*/locale/en")):
        name = en.parent.parent.name
        (dst / name).mkdir(parents=True, exist_ok=True)
        for f in en.glob("*.cfg"):
            shutil.copy2(f, dst / name / f.name)
        done.append(name)
    return done
