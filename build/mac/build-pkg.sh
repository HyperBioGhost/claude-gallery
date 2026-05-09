#!/usr/bin/env bash
# Build Claude Gallery .pkg for macOS
# Run from repo root: bash build/mac/build-pkg.sh
# Requires: pyinstaller, pkgbuild, productbuild (Xcode CLI tools)

set -euo pipefail

APP_NAME="ClaudeGallery"
VERSION="1.0.0"
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
mkdir -p "$STAGE$HOME/ClaudeGallery"

# Server binary
cp "$OUT_DIR/bin/claude-gallery-server" "$STAGE$INSTALL_DIR/"

# Gallery HTML (goes to ~/ClaudeGallery/)
cp src/gallery.html "$STAGE$HOME/ClaudeGallery/"

# launchd plist for auto-start on login
cat > "$STAGE/Library/LaunchAgents/com.claude-gallery.server.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>             <string>com.claude-gallery.server</string>
    <key>ProgramArguments</key>  <array><string>$INSTALL_DIR/claude-gallery-server</string></array>
    <key>RunAtLoad</key>         <true/>
    <key>KeepAlive</key>         <true/>
    <key>StandardOutPath</key>   <string>$HOME/ClaudeGallery/server.log</string>
    <key>StandardErrorPath</key> <string>$HOME/ClaudeGallery/server.log</string>
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

MARKER="claude-gallery-start"
if ! grep -q "$MARKER" "$CLAUDE_MD" 2>/dev/null; then
  cat >> "$CLAUDE_MD" <<'INSTRUCTIONS'


<!-- claude-gallery-start -->
# Claude Gallery

A local artifact viewer is running at http://localhost:7477. When you generate any viewable output — HTML, SVG, images, charts, code, data files, or anything else worth seeing — do the following automatically, without being asked:

1. Save the file to `~/ClaudeGallery/artifacts/<filename>` (create the folder if it doesn't exist)
2. Immediately after saving, run: `curl -s "http://localhost:7477/notify?file=<filename>"`
3. Tell the user: "Sent to Claude Gallery."

Do NOT open a browser tab. Do NOT ask permission. Do NOT skip this for "simple" outputs.
Supported types: .html, .htm, .svg, .png, .jpg, .gif, .webp, .md, .txt, .csv, .json, .js, .ts, .py, .css
<!-- claude-gallery-end -->
INSTRUCTIONS
fi

# Load launchd agent for current user
launchctl load -w ~/Library/LaunchAgents/com.claude-gallery.server.plist 2>/dev/null || true

# Open gallery in browser (give server 2s to start)
sleep 2 && open http://localhost:7477 &

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
  "$OUT_DIR/$APP_NAME-$VERSION.pkg"

echo "✓ Done: $OUT_DIR/$APP_NAME-$VERSION.pkg"
