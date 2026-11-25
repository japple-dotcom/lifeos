"""Updates without reinstalling: a small download with the new code, checked file by file, installed beside the old.

An update file (lifeos-update-<version>.zip) holds manifest.json (version, the app runtime it needs, notes, and a
SHA-256 for every file) plus lifeos/, web/ and the default settings. It never contains settings, keys or history.

Two ways to get one:
- From the update feed (Settings > Update now, or overnight if allowed). The feed is a small JSON file published
  with each release: {"version", "notes", "runtime", "update": {"url", "sha256", "size"}, "apps": {platform: url}}.
- From a file: Settings > Install an update file (an update file sent to you, saved on the phone or computer).

Installing puts the code in <home>/versions/<version>/ and starts it "on trial" (see lifeos_boot.py). If it doesn't
start properly, Life OS goes back to the version before by itself.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import __version__, paths

log = logging.getLogger("lifeos.updater")
MAX_BYTES = 60 * 1024 * 1024
MAX_FILES = 3000
ALLOWED_TOP = {"lifeos", "web", "config", "manifest.json", "requirements.txt", ".env.example"}


class UpdateError(Exception):
    pass


def runtime() -> int:
    return int(os.environ.get("LIFEOS_RUNTIME") or 1)


def is_app_download() -> bool:
    return paths.frozen()


def vtuple(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", str(v))[:3])


def platform_key() -> str:
    m = platform.machine().lower()
    arch = "arm64" if m in ("arm64", "aarch64") else "x64"
    if sys.platform == "darwin":
        return f"mac-{arch}"
    if os.name == "nt":
        return f"windows-{arch}"
    return f"linux-{'arm64' if arch == 'arm64' else 'x86_64'}"


def _active_file() -> Path:
    return paths.versions_dir() / "active.json"


def _read_active() -> dict:
    try:
        return json.loads(_active_file().read_text())
    except (OSError, ValueError):
        return {}


def _write_active(data: dict) -> None:
    f = _active_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, f)


# ---------- reading an update file ----------
def read_package(data: bytes) -> tuple[dict, zipfile.ZipFile]:
    if len(data) > MAX_BYTES:
        raise UpdateError("That file is too big to be a Life OS update.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UpdateError("That isn't a Life OS update file (it should end in .zip).") from e
    try:
        manifest = json.loads(z.read("manifest.json"))
    except (KeyError, ValueError) as e:
        raise UpdateError("That isn't a Life OS update file: it has no manifest.") from e
    if manifest.get("name") != "lifeos-update" or not re.fullmatch(r"\d+\.\d+\.\d+", str(manifest.get("version", ""))):
        raise UpdateError("That isn't a Life OS update file.")
    files = manifest.get("files") or {}
    names = [i for i in z.infolist() if not i.is_dir()]
    if len(names) > MAX_FILES or sum(i.file_size for i in names) > MAX_BYTES:
        raise UpdateError("That update file is larger than expected.")
    for info in names:
        n = info.filename
        if n == "manifest.json":
            continue
        parts = Path(n).parts
        if n.startswith(("/", "\\")) or ".." in parts or ":" in n or parts[0] not in ALLOWED_TOP:
            raise UpdateError(f"Unexpected file in the update: {n}")
        if n not in files:
            raise UpdateError(f"The update has a file it doesn't list: {n}")
        if hashlib.sha256(z.read(info)).hexdigest() != files[n]:
            raise UpdateError(f"The update file is damaged ({n}). Download it again.")
    missing = [n for n in files if n not in {i.filename for i in names}]
    if missing or "lifeos/__init__.py" not in files:
        raise UpdateError("The update file is incomplete. Download it again.")
    return manifest, z


def check_package(manifest: dict) -> None:
    v = manifest["version"]
    if vtuple(v) <= vtuple(__version__):
        raise UpdateError(f"You already have Life OS {__version__}; that file is {v}.")
    if is_app_download() and int(manifest.get("runtime", 1)) > runtime():
        raise UpdateError(f"Life OS {v} needs the new app download (this one is too old to update itself to it).")


def install_bytes(data: bytes, source: str = "file") -> dict:
    manifest, z = read_package(data)
    check_package(manifest)
    v = manifest["version"]
    vdir = paths.versions_dir()
    vdir.mkdir(parents=True, exist_ok=True)
    partial = vdir / f"{v}.partial"
    shutil.rmtree(partial, ignore_errors=True)
    for info in z.infolist():
        if info.is_dir():
            continue
        target = partial / info.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(z.read(info))
    for name, digest in manifest["files"].items():  # check what actually landed on disk
        if hashlib.sha256((partial / name).read_bytes()).hexdigest() != digest:
            shutil.rmtree(partial, ignore_errors=True)
            raise UpdateError("The update couldn't be saved properly. Is the disk full?")
    if not is_app_download():
        _source_packages(partial)
    final = vdir / v
    shutil.rmtree(final, ignore_errors=True)
    os.replace(partial, final)
    active = _read_active()
    running_layer = active.get("version") if active.get("version") and active.get("state") == "ok" else None
    _write_active({"version": v, "previous": running_layer, "state": "trial", "tries": 0,
                   "installed_at": time.time(), "from": __version__, "source": source,
                   "failed": [f for f in active.get("failed", []) if f != v]})
    log.info("Installed Life OS %s (from %s); restarting into it", v, __version__)
    return {"ok": True, "version": v, "notes": manifest.get("notes", ""), "restart": True}


def _source_packages(folder: Path) -> None:
    """Running from a source folder: install any new Python packages the update needs (the app download has them)."""
    req = folder / "requirements.txt"
    current = paths.CODE_ROOT / "requirements.txt"
    if not req.exists() or (current.exists() and current.read_text() == req.read_text()):
        return
    if sys.prefix == sys.base_prefix:
        log.warning("Update needs new packages but this isn't a virtual environment; run: pip install -r %s", req)
        return
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--only-binary=cryptography", "-r", str(req)],
                       capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise UpdateError("Couldn't install what the update needs: " + (r.stderr or r.stdout)[-300:])


def confirm_running() -> None:
    """The new version has been running happily: keep it, and tidy away all but it and the one before."""
    active = _read_active()
    if active.get("state") == "trial" and active.get("version") == __version__:
        _write_active({**active, "state": "ok", "tries": 0, "confirmed_at": time.time()})
    keep = {active.get("version"), active.get("previous")}
    for d in paths.versions_dir().glob("*"):
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)


# ---------- the update feed ----------
def feed_url(svc) -> str:
    """Settings or environment first; otherwise the feed of the place this copy was published from (release.json)."""
    configured = os.environ.get("LIFEOS_UPDATE_FEED") or (svc.settings.behaviour.get("updates") or {}).get("feed_url")
    if configured:
        return configured.strip()
    try:
        return json.loads((Path(__file__).with_name("release.json")).read_text()).get("feed", "").strip()
    except (OSError, ValueError):
        return ""


def check(svc, force: bool = False) -> dict:
    url = feed_url(svc)
    if not url:
        return status(svc)
    last = float(svc.store.get_state("update_checked_at", "0") or 0)
    if not force and time.time() - last < 6 * 3600:
        return status(svc)
    import httpx
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        r.raise_for_status()
        feed = r.json()
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(feed.get("version", ""))):
            raise ValueError("no version in the feed")
        svc.store.set_state("update_feed", json.dumps(feed))
        svc.store.set_state("update_error", "")
    except Exception as e:
        log.warning("update check failed: %s", e)
        svc.store.set_state("update_error", "Couldn't check for updates just now.")
    svc.store.set_state("update_checked_at", str(time.time()))
    return status(svc)


def status(svc) -> dict:
    feed = {}
    try:
        feed = json.loads(svc.store.get_state("update_feed", "") or "{}")
    except ValueError:
        pass
    latest = feed.get("version", "")
    newer = bool(latest) and vtuple(latest) > vtuple(__version__)
    needs_app = newer and is_app_download() and int(feed.get("runtime", 1)) > runtime()
    active = _read_active()
    return {"current": __version__, "latest": latest or __version__, "available": newer and not needs_app,
            "needs_new_app": needs_app, "notes": feed.get("notes", "") if newer else "",
            "app_url": (feed.get("apps") or {}).get(platform_key(), ""), "platform": platform_key(),
            "feed": bool(feed_url(svc)), "checked_at": float(svc.store.get_state("update_checked_at", "0") or 0),
            "error": svc.store.get_state("update_error", ""), "auto": bool((svc.settings.behaviour.get("updates") or {}).get("auto")),
            "app_download": is_app_download(), "home": str(paths.home()),
            "rolled_back": sorted(active.get("failed", []))[-1:] if active.get("failed") else []}


def download_and_install(svc) -> dict:
    st = check(svc, force=True)
    if not st["available"]:
        raise UpdateError("The new app download is needed for this update." if st["needs_new_app"]
                          else "You already have the latest Life OS.")
    feed = json.loads(svc.store.get_state("update_feed", "{}"))
    u = feed.get("update") or {}
    if not u.get("url") or not re.fullmatch(r"[0-9a-f]{64}", str(u.get("sha256", ""))):
        raise UpdateError("The update feed is missing the download.")
    import httpx
    chunks, size = [], 0
    with httpx.stream("GET", u["url"], timeout=60, follow_redirects=True) as r:
        r.raise_for_status()
        for chunk in r.iter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                raise UpdateError("The update download is larger than expected.")
            chunks.append(chunk)
    data = b"".join(chunks)
    if hashlib.sha256(data).hexdigest() != u["sha256"]:
        raise UpdateError("The download didn't match the update feed. Try again later.")
    return install_bytes(data, source="feed")


def daily(svc, now: float | None = None) -> dict:
    """Morning check: a notification when a new version is ready; installed overnight if that's allowed."""
    st = check(svc, force=True)
    if not (st["available"] or st["needs_new_app"]):
        return st
    if svc.store.get_state("update_notified", "") != st["latest"] and svc.notifier:
        msg = (f"Life OS {st['latest']} is ready. " + (st["notes"].splitlines()[0] if st["notes"] else "")).strip()
        if st["needs_new_app"]:
            msg += " This one needs the new app download."
        actions = [{"action": "view", "label": "Update", "url": f"{svc.settings.public_url}/app/#settings?sec=computer"}]
        try:
            svc.notifier.push(msg, title="Update ready", actions=actions)
        except TypeError:
            svc.notifier.push(msg, title="Update ready")
        svc.store.set_state("update_notified", st["latest"])
    return st
