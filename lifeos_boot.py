"""Starts Life OS: picks which installed version of the code to run, and falls back if a new one fails.

This file is the only part of Life OS that never changes with an update, so it is kept small and careful.
The app download contains it, a copy of the code (lifeos/ and web/) and Python with every package needed.
Updates add newer copies of the code into <home>/versions/<version>/; this picks the newest one that suits the
packages in this download.

A freshly installed version starts "on trial". The app confirms it once it has been running happily for a little
while. If it fails to start, or Life OS stops before confirming, the next start goes back to the version before.

    lifeos                    start (opens the browser on a desktop computer)
    lifeos --headless         start without opening a browser or showing an icon (servers, Raspberry Pi)
    lifeos --demo             look around with sample data (nothing is saved)
    lifeos setup              questions in the terminal instead of the setup page
    lifeos install-service    Linux: start Life OS whenever the machine starts (asks for your password)
    lifeos --version
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

RUNTIME = 1          # bumped when the Python packages in the app download change; updates name the one they need
RESTART = 75         # exit code the app uses to ask for a restart (a fresh start of this file)
_ARGV = list(sys.argv)


def _platform_home() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Life OS"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Life OS"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "lifeos"


def bundled_code() -> Path:
    """The copy of the code that came with this download (or this source folder)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "app"
    return Path(__file__).resolve().parent


def version_of(code: Path) -> str:
    try:
        m = re.search(r'__version__\s*=\s*"([^"]+)"', (code / "lifeos" / "__init__.py").read_text())
        return m.group(1) if m else "0"
    except OSError:
        return "0"


def vtuple(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v)[:3])


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _usable(folder: Path) -> bool:
    man = _read(folder / "manifest.json")
    if not (folder / "lifeos" / "__init__.py").exists() or not man:
        return False
    # An update needing packages this download doesn't have can't run here (the new app download is needed)
    return not (getattr(sys, "frozen", False) and int(man.get("runtime", 1)) > RUNTIME)


def choose_code(home: Path, log=print) -> tuple[Path, str, bool]:
    """Returns (code folder, version, on_trial)."""
    bundled = bundled_code()
    bundled_v = version_of(bundled)
    active_file = home / "versions" / "active.json"
    active = _read(active_file)
    v = active.get("version")
    if not v:
        return bundled, bundled_v, False
    folder = home / "versions" / v
    if vtuple(v) <= vtuple(bundled_v) or not _usable(folder):
        # This download is as new (someone downloaded the app again), or the update can't run here
        if vtuple(v) > vtuple(bundled_v):
            log(f"Life OS {v} needs the new app download; running {bundled_v}.")
        _write(active_file, {**active, "version": None, "state": "ok", "previous": None})
        return bundled, bundled_v, False
    if active.get("state") == "trial":
        if active.get("tries", 0) >= 1:  # it started before and never said it was fine: go back
            log(f"Life OS {v} didn't start properly last time, so going back to the version before.")
            return _fall_back(home, active, v, bundled, bundled_v)
        _write(active_file, {**active, "tries": active.get("tries", 0) + 1, "tried_at": time.time()})
        return folder, v, True
    return folder, v, False


def _fall_back(home: Path, active: dict, bad: str, bundled: Path, bundled_v: str) -> tuple[Path, str, bool]:
    prev = active.get("previous")
    failed = sorted(set(active.get("failed", [])) | {bad})
    folder = home / "versions" / prev if prev else None
    if folder and _usable(folder) and vtuple(prev) > vtuple(bundled_v):
        _write(home / "versions" / "active.json", {"version": prev, "state": "ok", "previous": None, "failed": failed})
        return folder, prev, False
    _write(home / "versions" / "active.json", {"version": None, "state": "ok", "previous": None, "failed": failed})
    return bundled, bundled_v, False


def mark_failed(home: Path, log=print) -> None:
    """The version on trial crashed while starting: go back now, so the restart runs the previous one."""
    active_file = home / "versions" / "active.json"
    active = _read(active_file)
    if active.get("state") == "trial" and active.get("version"):
        log(f"Life OS {active['version']} failed to start; going back to the version before.")
        _fall_back(home, active, active["version"], bundled_code(), version_of(bundled_code()))


def restart_command(extra: list[str] | None = None, drop: tuple[str, ...] = ()) -> list[str]:
    args = [a for a in _ARGV[1:] if a not in drop] + [a for a in (extra or []) if a not in _ARGV[1:]]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, str(Path(__file__).resolve()), *args]


def restart(extra: list[str] | None = None, drop: tuple[str, ...] = ()) -> None:
    cmd = restart_command(extra, drop)
    sys.stdout.flush()
    sys.stderr.flush()
    if os.name == "nt":
        import subprocess
        subprocess.Popen(cmd, close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x8))
        os._exit(0)
    os.execv(cmd[0], cmd)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(version_of(bundled_code()))
        return 0
    if "--home" in argv:
        i = argv.index("--home")
        os.environ["LIFEOS_HOME"] = str(Path(argv[i + 1]).expanduser().resolve())
    elif not os.environ.get("LIFEOS_HOME"):
        here = bundled_code()
        # A source folder with its own settings keeps using them; the app download uses the per-user folder.
        if getattr(sys, "frozen", False) or not (here / ".env").exists():
            os.environ["LIFEOS_HOME"] = str(_platform_home())
        else:
            os.environ["LIFEOS_HOME"] = str(here)
    home = Path(os.environ["LIFEOS_HOME"])
    home.mkdir(parents=True, exist_ok=True)

    code, version, trial = choose_code(home)
    sys.path.insert(0, str(code))
    os.environ["LIFEOS_RUNTIME"] = str(RUNTIME)
    os.environ["LIFEOS_TRIAL"] = "1" if trial else ""
    try:
        from lifeos import desktop
        code_rc = desktop.run(argv, boot=sys.modules[__name__])
    except SystemExit:
        raise
    except BaseException:
        if trial:
            import traceback
            traceback.print_exc()
            mark_failed(home)
            restart()
        raise
    if code_rc == RESTART:
        restart(*desktop.restart_args())
    return code_rc or 0


if __name__ == "__main__":
    sys.exit(main())
