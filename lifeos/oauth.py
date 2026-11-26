"""In-app 'Connect Google account' flow, so each person signs in on their own phone.

Needs an OAuth client of type 'Web application' whose authorised redirect URI is
<LIFEOS_PUBLIC_URL>/auth/google/callback (an https address, e.g. your Tailscale name).
"""
from __future__ import annotations

import os
import secrets
import time

from .integrations.google import BASE_SCOPES, GMAIL_SCOPE

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # a person may untick Gmail on Google's screen

_PENDING: dict[str, dict] = {}
TTL = 900


def redirect_uri(settings) -> str:
    return f"{settings.public_url}/auth/google/callback"


def _flow(settings, scopes, state=None):
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_secrets_file(settings.google_client_secret_file, scopes=scopes,
                                         redirect_uri=redirect_uri(settings), state=state)


def start(settings, person: str, include_gmail: bool, login_hint: str = "") -> str:
    scopes = BASE_SCOPES + ([GMAIL_SCOPE] if include_gmail else [])
    state = secrets.token_urlsafe(24)
    flow = _flow(settings, scopes, state)
    extra = {"login_hint": login_hint} if login_hint else {}
    url, _ = flow.authorization_url(access_type="offline", prompt="consent select_account",
                                    include_granted_scopes="false", **extra)
    now = time.time()
    for k in [k for k, v in _PENDING.items() if now - v["at"] > TTL]:
        _PENDING.pop(k, None)
    _PENDING[state] = {"person": person, "scopes": scopes, "verifier": flow.code_verifier, "at": now}
    return url


def finish(settings, accounts, state: str, code: str) -> str:
    pending = _PENDING.pop(state, None)
    if not pending or time.time() - pending["at"] > TTL:
        raise ValueError("This sign-in link has expired. Please try connecting again.")
    flow = _flow(settings, pending["scopes"], state)
    flow.code_verifier = pending["verifier"]
    flow.fetch_token(code=code)
    path = accounts.token_path(pending["person"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(flow.credentials.to_json())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    accounts.reload()
    return pending["person"]


# ---------- the Google sign-in file, from Settings ----------
CLIENT_ID = r"[0-9]{6,20}-[a-z0-9]{10,64}\.apps\.googleusercontent\.com"


def save_client(settings, file_text: str = "", client_id: str = "", client_secret: str = "") -> dict:
    """Settings > Google accounts: the downloaded JSON (or its client ID and secret pasted) written as
    secrets/client_secret.json, readable only by this user. Must be a 'Web application' client."""
    import json
    import re
    from pathlib import Path

    if file_text.strip():
        try:
            data = json.loads(file_text)
        except ValueError as e:
            raise ValueError("That isn't the file Google gives you. Choose the .json file you downloaded.") from e
        if not isinstance(data, dict):
            raise ValueError("That isn't the file Google gives you. Choose the .json file you downloaded.")
        if "installed" in data and "web" not in data:
            raise ValueError("That file is for a 'Desktop app' client. In Google Cloud, create a client of type "
                             "'Web application' instead, with the redirect address shown here.")
        web = data.get("web") or {}
        client_id, client_secret = str(web.get("client_id", "")), str(web.get("client_secret", ""))
        if not client_secret:
            raise ValueError("That file has no client secret in it (Google only includes it when the client is "
                             "first made). Click the client, add a secret, and download it again.")
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not re.fullmatch(CLIENT_ID, client_id):
        raise ValueError("The client ID should look like 1234567890-abc123.apps.googleusercontent.com.")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{10,100}", client_secret):
        raise ValueError("That client secret doesn't look right. Copy it again from Google Cloud.")
    out = {"web": {"client_id": client_id, "client_secret": client_secret,
                   "project_id": "", "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                   "token_uri": "https://oauth2.googleapis.com/token",
                   "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                   "redirect_uris": [redirect_uri(settings)]}}
    path = Path(settings.google_client_secret_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, path)
    return {"ok": True, "client_id": client_id}
