#!/bin/bash
# Life OS for Mac, installed from this folder: one command, then it's an app.
#
#   bash ~/Downloads/lifeos/scripts/install-mac.sh
#
# What it does:
#   1. finds Python 3.10 or newer on this Mac (Homebrew or python.org)
#   2. copies Life OS to ~/Library/Application Support/Life OS/app and installs what it needs there (a few minutes)
#   3. makes "Life OS" in your Applications folder (menu bar app; it opens Life OS in your browser)
#   4. starts it. The first time, a setup page opens; if you used Life OS before, it offers to bring your settings
#      across (from ~/Documents/lifeos or similar).
# After this, updates install from Settings in Life OS. You never need to run this again.
# (When the standalone Mac app is available from your GitHub releases, it uses the same settings and history.)
set -e

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[ "$(uname)" = "Darwin" ] || fail "This is for a Mac. On a Linux box or Raspberry Pi use scripts/install-box.sh."
SRC="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$SRC/lifeos_boot.py" ] || fail "Run this from inside the unzipped lifeos folder (scripts/install-mac.sh)."

say "1/4  Looking for Python 3.10 or newer"
PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
         "$(command -v python3 2>/dev/null)"; do
  [ -n "$c" ] && [ -x "$c" ] || continue
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || fail "Python 3.10 or newer isn't installed. Get it from https://www.python.org/downloads/macos/ (the big yellow button), then run this again."
echo "Using $PY ($("$PY" --version 2>&1))"

HOME_DIR="$HOME/Library/Application Support/Life OS"
APPDIR="$HOME_DIR/app"
say "2/4  Installing Life OS (a few minutes the first time)"
mkdir -p "$HOME_DIR"
if [ "$SRC" != "$APPDIR" ]; then
  rm -rf "$APPDIR.new"
  mkdir -p "$APPDIR.new"
  # Code only: settings, keys and history never live in here
  (cd "$SRC" && tar --exclude='./.venv' --exclude='./.env' --exclude='./secrets' --exclude='./data' \
     --exclude='./versions' --exclude='__pycache__' -cf - .) | (cd "$APPDIR.new" && tar -xf -)
  if [ -d "$APPDIR/.venv" ]; then mv "$APPDIR/.venv" "$APPDIR.new/.venv"; fi
  rm -rf "$APPDIR"
  mv "$APPDIR.new" "$APPDIR"
fi
xattr -dr com.apple.quarantine "$APPDIR" 2>/dev/null || true
[ -x "$APPDIR/.venv/bin/python" ] || "$PY" -m venv "$APPDIR/.venv"
"$APPDIR/.venv/bin/python" -m pip install -q --upgrade pip
"$APPDIR/.venv/bin/python" -m pip install -q --only-binary=cryptography -r "$APPDIR/requirements.txt"

say "3/4  Adding Life OS to your Applications folder"
APPS="$HOME/Applications"
[ -w /Applications ] && APPS="/Applications"
BUNDLE="$APPS/Life OS.app"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
cat > "$BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Life OS</string>
  <key>CFBundleDisplayName</key><string>Life OS</string>
  <key>CFBundleIdentifier</key><string>uk.lifeos.app</string>
  <key>CFBundleExecutable</key><string>Life OS</string>
  <key>CFBundleIconFile</key><string>lifeos</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$("$PY" "$APPDIR/lifeos_boot.py" --version)</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict></plist>
PLIST
cat > "$BUNDLE/Contents/MacOS/Life OS" <<LAUNCH
#!/bin/bash
export LIFEOS_HOME="$HOME_DIR"
exec "$APPDIR/.venv/bin/python" "$APPDIR/lifeos_boot.py" "\$@"
LAUNCH
chmod +x "$BUNDLE/Contents/MacOS/Life OS"
if [ -f "$APPDIR/packaging/lifeos.icns" ]; then cp "$APPDIR/packaging/lifeos.icns" "$BUNDLE/Contents/Resources/lifeos.icns"; fi
echo "Made $BUNDLE"

say "4/4  Starting Life OS"
open "$BUNDLE"
cat <<DONE

Life OS is running: look for its icon at the top right of the screen, and a page opening in your browser.
- First time? Answer the setup questions, or tap "Bring my settings across" if you used Life OS before.
- To have it start whenever you log in: Life OS > Settings > Updates and this computer.
- Updates: Life OS > Settings > Updates and this computer. No more downloads to unzip.
Your settings and history are kept in: $HOME_DIR
DONE
