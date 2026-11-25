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
