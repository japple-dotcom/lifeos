"""Is this request from a browser on this computer itself (not a phone coming through Tailscale Serve)?

Tailscale Serve hands phone requests to Life OS from 127.0.0.1, so "the connection is from loopback" isn't enough.
A request only counts as this computer when it comes from loopback, is addressed to localhost/127.0.0.1, and
carries no proxy or Tailscale headers. Used for "this computer only" actions (Quit, the setup page, the household
key page).
"""
from __future__ import annotations

import ipaddress

PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "x-real-ip",
                 "tailscale-user-login", "tailscale-user-name")
LOCAL_NAMES = ("localhost", "127.0.0.1", "::1", "[::1]", "testserver", "testclient")


def is_local(request) -> bool:
    host = request.client.host if request.client else ""
    try:
        loop = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loop = host in ("localhost", "testclient")
    if not loop:
        return False
    if any(h in request.headers for h in PROXY_HEADERS):
        return False
    name = (request.headers.get("host") or "").lower()
    if name.startswith("["):
        name = name.split("]")[0] + "]"
    else:
        name = name.rsplit(":", 1)[0] if name.count(":") == 1 else name
    return name in LOCAL_NAMES
