"""Photos and PDFs attached to renewals and home manual entries (a receipt, the warranty card, the stopcock).

Kept in <home>/data/files on the home computer, never in Google. Only images and PDFs, up to 15 MB each, and only
signed-in household members can see them.
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from . import paths

TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic", "application/pdf": ".pdf"}
MAX_BYTES = 15 * 1024 * 1024
FOR = {"renewal", "manual", "bill", "trip"}
MAGIC = {".jpg": [b"\xff\xd8\xff"], ".png": [b"\x89PNG"], ".webp": [b"RIFF"], ".pdf": [b"%PDF"], ".heic": [b"\x00\x00\x00"]}


def folder() -> Path:
    # The demo points this at a temporary folder, so trying attachments there leaves nothing behind.
    f = Path(os.environ["LIFEOS_FILES_DIR"]) if os.environ.get("LIFEOS_FILES_DIR") else paths.home() / "data" / "files"
    f.mkdir(parents=True, exist_ok=True)
    return f


def save(svc, data: bytes, content_type: str, for_kind: str, for_id: str, name: str = "") -> dict:
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype not in TYPES:
        raise ValueError("Only photos (JPEG, PNG, WebP, HEIC) and PDFs can be attached.")
    if not data or len(data) > MAX_BYTES:
        raise ValueError("That file is empty or bigger than 15 MB.")
    ext = TYPES[ctype]
    if not any(data.startswith(m) or (ext == ".heic" and b"ftyp" in data[:16]) for m in MAGIC[ext]):
        raise ValueError("That file doesn't look like the kind it says it is.")
    if for_kind not in FOR or not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", str(for_id)):
        raise ValueError("Attach it to a renewal or a home manual entry.")
    fid = secrets.token_hex(8)
    (folder() / f"{fid}{ext}").write_bytes(data)
    clean = re.sub(r"[^\w .,()\-]", "", name or "")[:80] or f"Attachment{ext}"
    return svc.store.put("file", fid, {"name": clean, "type": ctype, "size": len(data), "for_kind": for_kind,
                                       "for_id": str(for_id), "ext": ext})


def for_item(svc, for_kind: str, for_id) -> list[dict]:
    return [f for f in svc.store.records("file") if f["for_kind"] == for_kind and f["for_id"] == str(for_id)]


def path_of(svc, fid: str) -> tuple[Path, dict] | None:
    if not re.fullmatch(r"[0-9a-f]{16}", fid or ""):
        return None
    rec = svc.store.get("file", fid)
    if not rec:
        return None
    p = folder() / f"{fid}{rec['ext']}"
    return (p, rec) if p.exists() else None


def delete(svc, fid: str) -> bool:
    found = path_of(svc, fid)
    if found:
        found[0].unlink(missing_ok=True)
    return svc.store.remove("file", fid)
