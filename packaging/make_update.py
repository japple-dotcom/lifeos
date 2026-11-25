"""Builds the update file for this version, and optionally the update feed (latest.json).

    python packaging/make_update.py                         # dist/lifeos-update-<version>.zip
    python packaging/make_update.py --feed https://github.com/<you>/lifeos/releases/download/v<version>

The update holds only code (lifeos/, web/, default settings, requirements), with a SHA-256 for every file in
manifest.json. Notes come from the top section of CHANGES.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["lifeos", "web"]
EXTRA = ["config/settings.yaml", "requirements.txt", ".env.example"]
SKIP = re.compile(r"(__pycache__|\.pyc$|\.DS_Store$|\.pytest_cache)")


def version() -> str:
    return re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "lifeos" / "__init__.py").read_text()).group(1)


def runtime() -> int:
    return int(re.search(r"^RUNTIME\s*=\s*(\d+)", (ROOT / "lifeos_boot.py").read_text(), re.M).group(1))


def notes(v: str) -> str:
    changes = ROOT / "CHANGES.md"
    if not changes.exists():
        return ""
    m = re.search(rf"^## {re.escape(v)}\b.*?\n(.*?)(?=^## |\Z)", changes.read_text(), re.M | re.S)
    return m.group(1).strip() if m else ""


def files() -> list[Path]:
    out = []
    for top in INCLUDE:
        out += [p for p in sorted((ROOT / top).rglob("*")) if p.is_file() and not SKIP.search(str(p))]
    out += [ROOT / e for e in EXTRA if (ROOT / e).exists()]
    return out


def build(out_dir: Path) -> Path:
    v = version()
    paths = files()
    manifest = {"name": "lifeos-update", "version": v, "runtime": runtime(), "notes": notes(v),
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "files": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"lifeos-update-{v}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        for p in paths:
            z.write(p, p.relative_to(ROOT).as_posix())
    return target


def feed(update: Path, base_url: str, apps: dict[str, str]) -> dict:
    v = version()
    data = update.read_bytes()
    return {"version": v, "runtime": runtime(), "notes": notes(v),
            "published": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "update": {"url": f"{base_url.rstrip('/')}/{update.name}", "sha256": hashlib.sha256(data).hexdigest(),
                       "size": len(data)},
            "apps": {k: f"{base_url.rstrip('/')}/{name}" for k, name in apps.items()}}


APP_FILES = {"mac-arm64": "LifeOS-mac-arm64.dmg", "mac-x64": "LifeOS-mac-intel.dmg",
             "windows-x64": "LifeOS-windows.exe", "linux-x86_64": "lifeos-linux-x86_64",
             "linux-arm64": "lifeos-linux-arm64"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--feed", default="", help="release download base URL; writes latest.json beside the update")
    ap.add_argument("--apps", default="", help="comma list of platform keys that have app downloads (default: all)")
    a = ap.parse_args()
    target = build(Path(a.out))
    print(target)
    if a.feed:
        keys = [k for k in (a.apps.split(",") if a.apps else APP_FILES) if k in APP_FILES]
        f = feed(target, a.feed, {k: APP_FILES[k] for k in keys})
        (Path(a.out) / "latest.json").write_text(json.dumps(f, indent=2))
        print(Path(a.out) / "latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
