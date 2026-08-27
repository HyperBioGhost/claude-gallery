#!/usr/bin/env bash
# Build Claude Gallery .pkg for macOS
# Run from repo root: bash build/mac/build-pkg.sh
# Requires: pyinstaller, pkgbuild, productbuild (Xcode CLI tools)

set -euo pipefail

APP_NAME="ClaudeGallery"
# Supplied by CI from the git tag; the fallback marks a non-release build.
VERSION="${VERSION:-0.0.0-dev}"
BUNDLE_ID="com.claude-gallery.server"
INSTALL_DIR="/usr/local/bin"
ARTIFACTS_DIR="$HOME/ClaudeGallery/artifacts"
OUT_DIR="build/mac/dist"

echo "▶ Building server binary with PyInstaller..."
pyinstaller \
  --onefile \
  --name claude-gallery-server \
  --distpath "$OUT_DIR/bin" \
  src/server.py

echo "▶ Staging package files..."
STAGE="$OUT_DIR/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE$INSTALL_DIR"
mkdir -p "$STAGE/Library/LaunchAgents"

# Server binary
cp "$OUT_DIR/bin/claude-gallery-server" "$STAGE$INSTALL_DIR/"

# Open Gallery script — users can run this or add to Dock
cat > "$STAGE$INSTALL_DIR/open-claude-gallery" <<'LAUNCHER'
#!/bin/bash
/usr/local/bin/claude-gallery-server &>/dev/null &
sleep 1
open "http://127.0.0.1:7477"
LAUNCHER
chmod +x "$STAGE$INSTALL_DIR/open-claude-gallery"

# Gallery HTML — installed to a fixed system path; postinstall copies to user home
mkdir -p "$STAGE/Library/ClaudeGallery"
cp src/gallery.html "$STAGE/Library/ClaudeGallery/"

# launchd plist — use ~ which launchd expands to the loading user's home at runtime
# Do NOT embed $HOME here — this plist is installed system-wide and loaded per-user
cat > "$STAGE/Library/LaunchAgents/com.claude-gallery.server.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>             <string>com.claude-gallery.server</string>
    <key>ProgramArguments</key>  <array><string>/usr/local/bin/claude-gallery-server</string></array>
    <key>RunAtLoad</key>         <true/>
    <key>KeepAlive</key>         <true/>
    <key>StandardOutPath</key>   <string>/tmp/claude-gallery.log</string>
    <key>StandardErrorPath</key> <string>/tmp/claude-gallery.log</string>
</dict>
</plist>
PLIST

echo "▶ Writing postinstall script..."
mkdir -p "$OUT_DIR/scripts"
cat > "$OUT_DIR/scripts/postinstall" <<'SCRIPT'
#!/usr/bin/env bash
set -e

CLAUDE_MD="$HOME/.claude/CLAUDE.md"
mkdir -p "$(dirname "$CLAUDE_MD")"
mkdir -p "$HOME/ClaudeGallery/artifacts"

# Copy gallery.html from system install location to user home
cp /Library/ClaudeGallery/gallery.html "$HOME/ClaudeGallery/gallery.html"

MARKER="claude-gallery-start"
if ! grep -q "$MARKER" "$CLAUDE_MD" 2>/dev/null; then
  cat >> "$CLAUDE_MD" <<'INSTRUCTIONS'


<!-- claude-gallery-start -->
# Claude Gallery

A local artifact viewer is running at http://localhost:7477.

## Starting the gallery (if not running)
If `curl -s http://localhost:7477/` fails (connection refused), start it:
`/usr/local/bin/claude-gallery-server &`
The server runs silently in the background. Do NOT look for a .py file or npm script.

## Sending artifacts to the gallery
When you generate any viewable output - HTML, SVG, images, charts, code, data files, or anything else worth seeing - do the following automatically, without being asked:

1. Save the file to `~/ClaudeGallery/artifacts/<filename>` (create the folder if it doesn't exist)
2. Immediately after saving, run: `curl -s "http://localhost:7477/notify?file=<filename>"`
3. Tell the user: "Sent to Claude Gallery."

Gallery types: .html, .htm, .svg, .png, .jpg, .gif, .webp, .csv, .json, .py, .js, .ts, .css, .txt, .md

## Non-gallery files
If the output is a binary, archive, or temp file (.zip, .tar, .gz, .exe, etc.) - do NOT put it in the artifacts folder. Ask the user where to save it.

Do NOT open a browser tab. Do NOT assume the artifacts folder is a general-purpose output folder.
<!-- claude-gallery-end -->
INSTRUCTIONS
fi

# Load launchd agent — macOS 13+ uses bootstrap, older versions use load
PLIST="$HOME/Library/LaunchAgents/com.claude-gallery.server.plist"
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || \
launchctl load -w "$PLIST" 2>/dev/null || true

# Create Desktop shortcut (.command file)
DESKTOP="$HOME/Desktop/Gallery Server Starter.command"
cat > "$DESKTOP" <<'CMD'
#!/bin/bash
if curl -s -o /dev/null http://127.0.0.1:7477/ 2>/dev/null; then
    open "http://127.0.0.1:7477"
    exit 0
fi
/usr/local/bin/claude-gallery-server &>/dev/null &
sleep 2
if curl -s -o /dev/null http://127.0.0.1:7477/ 2>/dev/null; then
    open "http://127.0.0.1:7477"
else
    echo "Gallery server failed to start."
    echo "Check /tmp/claude-gallery.log for details."
    read -p "Press Enter to close..."
fi
CMD
chmod +x "$DESKTOP"

exit 0
SCRIPT
chmod +x "$OUT_DIR/scripts/postinstall"

cat > "$OUT_DIR/scripts/preremove" <<'SCRIPT'
#!/usr/bin/env bash
# Stop and unload the server
launchctl unload -w ~/Library/LaunchAgents/com.claude-gallery.server.plist 2>/dev/null || true

# Remove CLAUDE.md injection
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
if [ -f "$CLAUDE_MD" ]; then
  perl -i -0pe 's/\n*<!-- claude-gallery-start -->.*?<!-- claude-gallery-end -->//gs' "$CLAUDE_MD"
fi
exit 0
SCRIPT
chmod +x "$OUT_DIR/scripts/preremove"

echo "▶ Building .pkg..."
pkgbuild \
  --root "$STAGE" \
  --identifier "$BUNDLE_ID" \
  --version "$VERSION" \
  --scripts "$OUT_DIR/scripts" \
  --install-location "/" \
  "$OUT_DIR/$APP_NAME.pkg"

echo "✓ Done: $OUT_DIR/$APP_NAME.pkg"
