#!/bin/bash
# keybridgeBT mac-sender install script
# Creates a dedicated user, installs files, and loads the launchd service.
set -euo pipefail

INSTALL_DIR="/opt/keybridgebt/mac-sender"
VENV_DIR="$INSTALL_DIR/.venv"
LOG_DIR="/var/log/keybridgebt"
SERVICE_USER="_keybridgebt"
PLIST_SRC="com.keybridgebt.sender.plist"
PLIST_DST="/Library/LaunchDaemons/com.keybridgebt.sender.plist"

echo "=== keybridgeBT mac-sender installer ==="

# Must be root
if [[ $EUID -ne 0 ]]; then
    echo "Error: run with sudo"
    exit 1
fi

# 1. Create service user (if needed)
if ! dscl . -read /Users/$SERVICE_USER &>/dev/null; then
    echo "Creating user $SERVICE_USER…"
    # Find a free UID in the daemon range (400–499)
    NEXT_UID=400
    while dscl . -list /Users UniqueID | awk '{print $2}' | grep -q "^${NEXT_UID}$"; do
        NEXT_UID=$((NEXT_UID + 1))
    done
    dscl . -create /Users/$SERVICE_USER
    dscl . -create /Users/$SERVICE_USER UniqueID "$NEXT_UID"
    dscl . -create /Users/$SERVICE_USER PrimaryGroupID 20
    dscl . -create /Users/$SERVICE_USER UserShell /usr/bin/false
    dscl . -create /Users/$SERVICE_USER RealName "keybridgeBT Service"
    dscl . -create /Users/$SERVICE_USER NFSHomeDirectory /var/empty
    echo "  Created UID=$NEXT_UID"
else
    echo "User $SERVICE_USER already exists"
fi

# 2. Install files
echo "Installing to $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR"
cp -R keybridgebt_mac "$INSTALL_DIR/"
cp config.yaml "$INSTALL_DIR/" 2>/dev/null || true
cp requirements.txt "$INSTALL_DIR/"
chown -R $SERVICE_USER:staff "$INSTALL_DIR"

# 3. Create venv and install Python dependencies
# Using a venv avoids PEP 668 "externally managed" errors on macOS 12+
# and isolates dependencies from the system Python.
echo "Creating Python virtualenv at $VENV_DIR…"
python3 -m venv "$VENV_DIR"
echo "Installing Python dependencies into venv…"
"$VENV_DIR/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
chown -R $SERVICE_USER:staff "$VENV_DIR"

# 4. Create log directory
echo "Setting up log directory…"
mkdir -p "$LOG_DIR"
chown $SERVICE_USER:staff "$LOG_DIR"

# 5. Install and load launchd plist
echo "Installing launchd service…"
cp "$PLIST_SRC" "$PLIST_DST"
chown root:wheel "$PLIST_DST"
chmod 644 "$PLIST_DST"

# Unload existing service (both modern and legacy API)
launchctl bootout system "$PLIST_DST" 2>/dev/null || launchctl unload "$PLIST_DST" 2>/dev/null || true
# Load service (macOS 10.10+ bootstrap, fallback to legacy load)
if launchctl bootstrap system "$PLIST_DST" 2>/dev/null; then
    echo "Service bootstrapped (modern API)"
else
    launchctl load "$PLIST_DST"
    echo "Service loaded (legacy API)"
fi

echo ""
echo "=== Installation complete ==="
echo "Service loaded. Check status with:"
echo "  sudo launchctl list | grep keybridgebt"
echo "  tail -f $LOG_DIR/sender.log"
echo ""
echo "NOTE: You must grant Accessibility and Input Monitoring permissions"
echo "to the Python binary used by this service."
