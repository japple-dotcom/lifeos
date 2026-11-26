"""Getting phones onto Life OS: sign in by scanning a code, and the phone address set up by Life OS itself.

Sign in by QR code: the Life OS icon in the menu bar (Mac) or tray (Windows) > Show household key opens a page on
this computer only. Under the key there's a code for each grown-up; the phone's camera opens a one-time link that
signs that phone in as that person. Each link works once, for 10 minutes, and only that page shows it.

Phone access: Settings > Updates and this computer > Set up phone access runs Tailscale Serve on this computer
(`tailscale serve --bg http://127.0.0.1:<port>`), the same as the Terminal line, and saves the https address it
gives as the address for phones. If Tailscale asks for HTTPS to be switched on first, the app shows Tailscale's own
link to do that. Only the fixed command runs; nothing typed in the app goes into it.
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time

JOIN_SECONDS = 600
_codes: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


# ---------- one-time sign-in codes ----------
def make_code(person: str) -> str:
    code = secrets.token_urlsafe(18)
    with _lock:
        now = time.time()
        for k in [k for k, (_, until) in _codes.items() if until < now]:
            _codes.pop(k, None)
        _codes[code] = (person, now + JOIN_SECONDS)
    return code


def use_code(code: str) -> str | None:
    """The person the code signs in, once; None if it's wrong, used or old."""
    with _lock:
        hit = _codes.pop(code or "", None)
    if not hit or hit[1] < time.time():
        return None
    return hit[0]


def join_link(svc, code: str) -> str:
    base = (svc.settings.public_url or "").rstrip("/")
    return f"{base}/app/?join={code}"


# ---------- Tailscale Serve ----------
CANDIDATES = {
    "darwin": ["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale"],
    "win32": [r"C:\Program Files\Tailscale\tailscale.exe"],
}


def find_cli() -> str:
    import os
    for p in CANDIDATES.get(sys.platform, []):
        if os.path.exists(p):
            return p
    return shutil.which("tailscale") or ""


def _run(args: list[str], timeout: int = 25, runner=None) -> str:
    runner = runner or subprocess.run
    try:
        r = runner(args, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:  # Tailscale waits while HTTPS needs switching on; the link is in what it printed
        out = e.stdout or ""
        return out.decode(errors="replace") if isinstance(out, bytes) else out


def tailnet_address(cli: str, runner=None) -> str:
    out = _run([cli, "status", "--json"], timeout=15, runner=runner)
    try:
        name = (json.loads(out).get("Self") or {}).get("DNSName", "").rstrip(".")
    except ValueError:
        return ""
    return f"https://{name}" if name.endswith(".ts.net") else ""


def setup(svc, port: int, runner=None, save=None) -> dict:
    """Run Tailscale Serve for this computer and save the address. Returns {ok, url} or {ok: False, link?, problem}."""
    cli = find_cli() if runner is None else "tailscale"
    if not cli:
        return {"ok": False, "problem": "Tailscale isn't installed on this computer. Install it from tailscale.com, "
                                       "sign in, then try again."}
    out = _run([cli, "serve", "--bg", f"http://127.0.0.1:{int(port)}"], runner=runner)
    link = re.search(r"https://login\.tailscale\.com/\S+", out)
    url = re.search(r"https://[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net", out)
    if not url and not link:
        addr = tailnet_address(cli, runner)
        if addr and "error" not in out.lower():
            url = re.match(r".*", addr)
    if url:
        address = url.group(0).rstrip("/")
        if save is None:
            from . import setup_core
            save = lambda v: setup_core.update_env(v)  # noqa: E731
        save({"LIFEOS_PUBLIC_URL": address})
        return {"ok": True, "url": address}
    if link:
        return {"ok": False, "link": link.group(0).rstrip(".,"),
                "problem": "Tailscale needs HTTPS switched on for your network first. Open the link, click Enable, "
                           "then press Set up phone access again."}
    if "logged out" in out.lower() or "not logged in" in out.lower() or "needslogin" in out.lower():
        return {"ok": False, "problem": "Tailscale on this computer isn't signed in. Open Tailscale and sign in."}
    return {"ok": False, "problem": "Tailscale didn't give an address: " + " ".join(out.split())[:200]}
