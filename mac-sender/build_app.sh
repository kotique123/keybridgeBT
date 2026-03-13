#!/usr/bin/env bash
# mac-sender/build_app.sh
#
# Build a native keybridgeBT.app macOS application bundle.
#
# The .app wraps the Python daemon so that macOS attributes Accessibility
# and Input Monitoring permissions to the app's bundle ID
# (com.keybridgebt.sender) instead of Terminal.app.
#
# Usage (from mac-sender/ or repo root):
#   bash mac-sender/build_app.sh
#
# Output:
#   builds/dist/keybridgeBT.app
#
# The app is self-contained: it bundles a Python venv with all dependencies
# so the target Mac only needs Python 3.11+ installed system-wide.
set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$REPO_ROOT/builds/dist"
APP_NAME="keybridgeBT"
APP_DIR="$DIST_DIR/$APP_NAME.app"
CONTENTS="$APP_DIR/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"

VERSION="${VERSION:-$(cat "$REPO_ROOT/VERSION" 2>/dev/null || echo "2.0.0")}"

echo "=== Building $APP_NAME.app (v$VERSION) ==="

# ---------------------------------------------------------------------------
# Clean previous build
# ---------------------------------------------------------------------------
rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$RESOURCES"

# ---------------------------------------------------------------------------
# 1. Info.plist — macOS uses the bundle ID to track permissions
# ---------------------------------------------------------------------------
echo "[1/5] Writing Info.plist…"
cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>keybridgeBT</string>

    <key>CFBundleDisplayName</key>
    <string>keybridgeBT</string>

    <key>CFBundleIdentifier</key>
    <string>com.keybridgebt.sender</string>

    <key>CFBundleVersion</key>
    <string>${VERSION}</string>

    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>

    <key>CFBundleExecutable</key>
    <string>keybridgeBT</string>

    <key>CFBundlePackageType</key>
    <string>APPL</string>

    <key>CFBundleIconFile</key>
    <string>AppIcon</string>

    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>

    <key>LSUIElement</key>
    <false/>

    <key>NSHighResolutionCapable</key>
    <true/>

    <!-- These keys trigger the native macOS permission prompts -->
    <key>NSAppleEventsUsageDescription</key>
    <string>keybridgeBT needs permission to control system events.</string>

    <!-- Accessibility — for hotkey and trackpad capture -->
    <key>NSAccessibilityUsageDescription</key>
    <string>keybridgeBT needs Accessibility access to capture trackpad events and the global hotkey (Cmd+Shift+F12).</string>

    <!-- Input Monitoring — for HID keyboard capture -->
    <key>NSInputMonitoringUsageDescription</key>
    <string>keybridgeBT needs Input Monitoring access to read keyboard HID reports and forward them to Windows.</string>
</dict>
</plist>
PLIST

# ---------------------------------------------------------------------------
# 2. Copy source code + config
# ---------------------------------------------------------------------------
echo "[2/5] Copying source files…"
cp -R "$SCRIPT_DIR/keybridgebt_mac" "$RESOURCES/"
cp    "$SCRIPT_DIR/config.yaml"      "$RESOURCES/"
cp    "$SCRIPT_DIR/requirements.txt" "$RESOURCES/"

# ---------------------------------------------------------------------------
# 3. Create Python virtual environment inside the app
# ---------------------------------------------------------------------------
echo "[3/5] Creating Python virtual environment…"
python3 -m venv "$RESOURCES/.venv"

echo "[4/5] Installing dependencies (this may take a moment)…"
"$RESOURCES/.venv/bin/pip" install --quiet --upgrade pip
"$RESOURCES/.venv/bin/pip" install --quiet -r "$RESOURCES/requirements.txt"

# ---------------------------------------------------------------------------
# 5. Write the main executable (shell script)
# ---------------------------------------------------------------------------
echo "[5/5] Writing launcher…"
cat > "$MACOS/keybridgeBT" <<'LAUNCHER'
#!/usr/bin/env bash
# keybridgeBT.app launcher
# Launches the Python daemon using the bundled venv.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
export PYTHONPATH="$DIR"

exec "$DIR/.venv/bin/python" -m keybridgebt_mac
LAUNCHER
chmod +x "$MACOS/keybridgeBT"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "=== Build complete ==="
echo "App:  $APP_DIR"
echo ""
echo "To run:"
echo "  open $APP_DIR"
echo ""
echo "macOS will prompt for Accessibility and Input Monitoring permissions"
echo "on first launch. Grant both — they stick to the app permanently."
echo ""
echo "To install to /Applications:"
echo "  cp -R $APP_DIR /Applications/"
