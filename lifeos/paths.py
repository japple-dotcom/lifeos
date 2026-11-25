"""Where Life OS keeps things.

Two places, kept apart so an update can never touch your data:

- The *code*: the `lifeos` package and the `web` folder. Inside the app download, or a newer version installed by
  an update into `<home>/versions/<version>/`.
- The *home folder*: settings (.env, config/settings.yaml), Google connections (secrets/) and history (data/).
    Mac:      ~/Library/Application Support/Life OS
    Windows:  %APPDATA%\\Life OS
    Linux/Pi: ~/.local/share/lifeos  (or $XDG_DATA_HOME/lifeos)
  LIFEOS_HOME overrides it. Running from a source folder that already has a .env keeps using that folder, so
  existing installs carry on as they were.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent   # holds lifeos/ and web/ (and config/ defaults)


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def platform_home() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Life OS"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Life OS"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "lifeos"


def home() -> Path:
    if os.environ.get("LIFEOS_HOME"):
        return Path(os.environ["LIFEOS_HOME"]).expanduser()
    if not frozen():
        return CODE_ROOT  # a source folder: everything lives beside the code, as before
    return platform_home()


def web_dir() -> Path:
    return CODE_ROOT / "web"


def default_settings_file() -> Path:
    return CODE_ROOT / "config" / "settings.yaml"


def settings_file() -> Path:
    return home() / "config" / "settings.yaml"


def env_file() -> Path:
    return home() / ".env"


def versions_dir() -> Path:
    return home() / "versions"


def ensure_home() -> Path:
    """Creates the home folder on first run, with a copy of the default settings to edit."""
    h = home()
    for sub in ("config", "secrets", "data"):
        (h / sub).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(h / "secrets", 0o700)
    except OSError:
        pass
    if not settings_file().exists() and default_settings_file().exists():
        shutil.copyfile(default_settings_file(), settings_file())
    return h
