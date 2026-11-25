#!/usr/bin/env bash
# Life OS: one-command install on an always-on box (Raspberry Pi 5, mini PC or any Debian/Ubuntu machine).
#
#   cd lifeos && ./scripts/install-box.sh
#
# Safe to run again: it only adds what's missing, keeps .env, secrets/ and data/, and restarts Life OS.
# It installs Life OS as a background service that starts on boot, and (if you say yes) Tailscale, so the
# phones reach it privately. It never opens Life OS to the internet.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
ask_yes() { local a; read -r -p "$1 [y/N] " a || true; [[ "${a,,}" == y || "${a,,}" == yes ]]; }

[[ -f "$HERE/pyproject.toml" && -f "$HERE/lifeos/app.py" ]] || fail "Run this from inside the unzipped lifeos folder."
[[ $EUID -ne 0 ]] || fail "Run this as your normal user, not root (it asks for your password when it needs it)."
command -v apt-get >/dev/null || fail "This script is for Debian, Ubuntu or Raspberry Pi OS. Use Docker instead (see README)."
RUN_USER="$(id -un)"

say "1/5  Python"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip curl >/dev/null
python3 - <<'PY' || fail "Life OS needs Python 3.10 or newer. Update the operating system and try again."
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

say "2/5  Life OS and its packages (a few minutes the first time)"
[[ -d .venv ]] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q --prefer-binary -r requirements.txt
mkdir -p data secrets
chmod 700 secrets data

say "3/5  Your settings"
if [[ -f .env ]]; then
  echo "Keeping your existing .env."
elif [[ -t 0 ]]; then
  .venv/bin/python scripts/setup.py
else
  fail "No .env yet. Run: .venv/bin/python scripts/setup.py   then run this script again."
fi
chmod 600 .env 2>/dev/null || true

say "4/5  Private address with Tailscale"
if command -v tailscale >/dev/null; then
  echo "Tailscale is installed."
elif ask_yes "Install Tailscale now (recommended, keeps Life OS private to your own devices)?"; then
  curl -fsSL https://tailscale.com/install.sh | sh
else
  echo "Skipped. Phones on home Wi-Fi can use http://$(hostname -I | awk '{print $1}'):8800/app/ for now."
fi
if command -v tailscale >/dev/null; then
  if ! tailscale status >/dev/null 2>&1; then
    echo "Sign this box in to your Tailscale account (a link appears below):"
    sudo tailscale up
  fi
  sudo tailscale serve --bg 8800 >/dev/null 2>&1 || sudo tailscale serve --bg http://127.0.0.1:8800 || true
  TS_URL="$(tailscale serve status 2>/dev/null | grep -oE 'https://[^ ]+' | head -n1 || true)"
  if [[ -n "$TS_URL" ]]; then
    echo "Private address: ${TS_URL%/}"
    if ! grep -q "^LIFEOS_PUBLIC_URL=${TS_URL%/}\$" .env; then
      echo "Put this in .env as LIFEOS_PUBLIC_URL=${TS_URL%/} (setup.py asks for it), and add"
      echo "${TS_URL%/}/auth/google/callback as an authorised redirect URI on your Google OAuth client."
    fi
  fi
fi

say "5/5  Background service (starts on boot, restarts if it stops)"
sudo tee /etc/systemd/system/lifeos.service >/dev/null <<UNIT
[Unit]
Description=Life OS household assistant
After=network-online.target
Wants=network-online.target

[Service]
User=${RUN_USER}
WorkingDirectory=${HERE}
Environment=TZ=Europe/London
Environment=LIFEOS_HOME=${HERE}
ExecStart=${HERE}/.venv/bin/python ${HERE}/lifeos_boot.py --headless
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable lifeos.service >/dev/null
sudo systemctl restart lifeos.service

for _ in $(seq 1 30); do
  if HEALTH="$(curl -fsS http://127.0.0.1:8800/health 2>/dev/null)"; then break; fi
  sleep 1
done
[[ -n "${HEALTH:-}" ]] || fail "Life OS didn't start. See what went wrong with: journalctl -u lifeos -n 50"
VERSION="$(printf '%s' "$HEALTH" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin).get("version",""))')"
say "Life OS ${VERSION} is running."
printf '%s' "$HEALTH" | .venv/bin/python -c '
import json, sys
h = json.load(sys.stdin)
for p in h.get("problems", []):
    print(" - still to do:", p)
'
echo "Logs:     journalctl -u lifeos -f"
echo "Updates:  Life OS > Settings > Updates and this computer (no reinstall needed)"
