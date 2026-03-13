#!/usr/bin/env bash
# builds/mac/build.sh
# Produce a self-contained keybridgeBT mac-sender package.
#
# Usage (from repo root):
#   bash builds/mac/build.sh
#
# Output:
#   builds/dist/keybridgebt-mac-<version>.tar.gz
#
# Requirements:
#   - Python 3.11+  (python3 on PATH)
#   - pip           (bundled with Python)
#   - Internet access (to download wheels into the venv)
set -euo pipefail

# ---------------------------------------------------------------------------
# Paths and version
# ---------------------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST_DIR="$REPO_ROOT/builds/dist"
VERSION="${VERSION:-$(cat "$REPO_ROOT/VERSION" 2>/dev/null || echo "0.1.0")}"
PKG_NAME="keybridgebt-mac-$VERSION"
PKG_DIR="$DIST_DIR/$PKG_NAME"

echo "=== keybridgeBT mac-sender build ==="
echo "Version : $VERSION"
echo "Output  : $DIST_DIR/$PKG_NAME.tar.gz"
echo ""

# ---------------------------------------------------------------------------
# Clean previous build
# ---------------------------------------------------------------------------
rm -rf "$PKG_DIR"
mkdir -p "$PKG_DIR"

# ---------------------------------------------------------------------------
# 1. Copy source
# ---------------------------------------------------------------------------
echo "[1/5] Copying source files…"
cp -R "$REPO_ROOT/mac-sender/keybridgebt_mac" "$PKG_DIR/"
cp    "$REPO_ROOT/mac-sender/config.yaml"      "$PKG_DIR/"
cp    "$REPO_ROOT/mac-sender/requirements.txt" "$PKG_DIR/"
cp    "$REPO_ROOT/mac-sender/com.keybridgebt.sender.plist" "$PKG_DIR/"
cp    "$REPO_ROOT/mac-sender/install.sh"        "$PKG_DIR/"
chmod +x "$PKG_DIR/install.sh"

# ---------------------------------------------------------------------------
# 2. Create isolated Python virtual environment
# ---------------------------------------------------------------------------
echo "[2/5] Creating Python virtual environment…"
python3 -m venv "$PKG_DIR/.venv"

# ---------------------------------------------------------------------------
# 3. Install dependencies
# ---------------------------------------------------------------------------
echo "[3/5] Installing dependencies (this may take a moment)…"
"$PKG_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$PKG_DIR/.venv/bin/pip" install --quiet -r "$PKG_DIR/requirements.txt"

# ---------------------------------------------------------------------------
# 4. Write launcher
# ---------------------------------------------------------------------------
echo "[4/5] Writing launcher script…"
cat > "$PKG_DIR/run.sh" <<'EOF'
#!/usr/bin/env bash
# Launch keybridgeBT mac-sender using the bundled virtual environment.
# Run this script from its own directory, or pass --setup to force key exchange.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/.venv/bin/python" -m keybridgebt_mac "$@"
EOF
chmod +x "$PKG_DIR/run.sh"

# ---------------------------------------------------------------------------
# 5. Archive
# ---------------------------------------------------------------------------
echo "[5/5] Creating archive…"
mkdir -p "$DIST_DIR"
tar -czf "$DIST_DIR/$PKG_NAME.tar.gz" -C "$DIST_DIR" "$PKG_NAME"

# Print checksum so recipients can verify integrity
CHECKSUM=$(shasum -a 256 "$DIST_DIR/$PKG_NAME.tar.gz" | awk '{print $1}')

echo ""
echo "=== Build complete ==="
echo "Archive : $DIST_DIR/$PKG_NAME.tar.gz"
echo "SHA-256 : $CHECKSUM"
echo ""
echo "To deploy:"
echo "  tar xzf $PKG_NAME.tar.gz"
echo "  cd $PKG_NAME"
echo "  sudo ./install.sh        # service install"
echo "  # OR"
echo "  ./run.sh                  # run directly (dev/test)"
