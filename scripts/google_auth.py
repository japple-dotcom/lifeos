"""Fallback: connect a Google account from a laptop instead of the phone app.

The normal way is the app: Settings > Google accounts > Connect. Use this only if that is not possible.
Needs an OAuth client of type "Desktop app" saved as secrets/desktop_client_secret.json.

    python scripts/google_auth.py me            # calendar, tasks and Gmail
    python scripts/google_auth.py wife --no-gmail
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from lifeos.integrations.google import BASE_SCOPES, GMAIL_SCOPE  # noqa: E402

person = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "me"
scopes = BASE_SCOPES + ([] if "--no-gmail" in sys.argv else [GMAIL_SCOPE])
client_file = os.getenv("GOOGLE_DESKTOP_CLIENT_FILE", "secrets/desktop_client_secret.json")
token_dir = Path(os.getenv("GOOGLE_TOKEN_FILE", "secrets/google_token.json")).parent
token_file = token_dir / f"google_token_{person}.json"

flow = InstalledAppFlow.from_client_secrets_file(client_file, scopes)
creds = flow.run_local_server(port=0)
Path(token_file).parent.mkdir(parents=True, exist_ok=True)
Path(token_file).write_text(creds.to_json())
print(f"Saved {token_file}")
