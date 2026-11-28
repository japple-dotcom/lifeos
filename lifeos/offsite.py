"""A copy of Life OS kept off this computer: encrypted, every night, and checked by opening it again.

The copy goes to a folder: a USB stick, or a folder that Google Drive for desktop, iCloud Drive or OneDrive keeps in
sync. It holds the database (history, settings made in the app), config/settings.yaml, the keys (.env) and Google
sign-ins (secrets/), and attachments, encrypted with AES-256-GCM using a key made from the household's backup
passphrase (scrypt). Without the passphrase a backup can't be opened, so it must be written down somewhere safe.

After writing each backup it is read back from the folder, decrypted, and the database inside is opened and checked
(integrity and row counts against the live one). Settings > Backups shows the result; the doctor warns when the last
good one is more than three days old.

Restoring: Settings > Backups > Restore, with the file and the passphrase. It is checked, staged in
<home>/restore-pending, and put in place the next time Life OS starts (the current data is moved to
<home>/before-restore-<time>, never deleted).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import paths
from .dates import fmt as _dfmt

log = logging.getLogger("lifeos.offsite")

MAGIC = b"LIFEOS-BACKUP-1\n"
KEEP = 14
SUFFIX = ".lifeos-backup"
MAX_RESTORE = 500 * 1024 * 1024


class BackupError(Exception):
    pass


# ---------- settings ----------
def _secret_file() -> Path:
    return paths.home() / "secrets" / "backup-passphrase"


def passphrase() -> str:
    f = _secret_file()
    return f.read_text().strip() if f.exists() else ""


def configure(svc, folder: str, new_passphrase: str = "") -> dict:
    folder = (folder or "").strip()
    if folder:
        p = Path(folder).expanduser()
        if not p.is_dir():
            raise BackupError(f"There's no folder at {p}. Plug in the USB stick, or check the folder name.")
        if not os.access(p, os.W_OK):
            raise BackupError(f"Life OS can't write to {p}.")
    if new_passphrase:
        if len(new_passphrase) < 10:
            raise BackupError("Use a passphrase of at least 10 characters (three or four random words is good).")
        f = _secret_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(new_passphrase)
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
    svc.store.set_state("offsite_folder", folder)
    return status(svc)


def status(svc) -> dict:
    try:
        last = json.loads(svc.store.get_state("offsite_last", "") or "{}")
    except ValueError:
        last = {}
    folder = svc.store.get_state("offsite_folder", "") or ""
    files = []
    if folder and Path(folder).is_dir():
        files = sorted((f.name for f in Path(folder).glob(f"lifeos-*{SUFFIX}")), reverse=True)[:KEEP]
    return {"folder": folder, "has_passphrase": bool(passphrase()), "last": last, "files": files,
            "folder_there": bool(folder) and Path(folder).is_dir(), "pending_restore": (paths.home() / "restore-pending").exists()}


# ---------- encryption ----------
def _key(phrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(phrase.encode(), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def encrypt(data: bytes, phrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    return MAGIC + salt + nonce + AESGCM(_key(phrase, salt)).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, phrase: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 28:
        raise BackupError("That isn't a Life OS backup file.")
    salt = blob[len(MAGIC):len(MAGIC) + 16]
    nonce = blob[len(MAGIC) + 16:len(MAGIC) + 28]
    try:
        return AESGCM(_key(phrase, salt)).decrypt(nonce, blob[len(MAGIC) + 28:], MAGIC)
    except InvalidTag as e:
        raise BackupError("The passphrase is wrong, or the file is damaged.") from e


# ---------- making one ----------
def _bundle(svc) -> bytes:
    home = paths.home()
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "lifeos.db"
        svc.store.backup_to(str(db))
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(db, arcname="data/lifeos.db")
            for rel in (".env", "config/settings.yaml"):
                if (home / rel).is_file():
                    tar.add(home / rel, arcname=rel)
            for folder in ("secrets", "data/files"):
                d = home / folder
                if d.is_dir():
                    for f in sorted(d.rglob("*")):
                        if f.is_file() and f.name != "backup-passphrase":
                            tar.add(f, arcname=str(f.relative_to(home)))
            meta = json.dumps({"made": time.time(), "version": _version()}).encode()
            info = tarfile.TarInfo("lifeos-backup.json")
            info.size = len(meta)
            tar.addfile(info, io.BytesIO(meta))
    return buf.getvalue()


def _version() -> str:
    from . import __version__
    return __version__


def _check(blob: bytes, phrase: str, live_counts: dict | None = None) -> tuple[bool, str, dict]:
    """Opens a backup the way a restore would and checks the database inside."""
    data = decrypt(blob, phrase)
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = tar.getnames()
        if "data/lifeos.db" not in names:
            return False, "the database is missing from the backup", {}
        if any(n.startswith("/") or ".." in Path(n).parts for n in names):
            return False, "the backup has unsafe paths in it", {}
        _extract(tar, tmp, ["data/lifeos.db"])
        con = sqlite3.connect(Path(tmp) / "data" / "lifeos.db")
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # nosec B608 (fixed table names)
                      for t in ("records", "chore_log", "renewals", "meals") if _has_table(con, t)}
        finally:
            con.close()
    if not ok:
        return False, "the database inside failed its integrity check", counts
    if live_counts:
        short = [t for t, n in live_counts.items() if counts.get(t, 0) < n]
        if short:
            return False, f"the backup has fewer rows than Life OS in: {', '.join(short)}", counts
    return True, f"opened and checked ({sum(counts.values())} records, {len(names)} files)", counts


def _has_table(con, name: str) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _live_counts(svc) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "x.db"
        svc.store.backup_to(str(db))
        con = sqlite3.connect(db)
        try:
            return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # nosec B608
                    for t in ("records", "chore_log", "renewals", "meals") if _has_table(con, t)}
        finally:
            con.close()


def run(svc, now: datetime | None = None, notify_on_problem: bool = True) -> dict:
    folder = svc.store.get_state("offsite_folder", "") or ""
    if not folder:
        return {"ok": False, "skipped": "not set up"}
    now = now or datetime.now()
    result: dict
    try:
        phrase = passphrase()
        if not phrase:
            raise BackupError("No backup passphrase set (Settings > Backups).")
        dest = Path(folder)
        if not dest.is_dir():
            raise BackupError(f"The backup folder {folder} isn't there (is the USB stick plugged in?).")
        live = _live_counts(svc)
        blob = encrypt(_bundle(svc), phrase)
        target = dest / f"lifeos-{now.strftime('%Y-%m-%d-%H%M')}{SUFFIX}"
        tmp = target.with_suffix(".partial")
        tmp.write_bytes(blob)
        tmp.replace(target)
        ok, detail, _ = _check(target.read_bytes(), phrase, live)
        for old in sorted(dest.glob(f"lifeos-*{SUFFIX}"), reverse=True)[KEEP:]:
            old.unlink(missing_ok=True)
        result = {"ok": ok, "at": time.time(), "when": now.strftime(_dfmt("%-d %b %Y, %H:%M")), "file": target.name,
                  "size": len(blob), "detail": detail}
    except (BackupError, OSError, tarfile.TarError, sqlite3.Error) as e:
        result = {"ok": False, "at": time.time(), "when": now.strftime(_dfmt("%-d %b %Y, %H:%M")), "detail": str(e)}
    except ImportError:
        result = {"ok": False, "at": time.time(), "when": now.strftime(_dfmt("%-d %b %Y, %H:%M")),
                  "detail": "Backups need the 'cryptography' package (pip install cryptography)."}
    svc.store.set_state("offsite_last", json.dumps(result))
    if not result["ok"] and notify_on_problem and svc.notifier:
        day = now.strftime("%Y-%m-%d")
        if svc.store.get_state("offsite_warned", "") != day:
            svc.store.set_state("offsite_warned", day)
            svc.notifier.push(f"Last night's backup didn't work: {result['detail']}", title="Backup problem",
                              actions=[{"action": "view", "label": "Backups",
                                        "url": f"{svc.settings.public_url}/app/#settings?sec=backups"}])
    return result


# ---------- restoring ----------
def stage_restore(data: bytes, phrase: str) -> dict:
    """Checks a backup file and stages it; it is put in place when Life OS next starts."""
    if not data or len(data) > MAX_RESTORE:
        raise BackupError("That file is empty or too big.")
    ok, detail, counts = _check(data, phrase)
    if not ok:
        raise BackupError(f"That backup can't be used: {detail}.")
    staged = paths.home() / "restore-pending"
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(decrypt(data, phrase)), mode="r:gz") as tar:
        names = tar.getnames()
        if any(n.startswith("/") or ".." in Path(n).parts for n in names):
            raise BackupError("That backup has unsafe paths in it.")
        _extract(tar, staged, names)
    return {"ok": True, "detail": detail, "counts": counts}


def _extract(tar: tarfile.TarFile, dest, names: list[str]) -> None:
    """Extracts only regular files and folders (Python's 'data' filter where available)."""
    for n in names:
        m = tar.getmember(n)
        if not (m.isfile() or m.isdir()):
            continue
        try:
            tar.extract(m, dest, filter="data")
        except TypeError:  # Python without extraction filters: paths were checked above
            tar.extract(m, dest)  # nosec B202


def apply_pending(home: Path | None = None) -> str:
    """At start-up, before the database is opened: swaps a staged restore in. Returns what it did."""
    home = home or paths.home()
    staged = home / "restore-pending"
    if not (staged / "data" / "lifeos.db").exists():
        return ""
    keep = home / f"before-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    keep.mkdir()
    for rel in ("data/lifeos.db", ".env", "config/settings.yaml", "secrets", "data/files"):
        src = staged / rel
        if not src.exists():
            continue
        cur = home / rel
        if cur.exists():
            (keep / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(cur), str(keep / rel))
        cur.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(cur))
    # the passphrase stays: it was never in the backup
    if (keep / "secrets" / "backup-passphrase").exists() and not (home / "secrets" / "backup-passphrase").exists():
        shutil.copy2(keep / "secrets" / "backup-passphrase", home / "secrets" / "backup-passphrase")
    shutil.rmtree(staged, ignore_errors=True)
    log.warning("Restored a backup; the previous data is in %s", keep)
    return str(keep)
