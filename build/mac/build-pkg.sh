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
# The one copy of the Claude Code instructions, also what the README points
# other AI tools to; postinstall puts it between the gallery markers.
cp src/claude-instructions.md "$STAGE/Library/ClaudeGallery/"
cp build/mac/update-instructions.sh "$STAGE/Library/ClaudeGallery/"

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

echo "▶ Adding postinstall script..."
# Kept in its own file so it can be read, linted and tested on its own.
mkdir -p "$OUT_DIR/scripts"
cp build/mac/postinstall "$OUT_DIR/scripts/postinstall"
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
