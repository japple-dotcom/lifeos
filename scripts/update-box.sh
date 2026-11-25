#!/usr/bin/env bash
# Life OS: update to a new version from the zip, keeping your settings, Google connections and history.
#
#   ./scripts/update-box.sh ~/Downloads/lifeos.zip
#
# Copies the new code over this folder. Never touches .env, secrets/, data/ or .venv. Then reinstalls
# packages and restarts the service.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZIP="${1:-}"
fail() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }
[[ -n "$ZIP" && -f "$ZIP" ]] || fail "Usage: ./scripts/update-box.sh path/to/lifeos.zip"
command -v unzip >/dev/null || sudo apt-get install -y -qq unzip >/dev/null

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
unzip -q "$ZIP" -d "$TMP"
NEW="$(dirname "$(find "$TMP" -maxdepth 3 -name pyproject.toml -print -quit)")"
[[ -f "$NEW/lifeos/app.py" ]] || fail "That zip doesn't look like Life OS."
OLD_V="$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' "$HERE/lifeos/__init__.py" | head -n1 || true)"
NEW_V="$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' "$NEW/lifeos/__init__.py" | head -n1 || true)"
echo "Updating Life OS ${OLD_V:-?} -> ${NEW_V:-?}"

# Replace the code folders wholesale (so removed files go), keep everything personal.
for d in lifeos web scripts tests homeassistant; do
  [[ -d "$NEW/$d" ]] || continue
  rm -rf "${HERE:?}/$d"
  cp -a "$NEW/$d" "$HERE/$d"
done
for f in "$NEW"/*; do
  name="$(basename "$f")"
  case "$name" in
    .env|secrets|data|.venv|lifeos|web|scripts|tests|homeassistant) ;;
    config) mkdir -p "$HERE/config"; [[ -f "$HERE/config/settings.yaml" ]] || cp -a "$f/settings.yaml" "$HERE/config/";
            cp -a "$f/settings.yaml" "$HERE/config/settings.example.yaml" ;;
    *) cp -a "$f" "$HERE/" ;;
  esac
done
chmod +x "$HERE"/scripts/*.sh

"$HERE/.venv/bin/pip" install -q --prefer-binary -r "$HERE/requirements.txt"
sudo systemctl restart lifeos.service
for _ in $(seq 1 30); do
  if HEALTH="$(curl -fsS http://127.0.0.1:8800/health 2>/dev/null)"; then break; fi
  sleep 1
done
[[ -n "${HEALTH:-}" ]] || fail "Life OS didn't come back. See: journalctl -u lifeos -n 50"
echo "Done: $(printf '%s' "$HEALTH" | grep -oE '"version": *"[^"]+"')"
echo "Your settings.yaml was kept. The new version's example is in config/settings.example.yaml."
echo "On each phone, pull down to refresh (or close and reopen the app) to get the new screens."
